===========
Data Models
===========

Schedule
========

Fields
------

**id**
    A UUID 4 unique identifier for the record.

**frequency**
    (Deprecated) An optional integer number of times a task should be run in total.

**triggered**
    (Deprecated) An integer representing the number of times a task has been run.

**cron_definition**
    A character based representation of the schedule in cron format.

**celery_cron_definition**
    A reference to the Celery crontab schedule.

**interval_definition**
    An character based representation of the schedule in interval format.
    Given as an integer and period (from: days, hours, minutes, seconds,
    microseconds) e.g. `1 minute`.

**celery_interval_definition**
    A reference to the Celery interval schedule.

**endpoint**
    The URL to POST to when the task is run.

**auth_token**
    The auth token to use when POSTing to the `endpoint`.

**payload**
    The payload to use as the POST body to the `endpoint`.

**next_send_at**
    An rough estimate of when the next run is scheduled.

**enabled**
    A boolean enabled flag.

**created_at**
    A date and time field of when the record was created.

**updated_at**
    A date and time field of when the record was last updated.

**created_by**
    A reference to the User account that created this record.

**updated_by**
    A reference to the User account that last updated this record.
