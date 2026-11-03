"""Read-only runtime diagnostics. Never prints credentials or contacts cloud/camera."""
from pathlib import Path
import argparse
import importlib.metadata
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-only', action='store_true')
    args = parser.parse_args()
    from backend.config import Settings
    settings = Settings.from_env()
    checks = {'team_env_exists': (ROOT / '.env').is_file(),
              'camera_env_exists': (ROOT / 'elder-safety/.env').is_file(),
              'cloud_config_ready_or_disabled': bool(settings.api_key) or not settings.enable_network,
              'frontend_dist_exists': (ROOT / 'frontend/dist/index.html').is_file(),
              'detector_config_exists': settings.safety_model_config.is_file()}
    versions = {'backend_python': sys.version.split()[0]}
    for package in ('fastapi', 'uvicorn', 'numpy', 'opencv-python-headless'):
        versions[package] = importlib.metadata.version(package)
    for label, path in [('ffmpeg', settings.ffmpeg), ('ffprobe', settings.ffprobe)]:
        run = subprocess.run([path, '-version'], cwd=ROOT, capture_output=True, timeout=20)
        checks[label + '_works'] = run.returncode == 0
        if run.returncode == 0:
            versions[label] = run.stdout.decode('utf8', errors='replace').splitlines()[0]
    if not args.backend_only:
        checks['detector_python_exists'] = settings.safety_python.is_file()
        if settings.safety_python.is_file():
            from backend.hybrid_safety import detector_environment
            code = '''import sys,json,torch,importlib.metadata as m
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'elder-safety/ai-service'))
from ai_service.config import Config
from ai_service.models import local_weight, LocalModels
c=Config.load(str(Path.cwd()/'runtime/team-camera.json'))
for k in ('pose_weights','flame_weights'): local_weight(getattr(c.models,k),k)
models = LocalModels(c.models)
assert c.models.device=='cpu' or torch.cuda.is_available(),'CUDA unavailable; rerun setup-team.ps1 -Device cpu'
print(json.dumps({'python':sys.version.split()[0],'torch':torch.__version__,'cuda_available':torch.cuda.is_available(),'ultralytics':m.version('ultralytics'),'device':c.models.device,'pose_loaded':models.pose is not None,'flame_loaded':models.flame is not None}))'''
            run = subprocess.run([str(settings.safety_python), '-c', code], cwd=ROOT,
                    env=detector_environment(settings.safety_python), capture_output=True, timeout=60)
            checks['detector_import_and_weights'] = run.returncode == 0
            if run.returncode == 0:
                versions['detector'] = json.loads(run.stdout.decode('utf8').strip().splitlines()[-1])
    # Exercise the real backend and prebuilt front-end with networking disabled.
    os.environ['VLM_ENABLE_NETWORK'] = 'false'
    from fastapi.testclient import TestClient
    from backend.main import create_app
    with TestClient(create_app(), base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as client:
        health = client.get('/api/health')
        page = client.get('/')
        camera = client.get('/api/camera/status')
        checks['backend_health_200'] = health.status_code == 200
        checks['frontend_page_200'] = page.status_code == 200
        checks['camera_status_200_without_starting_camera'] = camera.status_code == 200
    report = {'checks': checks, 'versions': versions,
              'cloud_calls': 0, 'camera_connections': 0,
              'cloud_configured': bool(settings.api_key),
              'cloud_enabled': settings.enable_network, 'passed': all(checks.values())}
    (ROOT / 'runtime').mkdir(exist_ok=True)
    (ROOT / 'runtime/team-doctor.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
