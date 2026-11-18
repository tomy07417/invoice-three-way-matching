import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));
const runner = fileURLToPath(new URL('./manager-browser.mjs', import.meta.url));
// Observe the executable runner's public process boundary without starting its API or Chrome.
const hook = 'data:text/javascript,' + encodeURIComponent(`
  import childProcess from 'node:child_process';
  import { syncBuiltinESMExports } from 'node:module';
  childProcess.spawn = (command, args, options) => {
    process.stdout.write(JSON.stringify({ command, args, env: options.env }) + '\\n');
    process.exit(0);
  };
  syncBuiltinESMExports();
`);

test('Manager runner preserves isolated directories without inheriting application secrets', () => {
  const exercisePython = process.env.CHECK_MANAGER_PYTHON_ISOLATION === '1';
  const scratch = exercisePython ? mkdtempSync(join(root, '.manager-isolation-')) : null;
  const home = join(scratch ?? root, 'isolated-home');
  const temporary = join(scratch ?? root, 'isolated-tmp');
  try {
    if (scratch) { mkdirSync(home); mkdirSync(temporary); }
    const result = spawnSync(process.execPath, ['--import', hook, runner], {
      cwd: root, encoding: 'utf8', timeout: 5000,
      env: { PATH: process.env.PATH, HOME: home, TMPDIR: temporary,
        TEST_DATABASE_URL: 'postgresql+psycopg://test:test_only@127.0.0.1:55472/test_isolation_probe',
        AZURE_OPENAI_API_KEY: 'must-not-inherit', DATABASE_URL: 'must-not-inherit' },
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stderr, '');
    const captured = JSON.parse(result.stdout);
    assert.equal(captured.command, join(root, 'backend/.venv/bin/python'));
    assert.equal(captured.env.HOME, home);
    assert.equal(captured.env.TMPDIR, temporary);
    assert.equal(captured.env.AP_REVIEW_FIXTURE, '1');
    assert.equal(captured.env.PYTHONDONTWRITEBYTECODE, '1');
    assert.equal(captured.env.PYTHONUNBUFFERED, '1');
    assert.equal(captured.env.TEST_DATABASE_URL, 'postgresql+psycopg://test:test_only@127.0.0.1:55472/test_isolation_probe');
    assert.deepEqual(Object.keys(captured.env).sort(), [
      'AP_REVIEW_FIXTURE', 'HOME', 'PATH', 'PYTHONDONTWRITEBYTECODE', 'PYTHONUNBUFFERED', 'TEST_DATABASE_URL', 'TMPDIR',
    ]);
    if (exercisePython) {
      // Use the exact captured environment and real interpreter, but no server, database or provider.
      const python = spawnSync(captured.command, ['-c', `
import json, os, tempfile
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='manager-isolation-') as directory:
    proof = Path(directory) / 'proof.txt'
    proof.write_text('synthetic isolation proof')
    result = dict(home=os.environ.get('HOME'), root=tempfile.gettempdir(), allocated=directory,
                  content=proof.read_text())
result['removed'] = not Path(directory).exists()
print(json.dumps(result))
`], { cwd: root, env: captured.env, encoding: 'utf8', timeout: 5000 });
      assert.equal(python.status, 0, python.stderr);
      assert.equal(python.stderr, '');
      const proof = JSON.parse(python.stdout);
      assert.deepEqual(proof, { home, root: temporary, allocated: proof.allocated,
        content: 'synthetic isolation proof', removed: true });
      assert.equal(dirname(proof.allocated), temporary);
    }
  } finally {
    if (scratch) rmSync(scratch, { recursive: true, force: true });
  }
});
