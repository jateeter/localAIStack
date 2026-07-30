#!/usr/bin/env node
/**
 * Validate that native Ollama and OpenAI-compatible chat responses can be
 * converted into the RealityEngine PE completion ingress contract.
 */

import http from 'node:http';
import https from 'node:https';
import { readFileSync } from 'node:fs';
import { parseArgs } from 'node:util';

export const DEFAULT_COMPLETION_MAPPING = {
  id: 'acp-openclaw-completion',
  sensorIdTemplate: 'acp.openclaw.{agent}.completion',
  region: { offset: 4210, length: 4 },
  extract: {
    type: 'json',
    pointers: ['/completed', '/failed', '/confidence', '/actionClass'],
  },
  normalize: { mode: 'passthrough', clamp: true },
};

export const STUB_RESULTS = {
  native: {
    model: 'ternary-bonsai:4',
    created_at: '2026-07-30T00:00:00Z',
    message: {
      role: 'assistant',
      content: '{"completed":1,"failed":0,"confidence":0.91,"actionClass":0}',
    },
  },
  'openai-chat': {
    id: 'chatcmpl-localai-conformance',
    object: 'chat.completion',
    choices: [
      {
        index: 0,
        message: {
          role: 'assistant',
          content: '{"completed":1,"failed":0,"confidence":0.88,"actionClass":0}',
        },
        finish_reason: 'stop',
      },
    ],
  },
};

function jsonRequest(url, body, headers = {}) {
  return new Promise((resolve, reject) => {
    const target = new URL(url);
    const lib = target.protocol === 'https:' ? https : http;
    const payload = JSON.stringify(body);
    const req = lib.request(
      {
        hostname: target.hostname,
        port: target.port || (target.protocol === 'https:' ? 443 : 80),
        path: `${target.pathname}${target.search}`,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(payload),
          ...headers,
        },
        rejectUnauthorized: false,
      },
      (res) => {
        let data = '';
        res.setEncoding('utf8');
        res.on('data', (chunk) => {
          data += chunk;
        });
        res.on('end', () => {
          let parsed = data;
          try {
            parsed = data ? JSON.parse(data) : {};
          } catch {
            // Keep the raw body for clear callback failure messages.
          }
          resolve({ status: res.statusCode ?? 0, body: parsed });
        });
      },
    );
    req.on('error', reject);
    req.write(payload);
    req.end();
  });
}

function decodePointerToken(token) {
  return token.replace(/~1/g, '/').replace(/~0/g, '~');
}

function readPointer(root, pointer) {
  if (!pointer || pointer === '/') return root;
  if (!pointer.startsWith('/')) throw new Error(`invalid JSON pointer: ${pointer}`);
  let cursor = root;
  for (const rawPart of pointer.slice(1).split('/')) {
    const part = decodePointerToken(rawPart);
    if (Array.isArray(cursor)) {
      const index = Number(part);
      if (!Number.isInteger(index) || index < 0 || index >= cursor.length) {
        throw new Error(`missing required JSON pointer: ${pointer}`);
      }
      cursor = cursor[index];
    } else if (cursor && typeof cursor === 'object' && Object.hasOwn(cursor, part)) {
      cursor = cursor[part];
    } else {
      throw new Error(`missing required JSON pointer: ${pointer}`);
    }
  }
  return cursor;
}

function numericValue(value, pointer = 'value') {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'boolean') return value ? 1 : 0;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  throw new Error(`completion field ${pointer} is not a finite number`);
}

function clamp01(value) {
  return Math.max(0, Math.min(1, value));
}

export function extractNativeOllamaContent(response) {
  return response?.message?.content ?? response?.response ?? '';
}

export function extractOpenAIChatContent(response) {
  return response?.choices?.[0]?.message?.content ?? response?.choices?.[0]?.text ?? '';
}

export function parseCompletionContent(content) {
  if (content && typeof content === 'object') return content;
  if (typeof content !== 'string' || content.trim() === '') {
    throw new Error('provider response did not include completion content');
  }
  try {
    return JSON.parse(content);
  } catch (error) {
    throw new Error(`provider completion content is not JSON: ${error.message}`);
  }
}

export function valuesFromCompletionContent(contentJson, mapping = DEFAULT_COMPLETION_MAPPING) {
  let values;
  const extract = mapping?.extract ?? {};
  if (extract.type === 'json' && Array.isArray(extract.pointers)) {
    values = extract.pointers.map((pointer) => numericValue(readPointer(contentJson, pointer), pointer));
  } else if (extract.type === 'json' && extract.pointer) {
    const target = readPointer(contentJson, extract.pointer);
    values = Array.isArray(target)
      ? target.map((value, index) => numericValue(value, `${extract.pointer}/${index}`))
      : [numericValue(target, extract.pointer)];
  } else if (Array.isArray(contentJson?.values)) {
    values = contentJson.values.map((value, index) => numericValue(value, `/values/${index}`));
  } else if (Array.isArray(contentJson?.completion?.values)) {
    values = contentJson.completion.values.map((value, index) =>
      numericValue(value, `/completion/values/${index}`),
    );
  } else {
    throw new Error('provider response did not include completion values');
  }

  if (mapping?.normalize?.clamp) return values.map(clamp01);
  return values;
}

export function validateCompletionPayload(payload, mapping = DEFAULT_COMPLETION_MAPPING) {
  const errors = [];
  if (!payload || typeof payload !== 'object') errors.push('payload must be an object');
  if (!payload.provider) errors.push('provider is required');
  if (!payload.agent) errors.push('agent is required');
  if (!payload.sourceMappingId) errors.push('sourceMappingId is required');
  if (!Array.isArray(payload.values)) errors.push('values must be an array');
  if (Array.isArray(payload.values)) {
    payload.values.forEach((value, index) => {
      if (typeof value !== 'number' || !Number.isFinite(value)) {
        errors.push(`values[${index}] must be a finite number`);
      }
    });
    const expectedLength = mapping?.region?.length;
    if (Number.isInteger(expectedLength) && payload.values.length !== expectedLength) {
      errors.push(`values length ${payload.values.length} does not match mapping length ${expectedLength}`);
    }
  }
  if (!payload.metadata || typeof payload.metadata !== 'object') errors.push('metadata is required');
  return { valid: errors.length === 0, errors };
}

export function completionPayloadFromResponse({
  mode,
  response,
  provider = 'ollama',
  agent = 'localai-conformance',
  sourceMappingId = DEFAULT_COMPLETION_MAPPING.id,
  mapping = DEFAULT_COMPLETION_MAPPING,
  dispatchId = 'localai-conformance-dispatch',
  envelopeId = 'localai-conformance-envelope',
  correlationId = 'localai-conformance-correlation',
  completionId = `completion-${Date.now()}`,
}) {
  const content =
    mode === 'native'
      ? extractNativeOllamaContent(response)
      : extractOpenAIChatContent(response);
  const contentJson = parseCompletionContent(content);
  const values = valuesFromCompletionContent(contentJson, mapping);
  const payload = {
    provider,
    agent,
    sourceMappingId,
    sensorId: mapping.sensorIdTemplate?.replace('{provider}', provider).replace('{agent}', agent),
    values,
    correlationId,
    envelopeId,
    dispatchId,
    completionId,
    metadata: {
      provider,
      agent,
      mode,
      conformance: true,
      providerResponse: response,
    },
  };
  const validation = validateCompletionPayload(payload, { ...mapping, id: sourceMappingId });
  if (!validation.valid) {
    throw new Error(`completion payload failed validation: ${validation.errors.join('; ')}`);
  }
  return payload;
}

export async function callNativeOllama({ baseUrl, model, prompt }) {
  const res = await jsonRequest(`${baseUrl.replace(/\/$/, '')}/api/chat`, {
    model,
    stream: false,
    format: {
      type: 'object',
      properties: {
        completed: { type: 'number' },
        failed: { type: 'number' },
        confidence: { type: 'number' },
        actionClass: { type: 'number' },
      },
      required: ['completed', 'failed', 'confidence', 'actionClass'],
    },
    messages: [
      { role: 'system', content: 'Return only JSON for a PE completion.' },
      { role: 'user', content: prompt },
    ],
  });
  if (res.status < 200 || res.status >= 300) {
    throw new Error(`native Ollama returned ${res.status}: ${JSON.stringify(res.body)}`);
  }
  return res.body;
}

export async function callOpenAICompatibleOllama({ baseUrl, model, prompt }) {
  const res = await jsonRequest(`${baseUrl.replace(/\/$/, '')}/v1/chat/completions`, {
    model,
    stream: false,
    response_format: { type: 'json_object' },
    messages: [
      { role: 'system', content: 'Return only JSON for a PE completion.' },
      { role: 'user', content: prompt },
    ],
  });
  if (res.status < 200 || res.status >= 300) {
    throw new Error(`OpenAI-compatible Ollama returned ${res.status}: ${JSON.stringify(res.body)}`);
  }
  return res.body;
}

export async function postCompletion(peUrl, payload) {
  const res = await jsonRequest(`${peUrl.replace(/\/$/, '')}/api/integrations/completions`, payload);
  if (res.status < 200 || res.status >= 300) {
    throw new Error(`PE completions returned ${res.status}: ${JSON.stringify(res.body)}`);
  }
  return res;
}

function readResponseFromArgs(args, mode) {
  if (args['response-json']) return JSON.parse(args['response-json']);
  if (args['response-file']) return JSON.parse(readFileSync(args['response-file'], 'utf8'));
  if (args.stub) return STUB_RESULTS[mode];
  return null;
}

export async function runConformance(options) {
  const mode = options.mode;
  let response = readResponseFromArgs(options, mode);
  const prompt =
    options.prompt ??
    'Produce a PE completion JSON object with completed, failed, confidence, and actionClass.';

  if (!response) {
    response =
      mode === 'native'
        ? await callNativeOllama({
            baseUrl: options['ollama-url'],
            model: options.model,
            prompt,
          })
        : await callOpenAICompatibleOllama({
            baseUrl: options['ollama-url'],
            model: options.model,
            prompt,
          });
  }

  const payload = completionPayloadFromResponse({
    mode,
    response,
    provider: options.provider,
    agent: options.agent,
    sourceMappingId: options['source-mapping-id'],
    dispatchId: options['dispatch-id'],
    envelopeId: options['envelope-id'],
    correlationId: options['correlation-id'],
  });

  if (options['dry-run']) return { payload, posted: false };
  return { payload, posted: true, post: await postCompletion(options['pe-url'], payload) };
}

async function main() {
  const { values: args } = parseArgs({
    options: {
      mode: { type: 'string', default: 'native' },
      provider: { type: 'string', default: 'ollama' },
      agent: { type: 'string', default: 'localai-conformance' },
      model: { type: 'string', default: process.env.OLLAMA_MODEL || 'ternary-bonsai:4' },
      'ollama-url': { type: 'string', default: process.env.OLLAMA_BASE_URL || 'http://localhost:11434' },
      'pe-url': { type: 'string', default: process.env.PE_URL || 'http://localhost:5300' },
      'source-mapping-id': { type: 'string', default: DEFAULT_COMPLETION_MAPPING.id },
      'dispatch-id': { type: 'string', default: 'localai-conformance-dispatch' },
      'envelope-id': { type: 'string', default: 'localai-conformance-envelope' },
      'correlation-id': { type: 'string', default: 'localai-conformance-correlation' },
      'response-json': { type: 'string' },
      'response-file': { type: 'string' },
      prompt: { type: 'string' },
      stub: { type: 'boolean', default: false },
      'dry-run': { type: 'boolean', default: false },
    },
  });

  if (!['native', 'openai-chat'].includes(args.mode)) {
    throw new Error('--mode must be native or openai-chat');
  }

  const result = await runConformance(args);
  console.log(JSON.stringify(result, null, 2));
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    console.error(`[conformance] ${error.message}`);
    process.exitCode = 1;
  });
}
