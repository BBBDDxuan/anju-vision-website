import { request, type FullConfig } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';

// Exercise the real account gate on the isolated demo test server. The saved
// cookie belongs only to synthetic test data and stays in ignored data/run.
export default async function setup(config: FullConfig) {
  const { baseURL, storageState } = config.projects[0].use;
  if (!baseURL || !/^http:\/\/127\.0\.0\.1:80(?:18|19|20)$/.test(baseURL)
      || typeof storageState !== 'string' || !storageState.includes('data')) {
    throw new Error('Demo account setup requires an isolated local test server and state file');
  }
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Origin: baseURL } });
  try {
    const response = await api.get('/api/auth/status');
    if (!response.ok()) throw new Error(`Account status failed: ${response.status()}`);
    const status = await response.json();
    if (status.enabled !== true) throw new Error('Expected the merged household account service');
    const credentials = { username: 'demo_test_admin', password: 'synthetic-demo-test-password-2026' };
    const signed = status.setup_allowed
      ? await api.post('/api/auth/setup', { data: { ...credentials, display_name: '演示验收', household_name: '隔离测试家庭' } })
      : await api.post('/api/auth/login', { data: credentials });
    if (!signed.ok()) throw new Error(`Isolated test account sign-in failed: ${signed.status()}`);
    await mkdir(path.dirname(storageState), { recursive: true });
    await api.storageState({ path: storageState });
  } finally {
    await api.dispose();
  }
}
