// Screenshots of the actual frontend with fictional API fixtures, no live credentials.
import { chromium } from '../../frontend/node_modules/playwright/index.mjs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = path.dirname(fileURLToPath(import.meta.url));
const browser = await chromium.launch({
  executablePath: process.env.BOTFLOW_CHROME || undefined,
  headless: true, args: ['--no-sandbox'],
});
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 700 },
    deviceScaleFactor: 2, timezoneId: 'Asia/Yerevan', locale: 'ru-RU' });
  const admin = { id: 'demo-admin', telegram_id: 4200000001, display_name: '@taskpilot',
    role: 'admin', status: 'approved', access_version: 1 };
  const applicant = { id: 'demo-user', telegram_id: 4200000002, display_name: '@northstar',
    role: 'user', status: 'pending', access_version: 1, created_at: '2026-10-23T08:30:00Z',
    status_changed_at: null };
  const tasks = [
    ['voice-1', 'Подготовить документы для регистрации компании', 'pending', 'voice', '@northstar'],
    ['text-1', 'Сверить счёт от подрядчика', 'pending', 'text', '@taskpilot'],
    ['voice-2', 'Назначить встречу с клиентом на пятницу', 'in_progress', 'voice', '@taskpilot'],
    ['text-2', 'Отправить договор на согласование', 'completed', 'text', '@northstar'],
    ['text-3', 'Проверить реквизиты компании', 'completed', 'text', '@taskpilot'],
  ].map(([id,text,status,source_type,author_name],i)=>({ id,text,status,source_type,author_name,
    author_id:author_name==='@taskpilot'?admin.id:applicant.id, version:1,
    created_at:`2026-10-23T${String(10-i).padStart(2,'0')}:00:00Z`, updated_at:'2026-10-23T10:00:00Z' }));
  await page.routeWebSocket('**/ws/board', () => {});
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    const data = p.endsWith('/users/me') ? admin
      : p.endsWith('/admin/join-requests') ? (url.searchParams.get('status')==='rejected'?[]:[applicant])
      : p.endsWith('/admin/members') ? [admin]
      : p === '/api/v1/tasks' ? tasks.filter(t=>t.status===url.searchParams.get('status'))
      : tasks.find(t=>p.endsWith('/'+t.id));
    await route.fulfill({json:{data,meta:{has_more:false,next_cursor:null}}});
  });
  const base = process.env.BOTFLOW_SCREENSHOT_URL || 'http://127.0.0.1:5177';
  await page.goto(base);
  await page.getByRole('listitem').first().waitFor();
  await page.evaluate(()=>document.fonts.ready);
  await page.waitForFunction(() => [...document.querySelectorAll('button')]
    .some(button => button.textContent === 'Обновить доску' && !button.disabled));
  await page.screenshot({path:path.join(root,'images/02-kanban-board.png')});
  await page.getByRole('listitem').filter({hasText:tasks[0].text}).getByRole('button').first().click();
  await page.getByRole('dialog').waitFor();
  await page.evaluate(() => Promise.all(document.getAnimations().map(a => a.finished.catch(() => {}))));
  await page.screenshot({path:path.join(root,'images/03-task-details.png')});
  await page.getByRole('button',{name:'Отмена',exact:true}).click();
  await page.getByRole('link',{name:'Управление доступом',exact:true}).click();
  await page.getByRole('heading',{name:'Управление доступом',exact:true}).waitFor();
  await page.getByText('@northstar',{exact:true}).first().waitFor();
  await page.setViewportSize({ width: 960, height: 520 });
  await page.mouse.move(0, 0);
  await page.waitForTimeout(500);
  await page.screenshot({path:path.join(root,'images/04-admin-panel.png')});
  await page.setViewportSize({ width: 1440, height: 700 });
  await page.route('**/api/v1/users/me',route=>route.fulfill({status:401,json:{error:{code:'invalid_session'}}}));
  await page.goto(base+'/login');
  await page.getByRole('heading',{name:'Вход в BotFlow'}).waitFor();
  await page.evaluate(()=>document.fonts.ready);
  await page.screenshot({path:path.join(root,'images/01-login-screen.png')});
} finally { await browser.close(); }
