import json
import responses
from freezegun import freeze_time
from uuid import uuid4
from django.utils.six import StringIO
from datetime import timedelta

try:
    from urllib.parse import urlparse, urlencode
except ImportError:
    from urlparse import urlparse
    from urllib import urlencode

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.conf import settings
from django.db.models.signals import post_save
from django.core.management import call_command
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.authtoken.models import Token
from rest_hooks.models import Hook
from requests_testadapter import TestAdapter, TestSession
from go_http.metrics import MetricsApiClient

from .models import (Schedule, fire_metrics_if_new, QueueTaskRun,
                     ScheduleFailure)
from .tasks import deliver_task, queue_tasks, fire_metric, requeue_failed_tasks
from . import tasks

from djcelery.models import PeriodicTask, CrontabSchedule, IntervalSchedule

from seed_scheduler import celery_app


class RecordingAdapter(TestAdapter):

    """ Record the request that was handled by the adapter.
    """
    request = None

    def send(self, request, *args, **kw):
        self.request = request
        return super(RecordingAdapter, self).send(request, *args, **kw)


class APITestCase(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.adminclient = APIClient()
        self.session = TestSession()


class AuthenticatedAPITestCase(APITestCase):

    def make_schedule(self):
        schedule_data = {
            "frequency": 2,
            "cron_definition": "25 * * * *",
            "interval_definition": None,
            "endpoint": "http://example.com",
            "payload": {}
        }
        return Schedule.objects.create(**schedule_data)

    def _replace_get_metric_client(self, session=None):
        return MetricsApiClient(
            auth_token=settings.METRICS_AUTH_TOKEN,
            api_url=settings.METRICS_URL,
            session=self.session)

    def _restore_get_metric_client(self, session=None):
        return MetricsApiClient(
            auth_token=settings.METRICS_AUTH_TOKEN,
            api_url=settings.METRICS_URL,
            session=session)

    def _replace_post_save_hooks(self):
        post_save.disconnect(fire_metrics_if_new, sender=Schedule)

    def _restore_post_save_hooks(self):
        post_save.connect(fire_metrics_if_new, sender=Schedule)

    def setUp(self):
        super(AuthenticatedAPITestCase, self).setUp()
        self._replace_post_save_hooks()
        tasks.get_metric_client = self._replace_get_metric_client

        self.username = 'testuser'
        self.password = 'testpass'
        self.user = User.objects.create_user(self.username,
                                             'testuser@example.com',
                                             self.password)
        token = Token.objects.create(user=self.user)
        self.token = token.key
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token)
        self.superuser = User.objects.create_superuser('testsu',
                                                       'su@example.com',
                                                       'dummypwd')
        sutoken = Token.objects.create(user=self.superuser)
        self.adminclient.credentials(
            HTTP_AUTHORIZATION='Token %s' % sutoken)

    def tearDown(self):
        self._restore_post_save_hooks()
        tasks.get_metric_client = self._restore_get_metric_client


class TestSchedudlerAppAPI(AuthenticatedAPITestCase):

    def test_login(self):
        request = self.client.post(
            '/api/token-auth/',
            {"username": "testuser", "password": "testpass"})
        token = request.data.get('token', None)
        self.assertIsNotNone(
            token, "Could not receive authentication token on login post.")
        self.assertEqual(request.status_code, 200,
                         "Status code on /api/token-auth was %s -should be 200"
                         % request.status_code)

    def test_create_schedule_cron(self):
        post_data = {
            "frequency": 2,
            "cron_definition": "25 * * * *",
            "interval_definition": None,
            "endpoint": "http://example.com",
            "payload": {},
            "auth_token": "blah"
        }
        response = self.client.post('/api/v1/schedule/',
                                    json.dumps(post_data),
                                    content_type='application/json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        d = Schedule.objects.last()
        self.assertEqual(d.frequency, 2)
        self.assertEqual(d.cron_definition, "25 * * * *")
        self.assertIsNotNone(d.celery_cron_definition)
        self.assertEqual(d.celery_cron_definition.minute, '25')
        self.assertEqual(d.auth_token, 'blah')

    def test_create_schedule_cron_mappings(self):
        base_schedule = {
            "frequency": 2,
            "cron_definition": "",
            "interval_definition": None,
            "endpoint": "http://example.com",
            "payload": {}
        }
        schedule1 = base_schedule.copy()
        schedule1["cron_definition"] = "25 * * * * *"
        d = Schedule.objects.create(**schedule1)
        self.assertEqual(d.celery_cron_definition.minute, '25')

        schedule2 = base_schedule.copy()
        schedule2["cron_definition"] = "* 14 * * * *"
        d = Schedule.objects.create(**schedule2)
        self.assertEqual(d.celery_cron_definition.hour, '14')

        schedule3 = base_schedule.copy()
        schedule3["cron_definition"] = "* * * * 1 *"
        d = Schedule.objects.create(**schedule3)
        self.assertEqual(d.celery_cron_definition.day_of_week, '1')

        schedule4 = base_schedule.copy()
        schedule4["cron_definition"] = "* * 14 * * *"
        d = Schedule.objects.create(**schedule4)
        self.assertEqual(d.celery_cron_definition.day_of_month, '14')

        schedule5 = base_schedule.copy()
        schedule5["cron_definition"] = "* * * 10 * *"
        d = Schedule.objects.create(**schedule5)
        self.assertEqual(d.celery_cron_definition.month_of_year, '10')

    def test_create_schedule_cron_failed(self):
        post_data = {
            "frequency": 2,
            "cron_definition": "99 * * * *",
            "interval_definition": None,
            "endpoint": "http://example.com",
            "payload": {}
        }
        response = self.client.post('/api/v1/schedule/',
                                    json.dumps(post_data),
                                    content_type='application/json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.json(),
            {
                "cron_definition": [
                    "99 * * * * is not a valid crontab string: item value 99 "
                    "out of range [0, 59]"
                ]
            })

    def test_create_schedule_interval(self):
        post_data = {
            "frequency": 2,
            "cron_definition": None,
            "interval_definition": "1 minutes",
            "endpoint": "http://example.com",
            "payload": {}
        }
        response = self.client.post('/api/v1/schedule/',
                                    json.dumps(post_data),
                                    content_type='application/json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        d = Schedule.objects.last()
        self.assertEqual(d.frequency, 2)
        self.assertEqual(d.interval_definition, "1 minutes")
        self.assertIsNotNone(d.celery_interval_definition)
        self.assertEqual(d.celery_interval_definition.every, 1)
        self.assertEqual(d.celery_interval_definition.period, "minutes")

    def test_create_schedule_interval_failed(self):
        post_data = {
            "frequency": 2,
            "cron_definition": None,
            "interval_definition": "every one mins",
            "endpoint": "http://example.com",
            "payload": {}
        }
        response = self.client.post('/api/v1/schedule/',
                                    json.dumps(post_data),
                                    content_type='application/json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.json(),
            {
                "interval_definition": [
                    "every one mins is not a valid interval string: "
                    "integer and period (from: days, hours, minutes, "
                    "seconds, microseconds) e.g. 1 minutes"
                ]
            })

    def test_update_schedule(self):
        # Setup
        s = self.make_schedule()
        post_data = {
            "enabled": False
        }
        # Precheck
        self.assertEqual(s.enabled, True)
        self.assertEqual(s.frequency, 2)
        self.assertEqual(s.cron_definition, "25 * * * *")
        self.assertEqual(s.interval_definition, None)
        self.assertEqual(s.endpoint, "http://example.com")
        # Execute
        response = self.client.patch('/api/v1/schedule/%s/' % s.id,
                                     json.dumps(post_data),
                                     content_type='application/json')
        s.refresh_from_db()
        # Check
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(s.enabled, False)
        self.assertEqual(s.frequency, 2)
        self.assertEqual(s.cron_definition, "25 * * * *")
        self.assertEqual(s.interval_definition, None)
        self.assertEqual(s.endpoint, "http://example.com")

    def test_create_webhook(self):
        # Setup
        user = User.objects.get(username='testuser')
        post_data = {
            "target": "http://example.com/registration/",
            "event": "schedule.added"
        }
        # Execute
        response = self.client.post('/api/v1/webhook/',
                                    json.dumps(post_data),
                                    content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        d = Hook.objects.last()
        self.assertEqual(d.target, 'http://example.com/registration/')
        self.assertEqual(d.user, user)

    # This test is not working despite the code working fine
    # If you run these same steps below interactively the webhook will fire
    # @responses.activate
    # def test_webhook(self):
    #     # Setup
    #     post_save.connect(receiver=model_saved, sender=DummyModel,
    #                       dispatch_uid='instance-saved-hook')
    #     Hook.objects.create(user=self.adminuser,
    #                         event='dummymodel.added',
    #                         target='http://example.com/registration/')
    #
    #     expected_webhook = {
    #         "hook": {
    #             "target": "http://example.com/registration/",
    #             "event": "dummymodel.added",
    #             "id": 3
    #         },
    #         "data": {
    #         }
    #     }
    #     responses.add(
    #         responses.POST,
    #         "http://example.com/registration/",
    #         json.dumps(expected_webhook),
    #         status=200, content_type='application/json')
    #     dummymodel_data = {
    #         "product_code": "BLAHBLAH",
    #         "data": {"stuff": "nonsense"}
    #     }
    #     dummy = DummyModel.objects.create(**dummymodel_data)
    #     # Execute
    #     self.assertEqual(responses.calls[0].request.url,
    #                      "http://example.com/registration/")


class TestSchedudlerTasks(AuthenticatedAPITestCase):

    @responses.activate
    def test_deliver_task(self):
        # Tests the deliver task directly
        # Setup
        expected_body = {
            "run": 1
        }
        responses.add(
            responses.POST,
            "http://example.com/trigger/",
            json.dumps(expected_body),
            status=200, content_type='application/json')

        schedule_data = {
            "frequency": 2,
            "cron_definition": "25 * * * *",
            "interval_definition": None,
            "endpoint": "http://example.com/trigger/",
            "payload": {"run": 1}
        }
        schedule = Schedule.objects.create(**schedule_data)

        # Execute
        result = deliver_task.apply_async(kwargs={
            "schedule_id": str(schedule.id)})

        # Check
        self.assertEqual(result.get(), True)
        self.assertEqual(responses.calls[0].request.url,
                         "http://example.com/trigger/")

    @responses.activate
    def test_queue_tasks_one_crontab(self):
        # Tests crontab based task runs
        # Setup
        expected_body = {
            "run": 1
        }
        responses.add(
            responses.POST,
            "http://example.com/trigger/",
            json.dumps(expected_body),
            status=200, content_type='application/json')

        schedule_data = {
            "frequency": 2,
            "cron_definition": "25 * * * *",
            "interval_definition": None,
            "endpoint": "http://example.com/trigger/",
            "payload": {"run": 1}
        }
        schedule = Schedule.objects.create(**schedule_data)

        # Execute
        result = queue_tasks.apply_async(kwargs={
            "schedule_type": "crontab",
            "lookup_id": schedule.celery_cron_definition.id})

        # Check
        self.assertEqual(result.get(), "Queued <1> Tasks")
        self.assertEqual(responses.calls[0].request.url,
                         "http://example.com/trigger/")

    @responses.activate
    def test_queue_tasks_one_interval(self):
        # Tests interval based task runs
        # Setup
        expected_body = {
            "run": 1
        }
        responses.add(
            responses.POST,
            "http://example.com/trigger/",
            json.dumps(expected_body),
            status=200, content_type='application/json')

        schedule_data = {
            "frequency": 2,
            "cron_definition": None,
            "interval_definition": "1 minutes",
            "endpoint": "http://example.com/trigger/",
            "payload": {"run": 1}
        }
        schedule = Schedule.objects.create(**schedule_data)

        # Execute
        result = queue_tasks.apply_async(kwargs={
            "schedule_type": "interval",
            "lookup_id": schedule.celery_interval_definition.id})

        # Check
        self.assertEqual(result.get(), "Queued <1> Tasks")
        self.assertEqual(responses.calls[0].request.url,
                         "http://example.com/trigger/")

    @responses.activate
    def test_queue_tasks_one_not_enabled(self):
        # Tests that with two schedules, one disabled it just runs active
        # Setup
        expected_body = {
            "run": 1
        }
        responses.add(
            responses.POST,
            "http://example.com/trigger/",
            json.dumps(expected_body),
            status=200, content_type='application/json')

        schedule_data = {
            "frequency": 2,
            "cron_definition": "25 * * * *",
            "interval_definition": None,
            "endpoint": "http://example.com/trigger/",
            "payload": {"run": 1}
        }
        Schedule.objects.create(**schedule_data)
        schedule_data = {
            "frequency": 10,
            "cron_definition": "25 * * * *",
            "interval_definition": None,
            "endpoint": "http://example.com/notrun/",
            "payload": {"run": 1}
        }
        donotrun = Schedule.objects.create(**schedule_data)
        donotrun.enabled = False
        donotrun.save()

        # Execute
        result = queue_tasks.apply_async(kwargs={
            "schedule_type": "crontab",
            "lookup_id": donotrun.celery_cron_definition.id})

        # Check
        self.assertEqual(result.get(), "Queued <1> Tasks")
        self.assertEqual(responses.calls[0].request.url,
                         "http://example.com/trigger/")

    @responses.activate
    def test_queue_tasks_one_with_none_frequency(self):
        # Tests that with two schedules, one with a None frequency, it runs two
        # Setup
        expected_body = {
            "run": 1
        }
        responses.add(
            responses.POST,
            "http://example.com/trigger/",
            json.dumps(expected_body),
            status=200, content_type='application/json')
        responses.add(
            responses.POST,
            "http://example.com/runnone/",
            json.dumps(expected_body),
            status=200, content_type='application/json')

        schedule_data = {
            "frequency": 2,
            "cron_definition": "25 * * * *",
            "interval_definition": None,
            "endpoint": "http://example.com/trigger/",
            "payload": {"run": 1}
        }
        Schedule.objects.create(**schedule_data)
        schedule_data = {
            "frequency": None,
            "cron_definition": "25 * * * *",
            "interval_definition": None,
            "endpoint": "http://example.com/runnone/",
            "payload": {"run": 1}
        }
        runnone = Schedule.objects.create(**schedule_data)
        runnone.save()

        # Execute
        result = queue_tasks.apply_async(kwargs={
            "schedule_type": "crontab",
            "lookup_id": runnone.celery_cron_definition.id})

        # Check
        self.assertEqual(result.get(), "Queued <2> Tasks")
        self.assertEqual(responses.calls[0].request.url,
                         "http://example.com/runnone/")
        self.assertEqual(responses.calls[1].request.url,
                         "http://example.com/trigger/")

    @responses.activate
    def test_queue_tasks_repeat(self):
        # Tests crontab based task runs
        # Setup
        expected_body = {
            "run": 1
        }
        responses.add(
            responses.POST,
            "http://example.com/trigger/",
            json.dumps(expected_body),
            status=200, content_type='application/json')

        schedule_data = {
            "frequency": 2,
            "cron_definition": "25 * * * *",
            "interval_definition": None,
            "endpoint": "http://example.com/trigger/",
            "payload": {"run": 1}
        }
        schedule = Schedule.objects.create(**schedule_data)

        # Execute
        result = queue_tasks.apply_async(kwargs={
            "schedule_type": "crontab",
            "lookup_id": schedule.celery_cron_definition.id})

        # Check
        self.assertEqual(result.get(), "Queued <1> Tasks")
        self.assertEqual(responses.calls[0].request.url,
                         "http://example.com/trigger/")

        # Try run again
        result = queue_tasks.apply_async(kwargs={
            "schedule_type": "crontab",
            "lookup_id": schedule.celery_cron_definition.id})

        # Check
        self.assertIn("Aborted Queuing", result.get())
        self.assertEqual(QueueTaskRun.objects.all().count(), 1)

    @responses.activate
    def test_queue_tasks_repeat_after_interval(self):
        expected_body = {
            "run": 1
        }
        responses.add(
            responses.POST,
            "http://example.com/trigger/",
            json.dumps(expected_body),
            status=200, content_type='application/json')

        schedule_data = {
            "frequency": 2,
            "cron_definition": "25 * * * *",
            "interval_definition": None,
            "endpoint": "http://example.com/trigger/",
            "payload": {"run": 1}
        }
        schedule = Schedule.objects.create(**schedule_data)

        # Create a previous run
        d1 = timezone.now()
        d1 = d1.replace(minute=20)
        QueueTaskRun.objects.create(
            celery_cron_definition=schedule.celery_cron_definition,
            task_id=uuid4(),
            started_at=d1 - timedelta(hours=1),
            completed_at=d1 + timedelta(minutes=5)
        )

        # Execute
        result = queue_tasks.apply_async(kwargs={
            "schedule_type": "crontab",
            "lookup_id": schedule.celery_cron_definition.id})

        # Check
        self.assertEqual(result.get(), "Queued <1> Tasks")
        self.assertEqual(responses.calls[0].request.url,
                         "http://example.com/trigger/")
        self.assertEqual(QueueTaskRun.objects.all().count(), 2)

    @responses.activate
    @freeze_time("2017-01-01 17:24:00")
    @override_settings(DEFAULT_CLOCK_SKEW_SECONDS=120)  # 2 minutes
    def test_queue_tasks_repeat_clock_skew(self):
        expected_body = {
            "run": 1
        }
        responses.add(
            responses.POST,
            "http://example.com/trigger/",
            json.dumps(expected_body),
            status=200, content_type='application/json')

        schedule_data = {
            "frequency": 2,
            "cron_definition": "25 * * * *",
            "interval_definition": None,
            "endpoint": "http://example.com/trigger/",
            "payload": {"run": 1}
        }
        schedule = Schedule.objects.create(**schedule_data)

        # We allow for 2 minutes of clock skew in DEFAULT_CLOCK_SKEW_SECONDS
        # With current date the next run should only be in 1 minute, this is
        # inside our 2 minute allowance and we let it run

        # Create a previous run
        d1 = timezone.now()
        QueueTaskRun.objects.create(
            celery_cron_definition=schedule.celery_cron_definition,
            task_id=uuid4(),
            started_at=d1 - timedelta(minutes=59),
            completed_at=d1
        )

        # Execute
        result = queue_tasks.apply_async(kwargs={
            "schedule_type": "crontab",
            "lookup_id": schedule.celery_cron_definition.id})

        # Check
        self.assertEqual(result.get(), "Queued <1> Tasks")
        self.assertEqual(responses.calls[0].request.url,
                         "http://example.com/trigger/")
        self.assertEqual(QueueTaskRun.objects.all().count(), 2)

    @responses.activate
    @freeze_time("2017-01-01 17:24:00")
    @override_settings(DEFAULT_CLOCK_SKEW_SECONDS=10)  # 10 seconds
    def test_queue_tasks_repeat_clock_skew_stop(self):
        expected_body = {
            "run": 1
        }
        responses.add(
            responses.POST,
            "http://example.com/trigger/",
            json.dumps(expected_body),
            status=200, content_type='application/json')

        schedule_data = {
            "frequency": 2,
            "cron_definition": "25 * * * *",
            "interval_definition": None,
            "endpoint": "http://example.com/trigger/",
            "payload": {"run": 1}
        }
        schedule = Schedule.objects.create(**schedule_data)

        # We allow for 10 seconds of clock skew in DEFAULT_CLOCK_SKEW_SECONDS
        # With current date the next run should only be in 1 minute, this is
        # not inside our 10 second allowance and we it is stopped

        # Create a previous run
        d1 = timezone.now()
        QueueTaskRun.objects.create(
            celery_cron_definition=schedule.celery_cron_definition,
            task_id=uuid4(),
            started_at=d1 - timedelta(minutes=59),
            completed_at=d1
        )

        # Execute
        result = queue_tasks.apply_async(kwargs={
            "schedule_type": "crontab",
            "lookup_id": schedule.celery_cron_definition.id})

        # Check
        self.assertIn("Aborted Queuing", result.get())
        self.assertEqual(QueueTaskRun.objects.all().count(), 1)

    @responses.activate
    def test_requeue_failed_tasks(self):
        expected_body = {
            "run": 1
        }

        responses.add(
            responses.POST,
            "http://example.com/trigger/",
            json.dumps(expected_body),
            status=200, content_type='application/json')

        schedule_data = {
            "cron_definition": "25 * * * *",
            "interval_definition": None,
            "endpoint": "http://example.com/trigger/",
            "payload": {"run": 1}
        }
        schedule = Schedule.objects.create(**schedule_data)
        ScheduleFailure.objects.create(
            schedule=schedule,
            task_id=uuid4(),
            initiated_at=timezone.now(),
            reason='Error'
        )

        requeue_failed_tasks()

        # Check
        self.assertEqual(responses.calls[0].request.url,
                         "http://example.com/trigger/")
        self.assertEqual(ScheduleFailure.objects.all().count(), 0)


class TestMetricsAPI(AuthenticatedAPITestCase):

    def test_metrics_read(self):
        # Setup
        # Execute
        response = self.client.get('/api/metrics/',
                                   content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["metrics_available"], [
                'schedules.created.sum',
                'scheduler.deliver_task.connection_error.sum',
                'scheduler.deliver_task.http_error.400.sum',
                'scheduler.deliver_task.http_error.401.sum',
                'scheduler.deliver_task.http_error.403.sum',
                'scheduler.deliver_task.http_error.404.sum',
                'scheduler.deliver_task.http_error.500.sum',
                'scheduler.deliver_task.timeout.sum',
            ]
        )

    @responses.activate
    def test_post_metrics(self):
        # Setup
        # deactivate Testsession for this test
        self.session = None
        responses.add(responses.POST,
                      "http://metrics-url/metrics/",
                      json={"foo": "bar"},
                      status=200, content_type='application/json')
        # Execute
        response = self.client.post('/api/metrics/',
                                    content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["scheduled_metrics_initiated"], True)


class TestMetrics(AuthenticatedAPITestCase):

    def check_request(
            self, request, method, params=None, data=None, headers=None):
        self.assertEqual(request.method, method)
        if params is not None:
            url = urlparse.urlparse(request.url)
            qs = urlparse.parse_qsl(url.query)
            self.assertEqual(dict(qs), params)
        if headers is not None:
            for key, value in headers.items():
                self.assertEqual(request.headers[key], value)
        if data is None:
            self.assertEqual(request.body, None)
        else:
            self.assertEqual(json.loads(request.body), data)

    def _mount_session(self):
        response = [{
            'name': 'foo',
            'value': 9000,
            'aggregator': 'bar',
        }]
        adapter = RecordingAdapter(json.dumps(response).encode('utf-8'))
        self.session.mount(
            "http://metrics-url/metrics/", adapter)
        return adapter

    def test_direct_fire(self):
        # Setup
        adapter = self._mount_session()
        # Execute
        result = fire_metric.apply_async(kwargs={
            "metric_name": 'foo.last',
            "metric_value": 1,
            "session": self.session
        })
        # Check
        self.check_request(
            adapter.request, 'POST',
            data={"foo.last": 1.0}
        )
        self.assertEqual(result.get(),
                         "Fired metric <foo.last> with value <1.0>")

    def test_created_metrics(self):
        # Setup
        adapter = self._mount_session()
        # reconnect metric post_save hook
        post_save.connect(fire_metrics_if_new, sender=Schedule)

        # Execute
        self.make_schedule()

        # Check
        self.check_request(
            adapter.request, 'POST',
            data={"schedules.created.sum": 1.0}
        )
        # remove post_save hooks to prevent teardown errors
        post_save.disconnect(fire_metrics_if_new, sender=Schedule)


class TestUserCreation(AuthenticatedAPITestCase):

    def test_create_user_and_token(self):
        # Setup
        user_request = {"email": "test@example.org"}
        # Execute
        request = self.adminclient.post('/api/v1/user/token/', user_request)
        token = request.json().get('token', None)
        # Check
        self.assertIsNotNone(
            token, "Could not receive authentication token on post.")
        self.assertEqual(
            request.status_code, 201,
            "Status code on /api/v1/user/token/ was %s (should be 201)."
            % request.status_code)

    def test_create_user_and_token_fail_nonadmin(self):
        # Setup
        user_request = {"email": "test@example.org"}
        # Execute
        request = self.client.post('/api/v1/user/token/', user_request)
        error = request.json().get('detail', None)
        # Check
        self.assertIsNotNone(
            error, "Could not receive error on post.")
        self.assertEqual(
            error, "You do not have permission to perform this action.",
            "Error message was unexpected: %s."
            % error)

    def test_create_user_and_token_not_created(self):
        # Setup
        user_request = {"email": "test@example.org"}
        # Execute
        request = self.adminclient.post('/api/v1/user/token/', user_request)
        token = request.json().get('token', None)
        # And again, to get the same token
        request2 = self.adminclient.post('/api/v1/user/token/', user_request)
        token2 = request2.json().get('token', None)

        # Check
        self.assertEqual(
            token, token2,
            "Tokens are not equal, should be the same as not recreated.")

    def test_create_user_new_token_nonadmin(self):
        # Setup
        user_request = {"email": "test@example.org"}
        request = self.adminclient.post('/api/v1/user/token/', user_request)
        token = request.json().get('token', None)
        cleanclient = APIClient()
        cleanclient.credentials(HTTP_AUTHORIZATION='Token %s' % token)
        # Execute
        request = cleanclient.post('/api/v1/user/token/', user_request)
        error = request.json().get('detail', None)
        # Check
        # new user should not be admin
        self.assertIsNotNone(
            error, "Could not receive error on post.")
        self.assertEqual(
            error, "You do not have permission to perform this action.",
            "Error message was unexpected: %s."
            % error)


class TestHealthcheckAPI(AuthenticatedAPITestCase):

    def test_healthcheck_read(self):
        # Setup
        # Execute
        response = self.client.get('/api/health/',
                                   content_type='application/json')
        # Check
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["up"], True)
        self.assertEqual(response.data["result"]["database"], "Accessible")


@celery_app.task
def noop(argument):
    return argument


class TestTriggerPeriodicTask(TestCase):

    timeout = 1

    def test_trigger_periodic_task(self):
        cs = CrontabSchedule.objects.create(**{
            "minute": '*',
            "hour": '*',
            "day_of_week": '*',
            "day_of_month": '*',
            "month_of_year": '*',
        })

        pt = PeriodicTask.objects.create(**{
            "name": "noop task",
            "task": "scheduler.tests.noop",
            "crontab": cs,
            "enabled": True,
            "args": '["hello world"]',
        })
        stdout = StringIO()
        stderr = StringIO()
        self.assertFalse(pt.last_run_at)
        call_command('trigger_periodic_task', str(pt.pk),
                     '--confirm',
                     stdout=stdout, stderr=stderr)
        self.assertEqual(stdout.getvalue().strip(), 'hello world')
        pt_after_run = PeriodicTask.objects.get(pk=pt.pk)
        self.assertTrue(pt_after_run.last_run_at)


class TestTriggerDeliverTasks(TestCase):

    timeout = 1

    def setUp(self):
        post_save.disconnect(fire_metrics_if_new, sender=Schedule)
        self.session = TestSession()
        self.crontab_schedule = CrontabSchedule.objects.create(**{
            "minute": '*',
            "hour": '*',
            "day_of_week": '*',
            "day_of_month": '*',
            "month_of_year": '*',
        })

        self.periodic_crontab_task = PeriodicTask.objects.create(**{
            "name": "noop crontab task",
            "task": "scheduler.tests.noop",
            "crontab": self.crontab_schedule,
            "enabled": True,
            "args": '["hello world"]',
        })

        self.interval_schedule = IntervalSchedule.objects.create(**{
            'every': 5,
            'period': 'minutes',
        })

        self.periodic_interval_task = PeriodicTask.objects.create(**{
            'name': 'noop interval task',
            'task': 'scheduler.tests.noop',
            'interval': self.interval_schedule,
            'enabled': True,
            'args': '["hello world"]',
        })

    def mount_subscription(self, subscription_id, msisdns, identity_id=None):
        identity_id = identity_id or ('identity-for-%s' % (subscription_id,))
        responses.add(
            responses.GET,
            'http://sbm.example.org/api/v1/subscriptions/%s/' % (
                subscription_id,),
            status=200, json={
                'identity': identity_id,
            })

        addresses = [(msisdns[0], {'default': True})]
        for msisdn in msisdns[1:]:
            addresses.append((msisdn, {}))

        responses.add(
            responses.GET,
            'http://idstore.example.org/api/v1/identities/%s/' % (
                identity_id,),
            status=200, json={
                'details': {
                    'addresses': {
                        'msisdn': dict(addresses)
                    },
                    'default_addr_type': 'msisdn',
                }
            })

    def mount_outbound(self, msisdn, since, until, outbound_count=0):
        responses.add(
            responses.GET,
            'http://sender.example.org/api/v1/outbound/?%s' % (urlencode({
                'to_addr': msisdn,
                'after': since.isoformat(),
                'before': until.isoformat(),
            }),),
            status=200, body=json.dumps([{
                "to_addr": msisdn,
                "content": counter,
            } for counter in range(outbound_count)]),
            match_querystring=True)

    def mount_sbm_send(self, subscription_id):
        responses.add(
            responses.POST,
            'http://sbm.example.org/api/v1/subscriptions/%s/send' % (
                subscription_id,),
            status=200, body='{}')

    def sbm_send_happened(self, subscription_id):
        sbm_url = 'http://sbm.example.org/api/v1/subscriptions/%s/send' % (
            subscription_id,)
        for call in responses.calls:
            request = call.request
            if request.url == sbm_url:
                return True
        return False

    def assertSBMSend(self, subscription_id):
        self.assertTrue(self.sbm_send_happened(subscription_id),
                        'Expected SBM to send for %s but didn\'t.' % (
                            subscription_id,
                        ))

    def assertNotSBMSend(self, subscription_id):
        self.assertFalse(self.sbm_send_happened(subscription_id),
                         'Expected SBM to not send for %s but did.' % (
                            subscription_id,
                         ))

    @responses.activate
    def test_deliver_tasks_interval(self):

        since = timezone.now() - timedelta(days=1)
        until = timezone.now() + timedelta(days=1)

        stdout = StringIO()
        stderr = StringIO()

        Schedule.objects.create(
            enabled=True,
            celery_interval_definition=self.interval_schedule,
            endpoint=('http://sbm.example.org/api/v1/subscriptions'
                      '/subscription-uuid-1/send'),
            auth_token='sbm_token',
            payload={})

        self.mount_subscription(
            'subscription-uuid-1', ['+27000000000', '+27111111111'])
        self.mount_outbound(
            '+27000000000', since=since, until=until, outbound_count=1)
        self.mount_outbound(
            '+27111111111', since=since, until=until, outbound_count=0)

        resend = Schedule.objects.create(
            enabled=True,
            celery_interval_definition=self.interval_schedule,
            endpoint=('http://sbm.example.org/api/v1/subscriptions'
                      '/subscription-uuid-2/send'),
            auth_token='sbm_token',
            payload={})

        self.mount_subscription(
            'subscription-uuid-2', ['+27222222222'])
        self.mount_outbound(
            '+27222222222', since=since, until=until, outbound_count=0)
        self.mount_sbm_send('subscription-uuid-2')

        call_command(
            'trigger_deliver_tasks',
            '--identity-store-token', 'token',
            '--identity-store-url', 'http://idstore.example.org/api/v1',
            '--message-sender-token', 'token',
            '--message-sender-url', 'http://sender.example.org/api/v1',
            '--since', since.isoformat(),
            '--until', until.isoformat(),
            '--confirm',
            'interval:%s' % (self.interval_schedule.pk,),
            stdout=stdout, stderr=stderr)

        self.assertNotSBMSend('subscription-uuid-1')
        self.assertSBMSend('subscription-uuid-2')
        self.assertEqual(stdout.getvalue().strip(), str(resend))

    @responses.activate
    def test_deliver_tasks_dry_run(self):

        since = timezone.now() - timedelta(days=1)
        until = timezone.now() + timedelta(days=1)

        stdout = StringIO()
        stderr = StringIO()

        resend = Schedule.objects.create(
            enabled=True,
            celery_interval_definition=self.interval_schedule,
            endpoint=('http://sbm.example.org/api/v1/subscriptions'
                      '/subscription-uuid/send'),
            auth_token='sbm_token',
            payload={})

        self.mount_subscription(
            'subscription-uuid', ['+27222222222'])
        self.mount_outbound(
            '+27222222222', since=since, until=until, outbound_count=0)

        call_command(
            'trigger_deliver_tasks',
            '--identity-store-token', 'token',
            '--identity-store-url', 'http://idstore.example.org/api/v1',
            '--message-sender-token', 'token',
            '--message-sender-url', 'http://sender.example.org/api/v1',
            '--since', since.isoformat(),
            '--until', until.isoformat(),
            '--confirm',
            '--dry-run',
            'interval:%s' % (self.interval_schedule.pk,),
            stdout=stdout, stderr=stderr)

        self.assertNotSBMSend('subscription-uuid')
        self.assertEqual(
            stdout.getvalue().strip(),
            'Dry run for %s' % (str(resend),))


class TestFailedTaskAPI(AuthenticatedAPITestCase):

    @responses.activate
    def test_failed_tasks_requeue(self):
        expected_body = {
            "run": 1
        }

        responses.add(
            responses.POST,
            "http://example.com/trigger/",
            json.dumps(expected_body),
            status=200, content_type='application/json')

        schedule_data = {
            "cron_definition": "25 * * * *",
            "interval_definition": None,
            "endpoint": "http://example.com/trigger/",
            "payload": {"run": 1}
        }
        schedule = Schedule.objects.create(**schedule_data)
        ScheduleFailure.objects.create(
            schedule=schedule,
            task_id=uuid4(),
            initiated_at=timezone.now(),
            reason='Error'
        )

        response = self.client.post('/api/v1/failed-tasks/',
                                    content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["requeued_failed_tasks"], True)
        self.assertEqual(responses.calls[0].request.url,
                         "http://example.com/trigger/")
        self.assertEqual(ScheduleFailure.objects.all().count(), 0)
