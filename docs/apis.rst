===========
API Details
===========

The Scheduler provides REST like API with JSON payloads.

The root URL for all of the endpoints is:

    :samp:`https://{<scheduler-domain>}/api/`


Authenticating to the API
=========================

Please see the :doc:`Authentication and Authorization <auth>` document.

Pagination
==========

When the results set is larger than a configured amount, the data is broken up
into pages using the limit and offset parameters.

Paginated endpoints will provide information about the total amount of items
available along with links to the previous and next pages (where available) in
the returned JSON data.

.. http:get:: /(any)/
    :noindex:

    :query limit: the amount of record to limit a page of results to.
    :query offset: the starting position of the query in relation to the complete set of unpaginated items
    :>json int count: the total number of results available
    :>json string previous: the URL to the previous page of results (if available)
    :>json string next: the URL to the next page of results (if available)

    **Example request**:

    .. sourcecode:: http

        HTTP/1.1 200 OK
        Content-Type: application/json

        {
            "count": 50,
            "next": "http://sbm.example.org/api/v1/enpoint/?limit=10&offset=30",
            "previous": "http://smb.example.org/api/v1/endpoint/?limit=10&offset=10",
            "results": []
        }


Endpoints
=========

The endpoints provided by the Seed Scheduler are split into two categories,
core endpoints and helper endpoints

Core
----

The root URL for all of the core endpoints includes the version prefix
(:samp:`https://{<scheduler-domain>}/api/v1/`)

.. http:post:: /user/token/

    Creates a user and token for the given email address.

    If a user already exists for the given email address, the existing user
    account is used to generate a new token.

    :<json string email: the email address of the user to create or use.
    :>json string token: the auth token generated for the given user.
    :status 201: token successfully created.
    :status 400: an email address was not provided or was invalid.
    :status 401: the token is invalid/missing.


    **Example request**:

    .. sourcecode:: http

        POST /user/token/ HTTP/1.1
        Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b

        {
            "email": "bob@example.org"
        }


    **Example response**:

    .. sourcecode:: http

        HTTP/1.1 201 Created
        Content-Type: application/json

        {
            "token": "c05fbab6d5f912429052830c77eeb022249324cb"
        }

Users and Groups
~~~~~~~~~~~~~~~~

.. http:get:: /user/

    Returns a list of users for the Seed Scheduler service.

    :status 200: no error.
    :status 401: the token is invalid/missing.

    **Example request**:

    .. sourcecode:: http

        GET /user/ HTTP/1.1
        Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b

    **Example response**:

    .. sourcecode:: http

        HTTP/1.1 200 OK
        Content-Type: application/json

        {
            "count": 1,
            "next": null,
            "previous": null,
            "results": [
                {
                    "email": "john@example.org",
                    "groups": [],
                    "url": "http://scheduler.example.org/api/v1/user/1/",
                    "username": "john"
                }
            ]
        }

.. http:get:: /user/(int:user_id)/

    Returns the details of the specified user ID.

    :param user_id: a user's unique ID.
    :type user_id: int
    :status 200: no error.
    :status 401: the token is invalid/missing.

    **Example request**:

    .. sourcecode:: http

        GET /user/1/ HTTP/1.1
        Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b

    **Example response**:

    .. sourcecode:: http

        HTTP/1.1 200 OK
        Content-Type: application/json

        {
            "email": "john@example.org",
            "groups": [],
            "url": "http://scheduler.example.org/api/v1/user/1/",
            "username": "john"
        }

.. http:get:: /group/

    Returns a list of groups for the Seed Scheduler service.

    :status 200: no error
    :status 401: the token is invalid/missing.

    **Example request**:

    .. sourcecode:: http

        GET /group/ HTTP/1.1
        Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b


    **Example response**:

    .. sourcecode:: http

        HTTP/1.1 200 OK
        Content-Type: application/json

        {
            "count": 1,
            "next": null,
            "previous": null,
            "results": [
                {
                    "name": "Admins",
                    "url": "http://scheduler.example.org/api/v1/group/1/"
                }
            ]
        }

.. http:get:: /group/(int:group_id)/

    Returns the details of the specified group ID.

    :param group_id: a group's unique ID.
    :type group_id: int
    :status 200: no error.
    :status 401: the token is invalid/missing.

    **Example request**:

    .. sourcecode:: http

        GET /group/1/ HTTP/1.1
        Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b


    **Example response**:

    .. sourcecode:: http

        HTTP/1.1 200 OK
        Content-Type: application/json

        {
            "name": "Admins",
            "url": "http://scheduler.example.org/api/v1/group/1/"
        }


Schedules
~~~~~~~~~

.. http:get:: /schedule/

    Returns a list of Schedules.

.. http:post:: /schedule/

    Creates a new Schedule.

    :<json int frequency: (Deprecated) an optional integer number of times a task should be run in total.
    :<json string endpoint: a URL to POST to when this schedule is run.
    :<json string cron_definition: A crontab definition of when to run this schedule.
    :<json string inteval_definition: An interval definition of when to run this schedule.
    :<json string auth_token: An auth token to use when POSTing to the endpoint.
    :<json json payload: The JSON payload to include when POSTing to the endpoint.
    :<json boolean enabled: A boolean flag of whether this schedule is enabled.

    :resheader Location: the URL to the newly created resource.

    :status 201: created.
    :status 400: invalid data.
    :status 401: the token is invalid/missing.

.. http:get:: /schedule/(uuid:schedule_id)/

    Retuns the Schedule record for a given schedule_id.

.. http:put:: /schedule/(uuid:schedule_id)/

    Updates the Schedule record for a given schedule_id.

    :<json int frequency: an optional integer number of times a task should be run in total.
    :<json string endpoint: a URL to POST to when this schedule is run.
    :<json string cron_definition: A crontab definition of when to run this schedule.
    :<json string inteval_definition: An interval definition of when to run this schedule.
    :<json string auth_token: An auth token to use when POSTing to the endpoint.
    :<json json payload: The JSON payload to include when POSTing to the endpoint.
    :<json boolean enabled: A boolean flag of whether this schedule is enabled.

    :status 200: updated.
    :status 400: invalid data.
    :status 401: the token is invalid/missing.

.. http:delete:: /schedule/(uuid:schedule_id)/

    Deletes the Schedule record for a given schedule_id.


Helpers
-------

The root URL for the helper endpoints does not include a version prefix
(:samp:`https://{<scheduler-domain>}/api/`)

.. http:get:: /metrics/
    :noindex:

    Returns a list of all the available metric keys provided by this service.

    :status 200: no error
    :status 401: the token is invalid/missing.

.. http:post:: /metrics/
    :noindex:

    Starts a task that fires all scheduled metrics.

    :status 200: no error
    :status 401: the token is invalid/missing.

.. http:get:: /health/
    :noindex:

    Returns a basic health check status.

    :status 200: no error
    :status 401: the token is invalid/missing.
