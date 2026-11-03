"""Import a hash-verified public recording, then start the single-worker service."""
import os
import sqlite3
from contextlib import closing
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import Settings
from scripts.import_demo import import_all_recordings


def main():
    settings = Settings.from_env()
    if not settings.cloud_mode:
        raise RuntimeError('Cloud entrypoint requires CLOUD_MODE=true.')
    if settings.max_daily_calls > 100 or settings.max_concurrency != 1:
        raise RuntimeError('Cloud demo requires a daily cap <=100 and MAX_CONCURRENCY=1.')
    database = settings.data_dir/'memory.sqlite3'
    backup = settings.data_dir/'memory.before-delete-mode.sqlite3'
    if database.exists() and not backup.exists():
        # Use SQLite's consistent backup API, never remove WAL/SHM files.
        with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(backup)) as target:
            source.backup(target)
        print('Created private database backup before cloud journal migration.', flush=True)
    print(import_all_recordings(ROOT, settings), flush=True)
    os.execv(sys.executable, [sys.executable, '-m', 'uvicorn', 'backend.main:create_app',
                            '--factory', '--host', '0.0.0.0', '--port', os.getenv('PORT', '7860'),
                            '--workers', '1', '--no-access-log', '--no-proxy-headers'])


if __name__ == '__main__':
    main()
