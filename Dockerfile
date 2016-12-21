FROM praekeltfoundation/django-bootstrap:onbuild
ENV DJANGO_SETTINGS_MODULE "seed_scheduler.settings"
RUN python manage.py collectstatic --noinput
ENV APP_MODULE "seed_scheduler.wsgi:application"
