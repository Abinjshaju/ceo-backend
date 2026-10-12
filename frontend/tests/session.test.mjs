import { test } from 'node:test';
import assert from 'node:assert/strict';

async function client(storage) {
  globalThis.window = { localStorage: storage };
  return import(`../src/api.js?session-test=${Math.random()}`);
}

for (const mode of ['available', 'blocked', 'write-blocked', 'remove-blocked']) {
  test(`token lifecycle with ${mode} storage`, async () => {
    let saved = null;
    const api = await client({
      getItem() { if (mode === 'blocked') throw new Error('blocked'); return saved; },
      setItem(_, value) { if (mode === 'blocked' || mode === 'write-blocked') throw new Error('blocked'); saved = value; },
      removeItem() { if (mode === 'blocked' || mode === 'remove-blocked') throw new Error('blocked'); saved = null; },
    });
    api.setToken('synthetic-token');
    assert.equal(api.getToken(), 'synthetic-token');
    api.setToken(null);
    assert.equal(api.getToken(), null);
  });
}

test('working storage observes another tab signing out', async () => {
  let saved = null;
  const api = await client({ getItem() { return saved; }, setItem(_, value) { saved = value; }, removeItem() { saved = null; } });
  api.setToken('synthetic-token');
  saved = null;
  assert.equal(api.getToken(), null);
});

test('upload validation rejects empty, unsupported and oversized files', async () => {
  const api = await client({ getItem() { return null; } });
  assert.match(api.checkUpload({ name: 'empty.txt', size: 0 }), /empty/);
  assert.match(api.checkUpload({ name: 'unsafe.exe', size: 1 }), /use PDF/);
  assert.match(api.checkUpload({ name: 'large.pdf', size: api.MAX_UPLOAD_BYTES + 1 }), /larger/);
  assert.equal(api.checkUpload({ name: 'REPORT.PDF', size: api.MAX_UPLOAD_BYTES }), '');
});
