==============
Seed Scheduler
==============

The Seed Scheduler is one of the microservices in the Seed Stack.

The Scheduler has the following key responsibilities:

- Allow other services to create scheduled callbacks via the API.
- At the scheduled time, call the configured URLs with the configured payload.
