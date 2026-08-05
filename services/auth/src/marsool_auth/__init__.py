"""Marsool auth service.

Owns phone/OTP authentication and the lifecycle of access and refresh tokens. It is the
only service that mints tokens; every other service merely verifies them.

Bounded context: the ``auth`` PostgreSQL schema. Profile data (name, email, addresses)
belongs to the user service; auth keeps only the minimal denormalised fields needed to
populate a login response, kept fresh by consuming ``identity.user_profile_updated``.
"""

SCHEMA = "auth"
CONSUMER_GROUP = "auth-service"
