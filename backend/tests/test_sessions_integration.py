"""Migrated PostgreSQL sessions survive adapter recreation; CLI never reveals secrets."""
from datetime import datetime, timedelta, timezone
import os
import subprocess
import sys

import pytest
from sqlalchemy import text

from app.application.dtos.auth import Role
from app.application.ports.auth import AuthError
from app.application.use_cases.sessions import SessionService
from app.infrastructure.persistence.auth_repository import SqlAuthRepository


def test_persisted_session_restart_rejection_and_logout(migrated_engine):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = SessionService(SqlAuthRepository(migrated_engine), clock=lambda: now[0], lifetime=timedelta(minutes=1))
    actor = service.provision("alice", "correct horse battery", Role.INTAKE)
    token, identity = service.login("alice", "correct horse battery")
    restarted = SessionService(SqlAuthRepository(migrated_engine), clock=lambda: now[0])
    assert restarted.resolve(token) == identity
    for operation in [lambda: restarted.login("alice", "wrong"), lambda: restarted.require(token, "wrong"), lambda: restarted.require(token, identity.csrf_token, roles={Role.AP_MANAGER}), lambda: restarted.provision("alice", "another password here", Role.AP_MANAGER)]:
        with pytest.raises(AuthError):
            operation()
        assert restarted.resolve(token) == identity
    # Security storage invariant: only the digest is persisted, not the bearer token.
    with migrated_engine.connect() as connection:
        assert token not in str(connection.execute(text("SELECT * FROM user_sessions")).all())
        assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM user_sessions")).scalar_one() == 1
    assert actor.actor_id == identity.actor.actor_id
    restarted.logout(token, identity.csrf_token)
    with pytest.raises(AuthError):
        restarted.resolve(token)
    token, identity = restarted.login("alice", "correct horse battery")
    now[0] = identity.expires_at
    with pytest.raises(AuthError):
        restarted.resolve(token)


def test_provision_cli_success_and_duplicate(migrated_engine, database_environment):
    env = dict(database_environment)
    env["TEST_DEMO_PASSWORD"] = "private correct horse battery"
    args = [sys.executable, "-m", "app.interfaces.cli.provision_user", "--username", "manager", "--role", "ap_manager", "--password-env", "TEST_DEMO_PASSWORD"]
    first = subprocess.run(args, env=env, text=True, capture_output=True, timeout=5)
    assert (first.returncode, first.stdout, first.stderr) == (0, "Provisioned user: manager (ap_manager)\n", "")
    duplicate = subprocess.run(args, env=env, text=True, capture_output=True, timeout=5)
    assert (duplicate.returncode, duplicate.stdout, duplicate.stderr) == (1, "", "Provision failed: Account already exists\n")
    service = SessionService(SqlAuthRepository(migrated_engine))
    assert service.login("manager", env["TEST_DEMO_PASSWORD"])[1].actor.role == Role.AP_MANAGER
