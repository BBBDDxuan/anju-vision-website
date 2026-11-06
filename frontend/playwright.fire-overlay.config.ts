import { defineConfig } from '@playwright/test';
import path from 'node:path';
import fall from './playwright.fall-overlay.config';

const root = path.resolve(import.meta.dirname, '..');
const run = process.env.ANJU_CHECK_DIR || path.join(root, 'data/run/fire-overlay-check-20261106');
process.env.ANJU_CHECK_DIR = run;
export default defineConfig({
  ...fall,
  testMatch: 'fire-overlay.spec.ts',
  reporter: [['list'], ['json', { outputFile: path.join(run, 'browser-results.json') }]],
  outputDir: path.join(run, 'browser-artifacts'),
  use: { ...fall.use, baseURL: 'http://127.0.0.1:8019' },
  webServer: { command: '".venv/Scripts/python.exe" -m backend.local_server --port 8019', cwd: root,
    url: 'http://127.0.0.1:8019/api/health', reuseExistingServer: false, timeout: 30000,
    env: { DATA_DIR: path.join(run, 'isolated-server-data'), VLM_ENABLE_NETWORK: 'false',
      CLOUD_MODE: 'false', APP_LAN_TOKEN: '', PYTHONIOENCODING: 'utf-8' } },
});
