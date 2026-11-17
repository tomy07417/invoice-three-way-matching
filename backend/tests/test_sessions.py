"""Local demo identity contracts through the service and HTTP boundary."""
from datetime import datetime, timedelta, timezone
import os
import subprocess
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.application.dtos.auth import Role
from app.application.ports.auth import AuthError, SessionRecord, UserRecord
from app.application.use_cases.sessions import SessionService
from app.interfaces.api.sessions import build_session_router


class MemoryAccounts:
    def __init__(self):
        self.users = {}
        self.sessions = {}

    def create_user(self, username, password_hash, role):
        if username in self.users:
            raise AuthError("Account already exists")
        user = UserRecord(len(self.users) + 1, username, password_hash, role)
        self.users[username] = user
        return user.actor

    def find_user(self, username):
        return self.users.get(username)

    def create_session(self, record):
        self.sessions[record.token_hash] = record

    def find_session(self, token_hash):
        record = self.sessions.get(token_hash)
        if record is None:
            return None
        user = next(u for u in self.users.values() if u.user_id == record.actor.actor_id)
        return SessionRecord(record.token_hash, user.actor, record.csrf_token, record.expires_at)

    def delete_session(self, token_hash):
        self.sessions.pop(token_hash, None)


@pytest.fixture
def demo():
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    repo = MemoryAccounts()
    service = SessionService(repo, clock=lambda: now[0], lifetime=timedelta(minutes=5))
    service.provision("alice", "correct horse battery", Role.INTAKE)
    app = FastAPI()
    app.include_router(build_session_router(service))
    return service, repo, now, TestClient(app)


def test_login_logout_and_cookie_contract(demo):
    service, _, _, client = demo
    response = client.post("/sessions", json={"username": "alice", "password": "correct horse battery"})
    assert response.status_code == 200
    body = response.json()
    assert body["actor"] == {"actor_id": 1, "username": "alice", "role": "intake"}
    assert set(body) == {"actor", "csrf_token", "expires_at"}
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Secure" not in cookie
    token = client.cookies.get("demo_session")
    assert token not in repr(demo[1].sessions)
    assert client.get("/sessions/current").json() == body
    assert service.resolve(token).actor.can_view(1)
    assert not service.resolve(token).actor.can_view(2)
    response = client.delete("/sessions/current", headers={"X-CSRF-Token": body["csrf_token"]})
    assert response.status_code == 204 and response.content == b""
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.get("/sessions/current").status_code == 401
    with pytest.raises(AuthError, match="Authentication required"):
        service.resolve(token)


def test_generic_login_failure_and_role_tamper_do_not_replace_session(demo):
    _, _, _, client = demo
    client.post("/sessions", json={"username": "alice", "password": "correct horse battery"})
    before = client.get("/sessions/current").json()
    for username, password in [("alice", "wrong"), ("missing", "wrong")]:
        response = client.post("/sessions", json={"username": username, "password": password})
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid username or password"}
        assert "set-cookie" not in response.headers
        assert client.get("/sessions/current").json() == before
    response = client.post("/sessions", json={"username": "alice", "password": "correct horse battery", "role": "ap_manager"})
    assert response.status_code == 422
    assert client.get("/sessions/current").json() == before


def test_csrf_and_role_owner_guards_preserve_session(demo):
    service, _, _, client = demo
    body = client.post("/sessions", json={"username": "alice", "password": "correct horse battery"}).json()
    token = client.cookies.get("demo_session")
    for headers in ({}, {"X-CSRF-Token": "wrong"}):
        response = client.delete("/sessions/current", headers=headers)
        assert response.status_code == 403
        assert response.json() == {"detail": "CSRF validation failed"}
        assert client.get("/sessions/current").json() == body
    for kwargs in ({"roles": {Role.AP_MANAGER}}, {"owner_id": 2}):
        with pytest.raises(AuthError, match="Forbidden"):
            service.require(token, body["csrf_token"], **kwargs)
        assert service.resolve(token).actor.role == Role.INTAKE
    assert service.require(token, body["csrf_token"], owner_id=1).actor_id == 1
    for bad_token in (None, "", "unknown", "x" * 129):
        with pytest.raises(AuthError, match="Authentication required"):
            service.require(bad_token, body["csrf_token"])
    for bad_csrf in (None, "", "ñ", "x" * 129):
        with pytest.raises(AuthError, match="CSRF validation failed"):
            service.require(token, bad_csrf)
    assert client.get("/sessions/current").json() == body


def test_expiry_boundary_and_secure_cookie(demo):
    service, _, now, client = demo
    body = client.post("/sessions", json={"username": "alice", "password": "correct horse battery"}).json()
    token = client.cookies.get("demo_session")
    now[0] += timedelta(minutes=5)
    assert client.get("/sessions/current").status_code == 401
    assert client.delete("/sessions/current", headers={"X-CSRF-Token": body["csrf_token"]}).status_code == 401
    with pytest.raises(AuthError):
        service.resolve(token)
    app = FastAPI()
    app.include_router(build_session_router(service, secure_cookie=True))
    response = TestClient(app).post("/sessions", json={"username": "alice", "password": "correct horse battery"})
    assert "Secure" in response.headers["set-cookie"]


def test_provision_validation_duplicate_and_password_salts(demo):
    service, repo, _, _ = demo
    with pytest.raises(AuthError, match="Account already exists"):
        service.provision("alice", "different strong password", Role.AP_MANAGER)
    for username, password, role in [("", "strong password here", Role.INTAKE), ("bob", "short", Role.INTAKE), ("bob", "strong password here", "ap_manager")]:
        with pytest.raises(AuthError):
            service.provision(username, password, role)
    service.provision("bob", "correct horse battery", Role.AP_ANALYST)
    assert repo.users["alice"].password_hash != repo.users["bob"].password_hash
    assert "correct horse battery" not in repr(repo.users)
    assert service.login("alice", "correct horse battery")[1].actor.role == Role.INTAKE
    token, identity = service.login("bob", "correct horse battery")
    assert service.require(token, identity.csrf_token, owner_id=999).role == Role.AP_ANALYST
    second_token, second_identity = service.login("bob", "correct horse battery")
    assert second_token != token and second_identity.csrf_token != identity.csrf_token
    assert service.resolve(token) == identity
    service.provision("manager", "correct horse battery", Role.AP_MANAGER)
    manager_token, manager = service.login("manager", "correct horse battery")
    assert service.require(manager_token, manager.csrf_token, roles={Role.AP_MANAGER}, owner_id=999).role == Role.AP_MANAGER


def run_cli(*args, env=None):
    return subprocess.run([sys.executable, "-m", "app.interfaces.cli.provision_user", *args], env=env, capture_output=True, text=True, timeout=5)


def test_provision_cli_help_and_errors():
    result = run_cli("--help")
    assert result.returncode == 0 and result.stderr == ""
    assert "--username" in result.stdout and "--role" in result.stdout and "--password-env" in result.stdout
    assert "getpass" in result.stdout and "no .env" in result.stdout
    result = run_cli("--username", "alice", "--role", "admin")
    assert result.returncode == 2 and result.stdout == ""
    assert "invalid choice" in result.stderr
    env = os.environ.copy()
    env.pop("MISSING_DEMO_PASSWORD", None)
    result = run_cli("--username", "alice", "--role", "intake", "--password-env", "MISSING_DEMO_PASSWORD", env=env)
    assert (result.returncode, result.stdout, result.stderr) == (1, "", "Provision failed: password environment variable is empty\n")


def test_cli_getpass_and_environment_keep_password_private(monkeypatch, capsys):
    from app.interfaces.cli import provision_user

    repo = MemoryAccounts()
    service = SessionService(repo)

    class BorrowedEngine:
        disposed = False

        def dispose(self):
            self.disposed = True

    engine = BorrowedEngine()
    monkeypatch.setattr(provision_user, "create_database_engine", lambda: engine)
    monkeypatch.setattr(provision_user, "SqlAuthRepository", lambda _: repo)
    monkeypatch.setattr(provision_user, "getpass", lambda _: "private correct password")
    monkeypatch.setattr(sys, "argv", ["provision_user", "--username", "local", "--role", "intake"])
    assert provision_user.main() == 0
    assert capsys.readouterr() == ("Provisioned user: local (intake)\n", "")
    assert engine.disposed
    assert service.login("local", "private correct password")[1].actor.role == Role.INTAKE
    monkeypatch.setenv("UNIT_DEMO_PASSWORD", "another private password")
    monkeypatch.setattr(sys, "argv", ["provision_user", "--username", "other", "--role", "ap_manager", "--password-env", "UNIT_DEMO_PASSWORD"])
    assert provision_user.main() == 0
    assert capsys.readouterr() == ("Provisioned user: other (ap_manager)\n", "")
    assert service.login("other", "another private password")[1].actor.role == Role.AP_MANAGER
