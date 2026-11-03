"""Download official build archives to G, verify hashes, and extract locally."""
from pathlib import Path
import concurrent.futures
import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import PurePosixPath
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1] / "tools" / "android-build"
DOWNLOADS = ROOT / "downloads"

def fetch(name, url, digest, algorithm, target, inner=None):
    archive = DOWNLOADS / name
    request = urllib.request.Request(url, headers={"Range": "bytes=0-0", "User-Agent": "ElderSafety-local-build/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        if response.status != 206:
            raise ValueError("Server does not support verified range downloads")
        total = int(response.headers["Content-Range"].rsplit("/", 1)[1])
    have = archive.stat().st_size if archive.exists() else 0
    if have > total:
        raise ValueError("Local archive larger than official download")
    parts = DOWNLOADS / (name + ".parts")
    # Resume an interrupted merge before requesting any missing network ranges.
    if have < total and parts.is_dir():
        pieces = sorted((int(p.stem), p) for p in parts.glob("*.part") if p.stem.isdigit())
        with archive.open("ab") as output:
            for first, part in pieces:
                length = part.stat().st_size
                if first <= have < first + length:
                    with part.open("rb") as source:
                        source.seek(have - first)
                        data = source.read()
                    output.write(data)
                    have += len(data)
        print("Resumed cached merge " + name + " at " + str(have), flush=True)
    if have < total:
        print("Downloading " + name + " from " + str(have) + " / " + str(total), flush=True)
        parts = DOWNLOADS / (name + ".parts")
        parts.mkdir(exist_ok=True)
        chunk_size = 8 * 1024 * 1024
        ranges = [(start, min(start + chunk_size, total) - 1) for start in range(have, total, chunk_size)]
        def chunk(bounds):
            first, last = bounds
            path = parts / (str(first) + ".part")
            if path.exists() and path.stat().st_size == last - first + 1:
                return path
            for attempt in range(4):
                try:
                    req = urllib.request.Request(url, headers={"Range": "bytes=" + str(first) + "-" + str(last), "User-Agent": "ElderSafety-local-build/1.0"})
                    with urllib.request.urlopen(req, timeout=90) as source:
                        expected = "bytes " + str(first) + "-" + str(last) + "/" + str(total)
                        if source.status != 206 or source.headers.get("Content-Range") != expected:
                            raise ValueError("Unexpected range response")
                        with path.open("wb") as output:
                            shutil.copyfileobj(source, output, 1024 * 1024)
                    if path.stat().st_size != last - first + 1:
                        raise ValueError("Truncated chunk")
                    return path
                except Exception:
                    if attempt == 3:
                        raise
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            chunk_files = list(pool.map(chunk, ranges))
        with archive.open("ab") as output:
            for part in chunk_files:
                with part.open("rb") as source:
                    shutil.copyfileobj(source, output, 1024 * 1024)
    with archive.open("rb") as stream:
        actual = hashlib.file_digest(stream, algorithm).hexdigest()
    if actual.lower() != digest.lower():
        raise ValueError("Archive hash mismatch: " + name)
    print("Verified " + name + " (" + str(archive.stat().st_size) + " bytes)", flush=True)
    if not target.exists():
        stage = ROOT / ("extract-native-" + target.name + "-" + uuid.uuid4().hex[:8])
        stage.mkdir()
        with zipfile.ZipFile(archive) as z:
            for item in z.infolist():
                name_parts = PurePosixPath(item.filename.replace("\\", "/"))
                if name_parts.is_absolute() or ".." in name_parts.parts or any(":" in bit for bit in name_parts.parts):
                    raise ValueError("Unsafe archive path")
                if (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("Archive symlinks are not supported")
        print("Extracting " + name + " with Windows tar", flush=True)
        subprocess.run(["tar.exe", "-m", "-xf", str(archive), "-C", str(stage)], check=True)
        source = stage / inner if inner else next(x for x in stage.iterdir() if x.is_dir())
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
    return {"archive": name, "url": url, "digest": digest, "algorithm": algorithm,
            "directory": str(target)}

def main():
    manifest = json.loads((ROOT.parents[1] / "mobile-app/tool/android-toolchain.json").read_text(encoding="utf-8"))
    flutter, jdk, android = (manifest[key] for key in ("flutter", "jdk", "android_commandline"))
    jobs = [
        (flutter["archive"], flutter["url"], flutter["sha256"], "sha256", ROOT / "flutter", "flutter"),
        (jdk["archive"], jdk["url"], jdk["sha256"], "sha256", ROOT / "jdk", None),
        (android["archive"], android["url"], android["sha1"], "sha1", ROOT / "sdk/cmdline-tools" / android.get("directory", "latest"), "cmdline-tools"),
    ]
    results = []
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        tasks = [pool.submit(fetch, *job) for job in jobs]
        for task in concurrent.futures.as_completed(tasks):
            try:
                result = task.result(); results.append(result)
                print("Installed " + result["directory"], flush=True)
            except Exception as exc:
                errors.append(type(exc).__name__ + ": " + str(exc))
                print(errors[-1], flush=True)
    (ROOT / "provisioning-report.json").write_text(json.dumps({"archives": results, "errors": errors}, indent=2), encoding="utf-8")
    if errors:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
