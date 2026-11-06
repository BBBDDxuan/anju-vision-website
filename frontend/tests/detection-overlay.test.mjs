import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

async function load(file) {
  const source = await readFile(new URL(file, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
}
const { sampleDetection, videoContentRect } = await load('../src/detection/sampleDetection.ts');
const { fallDetectionDemo: demo } = await load('../src/detection/fallDemoData.ts');

test('Verified demo bounds/source and all normalized geometry are valid', () => {
  assert.equal(demo.source.kind, 'demo');
  assert.equal(demo.source.basis, 'offline_model_with_reviewed_annotations');
  assert.equal(demo.video.durationMs, 8550);
  assert.equal(demo.frames.length, 79);
  assert.equal(demo.source.evidenceStartMs, 4500);
  assert.equal(demo.source.evidenceEndMs, 6000);
  assert.equal(demo.frames[0].timeMs, 2300);
  for (const [index, frame] of demo.frames.entries()) {
    if (index) assert.ok(frame.timeMs > demo.frames[index - 1].timeMs);
    for (const p of frame.people) {
      assert.equal(p.boxSource, 'reviewed_annotation');
      for (const v of Object.values(p.box)) assert.ok(v >= 0 && v <= 1);
      assert.ok(p.box.x + p.box.width <= 1.000001 && p.box.y + p.box.height <= 1.000001);
      for (const k of p.keypoints) assert.ok(k.confidence >= .65 && k.x >= 0 && k.x <= 1 && k.y >= 0 && k.y <= 1);
    }
  }
});

test('Risk follows latest reviewed video time, fades after 6.0s and resets on reverse/loop seeks', () => {
  for (const t of [0, 2299, 6200, 8500, NaN]) assert.equal(sampleDetection(demo, t).opacity, 0);
  assert.equal(sampleDetection(demo, 4500).labelVisible, false);
  assert.equal(sampleDetection(demo, 4999).labelVisible, false);
  assert.equal(sampleDetection(demo, 5000).labelVisible, true);
  assert.equal(sampleDetection(demo, 6000).labelVisible, true);
  assert.ok(Math.abs(sampleDetection(demo, 6100).opacity - .5) < 1e-9);
  assert.equal(sampleDetection(demo, 3000).state, 'tracking');
  assert.equal(sampleDetection(demo, 0).people.length, 0);
});

test('Boxes interpolate in source coordinates; missing joints and track gaps are never invented', () => {
  const a = sampleDetection(demo, 5000), b = sampleDetection(demo, 5050);
  const middle = sampleDetection(demo, 5025);
  assert.ok(Math.abs(middle.people[0].box.y - (a.people[0].box.y + b.people[0].box.y) / 2) < 1e-9);
  assert.notEqual(sampleDetection(demo, 3000).people[0].box.y, sampleDetection(demo, 5800).people[0].box.y);
  assert.equal(sampleDetection(demo, 5000).people[0].keypoints.length, 0);
  const gap = { ...demo, frames: [demo.frames[0], demo.frames[20]] };
  assert.equal(sampleDetection(gap, 2500).people.length, 0);
  const absent = { ...demo, frames: [demo.frames[0], { ...demo.frames[1], people: [] }] };
  assert.equal(sampleDetection(absent, 2325).people.length, 0);
});

test('Content mapping accounts for contain bars, cover cropping, fill and position', () => {
  const contain = videoContentRect(720, 480, 400, 400, 'contain');
  assert.equal(contain.x, 0); assert.equal(contain.width, 400);
  assert.ok(Math.abs(contain.y - 200 / 3) < 1e-9);
  assert.ok(Math.abs(contain.height - 800 / 3) < 1e-9);
  const cover = videoContentRect(720, 480, 400, 400, 'cover');
  assert.equal(cover.x, -100); assert.equal(cover.y, 0);
  assert.equal(cover.width, 600); assert.equal(cover.height, 400);
  assert.ok(Math.abs(videoContentRect(720, 480, 400, 400, 'cover', '0% 50%').x) < 1e-9);
  assert.deepEqual(videoContentRect(720, 480, 400, 400, 'fill'), { x: 0, y: 0, width: 400, height: 400 });
  assert.deepEqual(videoContentRect(720, 480, 0, 0, 'contain'), { x: 0, y: 0, width: 0, height: 0 });
});
