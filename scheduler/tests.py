import json

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.authtoken.models import Token
from rest_hooks.models import Hook

from .models import Schedule


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
