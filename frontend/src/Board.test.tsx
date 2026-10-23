import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, expect, test, vi } from 'vitest';

import { Board } from './Board';
import { useLocale } from './locale';

afterEach(() => vi.unstubAllGlobals());

test.each([
  ['ru', 'Подключаемся к обновлениям…'],
  ['hy', 'Միանում ենք թարմացումներին…'],
])('shows the initial connection state in %s', (locale, expected) => {
  vi.stubGlobal('window', { localStorage: { getItem: () => locale } });
  const client = new QueryClient();
  function Screen() {
    const { messages, locale } = useLocale();
    return (
      <Board
        member={{
          id: 'owner',
          role: 'user',
          status: 'approved',
          telegram_id: 1,
          display_name: null,
          access_version: 1,
        }}
        messages={messages}
        locale={locale}
      />
    );
  }

  const html = renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <Screen />
    </QueryClientProvider>,
  );

  expect(html).toContain(expected);
  client.clear();
});
