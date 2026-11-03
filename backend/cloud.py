"""Single-worker visitor isolation and bounded storage for a public demo."""
import re
from ipaddress import ip_address, ip_network
import secrets
import threading
import shutil
from pathlib import Path

from .errors import AppError

COOKIE = 'anju_visitor'


class TrustedProxyHostMiddleware:
    """Use one exact external host only from explicitly configured proxy peers."""
    def __init__(self, app, cidrs, allowed_hosts):
        self.app = app
        self.networks = tuple(ip_network(value) for value in cidrs)
        if any(network.prefixlen != network.max_prefixlen for network in self.networks):
            raise ValueError('Proxy trust requires exact IP addresses, not subnets.')
        self.hosts = {host.lower() for host in allowed_hosts if '*' not in host}

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and scope.get('client'):
            try:
                peer = ip_address(scope['client'][0])
                trusted = any(peer in network for network in self.networks)
            except ValueError:
                trusted = False
            headers = scope.get('headers', [])
            forwarded = [value for key, value in headers if key.lower() == b'x-forwarded-host']
            if trusted and len(forwarded) == 1:
                host = forwarded[0].decode('latin1').lower()
                # Comma lists, ports, unknown domains and duplicate headers fail closed.
                if host in self.hosts:
                    scope = dict(scope, headers=[(key, value) for key, value in headers
                        if key.lower() != b'host'] + [(b'host', host.encode('ascii'))])
        await self.app(scope, receive, send)


class CloudAccess:
    def __init__(self, settings, db):
        self.settings, self.db = settings, db
        self.lock = threading.Lock()
        self.pending = {}

    def session(self, request):
        value = request.cookies.get(COOKIE, '')
        return value if re.fullmatch(r'[A-Za-z0-9_-]{43}', value) else secrets.token_urlsafe(32)

    @staticmethod
    def public(video):
        # This flag is written by the verified demo importer, never by uploads.
        return video['metadata'].get('imported_real_recording') is True

    def visible(self, video, visitor):
        return self.public(video) or video['metadata'].get('cloud_visitor') == visitor

    def authorize(self, request, visitor):
        path = request.url.path
        match = re.match(r'^/(?:api/videos|media/videos|media/frames)/([^/]+)(?:/|$)', path)
        if match and match[1] != 'upload':
            video = self.db.video(match[1])
        elif match := re.match(r'^/api/jobs/([^/]+)$', path):
            video = self.db.video(self.db.job(match[1])['video_id'])
        else:
            return
        if not self.visible(video, visitor):
            raise AppError('NOT_FOUND', '未找到当前访客可访问的录像。', 404)
        if self.public(video) and request.method not in {'GET', 'HEAD', 'OPTIONS'} and path not in {
                f"/api/videos/{video['id']}/query", f"/api/videos/{video['id']}/reanalyze"}:
            raise AppError('DEMO_READ_ONLY', '内置案例原记录受到保护；请点击重新分析，在自己的副本中体验。', 403)

    def reserve_upload(self, request, visitor):
        try:
            size = int(request.headers.get('content-length', '0'))
        except ValueError:
            size = 0
        if size <= 0:
            raise AppError('UPLOAD_LENGTH', '云端上传需要明确的文件长度，请使用网页上传。', 411)
        if self.settings.max_upload_bytes and size > self.settings.max_upload_bytes + 1024*1024:
            raise AppError('UPLOAD_TOO_LARGE', '视频超过云端演示上传上限。', 413)
        # Account for original/playback copies and room for evidence frames.
        estimate = 2*size + 10*1024*1024
        return self.reserve_video(visitor, estimate)

    def reserve_video(self, visitor, estimate):
        """Reserve a library slot for an upload or a private demo analysis."""
        with self.lock:
            videos = [self.db.video(v['id']) for v in self.db.list_videos()]
            if sum(not self.public(v) for v in videos)+len(self.pending) >= self.settings.cloud_max_videos:
                raise AppError('CLOUD_CAPACITY', '云端演示录像容量已满，请稍后体验或联系作者清理。', 429)
            owned = sum(not self.public(v) and v['metadata'].get('cloud_visitor') == visitor for v in videos)
            if owned+sum(v[0] == visitor for v in self.pending.values()) >= self.settings.cloud_videos_per_visitor:
                raise AppError('VISITOR_CAPACITY', '当前访客录像数量已达上限，请先删除自己的录像。', 429)
            used = sum(p.stat().st_size for v in videos for p in
                       {Path(v['original_path']), Path(v['playback_path'])} if p.is_file())
            pending_bytes = sum(v[1] for v in self.pending.values())
            if self.settings.cloud_storage_mb and used+pending_bytes+estimate > self.settings.cloud_storage_mb*1024**2:
                raise AppError('CLOUD_STORAGE', '云端演示存储额度不足，请先清理自己的录像。', 429)
            if pending_bytes+estimate+256*1024**2 > shutil.disk_usage(self.settings.data_dir).free:
                raise AppError('DISK_SPACE', '当前可用存储不足以接收视频并准备回放，请先清理自己的录像。', 413)
            ticket = secrets.token_hex(16)
            self.pending[ticket] = (visitor, estimate)
            return ticket

    def release_upload(self, ticket):
        with self.lock:
            self.pending.pop(ticket, None)

    def set_cookie(self, response, visitor):
        secure = self.settings.cloud_cookie_secure
        response.set_cookie(COOKIE, visitor, httponly=True, secure=secure,
                            samesite='none' if secure else 'lax', max_age=7*24*3600)
        # CHIPS permits isolated cookies in supporting browsers' Studio iframe.
        if secure:
            response.raw_headers = [(k, v+b'; Partitioned' if k == b'set-cookie' and v.startswith(COOKIE.encode()+b'=') else v)
                                    for k, v in response.raw_headers]
