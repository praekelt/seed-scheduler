import uuid

from crontab import CronTab
from django.contrib.postgres.fields import JSONField
from django.contrib.auth.models import User
from django.db import models
from django.utils.encoding import python_2_unicode_compatible
from django.dispatch import receiver
from django.db.models.signals import pre_save
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext_lazy as _
from djcelery.models import CrontabSchedule, IntervalSchedule


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
    frequency: number of times task should run in total
    cron_definition: cron syntax of schedule (i.e. 'm h d dM MY')
    interval_definition: integer and period
        (from: days, hours, minutes, seconds, microseconds) e.g. 1 minutes
    endpoint: what URL to POST to
    payload: what json encoded payload to include on the POST
    next_send_at: when the task is next expected to run (not guarenteed)
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    frequency = models.IntegerField(null=False, blank=False)
    cron_definition = models.CharField(max_length=500, null=True,
                                       validators=[validate_crontab])
    celery_cron_definition = models.OneToOneField(
        CrontabSchedule,
        on_delete=models.CASCADE,
        null=True
    )
    interval_definition = models.CharField(max_length=100, null=True,
                                           validators=[validate_interval])
    celery_interval_definition = models.OneToOneField(
        IntervalSchedule,
        on_delete=models.CASCADE,
        null=True
    )
    endpoint = models.CharField(max_length=500, null=False)
    payload = JSONField(null=False, blank=False, default={})
    next_send_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, related_name='schedules_created',
                                   null=True)
    updated_by = models.ForeignKey(User, related_name='schedules_updated',
                                   null=True)
    user = property(lambda self: self.created_by)

    def serialize_hook(self, hook):
        # optional, there are serialization defaults
        # we recommend always sending the Hook
        # metadata along for the ride as well
        return {
            'hook': hook.dict(),
            'data': {
                'id': str(self.id),
                'frequency': self.frequency,
                'cron_definition': self.cron_definition,
                'interval_definition': self.interval_definition,
                'endpoint': self.endpoint,
                'payload': self.payload,
                'next_send_at': self.next_send_at.isoformat(),
                'created_at': self.created_at.isoformat(),
                'updated_at': self.updated_at.isoformat()
            }
        }

    def __str__(self):  # __unicode__ on Python 2
        return str(self.id)


@receiver(pre_save, sender=Schedule)
def schedule_saved(sender, instance, **kwargs):
    if instance.cron_definition is not None and \
            instance.celery_cron_definition is not None:
        # clean up old cron schedule
        instance.celery_cron_definition.delete()
    if instance.interval_definition is not None and \
            instance.celery_interval_definition is not None:
        # clean up old interval schedule
        instance.celery_interval_definition.delete()
    if instance.cron_definition is not None and \
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
        cs = CrontabSchedule.objects.create(**schedule)
        instance.celery_cron_definition = cs
        # instance.save()
    if instance.interval_definition is not None and \
            instance.celery_interval_definition is None:
        every, period = instance.interval_definition.split()
        interval = {
            "every": int(every),
            "period": period
        }
        intsch = IntervalSchedule.objects.create(**interval)
        instance.celery_interval_definition = intsch
        # instance.save()
