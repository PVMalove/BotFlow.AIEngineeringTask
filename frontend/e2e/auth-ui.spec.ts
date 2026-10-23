import { expect, test } from '@playwright/test';

const member = {
  id: '85a66d7a-744f-4bd3-b4ca-7cb2a9c632c7',
  telegram_id: 4200000001,
  role: 'admin',
  status: 'approved',
  display_name: 'Malove',
};

test('validates an empty or malformed code locally in both languages', async ({
  page,
}) => {
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({
      status: 401,
      json: { error: { code: 'unauthenticated', message: 'unauthenticated' } },
    }),
  );
  const exchanges: string[] = [];
  page.on('request', (request) => {
    if (request.url().endsWith('/api/v1/auth/exchange'))
      exchanges.push(request.url());
  });
  await page.goto('/login');
  await page.getByRole('button', { name: 'Войти', exact: true }).click();

  await expect(
    page.getByText(
      'Введите код из 32 символов: латинские буквы, цифры, _ или -.',
    ),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Հայերեն', exact: true }).click();
  await page.getByLabel('Telegram-ի կոդը').fill('short');
  await page.getByRole('button', { name: 'Մուտք գործել', exact: true }).click();

  await expect(
    page.getByText(
      'Մուտքագրեք 32 նիշից կազմված կոդը՝ լատինատառ, թվեր, _ կամ -։',
    ),
  ).toBeVisible();
  expect(exchanges).toEqual([]);
});

for (const failure of [
  {
    status: 429,
    ru: 'Слишком много попыток. Подождите минуту и попробуйте снова.',
    hy: 'Չափազանց շատ փորձեր։ Սպասեք մեկ րոպե և փորձեք կրկին։',
  },
  {
    status: 503,
    ru: 'Сервис входа временно недоступен. Попробуйте позже.',
    hy: 'Մուտքի ծառայությունը ժամանակավորապես անհասանելի է։ Փորձեք ավելի ուշ։',
  },
  {
    status: 0,
    ru: 'Нет связи с сервером. Проверьте подключение и попробуйте снова.',
    hy: 'Սերվերի հետ կապ չկա։ Ստուգեք կապը և փորձեք կրկին։',
  },
]) {
  test(`localizes login failure ${failure.status} in both languages`, async ({
    page,
  }) => {
    await page.route('**/api/v1/users/me', (route) =>
      route.fulfill({
        status: 401,
        json: {
          error: { code: 'unauthenticated', message: 'unauthenticated' },
        },
      }),
    );
    await page.route('**/api/v1/auth/exchange', (route) =>
      failure.status === 0
        ? route.abort('failed')
        : route.fulfill({
            status: failure.status,
            json: {
              error: {
                code: 'private-server-detail',
                message: 'private-server-detail',
              },
            },
          }),
    );
    await page.goto('/login');
    await page.getByLabel('Код из Telegram').fill('x'.repeat(32));
    await page.getByRole('button', { name: 'Войти', exact: true }).click();

    await expect(page.getByRole('alert')).toHaveText(failure.ru);
    await page.getByRole('button', { name: 'Հայերեն', exact: true }).click();
    await expect(page.getByRole('alert')).toHaveText(failure.hy);
    await expect(page.getByText('private-server-detail')).toHaveCount(0);
  });
}

test('requires login when a protected route has no session', async ({
  page,
}) => {
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({
      status: 401,
      json: { error: { code: 'unauthenticated', message: 'unauthenticated' } },
    }),
  );

  await page.goto('/');

  await expect(page).toHaveURL(/\/login$/);
  await expect(
    page.getByRole('heading', { name: 'Вход в BotFlow' }),
  ).toBeVisible();
  await expect(page.getByLabel('Код из Telegram')).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'BotFlow', level: 2, exact: true }),
  ).toHaveCount(0);
});

test('returns to login when the active session is revoked', async ({
  page,
}) => {
  let revoked = false;
  await page.clock.install();
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill(
      revoked
        ? {
            status: 401,
            json: {
              error: { code: 'unauthenticated', message: 'unauthenticated' },
            },
          }
        : { json: { data: member, meta: {} } },
    ),
  );
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'BotFlow', level: 2, exact: true }),
  ).toBeVisible();

  revoked = true;
  await page.clock.runFor(31_000);

  await expect(page).toHaveURL(/\/login$/);
  await expect(
    page.getByRole('heading', { name: 'Вход в BotFlow' }),
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'BotFlow', level: 2, exact: true }),
  ).toHaveCount(0);
});

test('hides cached protected content on Identity failure and allows a retry', async ({
  page,
}) => {
  let unavailable = false;
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill(
      unavailable
        ? {
            status: 503,
            json: {
              error: {
                code: 'identity_unavailable',
                message: 'identity_unavailable',
              },
            },
          }
        : { json: { data: member, meta: {} } },
    ),
  );
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'BotFlow', level: 2, exact: true }),
  ).toBeVisible();

  unavailable = true;
  await page.evaluate(() =>
    window.dispatchEvent(new Event('visibilitychange')),
  );

  // Wait for the protected screen to leave before selecting the session alert;
  // its three independent task reads can still show transient errors meanwhile.
  await expect(
    page.getByRole('heading', { name: 'BotFlow', level: 2, exact: true }),
  ).toHaveCount(0);
  await expect(page.getByRole('alert')).toHaveText(
    'Сервис входа временно недоступен. Попробуйте позже.',
  );

  unavailable = false;
  await page.getByRole('button', { name: 'Повторить', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'BotFlow', level: 2, exact: true }),
  ).toBeVisible();
});

test('shows a safe invalid-code error and translates it without losing the form', async ({
  page,
}) => {
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({
      status: 401,
      json: { error: { code: 'unauthenticated', message: 'unauthenticated' } },
    }),
  );
  await page.route('**/api/v1/auth/exchange', (route) =>
    route.fulfill({
      status: 401,
      json: {
        error: { code: 'invalid_code', message: 'private-server-detail' },
      },
    }),
  );
  await page.goto('/login');
  await page.getByLabel('Код из Telegram').fill('x'.repeat(32));
  await page.getByRole('button', { name: 'Войти', exact: true }).click();

  await expect(page.getByRole('alert')).toHaveText(
    'Код недействителен или истёк. Получите новый через /login в Telegram.',
  );
  await expect(page.getByLabel('Код из Telegram')).toHaveValue('');
  await page.getByRole('button', { name: 'Հայերեն', exact: true }).click();

  await expect(page.getByRole('alert')).toHaveText(
    'Կոդն անվավեր է կամ ժամկետանց։ Telegram-ում /login հրամանով ստացեք նորը։',
  );
  await expect(page.getByText('private-server-detail')).toHaveCount(0);
});

test('exchanges a code, restores the session and logs out in the selected language', async ({
  page,
}) => {
  let signedIn = false;
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill(
      signedIn
        ? { json: { data: member, meta: {} } }
        : {
            status: 401,
            json: {
              error: { code: 'unauthenticated', message: 'unauthenticated' },
            },
          },
    ),
  );
  await page.route('**/api/v1/auth/exchange', async (route) => {
    expect(route.request().postDataJSON()).toEqual({ code: 'a'.repeat(32) });
    signedIn = true;
    await route.fulfill({ json: { data: member, meta: {} } });
  });
  await page.route('**/api/v1/auth/logout', async (route) => {
    expect(route.request().method()).toBe('POST');
    signedIn = false;
    await route.fulfill({ status: 204 });
  });

  await page.goto('/login');
  await page.getByLabel('Код из Telegram').fill('a'.repeat(32));
  await page.getByRole('button', { name: 'Войти', exact: true }).click();

  await expect(page).toHaveURL(/\/$/);
  await expect(
    page.getByRole('heading', { name: 'BotFlow', level: 2, exact: true }),
  ).toBeVisible();
  await expect(page.getByText('Malove', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Հայերեն', exact: true }).click();
  await page.reload();

  await expect(
    page.getByRole('heading', { name: 'BotFlow', level: 2, exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Դուրս գալ', exact: true }).click();

  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByLabel('Telegram-ի կոդը')).toHaveValue('');
  expect(await page.evaluate(() => ({ ...localStorage }))).toEqual({
    'botflow.locale': 'hy',
  });
});
