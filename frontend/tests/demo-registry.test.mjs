import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import ts from 'typescript';

const fixtureUrl = new URL('../public/demo/demo-public-fixture.json', import.meta.url);
const fixture = JSON.parse(await readFile(fixtureUrl, 'utf8'));
const source = await readFile(new URL('../src/demoRegistry.ts', import.meta.url), 'utf8');
async function loadRegistry(data = fixture) {
  const code = ts.transpileModule(source.replace(/^import fixture from .*;$/m, `const fixture = ${JSON.stringify(data)};`), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
}

test('Real demo adapter preserves evidence coordinates, five cited frames and pending null results', async () => {
  const { demoRegistry, fallEvidence, fallDemo } = await loadRegistry();
  assert.equal(fallDemo.durationMs, 8550);
  assert.equal(fallEvidence.startMs, 2300);
  assert.equal(fallEvidence.endMs, 6300);
  assert.equal(fallEvidence.humanConfirmed, false);
  assert.equal(fallEvidence.reviewVerdict, 'confirmed');
  assert.deepEqual(fallEvidence.frames.map(frame => frame.ptsMs), [2300, 4500, 5000, 6000, 6300]);
  assert.deepEqual(fallEvidence.frames.map(frame => frame.id), fixture.cases[0].event.referencedFrames.map(frame => frame.frame_id));
  assert.equal('confidence' in fallEvidence, false);
  assert.equal('modelScore' in fallEvidence, false);
  for (const item of demoRegistry.slice(1)) assert.deepEqual(item.modelResult, { status: 'pending_model_fix', source: null, result: null });
});

test('Public adapter rejects invalid time ranges and frame locations instead of creating evidence', async () => {
  const { adaptVerifiedResult } = await loadRegistry();
  const event = fixture.cases[0].event;
  for (const patch of [{ startMs: -1 }, { endMs: 9000 }, { endMs: 2000 }, { startMs: NaN }]) {
    assert.throws(() => adaptVerifiedResult({ ...event, ...patch }, 8550), /interval/);
  }
  assert.throws(() => adaptVerifiedResult({ ...event, referencedFrames: [] }, 8550), /frames/);
  assert.throws(() => adaptVerifiedResult({ ...event, referencedFrames: [{ ...event.referencedFrames[0], time_ms: 9000 }] }, 8550), /frames/);
});

test('Future verified results use the shared registry without a permanently pending case branch', async () => {
  const updated = structuredClone(fixture);
  updated.cases[1].resultStatus = 'verified_model_export';
  // Adapter contract example only, not a deployed help recognition result.
  updated.cases[1].event = { ...updated.cases[0].event, title: '测试用新事件', type: 'contract_test', modelReviewVerdict: 'uncertain' };
  const { demoRegistry, reviewLabel } = await loadRegistry(updated);
  assert.equal(demoRegistry[1].modelResult.status, 'available');
  assert.equal(demoRegistry[1].modelResult.result.type, 'contract_test');
  assert.equal(reviewLabel(demoRegistry[1].modelResult.result), '视觉模型复核尚不支持确认');
  assert.equal(fixture.cases[1].event, null);
});

test('Demo asset URLs support a deployment base and reject private or external paths', async () => {
  const { demoAsset, relativeTime } = await loadRegistry();
  assert.equal(demoAsset('/demo/videos/fall.mp4', '/showcase/'), '/showcase/demo/videos/fall.mp4');
  assert.equal(demoAsset('/demo/videos/fall.mp4'), '/demo/videos/fall.mp4');
  for (const path of ['G:/private/raw.json', '/demo-source/raw/events.json', 'https://example.com/file.mp4', '/demo/videos/../raw.json']) assert.throws(() => demoAsset(path));
  assert.equal(relativeTime(8550), '00:08.55');
  assert.equal(relativeTime(59999), '01:00.00');
});

test('Public demo contains only approved assets and no private run export metadata', async () => {
  const entries = await readdir(new URL('../public/demo/', import.meta.url));
  assert.deepEqual(entries.sort(), ['demo-public-fixture.json', 'keyframes', 'posters', 'videos']);
  const text = await readFile(fixtureUrl, 'utf8');
  assert.doesNotMatch(text, /[A-Z]:\\|prompt_sha256|response_id|occurred_at|api_key|\/media\/frames\//);
  assert.equal((await readdir(new URL('../public/demo/keyframes/', import.meta.url))).length, 5);
});
