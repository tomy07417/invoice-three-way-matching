// Host-only harness checks: requires installed Chrome, not part of the app runtime.
import test from 'node:test';
import assert from 'node:assert/strict';
import { rm } from 'node:fs/promises';
import { launchChrome } from './cdp.mjs';

test('Chrome profile cleanup is bounded for transient child writes and idempotent', async () => {
  const calls = [];
  const chrome = await launchChrome({ removeProfile: async (path, options) => {
    calls.push(options);
    // Node fs owns the retry, rather than hiding a failed acceptance or replaying it.
    await rm(path, options);
  } });
  assert.equal(await chrome.client.evaluate('1 + 1'), 2);
  await chrome.close();
  assert.deepEqual(calls, [{ recursive: true, force: true, maxRetries: 3, retryDelay: 100 }]);
  await chrome.close();
  assert.equal(calls.length, 1);
});
