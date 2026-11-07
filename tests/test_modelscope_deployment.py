"""Cloud deployment configuration tests; no model requests or real household data."""
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import sys

import pytest

from deployment.start_modelscope import configure_environment
from deployment.prepare_detector import prepare
from scripts.export_modelscope_source import allowed

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'elder-safety/ai-service'))
from ai_service.config import Config


def test_cloud_defaults_keep_health_local_and_network_off(tmp_path):
    env = {'DATA_DIR': str(tmp_path/'runtime'), 'APP_ALLOWED_HOSTS': 'my-studio.ms.show'}
    data = configure_environment(env)
    assert env['CLOUD_MODE'] == 'true'
    assert env['VLM_ENABLE_NETWORK'] == 'false'
    assert env['APP_ALLOWED_HOSTS'] == 'my-studio.ms.show,127.0.0.1'
    assert env['SAFETY_MODEL_CONFIG'] == str(data/'runtime/modelscope-detector.json')
    assert not data.exists()


@pytest.mark.parametrize('override', [
    {'CLOUD_MODE': 'false'}, {'APP_LAN_TOKEN': 'private-local-token'},
    {'MAX_CONCURRENCY': '2'}, {'MAX_MODEL_CALLS_PER_DAY': '101'},
    {'MAX_MODEL_CALLS_PER_DAY': '0'},
])
def test_cloud_entry_rejects_local_or_unbounded_settings(tmp_path, override):
    with pytest.raises(ValueError):
        configure_environment({'DATA_DIR': str(tmp_path/'data'), **override})


def test_cloud_entry_never_touches_household_database(tmp_path):
    marker = tmp_path/'accounts.sqlite3'
    marker.write_bytes(b'private synthetic account database')
    with pytest.raises(ValueError, match='household'):
        configure_environment({'DATA_DIR': str(tmp_path)})
    assert marker.read_bytes() == b'private synthetic account database'
    assert not (tmp_path/'runtime').exists()


def test_detector_preserves_rules_and_verified_weights(tmp_path):
    template = Config.load(str(ROOT/'elder-safety/ai-service/config.team.json'))
    output = tmp_path/'cloud/detector.json'
    assert prepare(ROOT, output) == {'device': 'cpu', 'assets_verified': True, 'cloud_calls': 0}
    cloud = Config.load(str(output))
    for name in ('fall', 'fire', 'help', 'tracking', 'evidence'):
        assert asdict(getattr(cloud, name)) == asdict(getattr(template, name))
    assert cloud.models.pose_weights == template.models.pose_weights
    assert cloud.models.flame_weights == template.models.flame_weights
    assert cloud.models.device == 'cpu'
    assert not cloud.recording.enabled
    assert cloud.recording.directory is None and cloud.recording.ffmpeg_path is None


def test_detector_refuses_changed_weight_before_generating_config(tmp_path):
    template_dir = tmp_path/'elder-safety/ai-service'
    weights = tmp_path/'elder-safety/weights'
    template_dir.mkdir(parents=True)
    weights.mkdir()
    shutil.copyfile(ROOT/'elder-safety/ai-service/config.team.json', template_dir/'config.team.json')
    shutil.copyfile(ROOT/'elder-safety/weights/manifest.json', weights/'manifest.json')
    for name in ('yolo11s-pose.pt', 'indoor-dfire-yolov8n.pt'):
        (weights/name).write_bytes(b'corrupt synthetic weight')
    output = tmp_path/'out/model.json'
    with pytest.raises(ValueError, match='hash'):
        prepare(tmp_path, output)
    assert not output.exists()


@pytest.mark.parametrize('name', ['.env', 'data/memory.sqlite3', 'runtime/team-camera.json',
    'recordings/private.mp4', 'frontend/public/.env', 'frontend/node_modules/library/index.js',
    'backend/__pycache__/config.pyc', 'elder-safety/tools/ffmpeg/ffmpeg.exe'])
def test_source_export_rejects_private_and_machine_local_paths(name):
    assert not allowed(name)


@pytest.mark.parametrize('name', ['Dockerfile', 'backend/main.py', 'backend/push_notifications/publisher.py',
    'frontend/public/demo/videos/help.mp4', 'frontend/src/App.tsx',
    'elder-safety/ai-service/ai_service/models.py', 'elder-safety/weights/yolo11s-pose.pt'])
def test_source_export_keeps_required_app_files(name):
    assert allowed(name)
