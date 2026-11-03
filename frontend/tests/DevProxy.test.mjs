import assert from 'node:assert/strict';
import http from 'node:http';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { createServer, loadConfigFromFile } from 'vite';

test('dev proxy preserves browser origin and Host for the camera same-origin guard', async (t) => {
  // Synthetic controller: no real camera, recording, or model calls.
  const received = [];
  const backend = http.createServer((req, res) => {
    received.push({ url: req.url, host: req.headers.host, origin: req.headers.origin });
    res.statusCode = req.headers.origin === `http://${req.headers.host}` ? 200 : 403;
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ state: 'synthetic' }));
  });
  await new Promise((resolve) => backend.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise((resolve) => backend.close(resolve)));
  const target = `http://127.0.0.1:${backend.address().port}`;
  const { config } = await loadConfigFromFile(
    { command: 'serve', mode: 'development' },
    fileURLToPath(new URL('../vite.config.ts', import.meta.url)),
  );
  const proxy = Object.fromEntries(Object.entries(config.server.proxy).map(([path, rule]) => [
    path, typeof rule === 'string' ? target : { ...rule, target },
  ]));
  const server = await createServer({
    ...config,
    configFile: false,
    root: fileURLToPath(new URL('..', import.meta.url)),
    logLevel: 'silent',
    server: {
      host: '127.0.0.1', port: 0, watch: null,
      // Change only the destination; exercise the real proxy policy from the config.
      proxy,
    },
  });
  t.after(() => server.close());
  await server.listen();
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  for (const [method, path] of [['POST', '/api/camera/start'], ['POST', '/api/camera/stop'], ['GET', '/media/example.jpg?frame=1']]) {
    const response = await fetch(origin + path, { method, headers: { Origin: origin } });
    await response.text();
    assert.equal(response.status, 200, path);
    assert.equal(received.at(-1).host, new URL(origin).host);
    assert.equal(received.at(-1).origin, origin);
    assert.equal(received.at(-1).url, path);
  }
  const rejected = await fetch(origin + '/api/camera/start', {
    method: 'POST', headers: { Origin: 'https://untrusted.example' },
  });
  await rejected.text();
  assert.equal(rejected.status, 403, 'proxy must not hide an untrusted Origin');
  assert.equal(received.at(-1).origin, 'https://untrusted.example');
});
