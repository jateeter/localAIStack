import assert from 'node:assert/strict';
import http from 'node:http';
import test from 'node:test';

import {
  DEFAULT_COMPLETION_MAPPING,
  STUB_RESULTS,
  completionPayloadFromResponse,
  postCompletion,
  runConformance,
  validateCompletionPayload,
  valuesFromCompletionContent,
} from './pe-completion-conformance.mjs';

function listen(handler) {
  const server = http.createServer(handler);
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      resolve({
        server,
        url: `http://127.0.0.1:${address.port}`,
      });
    });
  });
}

function close(server) {
  return new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
}

test('native Ollama response maps to PE completion payload', () => {
  const payload = completionPayloadFromResponse({
    mode: 'native',
    response: STUB_RESULTS.native,
    provider: 'ollama',
    agent: 'openclaw',
    completionId: 'completion-test',
  });

  assert.deepEqual(payload.values, [1, 0, 0.91, 0]);
  assert.equal(payload.provider, 'ollama');
  assert.equal(payload.sourceMappingId, DEFAULT_COMPLETION_MAPPING.id);
  assert.equal(payload.sensorId, 'acp.openclaw.openclaw.completion');
  assert.equal(validateCompletionPayload(payload).valid, true);
});

test('OpenAI-compatible chat response maps to the same PE completion contract', () => {
  const payload = completionPayloadFromResponse({
    mode: 'openai-chat',
    response: STUB_RESULTS['openai-chat'],
    provider: 'ollama-openai-compatible',
    agent: 'openclaw',
    completionId: 'completion-test',
  });

  assert.deepEqual(payload.values, [1, 0, 0.88, 0]);
  assert.equal(payload.metadata.mode, 'openai-chat');
  assert.equal(validateCompletionPayload(payload).valid, true);
});

test('mapped completion extraction rejects missing required fields', () => {
  assert.throws(
    () => valuesFromCompletionContent({ completed: 1, failed: 0, confidence: 0.5 }),
    /missing required JSON pointer: \/actionClass/,
  );
});

test('dry-run conformance prints a validated payload without posting', async () => {
  const result = await runConformance({
    mode: 'native',
    provider: 'ollama',
    agent: 'dry-run-agent',
    stub: true,
    'dry-run': true,
    'source-mapping-id': DEFAULT_COMPLETION_MAPPING.id,
    'dispatch-id': 'dispatch-dry',
    'envelope-id': 'envelope-dry',
    'correlation-id': 'correlation-dry',
  });

  assert.equal(result.posted, false);
  assert.deepEqual(result.payload.values, [1, 0, 0.91, 0]);
  assert.equal(validateCompletionPayload(result.payload).valid, true);
});

test('PE callback posts to /api/integrations/completions', async () => {
  let observed;
  const { server, url } = await listen((req, res) => {
    let body = '';
    req.on('data', (chunk) => {
      body += chunk;
    });
    req.on('end', () => {
      observed = { method: req.method, url: req.url, body: JSON.parse(body) };
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ success: true }));
    });
  });

  try {
    const payload = completionPayloadFromResponse({
      mode: 'native',
      response: STUB_RESULTS.native,
      completionId: 'completion-test',
    });
    const result = await postCompletion(url, payload);
    assert.equal(result.status, 200);
    assert.equal(observed.method, 'POST');
    assert.equal(observed.url, '/api/integrations/completions');
    assert.deepEqual(observed.body.values, [1, 0, 0.91, 0]);
  } finally {
    await close(server);
  }
});

test('PE callback non-2xx response fails visibly', async () => {
  const { server, url } = await listen((_req, res) => {
    res.writeHead(503, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: 'stub PE unavailable' }));
  });

  try {
    const payload = completionPayloadFromResponse({
      mode: 'openai-chat',
      response: STUB_RESULTS['openai-chat'],
      completionId: 'completion-test',
    });
    await assert.rejects(
      () => postCompletion(url, payload),
      /PE completions returned 503.*stub PE unavailable/,
    );
  } finally {
    await close(server);
  }
});
