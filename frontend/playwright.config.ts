import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests',
  testMatch: 'redesign.spec.ts',
  timeout: 30000,
  fullyParallel: false,
  workers: 1,
  reporter: [['list'], ['json', { outputFile: '../docs/browser-test-results.json' }]],
  outputDir: '../runtime/redesign-test-results',
  use: { baseURL: process.env.ANJU_TEST_BASE_URL || 'http://127.0.0.1:5178', viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce', launchOptions: { executablePath: process.env.ANJU_BROWSER || 'C:/Program Files/Google/Chrome/Application/chrome.exe' }, screenshot: 'only-on-failure' },
});
