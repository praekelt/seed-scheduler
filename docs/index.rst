============================
Seed Scheduler documentation
============================

The Seed Scheduler is one of the microservices in the Seed Stack.

The Scheduler has the following key responsibilities:

- Allow other services to create scheduled callbacks via the API.
- At the scheduled time, call the configured URLs with the configured payload.

Getting started
===============

The following resources are provided to help you get started running or
developing the Seed Scheduler:

* Learn about the :doc:`Requirements <requirements>` for running the service
  and basic :doc:`Setup <setup>` instructions.
* Read about the :doc:`Data Models <models>` used by the service.
* Read about the :doc:`Authorization <auth>` requirements.
* Browse the :doc:`API Documentation <apis>` for the available endpoints and
  parameters.
* Learn about what is required when running the service in
  :doc:`Production <production>`


How the task scheduling works
=============================

The Seed Scheduler uses Celery and the django-celery integration package to
handle the scheduling of the periodic tasks.

The schedules are kept in tables in the PostgreSQL database that a Celery beat
worker processes and then hands off actual task execution to another Celery
worker process.

When a new Schedule object is created via the :doc:`Seed Scheduler API <apis>`,
a corresponding django-celery PeriodicTask object is created for that schedule.
