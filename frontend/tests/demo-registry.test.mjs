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

test('Real demo adapter preserves fall/fire review and keeps wave heuristic provenance distinct', async () => {
  const { demoRegistry, fallEvidence, fallDemo } = await loadRegistry();
  assert.equal(fallDemo.durationMs, 8550);
  assert.equal(fallEvidence.startMs, 4500);
  assert.equal(fallEvidence.endMs, 6000);
  assert.equal(fallEvidence.humanConfirmed, false);
  assert.equal(fallEvidence.reviewVerdict, 'confirmed');
  assert.deepEqual(fallEvidence.frames.map(frame => frame.ptsMs), [4500, 5000, 5500, 6000]);
  assert.deepEqual(fallEvidence.frames.map(frame => frame.id), fixture.cases[0].event.referencedFrames.map(frame => frame.frame_id));
  assert.equal('confidence' in fallEvidence, false);
  assert.equal('modelScore' in fallEvidence, false);
  assert.equal(demoRegistry[1].modelResult.status, 'pose_based_heuristic');
  assert.equal(demoRegistry[1].modelResult.source, 'local_pose_temporal_rule');
  assert.equal(demoRegistry[1].modelResult.result.events[0].helpIntentEstablished, false);
  const fire = demoRegistry[2];
  assert.equal(fire.modelResult.status, 'available');
  assert.equal(fire.durationMs, 6621);
  assert.equal(fire.modelResult.result.startMs, 3034.483);
  assert.equal(fire.modelResult.result.endMs, 5758.621);
  assert.equal(fire.modelResult.result.frames.length, 12);
  assert.equal(fire.modelResult.result.humanConfirmed, false);
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
  updated.cases[1].durationMs = 8550;
  // Adapter contract example only, not a deployed help recognition result.
  updated.cases[1].event = { ...updated.cases[0].event, title: '测试用新事件', type: 'contract_test', modelReviewVerdict: 'uncertain' };
  const { demoRegistry, reviewLabel } = await loadRegistry(updated);
  assert.equal(demoRegistry[1].modelResult.status, 'available');
  assert.equal(demoRegistry[1].modelResult.result.type, 'contract_test');
  assert.equal(reviewLabel(demoRegistry[1].modelResult.result), '相关情况暂时无法确认');
  assert.equal(fixture.cases[1].event, null);
});

test('Demo asset URLs support a deployment base and reject private or external paths', async () => {
  const { demoAsset, relativeTime } = await loadRegistry();
  assert.equal(demoAsset('/demo/videos/fall.mp4', '/showcase/'), '/showcase/demo/videos/fall.mp4');
  assert.equal(demoAsset('/demo/videos/fall.mp4'), '/demo/videos/fall.mp4');
  assert.equal(demoAsset('/demo/videos/fire.mp4?v=21ea40da201d5c21', '/showcase/'), '/showcase/demo/videos/fire.mp4?v=21ea40da201d5c21');
  for (const path of ['G:/private/raw.json', '/demo-source/raw/events.json', 'https://example.com/file.mp4', '/demo/videos/../raw.json', '/demo/videos/fire.mp4?v=../../raw', '/demo/videos/fire.mp4?redirect=https://example.com']) assert.throws(() => demoAsset(path));
  assert.equal(relativeTime(8550), '00:08.55');
  assert.equal(relativeTime(59999), '01:00.00');
});

test('Public demo contains only approved assets and no private run export metadata', async () => {
  const entries = await readdir(new URL('../public/demo/', import.meta.url));
  assert.deepEqual(entries.sort(), ['demo-public-fixture.json', 'keyframes', 'posters', 'videos']);
  const text = await readFile(fixtureUrl, 'utf8');
  assert.doesNotMatch(text, /[A-Z]:\\|prompt_sha256|response_id|occurred_at|api_key|\/media\/frames\//);
  const published = await readdir(new URL('../public/demo/keyframes/', import.meta.url));
  for (const item of fixture.cases) for (const frame of item.event?.referencedFrames || []) assert.ok(published.includes(`${frame.frame_id}.jpg`));
});
