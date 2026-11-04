import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
import shutil
from uuid import uuid4

from fastapi import FastAPI, File, UploadFile, Request, Query as QueryParameter
from urllib.parse import unquote, urlsplit
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .live_camera import LiveCamera, camera_router
from .lan_access import authorize_lan
from .record_qa import RecordQuestionAnswerer
from .config import ROOT, Settings
from .db import Database
from .deletion import VideoDeletion
from .cloud import CloudAccess, TrustedProxyHostMiddleware
from .errors import AppError
from .jobs import JobManager
from .grouping import build_event_groups
from .media import file_sha256, inspect_video, prepare_playback
from .providers.base import VisionProvider
from .uploads import ChunkUploads
from .providers.qwen import QwenProvider
from .replay import build_replay
from .review import ReviewManager, RegionSet, HumanReview, VerifyRequest, EvidenceRequest


class Query(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    as_of_ms: int | None = Field(default=None, ge=0)
    entity_id: str | None = None
    use_model: bool = True


class UploadStart(BaseModel):
    filename: str = Field(min_length=1, max_length=150)
    size: int = Field(gt=0)


def create_app(settings: Settings | None = None, provider: VisionProvider | None = None):
    settings = settings or Settings.from_env()
    db = Database(settings.data_dir, journal_mode='DELETE' if settings.cloud_mode else 'WAL')
    provider = provider or QwenProvider(settings, db)
    record_qa = RecordQuestionAnswerer(settings, provider)
    manager = JobManager(settings, db, provider)
    reviewer = ReviewManager(settings, db, provider, manager)
    deletion = VideoDeletion(settings, db, manager, reviewer)
    cloud = CloudAccess(settings, db) if settings.cloud_mode else None
    chunks = ChunkUploads(settings, db, cloud)

    camera = LiveCamera(safety=manager.safety)

    @asynccontextmanager
    async def lifespan(app):
        chunks.recover()
        db.interrupt_running(mark_reserved_unknown=settings.cloud_mode)
        db.refresh_active_states()
        db.refresh_active_alerts(settings.absence_alert_seconds)
        try:
            yield
        finally:
            await asyncio.to_thread(camera.close)
            await asyncio.to_thread(manager.close)

    app = FastAPI(title="居家摄像头系统", version="0.2.0", lifespan=lifespan)
    app.state.db, app.state.manager, app.state.settings = db, manager, settings
    app.state.live_camera = camera
    app.include_router(camera_router(camera, settings.cloud_mode))
    app.state.reviewer = reviewer
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    if cloud and settings.trusted_proxy_cidrs:
        app.add_middleware(TrustedProxyHostMiddleware, cidrs=settings.trusted_proxy_cidrs, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type", "X-Video-Name"])

    @app.middleware("http")
    async def local_origin(request, call_next):
        visitor = cloud.session(request) if cloud else None
        request.state.visitor_id = visitor
        ticket = None
        try:
            authorize_lan(request, settings.lan_token)
            origin = request.headers.get('origin')
            same_host = (origin and urlsplit(origin).scheme in {'http', 'https'}
                         and urlsplit(origin).netloc.lower() == request.url.netloc.lower())
            if request.method not in {'GET', 'HEAD', 'OPTIONS'} and origin and not same_host and origin not in settings.allowed_origins:
                raise AppError('ORIGIN_REJECTED', '请从安居视界网页发起操作。', 403)
            if cloud:
                cloud.authorize(request, visitor)
            if request.url.path in {'/api/videos', '/api/videos/upload'} and request.method == 'POST':
                try:
                    declared_size = int(request.headers.get('content-length', '0'))
                except ValueError:
                    declared_size = 0
                if settings.max_upload_bytes and declared_size > settings.max_upload_bytes + 1024*1024:
                    raise AppError('UPLOAD_TOO_LARGE', '上传请求超过视频大小上限。', 413)
                if cloud:
                    ticket = cloud.reserve_upload(request, visitor)
            response = await call_next(request)
            if cloud and request.url.path == '/api/health' and response.status_code == 400:
                # Log only the hostname, never headers/cookies/credentials, so a
                # deployment can allowlist the exact reverse-proxy host.
                forwarded = request.headers.get('x-forwarded-host', '')
                original = request.headers.get('x-original-host', '')
                safe_host = lambda value: value if len(value) <= 253 and all(c.isalnum() or c in '.:-' for c in value) else '[invalid or absent]'
                logging.getLogger(__name__).warning('Cloud health host rejected: %s; forwarded=%s; original=%s; peer=%s',
                    request.url.hostname, safe_host(forwarded), safe_host(original),
                    request.client.host if request.client else '[absent]')
        except AppError as exc:
            response = JSONResponse({'error': exc.public()}, status_code=exc.status)
            if exc.code == 'PHONE_AUTH_REQUIRED':
                response.headers['WWW-Authenticate'] = 'Bearer'
        finally:
            if ticket:
                cloud.release_upload(ticket)
        if cloud:
            if request.url.path.startswith(('/api/', '/media/')):
                response.headers['Cache-Control'] = 'no-store'
            cloud.set_cookie(response, visitor)
        return response

    @app.exception_handler(AppError)
    async def app_error(request, exc):
        return JSONResponse({"error": exc.public()}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"error": {"code": "INVALID_REQUEST", "message": "从求字段无效，从检查视频文件、问题或查询时间。", "retryable": False}}, status_code=422)

    @app.get("/api/health")
    def health():
        reason = None
        try:
            provider.ready()
        except AppError as exc:
            reason = exc.public()
        return {"status": "ok", "provider": provider.name, "model": provider.model, "is_mock": provider.is_mock,
                "billing_mode": settings.billing_mode, "usage": db.usage_summary(),
                "analysis_ready": reason is None, "configuration_error": reason,
                "cloud_mode": settings.cloud_mode,
                "storage": {"free_bytes": shutil.disk_usage(settings.data_dir).free, "reserve_bytes": 256*1024**2},
                "limits": {"max_video_seconds": settings.max_video_seconds or None, "max_upload_mb": settings.max_upload_bytes // 1024 ** 2 if settings.max_upload_bytes else None,
                           "max_calls": settings.max_calls, "max_frame_references": settings.max_frame_references,
                           'max_output_tokens_per_batch': settings.max_output_tokens, 'max_daily_calls': settings.max_daily_calls}}

    @app.get('/api/demos')
    def public_demos():
        demos = []
        for row in db.list_videos():
            video = db.video(row['id'])
            if not CloudAccess.public(video) or not video['active_run'] or not video['metadata'].get('demo_gallery_visible',True):
                continue
            document, job = db.memory(video['id'])
            frames = document['frames']
            poster = video['metadata'].get('poster_frame_id')
            poster = poster if any(f['id']==poster for f in frames) else frames[0]['id'] if frames else ''
            demos.append({'id':video['id'], 'name':video['name'], 'duration_ms':video['duration_ms'],
                          'case_type':video['metadata'].get('case_type','day_fall'),
                          'record_count':len(document['events']),
                          'priority_count':sum(a['action'] in {'fall','help_gesture','long_absence','smoke','flame','sparks'}
                                               for a in db.safety_alerts(video['id'])),
                          'poster_url':f"/media/frames/{video['id']}/{poster}" if poster else '',
                          'description':video['metadata'].get('demo_description','公开模拟跌倒样例，已保存真实模型分析。')})
        demos.sort(key=lambda d: {'fire':0,'day_fall':1,'night_fall':2,'long_video':3}.get(d['case_type'],4))
        return {'demos':demos}

    @app.get("/api/videos")
    def list_videos(request: Request):
        videos = []
        for video in db.list_videos():
            if cloud:
                detail = db.video(video['id'])
                if not cloud.visible(detail, request.state.visitor_id):
                    continue
                video['read_only_demo'] = cloud.public(detail)
            videos.append(video)
        return {'videos': videos}

    @app.delete('/api/videos/{video_id}')
    @app.post('/api/videos/{video_id}/delete')
    def delete_video(video_id: str):
        return deletion.delete(video_id)

    async def finish_upload(video_id, directory, filename, visitor=None):
        original, playback = directory / 'original.mp4', directory / 'playback.mp4'
        metadata = await asyncio.to_thread(inspect_video, original, settings)
        metadata['file_size_bytes'] = original.stat().st_size
        if cloud:
            metadata['cloud_visitor'] = visitor
        await asyncio.to_thread(prepare_playback, original, playback, settings, metadata)
        record = {'id': video_id, 'sha256': await asyncio.to_thread(file_sha256, original),
                  'name': Path(filename.replace('\\', '/')).name[:150], 'duration_ms': metadata['duration_ms'],
                  'original_path': str(original), 'playback_path': str(playback), 'metadata': metadata}
        db.add_video(record)
        return {'video_id': video_id, 'name': record['name'], 'duration_ms': record['duration_ms'],
                'sha256': record['sha256'], 'playback_url': f'/media/videos/{video_id}'}

    @app.post('/api/uploads', status_code=201)
    async def create_upload(body: UploadStart, request: Request):
        return chunks.create(body.filename, body.size, request.state.visitor_id)

    @app.post('/api/uploads/{upload_id}/chunks')
    async def upload_chunk(upload_id: str, request: Request, offset: int = QueryParameter(ge=0)):
        return await chunks.append(upload_id, request.state.visitor_id, offset, request)

    @app.post('/api/uploads/{upload_id}/complete', status_code=201)
    async def complete_upload(upload_id: str, request: Request):
        return await chunks.complete(upload_id, request.state.visitor_id, finish_upload)

    @app.post('/api/uploads/{upload_id}/cancel')
    async def cancel_upload(upload_id: str, request: Request):
        return await chunks.cancel(upload_id, request.state.visitor_id)

    @app.post('/api/videos/upload', status_code=201)
    async def stream_upload(request: Request):
        # Raw streaming avoids multipart's whole-file temporary spool for large recordings.
        video_id = 'v_' + uuid4().hex
        directory = settings.data_dir / 'videos' / video_id
        directory.mkdir(parents=True)
        count = 0
        try:
            length = int(request.headers.get('content-length', '0'))
            if settings.max_upload_bytes and length > settings.max_upload_bytes:
                raise AppError('UPLOAD_TOO_LARGE', '文件超过当前上传大小上限。', 413)
            reserve = 256*1024**2
            if length > max(0, shutil.disk_usage(directory).free-reserve):
                raise AppError('DISK_SPACE', '数据盘空间不足，从释放空间或配置其他数据目录后再上传。', 413)
            with (directory / 'original.mp4').open('wb') as stream:
                async for chunk in request.stream():
                    count += len(chunk)
                    if settings.max_upload_bytes and count > settings.max_upload_bytes:
                        raise AppError('UPLOAD_TOO_LARGE', '文件超过当前上传大小上限。', 413)
                    if shutil.disk_usage(directory).free < len(chunk)+reserve:
                        raise AppError('DISK_SPACE', '数据盘可用空间不足，上传已停止，未保留不完整文件。', 413)
                    stream.write(chunk)
            return await finish_upload(video_id, directory, unquote(request.headers.get('x-video-name', 'video.mp4')), request.state.visitor_id)
        except BaseException:
            if directory.resolve().parent == (settings.data_dir / 'videos').resolve():
                shutil.rmtree(directory)
            raise

    @app.post("/api/videos", status_code=201)
    async def upload(request: Request, file: UploadFile = File(...)):
        video_id = "v_" + uuid4().hex
        directory = settings.data_dir / "videos" / video_id
        directory.mkdir(parents=True)
        original, playback = directory / "original.mp4", directory / "playback.mp4"
        count = 0
        try:
            with original.open("wb") as stream:
                while chunk := await file.read(1024 * 1024):
                    count += len(chunk)
                    if settings.max_upload_bytes and count > settings.max_upload_bytes:
                        raise AppError("UPLOAD_TOO_LARGE", f"视频超过配置的{settings.max_upload_bytes // 1024 ** 2}MB上传上限。", 413)
                    if shutil.disk_usage(directory).free < len(chunk)+256*1024**2:
                        raise AppError('DISK_SPACE', '数据盘可用空间不足，上传已停止。', 413)
                    stream.write(chunk)
            return await finish_upload(video_id, directory, file.filename or 'video.mp4', request.state.visitor_id)
        except BaseException:
            # This is a newly generated per-upload directory, never a user-supplied target.
            if directory.resolve().parent == (settings.data_dir / "videos").resolve():
                shutil.rmtree(directory)
            raise
        finally:
            await file.close()

    @app.get("/api/videos/{video_id}")
    def video_details(video_id: str):
        video = db.video(video_id)
        result = {**{k: video[k] for k in ["id", "name", "sha256", "duration_ms", "active_run", "metadata"]}, "latest_job": db.latest_job(video_id)}
        result['read_only_demo'] = bool(cloud and cloud.public(video))
        result['metadata'] = {k: v for k, v in video['metadata'].items() if k != 'cloud_visitor'}
        return result

    @app.post("/api/videos/{video_id}/analyze", status_code=202)
    def analyze(video_id: str, auto_continue: bool = True, mode: Literal['safety', 'objects', 'all'] = 'all'):
        with reviewer.lock:
            reviewer.assert_idle(video_id)
            return manager.start(video_id, auto_continue=auto_continue, mode=mode)

    @app.post('/api/videos/{video_id}/reanalyze', status_code=202)
    def reanalyze_demo(video_id: str, request: Request):
        source = db.video(video_id)
        if not CloudAccess.public(source):
            raise AppError('NOT_DEMO', '此入口用于内置案例；普通录像请使用分析按钮。', 400)
        provider.ready()
        original = Path(source['original_path'])
        playback = Path(source['playback_path'])
        if not original.is_file() or not playback.is_file():
            raise AppError('DEMO_MEDIA_MISSING', '案例视频暂不可用，请稍后重试。', 409)
        ticket = cloud.reserve_video(request.state.visitor_id, 10*1024**2) if cloud else None
        copy_id = 'v_' + uuid4().hex
        created = False
        try:
            # Share immutable media only. Jobs, caches, frames and results are
            # generated under the new ID; deletion retains referenced assets.
            metadata = {k:v for k,v in source['metadata'].items() if k not in {
                'imported_real_recording','cloud_visitor','demo_gallery_visible',
                'poster_frame_id','demo_description','source_mapping','playback_derivative'}}
            metadata.update(reanalysis_of=video_id, file_size_bytes=original.stat().st_size,
                reanalysis_input='compressed_playback' if source['metadata'].get('playback_derivative') else 'source_video')
            if cloud:
                metadata['cloud_visitor'] = request.state.visitor_id
            db.add_video({'id':copy_id, 'sha256':file_sha256(original),
                'name':'重新分析 · '+source['name'], 'duration_ms':source['duration_ms'],
                'original_path':str(original), 'playback_path':str(playback), 'metadata':metadata})
            created = True
            with reviewer.lock:
                return manager.start(copy_id)
        except BaseException:
            if created and db.latest_job(copy_id) is None:
                deletion.delete(copy_id)
            raise
        finally:
            if ticket:
                cloud.release_upload(ticket)

    @app.post('/api/videos/{video_id}/resume', status_code=202)
    def resume(video_id: str, max_calls: int | None = QueryParameter(default=None, ge=1, le=16), auto_continue: bool = True):
        with reviewer.lock:
            reviewer.assert_idle(video_id)
            return manager.start(video_id, resume=True, call_limit=max_calls, auto_continue=auto_continue)

    @app.post('/api/videos/{video_id}/pause', status_code=202)
    def pause(video_id: str):
        return manager.pause(video_id)

    @app.get('/api/videos/{video_id}/analysis-plan')
    def analysis_plan(video_id: str, mode: Literal['safety', 'objects', 'all'] = 'all'):
        return manager.plan(video_id, mode)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return manager.job_snapshot(job_id)

    @app.get("/api/videos/{video_id}/events")
    def events(video_id: str, grouped: bool = False):
        document, job = db.memory(video_id)
        result = dict(document)
        result["job"] = job
        result["is_mock"] = job.get("is_mock", False)
        if grouped:
            result['event_groups'] = build_event_groups(document, job)
        for frame in result["frames"]:
            frame["url"] = f"/media/frames/{video_id}/{frame['id']}"
        return result

    @app.get('/api/videos/{video_id}/alerts')
    def safety_alerts(video_id: str):
        alerts = db.safety_alerts(video_id)
        video = db.video(video_id)
        evidence, frames = [], []
        if video['active_run']:
            document, _ = db.raw_memory(video_id)
            refs = {ref for alert in alerts for ref in alert['evidence_ids']}
            evidence = [e for e in document['evidence'] if e['id'] in refs]
            fids = {fid for ref in evidence for fid in ref['frame_ids']}
            frames = [{**frame, 'url': f"/media/frames/{video_id}/{frame['id']}"} for frame in document['frames'] if frame['id'] in fids]
        return {'alerts': alerts, 'evidence': evidence, 'frames': frames, 'live_camera_connected': False,
                'remote_notifications_configured': False, 'source': 'uploaded_recording',
                'absence_alert_seconds': settings.absence_alert_seconds}

    @app.post('/api/videos/{video_id}/alerts/{alert_id}/acknowledge')
    def acknowledge_alert(video_id: str, alert_id: str):
        return db.acknowledge_alert(video_id, alert_id)

    @app.post("/api/videos/{video_id}/query")
    def query(video_id: str, request: Query):
        document, job = db.memory(video_id)
        result = record_qa.answer(document, job, request.question, request.as_of_ms, request.entity_id, request.use_model)
        result["run_id"] = job["id"]
        return result

    @app.get("/api/videos/{video_id}/replay")
    def entity_replay(video_id: str, entity_id: str, as_of_ms: int | None = None):
        document, job = db.memory(video_id)
        return build_replay(document, job, entity_id, as_of_ms)

    @app.get('/api/videos/{video_id}/review')
    def review_state(video_id: str):
        return reviewer.state(video_id)

    @app.post('/api/videos/{video_id}/regions')
    def save_regions(video_id: str, request: RegionSet):
        return reviewer.save_regions(video_id, request)

    @app.post('/api/videos/{video_id}/review/{event_id}/human')
    def human_review(video_id: str, event_id: str, request: HumanReview):
        return reviewer.human_review(video_id, event_id, request)

    @app.post('/api/videos/{video_id}/review/{event_id}/verify')
    async def verify_event(video_id: str, event_id: str, request: VerifyRequest):
        return await asyncio.to_thread(reviewer.verify, video_id, event_id, request)

    @app.post('/api/videos/{video_id}/review/{event_id}/sample')
    async def sample_evidence(video_id: str, event_id: str, request: EvidenceRequest):
        return await asyncio.to_thread(reviewer.sample_evidence, video_id, event_id, request)

    @app.get("/media/videos/{video_id}")
    def video_media(video_id: str):
        return FileResponse(db.video(video_id)["playback_path"], media_type="video/mp4")

    @app.get("/media/frames/{video_id}/{frame_id}")
    def frame_media(video_id: str, frame_id: str):
        document, job = db.memory(video_id)
        if frame_id not in {f["id"] for f in document["frames"]}:
            raise AppError("NOT_FOUND", "未找到该视频证据帧。", 404)
        reviewed = reviewer.frame_path(video_id, frame_id)
        if reviewed:
            return FileResponse(reviewed, media_type='image/jpeg')
        return FileResponse(settings.data_dir / "runs" / job["id"] / "frames" / f"{frame_id}.jpg", media_type="image/jpeg")

    dist = ROOT / "frontend/dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/")
    @app.get('/workspace')
    def index():
        if not (dist / "index.html").is_file():
            raise AppError("FRONTEND_NOT_BUILT", "从先进入frontend执行npm install和npm run build，或使用开发端口5173。", 503)
        return FileResponse(dist / "index.html")

    return app
