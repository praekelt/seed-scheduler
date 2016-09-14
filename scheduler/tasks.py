import json
import requests

from celery.task import Task
from celery.utils.log import get_task_logger
from django.conf import settings
from go_http.metrics import MetricsApiClient

from .models import Schedule


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

    def run(self, schedule_id, **kwargs):
        """
        Runs an instance of a scheduled task
        """
        l = self.get_logger(**kwargs)
        l.info("Running instance of <%s>" % (schedule_id,))

        # Load the schedule item
        schedule = Schedule.objects.get(id=schedule_id)
        headers = {"Content-Type": "application/json"}
        if schedule.auth_token is not None:
            headers["Authorization"] = "Token %s" % schedule.auth_token
        requests.post(
            url=schedule.endpoint,
            data=json.dumps(schedule.payload),
            headers=headers
        )

        return True

deliver_task = DeliverTask()


class QueueTasks(Task):

    """
    Task to queue delivery of scheduled hooks
    """
    name = "seed_scheduler.scheduler.tasks.queue_tasks"

    def run(self, schedule_type, lookup_id, **kwargs):
        """
        Loads Schedule linked to provided lookup
        """
        l = self.get_logger(**kwargs)
        l.info("Queuing <%s> <%s>" % (schedule_type, lookup_id))

        # Load the schedule active items
        schedules = Schedule.objects.filter(enabled=True)
        if schedule_type == "crontab":
            schedules = schedules.filter(celery_cron_definition=lookup_id)
        elif schedule_type == "interval":
            schedules = schedules.filter(celery_interval_definition=lookup_id)
        # create tasks for each active schedule
        l.info("Filtered schedule count: <%s>" % schedules.count())
        queued = 0
        for schedule in schedules:
            if schedule.frequency is None:
                schedule.triggered += 1
                schedule.save()
                DeliverTask.apply_async(kwargs={
                    "schedule_id": str(schedule.id)})
                queued += 1

            elif schedule.triggered < schedule.frequency:
                schedule.triggered += 1
                if schedule.triggered == schedule.frequency:
                    # schedule must have hit it's limit
                    schedule.enabled = False
                schedule.save()
                DeliverTask.apply_async(kwargs={
                    "schedule_id": str(schedule.id)})
                queued += 1

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
    name = "seed_identity_store.identities.tasks.fire_metric"

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
