"""Build a source-only teammate handoff; never include local credentials or recordings."""
from __future__ import annotations
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP_DIRS = {"backend", "ai-service", "mobile-app", "docs", "scripts", "deploy"}
TOP_FILES = {"README.md", ".env.example", ".gitignore", ".dockerignore", "pyproject.toml"}
EXCLUDED = {"recordings", "tools", ".venv", ".venv-gpu", "runtime", "var", "weights", "__pycache__", ".pytest_cache", ".dart_tool", "build", "node_modules", ".git", "deliverables", ".gradle"}
EXTENSIONS = {".py", ".toml", ".md", ".txt", ".ps1", ".yaml", ".yml", ".dart", ".json", ".lock", ".sh", ".mjs", ".js"}
PRIVATE_NAMES = {"google-services.json", "GoogleService-Info.plist", "firebase_options.dart", "local.properties", "key.properties"}
SECRET_PATTERN = re.compile(r"(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|sk-[A-Za-z0-9_-]{24,}|AKIA[A-Z0-9]{16})")


def main():
    out = ROOT / "deliverables"
    out.mkdir(exist_ok=True)
    name = "elder-safety-source-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    target = out / (name + ".zip")
    files = []
    from dotenv import dotenv_values
    local = dotenv_values(ROOT / ".env") if (ROOT / ".env").is_file() else {}
    protected = [local.get(key) for key in ("SAFETY_USER_TOKEN", "SAFETY_DEVICE_TOKEN", "DASHSCOPE_API_KEY", "RTSP_URL")]
    try:
        protected.extend(json.loads(local.get("SAFETY_DEVICE_TOKENS") or "{}").values())
    except (ValueError, AttributeError):
        raise RuntimeError("Local device token configuration is invalid; package aborted") from None
    protected = [value for value in protected if isinstance(value, str) and len(value) >= 12 and "REPLACE_" not in value]
    candidates = []
    for directory, dirs, names in os.walk(ROOT):
        current = Path(directory)
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and (current != ROOT or d in TOP_DIRS) and not (current / d).is_symlink())
        candidates.extend(current / name for name in names)
    for path in sorted(candidates):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDED for part in relative.parts) or path.name in PRIVATE_NAMES:
            continue
        if len(relative.parts) == 1:
            if path.name not in TOP_FILES:
                continue
        elif relative.parts[0] not in TOP_DIRS:
            continue
        if path.name.startswith(".env") and path.name != ".env.example":
            continue
        if ".local." in path.name or (path.name.startswith("config") and path.suffix == ".json" and path.name != "config.example.json"):
            continue
        if not path.name.endswith("Dockerfile") and path.suffix not in EXTENSIONS and path.name not in {".gitignore", ".dockerignore", ".env.example"}:
            continue
        if any(word in path.name.lower() for word in ("service-account", "credential", "secret", "private")):
            continue
        if path.stat().st_size > 2 * 1024 * 1024:
            raise RuntimeError("Unexpected large source file: " + relative.as_posix())
        data = path.read_bytes()
        text = data.decode("utf-8", errors="replace")
        if SECRET_PATTERN.search(text) or any(value in text for value in protected):
            raise RuntimeError("Potential credential detected; package aborted: " + relative.as_posix())
        files.append((relative.as_posix(), data))
    # This project only contains small source files: source enumeration never includes ancestor archives.
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, data in files:
            archive.writestr("elder-safety/" + relative, data)
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(files)
        assert all("/.env" not in name or name.endswith("/.env.example") for name in archive.namelist())
        assert all(not any(part in EXCLUDED for part in Path(name).parts) for name in archive.namelist())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(".zip.sha256").write_text(digest + "  " + target.name + "\n", encoding="utf-8")
    manifest = {"archive": target.name, "sha256": digest, "source_file_count": len(files), "archive_bytes": target.stat().st_size,
                "excluded": sorted(EXCLUDED | PRIVATE_NAMES), "files": [{"path": relative, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for relative, data in files]}
    target.with_suffix(".manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k not in {"files", "excluded"}}, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
