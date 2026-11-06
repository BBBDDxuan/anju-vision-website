"""Export the existing local pose model's offline trace for the verified fall clip.

Run with the detector Python. No cloud, camera, package installs or config writes.
Raw results/contact sheets stay in data/run; publishing requires a visual-review
record tied to the raw trace hash. See docs/FALL_DETECTION_DEMO.md.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'elder-safety/ai-service'))
VIDEO_SHA256 = '24bc42907926bf3981bdedc2a4f54bc32454fa06a1228b7cf02ac181ba584938'
JOINTS = {0, *range(5, 17)}  # COCO: nose, shoulders, elbows, wrists, hips, knees, ankles.
EDGES = [(0, 5), (0, 6), (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
         (5, 11), (6, 12), (11, 12), (11, 13), (13, 15), (12, 14), (14, 16)]
MIN_POINT_SCORE = .65
RISK_START_MS = 5000  # F05=4.50s is still standing; F06=5.00s begins descent.
# Source-pixel boxes checked against every displayed decoded frame. These are
# explicitly demonstration annotations, including gaps in the pose detector.
BOX_ANCHORS = [
    (2300, (224, 78, 330, 304)), (2500, (224, 78, 330, 304)),
    (2800, (220, 78, 330, 304)), (3100, (220, 78, 330, 304)),
    (3400, (220, 78, 332, 304)), (3700, (224, 76, 334, 304)),
    (4000, (222, 74, 330, 304)), (4300, (222, 76, 332, 304)),
    (4450, (224, 82, 332, 304)), (4600, (224, 108, 334, 304)),
    (4700, (220, 118, 334, 304)), (4800, (228, 150, 338, 324)),
    (4900, (236, 182, 342, 348)), (5000, (238, 204, 346, 374)),
    (5100, (240, 226, 350, 386)), (5250, (244, 240, 356, 400)),
    (5400, (254, 250, 366, 390)), (5500, (258, 250, 372, 400)),
    (5650, (266, 240, 384, 422)), (5800, (260, 240, 392, 454)),
    (5950, (248, 236, 406, 480)), (6100, (252, 236, 412, 480)),
    (6300, (274, 238, 414, 480)), (6500, (282, 236, 416, 480)),
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def infer(video, run, config):
    import cv2
    from ai_service.config import Config
    from ai_service.models import LocalModels

    cfg = Config.load(str(config))
    # Reuse production resizing and rotated-pose recovery, with no flame model.
    models = LocalModels(replace(cfg.models, flame_weights=None))
    probe = subprocess.run([str(ROOT / 'elder-safety/tools/ffmpeg/ffprobe.exe'),
        '-v', 'error', '-select_streams', 'v:0', '-show_entries',
        'frame=best_effort_timestamp_time', '-of', 'json', str(video)],
        capture_output=True, text=True, check=True)
    pts = [float(f['best_effort_timestamp_time']) for f in json.loads(probe.stdout)['frames']]
    cap = cv2.VideoCapture(str(video))
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frames = []
    try:
        for index, timestamp in enumerate(pts):
            ok, image = cap.read()
            if not ok:
                raise ValueError('Decoder ended before the source PTS list')
            people = []
            for pose in models.infer_pose(image):
                people.append({'bbox': [float(v) for v in pose.bbox],
                    'confidence': float(pose.confidence), 'keypoints': pose.keypoints.tolist()})
            frames.append({'timeMs': round((timestamp - pts[0]) * 1000), 'people': people})
            if index % 40 == 0:
                print(f'Local pose inference: {index + 1}/{len(pts)} frames', flush=True)
        if cap.read()[0]:
            raise ValueError('Decoder contains more frames than the source PTS list')
    finally:
        cap.release()
    raw = {'sourceSha256': digest(video), 'width': width, 'height': height,
        'durationMs': 8550, 'timestampBasis': 'ffprobe decoded best_effort_timestamp_time minus first PTS',
        'model': Path(cfg.models.pose_weights).name, 'modelSha256': digest(Path(cfg.models.pose_weights)),
        'ultralytics': importlib.metadata.version('ultralytics'), 'device': cfg.models.device,
        'poseMaxWidth': cfg.models.pose_max_width,
        'rotatedPoseRecovery': cfg.models.pose_fall_recovery_enabled,
        'exportedAt': datetime.now(timezone.utc).isoformat(), 'cloudCalls': 0, 'frames': frames}
    write_json(run / 'pose-recording.json', raw)
    return raw


def checked_box(time):
    for (a, box_a), (b, box_b) in zip(BOX_ANCHORS, BOX_ANCHORS[1:]):
        if a <= time <= b:
            ratio = (time - a) / (b - a)
            return [lo + (hi - lo) * ratio for lo, hi in zip(box_a, box_b)]
    raise ValueError('No checked box for this timestamp')


def display_frames(raw, evidence_end_ms):
    width, height = raw['width'], raw['height']
    frames = []
    clip = lambda v: round(max(0., min(1., v)), 6)
    for frame in raw['frames']:
        time = frame['timeMs']
        if not 2300 <= time <= evidence_end_ms + 200:
            continue
        people = []
        # This verified single-person clip has one main subject. Exclude tiny
        # screen reflections. Boxes are checked annotations; joints are never guessed.
        candidates = [p for p in frame['people'] if p['confidence'] >= .55
            and (p['bbox'][2] - p['bbox'][0]) * (p['bbox'][3] - p['bbox'][1]) > width * height * .015]
        points = []
        if candidates:
            person = max(candidates, key=lambda p: (p['bbox'][2] - p['bbox'][0]) * (p['bbox'][3] - p['bbox'][1]))
            points = [{'id': index, 'x': clip(point[0] / width), 'y': clip(point[1] / height),
                'confidence': round(point[2], 4)} for index, point in enumerate(person['keypoints'])
                if index in JOINTS and point[2] >= MIN_POINT_SCORE
                and 0 < point[0] < width and 0 < point[1] < height]
        x1, y1, x2, y2 = checked_box(time)
        people.append({'id': 'person-1', 'box': {'x': clip(x1 / width), 'y': clip(y1 / height),
            'width': clip((x2 - x1) / width), 'height': clip((y2 - y1) / height)},
            'boxSource': 'reviewed_annotation', 'keypointSource': 'offline_model', 'keypoints': points})
        frames.append({'timeMs': time, 'people': people,
            'state': 'suspected_fall' if time >= RISK_START_MS else 'tracking',
            'labelVisible': time >= RISK_START_MS})
    return frames


def read_analysis(folder):
    """Read only the authoritative saved result; never issue model requests."""
    checkpoint = json.loads((folder / 'checkpoint.json').read_text(encoding='utf8'))
    assert checkpoint['video_sha256'] == VIDEO_SHA256
    event, = [e for e in checkpoint['events'] if e['action'] == 'fall']
    evidence, = [e for e in checkpoint['evidence'] if e['id'] in event['evidence_ids']]
    candidate, = [c for c in checkpoint['safety']['candidates'] if c['event_type'] == 'suspected_fall']
    review = checkpoint['safety']['reviews'][candidate['id']]
    assert review['verdict'] == 'confirmed'
    assert evidence['frame_ids'] == review['frame_ids']
    assert 0 <= evidence['start_ms'] < RISK_START_MS < evidence['end_ms'] < 8550
    frames = {f['id']: f for f in checkpoint['frames']}
    return event, evidence, candidate, review, frames


def publish_evidence(folder, analysis):
    event, evidence, candidate, review, frames = analysis
    target = ROOT / 'frontend/public/demo'
    fixture = json.loads((target / 'demo-public-fixture.json').read_text(encoding='utf8'))
    fall, = [case for case in fixture['cases'] if case['id'] == 'fall']
    refs = []
    for frame_id in evidence['frame_ids']:
        assert frame_id.startswith('sf_') and frame_id.replace('_', '').isalnum()
        shutil.copy2(folder / 'frames' / f'{frame_id}.jpg', target / 'keyframes' / f'{frame_id}.jpg')
        refs.append({'time_ms': frames[frame_id]['pts_ms'], 'image': f'/demo/keyframes/{frame_id}.jpg', 'frame_id': frame_id})
    fall['event'] = {'title': '疑似跌倒', 'type': candidate['event_type'],
        'startMs': evidence['start_ms'], 'endMs': evidence['end_ms'],
        'localCandidateTriggerSec': candidate['t'], 'modelReviewVerdict': review['verdict'],
        'humanConfirmed': False, 'modelScore': event['model_score'],
        'originalReviewStatus': event['review_status'], 'modelReviewReason': review['reason'],
        'referencedFrames': refs,
        'subjectBboxHint': {'atSec': candidate['t'], 'xyxyNormalized': candidate['subject_bbox_hint'],
            'note': 'Only at this candidate trigger; not frame-by-frame output.'}}
    write_json(target / 'demo-public-fixture.json', fixture)


def contact_sheets(video, run, frames, width, height):
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(str(video))
    tiles = []
    for frame in frames:
        cap.set(cv2.CAP_PROP_POS_MSEC, frame['timeMs'])
        ok, image = cap.read()
        if not ok:
            raise ValueError('Cannot decode visual-review frame')
        for person in frame['people']:
            box = person['box']
            a = (round(box['x'] * width), round(box['y'] * height))
            b = (round((box['x'] + box['width']) * width), round((box['y'] + box['height']) * height))
            cv2.rectangle(image, a, b, (180, 220, 100), 2)
            points = {p['id']: (round(p['x'] * width), round(p['y'] * height)) for p in person['keypoints']}
            for a, b in EDGES:
                if a in points and b in points:
                    cv2.line(image, points[a], points[b], (180, 220, 100), 2)
            for point in points.values():
                cv2.circle(image, point, 3, (180, 245, 135), -1)
        image = cv2.resize(image, (360, 240))
        cv2.putText(image, f"{frame['timeMs']/1000:.2f}s", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 230, 255), 2)
        tiles.append(image)
    cap.release()
    for start in range(0, len(tiles), 20):
        page = tiles[start:start + 20]
        page += [np.zeros((240, 360, 3), dtype=np.uint8)] * (20 - len(page))
        sheet = np.vstack([np.hstack(page[i:i + 5]) for i in range(0, 20, 5)])
        cv2.imwrite(str(run / f'pose-contact-{start//20+1:02d}.jpg'), sheet)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, default=ROOT / 'data/run/fall-overlay-check-20261106')
    parser.add_argument('--config', type=Path, default=ROOT / 'runtime/team-camera.json')
    parser.add_argument('--analysis-dir', type=Path, required=True, help='Authoritative extracted analysis run')
    parser.add_argument('--reuse', action='store_true', help='Reuse the existing pose trace; no inference')
    parser.add_argument('--publish', action='store_true', help='Publish the trace after reviewing contact sheets')
    args = parser.parse_args()
    run = args.run_dir.resolve()
    run.mkdir(parents=True, exist_ok=True)
    video = ROOT / 'frontend/public/demo/videos/fall.mp4'
    if digest(video) != VIDEO_SHA256:
        raise ValueError('This exporter is only for the source-verified 8.55s fall demo')
    raw_path = run / 'pose-recording.json'
    raw = json.loads(raw_path.read_text(encoding='utf8')) if args.reuse else infer(video, run, args.config)
    assert raw['sourceSha256'] == VIDEO_SHA256 and (raw['width'], raw['height']) == (720, 480)
    analysis = read_analysis(args.analysis_dir)
    evidence_end_ms = analysis[1]['end_ms']
    frames = display_frames(raw, evidence_end_ms)
    if not args.publish:
        contact_sheets(video, run, frames, raw['width'], raw['height'])
    if args.publish:
        review = json.loads((run / 'coordinate-review.json').read_text(encoding='utf8'))
        assert review['status'] == 'visually_checked' and review['rawSha256'] == digest(raw_path)
        assert review['checkedFrames'] >= len(frames)
        assert review['checkedRangeMs'][0] <= frames[0]['timeMs'] and review['checkedRangeMs'][1] >= frames[-1]['timeMs']
        sequence = {'schemaVersion': 1,
            'source': {'kind': 'demo', 'basis': 'offline_model_with_reviewed_annotations',
                'model': raw['model'], 'videoSha256': VIDEO_SHA256,
                'modelSha256': raw['modelSha256'], 'coordinateReview': 'visually_checked',
                'reviewExportSha256': digest(args.analysis_dir / 'checkpoint.json'),
                'reviewModel': analysis[3]['model'], 'reviewedAt': analysis[3]['reviewed_at'],
                'evidenceStartMs': analysis[1]['start_ms'], 'evidenceEndMs': evidence_end_ms,
                'notice': '离线演示 · 含核对标注 · 非实时推理',
                'riskBasis': 'Latest saved Qwen review F05-F08 supports suspected fall; F05 still standing, F06 descending; not human confirmation.'},
            'video': {'width': raw['width'], 'height': raw['height'], 'durationMs': raw['durationMs']},
            'display': {'startMs': 2300, 'endMs': evidence_end_ms, 'fadeInMs': 150, 'fadeOutMs': 200},
            'maxInterpolationGapMs': 100, 'minimumKeypointConfidence': MIN_POINT_SCORE, 'frames': frames}
        target = ROOT / 'frontend/src/detection/fallDemoData.ts'
        target.parent.mkdir(parents=True, exist_ok=True)
        header = "// Generated from the latest saved review, checked boxes and separate local pose output.\n"
        header += "// Raw trace and visual checks: data/run/fall-overlay-check-20261106 (ignored by Git).\n"
        header += "import type { DetectionSequence } from './types';\n\n"
        # One real decoded frame per line keeps the versioned fixture reviewable.
        lines = json.dumps({k: v for k, v in sequence.items() if k != 'frames'}, ensure_ascii=False, indent=2)
        lines = lines[:-2] + ',\n  "frames": [\n' + ',\n'.join('    ' + json.dumps(f, ensure_ascii=False, separators=(',', ':')) for f in frames) + '\n  ]\n}'
        target.write_text(header + 'export const fallDetectionDemo: DetectionSequence = ' + lines + ';\n', encoding='utf8')
        publish_evidence(args.analysis_dir, analysis)
        write_json(run / 'published-provenance.json', {'sourceSha256': VIDEO_SHA256,
            'rawSha256': digest(raw_path), 'fixtureSha256': digest(target),
            'displayedFrames': len(frames), 'newCloudCalls': 0, 'riskStartMs': RISK_START_MS,
            'riskEndMs': evidence_end_ms, 'sourceInferenceFrames': len(raw['frames']),
            'latestExportSha256': digest(args.analysis_dir / 'checkpoint.json'),
            'evidenceStartMs': analysis[1]['start_ms'], 'referencedFrames': len(analysis[1]['frame_ids'])})
        print(f'Published {len(frames)} verified frame samples to {target}')
    else:
        print(f'Saved {len(raw["frames"])} raw frames and {len(frames)} visual-check frames in {run}')


if __name__ == '__main__':
    main()
