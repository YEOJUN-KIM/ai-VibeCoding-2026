const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function scenario({database = true, configured = true, statusFails = false, loginOk = true} = {}) {
  const nodes = new Map();
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {
      value: selector === '#username' ? 'demo' : 'password', textContent: '', disabled: true,
      addEventListener(event, handler) { this[event] = handler; }, focus() { this.focused = true; },
    });
    return nodes.get(selector);
  };
  const requests = [];
  const timers = [];
  let destination;
  const context = {
    document: {querySelector: node},
    window: {location: {replace(path) { destination = path; }}},
    setTimeout(handler) { timers.push(handler); return timers.length; }, clearTimeout() {},
    async fetch(path) {
      requests.push(path);
      if (path === '/startup/readiness') return {ok: true, json: async () => ({ready: database, database: {connected: database, message: 'DB unavailable'}})};
      if (path === '/auth/login') return {ok: loginOk, json: async () => loginOk ? {} : {detail: 'Wrong password'}};
      if (statusFails) throw new Error('Status unavailable');
      return {ok: true, json: async () => ({configured})};
    },
  };
  vm.runInNewContext(fs.readFileSync('auto_trader/static/login.js', 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  const readyDisabled = node('#login-button').disabled;
  await node('#login-form').submit({preventDefault() {}});
  return {nodes, requests, timers, destination, readyDisabled};
}

(async () => {
  const missingKeys = await scenario({configured: false});
  assert.equal(missingKeys.readyDisabled, false);
  assert.equal(missingKeys.destination, '/settings#account');
  assert.ok(missingKeys.nodes.get('#readiness-detail').textContent.includes('등록·복구'));
  assert.equal((await scenario()).destination, '/live');
  assert.equal((await scenario({statusFails: true})).destination, '/live');
  const noDatabase = await scenario({database: false});
  assert.equal(noDatabase.readyDisabled, true);
  assert.equal(noDatabase.destination, undefined);
  assert.ok(!noDatabase.requests.includes('/auth/login'));
  assert.ok(noDatabase.timers.length > 0);
  const rejected = await scenario({loginOk: false});
  assert.equal(rejected.destination, undefined);
  assert.ok(!rejected.requests.includes('/live/orders/real/readiness'));
  assert.equal(rejected.nodes.get('#password').value, '');
  assert.equal(rejected.nodes.get('#login-message').textContent, 'Wrong password');
  console.log('Login: missing keys, configured connection, status failure, DB outage and rejected credentials passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
