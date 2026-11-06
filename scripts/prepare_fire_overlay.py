"""Publish a source-matched fire demo from saved review and checked annotations.

No inference, network calls, package installs or environment-file writes.
Run after decoding frame PTS, matching exported evidence to the trimmed clip,
and visually checking each source-pixel rectangle. See docs/FIRE_DETECTION_DEMO.md.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video', type=Path, required=True)
    parser.add_argument('--analysis-dir', type=Path, required=True)
    parser.add_argument('--check-dir', type=Path, default=ROOT / 'data/run/fire-overlay-check-20261106')
    parser.add_argument('--trim-start-seconds', type=float, default=0,
                        help='Remove this much time from the checked input; retain source-frame correspondence.')
    parser.add_argument('--trim-end-seconds', type=float, default=0,
                        help='Remove this much time from the end after the leading trim.')
    parser.add_argument('--run-dir', type=Path, help='Save derived media and publication records separately from source checks.')
    args = parser.parse_args()
    check, source = args.check_dir.resolve(), args.analysis_dir.resolve()
    run = args.run_dir.resolve() if args.run_dir else check
    run.mkdir(parents=True, exist_ok=True)
    checkpoint = read(source / 'checkpoint.json')
    matches = read(check / 'video-frame-matches.json')
    boxes = read(check / 'box-proposals.json')
    checked = read(check / 'coordinate-review.json')
    event, = [e for e in checkpoint['events'] if e['action'] == 'flame']
    evidence, = [e for e in checkpoint['evidence'] if e['id'] in event['evidence_ids']]
    candidate, = [c for c in checkpoint['safety']['candidates'] if c['event_type'] == 'fire_detected']
    review = checkpoint['safety']['reviews'][candidate['id']]
    assert review['verdict'] == 'confirmed' and evidence['frame_ids'] == review['frame_ids']
    assert [f['frameId'] for f in matches] == evidence['frame_ids']
    assert checked['status'] == 'visually_checked' and checked['videoSha256'] == sha(args.video)
    assert checked['annotationsSha256'] == sha(check / 'box-proposals.json')
    assert checked['matchingSha256'] == sha(check / 'video-frame-matches.json')
    assert checked['checkedFrames'] == len(boxes)
    assert checked['checkpointSha256'] == sha(source / 'checkpoint.json')

    probe = probe_video(args.video)
    pts = [round(float(f['best_effort_timestamp_time']) * 1000, 3) for f in probe['frames']]
    width, height = probe['streams'][0]['width'], probe['streams'][0]['height']
    duration = float(probe['format']['duration']) * 1000
    for match in matches:
        assert abs(pts[match['trimFrameIndex']] - match['trimTimeMs']) < .01
        assert match['imageSize'] == [width, height]
    assert [b['frameIndex'] for b in boxes] == list(range(matches[0]['trimFrameIndex'], matches[-1]['trimFrameIndex'] + 1))
    assert math.isfinite(args.trim_start_seconds) and 0 <= args.trim_start_seconds * 1000 < pts[boxes[0]['frameIndex']]
    published_video = args.video
    source_matches = matches
    first_retained_index = 0
    source_offset_ms = 0
    if args.trim_start_seconds:
        source_pts = pts
        first_retained_index = bisect_left(source_pts, args.trim_start_seconds * 1000)
        source_offset_ms = source_pts[first_retained_index]
        published_video = run / 'fire-trimmed.mp4'
        assert published_video.resolve() != args.video.resolve(), 'Never overwrite the checked source video'
        subprocess.run([str(ROOT / 'elder-safety/tools/ffmpeg/ffmpeg.exe'), '-y', '-v', 'error',
            '-i', str(args.video), '-map', '0:v:0', '-map', '0:a?',
            '-vf', f'trim=start={args.trim_start_seconds},setpts=PTS-STARTPTS',
            '-af', f'atrim=start={args.trim_start_seconds},asetpts=PTS-STARTPTS',
            '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
            '-fps_mode', 'passthrough', '-enc_time_base:v', '1:29000', '-video_track_timescale', '29000',
            '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(published_video)], check=True)
        probe = probe_video(published_video)
        pts = [round(float(f['best_effort_timestamp_time']) * 1000, 3) for f in probe['frames']]
        assert len(pts) == len(source_pts) - first_retained_index
        assert (probe['streams'][0]['width'], probe['streams'][0]['height']) == (width, height)
        assert all(abs(p - (s - source_offset_ms)) < .01 for p, s in zip(pts, source_pts[first_retained_index:]))
        duration = float(probe['format']['duration']) * 1000
        matches = [{**m, 'inputFrameIndex': m['trimFrameIndex'], 'inputTimeMs': m['trimTimeMs'],
                    'trimFrameIndex': m['trimFrameIndex'] - first_retained_index,
                    'trimTimeMs': pts[m['trimFrameIndex'] - first_retained_index],
                    'offsetMs': round(m['sourceTimeMs'] - pts[m['trimFrameIndex'] - first_retained_index], 3)}
                   for m in matches]
        boxes = [{**b, 'frameIndex': b['frameIndex'] - first_retained_index} for b in boxes]
        write(run / 'derived-frame-mapping.json', {
            'inputVideoSha256': sha(args.video), 'publishedVideoSha256': sha(published_video),
            'requestedTrimStartMs': args.trim_start_seconds * 1000, 'actualFirstInputFrameMs': source_offset_ms,
            'firstRetainedInputFrameIndex': first_retained_index, 'retainedFrameCount': len(pts),
            'allFrameTimestampsVerified': True, 'spatialTransform': 'none',
            'sourceCoordinateReviewSha256': sha(check / 'coordinate-review.json'),
            'sourceMatches': source_matches, 'publishedMatches': matches})
    assert math.isfinite(args.trim_end_seconds) and args.trim_end_seconds >= 0
    if args.trim_end_seconds:
        before_end_trim = published_video
        before_end_probe = probe
        before_end_duration = duration
        retained_duration = duration / 1000 - args.trim_end_seconds
        assert retained_duration * 1000 > pts[boxes[-1]['frameIndex'] + 1], 'Keep all reviewed evidence'
        published_video = run / 'fire-end-trimmed.mp4'
        assert published_video.resolve() != before_end_trim.resolve(), 'Never overwrite the input video'
        subprocess.run([str(ROOT / 'elder-safety/tools/ffmpeg/ffmpeg.exe'), '-y', '-v', 'error',
            '-i', str(before_end_trim), '-t', f'{retained_duration:.6f}',
            '-map', '0:v:0', '-map', '0:a?',
            '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
            '-fps_mode', 'passthrough', '-enc_time_base:v', '1:29000', '-video_track_timescale', '29000',
            '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(published_video)], check=True)
        probe = probe_video(published_video)
        final_pts = [round(float(f['best_effort_timestamp_time']) * 1000, 3) for f in probe['frames']]
        assert all(abs(a - b) < .01 for a, b in zip(final_pts, pts))
        assert len(final_pts) <= len(pts) and len(final_pts) > boxes[-1]['frameIndex'] + 1
        assert (probe['streams'][0]['width'], probe['streams'][0]['height']) == (width, height)
        pts = final_pts
        duration = float(probe['format']['duration']) * 1000
        assert abs(duration - retained_duration * 1000) < 40, 'Trim precision is limited to one video frame'
        write(run / 'end-trim-mapping.json', {
            'inputVideoSha256': sha(before_end_trim), 'publishedVideoSha256': sha(published_video),
            'inputDurationMs': before_end_duration, 'requestedTrimEndMs': args.trim_end_seconds * 1000,
            'requestedDurationMs': retained_duration * 1000, 'publishedDurationMs': duration,
            'inputFrameCount': len(before_end_probe['frames']), 'retainedFrameCount': len(pts),
            'retainedFrameTimestampsUnchanged': True, 'spatialTransform': 'none',
            'reviewedEvidenceFullyRetained': True})
    start, end = matches[0]['trimTimeMs'], matches[-1]['trimTimeMs']
    valid_end = pts[boxes[-1]['frameIndex'] + 1]
    frames = []
    for b in boxes:
        index = b['frameIndex']
        x1, y1, x2, y2 = b['xyxy']
        assert 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height
        frames.append({'timeMs': pts[index], 'frameIndex': index, 'validUntilMs': pts[index + 1],
            'people': [], 'objects': [{'id': 'flame-region-1', 'className': candidate['details']['label'],
                'box': {'x': round(x1 / width, 6), 'y': round(y1 / height, 6),
                        'width': round((x2 - x1) / width, 6), 'height': round((y2 - y1) / height, 6)},
                'boxSource': 'reviewed_annotation'}], 'state': 'suspected_fire', 'labelVisible': True})
    sequence = {'schemaVersion': 1, 'target': 'fire', 'sampleMode': 'frame',
        'source': {'kind': 'demo', 'basis': 'model_review_with_reviewed_annotations',
            'notice': '离线演示 · 火焰框为逐帧核对标注 · 非实时推理',
            'model': 'indoor-dfire-yolov8n.pt', 'videoSha256': sha(published_video),
            'modelSha256': dict(checkpoint['signature'][5][2][2])['flame_weights'],
            'reviewExportSha256': sha(source / 'checkpoint.json'), 'reviewModel': review['model'],
            'reviewedAt': review['reviewed_at'], 'evidenceStartMs': start, 'evidenceEndMs': end,
            'coordinateReview': 'visually_checked',
            'riskBasis': 'Saved confirmed fire review; 12 exported images matched individually to the trimmed video; boxes are reviewed demonstration annotations, not model frame output.'},
        'video': {'width': width, 'height': height, 'durationMs': round(duration)},
        'display': {'startMs': start, 'endMs': end, 'fadeInMs': 60, 'fadeOutMs': round(valid_end - end, 3)},
        'maxInterpolationGapMs': 40, 'minimumKeypointConfidence': .65, 'frames': frames}
    target = ROOT / 'frontend/src/detection/fireDemoData.ts'
    header = "// Generated from saved fire review plus source-matched, visually checked rectangles.\n"
    header += "// This is preset demonstration geometry, not continuous model detection or segmentation.\n"
    header += "import type { DetectionSequence } from './types';\n\n"
    parts = json.dumps({k: v for k, v in sequence.items() if k != 'frames'}, ensure_ascii=False, indent=2)
    parts = parts[:-2] + ',\n  "frames": [\n' + ',\n'.join('    ' + json.dumps(f, ensure_ascii=False, separators=(',', ':')) for f in frames) + '\n  ]\n}'
    target.write_text(header + 'export const fireDetectionDemo: DetectionSequence = ' + parts + ';\n', encoding='utf8')
    public = ROOT / 'frontend/public/demo'
    fixture = read(public / 'demo-public-fixture.json')
    fire, = [c for c in fixture['cases'] if c['id'] == 'fire']
    refs = []
    for match in matches:
        frame_id = match['frameId']
        assert frame_id.startswith('sf_') and frame_id.replace('_', '').isalnum()
        shutil.copy2(source / 'frames' / f'{frame_id}.jpg', public / 'keyframes' / f'{frame_id}.jpg')
        refs.append({'time_ms': match['trimTimeMs'], 'image': f'/demo/keyframes/{frame_id}.jpg', 'frame_id': frame_id})
    fire.update({'durationMs': round(duration), 'previewStartSec': start / 1000, 'previewEndSec': end / 1000,
        'resultStatus': 'verified_model_export',
        'disclaimer': '木制模型燃烧实景测试；模型复核支持明火线索，未经人工确认，不代表已确认居家火灾。',
        'event': {'title': '疑似明火', 'type': 'fire_detected', 'startMs': start, 'endMs': end,
            # Trigger is mapped piecewise from nearby matching exported frames.
            'localCandidateTriggerSec': mapped_time(candidate['t'] * 1000, matches) / 1000,
            'modelReviewVerdict': review['verdict'], 'humanConfirmed': False, 'modelScore': event['model_score'],
            'originalReviewStatus': event['review_status'], 'modelReviewReason': review['reason'],
            'referencedFrames': refs}})
    shutil.copy2(published_video, public / 'videos/fire.mp4')
    subprocess.run([str(ROOT / 'elder-safety/tools/ffmpeg/ffmpeg.exe'), '-y', '-v', 'error',
        '-i', str(published_video), '-frames:v', '1', '-q:v', '2',
        str(public / 'posters/fire.jpg')], check=True)
    # Changed media must get a new browser cache key, even with the same filename.
    fire['videoSrc'] = f'/demo/videos/fire.mp4?v={sha(published_video)[:16]}'
    fire['posterSrc'] = f'/demo/posters/fire.jpg?v={sha(public / "posters/fire.jpg")[:16]}'
    write(public / 'demo-public-fixture.json', fixture)
    write(run / 'published-provenance.json', {'videoSha256': sha(published_video),
        'inputVideoSha256': sha(args.video), 'requestedTrimStartMs': args.trim_start_seconds * 1000,
        'requestedTrimEndMs': args.trim_end_seconds * 1000,
        'actualFirstInputFrameMs': source_offset_ms,
        'analysisSourceVideoSha256': checkpoint['video_sha256'], 'checkpointSha256': sha(source / 'checkpoint.json'),
        'fixtureSha256': sha(target), 'annotatedFrames': len(frames),
        'sourceEvidenceRangeMs': [evidence['start_ms'], evidence['end_ms']],
        'trimEvidenceRangeMs': [start, end], 'overlayValidUntilMs': valid_end,
        'newCloudCalls': 0, 'newModelInferences': 0, 'modelEventCount': 1,
        'hasModelFrameBoxes': False, 'hasSegmentationMask': False, 'hasReviewedAnnotations': True,
        'timeMapping': '12 individually matched exported images; exact decoded trim PTS; no assumed constant offset',
        'candidateConfidence': candidate['confidence'],
        'confidenceKind': candidate['details']['confidence_kind'], 'showConfidenceInUi': False})
    print(f'Published {len(frames)} checked fire frames, {start:.3f}–{end:.3f} ms; no inference calls.')


def mapped_time(time_ms, matches):
    for a, b in zip(matches, matches[1:]):
        if a['sourceTimeMs'] <= time_ms <= b['sourceTimeMs']:
            ratio = (time_ms - a['sourceTimeMs']) / (b['sourceTimeMs'] - a['sourceTimeMs'])
            return a['trimTimeMs'] + ratio * (b['trimTimeMs'] - a['trimTimeMs'])
    raise ValueError('Timestamp outside verified mapping')


def probe_video(video):
    return json.loads(subprocess.check_output([str(ROOT / 'elder-safety/tools/ffmpeg/ffprobe.exe'),
        '-v', 'error', '-select_streams', 'v:0', '-show_entries',
        'frame=best_effort_timestamp_time:stream=width,height,avg_frame_rate,nb_frames:format=duration',
        '-of', 'json', str(video)], text=True))


if __name__ == '__main__':
    main()
