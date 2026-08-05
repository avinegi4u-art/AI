#!/usr/bin/env python
"""Export every service's OpenAPI document to ``docs/openapi/``.

Generated from the running app definitions rather than maintained by hand, so the
specification cannot drift from the code. Run it in CI to detect an unintended contract
change: a diff in these files is a diff in the public API.

    python scripts/export_openapi.py
    python scripts/export_openapi.py --check   # fail if the checked-in files are stale
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "docs" / "openapi"

# Settings must be in place before service modules import them, because each service
# reads and caches its settings at import time.
os.environ.setdefault("MARSOOL_ENVIRONMENT", "local")
os.environ.setdefault("MARSOOL_JWT_SECRET", "openapi-export-placeholder-secret")


def _app_builders() -> dict[str, Callable[[], Any]]:
    """Return each service's app factory, imported lazily.

    Imported inside the function so that a failure in one service still lets the others
    export, and so the module-level environment defaults above are applied first.
    """
    from marsool_auth.main import build_service as build_auth  # noqa: PLC0415
    from marsool_catalog.main import build_service as build_catalog  # noqa: PLC0415
    from marsool_gateway.main import build_service as build_gateway  # noqa: PLC0415
    from marsool_merchant.main import build_service as build_merchant  # noqa: PLC0415
    from marsool_user.main import build_service as build_user  # noqa: PLC0415

    return {
        "gateway": build_gateway,
        "auth": build_auth,
        "user": build_user,
        "merchant": build_merchant,
        "catalog": build_catalog,
    }


def export(*, check_only: bool) -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []

    for name, build in _app_builders().items():
        document = build().app.openapi()
        # Sorted keys and a trailing newline keep the diff meaningful across runs.
        rendered = json.dumps(document, indent=2, sort_keys=True) + "\n"
        destination = OUTPUT_DIR / f"{name}.json"

        if check_only:
            existing = destination.read_text() if destination.exists() else ""
            if existing != rendered:
                stale.append(name)
            continue

        destination.write_text(rendered)
        operation_count = sum(
            1
            for path_item in document.get("paths", {}).values()
            for method in path_item
            if method in {"get", "post", "put", "patch", "delete"}
        )
        print(f"wrote {destination.relative_to(REPO_ROOT)} ({operation_count} operations)")

    if stale:
        print(
            f"\nOpenAPI documents are stale for: {', '.join(stale)}\n"
            "Run `make openapi` and commit the result.",
            file=sys.stderr,
        )
        return 1
    if check_only:
        print("OpenAPI documents are up to date.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the checked-in documents match the code instead of rewriting them",
    )
    arguments = parser.parse_args()
    return export(check_only=arguments.check)


if __name__ == "__main__":
    raise SystemExit(main())
