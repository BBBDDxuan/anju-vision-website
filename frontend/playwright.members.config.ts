import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests', testMatch: 'members.spec.ts', workers: 1, timeout: 30000,
  reporter: 'list', outputDir: '../runtime/member-browser-results',
  use: { baseURL: process.env.ANJU_TEST_BASE_URL || 'http://127.0.0.1:5178',
    viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce',
    launchOptions: { executablePath: process.env.ANJU_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' },
    screenshot: 'only-on-failure' },
});
