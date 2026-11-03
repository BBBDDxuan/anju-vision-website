from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import json
import logging
import math
import shutil
import threading
import time
from uuid import uuid4

from .config import Settings
from .db import Database
from .errors import AppError
from .events import Extraction, normalize, validate_memory
from .media import extract_frames, extract_segment_frames, windows
from .providers.base import VisionProvider, ProviderResult
from .providers.qwen import QwenProvider
from .prompting import load_event_prompt
from .activity import ActivityGate, FILTER_VERSION
from .hybrid_safety import HybridSafety, REVIEW_TOKENS
from .safety import RULES as SAFETY_RULES

PIPELINE_VERSION = 'uploaded-hybrid-safety-v1'


class JobManager:
    def __init__(self, settings: Settings, db: Database, provider: VisionProvider):
        self.settings, self.db, self.provider = settings, db, provider
        self.safety = HybridSafety(settings, db, provider)
        self.prompt = getattr(provider, 'event_prompt', None) or load_event_prompt()
        self.pool = ThreadPoolExecutor(max_workers=settings.max_concurrency, thread_name_prefix="video-analysis")
        self.lock, self.active = threading.Lock(), set()
        self.pause_requests = {}
        self.stopping = threading.Event()

    def close(self):
        self.stopping.set()
        self.pool.shutdown(wait=True, cancel_futures=False)

    def profile(self, video, mode='all'):
        if video['duration_ms'] > self.settings.long_video_threshold_seconds * 1000:
            return replace(self.settings, sampling_fps=self.settings.long_sampling_fps, window_seconds=self.settings.long_window_seconds, window_stride_seconds=self.settings.long_window_stride_seconds)
        if self.settings.enhanced_safety_sampling and mode != 'objects':
            window = min(self.settings.window_seconds, self.settings.safety_window_seconds)
            return replace(self.settings, sampling_fps=max(self.settings.sampling_fps, self.settings.safety_sampling_fps),
                           window_seconds=window, window_stride_seconds=min(window, self.settings.window_stride_seconds, self.settings.safety_window_stride_seconds))
        return self.settings

    def signature(self, video, mode='all'):
        profile = self.profile(video, mode)
        signature = [profile.sampling_fps, profile.window_seconds, profile.window_stride_seconds, self.provider.name, self.provider.model]
        if self.filter_enabled(video):
            signature.append(ActivityGate.signature(self.settings))
        enabled = self.safety.enabled and mode != 'objects'
        signature.append([PIPELINE_VERSION, bool(enabled), self.safety.signature() if enabled else None])
        if mode != 'all':
            signature.append(['analysis_mode', mode])
        return signature

    def filter_enabled(self, video):
        return self.settings.activity_filter_enabled and video['duration_ms'] > self.settings.long_video_threshold_seconds*1000

    def plan(self, video_id, mode='all'):
        if mode not in {'all', 'safety', 'objects'}:
            raise AppError('ANALYSIS_MODE', '请选择安全检测或物品追踪。', 400)
        video = self.db.video(video_id)
        profile = self.profile(video, mode)
        if mode == 'safety':
            return {'analysis_mode': mode, 'total_windows': 0, 'estimated_requests_without_retries': None,
                    'max_windows_per_batch': min(self.settings.max_calls, self.settings.max_frame_references // 12, self.settings.max_output_tokens // REVIEW_TOKENS),
                    'daily_requests_remaining': max(0, self.settings.max_daily_calls-self.db.usage_summary()['requests']),
                    'estimated_batches': None, 'billing_mode': self.settings.billing_mode, 'rmb_cost': None,
                    'hybrid_safety_enabled': bool(self.safety.enabled), 'estimated_requests_excludes_safety_reviews': True,
                    'safety_review_output_tokens': REVIEW_TOKENS, 'sampling_mode': 'local_safety_candidates',
                    'profile': 'safety', 'estimated_sampled_frames': None, 'activity_filter_enabled': False}
        count = max(1, math.ceil((video['duration_ms'] / 1000 - profile.window_seconds) / profile.window_stride_seconds) + 1)
        is_long = video['duration_ms'] > self.settings.long_video_threshold_seconds * 1000
        remaining = max(0, self.settings.max_daily_calls - self.db.usage_summary()['requests'])
        frame_bound = math.ceil(profile.window_seconds * profile.sampling_fps)
        batch = min(self.settings.max_calls, self.settings.max_frame_references // frame_bound,
                    self.settings.max_output_tokens // self.settings.window_output_tokens)
        return {'analysis_mode': mode, 'total_windows': count, 'estimated_requests_without_retries': count,
                'max_windows_per_batch': batch, 'daily_requests_remaining': remaining,
                'estimated_batches': math.ceil(count / batch) if batch else None,
                'billing_mode': self.settings.billing_mode, 'rmb_cost': None,
                'sampling_fps': profile.sampling_fps, 'window_seconds': profile.window_seconds,
                'profile': 'long' if is_long else 'short',
                'sampling_mode': 'on_demand_segments' if is_long else 'full_clip',
                'enhanced_safety_sampling': self.settings.enhanced_safety_sampling and not is_long,
                'activity_filter_enabled': self.filter_enabled(video),
                'hybrid_safety_enabled': bool(self.safety.enabled and mode != 'objects'),
                'safety_review_output_tokens': REVIEW_TOKENS if self.safety.enabled and mode != 'objects' else 0,
                'estimated_requests_excludes_safety_reviews': bool(self.safety.enabled and mode != 'objects'),
                'activity_filter_review_seconds': self.settings.activity_filter_review_seconds,
                'activity_filter_person_review_seconds': self.settings.activity_filter_person_review_seconds,
                'estimated_requests_is_upper_bound': self.filter_enabled(video),
                'estimated_sampled_frames': math.ceil(video['duration_ms']/1000*profile.sampling_fps)}

    def job_snapshot(self, job_id):
        with self.lock:
            job = self.db.job(job_id)
            signal = self.pause_requests.get(job['video_id'])
            if job['status'] in {'queued', 'running'} and signal and signal.is_set():
                job.update(pause_requested=True, stage='正在暂停，等待当前处理完成并保存进度')
            return job

    def pause(self, video_id):
        with self.lock:
            self.db.video(video_id)
            job = self.db.latest_job(video_id)
            if job and job['status'] == 'paused':
                return job
            if not job or video_id not in self.active or job['status'] not in {'queued', 'running'}:
                raise AppError('NOT_RUNNING', '当前没有正在分析的任务。', 409)
            self.pause_requests[video_id].set()
            return {**job, 'pause_requested': True, 'stage': '正在暂停，等待当前处理完成并保存进度'}

    def start(self, video_id, resume=False, call_limit=None, auto_continue=True, mode='all'):
        if self.stopping.is_set():
            raise AppError("SHUTTING_DOWN", "服务正在关闭，请重新启动后分析。", 503)
        if mode not in {'all', 'safety', 'objects'}:
            raise AppError('ANALYSIS_MODE', '请选择安全检测或物品追踪。', 400)
        previous = self.db.latest_job(video_id) if resume else None
        if previous:
            mode = previous.get('analysis_mode', 'all')
        if mode == 'safety' and not self.safety.enabled:
            raise AppError('SAFETY_REVIEW_DISABLED', '安全检测需要启用本地小模型与大模型复核。', 503)
        self.provider.ready()
        video = self.db.video(video_id)
        if self.plan(video_id, mode)['max_windows_per_batch'] < 1:
            raise AppError('WINDOW_BUDGET', '当前单个分析窗口超过帧数或输出预算，请调整窗口大小或批预算。')
        if not self.provider.is_mock and self.db.usage_summary()['requests'] >= self.settings.max_daily_calls:
            raise AppError('REQUEST_LIMIT', '今日模型请求额度已用完；已保存的进度和证据不会丢失。请核对预算后调整配置或次日继续。', 429)
        with self.lock:
            if video_id in self.active:
                raise AppError("ALREADY_ANALYZING", "该视频正在分析，请等待当前任务完成。", 409)
            if len(self.active) >= self.settings.max_concurrency:
                raise AppError("BUSY", "已达到分析并发上限，请稍后再试。", 429, True)
            previous = self.db.latest_job(video_id) if resume else None
            if resume:
                if not previous or previous['status'] == 'completed':
                    raise AppError('NOT_RESUMABLE', '没有可继续的未完成任务。', 409)
                checkpoint = self.settings.data_dir / 'runs' / previous['id'] / 'checkpoint.json'
                if not checkpoint.exists():
                    raise AppError('NO_CHECKPOINT', '此旧任务没有分窗检查点，请重新分析。', 409)
                saved = json.loads(checkpoint.read_text(encoding='utf8'))
                if saved['signature'] != self.signature(video, mode) or saved['video_sha256'] != video['sha256']:
                    raise AppError('CHECKPOINT_CONFIG', '模型或抽帧设置已改变，请恢复原配置继续，或重新分析。', 409)
                if saved.get('prompt_sha256') != self.prompt.sha256:
                    raise AppError('CHECKPOINT_CONFIG', '提示词已改变或旧任务缺少提示词指纹。请从头分析，避免把不同提示词的结果静默混用。', 409)
                job = {**previous, 'status': 'queued', 'stage': '从检查点继续', 'error': None, 'batch': previous.get('batch', 1) + 1}
            else:
                job = {"id": "j_" + uuid4().hex, "video_id": video_id, "status": "queued", "stage": "等待抽帧",
                   "progress": 0, "completed_windows": 0, "failed_windows": [], "calls": 0, "frame_references": 0,
                   "is_mock": self.provider.is_mock, "provider": self.provider.name, "model": self.provider.model,
                   "created_at": datetime.now(timezone.utc).isoformat(), "error": None, 'batch': 1,
                   'usage': {'prompt_tokens': 0, 'completion_tokens': 0}, 'retries': 0,
                   'prompt_version': self.prompt.version, 'prompt_sha256': self.prompt.sha256}
            job['analysis_mode'] = mode
            job.update(batch_calls=0, batch_frame_references=0, batch_output_budget=0, resumable=False,
                       batch_call_limit=min(self.settings.max_calls, call_limit or self.settings.max_calls),
                       auto_continue=auto_continue and call_limit is None, pause_requested=False, pause_reason=None)
            self.active.add(video_id)
            self.pause_requests[video_id] = threading.Event()
            try:
                self.db.save_job(job)
                self.pool.submit(self.run, video, job)
            except BaseException:
                self.active.remove(video_id)
                self.pause_requests.pop(video_id, None)
                raise
        return job.copy()

    def run(self, video, job):
        started = time.monotonic()
        run_dir = self.settings.data_dir / 'runs' / job['id']
        checkpoint = run_dir / 'checkpoint.json'
        mode = job.get('analysis_mode', 'all')
        safety_enabled = self.safety.enabled and mode != 'objects'
        objects_enabled = mode != 'safety'
        profile = self.profile(video, mode)
        try:
            signature = self.signature(video, mode)
            run_dir.mkdir(parents=True, exist_ok=True)
            job.update(status="running", stage="读取原始时间戳并抽取关键帧")
            self.db.save_job(job)
            if checkpoint.exists():
                saved = json.loads(checkpoint.read_text(encoding='utf8'))
                if saved['signature'] != signature or saved.get('prompt_sha256') != self.prompt.sha256 or saved['video_sha256'] != video['sha256']:
                    raise AppError('CHECKPOINT_CONFIG', '检查点不属于当前视频或分析配置，未混用旧结果。', 409)
                frames, entities, evidence, events, processed = [saved[k] for k in ['frames', 'entities', 'evidence', 'events', 'processed']]
                segmented = saved.get('media_mode') == 'segmented'
            else:
                saved = {}
                segmented = video['duration_ms'] > self.settings.long_video_threshold_seconds * 1000
                frames = [] if segmented or safety_enabled or mode != 'all' else extract_frames(video, run_dir / 'frames', profile)
                entities, evidence, events, processed = {}, [], [], []
                if mode != 'all' and video.get('active_run'):
                    retained = self.retain_other_mode(video, job, run_dir, mode)
                    if retained:
                        retained_frames, entities, evidence, events = retained
                        frames = retained_frames + frames
            object_frames_ready = saved.get('object_frames_ready', (bool(frames) and not safety_enabled and mode == 'all') or segmented)
            object_frame_ids = set(saved.get('object_frame_ids', [f['id'] for f in frames] if object_frames_ready else []))
            object_frames = [f for f in frames if f['id'] in object_frame_ids]
            safety_state = saved.get('safety') or {
                'enabled': bool(safety_enabled), 'scan_complete': False,
                'candidates': [], 'reviews': {}, 'processed': []}
            review_cache = run_dir / 'safety-reviews.json'
            if safety_enabled and review_cache.exists():
                cached_safety = json.loads(review_cache.read_text(encoding='utf8'))
                if (cached_safety.get('signature') != signature or cached_safety.get('video_sha256') != video['sha256']
                        or cached_safety.get('prompt_sha256') != self.prompt.sha256):
                    raise AppError('CACHE_MISMATCH', '安全复核缓存不属于当前分析配置，未采用。', 409)
                # The sidecar can be ahead of the checkpoint after a crash. Reuse paid
                # responses, but only the checkpoint knows which events were committed.
                safety_state.update(cached_safety['safety'])
                accounting = cached_safety.get('accounting', {})
                for key in ('calls', 'frame_references', 'retries'):
                    job[key] = max(job.get(key, 0), accounting.get(key, 0))
                for key in ('prompt_tokens', 'completion_tokens'):
                    job['usage'][key] = max(job['usage'].get(key, 0), accounting.get('usage', {}).get(key, 0))
            job['safety'] = safety_state
            protected_ids = set()
            if video.get('active_run') == job['id']:
                previous_document, _ = self.db.raw_memory(video['id'])
                protected_ids = {e['id'] for e in previous_document['events']}
            if segmented or not object_frames_ready:
                chunks = []
                for start in range(0, video['duration_ms'], profile.window_stride_seconds*1000):
                    end = min(video['duration_ms'], start+profile.window_seconds*1000)
                    chunks.append({'start_ms': start, 'end_ms': end})
                    if end == video['duration_ms']:
                        break
            else:
                chunks = list(windows(object_frames, video["duration_ms"], profile.window_seconds * 1000, profile.window_stride_seconds * 1000))
            output_limit = self.settings.window_output_tokens
            if not objects_enabled:
                chunks = []
            job['total_windows'] = len(chunks)
            split_windows = set(saved.get('split_windows', [])) if checkpoint.exists() else set()
            unit_processed = set(saved.get('unit_processed', [])) if checkpoint.exists() else set()
            gate = ActivityGate(self.settings, saved.get('activity_gate')) if objects_enabled and self.filter_enabled(video) else None
            decisions = saved.get('activity_decisions', {}) if checkpoint.exists() else {}
            failures = {item['index']: item for item in job.get('failed_windows', []) if 'index' in item}
            # Finish unattempted windows before explicitly retrying failed ones.
            remaining = sorted((index for index in range(len(chunks)) if index not in processed), key=lambda index: (index in failures, index))
            paused = False

            def persist():
                safety_state['counts'] = {verdict: sum(r.get('verdict') == verdict for r in safety_state['reviews'].values())
                                          for verdict in ('confirmed', 'rejected', 'uncertain', 'unavailable')}
                safety_state['counts'].update(candidates=len(safety_state['candidates']),
                                               pending=len(safety_state['candidates'])-len(safety_state['processed']))
                safety_state.update({key: safety_state['counts'][key] for key in ('confirmed', 'rejected', 'uncertain', 'unavailable')})
                safety_state['candidate_count'] = len(safety_state['candidates'])
                safety_state['reviewed'] = sum(safety_state['counts'][key] for key in ('confirmed', 'rejected', 'uncertain'))

                saved = {'signature': signature, 'video_sha256': video['sha256'], 'frames': frames, 'prompt_sha256': self.prompt.sha256,
                         'entities': entities, 'evidence': evidence, 'events': events, 'processed': processed,
                         'media_mode': 'segmented' if segmented else 'full', 'object_frame_ids': sorted(object_frame_ids), 'object_frames_ready': object_frames_ready, 'safety': safety_state,
                         'split_windows': sorted(split_windows), 'unit_processed': sorted(unit_processed)}
                if gate:
                    saved.update(activity_gate=gate.state(), activity_decisions=decisions)
                if safety_enabled:
                    # Do not pre-create run_dir/safety: the scanner owns that directory.
                    sidecar = {'signature': signature, 'video_sha256': video['sha256'], 'prompt_sha256': self.prompt.sha256,
                               'safety': {k: v for k, v in safety_state.items() if k != 'processed'},
                               'accounting': {k: job[k] for k in ('calls', 'frame_references', 'retries', 'usage')}}
                    tmp_review = review_cache.with_suffix('.tmp')
                    tmp_review.write_text(json.dumps(sidecar, ensure_ascii=False), encoding='utf8')
                    tmp_review.replace(review_cache)
                temporary = checkpoint.with_suffix('.tmp')
                temporary.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf8')
                temporary.replace(checkpoint)
                job['completed_windows'] = len(processed)
                job['failed_windows'] = list(failures.values())
                job['pending_windows'] = len(chunks) - len(processed)
                job['resumable'] = bool(job['pending_windows'] or (safety_enabled and (
                    not safety_state['scan_complete'] or len(safety_state['processed']) < len(safety_state['candidates']))))
                object_progress = len(processed)/len(chunks)*100 if chunks else 100
                scan_progress = safety_state.get('scan_progress', 0)
                review_total = len(safety_state['candidates'])
                review_progress = len(safety_state['processed'])/review_total*100 if review_total else (100 if safety_state['scan_complete'] else 0)
                safety_progress = min(99, scan_progress*.8 + review_progress*.2) if not safety_state['scan_complete'] else review_progress
                job['progress'] = round(safety_progress if mode == 'safety' else ((safety_progress+object_progress)/2 if safety_enabled else object_progress))
                skipped = {i for i in processed if decisions.get(str(i), {}).get('skip')}
                job['analyzed_ranges'] = [{'start_ms': chunks[i]['start_ms'], 'end_ms': chunks[i]['end_ms']} for i in sorted(processed) if i not in skipped]
                if gate:
                    job['skipped_static_ranges'] = [{**chunks[i], **decisions[str(i)]} for i in sorted(skipped)]
                    job['activity_filter'] = {'enabled': True, 'version': FILTER_VERSION,
                        'screened_windows': len(decisions), 'skipped_windows': len(skipped),
                        'model_analyzed_windows': len(processed)-len(skipped),
                        'review_seconds': self.settings.activity_filter_review_seconds,
                        'person_review_seconds': self.settings.activity_filter_person_review_seconds,
                        'risk_followup': gate.risk_hold}
                job['split_windows'] = sorted(split_windows)
                if processed or safety_state['processed'] or (mode == 'safety' and safety_state['scan_complete']):
                    document = self.document(video, frames, entities, evidence, events, protected_ids)
                    validate_memory(document)
                    self.db.save_memory(job['id'], document, job=job, absence_seconds=self.settings.absence_alert_seconds)
                # Publish terminal status together with releasing the active slot in finally.
                # Otherwise a client can see "paused" but its immediate resume still gets 409.
                if job['status'] == 'running':
                    self.db.save_job(job)

            def budget_available(window, tokens=output_limit):
                # Estimate before sampling; recheck the actual frame count before every request.
                frame_count = len(window['frames']) if 'frames' in window else math.ceil((window['end_ms']-window['start_ms'])/1000*profile.sampling_fps)
                return (job['batch_calls'] < job['batch_call_limit']
                        and job['batch_frame_references'] + frame_count <= self.settings.max_frame_references
                        and job['batch_output_budget'] + tokens <= self.settings.max_output_tokens)

            def check_control():
                if self.stopping.is_set():
                    raise AppError('INTERRUPTED', '服务关闭，已保存进度。', 503, True)
                if self.pause_requests[video['id']].is_set():
                    raise AppError('MANUAL_PAUSE', '已手动暂停，点击继续分析可从已保存进度恢复。', 429)

            def prepare_request(window, tokens=output_limit):
                check_control()
                # The provider still reserves each real request atomically across all jobs.
                if not self.provider.is_mock and self.db.usage_summary()['requests'] >= self.settings.max_daily_calls:
                    raise AppError('REQUEST_LIMIT', '今日模型请求额度已用完，进度已保存；次日可继续分析。', 429)
                if not budget_available(window, tokens):
                    if not job.get('auto_continue', False):
                        raise AppError('BATCH_LIMIT', '本批预算已用完，进度已保存，点击继续分析可恢复。', 429)
                    if job['batch_calls']:
                        persist()
                        job.update(batch=job['batch']+1, batch_calls=0, batch_frame_references=0,
                                   batch_output_budget=0, stage='自动接续下一批分析')
                        self.db.save_job(job)
                    if not budget_available(window, tokens):
                        raise AppError('WINDOW_BUDGET', '单个窗口超过本批预算，请调整窗口或预算配置后继续。')

            def process_candidate(candidate):
                nonlocal frames, entities, evidence, events
                if candidate['id'] not in {c['id'] for c in safety_state['candidates']}:
                    safety_state['candidates'].append(candidate)
                persist()
                check_control()
                identifier = candidate['id']
                if identifier in safety_state['processed']:
                    return
                candidate_frames = [{**f, 'kind': f.get('kind', 'original_frame')} for f in candidate['frames']]
                lookup = {f['id']: f for f in frames}
                for frame in candidate_frames:
                    previous = lookup.get(frame['id'])
                    if previous and any(previous[k] != frame[k] for k in ('pts_ms', 'kind', 'path')):
                        raise AppError('FORGED_FRAME', '安全候选与已保存原帧不一致。', 422)
                # Check source PTS, original-frame kind and uniqueness before paying.
                validate_memory(self.document(video, candidate_frames, {}, [], []))
                result = safety_state['reviews'].get(identifier)
                if result is None:
                    cloud_request = len(candidate_frames) >= 2
                    if cloud_request:
                        prepare_request({'frames': candidate_frames}, REVIEW_TOKENS)
                        job['calls'] += 1
                        job['frame_references'] += len(candidate_frames)
                        job['batch_calls'] += 1
                        job['batch_frame_references'] += len(candidate_frames)
                        job['batch_output_budget'] += REVIEW_TOKENS
                        # A crash/timeout after dispatch has an unknown paid outcome.
                        # Never automatically pay for this candidate again on resume.
                        safety_state['reviews'][identifier] = {
                            'candidate_id': identifier, 'event_type': candidate['event_type'],
                            'verdict': 'unavailable', 'reason': '复核请求已开始但结果未保存，未自动重复付费。',
                            'frame_ids': [], 'model': self.settings.review_model, 'interrupted': True}
                        persist()
                    job['stage'] = f"安全候选视觉复核 {len(safety_state['processed'])+1}/{len(safety_state['candidates'])}"
                    self.db.save_job(job)
                    try:
                        request_job = job['id'] if job['batch'] == 1 else f"{job['id']}-b{job['batch']}"
                        result = self.safety.review(candidate, video['id'], request_job)
                    except AppError as exc:
                        if exc.code in {'BATCH_LIMIT', 'REQUEST_LIMIT', 'COST_LIMIT', 'MANUAL_PAUSE', 'INTERRUPTED'}:
                            # No provider request is dispatched on these preflight failures.
                            safety_state['reviews'].pop(identifier, None)
                        else:
                            safety_state['reviews'][identifier] = {
                                'candidate_id': identifier, 'event_type': candidate['event_type'],
                                'verdict': 'unavailable', 'reason': exc.message, 'frame_ids': [],
                                'model': self.settings.review_model, 'error': exc.public()}
                        persist()
                        raise
                    safety_state['reviews'][identifier] = result
                    usage = result.get('usage', {})
                    usage = usage.get('usage', usage)
                    for key in ('prompt_tokens', 'completion_tokens'):
                        job['usage'][key] += usage.get(key, 0) or 0
                    # Persist the response independently of normalization/controls.
                    persist()
                if (result.get('candidate_id') != identifier or result.get('event_type') != candidate['event_type']
                        or result.get('verdict') not in {'confirmed', 'rejected', 'uncertain', 'unavailable'}):
                    raise AppError('SAFETY_REVIEW_INVALID', '安全复核结果与候选不匹配，未写入。', 422)
                extraction = self.safety.extraction(candidate, result) if result['verdict'] == 'confirmed' else None
                if extraction is not None:
                    canonical = run_dir / 'frames'
                    canonical.mkdir(exist_ok=True)
                    for frame in candidate_frames:
                        target = canonical / f"{frame['id']}.jpg"
                        shutil.copyfile(frame['path'], target)
                        frame['path'] = str(target)
                    combined = dict(lookup)
                    combined.update({f['id']: f for f in candidate_frames})
                    combined_frames = sorted(combined.values(), key=lambda f: (f['pts_ms'], f['id']))
                    window = {'start_ms': 0, 'end_ms': video['duration_ms'], 'frames': candidate_frames}
                    new_entities, new_evidence, new_events = normalize(extraction, video['id'], window, entities)
                    document = self.document(video, combined_frames, new_entities, evidence+new_evidence, events+new_events)
                    validate_memory(document, combined)
                    frames, entities = combined_frames, new_entities
                    evidence, events = evidence+new_evidence, events+new_events
                    result['event_ids'] = [e['id'] for e in new_events]
                safety_state['processed'].append(identifier)
                persist()

            def process_safety():
                if not safety_enabled:
                    return
                # Cached paid responses are processed before resuming the scanner.
                for candidate in list(safety_state['candidates']):
                    process_candidate(candidate)
                if not safety_state['scan_complete']:
                    check_control()
                    job['stage'] = '正在加载本地安全模型'
                    self.db.save_job(job)

                    def scan_progress(snapshot):
                        safety_state.update(processed_frames=snapshot.get('frames', 0),
                                            total_frames=snapshot.get('total_frames', 0),
                                            scan_progress=snapshot.get('progress', 0))
                        job['stage'] = ('本地安全扫描 ' + str(safety_state['processed_frames']) +
                                        '/' + str(safety_state['total_frames']) + ' 帧')
                        persist()

                    candidates = self.safety.scan_video(video, run_dir, check_control=check_control,
                                                         on_progress=scan_progress, on_candidate=process_candidate)
                    if len({c['id'] for c in candidates}) != len(candidates):
                        raise AppError('SAFETY_CANDIDATE_INVALID', '安全候选ID重复，未写入。', 422)
                    known = {candidate['id'] for candidate in safety_state['candidates']}
                    safety_state['candidates'].extend(candidate for candidate in candidates if candidate['id'] not in known)
                    safety_state.update(scan_complete=True, scan_progress=100)
                    persist()
                    for candidate in candidates:
                        process_candidate(candidate)
                check_control()

            def process_unit(index, unit, part, recovery):
                nonlocal frames, entities, evidence, events
                window = dict(unit)
                suffix = '' if part is None else f'-part-{part}'
                cache = run_dir / f'window-{index:05d}{suffix}.json'
                cached = json.loads(cache.read_text(encoding='utf8')) if cache.exists() else None
                used_recovery = recovery or bool(cached and cached.get('recovery'))
                check_control()
                if not cached:
                    prepare_request(window)
                if segmented:
                    if cached:
                        lookup = {f['id']: f for f in frames}
                        if any(fid not in lookup for fid in cached['frame_ids']):
                            raise AppError('CACHE_MISMATCH', '缓存证据缺失，未采用。')
                        window['frames'] = [lookup[fid] for fid in cached['frame_ids']]
                    else:
                        job['stage'] = f"按需抽帧 {index+1}/{len(chunks)}" + (f" · 子窗口{part+1}" if part is not None else '')
                        self.db.save_job(job)
                        sampled = extract_segment_frames(video, run_dir/'frames', profile, window['start_ms'], window['end_ms'], profile.sampling_fps)
                        lookup = {f['id']: f for f in frames}
                        lookup.update({f['id']: f for f in sampled})
                        if len(lookup) > profile.max_sampled_frames:
                            raise AppError('FRAME_LIMIT', '累计抽帧超出配置上限，已保存进度。', 429)
                        frames = sorted(lookup.values(), key=lambda f: f['pts_ms'])
                        window['frames'] = sampled
                elif part is not None:
                    window['frames'] = [f for f in object_frames if window['start_ms'] <= f['pts_ms'] < window['end_ms']]
                if cached and (cached['signature'] != signature or cached['frame_ids'] != [f['id'] for f in window['frames']] or cached.get('prompt_sha256') != self.prompt.sha256):
                    raise AppError('CACHE_MISMATCH', '缓存不属于当前窗口/提示词，未采用。')
                if cached:
                    result = ProviderResult(Extraction.model_validate(cached['extraction']), {})
                    job['cached_windows'] = job.get('cached_windows', 0)+1
                else:
                    for attempt in range(self.settings.max_retries+1):
                        prepare_request(window)
                        job['calls'] += 1
                        job['frame_references'] += len(window['frames'])
                        job['batch_calls'] += 1
                        job['batch_frame_references'] += len(window['frames'])
                        job['batch_output_budget'] += output_limit
                        job['stage'] = f"视觉识别 {index+1}/{len(chunks)}（已完成{len(processed)}）" + (' · 失败窗口恢复' if used_recovery else '')
                        self.db.save_job(job)
                        try:
                            recent = list(dict.fromkeys([e['subject_id'] for e in reversed(events)] + list(reversed(entities))))[:80]
                            request_job = job['id'] if job['batch'] == 1 else f"{job['id']}-b{job['batch']}"
                            kwargs = {'recovery': True} if used_recovery else {}
                            if mode == 'objects' and isinstance(self.provider, QwenProvider):
                                kwargs['object_only'] = True
                            result = self.provider.extract(window=window, entities=[entities[key] for key in recent], video=video,
                                                           job_id=request_job, output_tokens=output_limit, **kwargs)
                            break
                        except AppError as error:
                            if not (error.retryable and attempt < self.settings.max_retries
                                    and (budget_available(window) or job.get('auto_continue', False))
                                    and not self.stopping.is_set()):
                                raise
                            job['retries'] += 1
                            if error.code == 'MODEL_OUTPUT_INVALID':
                                used_recovery = True
                            time.sleep(.5)
                    usage = result.usage.get('usage', result.usage)
                    for key in ['prompt_tokens', 'completion_tokens']:
                        job['usage'][key] += usage.get(key, 0) or 0
                extraction = result.extraction
                if self.safety.enabled or mode == 'objects':
                    # Enforce the split before normalize, including malformed references
                    # on forbidden events; prompts alone cannot enforce this boundary.
                    extraction = extraction.model_copy(update={'events': [
                        item for item in extraction.events if item.action not in SAFETY_RULES]})
                new_entities, new_evidence, new_events = normalize(extraction, video['id'], window, entities)
                candidate = self.document(video, frames, new_entities, evidence+new_evidence, events+new_events, protected_ids)
                validate_memory(candidate, {f['id']: f for f in frames})
                if not cached:
                    cache.write_text(json.dumps({'signature': signature, 'prompt_sha256': self.prompt.sha256,
                        'frame_ids': [f['id'] for f in window['frames']], 'extraction': result.extraction.model_dump(),
                        'request_prompt_version': result.usage.get('prompt_version'), 'request_prompt_sha256': result.usage.get('prompt_sha256'),
                        'format_repairs': result.usage.get('format_repairs', []), 'recovery': used_recovery,
                        'start_ms': window['start_ms'], 'end_ms': window['end_ms']}, ensure_ascii=False), encoding='utf8')
                if used_recovery or result.usage.get('format_repairs'):
                    job.setdefault('window_recoveries', {})[f'{index}{suffix}'] = {
                        'start_ms': window['start_ms'], 'end_ms': window['end_ms'],
                        'request_prompt_version': result.usage.get('prompt_version') or (cached or {}).get('request_prompt_version'),
                        'request_prompt_sha256': result.usage.get('prompt_sha256') or (cached or {}).get('request_prompt_sha256'),
                        'format_repairs': result.usage.get('format_repairs', []), 'cached': bool(cached)}
                entities, evidence, events = new_entities, evidence+new_evidence, events+new_events

            persist()
            try:
                process_safety()
                check_control()
                if objects_enabled and not segmented and not object_frames_ready:
                    job['stage'] = '抽取物品追踪关键帧' if mode == 'objects' else '安全复核已保存，抽取物品追踪关键帧'
                    self.db.save_job(job)
                    sampled = extract_frames(video, run_dir / 'frames', profile)
                    lookup = {f['id']: f for f in frames}
                    for frame in sampled:
                        if frame['id'] in lookup and lookup[frame['id']] != frame:
                            raise AppError('FORGED_FRAME', '物品帧与已保存安全原帧冲突。', 422)
                        lookup[frame['id']] = frame
                    frames = sorted(lookup.values(), key=lambda f: (f['pts_ms'], f['id']))
                    object_frames, object_frame_ids = sampled, {f['id'] for f in sampled}
                    object_frames_ready = True
                    chunks = list(windows(object_frames, video['duration_ms'], profile.window_seconds*1000, profile.window_stride_seconds*1000))
                    job['total_windows'] = len(chunks)
                    remaining = [i for i in range(len(chunks)) if i not in processed]
                    persist()
            except AppError as exc:
                job['error'] = exc.public()
                safety_state['error'] = exc.public()
                paused = True
                persist()
            split_errors = {'MODEL_TRUNCATED', 'MODEL_OUTPUT_INVALID'}
            for index in remaining if not paused else []:
                recovery = index in failures or index in split_windows
                if gate and not recovery:
                    try:
                        check_control()
                        if str(index) not in decisions:
                            parent = chunks[index]
                            job['stage'] = f"本地画面变化筛查 {index+1}/{len(chunks)}"
                            self.db.save_job(job)
                            gate_dir = run_dir/'activity-frames'
                            sampled = []
                            try:
                                sampled = extract_segment_frames(video, gate_dir, profile, parent['start_ms'], parent['end_ms'],
                                                                 self.settings.activity_filter_fps, max_width=320)
                                decisions[str(index)] = gate.evaluate(sampled, parent['start_ms'], parent['end_ms'])
                            except (AppError, OSError, ValueError):
                                # Screening failure must fall back to semantic analysis, never a skip.
                                decisions[str(index)] = {'skip': False, 'reason': 'screening_failed', 'max_change_ratio': None}
                            finally:
                                for frame in sampled:
                                    path = Path(frame['path'])
                                    if path.resolve().parent == gate_dir.resolve():
                                        path.unlink(missing_ok=True)
                        check_control()
                        if decisions[str(index)]['skip']:
                            processed.append(index)
                            failures.pop(index, None)
                            persist()
                            continue
                        persist()
                    except AppError as exc:
                        job['error'] = exc.public()
                        paused = True
                        persist()
                        break
                while True:
                    parent = chunks[index]
                    if index in split_windows:
                        units = [{'start_ms': parent['start_ms'], 'end_ms': min(parent['end_ms'], parent['start_ms']+20000)},
                                 {'start_ms': parent['start_ms']+18000, 'end_ms': parent['end_ms']}]
                    else:
                        units = [parent]
                    try:
                        for part, unit in enumerate(units):
                            child = part if index in split_windows else None
                            key = f'{index}.part{part}' if child is not None else str(index)
                            if key in unit_processed:
                                continue
                            process_unit(index, unit, child, recovery)
                            unit_processed.add(key)
                            persist()
                        if gate:
                            gate.analyzed(parent['start_ms'], parent['end_ms'],
                                          [e for e in events if parent['start_ms'] <= e['start_ms'] and e['end_ms'] <= parent['end_ms']], entities)
                        processed.append(index)
                        failures.pop(index, None)
                        persist()
                        break
                    except AppError as exc:
                        job['error'] = exc.public()
                        if exc.code not in {'BATCH_LIMIT', 'MANUAL_PAUSE', 'REQUEST_LIMIT', 'INTERRUPTED', 'COST_LIMIT'}:
                            failures[index] = {'index': index, 'start_ms': parent['start_ms'], 'end_ms': parent['end_ms'], 'error': exc.public()}
                        if recovery and index not in split_windows and exc.code in split_errors and parent['end_ms']-parent['start_ms'] > 20000:
                            split_windows.add(index)
                            persist()
                            continue
                        if exc.status in {429, 503, 504}:
                            paused = True
                        persist()
                        break
                if paused:
                    break
            if paused:
                code = (job.get('error') or {}).get('code')
                reason = {'MANUAL_PAUSE': 'manual', 'BATCH_LIMIT': 'batch_limit', 'REQUEST_LIMIT': 'daily_limit',
                          'INTERRUPTED': 'shutdown'}.get(code, 'error')
                label = {'manual': '已手动暂停', 'daily_limit': '今日请求额度已用完'}.get(reason, '分析已暂停')
                job.update(status='paused', pause_reason=reason, stage=f"{label} · 已保存 {len(processed)}/{len(chunks)} 窗口")
                if reason == 'manual':
                    job['error'] = None
            elif not processed and not (mode == 'safety' and safety_state['scan_complete']):
                job.update(status="failed", stage="分析失败，未生成有效记忆", progress=100)
            else:
                safety_gaps = [r for r in safety_state['reviews'].values() if r.get('verdict') == 'unavailable']
                incomplete = bool(failures or safety_gaps)
                stage = '部分完成，存在未复核安全候选' if safety_gaps else ('部分完成，存在证据缺口' if failures else '分析完成')
                job.update(status='partial' if incomplete else 'completed', stage=stage)
                if safety_gaps:
                    job['error'] = safety_gaps[0].get('error') or {
                        'code': 'SAFETY_REVIEW_UNAVAILABLE', 'message': '存在未获得可用复核结果的安全候选，未作为确认报警；未自动重复付费。',
                        'retryable': False}
                    safety_state['error'] = job['error']
                elif not failures:
                    job['error'] = None
                    safety_state.pop('error', None)
            persist()
        except BaseException as exc:
            if not isinstance(exc, AppError):
                logging.getLogger(__name__).exception('Unexpected analysis failure for job %s', job['id'])
            error = exc.public() if isinstance(exc, AppError) else {"code": "ANALYSIS_FAILED", "message": "分析未完成，请检查媒体工具、磁盘空间或模型配置后重试。", "retryable": True}
            job.update(status="failed", stage="分析失败", progress=100, error=error)
        finally:
            job['batch_elapsed_ms'] = round((time.monotonic() - started) * 1000)
            job["elapsed_ms"] = job.get('elapsed_ms', 0) + job['batch_elapsed_ms']
            with self.lock:
                job['pause_requested'] = False
                self.db.save_job(job)
                self.active.discard(video["id"])
                self.pause_requests.pop(video['id'], None)

    def retain_other_mode(self, video, job, run_dir, mode):
        document, previous = self.db.memory(video['id'])
        if previous.get('is_mock') != self.provider.is_mock:
            return None
        checkpoint = self.settings.data_dir / 'runs' / previous['id'] / 'checkpoint.json'
        if not checkpoint.is_file():
            raise AppError('RETAINED_EVIDENCE_MISSING', '已有分析缺少证据检查点；原结果已保留，请先检查旧任务。', 409)
        saved = json.loads(checkpoint.read_text(encoding='utf8'))
        if saved.get('video_sha256') != video['sha256']:
            raise AppError('CACHE_MISMATCH', '已有分析不属于当前原片，未合并。', 409)
        kept = [event for event in document['events']
                if (event['action'] not in SAFETY_RULES) == (mode == 'safety')]
        evidence_ids = {ref for event in kept for ref in event['evidence_ids']}
        evidence = [item for item in document['evidence'] if item['id'] in evidence_ids]
        frame_ids = {fid for item in evidence for fid in item['frame_ids']}
        entity_ids = {event[key] for event in kept for key in ('subject_id', 'target_id') if event.get(key)}
        entities = {entity['id']: entity for entity in document['entities'] if entity['id'] in entity_ids}
        frames = [dict(frame) for frame in saved['frames'] if frame['id'] in frame_ids]
        if {frame['id'] for frame in frames} != frame_ids:
            raise AppError('RETAINED_EVIDENCE_MISSING', '已有分析的原帧不完整，未覆盖原结果。', 409)
        canonical = run_dir / 'frames'
        canonical.mkdir(exist_ok=True)
        for frame in frames:
            source = Path(frame['path']).resolve()
            if not source.is_relative_to((self.settings.data_dir / 'runs').resolve()) or not source.is_file():
                raise AppError('RETAINED_EVIDENCE_MISSING', '已有分析证据路径无效，未覆盖原结果。', 409)
            target = canonical / f"{frame['id']}.jpg"
            shutil.copyfile(source, target)
            frame['path'] = str(target)
        event_mapping = {event['id']: 'ev_' + uuid4().hex[:16] for event in kept}
        kept = [{**event, 'id': event_mapping[event['id']]} for event in kept]
        job['retained_analysis'] = {'event_id_mapping': event_mapping, 'run_id': previous['id'], 'analysis_mode': previous.get('analysis_mode', 'all'),
                                    'event_ids': [event['id'] for event in kept],
                                    'model': previous.get('model'), 'provider': previous.get('provider')}
        return frames, entities, evidence, kept

    def document(self, video, frames, entities, evidence, events, protected_ids=None):
        # Preserve already validated events when an earlier failed window is recovered later.
        # Retain all raw candidates and evidence; display grouping handles repetitions.
        return {"schema_version": "0.1", "fixture_kind": "manual_design_example" if self.provider.is_mock else "model_analysis",
                "video_id": video["id"], "video_duration_ms": video["duration_ms"],
                "model_run": {"provider": self.provider.name, "model": self.provider.model, "prompt_version": self.prompt.version, "input_video_sha256": video["sha256"]},
                "entities": list(entities.values()), "frames": [{k: f[k] for k in ["id", "pts_ms", "kind"]} for f in frames],
                "evidence": evidence, "events": sorted(events, key=lambda e: (e['start_ms'], e['end_ms']))}
