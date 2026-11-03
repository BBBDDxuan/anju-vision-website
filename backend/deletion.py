"""Delete one library entry and exclusively owned managed assets, retaining billing totals."""
import json
from pathlib import Path
import re
import shutil
from uuid import uuid4

from .errors import AppError


class VideoDeletion:
    def __init__(self, settings, db, jobs, reviewer):
        self.root = settings.data_dir.resolve()
        self.db, self.jobs, self.reviewer = db, jobs, reviewer

    def child(self, category: str, identifier: str) -> Path:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', identifier):
            raise AppError('DELETE_PATH', '录像关联文件路径无效，未执行删除。', 409)
        parent = self.root/category
        path = parent/identifier
        # Check absolute targets before any recursive operation or move. Reject
        # junctions/symlinks rather than following them outside managed storage.
        if (parent.resolve().parent != self.root or path.resolve().parent != parent.resolve()
                or path.is_symlink() or path.is_junction()):
            raise AppError('DELETE_PATH', '录像关联文件路径不安全，未执行删除。', 409)
        return path

    def delete(self, video_id: str):
        moves: list[tuple[Path, Path]] = []
        staging = None
        shared = False
        with self.reviewer.lock, self.jobs.lock:
            self.reviewer.assert_idle(video_id)
            try:
                with self.db.connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    row = c.execute('SELECT * FROM videos WHERE id=?', (video_id,)).fetchone()
                    if not row:
                        raise AppError('NOT_FOUND', '未找到该视频。', 404)
                    running = c.execute("SELECT 1 FROM jobs WHERE video_id=? AND status IN ('queued','running')", (video_id,)).fetchone()
                    pending = c.execute("SELECT 1 FROM requests WHERE video_id=? AND status='reserved'", (video_id,)).fetchone()
                    if running or pending:
                        raise AppError('DELETE_BUSY', '录像正在分析或复核，请等待完成后再删除。', 409)
                    other = c.execute('SELECT id,original_path,playback_path FROM videos WHERE id!=?', (video_id,)).fetchall()
                    other_ids = {v['id'] for v in other}
                    references = [Path(v[key]).resolve() for v in other for key in ['original_path','playback_path']]
                    targets = set()
                    # Imported review entries can share another entry's original.
                    # Also remove an orphaned managed original when its last user is removed.
                    video_dirs = {video_id}
                    for key in ['original_path','playback_path']:
                        path = Path(row[key]).resolve()
                        if path.parent.parent == (self.root/'videos').resolve():
                            video_dirs.add(path.parent.name)
                    for identifier in video_dirs:
                        directory = self.child('videos', identifier)
                        if identifier in other_ids or any(p.is_relative_to(directory.resolve()) for p in references):
                            shared = shared or directory.exists()
                        elif directory.exists():
                            targets.add(directory)
                    run_ids = {r[0] for r in c.execute('SELECT id FROM jobs WHERE video_id=?', (video_id,))}
                    run_ids.update(r[0] for r in c.execute('SELECT DISTINCT job_id FROM requests WHERE video_id=?', (video_id,)))
                    review_ids = set()
                    for record in c.execute('SELECT payload FROM review_log WHERE video_id=?', (video_id,)):
                        payload = json.loads(record[0])
                        review_ids.update(payload[k] for k in ['check_id','sample_id'] if payload.get(k))
                    run_ids.update(review_ids)
                    for category, identifiers in [('runs',run_ids), ('reviews',review_ids)]:
                        for identifier in identifiers:
                            directory = self.child(category, identifier)
                            if directory.exists():
                                targets.add(directory)
                    if targets:
                        staging = self.child('.trash', 'delete_'+uuid4().hex)
                        staging.mkdir(parents=True)
                        for index, original in enumerate(sorted(targets)):
                            destination = staging/f'{index:04d}-{original.name}'
                            original.rename(destination)
                            moves.append((original,destination))
                    c.execute('DELETE FROM states WHERE run_id IN (SELECT id FROM jobs WHERE video_id=?)', (video_id,))
                    c.execute('DELETE FROM events WHERE video_id=?', (video_id,))
                    c.execute('DELETE FROM memories WHERE video_id=?', (video_id,))
                    c.execute('DELETE FROM review_log WHERE video_id=?', (video_id,))
                    c.execute('DELETE FROM jobs WHERE video_id=?', (video_id,))
                    c.execute('DELETE FROM videos WHERE id=?', (video_id,))
                    # Actual requests/charges remain counted: deleting a video must
                    # never reset daily quotas or already incurred API costs.
            except Exception as exc:
                try:
                    for original,destination in reversed(moves):
                        destination.rename(original)
                    if staging and staging.exists():
                        staging.rmdir()
                except OSError:
                    raise AppError('DELETE_RECOVERY', '删除未完成，部分文件恢复失败；请保留数据目录并检查本地文件。', 500) from None
                if isinstance(exc, AppError):
                    raise
                raise AppError('DELETE_FAILED', '未能删除录像，原记录已保留。请暂停播放后重试。', 409) from None
        cleanup_pending = False
        if staging:
            try:
                # staging was constructed and checked inside DATA_DIR/.trash.
                shutil.rmtree(staging)
            except OSError:
                cleanup_pending = True
        return {'deleted':True, 'video_id':video_id, 'cleanup_pending':cleanup_pending,
                'shared_files_retained':shared}
