import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import ts from 'typescript';

const analysisPath = '../public/assets/analysis/wave/wave_analysis.json';
const bytes = await readFile(new URL(analysisPath, import.meta.url));
const demo = JSON.parse(bytes);
const summary = JSON.parse(await readFile(new URL('../public/assets/analysis/wave/wave_summary.json', import.meta.url)));
const source = await readFile(new URL('../src/detection/sampleDetection.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { sampleDetection } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);

test('Wave source is the exact new attachment and both independent approved exports agree', async () => {
  const hash = createHash('sha256').update(await readFile(new URL('../public/demo/videos/help.mp4', import.meta.url))).digest('hex');
  assert.equal(hash, 'f7f45b919cb03fb59fec122f92040cdc9a13e94d05f4c152bb60f092eb3c15a3');
  assert.equal(hash, demo.source.videoSha256);
  assert.deepEqual(bytes, await readFile(new URL('../../assets/analysis/wave/wave_analysis.json', import.meta.url)));
  assert.deepEqual(summary.events, demo.events);
  assert.equal(summary.model.trainedWaveClassifier, false);
  assert.equal(summary.newCloudCalls, 0);
  assert.equal(demo.source.basis, 'offline_pose_with_temporal_heuristic');
  assert.equal(demo.source.method, 'pose-based heuristic');
  assert.equal(demo.source.helpIntentEstablished, false);
  assert.equal('reviewModel' in demo.source, false);
  assert.equal(demo.frames.length, 116);
  assert.deepEqual(demo.video, { width: 720, height: 1280, durationMs: 3882.854 });
});

test('All decoded frames retain normalized model geometry, genuine scores and per-person tracks', () => {
  demo.frames.forEach((frame, index) => {
    assert.equal(frame.frameIndex, index);
    assert.ok(frame.validUntilMs > frame.timeMs);
    assert.equal(frame.people.length, 1);
    const p = frame.people[0];
    assert.equal(p.id, 'person-1');
    assert.equal(p.boxSource, 'offline_model');
    assert.equal(p.keypointSource, 'offline_model');
    assert.equal(p.keypoints.length, 17);
    assert.ok(p.confidence >= .55 && p.confidence <= 1);
    for (const value of Object.values(p.box)) assert.ok(value >= 0 && value <= 1);
    assert.ok(p.box.x + p.box.width <= 1 && p.box.y + p.box.height <= 1);
    for (const point of p.keypoints) assert.ok(point.x >= 0 && point.x <= 1 && point.y >= 0 && point.y <= 1 && point.confidence >= 0 && point.confidence <= 1);
  });
});

test('Action boundaries are retrospective with explicit later confirmation and no action confidence', () => {
  assert.equal(demo.events.length, 1);
  const e = demo.events[0];
  assert.equal(e.startMs, 733.333);
  assert.equal(e.endMs, 3866.333);
  assert.equal(e.confirmedAtMs, 1900);
  assert.equal('confidence' in e, false);
  for (const t of [0, 500, 733, 3867, 3883, NaN]) assert.equal(sampleDetection(demo, t).labelVisible, false);
  assert.equal(sampleDetection(demo, 2000).state, 'waving');
  assert.equal(sampleDetection(demo, 2000).people[0].wave.wrists.length, 2);
  assert.equal(sampleDetection(demo, 3800).people[0].wave.wrists.length, 1);
});

test('A wrist trajectory contains only actual same-track past samples within 600ms, never future/other-person points', () => {
  for (const f of demo.frames) for (const p of f.people) for (const wrist of p.wave?.wrists || []) {
    let last = -Infinity;
    for (const point of wrist.trajectory) {
      assert.ok(point.timeMs >= f.timeMs - 600 && point.timeMs <= f.timeMs);
      assert.ok(point.timeMs > last); last = point.timeMs;
      const recorded = demo.frames.find(old => old.timeMs === point.timeMs).people.find(old => old.id === p.id).keypoints.find(k => k.id === wrist.keypointId);
      assert.equal(point.x, recorded.x); assert.equal(point.y, recorded.y);
      assert.ok(recorded.confidence >= .65);
    }
  }
  const later = sampleDetection(demo, 3000).people[0].wave;
  const earlier = sampleDetection(demo, 1100).people[0].wave;
  assert.notDeepEqual(earlier, later);
  assert.equal(sampleDetection(demo, 0).people[0].wave, undefined);
  const gap = { ...demo, frames: [demo.frames[0], demo.frames[50]] };
  assert.equal(sampleDetection(gap, 1000).people.length, 0);
});
