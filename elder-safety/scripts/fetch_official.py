from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import httpx
URLS = [
 "https://www.alibabacloud.com/help/en/model-studio/vision",
 "https://firebase.google.com/docs/cloud-messaging/flutter/client",
 "https://firebase.google.com/docs/cloud-messaging/send/admin-sdk",
 "https://docs.ultralytics.com/modes/track/",
 "https://www.ultralytics.com/license",
]
out = Path(__file__).resolve().parents[1] / "runtime/official-sources"
out.mkdir(parents=True, exist_ok=True)
def read(item):
    i, url = item
    try:
        r = httpx.get(url, follow_redirects=True, timeout=30)
        print(r.status_code, url)
        if r.is_success:
            (out / f"{i}.html").write_text(r.text, encoding="utf-8")
        return {"url": url, "status": r.status_code}
    except httpx.HTTPError as exc:
        print(type(exc).__name__, url)
        return {"url": url, "error": type(exc).__name__}
with ThreadPoolExecutor(max_workers=5) as pool:
    result = list(pool.map(read, enumerate(URLS)))
(out / "index.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
