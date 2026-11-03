"""Migrate user credentials without ever displaying values; audit repository exposure."""
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import subprocess

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def main():
    template, local = ROOT / ".env.example", ROOT / ".env"
    values = dotenv_values(template) if template.exists() else {}
    previous = dotenv_values(local) if local.exists() else {}
    sensitive = lambda k: bool(re.search(r"KEY|TOKEN|SECRET|PASSWORD", k, re.I)) and "TOKEN" not in k.upper().replace("MAX_TOKENS", "").replace("TOKENS", "")
    secret_names = [k for k in values if any(s in k.upper() for s in ["API_KEY", "SECRET", "PASSWORD", "ACCESS_TOKEN"])]
    secrets = [str(v) for k, v in {**previous, **values}.items() if k in secret_names and v and not str(v).startswith(("YOUR_", "<"))]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = Path("D:/MemoryOSHome/private-config/backups") / stamp
    backup.mkdir(parents=True, exist_ok=True)
    for source in [template, local]:
        if source.exists():
            shutil.copy2(source, backup / (source.name + ".private"))
    # User explicitly designated .env.example as their new effective configuration.
    merged = dict(previous)
    merged.update({k: v for k, v in values.items() if v is not None and (v != "" or k not in previous)})
    def render(data):
        return "# Local configuration; never commit this file.\n" + "\n".join(f"{k}={json.dumps(str(v), ensure_ascii=False)}" for k, v in data.items() if v is not None) + "\n"
    local.write_text(render(merged), encoding="utf-8")
    safe = {k: ("" if k in secret_names else v) for k, v in values.items()}
    template.write_text("# Public example only. Real credentials belong in .env.\n" + "\n".join(f"{k}={json.dumps(str(v), ensure_ascii=False)}" for k, v in safe.items() if v is not None) + "\n", encoding="utf-8")
    hits = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path == local or any(part in {"node_modules", ".git", "data", "video", ".venv"} for part in path.relative_to(ROOT).parts):
            continue
        if path.stat().st_size > 20_000_000:
            continue
        blob = path.read_bytes()
        if any(s.encode() in blob for s in secrets):
            hits.append(str(path.relative_to(ROOT)))
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True).stdout.split(b"\0")
    tracked_hits = []
    for raw in tracked:
        if raw:
            path = ROOT / raw.decode("utf-8")
            if path.is_file() and any(s.encode() in path.read_bytes() for s in secrets):
                tracked_hits.append(str(path.relative_to(ROOT)))
    revisions = subprocess.run(["git", "rev-list", "--all"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    historical_hits = []
    for revision in revisions:
        diff = subprocess.run(["git", "show", "--format=", revision], cwd=ROOT, capture_output=True).stdout
        if any(s.encode() in diff for s in secrets):
            historical_hits.append(revision[:12])
    report = {"checked_at": stamp, "private_backup_created": True, "template_credentials_cleared": True,
              "local_key_configured": bool(merged.get("VLM_API_KEY") or merged.get("DASHSCOPE_API_KEY")),
              "model": merged.get("VLM_MODEL"), "endpoint": merged.get("VLM_BASE_URL"),
              "configuration_names": sorted(merged),
              "network_enabled": str(merged.get("VLM_ENABLE_NETWORK", "false")).lower() == "true",
              "price_confirmation": str(merged.get("VLM_COST_CONFIRMED", "false")).lower() == "true",
              "positive_prices_configured": all(float(merged.get(k) or 0) > 0 for k in ["VLM_INPUT_RMB_PER_MILLION", "VLM_OUTPUT_RMB_PER_MILLION"]),
              "commits_checked": len(revisions), "working_tree_secret_hits": hits,
              "tracked_secret_hits": tracked_hits, "historical_secret_hits": historical_hits,
              "public_exposure": "NOT_ESTABLISHED; local scan does not prove absence from external copies"}
    (ROOT / "evidence/security-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
