"""Out-of-band bootstrap CLI.

Tenant creation uses the privileged admin database connection (it bypasses RLS),
so it must never sit behind an anonymous HTTP route. Run it from an operator
shell instead:

    python -m recon.bootstrap create-tenant "Acme Security"
"""

from __future__ import annotations

import argparse
import sys

from recon.sessions import service


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recon.bootstrap")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-tenant", help="Create a tenant")
    create.add_argument("name")

    args = parser.parse_args(argv)
    if args.command == "create-tenant":
        tenant_id = service.create_tenant(args.name)
        print(tenant_id)
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
