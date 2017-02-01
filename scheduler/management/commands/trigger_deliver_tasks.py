import os
import re

from django.utils.six.moves import input

try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse

from django.core.management import BaseCommand, CommandError
from django.core.validators import URLValidator
from django.utils.dateparse import parse_datetime

from scheduler.models import Schedule
from scheduler.tasks import DeliverTask


def get_schedule(schedule_string):
    schedule_type, _, lookup_id = schedule_string.partition(':')
    if schedule_type == 'crontab':
        kwargs = {'celery_cron_definition': int(lookup_id)}
    elif schedule_type == 'interval':
        kwargs = {'celery_interval_definition': int(lookup_id)}
    else:
        raise CommandError(
            'Invalid schedule format: %s, unknown type: %s' % (
                schedule_string, schedule_type))
    return Schedule.objects.filter(enabled=True).filter(**kwargs)


def get_model(model_class):
    def callback(pk):
        try:
            return model_class.objects.get(pk=int(pk))
        except (model_class.DoesNotExist,) as e:
            raise CommandError(e)
        except (Exception,) as e:
            raise CommandError(e)
    return callback


def mk_validator(validator_class):
    def validator_callback(input_str):
        validator = validator_class()
        validator(input_str)
        return input_str
    return validator_callback


class Command(BaseCommand):
    help = ("Manually generate the deliver_tasks for a queue_tasks job "
            "that failed for some reason.\n\n"
            '* Only use this if things went HORRIBLY WRONG *')

    def add_arguments(self, parser):
        parser.add_argument(
            '--message-sender-token', type=str,
            default=os.environ.get('MESSAGE_SENDER_TOKEN'),
            help=('The authorization token for the '
                  'Message Sender'))
        parser.add_argument(
            '--message-sender-url',
            type=mk_validator(URLValidator),
            default=os.environ.get('MESSAGE_SENDER_URL'),
            help=('The URL for the Message Sender API'))
        parser.add_argument(
            '--identity-store-token', type=str,
            default=os.environ.get('IDENTITY_STORE_TOKEN'),
            help='The token for the ID Store')
        parser.add_argument(
            '--identity-store-url',
            type=mk_validator(URLValidator),
            default=os.environ.get('IDENTITY_STORE_URL'),
            help='The Identity Store API URL')
        parser.add_argument(
            '--default-addr-type',
            type=str,
            default='msisdn',
            help=('The default address type to assume when not specified. '
                  'Defaults to `msisdn`.'))
        parser.add_argument(
            '--since', type=parse_datetime,
            help=('Filter for outbound created_at since '
                  '(required YYYY-MM-DD HH:MM:SS)'))
        parser.add_argument(
            '--until', type=parse_datetime,
            help=('Filter for created_at until '
                  '(required YYYY-MM-DD HH:MM:SS)'))
        parser.add_argument(
            '--dry-run', action='store_true', default=False,
            help='Do a dry-run, do not actually dispatch the tasks.')
        parser.add_argument(
            '--confirm', action='store_true', default=False,
            help='Agree to everything, I know what I am doing.')
        parser.add_argument(
            'schedule', type=get_schedule,
            help=('The schedule to run deliver_tasks for. '
                  'The format is <definition>:<celery_definition_lookup_id>. '
                  'Valid options for definitions are `crontab` or `interval`, '
                  'e.g.: `crontab:1` or `interval:2`'))
        try:
            import seed_services_client  # noqa
        except (ImportError,):
            raise CommandError(
                'Please make sure you have the seed-services-client '
                'package installed.')

    def handle(self, *args, **options):
        schedules = options['schedule']
        default_addr_type = options['default_addr_type']
        identity_store_token = options['identity_store_token']
        identity_store_url = options['identity_store_url']
        message_sender_token = options['message_sender_token']
        message_sender_url = options['message_sender_url']
        since = options['since']
        until = options['until']
        dry_run = options['dry_run']

        if not since:
            raise CommandError('--since is a required parameter')

        if not until:
            raise CommandError('--until is a required parameter')

        from seed_services_client import (
            StageBasedMessagingApiClient,
            IdentityStoreApiClient,
            MessageSenderApiClient)

        id_store_client = IdentityStoreApiClient(
            identity_store_token, identity_store_url)

        message_sender_client = MessageSenderApiClient(
            message_sender_token, message_sender_url)

        def confirm(prompt):
            if options['confirm']:
                return True
            try:
                return input(
                    "%s [y/n] > " % (prompt,)).lower() == "y"
            except KeyboardInterrupt:
                raise CommandError("Please confirm the question.")

        msg = ("You are about to trigger a stage based messaging send for %s "
               "subscriptions. Are you sure you want to be doing this?" % (
                self.style.NOTICE(str(schedules.count()))
               ))

        if not confirm(msg):
            raise CommandError(
                'Please confirm as you need to know what you are doing.')

        for schedule in schedules.iterator():
            sbm_api_url, subscription_uuid = self.parse_sbm_api_url(
                schedule.endpoint)
            sbm_client = StageBasedMessagingApiClient(
                schedule.auth_token, sbm_api_url)
            subscription = sbm_client.get_subscription(subscription_uuid)
            identity = id_store_client.get_identity(subscription['identity'])
            addresses = self.get_addresses(identity, default_addr_type)
            any_outbounds = []
            for address in addresses:
                outbounds = message_sender_client.get_outbounds(params={
                    'to_addr': address,
                    'before': until.isoformat(),
                    'after': since.isoformat(),
                })
                any_outbounds.extend(outbounds)

            if any_outbounds:
                continue

            if dry_run:
                self.stdout.write('Dry run for %s' % (schedule,))
            else:
                self.stdout.write('%s' % (schedule,))
                DeliverTask.apply_async(
                    kwargs={"schedule_id": str(schedule.id)})

    def get_addresses(self, identity, default_addr_type):
        details = identity.get('details', {})
        default_addr_type = (
            details.get('default_addr_type') or default_addr_type)
        addresses = details.get('addresses', {}).get(default_addr_type, {})
        return addresses.keys()

    def parse_sbm_api_url(self, endpoint):
        parse_result = urlparse(endpoint)
        (subscription_uuid,) = re.match(
            r'/api/v1/subscriptions/([0-9a-z\-]+)/send',
            parse_result.path).groups()
        return (
            '%s://%s/api/v1/' % (parse_result.scheme, parse_result.netloc),
            subscription_uuid)
