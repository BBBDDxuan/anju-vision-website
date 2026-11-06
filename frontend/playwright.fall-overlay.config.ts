import { defineConfig } from '@playwright/test';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const run = process.env.ANJU_CHECK_DIR || path.join(root, 'data/run/fall-overlay-check-20261106');
export default defineConfig({
  testDir: './tests', testMatch: 'fall-overlay.spec.ts', workers: 1, timeout: 30000,
  globalSetup: './tests/demo-auth-setup.ts',
  reporter: [['list'], ['json', { outputFile: path.join(run, 'browser-results.json') }]],
  outputDir: path.join(run, 'browser-artifacts'),
  use: { baseURL: 'http://127.0.0.1:8018', viewport: { width: 1440, height: 1000 },
    storageState: path.join(run, 'browser-auth-state.json'),
    reducedMotion: 'reduce', screenshot: 'only-on-failure', trace: 'retain-on-failure',
    launchOptions: { executablePath: process.env.ANJU_BROWSER || 'C:/Program Files/Google/Chrome/Application/chrome.exe' } },
  webServer: { command: '".venv/Scripts/python.exe" -m backend.local_server --port 8018', cwd: root,
    url: 'http://127.0.0.1:8018/api/health', reuseExistingServer: false, timeout: 30000,
    env: { DATA_DIR: path.join(run, 'isolated-server-data'), VLM_ENABLE_NETWORK: 'false',
      CLOUD_MODE: 'false', APP_LAN_TOKEN: '', PYTHONIOENCODING: 'utf-8' } },
});
