import { renderToStaticMarkup } from 'react-dom/server';
import { expect, test } from 'vitest';

import App from './App';

test('shows the project shell and its unfinished state', () => {
  const html = renderToStaticMarkup(<App />);

  expect(html).toContain('BotFlow');
  expect(html).toContain('Панель готовится к запуску.');
});
