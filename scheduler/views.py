from django.contrib.auth.models import User, Group
from .models import Schedule
from rest_hooks.models import Hook
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from .serializers import (UserSerializer, GroupSerializer,
                          ScheduleSerializer, HookSerializer)
from seed_scheduler.utils import get_available_metrics
# Uncomment line below if scheduled metrics are added
# from .tasks import scheduled_metrics


class HookViewSet(viewsets.ModelViewSet):
    """
    Retrieve, create, update or destroy webhooks.
    """
    permission_classes = (IsAuthenticated,)
    queryset = Hook.objects.all()
    serializer_class = HookSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class UserViewSet(viewsets.ReadOnlyModelViewSet):

    """
    API endpoint that allows users to be viewed or edited.
    """
    permission_classes = (IsAuthenticated,)
    queryset = User.objects.all()
    serializer_class = UserSerializer


class GroupViewSet(viewsets.ReadOnlyModelViewSet):

    """
    API endpoint that allows groups to be viewed or edited.
    """
    permission_classes = (IsAuthenticated,)
    queryset = Group.objects.all()
    serializer_class = GroupSerializer


class ScheduleViewSet(viewsets.ModelViewSet):

    """
    API endpoint that allows schedule models to be viewed or edited.
    """
    permission_classes = (IsAuthenticated,)
    queryset = Schedule.objects.all()
    serializer_class = ScheduleSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user,
                        updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)


class MetricsView(APIView):

    """ Metrics Interaction
        GET - returns list of all available metrics on the service
        POST - starts up the task that fires all the scheduled metrics
    """
    permission_classes = (IsAuthenticated,)

    def get(self, request, *args, **kwargs):
        status = 200
        resp = {
            "metrics_available": get_available_metrics()
        }
        return Response(resp, status=status)

    def post(self, request, *args, **kwargs):
        status = 201
        # Uncomment line below if scheduled metrics are added
        # scheduled_metrics.apply_async()
        resp = {"scheduled_metrics_initiated": True}
        return Response(resp, status=status)
