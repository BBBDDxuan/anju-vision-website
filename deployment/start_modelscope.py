"""Start the uploaded-video Studio without importing any private demo/database."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def configure_environment(environ, root=ROOT):
    defaults = {
        'CLOUD_MODE': 'true', 'DATA_DIR': '/mnt/workspace/anju-data',
        'MAX_CONCURRENCY': '1', 'MAX_MODEL_CALLS_PER_DAY': '100',
        'VLM_BILLING_MODE': 'request_limited', 'VLM_ENABLE_NETWORK': 'false',
        'FACE_RECOGNITION_ENABLED': 'false', 'HYBRID_SAFETY_ENABLED': 'true',
        'APP_ALLOWED_HOSTS': 'localhost,127.0.0.1,[::1],*.ms.show',
        'SAFETY_PYTHON': '/opt/detector/bin/python', 'SAFETY_DEVICE': 'cpu',
        'PORT': '7860',
    }
    for key, value in defaults.items():
        environ.setdefault(key, value)
    hosts = [h.strip() for h in environ['APP_ALLOWED_HOSTS'].split(',') if h.strip()]
    if '127.0.0.1' not in hosts:
        hosts.append('127.0.0.1')  # Container health check remains local.
    environ['APP_ALLOWED_HOSTS'] = ','.join(hosts)
    if environ['CLOUD_MODE'].lower() != 'true' or environ.get('APP_LAN_TOKEN'):
        raise ValueError('The Studio entrypoint requires CLOUD_MODE=true without APP_LAN_TOKEN.')
    if environ['MAX_CONCURRENCY'] != '1' or not 1 <= int(environ['MAX_MODEL_CALLS_PER_DAY']) <= 100:
        raise ValueError('Studio requires MAX_CONCURRENCY=1 and a daily request cap from 1 to 100.')
    data = Path(environ['DATA_DIR']).resolve()
    if data == root.resolve() or root.resolve().is_relative_to(data):
        raise ValueError('DATA_DIR must be a dedicated runtime directory, not a source parent.')
    if (data/'accounts.sqlite3').exists():
        raise ValueError('Do not use a local household account directory for a Studio.')
    environ['DATA_DIR'] = str(data)
    environ['SAFETY_MODEL_CONFIG'] = str(data/'runtime/modelscope-detector.json')
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Validate assets/configuration without serving or calling Qwen')
    args = parser.parse_args(argv)
    configure_environment(os.environ)
    from backend.config import Settings
    from backend.hybrid_safety import detector_environment

    settings = Settings.from_env()
    if not (ROOT/'frontend/dist/index.html').is_file():
        raise ValueError('Frontend build is missing. Run npm ci and npm run build in frontend first.')
    if not Path(settings.safety_python).is_file():
        raise ValueError('The separate detector Python is missing.')
    for tool in (settings.ffmpeg, settings.ffprobe):
        if not shutil.which(tool):
            raise ValueError('FFmpeg/FFprobe must be installed for Linux and configured in PATH or environment variables.')
    completed = subprocess.run([str(settings.safety_python), str(ROOT/'deployment/prepare_detector.py'),
        '--output', str(settings.safety_model_config), '--device', os.environ['SAFETY_DEVICE']],
        env=detector_environment(settings.safety_python), check=True, capture_output=True, text=True, encoding='utf8')
    result = json.loads(completed.stdout)
    # No model-service credentials, URLs, household data, or full environment logging.
    print(json.dumps({'cloud_mode': True, 'detector_device': result['device'],
        'assets_verified': result['assets_verified'], 'cloud_calls': 0,
        'network_enabled': settings.enable_network, 'max_daily_calls': settings.max_daily_calls,
        'port': int(os.environ['PORT'])}), flush=True)
    if not args.check:
        os.execv(sys.executable, [sys.executable, '-m', 'uvicorn', 'backend.main:create_app', '--factory',
            '--host', '0.0.0.0', '--port', os.environ['PORT'], '--workers', '1',
            '--no-access-log', '--no-proxy-headers'])


if __name__ == '__main__':
    main()
