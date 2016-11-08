from django.core.management import BaseCommand, CommandError
from djcelery.models import PeriodicTask


class Command(BaseCommand):
    help = ('Fire djcelery PeriodicTask manually. \n\n'
            '* Only use this is things went wrong *')

    def add_arguments(self, parser):
        parser.add_argument(
            'periodic-task-id', type=int,
            help='What is the PK of the PeriodicTask to fire manually')
        parser.add_argument(
            '--confirm', action='store_true', default=False,
            help=('Do not ask for any kind of confirmation, '
                  'I know what I am doing'))
        parser.add_argument(
            '--ignore-result', action='store_false', default=True,
            help=('Do not wait for the task to complete and return '
                  'the result'))
        parser.add_argument(
            '--timeout', type=int, default=60,
            help=('How long to wait in secods for the result to return, '
                  'set to 0 to disable. Defaults to 60 seconds.'))
        parser.add_argument()

    def handle(self, *args, **options):
        print options
        periodic_task_id = options['periodic-task-id']
        ignore_result = options['ignore-result']
        if options['timeout']:
            timeout = options['timeout']
        else:
            timeout = None

        def confirm(prompt):
            if options['no_input']:
                return True
            try:
                return raw_input(
                    "%s [y/n] > " % (prompt,)).lower() == "y"
            except KeyboardInterrupt:
                raise CommandError("Please confirm the question.")

        try:
            periodic_task = PeriodicTask.objects.get(pk=periodic_task_id)
        except PeriodicTask.DoesNotExist:
            raise CommandError("PeriodicTask with id %s does not exist." % (
                periodic_task_id))

        if periodic_task.last_run_at:
            msg = ("The task %s was last run on %s\n"
                   "Are you sure you want to resubmit this periodic task?" % (
                    self.style.NOTICE(periodic_task),
                    self.style.NOTICE(periodic_task.last_run_at)))
        else:
            msg = ("The task %s has never been run before.\n"
                   "Are you sure you want to resubmit this periodic task?" % (
                    self.style.NOTICE(periodic_task),))

        if not confirm(msg):
            raise CommandError(
                'Please confirm, you need to know what you are doing.')

        from celery import current_app
        app = current_app._get_current_object()
        async_result = app.send_task(periodic_task.task,
                                     periodic_task.args,
                                     periodic_task.kwargs,
                                     **periodic_task.options)

        if not ignore_result:
            result = async_result.get(timeout=timeout)
            self.stdout.write(result)
