import { defineConfig } from '@playwright/test';
import path from 'node:path';
import product from './playwright.product-round2.config';

const root = path.resolve(import.meta.dirname, '..');
const run = path.join(root, 'data/run/frontend-copy-qa-20261107');
process.env.ANJU_CHECK_DIR = run;
export default defineConfig({
  ...product,
  testMatch: ['product-round2.spec.ts', 'copy-qa.spec.ts'],
  reporter: [['list'], ['json', { outputFile: path.join(run, 'browser-results.json') }]],
  outputDir: path.join(run, 'browser-artifacts'),
  use: { ...product.use, storageState: path.join(run, 'browser-auth-state.json') },
  webServer: { ...product.webServer,
    command: `"${process.env.ANJU_TEST_PYTHON || '.venv/Scripts/python.exe'}" -m backend.local_server --port 8020`,
    env: { ...product.webServer.env, DATA_DIR: path.join(run, 'isolated-server-data') },
  },
});
