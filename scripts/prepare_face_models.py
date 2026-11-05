#!/usr/bin/env python3
"""Explicit, dependency-free provisioning of pinned official OpenCV Zoo face models.

Importing this module does not download or write anything. Files are installed only
by prepare_models() / the CLI, after length and SHA-256 verification.
"""
import argparse
import hashlib
import os
from pathlib import Path
import tempfile
from urllib.request import urlopen

UPSTREAM_COMMIT = '48b10fa505aeb51fbd127fc60a5c660040e823b7'
BASE_URL = f'https://media.githubusercontent.com/media/opencv/opencv_zoo/{UPSTREAM_COMMIT}/models'
# Keep these hashes in sync with backend.face_recognition.MODEL_HASHES.
MODEL_SPECS = {
    'face_detection_yunet_2026may.onnx': {
        'url': f'{BASE_URL}/face_detection_yunet/face_detection_yunet_2026may.onnx',
        'bytes': 229738,
        'sha256': 'ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0',
    },
    'face_recognition_sface_2021dec.onnx': {
        'url': f'{BASE_URL}/face_recognition_sface/face_recognition_sface_2021dec.onnx',
        'bytes': 38696353,
        'sha256': '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79',
    },
}
DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[1] / 'models' / 'face-recognition'


def safe_path(target):
    for item in (target, *target.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError(f'Model path must not use symlinks or junctions: {item}')
    if target.exists() and (not target.is_file() or target.stat().st_nlink > 1):
        raise ValueError(f'Model must be an independent regular file: {target}')


def verified(target, spec):
    if not target.is_file() or target.stat().st_size != spec['bytes']:
        return False
    digest = hashlib.sha256()
    with target.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest() == spec['sha256']


def prepare_models(model_dir=DEFAULT_MODEL_DIR, *, check_only=False, timeout=60):
    model_dir = Path(model_dir).absolute()
    results = []
    for name, spec in MODEL_SPECS.items():
        target = model_dir / name
        safe_path(target)
        if verified(target, spec):
            results.append({'path': str(target), 'status': 'verified'})
            continue
        if check_only:
            raise ValueError(f'Model missing or integrity mismatch: {target}')
        model_dir.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            digest, count = hashlib.sha256(), 0
            with tempfile.NamedTemporaryFile(mode='wb', dir=model_dir, prefix=f'.{name}.', suffix='.part', delete=False) as output:
                temporary = Path(output.name)
                with urlopen(spec['url'], timeout=timeout) as source:
                    while chunk := source.read(1024 * 1024):
                        count += len(chunk)
                        if count > spec['bytes']:
                            raise ValueError(f'Download exceeds pinned size: {name}')
                        output.write(chunk)
                        digest.update(chunk)
                if count != spec['bytes'] or digest.hexdigest() != spec['sha256']:
                    raise ValueError(f'Download size or SHA-256 mismatch: {name}')
                output.flush()
                os.fsync(output.fileno())
            safe_path(target)
            os.replace(temporary, target)
            temporary = None
            results.append({'path': str(target), 'status': 'downloaded_and_verified'})
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument('--check', action='store_true', help='Verify local files only; never download.')
    parser.add_argument('--timeout', type=float, default=60, help='Network timeout in seconds (default: 60).')
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 300:
        parser.error('--timeout must be greater than 0 and at most 300 seconds')
    try:
        for result in prepare_models(args.model_dir, check_only=args.check, timeout=args.timeout):
            print(f"{result['status']}: {result['path']}")
    except (OSError, ValueError) as exc:
        parser.exit(1, f'Face model preparation failed: {exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
