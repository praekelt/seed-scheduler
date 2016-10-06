=================================
Production requirements and setup
=================================

.. _running-in-production:

Running in Production
=====================

The Seed Scheduler is expected to be run in a Docker container and as such
a Docker file is provided in the source code repository.

The web service portion and celery work portion of the Scheduler are expected
to be run in different instances of the same Docker container.

An example production setup might look like this:

.. image:: _images/scheduler-production.png
