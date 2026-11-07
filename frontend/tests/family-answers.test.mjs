import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const modules = new Map();
function load(name) {
  name = name.replace(/\.ts$/, '');
  if (modules.has(name)) return modules.get(name);
  const source = readFileSync(new URL(`../src/${name}.ts`, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
  const module = { exports: {} };
  new Function('require', 'exports', outputText)(id => load(id.replace(/^\.\//, '')), module.exports);
  modules.set(name, module.exports);
  return module.exports;
}
const { familyClaim, familyUnknown } = load('familyPresentation');
const index = { events: new Map([['wave', { action: 'help_gesture' }]]) };

test('Validated counts keep actual numbers instead of becoming a wave description', () => {
  const text = '已有直接观察记录包含2个人物候选。同一采样时刻记录的最多为2个人物候选。这不是整段录像准确的独立人数。';
  assert.equal(familyClaim({ text, event_ids: ['wave'], record_summary: true }, index), text);
});

test('Validated negative answers are not rewritten as unrelated source actions', () => {
  const text = '没有找到跌倒复核记录，当前记录仅有挥手动作。';
  assert.equal(familyClaim({ text, event_ids: ['wave'], record_summary: true }, index), text);
});

test('Insufficient count evidence does not claim the person image is unclear or absent', () => {
  const text = familyUnknown('已有有效直接观察中没有人物记录，无法确定人数；不能据此断言录像中有0人。');
  assert.match(text, /不足以确定人数/);
  assert.doesNotMatch(text, /没有识别到清晰/);
});

test('Topic-specific missing evidence remains a topic-specific answer', () => {
  const text = '已分析记录中未找到明火事件；这不等于确认视频中没有发生，请回看原片。';
  assert.equal(familyUnknown(text), text);
});
