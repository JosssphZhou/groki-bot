// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: MIT
import test from 'node:test';
import assert from 'node:assert/strict';
import { createBridge, accountRpc, validateBody } from './gbot_voice_session_bridge.mjs';
import { createServer } from 'node:net';
import { request } from 'node:http';

async function freePort() {
  const s = createServer(); await new Promise(r => s.listen(0, '127.0.0.1', r));
  const port = s.address().port; await new Promise(r => s.close(r)); return port;
}

test('loopback bridge rejects cross origin, wrong host, unknown RPC without consulting login', async () => {
  let calls = 0; const port = await freePort();
  const server = createBridge({port, agentId: 'a', rpc: async (method, body) => { calls++; return { instructions: 'test', method, body }; }});
  await new Promise(r => server.listen(port, '127.0.0.1', r));
  try {
    const url = `http://127.0.0.1:${port}`;
    assert.equal((await fetch(url + '/credential', {headers:{Origin:'https://example.com'}})).status, 403);
    const wrongHostStatus = await new Promise((resolve, reject) => { const req = request(url + '/credential', {headers:{Host:`localhost:${port}`}}, res => {res.resume(); resolve(res.statusCode);}); req.on('error', reject); req.end(); });
    assert.equal(wrongHostStatus, 403);
    assert.equal((await fetch(url + '/api/sendPrompt', {method:'POST'})).status, 404);
    assert.equal((await fetch(url + '/session', {method:'POST',headers:{'Content-Type':'application/json'},body:'{"agentId":"a","agentName":"b","rpc":"DeleteAgent"}'})).status, 400);
    assert.equal((await fetch(url + '/tool', {method:'POST'})).status, 403);
    assert.equal((await fetch(url + '/session', {method:'POST',headers:{'Content-Type':'application/json'},body:'{"agentId":"other","agentName":"b"}'})).status, 403);
    assert.equal(calls, 0);
    const response = await fetch(url + '/session', {method:'POST',headers:{'Content-Type':'application/json'},body:'{"agentId":"a","agentName":"b"}'});
    assert.equal(response.status, 200); assert.equal((await response.json()).method, 'VoiceCallHarnessSession'); assert.equal(calls, 1);
  } finally { await new Promise(r => server.close(r)); }
});

test('account RPC only admits three fixed methods and never leaks upstream response body', async () => {
  let loads = 0; const accountLoader = () => {loads++; return {token:'FAKE_TEST',machineId:'FAKE_MACHINE'};};
  await assert.rejects(accountRpc('SendPrompt', {}, {accountLoader}), /rpc_forbidden/); assert.equal(loads, 0);
  const fetcher = async (url, options) => {
    assert.equal(url, 'https://api2.cursor.sh/aiserver.v1.GrokBotService/MintSandVoiceCallSecret');
    assert.equal(options.redirect, 'error'); return {ok:false,status:403,json:async()=>({token:'DO_NOT_LEAK'})};
  };
  await assert.rejects(accountRpc('MintSandVoiceCallSecret', {}, {accountLoader,fetcher}), /^Error: upstream_http_403$/);
});

test('tool invocation requires complete bounded line state', () => {
  assert.throws(() => validateBody('POST /tool', {agentId:'a',callId:'c',name:'x',input:{},line:{}}), /invalid_line/);
});
