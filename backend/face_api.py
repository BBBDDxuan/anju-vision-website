"""Authenticated local/LAN face APIs; raw images are processed without being saved."""
import asyncio
import ipaddress

from pydantic import ValidationError

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from .errors import AppError
from .face_recognition import MAX_IMAGE_BYTES
from .lan_access import authorize_lan
from .member_profiles import MemberProfileInput


def face_router(service, settings):
    router = APIRouter(prefix='/api', tags=['家庭成员识别'])

    def allowed(request):
        if settings.cloud_mode:
            raise AppError('FACE_RECOGNITION_DISABLED', '云部署不开放家庭成员人脸接口。', 403)
        if getattr(request.state, 'account', None):
            return
        authorize_lan(request, settings.lan_token)
        # Also authenticate when this router is mounted without main's middleware.
        try:
            local = ipaddress.ip_address(request.client.host).is_loopback
        except (ValueError, AttributeError):
            local = False
        if not local and not settings.lan_token:
            raise AppError('FACE_LOCAL_ONLY', '未配置手机访问令牌时，人脸接口仅允许本机访问。', 403)

    async def image(request):
        if request.headers.get('content-type', '').split(';', 1)[0].lower() not in {'image/jpeg', 'image/png'}:
            raise AppError('FACE_IMAGE_TYPE', '请直接发送 JPEG/PNG 图片内容，不使用 multipart。', 415)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_IMAGE_BYTES:
                raise AppError('FACE_IMAGE_TOO_LARGE', '人脸图片不得超过 5 MiB。', 413)
            data.extend(chunk)
        return bytes(data)

    def response(value, status=200):
        return JSONResponse(value, status_code=status, headers={'Cache-Control': 'no-store'})

    @router.get('/faces/status')
    async def status(request: Request):
        allowed(request)
        return response(await asyncio.to_thread(service.status))

    @router.get('/faces/members')
    async def members(request: Request):
        allowed(request)
        return response({'members': await asyncio.to_thread(service.members)})

    @router.post('/faces/members/{member_id}/samples', status_code=201)
    async def enroll(request: Request, member_id: str,
                     name: str = Query(min_length=1, max_length=80), consent: bool = Query(False)):
        allowed(request)
        service.require_enabled()
        if not consent:
            raise AppError('FACE_CONSENT_REQUIRED', '录入前须取得被录入者的单独同意。', 422)
        data = await image(request)
        member = await asyncio.to_thread(service.enroll, member_id, name, consent, data)
        return response({'member': member}, 201)

    @router.delete('/faces/members/{member_id}/samples')
    @router.delete('/faces/members/{member_id}')
    async def delete(request: Request, member_id: str):
        allowed(request)
        return response(await asyncio.to_thread(service.delete, member_id))

    @router.post('/faces/recognize')
    async def recognize(request: Request):
        allowed(request)
        service.require_enabled()
        data = await image(request)
        return response(await asyncio.to_thread(service.recognize, data))

    @router.get('/household/members')
    async def profiles(request: Request):
        allowed(request)
        return response({'members': await asyncio.to_thread(service.profiles.members)})

    @router.put('/household/members/{member_id}')
    async def upsert_profile(request: Request, member_id: str):
        allowed(request)
        if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
            raise AppError('HOUSEHOLD_CONTENT_TYPE', '请发送 application/json 档案。', 415)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 65536:
                raise AppError('HOUSEHOLD_BODY_TOO_LARGE', '档案请求不得超过 64 KiB。', 413)
            data.extend(chunk)
        try:
            profile = MemberProfileInput.model_validate_json(bytes(data))
        except ValidationError:
            raise AppError('HOUSEHOLD_PROFILE_INVALID', '档案字段、类型或长度无效。', 422) from None
        member = await asyncio.to_thread(service.profiles.upsert, member_id, profile)
        return response({'member': member})

    @router.delete('/household/members/{member_id}')
    async def delete_profile(request: Request, member_id: str, revision: int | None = Query(default=None, ge=0)):
        allowed(request)
        return response(await asyncio.to_thread(service.profiles.delete, member_id, revision))

    return router
