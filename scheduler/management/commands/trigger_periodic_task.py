from django.utils.six.moves import input

from django.core.management import BaseCommand, CommandError
from djcelery.models import PeriodicTask
from djcelery.schedulers import ModelEntry


class Command(BaseCommand):
    help = ('Fire djcelery PeriodicTask manually. \n\n'
            '* Only use this if things went HORRIBLY WRONG *')

    def add_arguments(self, parser):
        parser.add_argument(
            'periodic-task-id', type=int,
            help='What is the PK of the PeriodicTask to fire manually')
        parser.add_argument(
            '--confirm', action='store_true', default=False,
            help=('Do not ask for any kind of confirmation as '
                  'I know what I am doing'))
        parser.add_argument(
            '--ignore-result', action='store_true', default=False,
            help=('Do not wait for the task to complete and return '
                  'the result'))
        parser.add_argument(
            '--timeout', type=int, default=60,
            help=('How long to wait in seconds for the result to return, '
                  'set to 0 to disable. Defaults to 60 seconds.'))

    def handle(self, *args, **options):
        periodic_task_id = options['periodic-task-id']
        ignore_result = options['ignore_result']
        if options['timeout']:
            timeout = options['timeout']
        else:
            timeout = None

        def confirm(prompt):
            if options['confirm']:
                return True
            try:
                return input(
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
                   "Are you sure you want to submit this periodic task?" % (
                    self.style.NOTICE(periodic_task),))

        if not confirm(msg):
            raise CommandError(
                'Please confirm as you need to know what you are doing.')

        from celery import current_app
        model_entry = ModelEntry(periodic_task)

        app = current_app._get_current_object()
        task = app.tasks.get(model_entry.task)
        if task:
            async_result = task.apply_async(
                model_entry.args, model_entry.kwargs,
                **model_entry.options)
        else:
            async_result = app.send_task(
                model_entry.task, model_entry.args, model_entry.kwargs,
                **model_entry.options)

        periodic_task.last_run_at = app.now()
        periodic_task.save()

        if not ignore_result:
            result = async_result.get(timeout=timeout)
            self.stdout.write(result)
