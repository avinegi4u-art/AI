"""Marsool API gateway.

The single entry point for every client. It authenticates the caller, enforces rate
limits, and forwards the request to the owning service.

What the gateway is *not*: it holds no business logic and no database. Every downstream
service verifies the JWT again for itself, so a bypassed or compromised gateway cannot
mint trust — the gateway is a convenience and a policy layer, never the only guard.
"""

SERVICE_NAME = "marsool-gateway"
