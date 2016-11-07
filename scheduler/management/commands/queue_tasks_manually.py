from django.core.management import BaseCommand


class Command(BaseCommand):
    help = ('Schedule queue tasks manually, these would normally be fired '
            'by PeriodicTasks. If that fails, you can still fire these '
            'manually using this management command.\n\n'
            'Use this command with care, probably only when things have '
            'gone wrong')

    def add_arguments(self, parser):
        parser.add_argument(
            'schedule_type', choices=['crontab', 'interval'],
            help='What schedule type')
        parser.add_argument(
            'lookup_id', type=int,
            help='What is the PK of the schedule to fire manually')

    def handle(self, *args, **options):
        print options
