FROM praekeltfoundation/django-bootstrap
ENV DJANGO_SETTINGS_MODULE "seed_scheduler.settings"
RUN ./manage.py collectstatic --noinput
ENV APP_MODULE "seed_scheduler.wsgi:application"
