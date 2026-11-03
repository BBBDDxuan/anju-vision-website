"""Single-worker, visitor-scoped chunk uploads for gateways with body limits."""
import asyncio
import json
from pathlib import Path
import shutil
import time
from uuid import uuid4

from .errors import AppError

CHUNK_BYTES = 4*1024**2


class ChunkUploads:
    def __init__(self, settings, db, cloud):
        self.settings, self.db, self.cloud = settings, db, cloud
        self.root = (settings.data_dir/'videos').resolve()
        self.sessions = {}

    def discard(self, session):
        self.sessions.pop(session['id'], None)
        session['status'] = 'cancelled'
        directory = session['directory']
        # Only a generated direct child of the managed video directory is removed.
        if directory.resolve().parent != self.root or directory.is_symlink() or directory.is_junction():
            raise AppError('UPLOAD_PATH', '上传目录无效，未执行清理。', 409)
        try:
            shutil.rmtree(directory)
        finally:
            if self.cloud:
                self.cloud.release_upload(session['ticket'])

    def recover(self):
        """Clean marked incomplete transfers after restart; retain registered videos."""
        for marker in self.root.glob('v_*/chunk-upload.json'):
            directory = marker.parent
            if directory.name in self.sessions or directory.resolve().parent != self.root or directory.is_junction() or directory.is_symlink():
                continue
            try:
                if json.loads(marker.read_text(encoding='utf8')).get('kind') != 'chunk-upload-v1':
                    continue
                try:
                    self.db.video(directory.name)
                except AppError as exc:
                    if exc.code != 'NOT_FOUND':
                        raise
                    shutil.rmtree(directory)
                else:
                    marker.unlink()
            except (OSError, ValueError):
                continue

    def create(self, filename, total, visitor):
        for session in list(self.sessions.values()):
            if time.monotonic()-session['touched'] > 1800 and not session['lock'].locked():
                self.discard(session)
        if self.settings.max_upload_bytes and total > self.settings.max_upload_bytes:
            raise AppError('UPLOAD_TOO_LARGE', '文件超过当前上传大小上限。', 413)
        estimate = 2*total+10*1024**2
        ticket = self.cloud.reserve_video(visitor, estimate) if self.cloud else None
        if not self.cloud and estimate+256*1024**2 > shutil.disk_usage(self.settings.data_dir).free:
            raise AppError('DISK_SPACE', '存储不足以接收视频并准备回放。', 413)
        identifier = 'v_'+uuid4().hex
        directory = self.root/identifier
        session = {'id':identifier,'directory':directory,'visitor':visitor,'total':total,
                   'received':0,'filename':Path(filename.replace('\\','/')).name[:150],
                   'ticket':ticket,'lock':asyncio.Lock(),'touched':time.monotonic(),'status':'receiving'}
        try:
            directory.mkdir(parents=True)
            (directory/'original.mp4').touch()
            (directory/'chunk-upload.json').write_text('{"kind":"chunk-upload-v1"}',encoding='utf8')
            self.sessions[identifier] = session
        except BaseException:
            if directory.exists():
                self.discard(session)
            elif self.cloud:
                self.cloud.release_upload(ticket)
            raise
        return {'upload_id':identifier,'chunk_bytes':CHUNK_BYTES,'received':0}

    def get(self, identifier, visitor):
        session = self.sessions.get(identifier)
        if session is None or session['visitor'] != visitor:
            raise AppError('NOT_FOUND', '未找到当前访客的上传任务。', 404)
        return session

    async def append(self, identifier, visitor, offset, request):
        session = self.get(identifier, visitor)
        async with session['lock']:
            if session['status'] != 'receiving' or offset != session['received']:
                raise AppError('UPLOAD_OFFSET', '上传片段位置不一致，请重新上传。', 409)
            count = 0
            with (session['directory']/'original.mp4').open('r+b') as stream:
                stream.seek(offset)
                try:
                    async for chunk in request.stream():
                        count += len(chunk)
                        if count > CHUNK_BYTES or offset+count > session['total']:
                            raise AppError('CHUNK_TOO_LARGE', '上传片段超过本次允许长度。', 413)
                        if shutil.disk_usage(self.settings.data_dir).free < len(chunk)+256*1024**2:
                            raise AppError('DISK_SPACE', '可用存储不足，上传已停止。', 413)
                        stream.write(chunk)
                    if count == 0:
                        raise AppError('EMPTY_CHUNK', '上传片段为空。', 400)
                except BaseException:
                    stream.truncate(offset)
                    raise
            session['received'] += count
            session['touched'] = time.monotonic()
            return {'received':session['received'],'total':session['total']}

    async def complete(self, identifier, visitor, finish):
        session = self.get(identifier, visitor)
        async with session['lock']:
            if session['received'] != session['total'] or session['status'] != 'receiving':
                raise AppError('UPLOAD_INCOMPLETE', '视频尚未传输完整。', 409)
            session['status'] = 'finalizing'
            try:
                result = await finish(identifier, session['directory'], session['filename'], visitor)
            except BaseException:
                self.discard(session)
                raise
            self.sessions.pop(identifier, None)
            if self.cloud:
                self.cloud.release_upload(session['ticket'])
            session['status'] = 'completed'
            try:
                (session['directory']/'chunk-upload.json').unlink(missing_ok=True)
            except OSError:
                pass  # Restart recovery keeps already registered videos.
            return result

    async def cancel(self, identifier, visitor):
        session = self.get(identifier, visitor)
        async with session['lock']:
            if session['status'] == 'receiving':
                self.discard(session)
            return {'cancelled':session['status'] != 'completed'}
