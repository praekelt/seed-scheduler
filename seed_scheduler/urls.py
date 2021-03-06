import os

from django.conf.urls import include, url
from django.contrib import admin
from django.urls import path
from django_prometheus import exports as django_prometheus
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.documentation import include_docs_urls

from scheduler import views
from seed_scheduler.decorators import internal_only

admin.site.site_header = os.environ.get("SCHEDULER_TITLE", "Scheduler Admin")


urlpatterns = [
    path("admin/", admin.site.urls),
    url(r"^api/auth/", include("rest_framework.urls", namespace="rest_framework")),
    url(r"^api/token-auth/", obtain_auth_token),
    url(r"^api/metrics/", views.MetricsView.as_view()),
    url(r"^api/health/", views.HealthcheckView.as_view()),
    url(r"^", include("scheduler.urls")),
    path("docs/", include_docs_urls(title=admin.site.site_header)),
    path(
        "metrics", internal_only(django_prometheus.ExportToDjangoView), name="metrics"
    ),
]
