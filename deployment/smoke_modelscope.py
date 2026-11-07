"""Inside the Linux CI container: synthetic upload, isolation, actual CPU detector, no Qwen."""
import http.cookiejar
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.error
import urllib.request

import cv2
import numpy as np

BASE = 'http://127.0.0.1:7860'
first = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
second = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def request(client, path, data=None):
    with client.open(urllib.request.Request(BASE+path, data=data, headers={
            'Content-Type': 'video/mp4', 'X-Video-Name': 'synthetic-ci.mp4'}), timeout=30) as response:
        return json.load(response)


health = request(first, '/api/health')
assert health['status'] == 'ok' and health['cloud_mode'] and not health['analysis_ready']
assert request(first, '/api/auth/status')['enabled'] is False
with first.open(BASE+'/') as response:
    assert b'<html' in response.read().lower()
with tempfile.TemporaryDirectory() as temporary:
    work = Path(temporary)
    video = work/'synthetic.mp4'
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 10, (320, 192))
    assert writer.isOpened()
    for _ in range(10):
        writer.write(np.zeros((192, 320, 3), dtype=np.uint8))
    writer.release()
    uploaded = request(first, '/api/videos/upload', video.read_bytes())
    vid = uploaded['video_id']
    assert request(first, f'/api/videos/{vid}')['id'] == vid
    assert request(second, '/api/videos')['videos'] == []
    try:
        request(second, f'/api/videos/{vid}')
    except urllib.error.HTTPError as error:
        assert error.code == 404
    else:
        raise AssertionError('Visitor isolation failed')
    output = work/'scan'
    subprocess.run([os.environ['SAFETY_PYTHON'], 'elder-safety/scripts/scan-safety-video.py',
        '--source', str(video), '--config', str(Path(os.environ['DATA_DIR'])/'runtime/modelscope-detector.json'),
        '--output', str(output), '--scan-fps', '10', '--flame-imgsz', '640'], check=True, timeout=180)
    report = json.loads((output/'scan.json').read_text(encoding='utf8'))
    assert report['completed'] and report['frames'] == 10 and report['candidate_count'] == 0
assert request(first, '/api/health')['usage']['requests'] == 0
print(json.dumps({'http': 'passed', 'visitor_isolation': 'passed', 'cpu_scan': 'passed',
                  'frames': 10, 'qwen_calls': 0}))
