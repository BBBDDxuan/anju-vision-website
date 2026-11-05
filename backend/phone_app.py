"""One phone origin: existing video APIs, confirmed alerts and allowlisted safety APIs."""
import asyncio
from contextlib import asynccontextmanager
import importlib
import inspect
import os
from pathlib import Path
import re
import sys

import httpx
from fastapi import Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .camera_monitoring import monitoring_router
from .alert_center import AlertCenter
from .alert_sources import AlertSources
from .errors import AppError
from .main import create_app as video_app

ROOT = Path(__file__).resolve().parents[1]


def create_internal_safety_app():
    """Legacy storage/device endpoints; the unified outbox owns formal notifications."""
    from dataclasses import replace
    sys.path.insert(0, str(ROOT / 'elder-safety/backend'))
    from safety_backend.config import Settings
    from safety_backend.main import create_app as safety_app
    return safety_app(replace(Settings.from_env(), worker_enabled=False))


def create_app(settings=None, provider=None, *, safety_repo=None, safety_url=None, push_handler=None, transport=None):
    app = video_app(settings, provider)
    if app.state.settings.cloud_mode:
        raise ValueError('Phone gateway must run separately from cloud mode')
    if not app.state.settings.lan_token:
        raise ValueError('Phone gateway requires APP_LAN_TOKEN')
    app.include_router(monitoring_router(app.state.live_camera))
    owned_repo = safety_repo is None
    if owned_repo:
        sys.path.insert(0, str(ROOT / 'elder-safety/backend'))
        from safety_backend.config import Settings as SafetySettings
        from safety_backend.repository import Repository
        safety_repo = Repository(SafetySettings.from_env())
    safety_url = safety_url or os.getenv('APP_SAFETY_INTERNAL_URL', 'http://127.0.0.1:8010')
    url = httpx.URL(safety_url)
    if (url.scheme != 'http' or url.host != '127.0.0.1' or url.username or url.password
            or url.path != '/' or url.query or url.fragment):
        raise ValueError('Safety upstream must be an explicit loopback HTTP origin')
    if push_handler is None and os.getenv('APP_ALERT_PUSH_HANDLER'):
        module, function = os.environ['APP_ALERT_PUSH_HANDLER'].split(':', 1)
        push_handler = getattr(importlib.import_module(module), function)
    if push_handler is not None and not inspect.iscoroutinefunction(push_handler):
        raise ValueError('C push handler must be async and accept alert, idempotency_key')
    center = AlertCenter(app.state.settings.data_dir / 'alert-center')
    sources = AlertSources(center, app.state.db, safety_repo)
    app.state.alert_center, app.state.alert_sources = center, sources
    old_lifespan = app.router.lifespan_context

    async def collect():
        while True:
            current = asyncio.create_task(asyncio.to_thread(sources.sync))
            try:
                await asyncio.shield(current)
            except asyncio.CancelledError:
                await current
                raise
            await asyncio.sleep(1)

    async def dispatch():
        while True:
            if push_handler is not None:
                job = await asyncio.to_thread(center.claim)
                if job:
                    item = await asyncio.to_thread(center.get, job['alert_id'])
                    if item['handling_status'] != 'unacknowledged' or item['review_status'] != 'confirmed':
                        await asyncio.to_thread(center.cancel_notification, item['id'])
                        await asyncio.sleep(.1)
                        continue
                    try:
                        # C owns recipient/token handling. Never infer delivery from a mock mode.
                        receipt = await asyncio.wait_for(push_handler(item, idempotency_key=item['id']), timeout=10)
                        if (not isinstance(receipt, dict) or receipt.get('accepted') is not True
                                or receipt.get('is_mock') is not False):
                            raise ValueError('A real provider acceptance receipt is required')
                    except Exception:
                        await asyncio.to_thread(center.delivered, item['id'], failed=True)
                    else:
                        await asyncio.to_thread(center.delivered, item['id'])
            await asyncio.sleep(.5)

    @asynccontextmanager
    async def lifespan(instance):
        tasks = []
        try:
            async with old_lifespan(instance):
                await asyncio.to_thread(sources.sync)
                tasks = [asyncio.create_task(collect()), asyncio.create_task(dispatch())]
                try:
                    yield
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            if owned_repo:
                safety_repo.engine.dispose()
    app.router.lifespan_context = lifespan

    @app.get('/api/connection')
    def connection():
        try:
            app.state.manager.provider.ready()
            analysis = {'ready': True}
        except AppError as exc:
            analysis = {'ready': False, 'error': exc.code}
        return {'contract_version': '2026-11-04.v1', 'household': 'default',
                'analysis': analysis, 'sources': sources.status,
                'notifications': {'configured': push_handler is not None,
                                  'delivery_verified': False},
                'legacy_safety_worker': 'disabled_in_unified_launcher',
                'camera_control': 'authenticated_shared_backend',
                'camera_monitoring': {'endpoint': '/api/monitoring/camera',
                                      'lifetime': 'backend', 'shared_camera': True}}

    @app.get('/api/alerts')
    def alerts(limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
               source: str | None = Query(None, pattern='^(live_camera|uploaded_recording)$')):
        return center.listing(limit, offset, source)

    @app.get('/api/alerts/changes')
    def changes(after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
        return center.changes(after, limit)

    @app.get('/api/alerts/{identifier}')
    def detail(identifier: str):
        return center.get(identifier)

    def handle(identifier, action):
        result = center.handle(identifier, action)
        try:
            sources.acknowledge_source(result, action)
        except Exception:
            # Unified state is authoritative even if the old run has been removed.
            pass
        return result

    @app.post('/api/alerts/{identifier}/ack')
    def acknowledge(identifier: str):
        return handle(identifier, 'ack')

    @app.post('/api/alerts/{identifier}/resolve')
    def resolve(identifier: str):
        return handle(identifier, 'resolve')

    @app.get('/api/alerts/{identifier}/evidence/{number}')
    def evidence(identifier: str, number: int = 0, version: str | None = Query(None, pattern=r'^[a-f0-9]{64}$')):
        if number < 0:
            raise AppError('EVIDENCE_NOT_FOUND', '未找到该告警证据。', 404)
        path = center.evidence_path(identifier, number, version)
        if not path.is_file():
            raise AppError('EVIDENCE_NOT_FOUND', '证据暂不可用。', 404)
        return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})

    # Fixed upstream and user-only allowlist: a phone must never acquire device
    # ingest privileges, arbitrary proxy destinations, or loopback camera control.
    allowed = {
        'GET': r'/health|/api/v1/(?:devices(?:/[^/]+/status)?|events(?:/[^/]+)?|evidence/[^/]+|preferences)',
        'POST': r'/api/v1/(?:events/[^/]+/(?:ack|resolve)|push-tokens)',
        'DELETE': r'/api/v1/(?:events/[^/]+|push-tokens/[^/]+)',
        'PUT': r'/api/v1/preferences',
    }

    @app.api_route('/health', methods=['GET'])
    @app.api_route('/api/v1/{path:path}', methods=['GET', 'POST', 'PUT', 'DELETE'])
    async def safety_proxy(request: Request):
        path = request.url.path
        if (not re.fullmatch(allowed[request.method], path) or '%' in request.scope.get('raw_path', b'').decode('ascii', 'ignore')
                or any(part in {'.', '..'} for part in path.split('/'))):
            return JSONResponse({'error': {'code': 'PHONE_ROUTE_FORBIDDEN', 'message': '此接口不开放给手机。'}}, status_code=403)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 32_000:
                return JSONResponse({'error': {'code': 'REQUEST_TOO_LARGE', 'message': '请求内容过大。'}}, status_code=413)
        try:
            async with httpx.AsyncClient(base_url=safety_url, timeout=10, trust_env=False, transport=transport) as client:
                result = await client.request(request.method, path, params=request.query_params, content=bytes(body),
                    headers={'Authorization': 'Bearer ' + safety_repo.s.user_token, 'Content-Type': 'application/json'})
        except httpx.HTTPError:
            return JSONResponse({'error': {'code': 'SAFETY_SERVICE_UNAVAILABLE', 'message': '内部安全服务暂不可用。'}}, status_code=503)
        return Response(result.content, status_code=result.status_code,
                        headers={'Content-Type': result.headers.get('content-type', 'application/json'), 'Cache-Control': 'no-store'})

    return app
