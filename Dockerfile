# This is a development Dockerfile. For versioned Dockerfiles see:
# https://github.com/praekeltfoundation/docker-seed
FROM praekeltfoundation/django-bootstrap:py3

COPY . /app
RUN pip install -e .

ENV DJANGO_SETTINGS_MODULE "seed_scheduler.settings"
RUN python manage.py collectstatic --noinput
CMD ["seed_scheduler.wsgi:application"]
