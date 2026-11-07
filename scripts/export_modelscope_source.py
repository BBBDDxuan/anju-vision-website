"""Export committed, allowlisted Studio sources; no local database or credentials."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXACT = {
    'Dockerfile', '.dockerignore', 'requirements.txt', 'requirements-detector.txt',
    'deployment/start_modelscope.py', 'deployment/prepare_detector.py',
    'deployment/requirements-torch.txt', 'deployment/modelscope.env.example',
    'elder-safety/ai-service/config.team.json', 'elder-safety/scripts/scan-safety-video.py',
    'elder-safety/weights/manifest.json', 'elder-safety/weights/yolo11s-pose.pt',
    'elder-safety/weights/indoor-dfire-yolov8n.pt', 'THIRD_PARTY_NOTICES.md',
    'frontend/package.json', 'frontend/package-lock.json', 'frontend/tsconfig.json',
    'frontend/vite.config.ts', 'frontend/index.html',
}


def allowed(name):
    path = Path(name)
    if any(part.startswith('.env') or part in {'.git', '__pycache__', 'node_modules'} for part in path.parts):
        return False
    if name in EXACT:
        return True
    return (name.startswith(('backend/', 'elder-safety/ai-service/ai_service/')) and path.suffix == '.py'
            or name.startswith(('frontend/src/', 'frontend/public/', 'schemas/', 'prompts/', 'licenses/')))


def export(output: Path):
    output = output.resolve()
    if output.exists() or output.with_suffix('.zip').exists() or output.is_relative_to(ROOT):
        raise ValueError('Choose a new output directory outside the checkout; existing files are preserved.')
    git = lambda *args: subprocess.check_output(['git', *args], cwd=ROOT).decode('utf8').strip()
    commit = git('rev-parse', 'HEAD')
    names = git('ls-files', '-z').strip('\x00').split('\x00')
    selected = sorted(name for name in names if allowed(name))
    if not EXACT.issubset(selected):
        raise ValueError('Required deployment files must be committed before export.')
    if git('status', '--porcelain', '--untracked-files=no'):
        raise ValueError('Commit or save tracked changes before exporting an identifiable source version.')
    output.mkdir(parents=True)
    hashes = {}
    for name in selected:
        source = (ROOT/name).resolve()
        if not source.is_relative_to(ROOT) or not source.is_file():
            raise ValueError('Source file is missing or escapes the checkout.')
        destination = output/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        hashes[name] = hashlib.sha256(destination.read_bytes()).hexdigest()
    shutil.copyfile(ROOT/'docs/MODELSCOPE_DEPLOY.md', output/'README.md')
    (output/'source-manifest.json').write_text(json.dumps({'git_commit': commit, 'sha256': hashes},
        ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    archive = output.with_suffix('.zip')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
        for file in sorted(output.rglob('*')):
            if file.is_file():
                bundle.write(file, file.relative_to(output).as_posix())
    return {'directory': str(output), 'archive': str(archive), 'git_commit': commit,
            'files': len(hashes), 'bytes': archive.stat().st_size}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(export(parser.parse_args().output), ensure_ascii=False))
