from django.conf.urls import url, include
from rest_framework import routers
from . import views

router = routers.DefaultRouter()
router.register(r'user', views.UserViewSet)
router.register(r'group', views.GroupViewSet)
router.register(r'schedule', views.ScheduleViewSet)
router.register(r'webhook', views.HookViewSet)
router.register(r'failed-tasks', views.FailedTaskViewSet)

# Wire up our API using automatic URL routing.
# Additionally, we include login URLs for the browseable API.
urlpatterns = [
    url(r'^api/v1/user/token/$', views.UserView.as_view(),
        name='create-user-token'),
    url(r'^api/v1/', include(router.urls)),
]
