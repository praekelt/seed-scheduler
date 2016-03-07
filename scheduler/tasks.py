import json
import requests

from celery.task import Task
from celery.utils.log import get_task_logger
from django.conf import settings


logger = get_task_logger(__name__)


class DeliverHook(Task):
    def run(self, target, payload, instance=None, hook=None, **kwargs):
        """
        target:     the url to receive the payload.
        payload:    a python primitive data structure
        instance:   a possibly null "trigger" instance
        hook:       the defining Hook object
        """
        requests.post(
            url=target,
            data=json.dumps(payload),
            headers={
                'Content-Type': 'application/json',
                'Authorization': 'Token %s' % settings.HOOK_AUTH_TOKEN
            }
        )

deliver_hook_wrapper = DeliverHook.delay
