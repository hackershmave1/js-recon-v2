"""Out-of-band bootstrap CLI.

Tenant + user creation use the privileged admin database connection (it bypasses
RLS), so they must never sit behind an anonymous HTTP route. Run from an operator
shell instead:

    python -m recon.bootstrap create-tenant "Acme Security"
    python -m recon.bootstrap seed-admin --tenant-id <uuid> --username admin --password <pw>

``seed-admin`` is idempotent (re-running refreshes the password), so it doubles as
the dev-login setup and a password reset.
"""

from __future__ import annotations

import argparse
import os
import sys

from recon.auth import service as auth_service
from recon.config import get_settings
from recon.sessions import service

# Environments where the weak dev default (admin/admin) is allowed without --force.
_DEV_ENVS = {"local", "dev", "test"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recon.bootstrap")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-tenant", help="Create a tenant")
    create.add_argument("name")

    seed = sub.add_parser(
        "seed-admin",
        help="Idempotently create/refresh a login user in an existing tenant (dev login).",
    )
    seed.add_argument("--tenant-id", required=True, help="UUID of the tenant to bind the user to")
    seed.add_argument("--tenant-name", default="QA", help="Name used only if the tenant is created")
    seed.add_argument("--username", default="admin")
    seed.add_argument("--password", default="admin")
    seed.add_argument("--role", default="admin")
    seed.add_argument(
        "--force",
        action="store_true",
        help="Allow the weak admin/admin default outside local/dev/test.",
    )

    args = parser.parse_args(argv)

    if args.command == "create-tenant":
        print(service.create_tenant(args.name))
        return 0

    if args.command == "seed-admin":
        # Fail closed: allow the weak dev default ONLY when RECON_ENV is EXPLICITLY set to a
        # dev env. An unset RECON_ENV defaults to "local", so without the explicit check a
        # prod host that forgot to set it would silently seed admin/admin.
        env_explicit = os.environ.get("RECON_ENV") is not None
        dev_env = env_explicit and get_settings().env in _DEV_ENVS
        if args.password == "admin" and not args.force and not dev_env:
            where = f"env={get_settings().env!r}" if env_explicit else "RECON_ENV unset"
            print(
                f"refusing to seed the weak dev password 'admin' ({where}); "
                "set RECON_ENV=local|dev|test, pass --password <strong>, or --force",
                file=sys.stderr,
            )
            return 2
        user_id = auth_service.seed_admin(
            username=args.username,
            password=args.password,
            tenant_id=args.tenant_id,
            tenant_name=args.tenant_name,
            role=args.role,
        )
        print(user_id)
        return 0

    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
