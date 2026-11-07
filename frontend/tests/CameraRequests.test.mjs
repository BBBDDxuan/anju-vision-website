import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/cameraRequests.ts', import.meta.url), 'utf8');
const module = { exports: {} };
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
new Function('module', 'exports', code)(module, module.exports);
const { cameraResponseError, cameraNetworkError } = module.exports;

test('camera errors distinguish a missing endpoint, permissions and camera RTSP', () => {
  assert.match(cameraResponseError('connect', 404, { detail: 'Not Found' }), /当前电脑服务未提供/);
  assert.match(cameraResponseError('connect', 401, null), /重新登录/);
  assert.match(cameraResponseError('connect', 403, null), /权限/);
  assert.match(cameraResponseError('connect', 422, {error:{code:'INVALID_REQUEST',message:'generic'}}), /摄像头参数无效/);
  assert.equal(cameraResponseError('connect', 422, {error:{code:'CAMERA_STREAM_NOT_FOUND',message:'设备没有该视频路径'}}), '设备没有该视频路径');
  assert.match(cameraNetworkError('stop', {name:'TimeoutError'}), /关闭摄像头请求超时/);
});
