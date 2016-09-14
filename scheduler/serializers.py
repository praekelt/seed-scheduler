from django.contrib.auth.models import User, Group
from .models import Schedule
from rest_hooks.models import Hook
from rest_framework import serializers


class UserSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = User
        fields = ('url', 'username', 'email', 'groups')


class GroupSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Group
        fields = ('url', 'name')


class CreateUserSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ScheduleSerializer(serializers.HyperlinkedModelSerializer):

    class Meta:
        model = Schedule
        read_only_fields = ('created_by', 'updated_by',
                            'celery_cron_definition',
                            'celery_interval_definition')
        fields = ('url', 'id', 'frequency', 'cron_definition',
                  'interval_definition', 'endpoint', 'payload', 'auth_token',
                  'next_send_at', 'enabled', 'created_at', 'created_by',
                  'updated_at', 'updated_by')


class HookSerializer(serializers.ModelSerializer):

    class Meta:
        model = Hook
        read_only_fields = ('user',)
