import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  testMatch: [
    'shell.spec.ts',
    'auth-ui.spec.ts',
    'admin-ui.spec.ts',
    'board-ui.spec.ts',
    'task-status-ui.spec.ts',
  ],
  use: { baseURL: 'http://127.0.0.1:4174' },
  webServer: {
    command:
      'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 4174 --strictPort',
    url: 'http://127.0.0.1:4174',
    reuseExistingServer: false,
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
    {
      name: 'narrow',
      use: { browserName: 'chromium', viewport: { width: 320, height: 740 } },
    },
  ],
});
