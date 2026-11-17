"""Operator-only local demo provisioning; never loads files or prints secrets."""
import argparse
from getpass import getpass
import os
import sys

from sqlalchemy.exc import SQLAlchemyError

from app.application.dtos.auth import Role
from app.application.ports.auth import AuthError
from app.application.use_cases.sessions import SessionService
from app.infrastructure.persistence.auth_repository import SqlAuthRepository
from app.infrastructure.persistence.connection import create_database_engine


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Provision a local demo account (no public signup).",
        epilog="Password: getpass prompt by default, or --password-env names an existing environment variable. "
               "Minimum 12, maximum 1024 characters. Username: 1-64 letters, digits, dots, underscores or hyphens. "
               "Existing accounts are not modified. Uses DATABASE_URL or POSTGRES_*; no .env loading. "
               "Success prints username and role only; failures exit 1 with a safe stderr message.",
    )
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", required=True, choices=[role.value for role in Role])
    parser.add_argument("--password-env", help="environment variable containing the password (never printed)")
    args = parser.parse_args()
    engine = None
    try:
        if args.password_env:
            password = os.environ.get(args.password_env)
            if not password:
                raise AuthError("password environment variable is empty")
        else:
            password = getpass("Password: ")
        try:
            engine = create_database_engine()
        except (ValueError, TypeError, OverflowError, SQLAlchemyError):
            raise AuthError("invalid or missing database configuration") from None
        actor = SessionService(SqlAuthRepository(engine)).provision(args.username, password, Role(args.role))
    except AuthError as error:
        print(f"Provision failed: {error}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("Provision failed: password input cancelled", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    print(f"Provisioned user: {actor.username} ({actor.role.value})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
