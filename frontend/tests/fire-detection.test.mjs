import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import ts from 'typescript';

async function load(file) {
  const source = await readFile(new URL(file, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
}
const { sampleDetection } = await load('../src/detection/sampleDetection.ts');
const { fireDetectionDemo: demo } = await load('../src/detection/fireDemoData.ts');

test('Fire source removes the first nine seconds and the final 2.6 seconds, preserving review timestamps', async () => {
  const video = await readFile(new URL('../public/demo/videos/fire.mp4', import.meta.url));
  assert.equal(createHash('sha256').update(video).digest('hex'), demo.source.videoSha256);
  assert.equal(demo.source.videoSha256, '3105463a9a3785c1ca38a6d42c7c4e7bed6bb5df59a9d1cf14efc007823d9444');
  assert.deepEqual(demo.video, { width: 854, height: 480, durationMs: 6621 });
  assert.equal(demo.source.evidenceStartMs, 3034.483);
  assert.equal(demo.source.evidenceEndMs, 5758.621);
  assert.equal(demo.source.basis, 'model_review_with_reviewed_annotations');
  assert.equal(demo.frames.length, 80);
});

test('Fire geometry is explicit reviewed rectangles, with no invented mask, joints or confidence', () => {
  assert.equal(demo.target, 'fire');
  assert.equal(demo.sampleMode, 'frame');
  for (const [index, frame] of demo.frames.entries()) {
    assert.equal(frame.frameIndex, 88 + index);
    assert.ok(frame.validUntilMs > frame.timeMs);
    assert.deepEqual(frame.people, []);
    assert.equal(frame.objects.length, 1);
    const object = frame.objects[0];
    assert.equal(object.className, 'fire');
    assert.equal(object.boxSource, 'reviewed_annotation');
    assert.equal(object.confidence, undefined);
    assert.equal(object.polygon, undefined);
    for (const value of Object.values(object.box)) assert.ok(value >= 0 && value <= 1);
    assert.ok(object.box.x + object.box.width <= 1 && object.box.y + object.box.height <= 1);
  }
});

test('Decoded frame sampling holds the correct rectangle, including reverse seeks, without interpolation', () => {
  const first = demo.frames[0], next = demo.frames[1];
  const sample = sampleDetection(demo, next.timeMs - .001);
  assert.deepEqual(sample.objects[0].box, first.objects[0].box);
  assert.equal(sample.frameIndex, 88);
  assert.equal(sampleDetection(demo, next.timeMs).frameIndex, 89);
  assert.notDeepEqual(sampleDetection(demo, 5500).objects[0].box, sampleDetection(demo, 3500).objects[0].box);
});

test('Unknown intervals and video start/end never inherit a flame box; fade uses media time', () => {
  for (const time of [0, 3000, 5800, 9000, NaN]) {
    const frame = sampleDetection(demo, time);
    assert.equal(frame.opacity, 0);
    assert.equal(frame.objects.length, 0);
    assert.equal(frame.labelVisible, false);
  }
  const a = demo.frames[0], b = demo.frames[10];
  const gap = { ...demo, frames: [a, b] };
  assert.equal(sampleDetection(gap, a.validUntilMs + 1).objects.length, 0);
  assert.equal(sampleDetection(demo, 4000).labelVisible, true);
  const last = demo.frames.at(-1);
  assert.ok(Math.abs(sampleDetection(demo, (last.timeMs + last.validUntilMs) / 2).opacity - .5) < .0001);
});

test('Published event count comes from one saved event; all 12 remapped evidence images exist', async () => {
  const fixture = JSON.parse(await readFile(new URL('../public/demo/demo-public-fixture.json', import.meta.url), 'utf8'));
  const fire = fixture.cases.find(item => item.id === 'fire');
  assert.equal(fire.event.modelReviewVerdict, 'confirmed');
  assert.equal(fire.event.modelScore, null);
  assert.equal(fire.event.humanConfirmed, false);
  assert.equal(fire.event.referencedFrames.length, 12);
  for (const frame of fire.event.referencedFrames) {
    assert.ok(frame.time_ms >= fire.event.startMs && frame.time_ms <= fire.event.endMs);
    assert.ok((await readFile(new URL(`../public${frame.image}`, import.meta.url))).length > 0);
  }
});
