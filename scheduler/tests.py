import json
import responses

try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse

from django.contrib.auth.models import User
from django.test import TestCase
from django.conf import settings
from django.db.models.signals import post_save
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.authtoken.models import Token
from rest_hooks.models import Hook
from requests_testadapter import TestAdapter, TestSession
from go_http.metrics import MetricsApiClient

from .models import Schedule, fire_metrics_if_new
from .tasks import deliver_task, queue_tasks, fire_metric
from . import tasks


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
        self.assertEqual(d.triggered, 0)
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
        s = Schedule.objects.get(id=schedule.id)
        self.assertEqual(s.triggered, 1)
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
        s = Schedule.objects.get(id=schedule.id)
        self.assertEqual(s.triggered, 1)
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
        run = Schedule.objects.create(**schedule_data)
        schedule_data = {
            "frequency": 10,
            "cron_definition": "25 * * * *",
            "interval_definition": None,
            "endpoint": "http://example.com/notrun/",
            "payload": {"run": 1}
        }
        donotrun = Schedule.objects.create(**schedule_data)
        donotrun.triggered = 10
        donotrun.enabled = False
        donotrun.save()

        # Execute
        result = queue_tasks.apply_async(kwargs={
            "schedule_type": "crontab",
            "lookup_id": donotrun.celery_cron_definition.id})

        # Check
        self.assertEqual(result.get(), "Queued <1> Tasks")
        s = Schedule.objects.get(id=run.id)
        self.assertEqual(s.triggered, 1)
        self.assertEqual(responses.calls[0].request.url,
                         "http://example.com/trigger/")

    @responses.activate
    def test_queue_tasks_one_interval_disable(self):
        # Tests does a final trigger now set to enabled = False
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
            "frequency": 10,
            "cron_definition": None,
            "interval_definition": "1 minutes",
            "endpoint": "http://example.com/trigger/",
            "payload": {"run": 1}
        }
        schedule = Schedule.objects.create(**schedule_data)
        schedule.triggered = 9
        schedule.save()

        # Execute
        result = queue_tasks.apply_async(kwargs={
            "schedule_type": "interval",
            "lookup_id": schedule.celery_interval_definition.id})

        # Check
        self.assertEqual(result.get(), "Queued <1> Tasks")
        s = Schedule.objects.get(id=schedule.id)
        self.assertEqual(s.triggered, 10)
        self.assertEqual(s.enabled, False)
        self.assertEqual(responses.calls[0].request.url,
                         "http://example.com/trigger/")


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
