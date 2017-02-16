import uuid

from crontab import CronTab
from django.contrib.postgres.fields import JSONField
from django.contrib.auth.models import User
from django.db import models
from django.utils.encoding import python_2_unicode_compatible
from django.dispatch import receiver
from django.db.models.signals import pre_save, post_save
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext_lazy as _
from djcelery.models import CrontabSchedule, IntervalSchedule, PeriodicTask


def validate_crontab(value):
    try:
        CronTab(value)
    except ValueError as e:
        raise ValidationError(
            _('%(value)s is not a valid crontab string: %(reason)s'),
            params={'value': value, 'reason': e},
        )


def validate_interval(value):
    try:
        every, period = value.split()
        int(every)
        if period not in ["days", "hours", "minutes", "seconds",
                          "microseconds"]:
            raise ValidationError(
                _("%(value)s is not a valid period. Accepted: days, hours, "
                  "minutes, seconds, microseconds)"),
                params={'value': value},
            )
    except ValueError:
        raise ValidationError(
            _("%(value)s is not a valid interval string: integer and "
              "period (from: days, hours, minutes, seconds, microseconds) "
              "e.g. 1 minutes"),
            params={'value': value},
        )


@python_2_unicode_compatible
class Schedule(models.Model):

    """
    Base model with all scheduled tasks
    frequency: number of times task should run in total (deprecated)
    cron_definition: cron syntax of schedule (i.e. 'm h d dM MY')
    interval_definition: integer and period
        (from: days, hours, minutes, seconds, microseconds) e.g. 1 minutes
    endpoint: what URL to POST to
    payload: what json encoded payload to include on the POST
    next_send_at: when the task is next expected to run (not guarenteed)
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # frequency and triggered are deprecated and will eventually be removed
    frequency = models.IntegerField(null=True, blank=True)
    triggered = models.IntegerField(null=False, blank=False, default=0)
    cron_definition = models.CharField(max_length=500,
                                       null=True, blank=True,
                                       validators=[validate_crontab])
    celery_cron_definition = models.ForeignKey(
        CrontabSchedule,
        on_delete=models.CASCADE,
        null=True, blank=True
    )
    interval_definition = models.CharField(max_length=100,
                                           null=True, blank=True,
                                           validators=[validate_interval])
    celery_interval_definition = models.ForeignKey(
        IntervalSchedule,
        on_delete=models.CASCADE,
        null=True, blank=True
    )
    endpoint = models.CharField(max_length=500, null=False)
    auth_token = models.CharField(max_length=500, null=True, blank=True)
    payload = JSONField(null=True, blank=True, default={})
    next_send_at = models.DateTimeField(null=True, blank=True)
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, related_name='schedules_created',
                                   null=True, blank=True)
    updated_by = models.ForeignKey(User, related_name='schedules_updated',
                                   null=True, blank=True)
    user = property(lambda self: self.created_by)

    def serialize_hook(self, hook):
        # optional, there are serialization defaults
        # we recommend always sending the Hook
        # metadata along for the ride as well
        # not sending auth token
        return {
            'hook': hook.dict(),
            'data': {
                'id': str(self.id),
                'frequency': self.frequency,
                'triggered': self.triggered,
                'cron_definition': self.cron_definition,
                'interval_definition': self.interval_definition,
                'endpoint': self.endpoint,
                'payload': self.payload,
                'next_send_at': self.next_send_at.isoformat(),
                'enabled': self.enabled,
                'created_at': self.created_at.isoformat(),
                'updated_at': self.updated_at.isoformat()
            }
        }

    def __str__(self):  # __unicode__ on Python 2
        return str(self.id)


@receiver(pre_save, sender=Schedule)
def schedule_saved(sender, instance, **kwargs):
    if instance.cron_definition is not None and \
            instance.cron_definition != "" and \
            instance.celery_cron_definition is None:
        # CronTab package just used to parse and validate the string nicely.
        entry = CronTab(instance.cron_definition)
        schedule = {
            "minute": entry.matchers.minute.input,
            "hour": entry.matchers.hour.input,
            "day_of_week": entry.matchers.weekday.input,
            "day_of_month": entry.matchers.day.input,
            "month_of_year": entry.matchers.month.input
        }
        cs, createdcs = CrontabSchedule.objects.get_or_create(**schedule)
        instance.celery_cron_definition = cs
        if createdcs:
            # make the periodic task
            pt = {
                "name": "Run %s" % instance.cron_definition,
                "task": "seed_scheduler.scheduler.tasks.queue_tasks",
                "crontab": cs,
                "enabled": True,
                "args": '["crontab", %s]' % cs.id
            }
            PeriodicTask.objects.create(**pt)
    if instance.interval_definition is not None and \
            instance.interval_definition != "" and \
            instance.celery_interval_definition is None:
        every, period = instance.interval_definition.split()
        interval = {
            "every": int(every),
            "period": period
        }
        intsch, createdsch = IntervalSchedule.objects.get_or_create(**interval)
        instance.celery_interval_definition = intsch
        if createdsch:
            # make the periodic task
            pt = {
                "name": "Run %s" % instance.interval_definition,
                "task": "seed_scheduler.scheduler.tasks.queue_tasks",
                "interval": intsch,
                "enabled": True,
                "args": '["interval", %s]' % intsch.id
            }
            PeriodicTask.objects.create(**pt)


@receiver(post_save, sender=Schedule)
def fire_metrics_if_new(sender, instance, created, **kwargs):
    from .tasks import fire_metric
    if created:
        fire_metric.apply_async(kwargs={
            "metric_name": 'schedules.created.sum',
            "metric_value": 1.0
        })


@python_2_unicode_compatible
class QueueTaskRun(models.Model):
    task_id = models.UUIDField()
    celery_cron_definition = models.ForeignKey(
        CrontabSchedule,
        on_delete=models.CASCADE,
        null=True, blank=True
    )
    celery_interval_definition = models.ForeignKey(
        IntervalSchedule,
        on_delete=models.CASCADE,
        null=True, blank=True
    )
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True)

    def __str__(self):  # __unicode__ on Python 2
        return str(self.id)


@python_2_unicode_compatible
class ScheduleFailure(models.Model):
    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE)
    task_id = models.UUIDField()
    initiated_at = models.DateTimeField()
    reason = models.TextField()

    def __str__(self):  # __unicode__ on Python 2
        return str(self.id)
