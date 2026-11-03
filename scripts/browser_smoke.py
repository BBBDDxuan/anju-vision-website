"""Actual browser workflow against an explicitly mocked vision service. No paid API."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

import cv2
import httpx
import numpy as np
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    work = Path("D:/MemoryOSHomePrep/browser-tests") / uuid4().hex
    work.mkdir(parents=True)
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", 8000)) == 0:
            raise RuntimeError("Port 8000 is already in use; stop the local application before this isolated test.")
    source = work / "SYNTHETIC_BROWSER_TEST.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 10, (640, 360))
    assert writer.isOpened()
    for i in range(120):
        frame = np.full((360, 640, 3), (34, 59, 41), dtype=np.uint8)
        cv2.putText(frame, "SYNTHETIC TEST - NOT AI EVIDENCE", (25, 60), cv2.FONT_HERSHEY_SIMPLEX, .7, (230, 240, 210), 1)
        cv2.putText(frame, f"frame {i}  t={i/10:.1f}s", (25, 100), cv2.FONT_HERSHEY_SIMPLEX, .65, (200, 220, 180), 1)
        writer.write(frame)
    writer.release()
    env = dict(os.environ, SHIJI_BROWSER_TEST_DIR=str(work / "data"), PYTHONIOENCODING="utf-8")
    log = (work / "server.log").open("w", encoding="utf-8")
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "tests.browser_server:create_test_app", "--factory", "--host", "127.0.0.1", "--port", "8000"], cwd=ROOT, env=env, stdout=log, stderr=log)
    errors = []
    try:
        for _ in range(100):
            try:
                if httpx.get("http://127.0.0.1:8000/api/health", timeout=1).status_code == 200:
                    break
            except httpx.RequestError:
                pass
            time.sleep(.1)
        else:
            raise RuntimeError("Test server did not become ready")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto("http://127.0.0.1:8000/workspace")
            page.get_by_role("heading", name="找回线索，也看清证据的边界。").wait_for()
            page.get_by_text("测试模拟模式", exact=False).wait_for()
            page.get_by_label("上传 MP4").set_input_files(str(source))
            page.get_by_role("button", name="开始 AI 分析").wait_for(state="visible", timeout=30000)
            page.get_by_role("button", name="开始 AI 分析").click()
            page.get_by_text("4 条记录", exact=True).wait_for(timeout=30000)
            page.get_by_label("你想找回什么线索？").fill("我的钥匙现在在哪里？")
            page.get_by_role("button", name="提问", exact=True).click()
            page.get_by_role("heading", name="条件推断", exact=True).first.wait_for()
            assert page.locator('.claim.inference').count() == 2
            page.get_by_text("无法确认当前位置。", exact=False).wait_for()
            page.locator(".answer-result .reference").first.click()
            page.get_by_text("已定位至", exact=False).wait_for()
            assert page.locator(".evidence-view img").count() == 2
            page.wait_for_function("() => [...document.querySelectorAll('.evidence-view img')].length === 2 && [...document.querySelectorAll('.evidence-view img')].every(i => i.complete && i.naturalWidth > 0)")
            assert abs(page.locator("video").evaluate("v => v.currentTime") - 2) < .25
            page.screenshot(path=str(ROOT / "evidence/ui-mock-smoke.png"), full_page=True)
            page.reload()
            page.locator("#video-select").select_option(index=1)
            page.get_by_text("4 条记录", exact=True).wait_for()
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert errors == [], errors
            browser.close()
        result = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "browser": "Installed Chrome via Playwright",
                  "vision_mode": "EXPLICIT_TEST_MOCK_NOT_REAL_AI", "input": "synthetic generated MP4",
                  "passed": ["upload_and_transcode", "analyze_and_persist", "timeline_four_events", "question_with_evidence",
                             "conditional_inference_and_unknown", "seek_to_two_seconds", "keyframes_loaded", "reload_persistence", "mobile_no_horizontal_overflow"],
                  "browser_page_errors": errors, "paid_model_calls": 0}
        (ROOT / "evidence/browser-smoke.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


if __name__ == "__main__":
    main()
