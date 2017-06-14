import json
import requests
from uuid import uuid4

from celery.task import Task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.db import connection, transaction
from django.utils.timezone import now
from djcelery.models import CrontabSchedule, IntervalSchedule
from go_http.metrics import MetricsApiClient
from requests import exceptions as requests_exceptions

from seed_scheduler import utils
from .models import Schedule, QueueTaskRun, ScheduleFailure


logger = get_task_logger(__name__)


class DeliverHook(Task):
    def run(self, target, payload, instance_id=None, hook_id=None, **kwargs):
        """
        target:     the url to receive the payload.
        payload:    a python primitive data structure
        instance_id:   a possibly None "trigger" instance ID
        hook_id:       the ID of defining Hook object
        """
        requests.post(
            url=target,
            data=json.dumps(payload),
            headers={
                'Content-Type': 'application/json',
                'Authorization': 'Token %s' % settings.HOOK_AUTH_TOKEN
            }
        )


def deliver_hook_wrapper(target, payload, instance, hook):
    if instance is not None:
        instance_id = instance.id
    else:
        instance_id = None
    kwargs = dict(target=target, payload=payload,
                  instance_id=instance_id, hook_id=hook.id)
    DeliverHook.apply_async(kwargs=kwargs)


class DeliverTask(Task):

    """
    Task to deliver scheduled hook
    """
    name = "seed_scheduler.scheduler.tasks.deliver_task"
    default_retry_delay = 5
    max_retries = 5

    def run(self, schedule_id, **kwargs):
        """
        Runs an instance of a scheduled task
        """
        l = self.get_logger(**kwargs)
        l.info("Running instance of <%s>" % (schedule_id,))
        if self.request.retries > 0:
            retry_delay = utils.calculate_retry_delay(self.request.retries)
        else:
            retry_delay = self.default_retry_delay

        schedule = Schedule.objects.get(id=schedule_id)
        headers = {"Content-Type": "application/json"}
        if schedule.auth_token is not None:
            headers["Authorization"] = "Token %s" % schedule.auth_token
        try:
            response = requests.post(
                url=schedule.endpoint,
                data=json.dumps(schedule.payload),
                headers=headers,
                timeout=settings.DEFAULT_REQUEST_TIMEOUT
            )
            # Expecting a 201, raise for errors.
            response.raise_for_status()
        except requests_exceptions.ConnectionError as exc:
            l.info('Connection Error to endpoint: %s' % schedule.endpoint)
            fire_metric.delay('scheduler.deliver_task.connection_error.sum', 1)
            self.retry(exc=exc, countdown=retry_delay)
        except requests_exceptions.HTTPError as exc:
            # Recoverable HTTP errors: 500, 401
            l.info('Request failed due to status: %s' %
                   exc.response.status_code)
            metric_name = ('scheduler.deliver_task.http_error.%s.sum' %
                           exc.response.status_code)
            fire_metric.delay(metric_name, 1)
            self.retry(exc=exc, countdown=retry_delay)
        except requests_exceptions.Timeout as exc:
            l.info('Request failed due to timeout')
            fire_metric.delay('scheduler.deliver_task.timeout.sum', 1)
            self.retry(exc=exc, countdown=retry_delay)

        return True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        if self.request.retries == self.max_retries:
            if 'schedule_id' in kwargs:
                schedule_id = kwargs['schedule_id']
            else:
                schedule_id = args[0]
            ScheduleFailure.objects.create(
                schedule_id=schedule_id,
                initiated_at=self.request.eta,
                reason=einfo.exception.message,
                task_id=task_id
            )
        super(DeliverTask, self).on_failure(exc, task_id, args, kwargs, einfo)


deliver_task = DeliverTask()


class QueueTasks(Task):

    """
    Task to queue delivery of scheduled hooks
    """
    name = "seed_scheduler.scheduler.tasks.queue_tasks"
    ignore_result = True

    def run(self, schedule_type, lookup_id, **kwargs):
        """
        Loads Schedule linked to provided lookup
        """
        l = self.get_logger(**kwargs)
        l.info("Queuing <%s> <%s>" % (schedule_type, lookup_id))

        task_run = QueueTaskRun()
        task_run.task_id = self.request.id or uuid4()
        task_run.started_at = now()
        tr_qs = QueueTaskRun.objects

        # Load the schedule active items
        schedules = Schedule.objects.filter(enabled=True)
        if schedule_type == "crontab":
            schedules = schedules.filter(celery_cron_definition=lookup_id)
            tr_qs = tr_qs.filter(celery_cron_definition=lookup_id)
            scheduler_type = CrontabSchedule
            task_run.celery_cron_definition_id = lookup_id
        elif schedule_type == "interval":
            schedules = schedules.filter(celery_interval_definition=lookup_id)
            tr_qs = tr_qs.filter(celery_interval_definition=lookup_id)
            scheduler_type = IntervalSchedule
            task_run.celery_interval_definition_id = lookup_id

        # Confirm that this task should run now based on last run time.
        try:
            last_task_run = tr_qs.latest('started_at')
        except QueueTaskRun.DoesNotExist:
            # No previous run so it is safe to continue.
            pass
        else:
            # This basicly replicates what celery beat is meant to do, but
            # we can't trust celery beat and django-celery to always accurately
            # update their own last run time.
            sched = scheduler_type.objects.get(id=lookup_id)
            due, due_next = sched.schedule.is_due(last_task_run.started_at)
            if not due and due_next >= settings.DEFAULT_CLOCK_SKEW_SECONDS:
                return ("Aborted Queuing <%s> <%s> due to last task run (%s) "
                        "at %s" % (schedule_type, lookup_id, last_task_run.id,
                                   last_task_run.started_at))

        task_run.save()
        # create tasks for each active schedule
        queued = 0
        schedules = schedules.values('id')
        with transaction.atomic(), connection.cursor() as cur:
            # A named cursor is declared here to make psycopg2 use a server
            # side cursor. The SSC prevents the entire result set from being
            # loaded into memory.
            # NOTE: this can be replaced with just a call to a queryset's
            # iterator() method in Django 1.11 as that directly supports using
            # a SSC.
            query = str(schedules.query)
            cursor_name = '_cur_queue_tasks_{uuid}'.format(uuid=uuid4().hex)
            cur.execute(
                "DECLARE {cursor_name} CURSOR FOR {query}".format(
                    cursor_name=cursor_name,
                    query=query
                ),
                {'lookup_id': lookup_id}
            )
            while True:
                cur.execute("FETCH 10000 FROM {0}".format(cursor_name))
                chunk = cur.fetchall()
                if not chunk:
                    break
                for row in chunk:
                    DeliverTask.apply_async(
                        kwargs={"schedule_id": str(row[0])})
                    queued += 1

        task_run.completed_at = now()
        task_run.save()
        return "Queued <%s> Tasks" % (queued, )


queue_tasks = QueueTasks()


def get_metric_client(session=None):
    return MetricsApiClient(
        auth_token=settings.METRICS_AUTH_TOKEN,
        api_url=settings.METRICS_URL,
        session=session)


class FireMetric(Task):

    """ Fires a metric using the MetricsApiClient
    """
    name = "seed_scheduler.scheduler.tasks.fire_metric"

    def run(self, metric_name, metric_value, session=None, **kwargs):
        metric_value = float(metric_value)
        metric = {
            metric_name: metric_value
        }
        metric_client = get_metric_client(session=session)
        metric_client.fire(metric)
        return "Fired metric <%s> with value <%s>" % (
            metric_name, metric_value)


fire_metric = FireMetric()


class RequeueFailedTasks(Task):

    """
    Task to requeue failed schedules.
    """
    name = "seed_scheduler.scheduler.tasks.requeue_failed_tasks"

    def run(self, **kwargs):
        """
        Runs an instance of a scheduled task
        """
        l = self.get_logger(**kwargs)
        failures = ScheduleFailure.objects
        l.info("Attempting to requeue <%s> failed schedules" %
               failures.count())
        for failure in failures.iterator():
            schedule_id = str(failure.schedule_id)
            # Cleanup the failure before requeueing it.
            failure.delete()
            deliver_task.delay(schedule_id)


requeue_failed_tasks = RequeueFailedTasks()
