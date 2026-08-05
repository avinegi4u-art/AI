#!/usr/bin/env python
"""End-to-end smoke test through the API gateway.

Walks the customer journey the MVP is built around, against running services and a real
database: browse merchants, request an OTP, log in, read a profile, add an address, fetch a
menu, and validate a basket. Unit and integration suites cover each service in isolation;
this proves the pieces work together through the gateway.

    python scripts/smoke_test.py --base-url http://localhost:8000
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

import httpx

# Sky Tower, Al Reem Island — matches the seeded demo customer's default address.
ORIGIN = {"lat": "24.494640", "lng": "54.399460"}
DEMO_PHONE = "+971501234567"

STEP_OK = "PASS"
STEP_FAILED = "FAIL"


class SmokeTestFailure(AssertionError):
    """Raised when a step does not behave as expected."""


class Reporter:
    """Prints a readable pass/fail line per step and tracks failures."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self._step = 0

    def ok(self, description: str, detail: str = "") -> None:
        self._step += 1
        suffix = f" — {detail}" if detail else ""
        print(f"  [{STEP_OK}] {self._step:2d}. {description}{suffix}")

    def fail(self, description: str, detail: str) -> None:
        self._step += 1
        self.failures.append(description)
        print(f"  [{STEP_FAILED}] {self._step:2d}. {description} — {detail}")

    def summary(self) -> int:
        print()
        if self.failures:
            print(f"{len(self.failures)} step(s) failed: {', '.join(self.failures)}")
            return 1
        print(f"All {self._step} steps passed.")
        return 0


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeTestFailure(message)


async def run(base_url: str) -> int:
    reporter = Reporter()
    print(f"\nMarsool smoke test against {base_url}\n")

    async with httpx.AsyncClient(base_url=base_url, timeout=20.0) as client:
        # 1. Health -------------------------------------------------------------------
        try:
            response = await client.get("/health/ready")
            expect(response.status_code == 200, f"gateway not ready: {response.text}")
            reporter.ok("Gateway is ready", response.json()["status"])
        except Exception as exc:  # noqa: BLE001 - report and stop; nothing else can work
            reporter.fail("Gateway is ready", str(exc))
            return reporter.summary()

        # 2. Anonymous browsing -------------------------------------------------------
        merchants: list[dict[str, Any]] = []
        try:
            response = await client.get("/merchants", params={**ORIGIN, "radius_m": 25_000})
            expect(response.status_code == 200, response.text)
            merchants = response.json()["items"]
            expect(len(merchants) > 0, "no merchants returned; run `make seed`")
            distances = [item["distance_m"] for item in merchants]
            expect(distances == sorted(distances), "results are not ordered by distance")
            reporter.ok(
                "Anonymous merchant search works",
                f"{len(merchants)} merchants, nearest {distances[0]:.0f} m",
            )
        except Exception as exc:  # noqa: BLE001
            reporter.fail("Anonymous merchant search works", str(exc))
            return reporter.summary()

        # 3. Cuisine filter -----------------------------------------------------------
        try:
            response = await client.get(
                "/merchants", params={**ORIGIN, "radius_m": 25_000, "cuisine": "japanese"}
            )
            expect(response.status_code == 200, response.text)
            filtered = response.json()["items"]
            expect(
                all("japanese" in item["cuisines"] for item in filtered),
                "cuisine filter returned a non-matching merchant",
            )
            reporter.ok("Cuisine filter works", f"{len(filtered)} japanese merchants")
        except Exception as exc:  # noqa: BLE001
            reporter.fail("Cuisine filter works", str(exc))

        # 4. Protected route rejects anonymous access ---------------------------------
        try:
            response = await client.get("/users/me")
            expect(response.status_code == 401, f"expected 401, got {response.status_code}")
            expect(
                response.json()["error"]["code"] == "missing_credentials",
                f"unexpected error code: {response.text}",
            )
            reporter.ok("Protected route rejects anonymous access", "401 missing_credentials")
        except Exception as exc:  # noqa: BLE001
            reporter.fail("Protected route rejects anonymous access", str(exc))

        # 5. OTP request --------------------------------------------------------------
        otp_code = ""
        try:
            response = await client.post("/auth/otp", json={"phone": DEMO_PHONE})
            expect(response.status_code == 201, response.text)
            body = response.json()
            expect(body["phone"] == DEMO_PHONE, "phone was not normalised")
            otp_code = body.get("debug_code") or ""
            expect(bool(otp_code), "debug_code absent; is the service running in local mode?")
            reporter.ok("OTP challenge issued", f"challenge {body['challenge_id']}")
        except Exception as exc:  # noqa: BLE001
            reporter.fail("OTP challenge issued", str(exc))
            return reporter.summary()

        # 6. Login --------------------------------------------------------------------
        tokens: dict[str, Any] = {}
        try:
            response = await client.post(
                "/auth/login", json={"phone": DEMO_PHONE, "otp": otp_code}
            )
            expect(response.status_code == 200, response.text)
            tokens = response.json()
            expect(tokens["token_type"] == "Bearer", "unexpected token type")  # noqa: S105
            expect(tokens["user"]["phone"] == DEMO_PHONE, "wrong user returned")
            reporter.ok("Login issued tokens", f"user {tokens['user']['id']}")
        except Exception as exc:  # noqa: BLE001
            reporter.fail("Login issued tokens", str(exc))
            return reporter.summary()

        auth = {"Authorization": f"Bearer {tokens['access_token']}"}

        # 7. Wrong OTP is rejected ----------------------------------------------------
        try:
            await client.post("/auth/otp", json={"phone": DEMO_PHONE})
            response = await client.post(
                "/auth/login", json={"phone": DEMO_PHONE, "otp": "000000"}
            )
            expect(response.status_code == 401, f"expected 401, got {response.status_code}")
            reporter.ok("Wrong OTP is rejected", response.json()["error"]["code"])
        except Exception as exc:  # noqa: BLE001
            reporter.fail("Wrong OTP is rejected", str(exc))

        # 8. Profile ------------------------------------------------------------------
        try:
            response = await client.get("/users/me", headers=auth)
            expect(response.status_code == 200, response.text)
            profile = response.json()
            expect(profile["id"] == tokens["user"]["id"], "profile id does not match the token")
            reporter.ok("Profile read", f"{profile.get('name') or 'unnamed'} ({profile['role']})")
        except Exception as exc:  # noqa: BLE001
            reporter.fail("Profile read", str(exc))

        # 9. Address list -------------------------------------------------------------
        try:
            response = await client.get("/users/me/addresses", headers=auth)
            expect(response.status_code == 200, response.text)
            addresses = response.json()
            reporter.ok("Address list read", f"{len(addresses)} addresses")
        except Exception as exc:  # noqa: BLE001
            reporter.fail("Address list read", str(exc))

        # 10. Address creation --------------------------------------------------------
        try:
            response = await client.post(
                "/users/me/addresses",
                headers=auth,
                json={
                    "label": "OTHER",
                    "line1": "Marina Mall, Breakwater",
                    "community": "Al Marina",
                    "city": "Abu Dhabi",
                    "latitude": "24.475900",
                    "longitude": "54.322000",
                },
            )
            expect(response.status_code == 201, response.text)
            reporter.ok("Address created", response.json()["id"])
        except Exception as exc:  # noqa: BLE001
            reporter.fail("Address created", str(exc))

        # 11. Serviceability ----------------------------------------------------------
        merchant_id = merchants[0]["id"]
        try:
            response = await client.get(
                f"/merchants/{merchant_id}/serviceability", params=ORIGIN
            )
            expect(response.status_code == 200, response.text)
            body = response.json()
            reporter.ok(
                "Serviceability checked",
                f"deliverable={body['deliverable']} reason={body['reason']}",
            )
        except Exception as exc:  # noqa: BLE001
            reporter.fail("Serviceability checked", str(exc))

        # 12. Menu (routed to catalog under a /merchants path) ------------------------
        menu: dict[str, Any] = {}
        try:
            response = await client.get(f"/merchants/{merchant_id}/menu")
            expect(response.status_code == 200, response.text)
            menu = response.json()
            expect(len(menu["categories"]) > 0, "menu has no categories")
            reporter.ok(
                "Menu fetched via the gateway",
                f"{menu['item_count']} items in {len(menu['categories'])} categories",
            )
        except Exception as exc:  # noqa: BLE001
            reporter.fail("Menu fetched via the gateway", str(exc))
            return reporter.summary()

        # 13. Menu is served from cache on the second read ----------------------------
        try:
            response = await client.get(f"/merchants/{merchant_id}/menu")
            expect(response.status_code == 200, response.text)
            expect(response.json() == menu, "cached menu differs from the first read")
            reporter.ok("Cached menu read matches the first read")
        except Exception as exc:  # noqa: BLE001
            reporter.fail("Cached menu read matches the first read", str(exc))

        # 14. Valid basket ------------------------------------------------------------
        item = menu["categories"][0]["items"][0]
        required_group = next(
            (group for group in item["option_groups"] if group["is_required"]), None
        )
        selections = (
            [
                {
                    "group_id": required_group["id"],
                    "option_ids": [required_group["options"][0]["id"]],
                }
            ]
            if required_group
            else []
        )
        try:
            response = await client.post(
                f"/merchants/{merchant_id}/basket/validate",
                headers=auth,
                json={"lines": [{"item_id": item["id"], "quantity": 2, "selections": selections}]},
            )
            expect(response.status_code == 200, response.text)
            body = response.json()
            expect(body["valid"] is True, f"basket was rejected: {body['issues']}")
            reporter.ok(
                "Valid basket priced",
                f"{body['items_amount']} {body['currency']} for 2 x {item['name']}",
            )
        except Exception as exc:  # noqa: BLE001
            reporter.fail("Valid basket priced", str(exc))

        # 15. Invalid basket ----------------------------------------------------------
        if required_group:
            try:
                response = await client.post(
                    f"/merchants/{merchant_id}/basket/validate",
                    headers=auth,
                    json={"lines": [{"item_id": item["id"], "quantity": 1}]},
                )
                expect(response.status_code == 200, response.text)
                body = response.json()
                expect(body["valid"] is False, "basket missing a required group was accepted")
                codes = [issue["code"] for issue in body["issues"]]
                expect("required_group_missing" in codes, f"unexpected issues: {codes}")
                reporter.ok("Basket missing a required option is rejected", codes[0])
            except Exception as exc:  # noqa: BLE001
                reporter.fail("Basket missing a required option is rejected", str(exc))

        # 16. Basket validation requires authentication -------------------------------
        try:
            response = await client.post(
                f"/merchants/{merchant_id}/basket/validate",
                json={"lines": [{"item_id": item["id"], "quantity": 1}]},
            )
            expect(response.status_code == 401, f"expected 401, got {response.status_code}")
            reporter.ok("Basket validation requires authentication", "401")
        except Exception as exc:  # noqa: BLE001
            reporter.fail("Basket validation requires authentication", str(exc))

        # 17. Token refresh -----------------------------------------------------------
        try:
            response = await client.post(
                "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
            )
            expect(response.status_code == 200, response.text)
            rotated = response.json()
            expect(
                rotated["refresh_token"] != tokens["refresh_token"],
                "refresh token was not rotated",
            )
            reporter.ok("Refresh token rotated")

            replay = await client.post(
                "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
            )
            expect(replay.status_code == 401, f"replay accepted: {replay.status_code}")
            expect(
                replay.json()["error"]["code"] == "token_reused",
                f"unexpected error: {replay.text}",
            )
            reporter.ok("Replayed refresh token is rejected", "401 token_reused")
        except Exception as exc:  # noqa: BLE001
            reporter.fail("Refresh token rotation", str(exc))

        # 18. Correlation id ----------------------------------------------------------
        try:
            response = await client.get(
                "/merchants",
                params=ORIGIN,
                headers={"X-Correlation-Id": "smoke_test_correlation"},
            )
            expect(
                response.headers.get("X-Correlation-Id") == "smoke_test_correlation",
                f"correlation id not echoed: {dict(response.headers)}",
            )
            reporter.ok("Correlation id is echoed back")
        except Exception as exc:  # noqa: BLE001
            reporter.fail("Correlation id is echoed back", str(exc))

    return reporter.summary()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000", help="Gateway base URL")
    arguments = parser.parse_args()
    try:
        return asyncio.run(run(arguments.base_url.rstrip("/")))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
