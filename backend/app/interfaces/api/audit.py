"""Read-only Manager invoice audit, with private responses and normalized errors."""
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app.application.dtos.audit import AuditError, AuditFilters, AuditKind
from app.application.ports.auth import AuthError
from app.application.use_cases.audit import AuditService
from app.interfaces.api.sessions import COOKIE_NAME


def build_audit_router(service: AuditService) -> APIRouter:
    class SafeAuditRoute(APIRoute):
        def get_route_handler(self):
            handler = super().get_route_handler()

            async def safe(request):
                correlation = str(uuid4())
                headers = {'Cache-Control': 'private, no-store', 'X-Correlation-ID': correlation}
                try:
                    # Deny before validation as well as independently in the use case.
                    await run_in_threadpool(service.authorize, request.cookies.get(COOKIE_NAME))
                    response = await handler(request)
                    response.headers.update(headers)
                    return response
                except AuthError as error:
                    status, code, message = error.status, 'authorization_failed', str(error)
                except AuditError as error:
                    status, code, message = error.status, error.code, str(error)
                except RequestValidationError:
                    status, code, message = 422, 'invalid_request', 'Invalid audit request parameters'
                except Exception:
                    status, code, message = 503, 'audit_unavailable', 'Audit service is temporarily unavailable'
                return JSONResponse({'error': {'code': code, 'message': message, 'retryable': status == 503,
                    'correlation_id': correlation}}, status_code=status, headers=headers)
            return safe

    router = APIRouter(prefix='/audit', tags=['Manager audit'], route_class=SafeAuditRoute)

    @router.get('/{invoice_id}', summary='Manager-only immutable invoice audit; unknown times sort last',
                description='Inclusive timezone-aware time bounds exclude unknown times. Filter by exact actor ID or '
                            'event kind; stable ascending time order, then revision/source ID. Reads never write history.')
    def history(request: Request, invoice_id: UUID,
                offset: int = Query(0, ge=0, le=2_147_483_647), limit: int = Query(50, ge=1, le=100),
                kind: AuditKind | None = None, actor_id: str | None = Query(None, min_length=1, max_length=128),
                time_from: datetime | None = None, time_to: datetime | None = None):
        return service.history(request.cookies.get(COOKIE_NAME), invoice_id,
                               AuditFilters(offset, limit, kind, actor_id, time_from, time_to))

    return router
