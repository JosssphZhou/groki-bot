#!/usr/bin/env node
// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: MIT
/** Local validation bridge for Grok Bot 0.71.2's existing macOS login.
 * Never persists credentials or refreshes/modifies the client's account state.
 * Internal APIs are version dependent; expired sessions must be renewed in the app.
 */
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { pbkdf2Sync, createDecipheriv, randomUUID } from 'node:crypto';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createServer } from 'node:http';

const BACKEND = 'https://api2.cursor.sh/aiserver.v1.GrokBotService/';
export const RPC_ROUTES = Object.freeze({
  'GET /credential': 'MintSandVoiceCallSecret',
  'POST /session': 'VoiceCallHarnessSession',
  'POST /tool': 'VoiceCallHarnessTool',
});

export function loadAccount() {
  const data = JSON.parse(readFileSync(join(homedir(), 'Library/Application Support/Grok Bot/sand-secrets.json'), 'utf8'));
  const accounts = JSON.parse(data['cursor-accounts']);
  const active = accounts.accounts[accounts.active];
  if (!active) throw new Error('login_unavailable');
  const password = execFileSync('/usr/bin/security', ['find-generic-password', '-w', '-s', 'Grok Bot Safe Storage'], {
    encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 10000,
  }).trimEnd();
  const key = pbkdf2Sync(password, 'saltysalt', 1003, 16, 'sha1');
  const decrypt = (value) => {
    const blob = Buffer.from(value, 'base64');
    if (blob.subarray(0, 3).toString() !== 'v10') throw new Error('unsupported_storage');
    const cipher = createDecipheriv('aes-128-cbc', key, Buffer.alloc(16, 32));
    return Buffer.concat([cipher.update(blob.subarray(3)), cipher.final()]).toString('utf8');
  };
  const token = decrypt(active['cursor-access-token']);
  const claims = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString());
  if (!Number.isFinite(claims.exp) || claims.exp * 1000 < Date.now() + 30000) throw new Error('login_expired_open_grok_bot');
  const machineId = decrypt(data['cursor-machine-id']);
  const teamId = active['cursor-selected-team-id'] ? decrypt(active['cursor-selected-team-id']) : undefined;
  return { token, machineId, teamId };
}

function checksum(machineId) {
  const t = Math.floor(Date.now() / 1e6);
  const bytes = Buffer.from([t >> 40 & 255, t >> 32 & 255, t >> 24 & 255, t >> 16 & 255, t >> 8 & 255, t & 255]);
  let previous = 165;
  for (let i = 0; i < bytes.length; i++) { bytes[i] = (bytes[i] ^ previous) + i % 256; previous = bytes[i]; }
  return bytes.toString('base64url') + machineId;
}

export async function accountRpc(method, body, { accountLoader = loadAccount, fetcher = fetch } = {}) {
  if (!Object.values(RPC_ROUTES).includes(method)) throw new Error('rpc_forbidden');
  const account = accountLoader();
  const headers = {
    authorization: `Bearer ${account.token}`, 'content-type': 'application/json',
    'connect-protocol-version': '1', 'x-cursor-client-type': 'sand',
    'x-cursor-client-version': '0.71.2', 'x-cursor-client-source': 'sand-desktop',
    'x-cursor-client-os': 'darwin', 'x-sand-box-namespace': 'prod',
    'x-ghost-mode': 'true', 'x-cursor-checksum': checksum(account.machineId),
    'x-request-id': randomUUID(), 'x-cursor-timezone': Intl.DateTimeFormat().resolvedOptions().timeZone,
  };
  if (account.teamId) headers['x-cursor-team-id'] = account.teamId;
  const response = await fetcher(BACKEND + method, { method: 'POST', headers,
    body: JSON.stringify(body), redirect: 'error', signal: AbortSignal.timeout(20000) });
  if (!response.ok) {
    // Upstream bodies can contain user content or credentials; never expose them.
    throw new Error(`upstream_http_${response.status}`);
  }
  return response.json();
}

export function validateBody(route, body) {
  if (route === 'GET /credential') return {};
  if (!body || typeof body !== 'object' || Array.isArray(body)) throw new Error('invalid_body');
  const fields = route === 'POST /session'
    ? ['agentId', 'agentName', 'userName', 'description', 'spokenLanguage']
    : ['agentId', 'callId', 'name', 'input', 'line'];
  if (Object.keys(body).some(k => !fields.includes(k))) throw new Error('invalid_body');
  if (typeof body.agentId !== 'string' || !body.agentId || body.agentId.length > 200) throw new Error('invalid_agent');
  if (route === 'POST /session') {
    if (typeof body.agentName !== 'string') throw new Error('invalid_name');
    for (const k of fields.slice(2)) if (body[k] !== undefined && typeof body[k] !== 'string') throw new Error('invalid_body');
  } else {
    if (typeof body.callId !== 'string' || !body.callId || typeof body.name !== 'string' || !body.name) throw new Error('invalid_tool');
    const lineKeys = ['theyJustTalked', 'newsOwed', 'receiptOwed', 'alreadySpokeThisTurn'];
    if (!body.line || Object.keys(body.line).some(k => !lineKeys.includes(k)) || lineKeys.some(k => typeof body.line[k] !== 'boolean')) throw new Error('invalid_line');
  }
  return body;
}

export function createBridge({ port = 18771, rpc = accountRpc, agentId = process.env.STACKCHAN_GBOT_VOICE_BOT_ID || process.env.STACKCHAN_GBOT_VOICE_AGENT_ID || process.env.STACKCHAN_TOOL_BOT_ID, toolsEnabled = process.env.STACKCHAN_GBOT_VOICE_TOOLS === '1' } = {}) {
  return createServer(async (req, res) => {
    const send = (status, value) => { res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' }); res.end(JSON.stringify(value)); };
    const address = req.socket.remoteAddress;
    if (address !== '127.0.0.1' || req.headers.origin !== undefined || req.headers['sec-fetch-site'] && req.headers['sec-fetch-site'] !== 'none' || req.headers.host !== `127.0.0.1:${port}`) return send(403, { ok: false, error: 'local_client_only' });
    const route = `${req.method} ${req.url}`;
    if (route === 'GET /health') return send(200, { ok: true, source: 'grok-bot-existing-login', clientVersion: '0.71.2', agentId: agentId || null, toolsEnabled });
    const method = RPC_ROUTES[route];
    if (!method) return send(404, { ok: false, error: 'route_not_found' });
    if (route === 'POST /tool' && !toolsEnabled) return send(403, { ok: false, error: 'tools_disabled' });
    try {
      let raw = '';
      for await (const chunk of req) { raw += chunk; if (Buffer.byteLength(raw) > 65536) return send(413, { ok: false, error: 'body_too_large' }); }
      if (req.method === 'POST' && req.headers['content-type']?.split(';')[0] !== 'application/json') return send(415, { ok: false, error: 'json_required' });
      let body;
      try { body = validateBody(route, raw ? JSON.parse(raw) : {}); } catch { return send(400, { ok: false, error: 'invalid_body' }); }
      if (req.method === 'POST' && (!agentId || body.agentId !== agentId)) return send(403, { ok: false, error: 'agent_not_allowed' });
      const result = await rpc(method, body);
      if (route === 'GET /credential') return send(200, { ok: true, credential: {
        clientSecret: result.clientSecret, expiresAtUnixSeconds: Number(result.expiresAtUnixSeconds),
        model: result.model, websocketUrl: result.websocketUrl,
      } });
      if (route === 'POST /tool') {
        const kinds = { VOICE_CALL_HARNESS_TOOL_OUTCOME_KIND_SERVED: 'served', VOICE_CALL_HARNESS_TOOL_OUTCOME_KIND_UNKNOWN_TOOL: 'unknown_tool', VOICE_CALL_HARNESS_TOOL_OUTCOME_KIND_INVALID_INPUT: 'invalid_input', 1: 'served', 2: 'unknown_tool', 3: 'invalid_input' };
        return send(200, { ...result, kind: kinds[result.kind] ?? result.kind });
      }
      return send(200, result);
    } catch (error) {
      const safe = /^(upstream_http_\d{3}|login_unavailable|login_expired_open_grok_bot|unsupported_storage)$/.test(error.message) ? error.message : 'bridge_request_failed';
      return send(502, { ok: false, error: safe });
    }
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const port = Number(process.env.GBOT_VOICE_BRIDGE_PORT || 18771);
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('invalid_port');
  const server = createBridge({ port });
  server.requestTimeout = 25000;
  server.listen(port, '127.0.0.1', () => console.log(`Grok Bot voice session bridge listening on 127.0.0.1:${port}`));
}
