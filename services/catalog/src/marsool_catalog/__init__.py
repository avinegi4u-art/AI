"""Marsool catalog service.

Owns what a merchant sells: menus, categories, items, and the option groups that make an
item configurable. It is also the authority on what a *valid* basket line is — which
options are required, how many may be chosen, and what the configured line costs.

Putting basket validation here rather than in the order service means the rules live next
to the data that defines them, and the order service cannot accept a line the catalog
would reject.

Bounded context: the ``catalog`` PostgreSQL schema.
"""

SCHEMA = "catalog"
CONSUMER_GROUP = "catalog-service"
