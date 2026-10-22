import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { expect, test } from 'vitest';

import App from './App';

test('keeps protected content hidden until the session has been checked', () => {
  const html = renderToStaticMarkup(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  expect(html).toContain('BotFlow');
  expect(html).toContain('Проверяем сессию…');
  expect(html).not.toContain('Командная доска');
});
