const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');
function load(file, imports = {}, env = {}) {
  const source = fs.readFileSync(path.join(__dirname, '../src/', file), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  const context = vm.createContext({ exports: {}, process: { env }, Request, Response, URL, AbortSignal,
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
    '@/lib/session-gateway': { sessionGateway: async () => Response.json({ connected: true, scheduler_running: false }, { status: connected ? 200 : 503 }) },
  });
  return { route, writes: () => writes };
}
const req = (origin = 'https://loma.test') => new Request('https://loma.test/gateway-setup', { method: 'POST', headers: { origin } });
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
