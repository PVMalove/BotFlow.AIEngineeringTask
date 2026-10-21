import { defineConfig } from '@playwright/test';

if (!process.env.BOTFLOW_E2E_BASE_URL) {
  throw new Error('Use make e2e to prepare an isolated Compose project.');
}

export default defineConfig({
  testDir: './e2e',
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
