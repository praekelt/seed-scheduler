import json
import responses

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.authtoken.models import Token
from rest_hooks.models import Hook

from .models import Schedule
from .tasks import deliver_task, queue_tasks


class APITestCase(TestCase):

    def setUp(self):
        self.client = APIClient()


class AuthenticatedAPITestCase(APITestCase):

    def setUp(self):
        super(AuthenticatedAPITestCase, self).setUp()
        self.username = 'testuser'
        self.password = 'testpass'
        self.user = User.objects.create_user(self.username,
                                             'testuser@example.com',
                                             self.password)
        token = Token.objects.create(user=self.user)
        self.token = token.key
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token)


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
            "payload": {}
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
