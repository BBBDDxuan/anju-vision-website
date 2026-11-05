"""Private, phone-only APK catalog. No runtime directory is mounted as static files."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Literal

from fastapi import APIRouter, Query, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from .errors import AppError

PACKAGE_NAME = 'com.eldersafety.elder_safety_mobile'
ABIS = ('arm64-v8a', 'armeabi-v7a', 'x86_64')
MAX_VERSION_CODE = 2**63 - 1
MAX_APK_BYTES = 256 * 1024**2
MAX_NOTES_CHARS = 10000
DIGEST = re.compile(r'[0-9a-f]{64}\Z')
MAX_MANIFEST_BYTES = 1024 * 1024


class InvalidUpdate(ValueError):
    pass


def private_path(path: Path) -> Path:
    """Reject symlinks AND Windows junction/reparse points, including ancestors."""
    path = Path(os.path.abspath(path))
    for part in (*reversed(path.parents), path):
        info = part.lstat()  # Missing is an error; never resolve through a link.
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise InvalidUpdate('Update paths must not contain links or reparse points')
    return path


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def open_private(path: Path):
    path = private_path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise InvalidUpdate('Expected a regular update file')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    stream = os.fdopen(fd, 'rb')
    try:
        # Windows fstat may report a different ctime sentinel than path.lstat.
        # Compare handle identity/size/mtime, and compare path ctime only to path ctime.
        if (fingerprint(before)[:4] != fingerprint(os.fstat(stream.fileno()))[:4]
                or fingerprint(before) != fingerprint(path.lstat())):
            raise InvalidUpdate('Update file changed while opening')
        private_path(path)
        return stream
    except BaseException:
        stream.close()
        raise


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidUpdate('Duplicate manifest key')
        result[key] = value
    return result


def validate_manifest(document):
    if (not isinstance(document, dict) or set(document) != {'schema_version', 'certificate_sha256', 'updates'}
            or type(document['schema_version']) is not int or document['schema_version'] != 1
            or not isinstance(document['certificate_sha256'], str)
            or not DIGEST.fullmatch(document['certificate_sha256'])
            or not isinstance(document['updates'], list) or not 1 <= len(document['updates']) <= 4096):
        raise InvalidUpdate('Invalid update manifest')
    keys = {'version_name', 'version_code', 'abi', 'package_name', 'size_bytes', 'sha256', 'download_path', 'notes'}
    seen = set()
    files = {}
    for item in document['updates']:
        if not isinstance(item, dict) or set(item) != keys:
            raise InvalidUpdate('Invalid update entry')
        if (not isinstance(item['version_name'], str) or not 1 <= len(item['version_name']) <= 100
                or type(item['version_code']) is not int or not 1 <= item['version_code'] <= MAX_VERSION_CODE
                or item['abi'] not in ABIS or item['package_name'] != PACKAGE_NAME
                or type(item['size_bytes']) is not int or not 1 <= item['size_bytes'] <= MAX_APK_BYTES
                or not isinstance(item['sha256'], str) or not DIGEST.fullmatch(item['sha256'])
                or item['download_path'] != '/api/app/apk/' + item['sha256']
                or not isinstance(item['notes'], str) or len(item['notes']) > MAX_NOTES_CHARS):
            raise InvalidUpdate('Invalid update metadata')
        identity = (item['abi'], item['version_code'])
        if identity in seen:
            raise InvalidUpdate('Duplicate ABI/version')
        seen.add(identity)
        # Universal APKs can have one catalog entry per supported ABI.
        metadata = tuple(item[key] for key in ('size_bytes', 'version_code', 'version_name', 'package_name'))
        if item['sha256'] in files and files[item['sha256']] != metadata:
            raise InvalidUpdate('Conflicting content metadata')
        files[item['sha256']] = metadata
    return document


def read_manifest(root: Path):
    """Only an absent manifest means no publication. Corruption fails closed."""
    try:
        stream = open_private(root / 'manifest.json')
    except FileNotFoundError:
        return None
    with stream:
        content = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(content) > MAX_MANIFEST_BYTES:
        raise InvalidUpdate('Manifest too large')
    try:
        return validate_manifest(json.loads(content, object_pairs_hook=_unique_object))
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        raise InvalidUpdate('Invalid update manifest') from exc


def verified_apk(root: Path, item):
    """Hash each request; no stale stat/hash cache. Return the SAME open file."""
    digest = item['sha256']
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise InvalidUpdate('Invalid APK digest')
    stream = open_private(root / (digest + '.apk'))
    try:
        before = os.fstat(stream.fileno())
        if before.st_size != item['size_bytes']:
            raise InvalidUpdate('APK size mismatch')
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        if sha != digest or fingerprint(before) != fingerprint(os.fstat(stream.fileno())):
            raise InvalidUpdate('APK digest mismatch or changed file')
        stream.seek(0)
        return stream
    except BaseException:
        stream.close()
        raise


def app_updates_router(root: Path):
    router = APIRouter()

    def catalog():
        try:
            return read_manifest(root)
        except (OSError, InvalidUpdate):
            raise AppError('APP_UPDATE_UNAVAILABLE', '本机更新目录不可用，请联系维护者。', 503)

    @router.get('/api/app/version')
    def version(response: Response, version_code: int = Query(ge=0, le=MAX_VERSION_CODE),
                abi: Literal['arm64-v8a', 'armeabi-v7a', 'x86_64'] = Query()):
        response.headers['Cache-Control'] = 'private, no-store'
        document = catalog()
        choices = [item for item in document['updates'] if item['abi'] == abi] if document else []
        latest = max(choices, key=lambda item: item['version_code'], default=None)
        if latest is None or latest['version_code'] <= version_code:
            return {'available': False, 'update': None}
        try:
            with verified_apk(root, latest):
                pass
        except (OSError, InvalidUpdate):
            raise AppError('APP_UPDATE_UNAVAILABLE', '本机安装包不可用，请联系维护者。', 503)
        return {'available': True, 'update': latest}

    @router.get('/api/app/apk/{sha256}')
    def download(sha256: str):
        if not DIGEST.fullmatch(sha256):
            raise AppError('APP_APK_NOT_FOUND', '未找到安装包。', 404)
        document = catalog()
        item = next((item for item in document['updates'] if item['sha256'] == sha256), None) if document else None
        if item is None:
            raise AppError('APP_APK_NOT_FOUND', '未找到安装包。', 404)
        try:
            stream = verified_apk(root, item)
        except (OSError, InvalidUpdate):
            raise AppError('APP_APK_NOT_FOUND', '安装包缺失或校验失败。', 404)

        def chunks():
            try:
                while block := stream.read(1024 * 1024):
                    yield block
            finally:
                stream.close()

        return StreamingResponse(chunks(), media_type='application/vnd.android.package-archive', headers={
            'Content-Length': str(item['size_bytes']),
            'Content-Disposition': f'attachment; filename="{sha256}.apk"',
            'Cache-Control': 'private, no-store',
            'X-Content-Type-Options': 'nosniff',
        }, background=BackgroundTask(stream.close))

    return router
