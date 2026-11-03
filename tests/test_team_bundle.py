"""Team checkout preparation uses synthetic assets, never videos or APIs."""
import importlib.util
import hashlib
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('team_prepare', ROOT / 'scripts/prepare_team.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def minimal_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, 'ROOT', tmp_path)
    asset = tmp_path / 'elder-safety/weights/synthetic.pt'
    asset.parent.mkdir(parents=True)
    payload = b'synthetic-test-only-not-real-weights'
    asset.write_bytes(payload)
    manifest = {'assets': [{'path': 'elder-safety/weights/synthetic.pt',
        'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}]}
    (tmp_path / 'repro-assets.json').write_text(json.dumps(manifest))
    cfg = tmp_path / 'elder-safety/ai-service/config.team.json'
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({'models': {'pose_weights': 'elder-safety/weights/synthetic.pt',
        'flame_weights': 'elder-safety/weights/synthetic.pt', 'device': '0'},
        'recording': {'directory': 'recordings', 'ffmpeg_path': 'elder-safety/tools/ffmpeg/ffmpeg.exe'}}))
    return asset


def test_prepare_needs_no_videos_and_resolves_all_paths_in_checkout(tmp_path, monkeypatch):
    minimal_bundle(tmp_path, monkeypatch)
    path = prepare.prepare('cpu')
    cfg = json.loads(path.read_text(encoding='utf8'))
    assert cfg['models']['device'] == 'cpu'
    for value in [cfg['models']['pose_weights'], cfg['models']['flame_weights'],
                  cfg['recording']['directory'], cfg['recording']['ffmpeg_path']]:
        assert Path(value).is_absolute() and Path(value).is_relative_to(tmp_path)
    assert not (tmp_path / 'examples').exists()
    assert prepare.prepare('cpu') == path


def test_prepare_rejects_corrupted_model_before_creating_config(tmp_path, monkeypatch):
    asset = minimal_bundle(tmp_path, monkeypatch)
    asset.write_bytes(b'corrupted')
    with pytest.raises(ValueError, match='integrity mismatch'):
        prepare.prepare('cpu')
    assert not (tmp_path / 'runtime/team-camera.json').exists()


def test_prepare_regenerates_machine_local_config_without_overwriting_template(tmp_path, monkeypatch):
    minimal_bundle(tmp_path, monkeypatch)
    template = tmp_path / 'elder-safety/ai-service/config.team.json'
    before = template.read_bytes()
    config = prepare.prepare('cpu')
    config.write_text('wrong-old-machine-config')
    prepare.prepare('cpu')
    assert json.loads(config.read_text(encoding='utf8'))['models']['device'] == 'cpu'
    assert template.read_bytes() == before


def test_delivery_templates_keep_secrets_empty_and_network_disabled():
    from dotenv import dotenv_values
    import re
    for name in ('.env.example', 'elder-safety/.env.example'):
        values = dotenv_values(ROOT / name)
        for key, value in values.items():
            if re.search('KEY|(?:^|_)TOKEN(?:_|$)|DEVICE_TOKENS|SECRET|PASSWORD|RTSP|CREDENTIAL|FCM|FIREBASE', key, re.I):
                assert not value, f'{name}: {key} must be provided privately'
    values = dotenv_values(ROOT / '.env.example')
    assert values['VLM_ENABLE_NETWORK'] == 'false'
    assert int(values['VLM_OUTPUT_TOKENS_PER_WINDOW']) > 0
    assert int(values['MAX_OUTPUT_TOKENS_PER_VIDEO']) > 0


def test_delivery_excludes_real_configs_and_all_development_videos():
    import subprocess
    import shutil
    git = shutil.which('git')
    if not git or not (ROOT / '.git').exists():
        pytest.skip('source archive without Git metadata')
    paths = ['.env', 'elder-safety/.env',
             'examples/repro-video/example.mp4',
             'examples/repro-video/example.mp4.part01']
    run = subprocess.run([git, 'check-ignore', '--no-index', '--', *paths],
                         cwd=ROOT, capture_output=True, text=True, encoding='utf8')
    assert run.returncode == 0
    assert set(run.stdout.splitlines()) == set(paths)


def test_detector_setup_declares_legacy_checkpoint_dependency_and_loads_models():
    assert 'dill==0.4.1' in (ROOT / 'requirements-detector.txt').read_text(encoding='utf8')
    diagnostic = (ROOT / 'scripts/doctor_team.py').read_text(encoding='utf8')
    assert 'LocalModels(c.models)' in diagnostic
    assert "'flame_loaded':models.flame is not None" in diagnostic
