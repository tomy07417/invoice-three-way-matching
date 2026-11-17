"""Private invoice endpoints. Never accept client-supplied actors or file paths."""
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException

from app.application.dtos.processing import ProcessingState
from app.application.ports.auth import AuthError
from app.application.ports.invoice_documents import DocumentValidationError
from app.application.ports.processing import AdmissionRejected
from app.infrastructure.processing.service import InvoiceError
from app.interfaces.api.sessions import COOKIE_NAME, CSRF_HEADER


class SafeInvoiceRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe(request):
            correlation_id = str(uuid4())
            try:
                if request.method == 'POST' and self.path == '/invoices':
                    # FastAPI parses multipart before calling the endpoint. Gate
                    # it here, then cap the entire multipart stream (including a
                    # small framing allowance), not merely the parsed file.
                    await self.invoice_service.authorize_upload(
                        request.cookies.get(COOKIE_NAME), request.headers.get(CSRF_HEADER))
                    receive = request._receive
                    received = 0
                    maximum = self.invoice_service.validator.max_bytes + 64 * 1024

                    async def bounded_receive():
                        nonlocal received
                        message = await receive()
                        received += len(message.get('body', b''))
                        if received > maximum:
                            raise DocumentValidationError('invalid_size', 'Document size is not allowed')
                        return message
                    request._receive = bounded_receive
                response = await handler(request)
                response.headers['Cache-Control'] = 'no-store'
                response.headers['X-Correlation-ID'] = correlation_id
                return response
            except AuthError as error:
                status, code, message, retryable = error.status, 'authorization_failed', str(error), False
            except InvoiceError as error:
                status, code, message, retryable = error.status, error.code, str(error), error.retryable
            except DocumentValidationError as error:
                status, code, message, retryable = 422, error.code, str(error), False
            except (RequestValidationError, HTTPException):
                status, code, message, retryable = 422, 'invalid_request', 'Invalid request parameters', False
            except AdmissionRejected:
                status, code, message, retryable = 429, 'queue_full', 'Processing queue is full', True
            except Exception:
                status, code, message, retryable = 503, 'service_unavailable', 'Invoice service unavailable', True
            return JSONResponse(dict(error=dict(code=code, message=message, retryable=retryable,
                                                correlation_id=correlation_id)), status_code=status,
                                headers={'Cache-Control': 'no-store', 'X-Correlation-ID': correlation_id})
        return safe


def build_invoice_router(service):
    class InvoiceRoute(SafeInvoiceRoute):
        invoice_service = service

    router = APIRouter(prefix='/invoices', tags=['invoices'], route_class=InvoiceRoute)

    def token(request):
        return request.cookies.get(COOKIE_NAME)

    @router.post('', status_code=202, summary='Upload a private invoice; requires CSRF and Idempotency-Key')
    async def upload(request: Request, file: UploadFile = File(...)):
        try:
            await service.authorize_upload(token(request), request.headers.get(CSRF_HEADER))
            content = bytearray()
            while chunk := await file.read(64 * 1024):
                content.extend(chunk)
                if len(content) > service.validator.max_bytes:
                    raise DocumentValidationError('invalid_size', 'Document size is not allowed')
            return await service.upload(token(request), request.headers.get(CSRF_HEADER),
                                        request.headers.get('Idempotency-Key'), file.filename or '',
                                        file.content_type or '', bytes(content))
        finally:
            await file.close()

    @router.get('', summary='List visible invoices with optional technical-state filter and pagination')
    async def listing(request: Request, status: ProcessingState | None = None,
                      offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        return await service.list(token(request), status=status, offset=offset, limit=limit)

    @router.get('/{invoice_id}')
    async def detail(request: Request, invoice_id: UUID):
        return await service.detail(token(request), invoice_id)

    @router.get('/{invoice_id}/processing')
    async def processing(request: Request, invoice_id: UUID):
        return await service.processing(token(request), invoice_id)

    @router.post('/{invoice_id}/retry', status_code=202,
                 summary='Return an already-scheduled retry; never reset finite attempt budget; requires CSRF')
    async def retry(request: Request, invoice_id: UUID):
        return await service.retry(token(request), request.headers.get(CSRF_HEADER), invoice_id)

    @router.get('/{invoice_id}/document', summary='Read an authorized private original by opaque invoice ID')
    async def document(request: Request, invoice_id: UUID):
        content, media_type = await service.document(token(request), invoice_id)
        async def chunks():
            for offset in range(0, len(content), 64 * 1024):
                yield content[offset:offset + 64 * 1024]
        return StreamingResponse(chunks(), media_type=media_type,
                                 headers={'Content-Disposition': 'inline', 'X-Content-Type-Options': 'nosniff'})

    return router
