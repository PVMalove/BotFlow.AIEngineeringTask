import { defineConfig } from '@playwright/test';

if (!process.env.BOTFLOW_E2E_BASE_URL) {
  throw new Error('Use make e2e to prepare an isolated Compose project.');
}

export default defineConfig({
  testDir: './e2e',
  testMatch: ['infrastructure.spec.ts', 'auth.spec.ts', 'admin.spec.ts'],
  workers: 1,
  outputDir: process.env.BOTFLOW_E2E_OUTPUT_DIR,
  use: { baseURL: process.env.BOTFLOW_E2E_BASE_URL },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
    {
      name: 'narrow',
      use: {
        browserName: 'chromium',
        viewport: { width: 390, height: 844 },
      },
    },
  ],
});
