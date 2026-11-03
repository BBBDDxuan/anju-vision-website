"""Seed the official wrapper cache on G with a verified Gradle distribution."""
from pathlib import Path
import hashlib
import importlib.util
import json
import shutil
ROOT = Path(__file__).resolve().parents[1] / "tools/android-build"
spec = importlib.util.spec_from_file_location("android_tools", Path(__file__).with_name("prepare-android-tools.py"))
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)
metadata = json.loads((ROOT / "gradle-distribution.json").read_text())
url = metadata["url"]
value = int.from_bytes(hashlib.md5(url.encode()).digest(), "big")
alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
encoded = ""
while value:
    value, digit = divmod(value, 36)
    encoded = alphabet[digit] + encoded
name = url.rsplit("/", 1)[1]
version = name.removesuffix("-bin.zip")
cache = ROOT / "cache/gradle/wrapper/dists" / name.removesuffix(".zip") / encoded
cache.mkdir(parents=True, exist_ok=True)
result = tools.fetch(name, url, metadata["sha256"], "sha256", cache / version, version)
archive = tools.DOWNLOADS / name
shutil.copyfile(archive, cache / name)
# Gradle's own wrapper performs extraction/checks and creates its .ok marker.
(ROOT / "gradle-cache-report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print("Gradle wrapper cache seeded: " + str(cache), flush=True)
