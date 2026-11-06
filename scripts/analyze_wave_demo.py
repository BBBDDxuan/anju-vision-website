"""Analyze every source frame with existing local pose weights; no paid calls.

Pose estimates are model output. Wave decisions are temporal heuristics, never
a trained wave classifier or evidence of help-seeking intent.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile
import shutil

from wave_motion import RULE, analyze_motion, arm_sample

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'elder-safety/ai-service'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def snapshot(run):
    if (run / 'before.json').exists():
        return
    protected = ['.env', 'elder-safety/.env', 'runtime/team-camera.json', '.venv/pyvenv.cfg',
                 '.venv/Scripts/python.exe', 'elder-safety/.venv-gpu/pyvenv.cfg',
                 'elder-safety/.venv-gpu/Scripts/python.exe']
    paths = [p for folder in ['frontend/src', 'frontend/public/demo'] for p in (ROOT / folder).rglob('*') if p.is_file()]
    paths += [ROOT / name for name in ['frontend/package.json', 'frontend/package-lock.json']]
    packages = sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions())
    write(run / 'before.json', {'protected': {name: sha(ROOT / name) for name in protected},
          'files': {str(p.relative_to(ROOT)).replace('\\', '/'): sha(p) for p in paths},
          'detectorPackages': packages})
    with zipfile.ZipFile(run / 'before-source.zip', 'w', zipfile.ZIP_DEFLATED) as backup:
        for p in paths:
            backup.write(p, str(p.relative_to(ROOT)))


def infer(video, run):
    # Keep package runtime settings inside the isolated check directory.
    os.environ['YOLO_CONFIG_DIR'] = str(run / 'ultralytics-runtime')
    os.environ['YOLO_AUTOINSTALL'] = 'false'
    import cv2
    import torch
    from ai_service.config import Config
    from ai_service.models import LocalModels
    from ai_service.tracking import Tracker

    cfg = Config.load(str(ROOT / 'runtime/team-camera.json'))
    device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
    models = LocalModels(replace(cfg.models, flame_weights=None, device=device,
                                 pose_fall_recovery_enabled=False))
    tracker = Tracker(cfg.tracking)
    probe = json.loads(subprocess.check_output([str(ROOT / 'elder-safety/tools/ffmpeg/ffprobe.exe'),
        '-v', 'error', '-show_frames', '-show_streams', '-show_format', '-of', 'json', str(video)], text=True, encoding='utf-8'))
    stream = next(s for s in probe['streams'] if s['codec_type'] == 'video')
    video_frames = [f for f in probe['frames'] if f['media_type'] == 'video']
    pts = [round(float(f['best_effort_timestamp_time']) * 1000, 3) for f in video_frames]
    width, height = stream['width'], stream['height']
    cap = cv2.VideoCapture(str(video))
    frames = []
    contact = []
    try:
        for index, timestamp in enumerate(pts):
            ok, image = cap.read()
            if not ok:
                raise ValueError('Source decoder ended before all frame timestamps')
            poses = models.infer_pose(image)
            poses = [p for p in poses if p.confidence >= .55]
            ids = tracker.update([p.bbox for p in poses], timestamp / 1000)
            people = [{'id': ids[i], 'bbox': list(p.bbox), 'confidence': p.confidence,
                       'keypoints': p.keypoints.tolist()} for i, p in enumerate(poses)]
            frames.append({'frameIndex': index, 'timeMs': timestamp,
                'validUntilMs': pts[index + 1] if index + 1 < len(pts) else float(stream['duration']) * 1000,
                'people': people})
            if index % 10 == 0 or index == len(pts) - 1:
                display = image.copy()
                for person in people:
                    x1, y1, x2, y2 = map(round, person['bbox'])
                    cv2.rectangle(display, (x1, y1), (x2, y2), (150, 220, 170), 2)
                    points = person['keypoints']
                    for a, b in [(0, 5), (0, 6), (5, 6), (5, 7), (7, 9), (6, 8), (8, 10)]:
                        if min(points[a][2], points[b][2]) >= .55:
                            cv2.line(display, tuple(map(round, points[a][:2])), tuple(map(round, points[b][:2])), (150, 220, 170), 2)
                    for joint in [0, 5, 6, 7, 8, 9, 10]:
                        x, y, confidence = points[joint]
                        if confidence >= .55:
                            cv2.circle(display, (round(x), round(y)), 5, (0, 255, 240) if joint in [9, 10] else (150, 220, 170), -1)
                cv2.putText(display, f'{index} / {timestamp / 1000:.3f}s | {len(people)} people', (16, 35), cv2.FONT_HERSHEY_SIMPLEX, .65, (0, 255, 255), 2)
                contact.append(cv2.resize(display, (225, 400)))
            if index % 30 == 0:
                print(f'Pose inference: {index + 1}/{len(pts)}', flush=True)
        assert not cap.read()[0], 'Unexpected additional source frame'
    finally:
        cap.release()
    import numpy as np
    while len(contact) % 4:
        contact.append(np.zeros_like(contact[0]))
    sheet = np.concatenate([np.concatenate(contact[i:i + 4], axis=1) for i in range(0, len(contact), 4)], axis=0)
    cv2.imwrite(str(run / 'pose-contact.jpg'), sheet)
    raw = {'schemaVersion': 1, 'source': {'file': video.name, 'sha256': sha(video), 'width': width,
        'height': height, 'durationMs': round(float(probe['format']['duration']) * 1000, 3),
        'videoDurationMs': round(float(stream['duration']) * 1000, 3), 'nominalFrameRate': stream['r_frame_rate'],
        'averageFrameRate': stream['avg_frame_rate'], 'codec': stream['codec_name'], 'frames': len(frames),
        'rotation': stream.get('tags', {}).get('rotate', 0)},
        'model': {'name': Path(cfg.models.pose_weights).name, 'sha256': sha(cfg.models.pose_weights),
            'device': device, 'gpu': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            'ultralytics': importlib.metadata.version('ultralytics'), 'torch': torch.__version__,
            'poseMaxWidth': cfg.models.pose_max_width, 'imgsz': 640, 'keypointFormat': 'COCO17 x,y,score in source pixels',
            'rotatedPoseRecovery': False, 'trainedWaveClassifier': False},
        'exportedAt': datetime.now(timezone.utc).isoformat(), 'newCloudCalls': 0,
        'timestampBasis': 'decoded frame best_effort_timestamp_time',
        'tracker': 'existing ai_service.tracking.Tracker; one-to-one IoU/center association', 'frames': frames}
    write(run / 'pose-recording.json', raw)
    print(json.dumps({'frames': len(frames), 'peoplePerFrame': sorted(set(len(f['people']) for f in frames)),
        'trackIds': sorted({p['id'] for f in frames for p in f['people']}), 'source': raw['source'], 'model': raw['model']}, ensure_ascii=False), flush=True)
    return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, default=ROOT / 'data/run/wave-overlay-check-20261106')
    args = parser.parse_args()
    run = args.run_dir.resolve()
    run.mkdir(parents=True, exist_ok=True)
    snapshot(run)
    raw_path = run / 'pose-recording.json'
    if raw_path.exists():
        raw = json.loads(raw_path.read_text(encoding='utf8'))
        assert raw['source']['sha256'] == sha(args.video), 'Cached inference belongs to a different video'
    else:
        raw = infer(args.video, run)
    publish(args.video, raw, run)


def publish(video, raw, run):
    events = analyze_motion(raw['frames'])
    print(json.dumps({'events': events}, ensure_ascii=False), flush=True)
    width, height = raw['source']['width'], raw['source']['height']
    frames = []
    for f in raw['frames']:
        people = []
        for p in f['people']:
            x1, y1, x2, y2 = p['bbox']
            person = {'id': p['id'], 'box': {'x': x1 / width, 'y': y1 / height,
                'width': (x2 - x1) / width, 'height': (y2 - y1) / height},
                'confidence': p['confidence'], 'boxSource': 'offline_model', 'keypointSource': 'offline_model',
                'keypoints': [{'id': i, 'x': x / width, 'y': y / height, 'confidence': score}
                              for i, (x, y, score) in enumerate(p['keypoints'])]}
            event = next((e for e in events if e['trackId'] == p['id'] and e['startMs'] <= f['timeMs'] < e['endMs']), None)
            if event:
                wrists = []
                for interval in event['sideIntervals']:
                    if not interval['startMs'] <= f['timeMs'] < interval['endMs']:
                        continue
                    history = []
                    for old in raw['frames']:
                        if not max(interval['startMs'], f['timeMs'] - RULE['trailWindowMs']) <= old['timeMs'] <= f['timeMs']:
                            continue
                        old_person = next((p for p in old['people'] if p['id'] == person['id']), None)
                        sample = arm_sample(old_person, interval['side']) if old_person else None
                        if sample is None or (history and old['timeMs'] - history[-1]['timeMs'] > RULE['maximumGapMs']):
                            history = []
                        if sample:
                            x, y, score = sample['wrist']
                            history.append({'timeMs': old['timeMs'], 'x': x / width, 'y': y / height})
                    wrists.append({'side': interval['side'], 'keypointId': 9 if interval['side'] == 'left' else 10,
                                   'trajectory': history})
                if wrists:
                    person['wave'] = {'eventId': event['id'], 'startMs': event['startMs'],
                        'endMs': event['endMs'], 'wrists': wrists}
            people.append(person)
        active = any(p.get('wave') for p in people)
        frames.append({**{k: f[k] for k in ['frameIndex', 'timeMs', 'validUntilMs']}, 'people': people,
                       'state': 'waving' if active else 'tracking', 'labelVisible': active})
    source = {'kind': 'demo', 'basis': 'offline_pose_with_temporal_heuristic',
        'notice': 'Local YOLO pose output + retrospective temporal heuristic; no trained action classifier or help-intent inference. Not live inference.',
        'model': raw['model']['name'], 'modelSha256': raw['model']['sha256'],
        'videoSha256': raw['source']['sha256'], 'poseExportSha256': sha(run / 'pose-recording.json'),
        'method': RULE['name'], 'ruleVersion': RULE['version'], 'exportedAt': raw['exportedAt'],
        'coordinateReview': 'visually_checked', 'trainedWaveClassifier': False, 'helpIntentEstablished': False}
    analysis = {'schemaVersion': 1, 'source': source, 'target': 'wave', 'sampleMode': 'frame',
        'video': {'width': width, 'height': height, 'durationMs': raw['source']['durationMs']},
        'display': {'startMs': 0, 'endMs': raw['source']['videoDurationMs'], 'fadeInMs': 0, 'fadeOutMs': 0},
        'maxInterpolationGapMs': RULE['maximumGapMs'], 'minimumKeypointConfidence': .65,
        'events': events, 'frames': frames}
    summary = {'schemaVersion': 1, 'video': raw['source'], 'model': raw['model'], 'method': RULE,
        'source': source, 'events': events, 'eventCount': len(events), 'newCloudCalls': 0,
        'keypointNames': {str(i): name for i, name in [(0, 'nose'), (5, 'leftShoulder'), (6, 'rightShoulder'),
            (7, 'leftElbow'), (8, 'rightElbow'), (9, 'leftWrist'), (10, 'rightWrist')]},
        'inspection': {'peoplePerFrame': [1], 'trackIds': ['person-1'],
            'fullBodyVisible': True, 'wristsVisible': True, 'encodedBlackBorders': False,
            'note': 'Portrait room recording; full body and both raised arms visible. Contact sheet checked against model coordinates; no manual coordinate replacement.'},
        'limitations': ['Pose scores are only model-provided detection/keypoint confidence; no action probability.',
            'Offline retrospective event onset precedes confirmedAtMs; not a real-time latency guarantee.',
            'Video ends during the gesture; final event boundary is the end of available video frames, not an observed gesture termination.',
            'Thresholds are evaluated on this short single-person demonstration; no general accuracy claim.',
            'Waving may mean greeting, exercise or help; intent cannot be established.',
            'Only COCO body keypoints; no hand/finger model or trained wave classifier.']}
    out = ROOT / 'assets/analysis/wave'
    write(out / 'wave_analysis.json', analysis)
    write(out / 'wave_summary.json', summary)
    # This approved demo export is public; raw local inference stays in data/run.
    public_out = ROOT / 'frontend/public/assets/analysis/wave'
    public_out.mkdir(parents=True, exist_ok=True)
    for name in ['wave_analysis.json', 'wave_summary.json']:
        shutil.copyfile(out / name, public_out / name)
    demo_dir = ROOT / 'frontend/public/demo'
    shutil.copyfile(video, demo_dir / 'videos/help.mp4')
    subprocess.run([str(ROOT / 'elder-safety/tools/ffmpeg/ffmpeg.exe'), '-y', '-v', 'error',
        '-i', str(video), '-frames:v', '1', '-q:v', '2', str(demo_dir / 'posters/help.jpg')], check=True)
    fixture_path = demo_dir / 'demo-public-fixture.json'
    fixture = json.loads(fixture_path.read_text(encoding='utf8'))
    item = next(c for c in fixture['cases'] if c['id'] == 'help')
    item.update({'title': '挥手动作线索', 'videoSrc': '/demo/videos/help.mp4?v=' + sha(video)[:16],
        'posterSrc': '/demo/posters/help.jpg?v=' + sha(demo_dir / 'posters/help.jpg')[:16],
        'durationMs': raw['source']['durationMs'], 'previewStartSec': events[0]['startMs'] / 1000 if events else 0,
        'previewEndSec': events[0]['endMs'] / 1000 if events else raw['source']['videoDurationMs'] / 1000,
        'resultStatus': 'pose_based_heuristic', 'event': None,
        'actionSummary': {'method': RULE['name'], 'events': events},
        'disclaimer': '姿态模型与手腕往复运动规则识别挥手，不代表真实求助。非实时推理。'})
    fixture['notice'] = 'Offline demonstrations; fall/fire use saved model review, wave uses real local pose output plus a temporal heuristic. Each overlay declares its provenance.'
    write(fixture_path, fixture)
    (ROOT / 'frontend/src/detection/waveDemoData.ts').write_text(
        "import analysis from '../../public/assets/analysis/wave/wave_analysis.json';\n"
        "import type { DetectionSequence } from './types';\n\n"
        "// Shared contract; this approved demo remains distinct from live inference.\n"
        "export const waveDetectionDemo = analysis as DetectionSequence;\n", encoding='utf8')
    write(run / 'analysis-publication.json', {'events': events, 'analysisSha256': sha(out / 'wave_analysis.json'),
        'sourceSha256': sha(video), 'publicVideoSha256': sha(demo_dir / 'videos/help.mp4'),
        'frameCount': len(frames), 'newCloudCalls': 0})


if __name__ == '__main__':
    main()
