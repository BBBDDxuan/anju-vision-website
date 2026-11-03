"""Preparation-only probe. No model calls, credentials, or camera access."""
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone


def main():
    names = ["DASHSCOPE_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
             "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY", "VLM_API_KEY"]
    packages = {}
    for name in ["fastapi", "uvicorn", "pydantic", "httpx", "opencv-python", "torch", "transformers"]:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    db = sqlite3.connect(":memory:")
    db.execute("create table probe (id integer primary key, payload text)")
    db.execute("insert into probe values (?, ?)", (1, '{"probe":true}'))
    sqlite_ok = db.execute("select payload from probe where id=1").fetchone()[0] == '{"probe":true}'
    db.close()
    result = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(), "python": sys.version.split()[0],
        "executable": sys.executable, "packages": packages,
        "process_credential_status": {n: bool(os.environ.get(n)) for n in names},
        "tools": {n: shutil.which(n) for n in ["node", "npm", "ffmpeg", "ffprobe", "git", "nvidia-smi"]},
        "sqlite_roundtrip": sqlite_ok, "model_calls": 0,
        "real_video_model_test": "NOT_RUN_NO_VIDEO_OR_CREDENTIALS",
    }
    if importlib.util.find_spec("fastapi") and importlib.util.find_spec("httpx"):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()

        @app.get("/probe")
        def probe():
            return {"status": "preparation_only", "model_connected": False}

        with TestClient(app) as client:
            response = client.get("/probe")
        result["fastapi_inprocess_http"] = {"status_code": response.status_code, "body": response.json()}
        assert response.status_code == 200 and not response.json()["model_connected"]
    if shutil.which("nvidia-smi"):
        p = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                            "--format=csv,noheader"], capture_output=True, text=True, timeout=15)
        result["gpu_hardware"] = p.stdout.strip() if p.returncode == 0 else "probe_failed"
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("evidence/environment.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
