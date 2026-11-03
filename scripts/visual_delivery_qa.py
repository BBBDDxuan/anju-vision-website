"""Render the editable cover and decode the actual final video for export QA."""
import json
from pathlib import Path
import sys

import cv2
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome',headless=True)
        page = browser.new_page(viewport={'width':1600,'height':900},device_scale_factor=1)
        page.goto((ROOT/'design/cover.svg').as_uri())
        page.screenshot(path=str(ROOT/'design/cover.png'))
        browser.close()
    cap=cv2.VideoCapture(str(ROOT/'submission/demo/ShijiHome-Demo.mp4'))
    assert cap.isOpened()
    checks=[]
    for second in [0,8,24,46,59,69,77,82,102,108]:
        cap.set(cv2.CAP_PROP_POS_MSEC,second*1000)
        ok,frame=cap.read()
        assert ok,second
        encoded,buffer=cv2.imencode('.jpg',frame)
        assert encoded
        (ROOT/'evidence'/f'demo-qa-{second:03d}.jpg').write_bytes(buffer.tobytes())
        checks.append(second)
    cap.release()
    print(json.dumps({'decoded_video_seconds':checks,'cover':'1600x900 PNG via Chrome'},ensure_ascii=False))


if __name__=='__main__':main()
