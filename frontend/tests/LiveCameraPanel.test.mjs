import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const uiSource = readFileSync(new URL('../src/ui.tsx', import.meta.url), 'utf8');
const uiOutput = ts.transpileModule(uiSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
}).outputText;
const uiModule = { exports: {} };
new Function('require', 'module', 'exports', uiOutput)(require, uiModule, uiModule.exports);
const apiOutput = ts.transpileModule(readFileSync(new URL('../src/memberApi.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const memberApiModule = { exports: {} };
new Function('require', 'module', 'exports', apiOutput)(require, memberApiModule, memberApiModule.exports);
const cameraRequestsModule = { exports: {} };
const cameraRequestsOutput = ts.transpileModule(readFileSync(new URL('../src/cameraRequests.ts', import.meta.url), 'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
new Function('module','exports',cameraRequestsOutput)(cameraRequestsModule,cameraRequestsModule.exports);
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
    if (id === './ui') return uiModule.exports;
    if (id === './cameraRequests') return cameraRequestsModule.exports;
    if (id === './memberApi') return memberApiModule.exports;
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

test('Panel keeps compact status controls while camera title stays in the page header and safety types sit beside the picture', () => {
  const html = render({ ...baseStatus, state: 'stopped' });
  const section = html.match(/<section id="camera"[\s\S]*?<\/section>/)?.[0] ?? assert.fail('Camera section missing');
  assert.match(section, /class="live-camera-controls"/);
  assert.match(section, /开启摄像头/);
  assert.doesNotMatch(section, /<h[1-3][^>]*>实时摄像头<\/h[1-3]>/);
  assert.match(section, /安全识别/);
  assert.match(section, /跌倒识别|挥手识别|明火识别/);
  assert.match(html, /<div class="live-camera-options">/);
  assert.match(html, /<details class="live-camera-session"><summary>[\s\S]*?本次会话事件/);
  assert.match(html, /<details><summary>[\s\S]*?检测详情/);
  assert.match(html, /<details><summary>[\s\S]*?录像与存储/);
  assert.equal((html.match(/<details/g) || []).length, 3);
  assert.doesNotMatch(section, /本次会话事件|检测详情|录像与存储/);
});
test('Closing the workspace keeps backend monitoring and recording active until explicitly stopped', () => {
  const html = render({ ...baseStatus, recording_status: { enabled: true, state: 'recording' } });
  assert.match(html, /关闭工作台后继续录像/);
  assert.match(html, /关闭工作台后继续监测，点击停止或关闭后端服务后结束/);
  assert.doesNotMatch(html, /关闭工作台约 30 秒后停止/);
});

test('Only confirmed danger events with a per-event review model are listed', () => {
  const events = ['suspected_fall', 'help_requested', 'fire_detected'].map((event_type, i) => ({
    ...candidate, candidate_id: `confirmed-${i}`, event_type, review_status: 'confirmed', review_model: 'qwen-review-test',
  }));
  const html = render({ ...baseStatus, events });
  assert.equal((html.match(/<ul class="live-camera-events"[^>]*>([\s\S]*?)<\/ul>/)?.[1].match(/<li>/g) || []).length, 3);
  for (const text of ['跌倒提醒', '举手提醒', '明火提醒', 'dateTime="2026-10-31T08:00:00Z"']) {
    assert.ok(html.includes(text), text);
  }
});

test('Pending, excluded, uncertain, unavailable and unknown events are hidden from a mixed session', () => {
  const events = ['pending', 'rejected', 'uncertain', 'unavailable', 'future_status', '', null, '__proto__', 'toString', 'constructor'].map((review_status, i) => ({
    ...candidate, candidate_id: `hidden-${i}`, review_status, review_model: 'qwen-review-test', review_reason: `hidden-reason-${i}`,
  }));
  events.push({ ...candidate, review_status: 'confirmed', review_model: 'qwen-review-test', review_reason: 'visible-confirmation' });
  const html = render({ ...baseStatus, events });
  assert.equal((html.match(/<ul class="live-camera-events"[^>]*>([\s\S]*?)<\/ul>/)?.[1].match(/<li>/g) || []).length, 1);
  assert.match(html, /跌倒提醒/);
  assert.doesNotMatch(html, /visible-confirmation|Qwen/);
  assert.doesNotMatch(html, /hidden-reason|复核排除|待复核|证据不足|data-review="rejected"/);
});

test('Legacy events remain hidden even with a configured global model and event ID', () => {
  const legacyEvent = { event_type: 'fire_detected', occurred_at: candidate.occurred_at, confidence: 0.99, event_id: 'legacy-event' };
  const html = render({ ...baseStatus, events: [legacyEvent] });
  assert.match(html, /目前没有需要查看的提醒。/);
  assert.doesNotMatch(html, /class="live-camera-events"|已复核确认/);
});

test('Confirmed status without an actual review model and unsupported event types remain hidden', () => {
  for (const event of [
    ...[undefined, '', '   ', null].map(review_model => ({ ...candidate, review_status: 'confirmed', review_model })),
    ...['unknown_type', '__proto__', 'constructor'].map(event_type => ({ ...candidate, event_type, review_status: 'confirmed', review_model: 'qwen-review-test' })),
  ]) {
    const html = render({ ...baseStatus, events: [event] });
    assert.match(html, /目前没有需要查看的提醒。/);
    assert.doesNotMatch(html, /class="live-camera-events"|已复核确认/);
  }
});

test('Internal event review diagnostics stay out of the family interface', () => {
  const html = eventMarkup({ ...candidate, review_status: 'confirmed', review_reason: '<script>confirmed danger</script>', review_model: 'event-review-model' });
  assert.match(html, /跌倒提醒/);
  assert.doesNotMatch(html, /<script>|confirmed danger|event-review-model|qwen-review-test|复核/);
});

test('Person alerts show matched identity as a candidate; unknown and fire never borrow a name', () => {
  const event = { ...candidate, review_status: 'confirmed', review_model: 'synthetic-review',
    face_identity: { status: 'matched', member_id: 'member-1', name: '<测试家人>' } };
  assert.match(eventMarkup(event), /可能是&lt;测试家人&gt;/);
  const unknown = eventMarkup({ ...event, face_identity: { ...event.face_identity, status: 'unknown', reason: 'member_deleted' } });
  assert.match(unknown, /身份未确认/);
  assert.doesNotMatch(unknown, /测试家人/);
  assert.doesNotMatch(eventMarkup({ ...event, event_type: 'fire_detected' }), /成员：|测试家人/);
});

test('Unavailable safety service gives a natural error while keeping recording controls', () => {
  const html = render({ ...baseStatus, review_error: 'review_timeout', events: [{ ...candidate, review_status: 'unavailable' }] });
  assert.match(html, /role="alert">安全分析暂时不可用/);
  assert.match(html, /安全提醒<\/dt><dd>已启用/);
  assert.match(html, /安全分析会使用相关画面/);
  assert.doesNotMatch(html, /Qwen|qwen-review-test|review_timeout|复核/);
});

test('Disabled or missing safety configuration never implies a confirmed event', () => {
  const disabled = render({ ...baseStatus, review_enabled: false, cloud_calls: false });
  assert.match(disabled, /安全提醒<\/dt><dd>未启用/);
  const legacy = render({ state: 'stopped', frames: 0, recording: false, events: [] });
  assert.match(legacy, /安全提醒<\/dt><dd>待获取/);
  for (const html of [disabled, legacy]) {
    assert.doesNotMatch(html, /无云上传|不上传画面|不调用云|已复核确认|Qwen/);
    assert.match(html, /目前没有需要查看的提醒/);
  }
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
  for (const text of ['关闭摄像头', '25 FPS', '8 ms', '4 FPS', '15 ms', '1920 × 1080', '960 × 540', 'GPU-test', '完整画面与局部细节', 'G:/recordings-test', '1.000 GiB', '正在录像', '正在录制</dt><dd>是', '3 天', '10 GiB', '5 GiB', '300 秒', '录像错误：recording-test-error', 'person-1：正在观察下降过程', '待传送 2 条']) {
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
    assert.doesNotMatch(html, /25 FPS|4 FPS|画面人数<\/dt><dd>2|疑似明火<\/dt><dd>1/);
    assert.match(html, /— FPS/);
    assert.doesNotMatch(html, /data-state="running"/);
  }
});

test('An unknown camera state is explicit and does not assume the worker is running', () => {
  const html = render({ ...baseStatus, state: 'future_state' });
  assert.match(html, /未知状态/);
  assert.match(html, /开启摄像头/);
  assert.doesNotMatch(html, /关闭摄像头|本地实时识别中/);
});

