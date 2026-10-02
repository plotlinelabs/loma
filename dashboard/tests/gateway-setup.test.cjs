const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');
function load(file, imports = {}, env = {}) {
  const source = fs.readFileSync(path.join(__dirname, '../src/', file), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  const context = vm.createContext({ exports: {}, process: { env }, Request, Response, URL, AbortSignal, fetch: (...args) => global.fetch(...args),
    require: (key) => key in imports ? imports[key] : require(key) });
  vm.runInContext(outputText, context);
  return context.exports;
}
function fixture({ role = 'admin', email = 'admin@example.test', status = 'active', deleted = false, connected = true } = {}) {
  let writes = 0;
  const route = load('app/gateway-setup/route.ts', {
    '@/auth': { auth: async () => ({ user: { email } }) },
    '@/auth-node': { getUsersCollection: async () => ({ findOne: async () => ({ system_role: role, status, deleted }) }) },
    '@/lib/gateway-config': { configureGateway: async () => { writes++; }, getGatewaySecret: async () => 's'.repeat(64) },
    '@/lib/same-origin': load('lib/same-origin.ts'),
    '@/lib/session-gateway': { sessionGateway: async () => Response.json({ connected: true, scheduler_running: false }, { status: connected ? 200 : 503 }) },
  });
  return { route, writes: () => writes };
}
// Mirrors production: nginx forwards the public host while request.url is the internal address.
const INTERNAL = 'http://0.0.0.0:3001/gateway-setup';
const req = (origin = 'https://loma.test') => new Request(INTERNAL, { method: 'POST', headers: { origin, 'x-forwarded-host': 'loma.test' } });
for (const role of ['chatter', 'operator', 'analyst', undefined]) {
  test(`reject non-admin role ${role}`, async () => {
    const f = fixture({ role: role || 'unknown' });
    assert.equal((await f.route.POST(req())).status, 403); assert.equal(f.writes(), 0);
  });
}
for (const options of [{ status: 'disabled' }, { deleted: true }, { email: '' }]) {
  test(`reject ineligible session ${JSON.stringify(options)}`, async () => {
    const f = fixture(options); assert.ok([401, 403].includes((await f.route.POST(req())).status)); assert.equal(f.writes(), 0);
  });
}
test('reject cross-origin setup', async () => {
  const f = fixture(); assert.equal((await f.route.POST(req('https://evil.test'))).status, 403); assert.equal(f.writes(), 0);
});
for (const role of ['admin', 'maintainer']) {
  test(`${role} can set up without returning a secret`, async () => {
    const f = fixture({ role }); const result = await f.route.POST(req());
    assert.deepEqual(await result.json(), { configured: true, connected: true, scheduler_running: false });
    assert.equal(f.writes(), 1); assert.equal(result.headers.get('cache-control'), 'no-store');
  });
}
test('GET never creates configuration and backend failures are explicit', async () => {
  const f = fixture({ connected: false }); const result = await f.route.GET(new Request('https://loma.test/gateway-setup'));
  assert.equal((await result.json()).connected, false); assert.equal(f.writes(), 0);
});
test('setup retries preserve the original key and creator', async () => {
  let saved;
  const collection = { findOne: async () => saved, updateOne: async (filter, update, options) => {
    assert.equal(filter._id, 'human-session'); assert.equal(options.upsert, true);
    saved ??= { _id: filter._id, ...update.$setOnInsert };
  }};
  const config = load('lib/gateway-config.ts', { 'server-only': {}, '@/auth-node': { getDashboardDb: async () => ({ collection: () => collection }) } }, { LOMA_WORK_GATEWAY_SECRET: 'legacy' });
  assert.equal(await config.getGatewaySecret(), 'legacy');
  await config.configureGateway('admin@example.test'); const key = await config.getGatewaySecret();
  assert.equal(key.length, 64);
  await config.configureGateway('second@example.test'); assert.equal(await config.getGatewaySecret(), key); assert.equal(saved.created_by, 'admin@example.test');
});
test('setup database failure is fail-closed', async () => {
  const config = load('lib/gateway-config.ts', { 'server-only': {}, '@/auth-node': { getDashboardDb: async () => { throw Error('offline'); } } }, { LOMA_WORK_GATEWAY_SECRET: 'legacy' });
  await assert.rejects(config.getGatewaySecret());
});

test('maintainer cannot change runtime permissions', async () => {
  const f = fixture({ role: 'maintainer' });
  const r = new Request(INTERNAL, { method: 'POST',
    headers: { origin: 'https://loma.test', 'x-forwarded-host': 'loma.test' }, body: JSON.stringify({ action: 'save-settings', settings: {} }) });
  assert.equal((await f.route.POST(r)).status, 403);
});
test('runtime settings validate, normalize and support revocation without file IO', async () => {
  const writes = [];
  const config = load('lib/gateway-config.ts', { 'server-only': {}, '@/auth-node': {
    getDashboardDb: async () => ({ collection: name => name === 'users' ?
      { findOne: async ({ email }) => email === 'active@example.test' ? { status: 'active' } : null } :
      { updateOne: async (...args) => writes.push(args) } })
  } });
  for (const data of [null, {}, [], { bounded_work_enabled: 'yes', ashby_allowed_users: [] },
      { bounded_work_enabled: true, ashby_allowed_users: ['nope'] },
      { bounded_work_enabled: true, ashby_allowed_users: ['missing@example.test'] },
      { bounded_work_enabled: true, ashby_allowed_users: [], secret: 'forged' }]) {
    await assert.rejects(config.saveRuntimeSettings('admin@example.test', data));
  }
  assert.equal(writes.length, 0);
  await config.saveRuntimeSettings('admin@example.test', { bounded_work_enabled: true,
    ashby_allowed_users: ['ACTIVE@example.test', 'active@example.test'] });
  assert.equal(JSON.stringify(writes[0][1].$set.ashby_allowed_users), '["active@example.test"]');
  await config.saveRuntimeSettings('admin@example.test', { bounded_work_enabled: false, ashby_allowed_users: [] });
  assert.equal(writes[1][1].$set.ashby_allowed_users.length, 0);
  assert.equal(writes[1][1].$set.updated_by, 'admin@example.test');
});

const originCase = (headers, env = {}) => load('lib/same-origin.ts', {}, env).isSameOrigin(new Request(INTERNAL, { method: 'POST', headers }));
test('same-origin check uses the public host behind a proxy', () => {
  assert.equal(originCase({ origin: 'https://loma.test', 'x-forwarded-host': 'loma.test' }), true);
  assert.equal(originCase({ origin: 'https://LOMA.test', 'x-forwarded-host': 'loma.test, internal' }), true);
  assert.equal(originCase({ origin: 'http://localhost:3001', host: 'localhost:3001' }), true);
  // Public address on a non-default port: nginx forwards the host without the port, AUTH_URL covers it.
  assert.equal(originCase({ origin: 'https://loma.test:8443', 'x-forwarded-host': 'loma.test' }), false);
  assert.equal(originCase({ origin: 'https://loma.test:8443', 'x-forwarded-host': 'loma.test' }, { AUTH_URL: 'https://loma.test:8443' }), true);
});
test('same-origin check rejects foreign, missing and malformed origins', () => {
  assert.equal(originCase({ origin: 'https://evil.test', 'x-forwarded-host': 'loma.test' }), false);
  assert.equal(originCase({ origin: 'https://loma.test.evil.test', 'x-forwarded-host': 'loma.test' }), false);
  assert.equal(originCase({ origin: 'https://evil.test', 'x-forwarded-host': 'loma.test' }, { AUTH_URL: 'https://loma.test' }), false);
  assert.equal(originCase({ 'x-forwarded-host': 'loma.test' }), false);
  assert.equal(originCase({ origin: 'null', 'x-forwarded-host': 'loma.test' }), false);
  assert.equal(originCase({ origin: 'https://loma.test' }), false);
  // The internal address itself is no longer accepted as an origin.
  assert.equal(originCase({ origin: 'http://0.0.0.0:3001', 'x-forwarded-host': 'loma.test' }), false);
  assert.equal(originCase({ origin: 'https://loma.test', 'x-forwarded-host': 'loma.test' }, { AUTH_URL: 'not a url' }), true);
});
test('human decisions pass the proxy origin check and foreign origins are blocked', async () => {
  const calls = [];
  const context = { params: Promise.resolve({ path: ['task-1', 'respond'] }) };
  const gateway = load('lib/session-gateway.ts', {
    '@/auth': { auth: async () => ({ user: { email: 'approver@example.test' } }) },
    '@/lib/gateway-config': { getGatewaySecret: async () => 's'.repeat(64) },
    '@/lib/same-origin': load('lib/same-origin.ts'),
  });
  const original = global.fetch;
  global.fetch = async (url, init) => { calls.push([url, init]); return Response.json({ ok: true }); };
  try {
    const send = origin => gateway.sessionGateway(new Request('http://0.0.0.0:3001/human-tasks/task-1/respond',
      { method: 'POST', headers: { origin, 'x-forwarded-host': 'loma.test' }, body: '{"decision":"approve"}' }), context, '/api/human-tasks');
    assert.equal((await send('https://evil.test')).status, 403); assert.equal(calls.length, 0);
    assert.equal((await send('https://loma.test')).status, 200); assert.equal(calls.length, 1);
    assert.equal(calls[0][1].headers['X-User-Email'], 'approver@example.test');
  } finally { global.fetch = original; }
});
