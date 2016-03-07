FROM praekeltfoundation/django-bootstrap
ENV DJANGO_SETTINGS_MODULE "seed_scheduler.settings"
RUN django-admin collectstatic --noinput
CMD ["seed_scheduler.wsgi:application"]
