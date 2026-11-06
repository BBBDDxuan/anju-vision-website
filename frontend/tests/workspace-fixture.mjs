import { readFile } from 'node:fs/promises';
import path from 'node:path';

// Browser-only fixture, always marked is_mock. Never written to the real backend.
export async function installWorkspaceFixture(page, options = {}) {
  const source = JSON.parse(await readFile(path.resolve('public/demo/demo-public-fixture.json'), 'utf8'));
  const bytes = await readFile(path.resolve('public/demo/videos/fall.mp4'));
  const state = { analyzed: true, ready: true, status: 'completed', autoComplete: true, queryDelay: 0, queryError: false, unknown: false, empty: false, ...options };
  const calls = [];
  const ids = ['v_inspector_fixture', 'v_inspector_other'];
  const frames = source.cases[0].event.referencedFrames.map((frame, i) => ({ id: `fixture_frame_${i}`, pts_ms: frame.time_ms, url: frame.image }));
  // These intervals/events intentionally simulate UI state; media references
  // may change when the authoritative public model example is refreshed.
  const evidence = [{ id: 'fixture_evidence_fall', start_ms: 2300, end_ms: 6300, frame_ids: [frames[0].id, frames.at(-1).id] }, { id: 'fixture_evidence_second', start_ms: 4500, end_ms: 6300, frame_ids: [frames.at(-2).id, frames.at(-1).id] }];
  const events = [{ id: 'fixture_event_fall', start_ms: 2300, end_ms: 6300, subject_id: 'fixture_person', target_id: null, action: 'fall', epistemic_type: 'observed', evidence_ids: ['fixture_evidence_fall'], uncertainty_reasons: [], review_status: 'unreviewed', model_score: .86 }, { id: 'fixture_event_second', start_ms: 4500, end_ms: 6300, subject_id: 'fixture_person', target_id: null, action: 'help_gesture', epistemic_type: 'observed', evidence_ids: ['fixture_evidence_second'], uncertainty_reasons: [], review_status: 'unreviewed', model_score: .84 }];
  if (state.sharedEvidence) { events[1].evidence_ids = [evidence[0].id]; events[1].start_ms = 2300; }
  const entities = [{ id: 'fixture_person', label: '测试人物', entity_type: 'person', aliases: [], appearance: '仅供界面回归的测试对象', identity_status: 'confirmed' }];
  const job = id => ({ id: `j_${id}`, video_id: id, status: state.status, stage: state.status === 'running' ? 'safety_scan' : state.status, analysis_mode: 'safety', progress: state.status === 'running' ? 30 : 100, resumable: state.status === 'paused', is_mock: true, failed_windows: [], analyzed_ranges: [{ start_ms: 0, end_ms: 7000 }], safety: { processed_frames: 171, total_frames: 171, candidate_count: 2, reviewed: 2, confirmed: 2, rejected: 0, uncertain: 0, unavailable: 0 } });
  const videos = () => state.empty ? [] : ids.map((id, i) => ({ id, name: i ? '另一段测试录像.mp4' : '工作台联动测试录像.mp4', duration_ms: 8550, active_run: state.analyzed ? `j_${id}` : null, read_only_demo: false }));
  const memory = id => ({ entities, events: state.analyzed && id === ids[0] ? events : [], evidence: state.analyzed ? evidence : [], frames, job: job(id), is_mock: true, event_groups: [] });
  const reviews = { revision: 1, run_id: `j_${ids[0]}`, regions: [], entries: events.map(event => ({ original: event, status: 'unreviewed', machine: null, human: null, conflict: false, conflict_note: '' })), entities, busy: false, history: [] };
  await page.route('**/media/videos/v_inspector_*', route => {
    const range = route.request().headers().range?.match(/bytes=(\d+)-(\d*)/);
    if (!range) return route.fulfill({ body: bytes, contentType: 'video/mp4', headers: { 'Accept-Ranges': 'bytes' } });
    const start = Number(range[1]), end = range[2] ? Math.min(bytes.length - 1, Number(range[2])) : bytes.length - 1;
    return route.fulfill({ status: 206, body: bytes.subarray(start, end + 1), contentType: 'video/mp4', headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${bytes.length}`, 'Content-Length': String(end - start + 1) } });
  });
  await page.route('**/api/**', async route => {
    const request = route.request(); const url = new URL(request.url());
    calls.push({ path: url.pathname + url.search, method: request.method(), body: request.postDataJSON?.() });
    const send = json => route.fulfill({ json });
    if (url.pathname === '/api/health') return send({ analysis_ready: state.ready, is_mock: true, cloud_mode: false, model: 'UI fixture / 非真实推理', configuration_error: state.ready ? null : { code: 'MODEL_NOT_CONFIGURED', message: '测试：模型未配置。' }, limits: { max_video_seconds: null, max_upload_mb: null } });
    if (url.pathname === '/api/videos') return send({ videos: videos() });
    if (url.pathname === '/api/demos') return send({ demos: [] });
    const id = url.pathname.split('/')[3];
    if (ids.includes(id) && url.pathname === `/api/videos/${id}`) return send({ ...videos().find(video => video.id === id), latest_job: state.analyzed ? job(id) : null });
    if (url.pathname.endsWith('/analysis-plan')) return send({ analysis_mode: url.searchParams.get('mode'), total_windows: 3, max_windows_per_batch: 2, daily_requests_remaining: 10, estimated_batches: 2, sampling_fps: 2, window_seconds: 4 });
    if (url.pathname.endsWith('/analyze') || url.pathname.endsWith('/resume')) { state.analyzed = true; state.status = 'running'; return send(job(id)); }
    if (url.pathname.endsWith('/pause')) { state.status = 'paused'; return send(job(id)); }
    if (url.pathname.startsWith('/api/jobs/')) { if (state.autoComplete) state.status = 'completed'; return send(job(ids[0])); }
    if (url.pathname.endsWith('/events')) return send(memory(id));
    if (url.pathname.endsWith('/alerts')) return send({ alerts: [], evidence, frames, absence_alert_seconds: 900 });
    if (url.pathname.endsWith('/review')) return send(reviews);
    if (url.pathname.endsWith('/human')) {
      const body = request.postDataJSON(); const eventId = url.pathname.split('/').at(-2); const entry = reviews.entries.find(item => item.original.id === eventId);
      if (entry) { entry.human = { decision: body.decision, note: body.note }; entry.status = `human_${body.decision}`; }
      reviews.revision++; return send(reviews);
    }
    if (url.pathname.endsWith('/regions')) { reviews.regions = request.postDataJSON().regions; reviews.revision++; return send(reviews); }
    if (url.pathname.endsWith('/replay')) return send({ entity: entities[0], entries: events.map(event => ({ ...event, event, unknowns: [] })), unknowns: ['UI 测试数据，不是实际追踪结论。'] });
    if (url.pathname.endsWith('/query')) {
      if (state.queryDelay) await new Promise(resolve => setTimeout(resolve, state.queryDelay));
      if (state.queryError) return route.fulfill({ status: 503, json: { error: { code: 'NETWORK_ERROR', message: '测试：问答连接失败，请重试。' } } });
      return send({ status: state.unknown ? 'unknown' : 'answered', observations: state.unknown ? [] : [{ text: '界面回归测试中的疑似跌倒记录；未执行真实模型推理。', event_ids: [events[0].id], evidence_ids: [evidence[0].id], record_summary: true }], inferences: [], unknowns: state.unknown ? ['测试：现有证据不足，无法确定。'] : [], evidence, coverage_note: 'UI 接口模拟，不能用作真实分析验收。', is_mock: true, candidates: [], answer_engine: 'record_statistics' });
    }
    return send({});
  });
  return { state, calls, ids, events, evidence };
}
