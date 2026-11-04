"""Prepare private local phone credentials. Does not enable cloud calls or print secrets."""
from datetime import datetime
import json
from pathlib import Path
import secrets
import shutil

from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]


def configure():
    backup = ROOT / 'runtime' / ('phone-setup-backup-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    backup.mkdir(parents=True)
    for name in ('.env', 'elder-safety/.env', '.env.phone'):
        path = ROOT / name
        if path.exists():
            shutil.copy2(path, backup / name.replace('/', '-'))
    for name in ('.env', 'elder-safety/.env'):
        path = ROOT / name
        if not path.exists():
            shutil.copy2(ROOT / (name + '.example'), path)
    phone = ROOT / '.env.phone'
    token = dotenv_values(phone).get('APP_LAN_TOKEN') if phone.exists() else None
    if not token:
        token = secrets.token_urlsafe(32)
        set_key(str(phone), 'APP_LAN_TOKEN', token)
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError('Existing phone token is invalid; inspect .env.phone privately.')
    safety = ROOT / 'elder-safety/.env'
    values = dotenv_values(safety)
    user = values.get('SAFETY_USER_TOKEN')
    if not user:
        user = secrets.token_urlsafe(32)
        set_key(str(safety), 'SAFETY_USER_TOKEN', user)
    devices = values.get('SAFETY_DEVICE_TOKENS')
    if not devices:
        device_token = secrets.token_urlsafe(32)
        device_id = values.get('SAFETY_DEVICE_ID') or 'camera_001'
        set_key(str(safety), 'SAFETY_DEVICE_TOKENS', json.dumps({device_id: device_token}))
        set_key(str(safety), 'SAFETY_DEVICE_TOKEN', device_token)
    else:
        parsed = json.loads(devices)
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError('Existing safety device tokens need private review.')
    # Resolve only the relocated detector settings, preserving model endpoint,
    # API key, network flag, budgets, camera parameters and existing databases.
    set_key(str(ROOT / '.env'), 'SAFETY_PYTHON', 'elder-safety/.venv-gpu/Scripts/python.exe')
    set_key(str(ROOT / '.env'), 'SAFETY_MODEL_CONFIG', 'runtime/team-camera.json')
    print('Local phone credentials prepared; values were not printed. Backups are in runtime/.')


if __name__ == '__main__':
    configure()
