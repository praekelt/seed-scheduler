from django.contrib import admin

from .models import Schedule


class ScheduleAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'triggered', 'cron_definition', 'interval_definition',
        'endpoint', 'next_send_at', 'enabled', 'created_at', 'updated_at',)
    list_filter = (
        'cron_definition', 'interval_definition', 'enabled', 'next_send_at',
        'created_at', 'updated_at', )
    search_fields = ['id', 'endpoint']


admin.site.register(Schedule, ScheduleAdmin)
