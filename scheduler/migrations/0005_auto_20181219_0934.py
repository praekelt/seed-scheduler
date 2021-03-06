# Generated by Django 2.1.4 on 2018-12-19 09:34

import django.contrib.postgres.fields.jsonb
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("scheduler", "0004_schedulefailure")]

    operations = [
        migrations.AlterField(
            model_name="schedule",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="schedules_created",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="schedule",
            name="payload",
            field=django.contrib.postgres.fields.jsonb.JSONField(
                blank=True, default=dict, null=True
            ),
        ),
        migrations.AlterField(
            model_name="schedule",
            name="updated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="schedules_updated",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
