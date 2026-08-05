"""Marsool merchant service.

Owns merchants: who they are, where they are, when they are open, and where they deliver.
It answers the first question a customer asks — "what can I order from right now?" — which
makes its geospatial search the hottest read path in the platform.

Bounded context: the ``merchant`` PostgreSQL schema. Menus and items belong to the catalog
service; orders and pricing belong to theirs.
"""

SCHEMA = "merchant"
CONSUMER_GROUP = "merchant-service"
