import { spawnSync } from 'node:child_process';
import { expect, test } from '@playwright/test';

function fixture(action: 'reset' | 'code' | 'expire' | 'revoke'): {
  code?: string;
} {
  const raw = process.env.BOTFLOW_E2E_COMPOSE;
  if (!raw) throw new Error('Run browser auth checks through make e2e.');
  const compose: string[] = JSON.parse(raw);
  const result = spawnSync(
    compose[0],
    [
      ...compose.slice(1),
      'run',
      '--rm',
      '--no-deps',
      '-T',
      'smoke',
      'python',
      '/workspace/backend/tests/integration/browser_fixture.py',
      action,
    ],
    { encoding: 'utf8', timeout: 30_000 },
  );
  if (result.status !== 0) throw new Error('Isolated browser fixture failed.');
  return JSON.parse(result.stdout);
}

function loginCode(): string {
  const { code } = fixture('code');
  if (!code || !/^[A-Za-z0-9_-]{32}$/.test(code)) {
    throw new Error('The test Telegram adapter did not receive a valid code.');
  }
  return code;
}

test.beforeEach(() => {
  fixture('reset');
});

test('logs in through real Identity, restores the bilingual session and logs out', async ({
  page,
  context,
}) => {
  const code = loginCode();
  const consoleMessages: string[] = [];
  page.on('console', (message) => consoleMessages.push(message.text()));
  await page.goto('/');
  await expect(page).toHaveURL(/\/login$/);
  await expect(
    page.getByRole('heading', { name: 'Командная доска' }),
  ).toHaveCount(0);
  await page.getByRole('button', { name: 'Հայերեն', exact: true }).click();
  await page.getByLabel('Telegram-ի կոդը').fill(code);
  await page.getByRole('button', { name: 'Մուտք գործել', exact: true }).click();

  await expect(page).toHaveURL(/\/$/);
  await expect(
    page.getByRole('heading', { name: 'Թիմի առաջադրանքների վահանակ' }),
  ).toBeVisible();
  await expect(page.getByText('4200000001', { exact: true })).toBeVisible();
  const cookie = (await context.cookies()).find(
    (item) => item.name === 'botflow_session',
  );
  expect(cookie?.httpOnly).toBe(true);
  expect(cookie?.sameSite).toBe('Strict');
  const me = await page.request.get('/api/v1/users/me');
  expect(me.status()).toBe(200);
  expect((await me.json()).role).toBe('admin');

  await page.reload();
  await expect(
    page.getByRole('heading', { name: 'Թիմի առաջադրանքների վահանակ' }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Русский', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Командная доска' }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth,
    ),
  ).toBe(true);
  // Keep an earlier protected entry in history. Redirects use replace, so
  // without this navigation Back would correctly leave the app for about:blank.
  await page.goto('/login');
  await expect(page).toHaveURL(/\/$/);
  await expect(
    page.getByRole('heading', { name: 'Командная доска' }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();

  await expect(page).toHaveURL(/\/login$/);
  expect((await page.request.get('/api/v1/users/me')).status()).toBe(401);
  await page.goBack();
  await expect(page).toHaveURL(/\/login$/);
  const storage = await page.evaluate(() => ({
    local: { ...localStorage },
    session: { ...sessionStorage },
    cookie: document.cookie,
  }));
  expect(storage.local).toEqual({ 'botflow.locale': 'ru' });
  expect(storage.session).toEqual({});
  expect(storage.cookie.includes('botflow_session')).toBe(false);
  expect(page.url().includes(code)).toBe(false);
  expect(
    consoleMessages.some(
      (message) =>
        message.includes(code) || (cookie && message.includes(cookie.value)),
    ),
  ).toBe(false);
});

test('rejects a used Telegram code with a localized error', async ({
  page,
}) => {
  const code = loginCode();
  await page.goto('/login');
  await page.getByLabel('Код из Telegram').fill(code);
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Выйти', exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await page.getByLabel('Код из Telegram').fill(code);
  await page.getByRole('button', { name: 'Войти', exact: true }).click();

  await expect(page.getByRole('alert')).toHaveText(
    'Код недействителен или истёк. Получите новый через /login в Telegram.',
  );
  await expect(page.getByLabel('Код из Telegram')).toHaveValue('');
  await page.getByRole('button', { name: 'Հայերեն', exact: true }).click();
  await expect(page.getByRole('alert')).toHaveText(
    'Կոդն անվավեր է կամ ժամկետանց։ Telegram-ում /login հրամանով ստացեք նորը։',
  );
  expect((await page.request.get('/api/v1/users/me')).status()).toBe(401);
});

for (const action of ['expire', 'revoke'] as const) {
  test(`returns to login after the real session is changed: ${action}`, async ({
    page,
  }) => {
    await page.clock.install();
    const code = loginCode();
    await page.goto('/login');
    await page.getByLabel('Код из Telegram').fill(code);
    await page.getByRole('button', { name: 'Войти', exact: true }).click();
    await expect(
      page.getByRole('heading', { name: 'Командная доска' }),
    ).toBeVisible();

    fixture(action);
    await page.clock.runFor(31_000);

    await expect(page).toHaveURL(/\/login$/);
    await expect(
      page.getByRole('heading', { name: 'Вход в BotFlow' }),
    ).toBeVisible();
    await expect(
      page.getByRole('heading', { name: 'Командная доска' }),
    ).toHaveCount(0);
    expect((await page.request.get('/api/v1/users/me')).status()).toBe(401);
  });
}
