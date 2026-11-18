"""DI-compatible Manager parameter routes with exact inputs and safe errors."""
import json
import re
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app.application.dtos.review import ReviewError
from app.application.ports.auth import AuthError
from app.interfaces.api.sessions import COOKIE_NAME, CSRF_HEADER


MAX_BODY_BYTES = 16 * 1024


def build_parameters_router(service):
    class SafeParameterRoute(APIRoute):
        def get_route_handler(self):
            handler = super().get_route_handler()
            async def safe(request):
                correlation = str(uuid4())
                try:
                    # Resolve persisted authority before parsing any input, for
                    # reads too. Use cases repeat it for non-HTTP callers.
                    await run_in_threadpool(service.authorize, request.cookies.get(COOKIE_NAME),
                        request.headers.get(CSRF_HEADER), write=request.method == 'PATCH')
                    response = await handler(request)
                    response.headers['Cache-Control'] = 'no-store'
                    response.headers['X-Correlation-ID'] = correlation
                    return response
                except AuthError as error:
                    status, code, message = error.status, 'authorization_failed', str(error)
                except ReviewError as error:
                    status, code, message = error.status, error.code, str(error)
                except RequestValidationError:
                    status, code, message = 422, 'invalid_parameters', 'Invalid parameter request'
                except Exception:
                    status, code, message = 503, 'parameters_unavailable', 'Parameter service is temporarily unavailable'
                return JSONResponse({'error': {'code': code, 'message': message,
                    'retryable': status == 503, 'correlation_id': correlation}}, status_code=status,
                    headers={'Cache-Control': 'no-store', 'X-Correlation-ID': correlation})
            return safe

    router = APIRouter(prefix='/manager/parameters', tags=['Manager parameters'], route_class=SafeParameterRoute)

    def response(value):
        return JSONResponse(value, headers={'ETag': f'"{value["version_id"]}"'})

    @router.get('', summary='Manager-only current exact fractional tolerance and USD limit; returns ETag')
    def current(request: Request):
        return response(service.current(request.cookies.get(COOKIE_NAME)))

    @router.get('/history', summary='Manager-only parameter versions with known actor, before/after and effective time')
    def history(request: Request, offset: int = Query(0, ge=0, le=2_147_483_647),
                limit: int = Query(20, ge=1, le=100)):
        return service.history(request.cookies.get(COOKIE_NAME), offset=offset, limit=limit)

    @router.patch('', summary='Change future parameters with CSRF and quoted If-Match; unchanged values create no event',
                  description='JSON object: price_tolerance and/or analyst_approval_limit as finite nonnegative exact decimal strings; optional comment (max 4000 characters). No business maximum or fixed scale. Tolerance is a fraction; limit is USD. Body limited to 16 KiB. Server selects effective time; no backdating. Returns current version and ETag, including on a no-op.')
    async def change(request: Request):
        match = request.headers.get('If-Match')
        if match is None:
            raise ReviewError('version_required', 'If-Match with the current configuration version is required', 428)
        if not re.fullmatch(r'"[1-9][0-9]{0,9}"', match) or int(match[1:-1]) > 2_147_483_647:
            raise ReviewError('invalid_version', 'If-Match must contain a quoted configuration version ID', 422)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_BODY_BYTES:
                raise ReviewError('parameters_body_too_large', 'Parameter input exceeds 16 KiB', 413)
            data.extend(chunk)
        try:
            def unique_object(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError('Duplicate JSON field')
                    result[key] = value
                return result
            body = json.loads(data, object_pairs_hook=unique_object)
        except (ValueError, UnicodeError, RecursionError):
            raise ReviewError('invalid_parameters', 'Invalid parameter JSON object', 422) from None
        value = await run_in_threadpool(service.change, request.cookies.get(COOKIE_NAME),
            request.headers.get(CSRF_HEADER), int(match[1:-1]), body)
        return response(value)

    return router
