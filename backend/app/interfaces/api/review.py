"""Authenticated AP review routes with optimistic revision preconditions."""
from datetime import date
from decimal import Decimal
import re
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from starlette.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from app.application.dtos.review import ReviewError
from app.application.ports.auth import AuthError
from app.interfaces.api.sessions import COOKIE_NAME, CSRF_HEADER


class CommentBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    comment: StrictStr = Field(min_length=1, max_length=4000)


class CorrectionBody(CommentBody):
    invoice: dict


class DecisionBody(CommentBody):
    action: Literal['approve', 'reject', 'request_credit_note']


def build_review_router(service):
    class SafeReviewRoute(APIRoute):
        def get_route_handler(self):
            handler = super().get_route_handler()
            async def safe(request):
                correlation = str(uuid4())
                try:
                    if request.method in ('POST', 'PATCH'):
                        # Authenticate before body parsing; the use case repeats authorization.
                        await run_in_threadpool(service._actor, request.cookies.get(COOKIE_NAME),
                                                request.headers.get(CSRF_HEADER), write=True)
                        original_receive = request._receive
                        size = 0
                        async def receive():
                            nonlocal size
                            message = await original_receive()
                            size += len(message.get('body', b''))
                            if size > 256 * 1024:
                                raise HTTPException(413, 'Review input exceeds 256 KiB')
                            return message
                        request._receive = receive
                    response = await handler(request)
                    response.headers['Cache-Control'] = 'no-store'
                    response.headers['X-Correlation-ID'] = correlation
                    return response
                except AuthError as error:
                    status, code, message = error.status, 'authorization_failed', str(error)
                except ReviewError as error:
                    status, code, message = error.status, error.code, str(error)
                except HTTPException as error:
                    if error.status_code == 413:
                        status, code, message = 413, 'review_body_too_large', 'Review input exceeds 256 KiB'
                    else:
                        status, code, message = 422, 'invalid_request', 'Invalid review request parameters'
                except RequestValidationError:
                    status, code, message = 422, 'invalid_request', 'Invalid review request parameters'
                except Exception:
                    status, code, message = 503, 'review_unavailable', 'Review service is temporarily unavailable'
                return JSONResponse({'error': {'code': code, 'message': message, 'retryable': status == 503,
                    'correlation_id': correlation}}, status_code=status,
                    headers={'Cache-Control': 'no-store', 'X-Correlation-ID': correlation})
            return safe

    router = APIRouter(prefix='/review', tags=['AP review'], route_class=SafeReviewRoute)

    def token(request):
        return request.cookies.get(COOKIE_NAME)

    def revision(request):
        value = request.headers.get('If-Match')
        if value is None:
            raise ReviewError('revision_required', 'If-Match with the current case revision is required', 428)
        if not re.fullmatch(r'"(0|[1-9][0-9]{0,9})"', value) or int(value[1:-1]) > 2_147_483_647:
            raise ReviewError('invalid_revision', 'If-Match must contain a quoted nonnegative case revision', 422)
        return int(value[1:-1])

    def response(value):
        return JSONResponse(value, headers={'ETag': f'"{value["revision"]}"'})

    @router.get('', summary='AP queue with evidence filters, stable order and pagination')
    def queue(request: Request, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
              supplier: str | None = Query(None, max_length=200), date_from: date | None = None,
              date_to: date | None = None, min_amount: Decimal | None = None, max_amount: Decimal | None = None,
              reason: str | None = Query(None, max_length=200), operational_state: Literal[
                  'REVIEW_REQUIRED', 'REEVALUATION_REQUIRED', 'ESCALATED', 'APPROVED', 'REJECTED',
                  'CREDIT_NOTE_REQUESTED'] | None = None,
              min_age_days: int | None = Query(None, ge=0, le=36500),
              sort: Literal['created_at', 'age', 'amount', 'supplier', 'invoice_date'] = 'created_at',
              direction: Literal['asc', 'desc'] = 'asc',
              outcome: Literal['Held for Review', 'Rejected', 'Auto-Pay', 'all'] = 'Held for Review'):
        return service.queue(token(request), offset=offset, limit=limit, supplier=supplier, date_from=date_from,
            date_to=date_to, min_amount=min_amount, max_amount=max_amount, reason=reason,
            operational_state=operational_state, min_age_days=min_age_days, sort=sort, direction=direction, outcome=outcome)

    @router.get('/{invoice_id}', summary='AP document, comparison and current decision; returns ETag')
    def detail(request: Request, invoice_id: UUID):
        return response(service.detail(token(request), invoice_id))

    @router.get('/{invoice_id}/history', summary='Append-only versions, evaluations, decisions and audit')
    def history(request: Request, invoice_id: UUID):
        return response(service.history(token(request), invoice_id))

    @router.patch('/{invoice_id}', summary='Correct extraction with comment, CSRF and If-Match; requires reevaluation')
    def correct(request: Request, invoice_id: UUID, body: CorrectionBody):
        return response(service.correct(token(request), request.headers.get(CSRF_HEADER), invoice_id,
                                        revision(request), body.invoice, body.comment))

    @router.post('/{invoice_id}/reevaluations', summary='Deterministic audited reevaluation; no model call')
    def reevaluate(request: Request, invoice_id: UUID, body: CommentBody):
        return response(service.reevaluate(token(request), request.headers.get(CSRF_HEADER), invoice_id,
                                           revision(request), body.comment))

    @router.post('/{invoice_id}/decisions', summary='AP decision with fresh approval checks, mandatory comment and If-Match')
    def decide(request: Request, invoice_id: UUID, body: DecisionBody):
        return response(service.decide(token(request), request.headers.get(CSRF_HEADER), invoice_id,
                                       revision(request), body.action, body.comment))

    @router.post('/{invoice_id}/escalations', summary='Audit a request for above-limit or non-USD Manager approval')
    def escalate(request: Request, invoice_id: UUID, body: CommentBody):
        return response(service.escalate(token(request), request.headers.get(CSRF_HEADER), invoice_id,
                                         revision(request), body.comment))

    return router
