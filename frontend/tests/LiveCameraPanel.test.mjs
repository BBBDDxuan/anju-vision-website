import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../src/LiveCameraPanel.tsx', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
});
const loadPanel = new Function('require', 'module', 'exports', 'fetch', outputText);

// Render-only regression tests: inject status and disable effects so no polling,
// commands, real API requests, frame downloads or camera access can run.
function render(status, props = {}) {
  let stateIndex = 0;
  const module = { exports: {} };
  const forbidSideEffect = () => { throw new Error('Side effects are forbidden in this offline test'); };
  loadPanel(id => {
    if (id === './live-camera.css') return {};
    if (id === 'react') return {
      ...React,
      useState: initial => [stateIndex++ === 0 ? status : initial, forbidSideEffect],
      useEffect: () => {},
      useRef: initial => ({ current: initial }),
    };
    return require(id);
  }, module, module.exports, forbidSideEffect);
  return renderToStaticMarkup(React.createElement(module.exports.LiveCameraPanel, props));
}

const candidate = { candidate_id: 'candidate-1', event_id: null, event_type: 'suspected_fall', occurred_at: '2026-10-31T08:00:00Z', confidence: 0.91 };
const baseStatus = {
  state: 'running', frames: 12, recording: false,
  safety_pipeline: 'small_models_then_qwen', review_enabled: true,
  review_model: 'qwen-review-test', cloud_calls: true, sparks_enabled: false,
  events: [],
};
function eventMarkup(event) {
  const html = render({ ...baseStatus, events: [event] });
  return html.match(/<ul class="live-camera-events"[^>]*>([\s\S]*?)<\/ul>/)?.[1] ?? assert.fail('Event list missing');
}
function reviewMarkup(event) {
  return eventMarkup(event).match(/<span class="live-camera-event-review"[^>]*>([^<]*)<\/span>/)?.[1] ?? assert.fail('Review label missing');
}

for (const [status, label] of [
  ['pending', '待复核'],
  ['confirmed', '已复核确认'],
  ['rejected', '复核排除'],
  ['uncertain', '证据不足'],
  ['unavailable', '复核不可用'],
]) {
  test(`Event review status: ${status}`, () => {
    const event = { ...candidate, review_status: status, event_id: status === 'confirmed' ? 'event-1' : null };
    assert.equal(reviewMarkup(event), label);
    assert.equal(eventMarkup(event).includes('已复核确认'), status === 'confirmed');
    assert.match(eventMarkup(event), /疑似跌倒/);
    assert.match(eventMarkup(event), /dateTime="2026-10-31T08:00:00Z"/);
  });
}

test('Legacy events remain unreviewed even with a configured model and event ID', () => {
  const legacyEvent = { event_type: 'fire_detected', occurred_at: candidate.occurred_at, confidence: 0.99, event_id: 'legacy-event' };
  assert.match(reviewMarkup(legacyEvent), /^未复核/);
  assert.doesNotMatch(eventMarkup(legacyEvent), /已复核确认/);
  assert.match(eventMarkup(legacyEvent), /疑似明火/);
});

test('Unknown, empty and inherited-object statuses fail closed to unreviewed', () => {
  for (const status of ['future_status', '', null, '__proto__', 'toString', 'constructor']) {
    assert.match(reviewMarkup({ ...candidate, review_status: status }), /^未复核/);
    assert.doesNotMatch(eventMarkup({ ...candidate, review_status: status }), /已复核确认/);
  }
});

test('Per-event review reason and model are shown as escaped text, without a global-model fallback', () => {
  const html = eventMarkup({ ...candidate, review_status: 'uncertain', review_reason: '<script>insufficient evidence</script>', review_model: 'event-review-model' });
  assert.match(html, /复核说明：&lt;script&gt;insufficient evidence&lt;\/script&gt;/);
  assert.match(html, /复核模型：event-review-model/);
  assert.doesNotMatch(html, /<script>|qwen-review-test/);
  assert.doesNotMatch(eventMarkup(candidate), /复核说明：|复核模型：/);
});

test('Pipeline, availability, supported tasks and review errors are explicit', () => {
  const html = render({ ...baseStatus, review_error: 'review_timeout', events: [{ ...candidate, review_status: 'unavailable' }] });
  assert.match(html, /小模型候选 → Qwen 复核/);
  assert.match(html, /复核模型<\/dt><dd>qwen-review-test/);
  assert.match(html, /候选复核<\/dt><dd>已启用/);
  assert.match(html, /云端复核配置<\/dt><dd>已配置/);
  assert.doesNotMatch(html, /候选不等于|不代表|不构成诊断/);
  assert.match(html, /摔倒 · 举手求助 · 可见明火/);
  assert.doesNotMatch(html, /烟雾|电火花/);
  assert.match(html, /role="alert">复核异常：review_timeout/);
  assert.match(html, /云端复核使用候选证据画面/);
  assert.doesNotMatch(html, /无云上传|不上传画面|不调用云|已复核确认/);
});

test('Disabled or missing review configuration never implies confirmation or local-only processing', () => {
  const disabled = render({ ...baseStatus, review_enabled: false, cloud_calls: false });
  assert.match(disabled, /候选复核<\/dt><dd>未启用/);
  assert.match(disabled, /云端复核配置<\/dt><dd>未配置/);
  const legacy = render({ state: 'stopped', frames: 0, recording: false, events: [] });
  for (const field of ['安全流程', '复核模型', '云端复核配置']) {
    assert.ok(legacy.includes(`${field}</dt><dd>未下发`));
  }
  for (const html of [disabled, legacy]) {
    assert.doesNotMatch(html, /无云上传|不上传画面|不调用云|已复核确认/);
    assert.match(html, /暂无候选事件/);
  }
  for (const sparks_enabled of [undefined, true]) {
    assert.match(render({ ...baseStatus, sparks_enabled }), /摔倒 · 举手求助 · 可见明火/);
  }
  assert.match(render({ ...baseStatus, safety_pipeline: 'future_pipeline' }), /未知流程/);
});

test('Recording, performance, help/fall diagnostics and alert queue information remain visible', () => {
  const html = render({
    ...baseStatus, recording: true, persons: 2, fire_candidates: 1,
    fps: 25, inference_ms: 8, fire_fps: 4, fire_inference_ms: 15,
    width: 1920, height: 1080, preview_width: 960, preview_height: 540, device: 'GPU-test',
    flame_tiling_enabled: true, help_facing_camera_required: true, help_face_candidates: 1,
    fall_diagnostics: [{ track_id: 'person-1', state: 'observing_descent' }],
    alerts: { enabled: true, pending: 2, worker_alive: true },
    recording_status: { enabled: true, state: 'recording', directory: 'G:/recordings-test', files: 3, bytes: 1024 ** 3, deleted_files: 2, retention_days: 3, max_gib: 10, min_free_gib: 5, segment_seconds: 300, error: 'recording-test-error' },
  });
  for (const text of ['停止预览', '25 FPS', '8 ms', '4 FPS', '15 ms', '1920 × 1080', '960 × 540', 'GPU-test', '全画面 + 高清重叠切片', 'G:/recordings-test', '1.000 GiB', '正在录像', '正在录制</dt><dd>是', '3 天', '10 GiB', '5 GiB', '300 秒', '录像错误：recording-test-error', '面部朝向候选 1 / 2', 'person-1：正在观察下降过程', '待传送 2 条']) {
    assert.ok(html.includes(text), `Missing existing information: ${text}`);
  }
});

test('Loading and configuration errors keep the existing disabled control and retry affordance', () => {
  const loading = render(null, { ready: false });
  assert.match(loading, /正在确认本机运行环境/);
  assert.match(loading, /aria-busy="true"/);
  assert.match(loading, /button disabled=""/);
  const failure = render(null, { ready: false, configurationError: 'offline-test-error', onRetry: () => {} });
  assert.match(failure, /role="alert">offline-test-error/);
  assert.match(failure, /重新读取/);
});

test('Disconnected camera states do not present previous FPS or counts as current measurements', () => {
  for (const state of ['stopped', 'starting', 'connecting', 'reconnecting', 'stale', 'error']) {
    const html = render({ ...baseStatus, state, fps: 25, fire_fps: 4, persons: 2, fire_candidates: 1 });
    assert.doesNotMatch(html, /25 FPS|4 FPS|画面人数<\/dt><dd>2|火焰候选<\/dt><dd>1/);
    assert.match(html, /— FPS/);
    assert.doesNotMatch(html, /data-state="running"/);
  }
});

test('An unknown camera state is explicit and does not assume the worker is running', () => {
  const html = render({ ...baseStatus, state: 'future_state' });
  assert.match(html, /未知状态/);
  assert.match(html, /开启摄像头/);
  assert.doesNotMatch(html, /停止预览|本地实时识别中/);
});

