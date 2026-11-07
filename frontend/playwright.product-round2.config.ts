import { defineConfig } from '@playwright/test';
import wave from './playwright.wave-overlay.config';

export default defineConfig({ ...wave, testMatch: 'product-round2.spec.ts' });
