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
    cfg.write_text(json.dumps({'models': {'pose_weights': '../weights/synthetic.pt',
        'flame_weights': '../weights/synthetic.pt', 'device': '0'},
        'recording': {'directory': '../../recordings', 'ffmpeg_path': '../tools/ffmpeg/ffmpeg.exe'}}))
    return asset


def test_prepare_needs_no_videos_and_keeps_portable_paths_in_checkout(tmp_path, monkeypatch):
    minimal_bundle(tmp_path, monkeypatch)
    path = prepare.prepare('cpu')
    cfg = json.loads(path.read_text(encoding='utf8'))
    assert cfg['models']['device'] == 'cpu'
    for value in [cfg['models']['pose_weights'], cfg['models']['flame_weights'],
                  cfg['recording']['directory'], cfg['recording']['ffmpeg_path']]:
        assert not Path(value).is_absolute()
        assert (path.parent / value).resolve().is_relative_to(tmp_path)
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


def test_detector_config_resolves_relative_paths_from_its_own_directory(tmp_path):
    import sys
    sys.path.insert(0, str(ROOT / 'elder-safety/ai-service'))
    from ai_service.config import Config
    config_dir = tmp_path / 'portable/runtime'
    config_dir.mkdir(parents=True)
    path = config_dir / 'camera.json'
    path.write_text(json.dumps({'models': {'pose_weights': '../weights/pose.pt'},
        'recording': {'directory': '../recordings', 'ffmpeg_path': '../tools/ffmpeg.exe'}}))
    config = Config.load(str(path))
    assert Path(config.models.pose_weights) == tmp_path / 'portable/weights/pose.pt'
    assert Path(config.recording.directory) == tmp_path / 'portable/recordings'
    assert Path(config.recording.ffmpeg_path) == tmp_path / 'portable/tools/ffmpeg.exe'


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


@pytest.mark.parametrize('available, expected', [(True, '0'), (False, 'cpu')])
def test_prepare_auto_uses_detector_cuda_availability(tmp_path, monkeypatch, available, expected):
    import sys
    from types import SimpleNamespace
    minimal_bundle(tmp_path, monkeypatch)
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: available)))
    output = prepare.prepare('auto')
    assert json.loads(output.read_text(encoding='utf8'))['models']['device'] == expected


def test_setup_defaults_to_cuda_and_pins_build_when_switching_from_cpu():
    setup = (ROOT / 'setup-team.ps1').read_text(encoding='utf8')
    assert "[string]$Device = 'cuda'" in setup
    assert "[string]$Device = 'cuda'" in (ROOT / 'setup.ps1').read_text(encoding='utf8')
    assert "{ 'cu126' } else { 'cpu' }" in setup
    assert 'torch==2.13.0+$build' in setup
    assert 'torchvision==0.28.0+$build' in setup
    assert 'Requested detector device is unavailable; configuration was not switched.' in setup
    diagnostic = (ROOT / 'scripts/doctor_team.py').read_text(encoding='utf8')
    assert 'models.infer(np.zeros((480,640,3),dtype=np.uint8))' in diagnostic
    assert 'next(model.model.parameters()).device.type == expected' in diagnostic
    startup = (ROOT / 'start.ps1').read_text(encoding='utf8')
    assert '& $configPython scripts/prepare_team.py --device auto' in startup
