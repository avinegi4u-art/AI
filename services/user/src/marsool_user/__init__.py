"""Marsool user service.

Owns everything about *who* a user is: profile, delivery addresses, preferences, and the
role-specific extensions for couriers and merchant staff. It does not authenticate — that
is the auth service — but it is the system of record for the profile data auth and other
services denormalise.

Bounded context: the ``identity`` PostgreSQL schema.
"""

SCHEMA = "identity"
CONSUMER_GROUP = "user-service"
