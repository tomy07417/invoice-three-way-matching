"""Opt-in session routes; composition root owns wiring and HTTPS configuration."""
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from app.application.ports.auth import AuthError
from app.application.use_cases.sessions import SessionService

COOKIE_NAME = "demo_session"
CSRF_HEADER = "X-CSRF-Token"


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(max_length=64)
    password: str = Field(max_length=1024, repr=False)


def build_session_router(service: SessionService, secure_cookie: bool = False) -> APIRouter:
    """Synchronous endpoints offload short DB/password work from the event loop.

    No public signup or role claims. Login sets a fresh opaque cookie; introspection
    supplies the synchronizer token. All authenticated mutations must require it.
    """
    router = APIRouter(tags=["sessions"])

    def fail(error: AuthError):
        raise HTTPException(error.status, str(error), headers={"Cache-Control": "no-store"}) from None

    @router.post("/sessions", summary="Log in with a provisioned local demo account")
    def login(payload: LoginRequest):
        try:
            token, identity = service.login(payload.username, payload.password)
        except AuthError as error:
            fail(error)
        response = JSONResponse(jsonable_encoder(asdict(identity)), headers={"Cache-Control": "no-store"})
        response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", secure=secure_cookie,
                            path="/", max_age=int(service.lifetime.total_seconds()))
        return response

    @router.get("/sessions/current", summary="Resolve server-side identity and CSRF synchronizer token")
    def current(request: Request):
        try:
            identity = service.resolve(request.cookies.get(COOKIE_NAME))
        except AuthError as error:
            fail(error)
        return JSONResponse(jsonable_encoder(asdict(identity)), headers={"Cache-Control": "no-store"})

    @router.delete("/sessions/current", status_code=204, summary="Log out; requires X-CSRF-Token header")
    def logout(request: Request):
        try:
            service.logout(request.cookies.get(COOKIE_NAME), request.headers.get(CSRF_HEADER))
        except AuthError as error:
            fail(error)
        response = Response(status_code=204, headers={"Cache-Control": "no-store"})
        response.delete_cookie(COOKIE_NAME, httponly=True, samesite="lax", secure=secure_cookie, path="/")
        return response

    return router
