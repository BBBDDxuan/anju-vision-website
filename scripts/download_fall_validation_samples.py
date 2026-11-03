"""Download exactly nine additional public CAUCAFall clips, never an archive.

No uploads or model calls. Original AVI files, checksums and attribution remain
alongside browser-compatible MP4 derivatives for user-controlled evaluation.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import csv
import hashlib
import json
from pathlib import Path
import subprocess

import cv2

from backend.config import ROOT, Settings
from backend.media import inspect_video

SOURCE = 'https://data.mendeley.com/datasets/7w7fccy7ky/5'
OUT = ROOT / 'video/public_samples/CAUCAFall_validation_9'
CACHE = ROOT / 'data/dataset_acquisition_20261020'
SELECTION = [(2,'Fall backwards','backward','向后跌倒'), (3,'Fall left','left','向左跌倒'),
             (4,'Fall right','right','向右跌倒'), (5,'Fall sitting','sitting','坐姿跌倒'),
             (6,'Fall forward','forward','向前跌倒'), (7,'Fall backwards','backward','向后跌倒'),
             (8,'Fall left','left','向左跌倒'), (9,'Fall right','right','向右跌倒'),
             (10,'Fall sitting','sitting','坐姿跌倒')]


def sha(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def fetch(url, target):
    result = subprocess.run(['curl.exe','--silent','--show-error','--fail','--location',
                             '--retry','2','--max-time','180','--max-filesize','104857600',
                             '--output',str(target),url],capture_output=True,timeout=560)
    if result.returncode:
        raise RuntimeError(f'Download failed: {target.name}: {result.stderr.decode(errors="replace")[:200]}')


def decode(path):
    cap = cv2.VideoCapture(str(path))
    assert cap.isOpened(), f'Cannot open {path.name}'
    fps, count = cap.get(cv2.CAP_PROP_FPS), 0
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    while True:
        ok, _ = cap.read()
        if not ok:
            break
        count += 1
    cap.release()
    assert fps > 0 and count > 0
    return {'fps':fps,'frames':count,'duration_seconds':count/fps,'width':width,'height':height,
            'bytes':path.stat().st_size,'sha256':sha(path),'full_decode_ok':True}


def acquire(selection):
    number, action, slug, chinese = selection
    folders = json.loads((CACHE/'caucafall-folders.json').read_text(encoding='utf8'))
    subject = next(f for f in folders if f['name'] == f'Subject.{number}')
    folder = next(f for f in folders if f.get('parent_id') == subject['id'] and f['name'] == action)
    listing = CACHE/f'validation-S{number}-{slug}-files.json'
    if not listing.exists():
        fetch(f'https://data.mendeley.com/public-api/datasets/7w7fccy7ky/files?folder_id={folder["id"]}&version=5&$start=0&$limit=1000',listing)
    files = json.loads(listing.read_text(encoding='utf8'))
    clips = [f for f in files if f['filename'].lower().endswith('.avi')]
    assert len(clips) == 1, f'Expected one AVI for Subject.{number}/{action}'
    item = clips[0]
    details = item['content_details']
    original = OUT/'originals'/item['filename']
    if not original.exists():
        partial = original.with_suffix('.avi.part')
        fetch(details['download_url'],partial)
        assert sha(partial) == details['sha256_hash'], f'Provider checksum mismatch: {original.name}'
        partial.rename(original)
    assert sha(original) == details['sha256_hash']
    target = OUT/f'{number-1:02d}_S{number:02d}_simulated_fall_{slug}.mp4'
    if not target.exists():
        partial = target.with_suffix('.partial.mp4')
        settings = Settings.from_env()
        result = subprocess.run([settings.ffmpeg,'-v','error','-i',str(original),'-an','-vcodec','libx264',
                                 '-pix_fmt','yuv420p','-bf','0','-vsync','0','-movflags','+faststart',str(partial)],
                                capture_output=True,timeout=120)
        assert result.returncode == 0, f'Transcode failed: {original.name}: {result.stderr[-300:]}'
        partial.rename(target)
    src, mp4 = decode(original), decode(target)
    assert src['frames'] == mp4['frames'] and (src['width'],src['height']) == (mp4['width'],mp4['height'])
    assert abs(src['duration_seconds']-mp4['duration_seconds']) < .06
    validated = inspect_video(target,Settings.from_env())
    assert abs(validated['duration_ms']/1000-src['duration_seconds']) < .06
    result = {'subject':number,'action':action,'action_zh':chinese,'filename':target.name,
              'original_filename':item['filename'],'download_url':details['download_url'],
              'provider_sha256':details['sha256_hash'],'original':src,'mp4':mp4,
              'is_simulated_fall':True,'elderly_actor_verified':False,'model_tested':False}
    (OUT/f'{target.stem}.provenance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'downloaded':target.name,'seconds':mp4['duration_seconds'],
                      'checksum_verified':True,'decode_verified':True},ensure_ascii=False),flush=True)
    return result


def main():
    (OUT/'originals').mkdir(parents=True,exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        clips = sorted(pool.map(acquire,SELECTION),key=lambda c:c['subject'])
    assert len(clips) == len(list(OUT.glob('*.mp4'))) == 9
    assert len({c['original']['sha256'] for c in clips}) == 9
    manifest = {'dataset':'CAUCAFall','version':5,'source':SOURCE,'license':'CC BY 4.0',
                'authors':['Jose Camilo Eraso','Elena Muñoz','Mariela Muñoz','Jesus Pinto'],
                'acquired_at_utc':datetime.now(timezone.utc).isoformat(),'additional_clips':9,
                'new_model_calls':0,'original_download_bytes':sum(c['original']['bytes'] for c in clips),
                'mp4_bytes':sum(c['mp4']['bytes'] for c in clips),'clips':clips}
    (OUT/'MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    table = '\n'.join(f"| {c['filename']} | Subject.{c['subject']} | {c['action_zh']} | {c['mp4']['duration_seconds']:.2f} |" for c in clips)
    text = f'''# 新增 9 段模拟跌倒验证视频

根目录 9 个 MP4 可直接上传到安居视界。来自 9 位不同参与者（Subject.2–Subject.10），覆盖前、后、左、右、坐姿跌倒。此前已下载的 Subject.1 向前跌倒保持在原目录，未重复下载。

均为研究参与者模拟跌倒，不是自然发生的老人跌倒；没有确认参与者为老年人。动作类别来自发布方目录，不代表逐帧起止时间已人工核定。本次没有上传到系统或调用 Qwen，识别效果由你逐段验证。

| MP4 文件 | 参与者 | 发布方动作类别 | 时长（秒） |
| --- | --- | --- | --- |
{table}

原片位于 `originals/`。已验证 9 个原片的 SHA256 与发布方一致；原片及 MP4 均完整解码，转换前后帧数、分辨率和时长一致，MP4 通过本项目的视频格式校验。转换移除了音轨。

来源：[CAUCAFall v5]({SOURCE})，DOI: 10.17632/7w7fccy7ky.5。许可：CC BY 4.0。作者：Jose Camilo Eraso、Elena Muñoz、Mariela Muñoz、Jesus Pinto。展示或分享时保留作者、来源和许可说明，并标明 MP4 为转换版本。没有下载整个数据集、图片帧或逐帧标注。

`MANIFEST.json` 和每段 `.provenance.json` 记录原始地址、SHA256、转换结果。`REVIEW_TEMPLATE.csv` 供你记录验证结果。

## 验证步骤

1. 打开 http://127.0.0.1:8000/workspace ，逐段导入根目录 MP4，点击开始 AI 分析。
2. 先自己完整看一次，记录实际跌倒区间；再检查模型是否检出、区间是否准确、是否重复报警。独立动作如跌倒前行走、跌倒后躺卧可以分别保留。
3. 点击模型事件证据回看片段；重复组可展开查看两个窗口的来源。
4. 在 CSV 中填写每段的人工观察、模型结果与误报/漏检。文件名说明动作类型，尚未填写的格子不代表测试结果。

这 9 段都是跌倒正样本，适合检查检出与重复；评估误报率还需要正常行走、坐下、弯腰等未跌倒片段作为负样本。请记录失败结果，避免只选择成功片段。
'''
    (OUT/'README.md').write_text(text,encoding='utf8')
    template = OUT/'REVIEW_TEMPLATE.csv'
    if not template.exists():
        with template.open('w',encoding='utf-8-sig',newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['文件名','发布方动作类别','人工观察跌倒起点秒','人工观察跌倒终点秒',
                             '模型是否检出','模型跌倒事件数量','模型区间是否合理','重复报警','误报或漏检说明','其他备注'])
            for clip in clips:
                writer.writerow([clip['filename'],clip['action_zh'],*['']*8])
    print(json.dumps({'additional_clips':9,'total_seconds':sum(c['mp4']['duration_seconds'] for c in clips),
                      'mp4_bytes':manifest['mp4_bytes'],'new_model_calls':0,'folder':str(OUT)},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
