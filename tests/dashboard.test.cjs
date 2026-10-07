const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const script = fs.readFileSync('app/static/dashboard.js', 'utf8');
function setup(fetch) {
  const nodes = new Map();
  function node() { return {textContent: '', value: '', children: [], listeners: {},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    replaceChildren() { this.children = []; }, append(child) { this.children.push(child); }}; }
  const get = id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  vm.runInNewContext(script, {document: {getElementById: get, createElement: node}, fetch,
    AbortSignal, Date, Number, String, Promise, Error});
  return get;
}
const metrics = {total_requests: 0, routes: {}, provider_attempts: 0, failed_requests: 0,
  average_latency_ms: null, p95_latency_ms: null, known_estimated_cost_usd: '0',
  unknown_cost_requests: 0, retention_days: 30};
const response = body => ({ok: true, json: async () => body});
const settle = () => new Promise(resolve => setImmediate(resolve));
test('empty metrics retain unknown latency and show empty state', async () => {
  const get = setup(async url => response(url === '/metrics' ? metrics : {requests: []}));
  await settle();
  assert.equal(get('total').textContent, '0');
  assert.equal(get('latency').textContent, 'Unknown / Unknown');
  assert.match(get('status').textContent, /No requests yet/);
  assert.equal(get('refresh').disabled, false);
});
test('refresh failure is visible and restores controls', async () => {
  const get = setup(async () => { throw new Error('offline'); });
  await settle();
  assert.match(get('status').textContent, /may be stale/);
  assert.equal(get('refresh').disabled, false);
});
test('query and history render untrusted strings as plain text', async () => {
  const sent = [];
  const raw = '<img src=x onerror=alert(1)>';
  const get = setup(async (url, options) => {
    if (url === '/query') {
      sent.push(JSON.parse(options.body));
      return response({answer: raw, route: 'system2', latency_ms: 1, metadata_persisted: false});
    }
    return response(url === '/metrics' ? {...metrics, total_requests: 1} : {requests: [
      {created_at: '2026-01-01', route: 'system2', status_code: 200, latency_ms: 1, model: raw}]});
  });
  await settle();
  get('prompt').value = ' test ';
  await get('query-form').listeners.submit({preventDefault() {}});
  assert.deepEqual(sent, [{prompt: 'test'}]);
  assert.equal(get('answer').textContent, raw);
  assert.equal(get('history-rows').children[0].children[4].textContent, raw);
  assert.match(get('query-status').textContent, /not saved/);
  assert.equal(get('submit').disabled, false);
});
test('provider failure does not leave old answer or disabled submit button', async () => {
  const get = setup(async url => url === '/query'
    ? {ok: false, status: 503, json: async () => ({error: 'provider_unavailable'})}
    : response(url === '/metrics' ? metrics : {requests: []}));
  await settle();
  get('prompt').value = 'Explain consensus';
  get('answer').textContent = 'old answer';
  await get('query-form').listeners.submit({preventDefault() {}});
  assert.equal(get('answer').textContent, '');
  assert.match(get('query-status').textContent, /provider_unavailable/);
  assert.equal(get('submit').disabled, false);
});
