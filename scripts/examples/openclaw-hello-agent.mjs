#!/usr/bin/env node
/**
 * openclaw-hello-agent.mjs
 *
 * Phase 1 e2e fixture: deterministic OpenClaw completion, no gateway required.
 * Simulates a finished agent by posting directly to PE /api/integrations/completions.
 *
 * Usage:
 *   node scripts/examples/openclaw-hello-agent.mjs [options]
 *
 * Options:
 *   --pe-url <url>              PE base URL  (default: $PE_URL or http://localhost:5300)
 *   --agent <name>              Agent name   (default: hello-world)
 *   --source-mapping-id <id>   Source mapping to resolve sensorId (default: acp-openclaw-completion)
 *   --values <n,n,n,n>         Completion values [completed,failed,confidence,actionClass]
 *                               (default: 1,0,0.95,0)
 *   --correlation-id <id>      Correlation ID to echo back
 *   --envelope-id <id>         Envelope ID to echo back
 *   --completion-id <id>       Explicit completion ID
 *   --dry-run                  Print payload without posting
 */

import https from 'node:https';
import http  from 'node:http';
import { parseArgs } from 'node:util';

const { values: args } = parseArgs({
  options: {
    'pe-url':             { type: 'string',  default: process.env.PE_URL || 'http://localhost:5300' },
    'agent':              { type: 'string',  default: 'hello-world' },
    'source-mapping-id':  { type: 'string',  default: 'acp-openclaw-completion' },
    'values':             { type: 'string',  default: '1,0,0.95,0' },
    'correlation-id':     { type: 'string',  default: '' },
    'envelope-id':        { type: 'string',  default: '' },
    'completion-id':      { type: 'string',  default: '' },
    'dry-run':            { type: 'boolean', default: false },
  },
});

const peUrl    = args['pe-url'].replace(/\/$/, '');
const agent    = args['agent'];
const smId     = args['source-mapping-id'];
const values   = args['values'].split(',').map(Number);
const ts       = Date.now();
const sensorId = `acp.openclaw.${agent}.completion`;

const payload = {
  provider:        'openclaw',
  agent,
  sourceMappingId: smId,
  sensorId,
  values,
  correlationId:   args['correlation-id'] || `hello-${ts}`,
  envelopeId:      args['envelope-id']    || `env-${ts}`,
  completionId:    args['completion-id']  || `compl-${ts}`,
  metadata: {
    fixture:   'openclaw-hello-agent',
    message:   'hello world from OpenClaw',
    timestamp: ts,
  },
};

if (args['dry-run']) {
  console.log('DRY RUN — payload:');
  console.log(JSON.stringify(payload, null, 2));
  process.exit(0);
}

const body   = JSON.stringify(payload);
const target = new URL(`${peUrl}/api/integrations/completions`);
const lib    = target.protocol === 'https:' ? https : http;

const req = lib.request({
  hostname:           target.hostname,
  port:               target.port || (target.protocol === 'https:' ? 443 : 80),
  path:               target.pathname,
  method:             'POST',
  headers:            { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
  rejectUnauthorized: false,
}, (res) => {
  let data = '';
  res.on('data', (chunk) => { data += chunk; });
  res.on('end', () => {
    if (res.statusCode >= 200 && res.statusCode < 300) {
      console.log(`[openclaw-hello] OK ${res.statusCode}  sensorId=${sensorId}`);
      try { console.log(JSON.stringify(JSON.parse(data), null, 2)); }
      catch { console.log(data); }
    } else {
      console.error(`[openclaw-hello] FAIL ${res.statusCode}:`, data);
      process.exit(1);
    }
  });
});

req.on('error', (err) => {
  console.error('[openclaw-hello] request error:', err.message);
  process.exit(1);
});

req.write(body);
req.end();
