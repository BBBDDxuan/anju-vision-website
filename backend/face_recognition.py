"""Local YuNet/SFace member templates and conservative candidate identity association."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import struct
import threading

import cv2
import numpy as np

from .errors import AppError
from .member_profiles import MemberProfiles, bump_revision, display_name, initialize_profiles, validate_member_id

MODEL_VERSION = 'yunet-2026may+sface-2021dec'
MODEL_HASHES = {
    'face_detection_yunet_2026may.onnx': 'ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0',
    'face_recognition_sface_2021dec.onnx': '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79',
}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_MEMBERS = 32
MAX_SAMPLES = 10


def unknown(reason):
    return {'status': 'unknown', 'member_id': None, 'reason': reason, 'model': MODEL_VERSION}


def image_dimensions(data):
    """Bound JPEG/PNG dimensions before allowing the image decoder to allocate."""
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 24 and data[12:16] == b'IHDR':
        return struct.unpack('>II', data[16:24])
    if data.startswith(b'\xff\xd8'):
        offset = 2
        while offset + 3 < len(data):
            if data[offset] != 255:
                break
            while offset < len(data) and data[offset] == 255:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in {0xD9, 0xDA}:
                break
            if marker in {0x01, *range(0xD0, 0xD8)}:
                continue
            if offset + 2 > len(data):
                break
            size = int.from_bytes(data[offset:offset+2], 'big')
            if size < 2 or offset + size > len(data):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and size >= 7:
                height, width = struct.unpack('>HH', data[offset+3:offset+7])
                return width, height
            offset += size
    raise AppError('FACE_IMAGE_INVALID', '请提供可解码的 JPEG 或 PNG 图片。', 422)


def valid_box(box):
    return (isinstance(box, (list, tuple)) and len(box) == 4
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in box)
            and 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)


def contains_face(person, face):
    if not valid_box(person) or not valid_box(face):
        return False
    cx, cy = (face[0]+face[2])/2, (face[1]+face[3])/2
    area = (face[2]-face[0])*(face[3]-face[1])
    overlap = max(0, min(person[2], face[2])-max(person[0], face[0])) * max(0, min(person[3], face[3])-max(person[1], face[1]))
    return person[0] <= cx <= person[2] and person[1] <= cy <= person[3] and overlap >= .8*area


class FaceRecognition:
    def __init__(self, settings):
        self.settings = settings
        self.enabled = settings.face_recognition_enabled and not settings.cloud_mode
        self.path = Path(settings.data_dir).absolute() / 'face-identities.sqlite3'
        self.lock = threading.RLock()
        self.model_lock = threading.RLock()
        self.detector = self.recognizer = None
        self.model_version = MODEL_VERSION
        self.profiles = MemberProfiles(self)
        if self.enabled:
            with self.connect():
                pass

    def _storage_path(self):
        # Reject symlinks/junctions, including parent aliases, for the private DB and sidecars.
        for target in (self.path, *self.path.parents):
            if target.is_symlink() or target.is_junction():
                raise AppError('FACE_STORAGE_UNSAFE', '身份存储路径不能使用符号链接或目录联接。', 503)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        for suffix in ('', '-journal', '-wal', '-shm'):
            target = Path(str(self.path) + suffix)
            if target.is_symlink() or target.is_junction():
                raise AppError('FACE_STORAGE_UNSAFE', '身份存储文件不能使用符号链接。', 503)
            try:
                metadata = target.lstat()
            except FileNotFoundError:
                # Another SQLite connection may just have removed its rollback journal.
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink > 1:
                raise AppError('FACE_STORAGE_UNSAFE', '身份存储须使用独立的普通文件。', 503)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(self.path, flags, 0o600)
        try:
            if os.name != 'nt':
                os.fchmod(fd, 0o600)
        finally:
            os.close(fd)

    @contextmanager
    def connect(self):
        with self.lock:
            self._storage_path()
            c = sqlite3.connect(self.path, timeout=15)
            c.row_factory = sqlite3.Row
            try:
                c.execute('PRAGMA foreign_keys=ON')
                c.execute('PRAGMA secure_delete=ON')
                # Serialize read/check/write across instances and processes, not only threads.
                c.execute('BEGIN IMMEDIATE')
                c.execute('''CREATE TABLE IF NOT EXISTS members (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, consent_at TEXT NOT NULL)''')
                c.execute('''CREATE TABLE IF NOT EXISTS samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
                    model TEXT NOT NULL, embedding BLOB NOT NULL, created_at TEXT NOT NULL)''')
                c.execute('''CREATE TABLE IF NOT EXISTS associations (
                    candidate_id TEXT PRIMARY KEY,
                    member_id TEXT REFERENCES members(id) ON DELETE SET NULL,
                    payload TEXT NOT NULL)''')
                initialize_profiles(c)
                yield c
                c.commit()
            except BaseException:
                c.rollback()
                raise
            finally:
                c.close()

    def require_enabled(self):
        if not self.enabled:
            raise AppError('FACE_RECOGNITION_DISABLED', '本地成员识别未启用，云部署不开放人脸接口。', 403)

    def ready(self):
        self.require_enabled()
        with self.model_lock:
            if self.detector is not None:
                return
            buffers = []
            for filename, expected in MODEL_HASHES.items():
                try:
                    data = (Path(self.settings.face_model_dir) / filename).read_bytes()
                except OSError:
                    raise AppError('FACE_MODEL_MISSING', '人脸模型缺失，请检查模型目录。', 503) from None
                if hashlib.sha256(data).hexdigest() != expected:
                    raise AppError('FACE_MODEL_INTEGRITY', '人脸模型完整性校验失败。', 503)
                buffers.append(np.frombuffer(data, np.uint8))
            try:
                empty = np.empty(0, np.uint8)
                detector = cv2.FaceDetectorYN.create('onnx', buffers[0], empty, (320, 320), .9, .3, 5000)
                recognizer = cv2.FaceRecognizerSF.create('onnx', buffers[1], empty)
            except cv2.error:
                raise AppError('FACE_MODEL_RUNTIME', '当前 OpenCV 环境无法加载人脸模型。', 503) from None
            self.detector, self.recognizer = detector, recognizer

    def status(self):
        result = {'enabled': self.enabled, 'ready': False, 'model': MODEL_VERSION,
                  'threshold': self.settings.face_match_threshold, 'margin': self.settings.face_match_margin,
                  'min_face_pixels': self.settings.face_min_pixels, 'local_only': True,
                  'stores_source_photos': False, 'max_image_bytes': MAX_IMAGE_BYTES,
                  'max_image_pixels': MAX_IMAGE_PIXELS, 'max_members': MAX_MEMBERS,
                  'max_samples_per_member': MAX_SAMPLES, 'max_bindings_per_member': 32,
                  'profiles_available': not self.settings.cloud_mode}
        if not self.settings.cloud_mode:
            result['member_count'] = len(self.profiles.members())
        if self.enabled:
            try:
                self.ready()
                result['ready'] = True
            except AppError as exc:
                result['error'] = exc.code
        return result

    def members(self):
        with self.connect() as c:
            rows = c.execute('''SELECT m.id, m.name, m.consent_at, COUNT(s.id) AS sample_count
                FROM members m LEFT JOIN samples s ON s.member_id=m.id AND s.model=?
                GROUP BY m.id ORDER BY m.id''', (MODEL_VERSION,)).fetchall()
            return [dict(row) for row in rows]

    def gallery(self):
        self.require_enabled()
        with self.connect() as c:
            return self._gallery(c)

    def _gallery(self, c):
        rows = c.execute('SELECT member_id,embedding FROM samples WHERE model=? ORDER BY id', (MODEL_VERSION,)).fetchall()
        return [(r['member_id'], np.frombuffer(r['embedding'], dtype='<f4').copy()) for r in rows]

    def _decode(self, data):
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise AppError('FACE_IMAGE_TOO_LARGE', '人脸图片不能为空且不得超过 5 MiB。', 413)
        width, height = image_dimensions(data)
        if min(width, height) < 1 or width * height > MAX_IMAGE_PIXELS:
            raise AppError('FACE_IMAGE_DIMENSIONS', '图片像素数不得超过 1600 万。', 422)
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
        if image is None or image.shape[:2] != (height, width):
            raise AppError('FACE_IMAGE_INVALID', '无法解码图片，请先将照片方向校正后保存。', 422)
        scale = min(1.0, self.settings.face_max_side / max(width, height))
        if scale < 1:
            image = cv2.resize(image, (max(1, round(width*scale)), max(1, round(height*scale))), interpolation=cv2.INTER_AREA)
        return image

    def _features(self, data):
        image = self._decode(data)
        self.ready()
        with self.model_lock:
            try:
                height, width = image.shape[:2]
                self.detector.setInputSize((width, height))
                _, detected = self.detector.detect(image)
                if (detected is None or len(detected) == 0) and max(width, height) > 640:
                    # Large close-up faces can exceed YuNet's useful detection scale.
                    # Retry a bounded smaller image only when the first pass has no faces.
                    scale = 640 / max(width, height)
                    image = cv2.resize(image, (max(1, round(width*scale)), max(1, round(height*scale))),
                                       interpolation=cv2.INTER_AREA)
                    height, width = image.shape[:2]
                    self.detector.setInputSize((width, height))
                    _, detected = self.detector.detect(image)
                results = []
                if detected is not None and len(detected) > MAX_MEMBERS:
                    raise AppError('FACE_TOO_MANY_FACES', '单张图片人脸数量超过处理上限。', 422)
                for face in detected if detected is not None else []:
                    x, y, w, h = [float(v) for v in face[:4]]
                    bbox = [max(0., x/width), max(0., y/height), min(1., (x+w)/width), min(1., (y+h)/height)]
                    row = {'bbox': bbox, 'detection_score': float(face[14]), 'feature': None}
                    if min(w, h) < self.settings.face_min_pixels or not valid_box(bbox):
                        row['quality'] = 'face_too_small'
                    else:
                        aligned = self.recognizer.alignCrop(image, face)
                        feature = self.recognizer.feature(aligned).reshape(-1).copy().astype('<f4')
                        norm = float(np.linalg.norm(feature))
                        if feature.size != 128 or not np.isfinite(feature).all() or norm <= 0:
                            raise AppError('FACE_FEATURE_INVALID', '人脸特征无效。', 503)
                        row.update(feature=feature / norm, quality='usable')
                    results.append(row)
            except cv2.error:
                raise AppError('FACE_INFERENCE_FAILED', '人脸推理失败。', 503) from None
        return results

    def enroll(self, member_id, name, consent, data):
        self.require_enabled()
        if consent is not True:
            raise AppError('FACE_CONSENT_REQUIRED', '录入前须取得被录入者对本地身份识别的单独同意。', 422)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', member_id) or not name.strip() or len(name.strip()) > 80:
            raise AppError('FACE_MEMBER_INVALID', '成员编号或姓名无效。', 422)
        # Check the lifecycle again after expensive inference; deletion must not resurrect a profile.
        with self.connect() as c:
            version = c.execute('SELECT revision FROM household_versions WHERE id=?', (member_id,)).fetchone()
            expected = version['revision'] if version else 0
        faces = self._features(data)
        if len(faces) != 1 or faces[0]['feature'] is None:
            raise AppError('FACE_ENROLLMENT_QUALITY', '录入照片必须且只能包含一张足够清晰、大小合适的人脸。', 422)
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as c:
            version = c.execute('SELECT revision FROM household_versions WHERE id=?', (member_id,)).fetchone()
            if (version['revision'] if version else 0) != expected:
                raise AppError('HOUSEHOLD_REVISION_CONFLICT', '档案在录入期间已更改，请刷新后重试。', 409)
            profile = c.execute('SELECT * FROM household_members WHERE id=?', (member_id,)).fetchone()
            if not profile:
                if c.execute('SELECT COUNT(*) FROM household_members').fetchone()[0] >= MAX_MEMBERS:
                    raise AppError('FACE_MEMBER_LIMIT', '家庭成员数量已达 32 人上限。', 409)
                number = c.execute('SELECT COALESCE(MAX(number),0)+1 FROM household_members').fetchone()[0]
                c.execute("INSERT INTO household_members VALUES(?,?,?,'','','[]',0)", (member_id, number, name.strip()))
                profile = c.execute('SELECT * FROM household_members WHERE id=?', (member_id,)).fetchone()
            if c.execute('SELECT COUNT(*) FROM samples WHERE member_id=? AND model=?', (member_id, MODEL_VERSION)).fetchone()[0] >= MAX_SAMPLES:
                raise AppError('FACE_SAMPLE_LIMIT', '每位成员最多保留 10 个样本，请先清除模板再重新录入。', 409)
            c.execute('INSERT INTO members VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,consent_at=excluded.consent_at',
                      (member_id, display_name(profile), now))
            c.execute('INSERT INTO samples(member_id,model,embedding,created_at) VALUES(?,?,?,?)',
                      (member_id, MODEL_VERSION, faces[0]['feature'].astype('<f4').tobytes(), now))
            bump_revision(c, member_id)
            row = c.execute('''SELECT m.*,COUNT(s.id) AS sample_count FROM members m
                LEFT JOIN samples s ON s.member_id=m.id AND s.model=? WHERE m.id=? GROUP BY m.id''',
                (MODEL_VERSION, member_id)).fetchone()
            return dict(row)

    def delete(self, member_id):
        """Legacy DELETE clears face templates only; household deletion is explicit."""
        validate_member_id(member_id)
        with self.connect() as c:
            count = c.execute('SELECT COUNT(*) FROM samples WHERE member_id=?', (member_id,)).fetchone()[0]
            deleted = c.execute('DELETE FROM members WHERE id=?', (member_id,)).rowcount
            # A clear request also cancels first enrollment already in flight.
            if deleted or c.execute('SELECT 1 FROM household_members WHERE id=?', (member_id,)).fetchone():
                bump_revision(c, member_id)
            return {'member_id': member_id, 'deleted': bool(deleted), 'removed_sample_count': count}

    def _match(self, feature, gallery):
        if feature is None:
            return unknown('face_too_small')
        if not gallery:
            return unknown('no_enrolled_members')
        scores = {}
        for member_id, reference in gallery:
            score = float(np.clip(np.dot(feature, reference), -1, 1))
            scores[member_id] = max(scores.get(member_id, -1.), score)
        ranking = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        member_id, score = ranking[0]
        margin = score - ranking[1][1] if len(ranking) > 1 else None
        reason = ('below_threshold' if score < self.settings.face_match_threshold else
                  'ambiguous_match' if margin is not None and margin < self.settings.face_match_margin else None)
        if reason:
            return {**unknown(reason), 'similarity': score}
        return {'status': 'matched', 'member_id': member_id, 'similarity': score,
                'margin': margin, 'model': MODEL_VERSION, 'reason': 'cosine_match'}

    def recognize(self, data):
        self.require_enabled()
        faces = self._features(data)
        # Match against the current gallery AFTER inference, under one DB transaction.
        with self.connect() as c:
            gallery = self._gallery(c)
            results = [{**self._match(f['feature'], gallery), 'bbox': f['bbox'],
                        'detection_score': f['detection_score'], 'quality': f['quality']} for f in faces]
            for result in results:
                if result['member_id'] is not None:
                    profile = c.execute('SELECT * FROM household_members WHERE id=?', (result['member_id'],)).fetchone()
                    if profile is None:
                        result.update(unknown('member_deleted'))
                    else:
                        result.update(name=display_name(profile), revision=profile['revision'])
        return {'faces': results, 'face_count': len(results), 'model': MODEL_VERSION,
                'threshold': self.settings.face_match_threshold, 'similarity_is_probability': False}

    def associate_candidate(self, candidate):
        """Separate from danger validation; any failure leaves the alarm eligible."""
        if not self.enabled:
            return unknown('disabled')
        try:
            identity = self._candidate_identity(candidate)
            with self.connect() as c:
                # The FK and current membership check also prevent stale post-delete matches.
                member = identity['member_id']
                if member:
                    active = c.execute('''SELECT p.* FROM household_members p JOIN members m ON m.id=p.id
                        WHERE p.id=?''', (member,)).fetchone()
                    if active is None or ('revision' in identity and identity['revision'] != active['revision']):
                        identity = unknown('member_deleted')
                        member = None
                    else:
                        identity.update(name=display_name(active), revision=active['revision'])
                payload = {k: v for k, v in identity.items() if k != 'member_id'}
                c.execute('INSERT INTO associations VALUES(?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET member_id=excluded.member_id,payload=excluded.payload',
                          (candidate['id'], member, json.dumps(payload, allow_nan=False)))
            return identity
        except Exception:
            return unknown('identity_unavailable')

    def _candidate_identity(self, candidate):
        if candidate.get('event_type') not in {'suspected_fall', 'help_requested'}:
            return unknown('not_person_event')
        if not self.gallery():
            return unknown('no_enrolled_members')
        matches, seen_pts = [], set()
        track_id = candidate.get('track_id')
        for frame in candidate.get('frames', [])[:12]:
            people = frame.get('person_bboxes')
            if (not isinstance(people, dict) or track_id not in people or frame.get('pts_ms') in seen_pts
                    or not all(valid_box(box) for box in people.values())):
                continue
            seen_pts.add(frame['pts_ms'])
            path = Path(frame['path'])
            if path.stat().st_size > MAX_IMAGE_BYTES:
                continue
            recognized = self.recognize(path.read_bytes())['faces']
            possible = [f for f in recognized if contains_face(people[track_id], f['bbox'])]
            if len(possible) != 1:
                continue
            face = possible[0]
            if any(other != track_id and contains_face(box, face['bbox']) for other, box in people.items()):
                continue
            if face['status'] == 'matched':
                matches.append((frame['id'], face))
        members = {face['member_id'] for _, face in matches}
        if len(members) > 1 or len({f.get('revision') for _, f in matches}) > 1:
            return unknown('conflicting_frames')
        if len(matches) < 2:
            return unknown('insufficient_tracked_face_evidence')
        return {'status': 'matched', 'member_id': matches[0][1]['member_id'],
                'similarity': min(face['similarity'] for _, face in matches), 'model': MODEL_VERSION,
                'reason': 'consistent_tracked_faces', 'supporting_frame_ids': [fid for fid, _ in matches],
                **{key: matches[0][1][key] for key in ('name', 'revision') if key in matches[0][1]}}

    def event_identity(self, candidate_ids):
        try:
            return self._event_identity(candidate_ids)
        except Exception:
            return unknown('identity_unavailable')

    def _event_identity(self, candidate_ids):
        if not self.enabled:
            return unknown('disabled')
        ids = list(dict.fromkeys(candidate_ids))
        if not ids:
            return unknown('identity_not_analyzed')
        results = []
        with self.connect() as c:
            for identifier in ids:
                row = c.execute('SELECT a.member_id,a.payload,m.id AS active,p.name,p.number,p.revision FROM associations a LEFT JOIN members m ON m.id=a.member_id LEFT JOIN household_members p ON p.id=m.id WHERE candidate_id=?', (identifier,)).fetchone()
                if row is None:
                    return unknown('identity_not_analyzed')
                value = json.loads(row['payload'])
                if value.get('status') != 'matched':
                    return {**value, 'member_id': None}
                if row['active'] is None:
                    return unknown('member_deleted')
                results.append({**value, 'member_id': row['member_id'],
                                'name': display_name(row), 'revision': row['revision']})
        if len({r['member_id'] for r in results}) != 1:
            return unknown('conflicting_candidates')
        return {**results[0], 'similarity': min(r['similarity'] for r in results)}
