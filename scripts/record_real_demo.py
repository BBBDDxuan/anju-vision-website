"""Record actual Chrome UI and one limited real Qwen analysis, then verify evidence seeking."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/validation/demo-recording'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report_path = OUT / 'report.json'
    if report_path.exists():
        raise RuntimeError('Recording already exists; do not duplicate paid model analysis.')
    source = ROOT / 'data/validation/clips/cabinet-demo-016-034.mp4'
    if not source.exists():
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(ROOT / 'data/validation/clips/cabinet-notebook-016-052.mp4'),
                        '-t', '18', '-an', '-vcodec', 'libx264', '-bf', '0', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(source)], check=True, timeout=60)
    report = {'is_mock': False, 'source': source.name, 'source_mapping': {'original_start_ms': 16000, 'original_end_ms': 34000}, 'steps': [], 'page_errors': []}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, record_video_dir=str(OUT), record_video_size={'width': 1440, 'height': 1000})
        page = context.new_page()
        page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
        started = time.monotonic()
        def mark(name):
            report['steps'].append({'name': name, 'seconds': round(time.monotonic()-started, 2)})
            print(name, report['steps'][-1]['seconds'], flush=True)
        page.goto('http://127.0.0.1:8000/workspace')
        assert page.get_by_text('测试模拟模式', exact=False).count() == 0
        mark('真实程序首页')
        page.wait_for_timeout(3500)
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/videos')) as upload:
            page.get_by_label('上传 MP4').set_input_files(str(source))
        vid = upload.value.json()['video_id']
        report['video_id'] = vid
        page.get_by_role('button', name='开始 AI 分析', exact=True).wait_for(timeout=30000)
        page.locator('video').evaluate('v => v.play()')
        mark('上传真实视频并播放')
        page.wait_for_timeout(5000)
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/analyze')) as analysis:
            page.get_by_role('button', name='开始 AI 分析', exact=True).click()
        report['job_id'] = analysis.value.json()['id']
        mark('触发真实Qwen分窗分析')
        page.get_by_role('button', name='重新分析', exact=True).wait_for(timeout=240000)
        mark('真实分析完成')
        memory = httpx.get(f'http://127.0.0.1:8000/api/videos/{vid}/events').json()
        assert not memory['is_mock'] and memory['events'] and memory['job']['status'] == 'completed', memory.get('job')
        counts = {e['id']: sum(event['subject_id'] == e['id'] and event['action'] not in {'seen_at','unknown'} for event in memory['events']) for e in memory['entities']}
        entity = max(memory['entities'], key=lambda e: counts[e['id']])
        report['entity'] = entity
        report['events'] = len(memory['events'])
        report['job'] = memory['job']
        page.wait_for_timeout(4000)
        page.locator('.timeline').scroll_into_view_if_needed()
        page.wait_for_timeout(3500)
        page.get_by_label('你想找回什么线索？').fill(entity['label']+'最后在哪里出现？')
        page.get_by_role('button', name='提问', exact=True).click()
        page.locator('.answer-result .claim').first.wait_for()
        mark('基于真实事件的自然语言回答')
        page.wait_for_timeout(6000)
        page.locator('.answer-result .reference').first.click()
        page.get_by_text('已定位至', exact=False).wait_for()
        ev = page.locator('.answer-result .reference').first.inner_text()
        report['seek_time_seconds'] = page.locator('video').evaluate('v => v.currentTime')
        answer = httpx.post(f'http://127.0.0.1:8000/api/videos/{vid}/query', json={'question': entity['label']+'最后在哪里出现？'}).json()
        assert any(abs(report['seek_time_seconds'] - e['start_ms']/1000) < .25 for e in answer['evidence']), ev
        page.wait_for_function("() => [...document.querySelectorAll('.evidence-view img')].length > 0 && [...document.querySelectorAll('.evidence-view img')].every(i => i.complete && i.naturalWidth > 0)")
        mark('点击证据并核对播放器与关键帧')
        page.screenshot(path=str(ROOT / 'evidence/ui-real-answer.png'), full_page=True)
        page.wait_for_timeout(5000)
        page.get_by_role('button', name='LifeReplay', exact=True).click()
        page.locator('#entity-select').select_option(entity['id'])
        page.locator('.replay-entry').first.wait_for()
        mark('LifeReplay真实物品经历与缩略图')
        page.locator('.replay-view').scroll_into_view_if_needed()
        page.wait_for_timeout(6000)
        page.locator('.frame-link').first.click()
        page.get_by_text('已定位至', exact=False).wait_for()
        page.screenshot(path=str(ROOT / 'evidence/ui-real-replay.png'), full_page=True)
        page.get_by_label('你想找回什么线索？').fill('能确定这个物品现在还在那里吗？')
        page.get_by_role('button', name='提问', exact=True).click()
        page.get_by_text('无法确认当前位置。', exact=False).wait_for()
        mark('当前实际位置未知，明确说明边界')
        page.locator('.answer-result').scroll_into_view_if_needed()
        page.wait_for_timeout(5500)
        page.get_by_role('button', name='清除', exact=True).click()
        page.get_by_label('你想找回什么线索？').fill('我的紫色护照在哪里？')
        page.get_by_role('button', name='提问', exact=True).click()
        page.get_by_text('没有找到明确匹配的物品。', exact=False).wait_for()
        mark('不存在的物品不编造回答')
        page.wait_for_timeout(4000)
        page.get_by_label('你想找回什么线索？').fill(entity['label']+'的移动经过是什么？')
        page.get_by_role('button', name='提问', exact=True).click()
        page.locator('.answer-result .reference').first.wait_for()
        page.evaluate('window.scrollTo(0,0)')
        page.wait_for_timeout(max(1000, 109000-(time.monotonic()-started)*1000))
        mark('录屏结束')
        video = page.video
        awaitable_path = video.path()
        context.close()
        report['raw_video'] = str(awaitable_path)
        report['raw_duration_seconds'] = round(time.monotonic()-started, 3)
        # Persistence and mobile checks are performed after the main recording.
        verify = browser.new_page(viewport={'width': 390, 'height': 844})
        verify.goto('http://127.0.0.1:8000')
        verify.locator('#video-select').select_option(vid)
        verify.get_by_text(f"{len(memory['events'])} 条记录", exact=True).wait_for()
        assert verify.evaluate('document.documentElement.scrollWidth <= innerWidth')
        report['reload_persistence'] = 'PASS'
        report['mobile_no_overflow'] = 'PASS'
        assert report['page_errors'] == []
        browser.close()
    report['status'] = 'PASS'
    report['checked_at_utc'] = datetime.now(timezone.utc).isoformat()
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'status': report['status'], 'events': report['events'], 'seconds': report['raw_duration_seconds'], 'raw_video': report['raw_video']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
