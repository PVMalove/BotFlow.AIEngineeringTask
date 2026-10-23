import { spawnSync } from 'node:child_process';

type Action =
  | 'reset'
  | 'code'
  | 'user-code'
  | 'join'
  | 'expire'
  | 'revoke'
  | 'board'
  | 'board-change'
  | 'dnd-board';

export function fixture(action: Action): { code?: string } {
  const raw = process.env.BOTFLOW_E2E_COMPOSE;
  if (!raw) throw new Error('Run browser checks through make e2e.');
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

export function loginCode(action: 'code' | 'user-code' = 'code'): string {
  const { code } = fixture(action);
  if (!code || !/^[A-Za-z0-9_-]{32}$/.test(code)) {
    throw new Error('The test Telegram adapter did not receive a valid code.');
  }
  return code;
}
