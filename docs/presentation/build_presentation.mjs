// Renders presentation.html to BotFlow.pdf (16:9, one page per slide) with Playwright from the frontend dependencies.
import { chromium } from '../../frontend/node_modules/playwright/index.mjs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
const root = path.dirname(fileURLToPath(import.meta.url));
const browser = await chromium.launch({
  executablePath: process.env.BOTFLOW_CHROME || undefined,
  headless: true, args: ['--no-sandbox'],
});
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  await page.goto(pathToFileURL(path.join(root, 'presentation.html')).href);
  await page.evaluate(() => document.fonts.ready);
  await page.waitForFunction(() => [...document.images].every(image => image.complete));
  await page.pdf({ path: path.join(root, 'BotFlow.pdf'), width: '1280px', height: '720px',
    printBackground: true, preferCSSPageSize: true });
} finally { await browser.close(); }
console.log(path.join(root, 'BotFlow.pdf'));
