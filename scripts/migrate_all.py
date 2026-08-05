#!/usr/bin/env python
"""Apply every service's migrations, in dependency-free order.

Each service owns its own Alembic chain and its own schema, so the order does not matter
and each runs independently. Used by `make migrate` locally and by the migration job in
deployment.

    python scripts/migrate_all.py                # upgrade every service to head
    python scripts/migrate_all.py --service auth # one service only
    python scripts/migrate_all.py --revision base --downgrade
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Service directories that own an Alembic chain, in a stable order for readable output.
SERVICES: tuple[str, ...] = (
    "services/auth",
    "services/user",
    "services/merchant",
    "services/catalog",
)


def run_alembic(service_path: Path, *args: str) -> int:
    """Run an Alembic command inside a service directory."""
    command = [sys.executable, "-m", "alembic", *args]
    print(f"==> {service_path.name}: {' '.join(args)}")
    result = subprocess.run(command, cwd=service_path, check=False)  # noqa: S603
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--service",
        action="append",
        dest="services",
        help="Service directory to migrate; repeatable. Defaults to all.",
    )
    parser.add_argument(
        "--revision", default="head", help="Target revision (default: head)"
    )
    parser.add_argument(
        "--downgrade",
        action="store_true",
        help="Downgrade to the target revision instead of upgrading",
    )
    arguments = parser.parse_args()

    selected = arguments.services or list(SERVICES)
    direction = "downgrade" if arguments.downgrade else "upgrade"
    # Downgrades run in reverse so a chain of dependent services unwinds cleanly.
    ordered = list(reversed(selected)) if arguments.downgrade else selected

    failures: list[str] = []
    for service in ordered:
        service_path = REPO_ROOT / service
        if not (service_path / "alembic.ini").exists():
            print(f"!! {service}: no alembic.ini, skipping", file=sys.stderr)
            continue
        if run_alembic(service_path, direction, arguments.revision) != 0:
            failures.append(service)

    if failures:
        print(f"\nFAILED: {', '.join(failures)}", file=sys.stderr)
        return 1
    print(f"\nAll migrations {direction}d to {arguments.revision}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
