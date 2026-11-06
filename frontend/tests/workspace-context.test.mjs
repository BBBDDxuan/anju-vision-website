import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

function load(name) {
  const source = readFileSync(new URL(`../src/${name}.ts`, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
  const module = { exports: {} }; new Function('exports', outputText)(module.exports); return module.exports;
}
const { inspectorFromUrl, viewFromUrl } = load('navigation');
const { seekVideo } = load('seekVideo');

class Media extends EventTarget {
  readyState = 1; duration = 8.55; error = null; seeking = false; paused = false; position = 0;
  listeners = new Set();
  addEventListener(type, handler, options) { this.listeners.add(handler); super.addEventListener(type, handler, options); }
  removeEventListener(type, handler) { this.listeners.delete(handler); super.removeEventListener(type, handler); }
  pause() { this.paused = true; }
  get currentTime() { return this.position; }
  set currentTime(value) { this.position = value; queueMicrotask(() => this.dispatchEvent(new Event('seeked'))); }
}

test('Workspace panel links and legacy anchors resolve without changing selected video routing', () => {
  for (const [link, expected] of [['?panel=qa', 'qa'], ['?panel=tools', 'tools'], ['?panel=invalid', 'events'], ['#ask', 'qa'], ['#timeline', 'events'], ['#analysis-tools', 'tools']]) {
    const url = new URL(`http://localhost/workspace${link}`);
    assert.equal(inspectorFromUrl(url), expected); assert.equal(viewFromUrl(url), 'videos');
  }
  assert.equal(viewFromUrl(new URL('http://localhost/workspace?view=camera&panel=qa')), 'camera');
  assert.equal(viewFromUrl(new URL('http://localhost/workspace?view=members')), 'members');
});

test('Shared seek waits for actual metadata and seeked, then pauses at the real video time', async () => {
  const media = new Media(); media.readyState = 0;
  const action = seekVideo(media, 5000, new AbortController().signal);
  assert.equal(media.position, 0); assert.equal(media.paused, true);
  media.readyState = 1; media.dispatchEvent(new Event('loadedmetadata'));
  assert.equal(await action, 5); assert.equal(media.listeners.size, 0);
});

test('An aborted video selection removes pending seek listeners and cannot move the new video', async () => {
  const media = new Media(); media.readyState = 0;
  const controller = new AbortController(); const action = seekVideo(media, 6300, controller.signal);
  controller.abort(); await assert.rejects(action, { name: 'AbortError' });
  media.readyState = 1; media.dispatchEvent(new Event('loadedmetadata'));
  assert.equal(media.position, 0); assert.equal(media.listeners.size, 0);
});

test('Shared seek surfaces media errors and clamps manual positions to actual duration', async () => {
  const media = new Media();
  assert.equal(await seekVideo(media, 90000, new AbortController().signal), 8.55);
  media.error = new Error('Media decode failure');
  await assert.rejects(seekVideo(media, 2300, new AbortController().signal), /视频暂不可用/);
  assert.equal(media.listeners.size, 0);
});
