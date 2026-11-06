import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const cache = new Map();
function load(name, overrides = {}) {
  name = name.replace(/\.tsx?$/, '');
  if (cache.has(name) && !Object.keys(overrides).length) return cache.get(name);
  const tsx = new URL(`../src/${name}.tsx`, import.meta.url);
  const file = existsSync(tsx) ? tsx : new URL(`../src/${name}.ts`, import.meta.url);
  const moduleRequire = createRequire(file);
  let source = readFileSync(file, 'utf8');
  // This offline CJS harness has no Vite transform. Provide its root BASE_URL
  // while loading the real landing-page dependencies; keep every App assertion.
  source = source.replace(/import\.meta/g, '({ env: { BASE_URL: "/" } })');
  if (name === 'main') source = source.replace(/createRoot\(document[\s\S]*$/, 'export { App };');
  const module = { exports: {} };
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } });
  new Function('require', 'module', 'exports', ...Object.keys(overrides), outputText)(id => {
    if (id.endsWith('.css')) return {};
    if (id.endsWith('.json')) return { default: moduleRequire(id) };
    if (id === 'react' && overrides.hooks) return overrides.hooks;
    if (id.startsWith('.')) return load(path.posix.normalize(path.posix.join(path.posix.dirname(name), id)));
    return require(id);
  }, module, module.exports, ...Object.values(overrides));
  if (!Object.keys(overrides).length) cache.set(name, module.exports);
  return module.exports;
}
const { FlameScanSetting, VideoAnalysisControls, SafetyScanProgress, SafetyAnalysisPlan } = load('VideoAnalysis');
const render = (component, props) => renderToStaticMarkup(React.createElement(component, props));
const baseJob = { id: 'j_test', video_id: 'v_test', status: 'paused', stage: '等待恢复', progress: 40, resumable: true, failed_windows: [], is_mock: false };
const props = { mode: 'safety', job: null, pending: false, ready: true, demo: false, onModeChange() {}, onStart() {}, onResume() {}, onPause() {} };

// Exercise App handlers and effects offline. No mount, real network, camera or media access.
function appHarness(seed = {}, respond = () => ({})) {
  const names = [...readFileSync(new URL('../src/main.tsx', import.meta.url), 'utf8').matchAll(/const \[(\w+),[^\n]+? = useState/g)].map(match => match[1]);
  const state = new Map(Object.entries({ view: 'workspace', selected: 'v_test', health: { cloud_mode: true, analysis_ready: true, model: 'test-model', limits: { max_video_seconds: null, max_upload_mb: null } }, videos: [{ id: 'v_test', name: 'test.mp4', duration_ms: 10000 }], ...seed }));
  let stateIndex = 0, refIndex = 0;
  const refs = [], effects = [], calls = [], timers = [];
  const hooks = { ...React,
    useState(initial) {
      const key = names[stateIndex++];
      assert.ok(key, 'Every App state has a name');
      if (!state.has(key)) state.set(key, typeof initial === 'function' ? initial() : initial);
      return [state.get(key), value => state.set(key, typeof value === 'function' ? value(state.get(key)) : value)];
    },
    useRef(initial) { const i = refIndex++; return refs[i] ||= { current: initial }; },
    useMemo(fn) { return fn(); },
    useEffect(fn) { effects.push(fn); },
  };
  const location = new URL('http://localhost/workspace?video=v_test');
  const fetch = async (path, init) => { calls.push({ path, init }); const data = await respond(path, init); return { ok: true, json: async () => data }; };
  const { App } = load('main', { hooks, location, window: { location, history: { replaceState() {} }, addEventListener() {}, removeEventListener() {} }, fetch, setTimeout: fn => timers.push(fn), clearTimeout() {} });
  const tree = () => { stateIndex = 0; refIndex = 0; effects.length = 0; return App(); };
  function find(node, type) {
    if (!React.isValidElement(node)) return null;
    if (node.type === type) return node;
    for (const child of React.Children.toArray(node.props.children)) { const result = find(child, type); if (result) return result; }
    return null;
  }
  return { state, calls, effects, timers, tree, controls: () => find(tree(), VideoAnalysisControls).props,
    flameScan: () => find(tree(), FlameScanSetting)?.props,
    html: () => renderToStaticMarkup(tree()), effect: text => { tree(); const effect = effects.find(fn => fn.toString().includes(text)); assert.ok(effect, `Missing effect: ${text}`); return effect; } };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('Safety and objects have distinct controls and honest method descriptions', () => {
  const safety = render(VideoAnalysisControls, props);
  assert.match(safety, /开始安全分析/);
  assert.match(safety, /本地小模型扫描摔倒、举手求助、可见明火/);
  assert.doesNotMatch(safety, /候选不等于确认|复核可能不确定或不可用/);
  assert.doesNotMatch(safety, /火花|sparks/);
  const objects = render(VideoAnalysisControls, { ...props, mode: 'objects' });
  assert.match(objects, /开始物体追踪/);
  assert.match(objects, /使用 Qwen 分析物体与位置变化/);
  assert.match(objects, /带时间证据的踪迹/);
  assert.doesNotMatch(objects, /小火苗识别/);
});

test('A queued or running task blocks both mode selectors and a new start, retaining pause', () => {
  for (const status of ['queued', 'running']) {
    const html = render(VideoAnalysisControls, { ...props, job: { ...baseJob, analysis_mode: 'safety', status } });
    assert.equal((html.match(/disabled=""/g) || []).length, 3);
    assert.match(html, /暂停当前任务/);
    assert.match(html, /同一视频一次只能运行一个任务/);
  }
  const html = render(VideoAnalysisControls, { ...props, pending: true });
  assert.equal((html.match(/disabled=""/g) || []).length, 3);
  assert.match(render(VideoAnalysisControls, { ...props, ready: false }), /disabled=""[^>]*>开始安全分析/);
});

test('Resume identifies the latest mode regardless of the new selection, including legacy all jobs', () => {
  for (const [analysis_mode, label] of [['safety', '安全分析'], ['objects', '物体追踪'], ['all', '安全与物体联合分析'], [undefined, '安全与物体联合分析']]) {
    const html = render(VideoAnalysisControls, { ...props, mode: 'objects', job: { ...baseJob, analysis_mode } });
    assert.ok(html.includes(`继续上次${label}`));
  }
});

test('Safety scan and review counts stay separate, including missing and zero values', () => {
  const html = render(SafetyScanProgress, { safety: { processed_frames: 50, total_frames: 100, scan_progress: 90, candidates: 8, reviewed: 5, confirmed: 0, rejected: 2, uncertain: 2, unavailable: 1 } });
  assert.match(html, /扫描帧数：50 \/ 100 · 扫描进度 50.0%/);
  for (const [label, value] of [['候选',8],['已复核',5],['已确认',0],['已排除',2],['不确定',2],['复核不可用',1]]) assert.ok(html.includes(`<dt>${label}</dt><dd>${value}</dd>`));
  assert.doesNotMatch(html, /不能代表|不代表没有风险/);
  const absent = render(SafetyScanProgress, {});
  assert.match(absent, /扫描帧数：待上报 \/ 待上报/);
  assert.match(absent, /已确认<\/dt><dd>待上报/);
  assert.doesNotMatch(absent, /NaN|undefined|<progress/);
});

test('Safety plan never borrows object windows or guarantees completion or cost', () => {
  const html = render(SafetyAnalysisPlan);
  assert.doesNotMatch(html, /窗口|批次|帧\/秒|火花/);
  assert.match(html, /候选证据画面交由 Qwen 复核/);
  assert.doesNotMatch(html, /无法预先保证|不等于|不代表/);
  const limited = render(SafetyAnalysisPlan, { plan: { daily_requests_remaining: 0, max_windows_per_batch: 3 } });
  assert.match(limited, /今日剩余 0 次云端请求/);
  assert.match(limited, /每批最多复核 3 个候选/);
  const app = appHarness({ job: { ...baseJob, analysis_mode: 'safety', status: 'completed' }, plan: { total_windows: 99 } });
  assert.doesNotMatch(app.html(), /99 个窗口|全片已处理|录像分析覆盖|undefined|火花/);
});

test('Both App start actions POST the selected mode and guard duplicate clicks', async () => {
  for (const mode of ['safety', 'objects']) {
    const app = appHarness({ analysisMode: mode }, () => ({ ...baseJob, analysis_mode: mode, status: 'queued' }));
    const controls = app.controls(); controls.onStart(); controls.onStart();
    await flush();
    assert.equal(app.calls.length, 1);
    assert.equal(app.calls[0].path, `/api/videos/v_test/analyze?mode=${mode}`);
    assert.equal(app.calls[0].init.method, 'POST');
    assert.equal(app.state.get('job').analysis_mode, mode);
  }
});

test('Small flame selection is visible above collapsed settings and switches to safety analysis', async () => {
  const app = appHarness({ analysisMode: 'objects' }, () => ({ ...baseJob, analysis_mode: 'safety', flame_scan: 'small_flame', status: 'queued' }));
  const html = app.html();
  assert.match(html, /value="normal" selected="">正常扫描/);
  assert.ok(html.indexOf('flame-scan-row') < html.indexOf('workspace-task-tray'));
  app.flameScan().onChange('small_flame');
  assert.equal(app.controls().mode, 'safety');
  app.controls().onStart(); await flush();
  assert.equal(app.calls[0].path, '/api/videos/v_test/analyze?mode=safety&flame_scan=small_flame');
  assert.match(app.html(), /最近任务：安全分析 · 小火苗识别/);
  assert.equal(app.flameScan().disabled, true);
});

test('App resumes latest job without changing mode and pauses without a mode override', async () => {
  const app = appHarness({ analysisMode: 'objects', job: { ...baseJob, analysis_mode: 'safety' } }, () => ({ ...baseJob, analysis_mode: 'safety', status: 'running' }));
  app.controls().onResume(); await flush();
  assert.equal(app.calls[0].path, '/api/videos/v_test/resume');
  app.controls().onPause(); await flush();
  assert.equal(app.calls[1].path, '/api/videos/v_test/pause');
  assert.ok(app.calls.every(call => call.init.method === 'POST'));
});

test('Switching mode changes the plan endpoint and ignores a stale response', async () => {
  let resolveSafety;
  const app = appHarness({}, path => path.endsWith('mode=safety') ? new Promise(resolve => { resolveSafety = resolve; }) : { total_windows: 12 });
  const cleanup = app.effect('planPath')();
  app.controls().onModeChange('objects'); cleanup();
  app.effect('planPath')(); await flush();
  resolveSafety({ total_windows: 999 }); await flush();
  assert.deepEqual(app.calls.map(call => call.path), ['/api/videos/v_test/analysis-plan?mode=safety', '/api/videos/v_test/analysis-plan?mode=objects']);
  assert.equal(app.state.get('plan').total_windows, 12);
  assert.equal(app.state.get('eventCategory'), 'object');
});

test('A completed safety job refreshes evidence even without object window counts', async () => {
  const app = appHarness({ job: { ...baseJob, status: 'running', analysis_mode: 'safety' } }, path => path.startsWith('/api/jobs/') ? { ...baseJob, status: 'completed', analysis_mode: 'safety', safety: { confirmed: 1 } } : path.includes('/events?') ? { entities: [], events: [], evidence: [], frames: [] } : path === '/api/videos' ? { videos: [] } : { demos: [] });
  app.effect('/api/jobs/')(); await app.timers.at(-1)();
  assert.ok(app.calls.some(call => call.path === '/api/videos/v_test/events?grouped=true'));
  assert.equal(app.state.get('job').status, 'completed');
});

test('Read-only demo keeps its existing copy/reanalyze flow without promising a mode', async () => {
  const app = appHarness({ videos: [{ id: 'v_test', name: 'demo.mp4', duration_ms: 10000, read_only_demo: true }] }, path => path.endsWith('/reanalyze') ? { ...baseJob, video_id: 'v_copy', status: 'queued' } : path === '/api/videos' ? { videos: [] } : { demos: [] });
  const html = render(VideoAnalysisControls, app.controls());
  assert.match(html, /重新分析此案例/);
  assert.match(html, /重新分析将创建案例副本/);
  assert.doesNotMatch(html, /开始安全分析|开始物体追踪/);
  app.controls().onStart(); await flush();
  assert.equal(app.calls[0].path, '/api/videos/v_test/reanalyze');
});

test('Actual safety plan zero windows and null estimates never become an object estimate', async () => {
  const app = appHarness({}, () => ({ analysis_mode: 'safety', profile: 'safety', total_windows: 0, max_windows_per_batch: 4, daily_requests_remaining: 20, estimated_batches: null, estimated_requests_without_retries: null, estimated_sampled_frames: null, safety_review_output_tokens: 1024 }));
  app.effect('planPath')(); await flush();
  const html = app.html();
  assert.match(html, /今日剩余 20 次云端请求/);
  assert.match(html, /每批最多复核 4 个候选/);
  assert.doesNotMatch(html, /0 个窗口|帧\/秒|NaN|Infinity|undefined|物体追踪计划与预算/);
});

test('Object sampling comes from its plan, while retained results show their provenance', async () => {
  const app = appHarness({ analysisMode: 'objects', job: { ...baseJob, analysis_mode: 'objects', retained_analysis: { run_id: 'j_previous', analysis_mode: 'safety', event_ids: ['ev_kept'] } } }, () => ({ total_windows: 2, max_windows_per_batch: 4, daily_requests_remaining: 20, sampling_fps: 1, window_seconds: 10 }));
  app.effect('planPath')(); await flush();
  const html = app.html();
  assert.match(html, /1帧\/秒/);
  assert.doesNotMatch(html, /4帧\/秒|安全扫描与复核进度/);
  assert.match(html, /保留历史分析：安全分析 · 1 条事件 · 来源任务 j_previous/);
  assert.match(html, /未由本次任务重新识别或复核/);
});
