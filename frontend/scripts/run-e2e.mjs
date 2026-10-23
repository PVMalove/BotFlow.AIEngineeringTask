import { spawnSync } from 'node:child_process';
import { fileURLToPath, URL } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));
const result = spawnSync(
    process.env.PYTHON ?? 'python3',
    ['scripts/integration-smoke.py', '--browser'],
    {
        cwd: root,
        stdio: 'inherit',
        env: { ...process.env, BOTFLOW_NODE: process.execPath },
    },
);
if (result.error) console.error(result.error.message);
process.exitCode = result.status ?? 1;
