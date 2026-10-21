import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  use: { baseURL: 'http://127.0.0.1:18080' },
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
