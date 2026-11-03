import hashlib
import json
import math
import os
import shutil
from pathlib import Path
import subprocess
import re
from uuid import uuid4
from fractions import Fraction
from bisect import bisect_left

import cv2

from .config import Settings
from .errors import AppError


def command_json(args, timeout=30):
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout)
        if p.returncode:
            raise ValueError("media tool failed")
        return json.loads(p.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise AppError("MEDIA_PROBE_FAILED", "无法读取视频，请检查MP4是否损坏，以及FFprobe配置。", 400) from None


def inspect_video(path: Path, settings: Settings):
    with path.open("rb") as f:
        if b"ftyp" not in f.read(64):
            raise AppError("INVALID_MP4", "文件内容不是受支持的MP4容器。", 400)
    info = command_json([settings.ffprobe, "-v", "error", "-show_streams", "-show_format", "-print_format", "json", str(path)])
    streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if len(streams) != 1:
        raise AppError("VIDEO_STREAM", "首版只支持包含一个视频轨道的MP4。", 400)
    stream = streams[0]
    try:
        duration = float(stream.get("duration") or info["format"]["duration"])
        width, height = int(stream["width"]), int(stream["height"])
        if not math.isfinite(duration) or duration <= 0:
            raise AppError('VIDEO_DURATION', '视频缺少有效的正数时长。', 400)
        if settings.max_video_seconds and duration > settings.max_video_seconds + .05:
            raise AppError("VIDEO_DURATION", f"请上传不超过{settings.max_video_seconds}秒的MP4。", 400)
        if width * height > 4096 * 2160 or min(width, height) < 16:
            raise AppError("VIDEO_RESOLUTION", "首版支持16像素以上、总像素不超过4096×2160的视频。", 400)
        rotation = int(stream.get("tags", {}).get("rotate", 0)) % 360
        for side in stream.get("side_data_list", []):
            rotation = int(side.get("rotation", rotation)) % 360
        if rotation:
            raise AppError("VIDEO_ROTATION", "首版请先将手机视频旋转为正确方向并导出，再上传。", 400)
    except (ValueError, KeyError, TypeError):
        raise AppError("INVALID_METADATA", "视频缺少有效的时长或尺寸信息。", 400) from None
    return {"duration_ms": round(duration * 1000), "width": width, "height": height, "codec": stream.get("codec_name"),
            "pixel_format": stream.get("pix_fmt"), "start_seconds": float(stream.get("start_time") or 0), "time_mapping": "source_pts_minus_first_pts"}


def prepare_playback(source: Path, target: Path, settings: Settings, metadata=None):
    if target.exists():
        raise AppError('PLAYBACK_EXISTS', '播放文件已经存在，未覆盖原有文件。', 409)
    try:
        metadata = metadata or inspect_video(source, settings)
        # Compatible H.264 files need only a fast MP4 remux, preserving source frame timing.
        if metadata.get("codec") == "h264" and metadata.get("pixel_format") == "yuv420p" and abs(metadata.get("start_seconds", 0)) < .001:
            # A same-volume link avoids duplicating a day-long compatible recording.
            # Only the app's new upload is linked; the user's source file is untouched.
            try:
                os.link(source, target)
                return
            except OSError:
                if shutil.disk_usage(target.parent).free < source.stat().st_size + 256 * 1024 ** 2:
                    raise AppError('DISK_SPACE', '空间不足以准备播放文件，请更换数据目录或释放空间。', 413) from None
            args = [settings.ffmpeg, "-v", "error", "-y", "-i", str(source), "-map", "0:v:0", "-an", "-vcodec", "copy", "-movflags", "+faststart", str(target)]
        else:
            args = [settings.ffmpeg, "-v", "error", "-y", "-i", str(source), "-an", "-vf",
                            "scale=trunc(iw/2)*2:trunc(ih/2)*2,setpts=PTS-STARTPTS", "-vsync", "0",
                            "-vcodec", "libx264", "-bf", "0", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target)]
        p = subprocess.run(args, capture_output=True, timeout=settings.media_timeout)
        if p.returncode or not target.is_file():
            raise ValueError("transcode failed")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise AppError("TRANSCODE_FAILED", "视频转码失败；请确认FFmpeg支持H.264编码。", 400) from None


def extract_frames(video: dict, directory: Path, settings: Settings):
    source = Path(video["original_path"])
    directory.mkdir(parents=True, exist_ok=True)
    # Spool only timestamps to disk; never load all decoded-frame metadata into RAM.
    timestamps = directory / 'source-pts.txt'
    try:
        with timestamps.open('wb') as output:
            process = subprocess.run([settings.ffprobe, '-v', 'error', '-select_streams', 'v:0',
                                      '-show_entries', 'frame=best_effort_timestamp_time,pkt_pts_time', '-of', 'compact=p=0:nk=0', str(source)],
                                     stdout=output, stderr=subprocess.PIPE, timeout=settings.media_timeout)
        if process.returncode:
            raise ValueError('PTS probe failed')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise AppError('PTS_UNAVAILABLE', '无法读取原始时间戳，请检查媒体工具与处理时限。', 400) from None
    def points():
        origin, previous = None, -1
        with timestamps.open(encoding='utf8') as stream:
            for line in stream:
                fields = dict(part.split('=', 1) for part in line.strip().split('|') if '=' in part)
                if not fields:
                    continue
                try:
                    value = float(fields.get('best_effort_timestamp_time', fields.get('pkt_pts_time')))
                    if not math.isfinite(value):
                        raise ValueError('Invalid PTS')
                    if origin is None:
                        origin = value
                    pts = round((value-origin)*1000)
                    if pts < previous:
                        raise ValueError('PTS order')
                except (TypeError, ValueError):
                    raise AppError('PTS_UNAVAILABLE', '无法建立可靠的原始帧时间戳映射。', 400) from None
                previous = pts
                yield pts
    cap = cv2.VideoCapture(str(source))
    frames = []
    next_ms, index = 0, 0
    interval_ms = 1000 / settings.sampling_fps
    try:
        for pts in points():
            ok, image = cap.read()
            if not ok:
                raise AppError('DECODE_INCOMPLETE', '视频未完整解码，不能生成完整证据。', 400)
            if pts >= next_ms and pts < video["duration_ms"]:
                if len(frames) >= settings.max_sampled_frames:
                    raise AppError('FRAME_LIMIT', '抽样帧数量超过配置上限，请降低抽帧率或提高MAX_SAMPLED_FRAMES。', 400)
                scale = min(1, 960 / max(image.shape[:2]))
                image = cv2.resize(image, (max(1, round(image.shape[1] * scale)), max(1, round(image.shape[0] * scale))))
                frame_id = f"f{index:06d}"
                path = directory / f"{frame_id}.jpg"
                encoded_ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if not encoded_ok:
                    raise AppError("FRAME_WRITE", "关键帧保存失败，请检查磁盘空间。", 500)
                # OpenCV's Windows imwrite path handling can fail on Chinese directories.
                try:
                    path.write_bytes(encoded.tobytes())
                except OSError:
                    raise AppError("FRAME_WRITE", "关键帧保存失败，请检查目录权限或磁盘空间。", 500) from None
                frames.append({"id": frame_id, "pts_ms": pts, "kind": "original_frame", "path": str(path), "source_frame_index": index})
                next_ms = (math.floor(pts / interval_ms) + 1) * interval_ms
            index += 1
        if cap.read()[0]:
            raise AppError('PTS_MISMATCH', '解码帧与时间戳不匹配，不能生成可靠证据。', 400)
    finally:
        cap.release()
    if not frames:
        raise AppError("DECODE_INCOMPLETE", "视频未完整解码，未将不完整画面标为成功。", 400)
    return frames


def windows(frames, duration_ms, window_ms=8000, stride_ms=6000):
    points = [f['pts_ms'] for f in frames]
    for start in range(0, duration_ms, stride_ms):
        end = min(start + window_ms, duration_ms)
        selected = frames[bisect_left(points, start):bisect_left(points, end)]
        if selected:
            yield {"start_ms": start, "end_ms": end, "frames": selected}
        if end == duration_ms:
            break


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_segment_frames(video, directory, settings, start_ms, end_ms, fps, max_width=960):
    """Seek to one bounded interval. Evidence time comes from decoded PTS, never requested FPS."""
    if not 0 <= start_ms < end_ms <= video['duration_ms'] or end_ms - start_ms > 30000 or not .2 <= fps <= 4:
        raise AppError('SEGMENT_INTERVAL', '抽帧窗口或采样率无效。', 422)
    directory.mkdir(parents=True, exist_ok=True)
    source = video['original_path']
    # Read only stream timing; enumerating all source frames would decode the entire day.
    source_path = Path(source).resolve()
    signature = [str(source_path), source_path.stat().st_size, source_path.stat().st_mtime_ns]
    key = hashlib.sha256(json.dumps(signature).encode('utf8')).hexdigest()[:20]
    index_path = directory.parent / f'source-timing-{key}.json'
    if index_path.is_file():
        info = json.loads(index_path.read_text(encoding='utf8'))
    else:
        info = command_json([settings.ffprobe, '-v', 'error', '-select_streams', 'v:0',
                             '-show_entries', 'stream=time_base,start_time', '-of', 'json', source],
                            timeout=settings.media_timeout)
        index_path.write_text(json.dumps(info), encoding='utf8')
    try:
        origin = float(info['streams'][0].get('start_time', video.get('metadata', {}).get('start_seconds', 0)))
        time_base = Fraction(info['streams'][0]['time_base'])
    except (KeyError, IndexError, ValueError, TypeError):
        raise AppError('REVIEW_PTS', '无法读取原视频首帧时间戳。', 422) from None
    seek = max(0, start_ms / 1000 - 1)
    lower, upper = origin + start_ms / 1000, origin + end_ms / 1000
    select = f"select='gte(t,{lower:.6f})*lt(t,{upper:.6f})*(isnan(prev_selected_t)+gte(t-prev_selected_t,{1/fps - .00001:.6f}))'"
    limit = math.ceil((end_ms-start_ms)/1000*fps) + 1
    staging = directory / ('.segment-' + uuid4().hex)
    staging.mkdir()
    # Bound input decoding as well as output timestamps. A select filter drops all
    # later frames, so an output-only -t may otherwise keep decoding to source EOF.
    # The installed older decoder compares this stop bound against source PTS;
    # include a positive stream origin so offset recordings are not cut too early.
    input_duration = end_ms / 1000 - seek + 1 + max(0, origin)
    args = [settings.ffmpeg, '-loglevel', 'info', '-copyts', '-ss', f'{seek:.6f}', '-t', f'{input_duration:.6f}', '-i', source, '-an', '-vf',
            select + f",scale='min({max_width},iw)':-1,showinfo", '-vsync', '0', '-t', f'{upper:.6f}',
            '-frames:v', str(limit), '-q:v', '3', str(staging / '%04d.jpg')]
    try:
        process = subprocess.run(args, capture_output=True, timeout=min(settings.media_timeout, 180))
        pts = [round((float(int(p)*time_base) - origin)*1000) for p in re.findall(r'\bn:\s*\d+\s+pts:\s*([\d+\-]+)', process.stderr.decode('utf8', errors='replace'))]
        files = sorted(staging.glob('*.jpg'))
        if process.returncode or not files or len(files) != len(pts) or len(files) > limit:
            raise ValueError('Invalid frame/PTS mapping')
        if any(not start_ms <= p < end_ms for p in pts) or len(set(pts)) != len(pts) or pts != sorted(pts):
            raise ValueError('Invalid source timestamps')
        frames = []
        for path, timestamp in zip(files, pts):
            identifier = f'sf_{timestamp:012d}'
            target = directory / (identifier + '.jpg')
            if target.exists():
                path.unlink()
            else:
                path.rename(target)
            frames.append({'id': identifier, 'pts_ms': timestamp, 'kind': 'original_frame', 'path': str(target)})
        return frames
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise AppError('REVIEW_DECODE', '复核片段抽帧失败，未生成伪造时间证据。', 422) from None
    finally:
        if staging.resolve().parent == directory.resolve():
            shutil.rmtree(staging, ignore_errors=True)


def extract_review_frames(video, directory, settings, start_ms, end_ms, fps=2):
    if end_ms - start_ms > 12000 or not 1 <= fps <= 4:
        raise AppError('REVIEW_INTERVAL', '复核仅支持最长12秒、每秒1–4帧的有效视频片段。', 422)
    # Each review has its own folder; repeated checks retain independent evidence IDs.
    frames = extract_segment_frames(video, directory, settings, start_ms, end_ms, fps, max_width=1920)
    for frame in frames:
        identifier = 'rf_' + uuid4().hex
        target = directory / (identifier + '.jpg')
        Path(frame['path']).rename(target)
        frame.update(id=identifier, path=str(target))
    return frames
