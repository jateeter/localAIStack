#!/usr/bin/env node
/**
 * openclaw-adapter.mjs
 *
 * Bridges PE ACP handoff receipts to the OpenClaw xACP gateway and posts
 * completion results back to the PE /api/integrations/completions endpoint.
 *
 * The adapter subscribes to the PE SSE event stream and reacts to
 * `acp.handoff.accepted` events. For each handoff it:
 *   1. Connects to the OpenClaw xACP gateway via WebSocket
 *   2. Activates / resumes the target agent with the handoff envelope
 *   3. Collects the result and POSTs it to the PE completion endpoint
 *
 * Usage:
 *   node scripts/openclaw-adapter.mjs [options]
 *
 * Options:
 *   --pe-url <url>            PE base URL
 *                               (default: $PE_URL or http://localhost:5300)
 *   --gateway-url <wsurl>     OpenClaw xACP gateway WebSocket URL
 *                               (default: $ACP_GATEWAY_URL or ws://127.0.0.1:18789)
 *   --session-key <key>       ACP session key
 *                               (default: $ACP_SESSION_KEY or agent:main:main)
 *   --agent <name>            Default target agent
 *                               (default: $ACP_TARGET_AGENT or openclaw)
 *   --source-mapping-id <id> Completion source mapping ID sent to the PE
 *                               (default: $ACP_COMPLETION_SOURCE_MAPPING_ID
 *                                        or acp-openclaw-completion)
 *   --timeout-ms <n>          Per-handoff agent timeout in ms (default: 30000)
 *   --retry-ms <n>            SSE reconnect delay in ms (default: 5000)
 *   --dry-run                 Log handoffs without calling OpenClaw or posting
 */

import https from 'node:https';
import http  from 'node:http';
import { parseArgs } from 'node:util';

// ── CLI / env ──────────────────────────────────────────────────────────────

const { values: args } = parseArgs({
  options: {
    'pe-url':            { type: 'string',  default: process.env.PE_URL            || 'http://localhost:5300' },
    'gateway-url':       { type: 'string',  default: process.env.ACP_GATEWAY_URL   || 'ws://127.0.0.1:18789' },
    'session-key':       { type: 'string',  default: process.env.ACP_SESSION_KEY   || 'agent:main:main' },
    'agent':             { type: 'string',  default: process.env.ACP_TARGET_AGENT  || 'openclaw' },
    'source-mapping-id': { type: 'string',  default: process.env.ACP_COMPLETION_SOURCE_MAPPING_ID || 'acp-openclaw-completion' },
    'timeout-ms':        { type: 'string',  default: '30000' },
    'retry-ms':          { type: 'string',  default: '5000' },
    'dry-run':           { type: 'boolean', default: false },
  },
});

const PE_URL        = args['pe-url'].replace(/\/$/, '');
const GATEWAY_URL   = args['gateway-url'];
const SESSION_KEY   = args['session-key'];
const DEFAULT_AGENT = args['agent'];
const SM_ID         = args['source-mapping-id'];
const TIMEOUT_MS    = parseInt(args['timeout-ms'], 10);
const RETRY_MS      = parseInt(args['retry-ms'],   10);
const DRY_RUN       = args['dry-run'];

console.log('[adapter] openclaw-adapter starting');
console.log(`[adapter]   PE url        : ${PE_URL}`);
console.log(`[adapter]   Gateway url   : ${GATEWAY_URL}`);
console.log(`[adapter]   Session key   : ${SESSION_KEY}`);
console.log(`[adapter]   Agent default : ${DEFAULT_AGENT}`);
console.log(`[adapter]   Source mapping: ${SM_ID}`);
if (DRY_RUN) console.log('[adapter]   DRY RUN — no actual gateway calls or PE posts');

// ── HTTP helpers ───────────────────────────────────────────────────────────

function httpPost(baseUrl, path, body) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const target  = new URL(`${baseUrl}${path}`);
    const lib     = target.protocol === 'https:' ? https : http;
    const req     = lib.request({
      hostname:           target.hostname,
      port:               target.port || (target.protocol === 'https:' ? 443 : 80),
      path:               target.pathname,
      method:             'POST',
      headers:            { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) },
      rejectUnauthorized: false,
    }, (res) => {
      let data = '';
      res.on('data', (c) => { data += c; });
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: JSON.parse(data) }); }
        catch { resolve({ status: res.statusCode, body: data }); }
      });
    });
    req.on('error', reject);
    req.write(payload);
    req.end();
  });
}

// ── OpenClaw xACP gateway ──────────────────────────────────────────────────
//
// xACP wire protocol (OpenClaw gateway, port 18789):
//   Connect:  ws://<host>:<port>
//   Activate: send JSON  { "type": "activate", "sessionKey": "<key>",
//                          "agentId": "<agent>", "envelopeId": "<id>",
//                          "payload": { "prompt": "<text>", ... } }
//   Response: receive JSON { "type": "result", "envelopeId": "<id>",
//                            "completed": 1.0, "failed": 0.0,
//                            "confidence": 0.95, "actionClass": 0.0,
//                            "output": { ... } }
//             or           { "type": "error", "reason": "..." }
//
// The WebSocket class is built-in since Node 22 (globalThis.WebSocket).

async function callOpenClaw({ gatewayUrl, sessionKey, agentId, envelopeId, prompt, dispatchId }) {
  return new Promise((resolve, reject) => {
    const ws      = new WebSocket(gatewayUrl);
    const timer   = setTimeout(() => {
      ws.close();
      reject(new Error(`xACP timeout after ${TIMEOUT_MS}ms (dispatchId=${dispatchId})`));
    }, TIMEOUT_MS);

    ws.addEventListener('open', () => {
      ws.send(JSON.stringify({
        type:       'activate',
        sessionKey,
        agentId,
        envelopeId,
        payload:    { prompt: prompt || `acp:${dispatchId}`, dispatchId },
      }));
    });

    ws.addEventListener('message', (ev) => {
      clearTimeout(timer);
      ws.close();
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'error') { reject(new Error(`xACP error: ${msg.reason}`)); return; }
        resolve(msg);
      } catch (e) { reject(e); }
    });

    ws.addEventListener('error', (ev) => {
      clearTimeout(timer);
      reject(new Error(`xACP WebSocket error: ${ev.message || 'unknown'}`));
    });
  });
}

// ── Completion post ────────────────────────────────────────────────────────

async function postCompletion({ agentId, result, dispatchId, envelopeId, correlationId }) {
  const ts      = Date.now();
  const sensorId = `acp.openclaw.${agentId}.completion`;
  const payload = {
    provider:        'openclaw',
    agent:           agentId,
    sourceMappingId: SM_ID,
    sensorId,
    values: [
      result.completed    ?? 1.0,
      result.failed       ?? 0.0,
      result.confidence   ?? 0.95,
      result.actionClass  ?? 0.0,
    ],
    correlationId:  correlationId || `acp-${ts}`,
    envelopeId:     envelopeId   || `env-${ts}`,
    dispatchId,
    completionId:   `compl-${ts}`,
    metadata:       { provider: 'openclaw', agent: agentId, output: result.output },
  };

  if (DRY_RUN) {
    console.log(`[adapter] DRY RUN — would post completion for dispatch ${dispatchId}:`);
    console.log(JSON.stringify(payload, null, 2));
    return { status: 200, body: { dryRun: true } };
  }

  const res = await httpPost(PE_URL, '/api/integrations/completions', payload);
  if (res.status < 200 || res.status >= 300) {
    throw new Error(`PE completions returned ${res.status}: ${JSON.stringify(res.body)}`);
  }
  return res;
}

// ── Handoff handler ────────────────────────────────────────────────────────

async function handleHandoff(record) {
  const dispatchId   = record.id;
  const agentId      = record.agentId || DEFAULT_AGENT;
  const envelopeId   = record.envelopeId   || record.body?.envelopeId   || `env-${dispatchId}`;
  const correlationId = record.correlationId || record.body?.correlationId;
  const prompt        = record.body?.prompt || record.body?.content;

  console.log(`[adapter] handoff  dispatch=${dispatchId}  agent=${agentId}`);

  try {
    let result;
    if (DRY_RUN) {
      console.log(`[adapter] DRY RUN — skipping xACP call for dispatch ${dispatchId}`);
      result = { completed: 1.0, failed: 0.0, confidence: 0.95, actionClass: 0.0 };
    } else {
      result = await callOpenClaw({
        gatewayUrl:  record.endpoint || GATEWAY_URL,
        sessionKey:  record.sessionKey || SESSION_KEY,
        agentId,
        envelopeId,
        prompt,
        dispatchId,
      });
    }

    const res = await postCompletion({ agentId, result, dispatchId, envelopeId, correlationId });
    console.log(`[adapter] complete dispatch=${dispatchId}  status=${res.status}`);
  } catch (err) {
    console.error(`[adapter] failed  dispatch=${dispatchId}:`, err.message);
  }
}

// ── SSE subscription ───────────────────────────────────────────────────────

function subscribeSSE() {
  const target = new URL(`${PE_URL}/api/events`);
  const lib    = target.protocol === 'https:' ? https : http;

  console.log(`[adapter] connecting SSE  ${PE_URL}/api/events`);

  const req = lib.request({
    hostname:           target.hostname,
    port:               target.port || (target.protocol === 'https:' ? 443 : 80),
    path:               '/api/events',
    method:             'GET',
    headers:            { Accept: 'text/event-stream' },
    rejectUnauthorized: false,
  }, (res) => {
    if (res.statusCode !== 200) {
      console.error(`[adapter] SSE ${res.statusCode} — retry in ${RETRY_MS}ms`);
      res.resume();
      setTimeout(subscribeSSE, RETRY_MS);
      return;
    }

    console.log('[adapter] SSE connected');
    res.setEncoding('utf8');
    let buf = '';

    res.on('data', (chunk) => {
      buf += chunk;
      const lines = buf.split('\n');
      buf = lines.pop();
      let event = null;
      for (const line of lines) {
        if (line.startsWith('event:')) { event = line.slice(6).trim(); }
        else if (line.startsWith('data:')) {
          const data = line.slice(5).trim();
          if (!data || data === ':heartbeat') continue;
          try {
            const msg = JSON.parse(data);
            if (msg.type === 'acp.handoff.accepted' && msg.record) {
              handleHandoff(msg.record);
            }
          } catch {
            // non-JSON SSE data (heartbeat, plain text) — ignore
          }
        }
      }
    });

    res.on('end', () => {
      console.warn(`[adapter] SSE stream ended — retry in ${RETRY_MS}ms`);
      setTimeout(subscribeSSE, RETRY_MS);
    });

    res.on('error', (err) => {
      console.error(`[adapter] SSE error: ${err.message} — retry in ${RETRY_MS}ms`);
      setTimeout(subscribeSSE, RETRY_MS);
    });
  });

  req.on('error', (err) => {
    console.error(`[adapter] SSE connect error: ${err.message} — retry in ${RETRY_MS}ms`);
    setTimeout(subscribeSSE, RETRY_MS);
  });

  req.end();
}

// ── Entry point ────────────────────────────────────────────────────────────

subscribeSSE();
