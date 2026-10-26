'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const http = require('http');
const { spawnSync } = require('child_process');
const path = require('path');
const { ask, preview } = require('./client');

test('MCP initialize/list/call stays offline through the project config override', () => {
  const result = spawnSync(process.execPath, [path.join(__dirname, 'selftest.js'), '离线测试问题'], {
    encoding: 'utf8', timeout: 5000,
    env: { ...process.env, MAXKB_CONFIG: path.join(__dirname, 'missing-offline-config.json'), MAXKB_TOKEN: '', MAXKB_BASE: '' }
  });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /服务调用 0 次/);
  assert.match(result.stdout, /preview_hash/);
});

test('unconfirmed and stale confirmations make zero HTTP requests', async () => {
  const cfg = { base: 'http://127.0.0.1:1', token: 'test-only', timeout_ms: 1000 };
  const original = http.request;
  let calls = 0;
  http.request = () => { calls++; throw new Error('unexpected request'); };
  try {
    const plan = preview(cfg, '测试问题');
    await assert.rejects(ask(cfg, '测试问题'), /confirmed/);
    await assert.rejects(ask(cfg, '另一个问题', '', { confirmed: true, expected_preview_hash: plan.preview_hash }), /confirmed/);
    await assert.rejects(ask(cfg, '测试问题', 'another-chat', { confirmed: true, expected_preview_hash: plan.preview_hash }), /confirmed/);
    assert.equal(calls, 0);
  } finally { http.request = original; }
});

test('non-200 responses surface the server-side reason instead of dropping the body', async () => {
  const stored = 'Exception:Error code: 403 - AccessDenied.Unpurchased';
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      if (req.url.endsWith('/open')) {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        return res.end(JSON.stringify({ code: 200, data: 'chat-test' }));
      }
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ code: 500, message: '成功', data: { id: 'record-test', chat_id: 'chat-test', content: stored } }));
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const cfg = { base: 'http://127.0.0.1:' + server.address().port, token: 'test-only', timeout_ms: 1000 };
  const authorization = { confirmed: true, expected_preview_hash: preview(cfg, '测试问题').preview_hash };
  try {
    await assert.rejects(ask(cfg, '测试问题', '', authorization), err => {
      assert.match(err.message, /问答 HTTP 500/);
      assert.match(err.message, /AccessDenied\.Unpurchased/);
      return true;
    });
  } finally { await new Promise(resolve => server.close(resolve)); }
});

test('an upstream exception stored as message content is not returned as an answer', async () => {
  const calls = [];
  const server = http.createServer((req, res) => {
    calls.push([req.method, req.url]);
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      const data = req.url.endsWith('/open')
        ? 'chat-test'
        : { id: 'record-test', chat_id: 'chat-test', content: 'Exception:Error code: 403 - AccessDenied.Unpurchased' };
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ code: 200, data }));
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const cfg = { base: 'http://127.0.0.1:' + server.address().port, token: 'test-only', timeout_ms: 1000 };
  const authorization = { confirmed: true, expected_preview_hash: preview(cfg, '测试问题').preview_hash };
  try {
    await assert.rejects(ask(cfg, '测试问题', '', authorization), /拒绝当作答案/);
    assert.equal(calls.length, 2);
  } finally { await new Promise(resolve => server.close(resolve)); }
});

test('error hints never echo the configured token', async () => {
  const server = http.createServer((req, res) => {
    req.resume();
    req.on('end', () => {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ code: 500, message: '鉴权失败，收到的凭据为 test-only' }));
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const cfg = { base: 'http://127.0.0.1:' + server.address().port, token: 'test-only', timeout_ms: 1000 };
  const authorization = { confirmed: true, expected_preview_hash: preview(cfg, '测试问题').preview_hash };
  try {
    await assert.rejects(ask(cfg, '测试问题', '', authorization), err => {
      assert.doesNotMatch(err.message, /test-only/);
      assert.match(err.message, /\[redacted\]/);
      return true;
    });
  } finally { await new Promise(resolve => server.close(resolve)); }
});

test('write is verified by a separate stored-record GET and mismatches fail', async () => {
  let mismatch = false;
  let calls = [];
  const server = http.createServer((req, res) => {
    calls.push([req.method, req.url]);
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      let data;
      if (req.url.endsWith('/open')) data = 'chat-test';
      else if (req.method === 'POST') {
        assert.equal(JSON.parse(body).message, '测试问题');
        data = { id: 'record-test', chat_id: 'chat-test', content: '测试答案' };
      } else data = [{ id: 'record-test', chat_id: 'chat-test', problem_text: '测试问题', answer_text: mismatch ? '错误答案' : '测试答案' }];
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ code: 200, data }));
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const cfg = { base: 'http://127.0.0.1:' + server.address().port, token: 'test-only', timeout_ms: 1000 };
  const authorization = { confirmed: true, expected_preview_hash: preview(cfg, '测试问题').preview_hash };
  try {
    assert.equal((await ask(cfg, '测试问题', '', authorization)).readback_verified, true);
    assert.equal(calls.length, 3);
    assert.deepEqual(calls[2], ['GET', '/chat/api/historical_conversation_record/chat-test']);
    mismatch = true;
    await assert.rejects(ask(cfg, '测试问题', '', authorization), /读回不一致/);
  } finally { await new Promise(resolve => server.close(resolve)); }
});

async function withAnswer(content, callback) {
  const server = http.createServer((req, res) => {
    req.resume();
    req.on('end', () => {
      const data = req.url.endsWith('/open') ? 'chat-test'
        : req.method === 'POST' ? { id: 'record-test', chat_id: 'chat-test', content }
        : [{ id: 'record-test', chat_id: 'chat-test', problem_text: '测试问题', answer_text: content }];
      res.end(JSON.stringify({ code: 200, data }));
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const cfg = { base: 'http://127.0.0.1:' + server.address().port, token: 'test-only', timeout_ms: 1000 };
  try { await callback(() => ask(cfg, '测试问题', '', {
    confirmed: true, expected_preview_hash: preview(cfg, '测试问题').preview_hash
  })); } finally { await new Promise(resolve => server.close(resolve)); }
}

test('knowledge answers explaining error codes and exceptions remain valid', async () => {
  for (const answer of [
    '如果页面显示 Error code: 403，请联系管理员检查功能权限。',
    'Exception 表示程序异常；AccessDenied.Unpurchased 表示模型权限需要检查。',
    'Error code: 403 表示没有访问权限，请联系管理员。'
  ]) {
    await withAnswer(answer, async run => {
      const result = await run();
      assert.equal(result.answer, answer);
      assert.equal(result.readback_verified, true);
    });
  }
});

test('known raw provider error envelopes remain rejected', async () => {
  for (const answer of [
    'Exception:Error code: 403 - AccessDenied.Unpurchased',
    'Error code: 403 - {"error":{"code":"AccessDenied.Unpurchased"}}',
    'AccessDenied.Unpurchased'
  ]) await withAnswer(answer, async run => assert.rejects(run(), /拒绝当作答案/));
});

test('empty and whitespace answers are not reported as successful answers', async () => {
  for (const answer of ['', ' \n\t ']) {
    await withAnswer(answer, async run => assert.rejects(run(), /未获得有效答案/));
  }
});

test('socket timeout remains per-request and preserves its reason', async () => {
  const { EventEmitter } = require('events');
  const original = http.request;
  let timeout;
  http.request = (url, options) => {
    timeout = options.timeout;
    const req = new EventEmitter();
    req.end = () => process.nextTick(() => req.emit('timeout'));
    req.destroy = err => process.nextTick(() => req.emit('error', err));
    return req;
  };
  const cfg = { base: 'http://127.0.0.1:1', token: 'test-only', timeout_ms: 120000 };
  try {
    await assert.rejects(ask(cfg, '测试问题', '', {
      confirmed: true, expected_preview_hash: preview(cfg, '测试问题').preview_hash
    }), err => {
      assert.match(err.message, /问答请求超时/);
      assert.doesNotMatch(err.message, /网络请求失败/);
      return true;
    });
    assert.equal(timeout, 120000);
  } finally { http.request = original; }
});

test('ordinary network errors remain sanitized', async () => {
  const { EventEmitter } = require('events');
  const original = http.request;
  http.request = () => {
    const req = new EventEmitter();
    req.end = () => process.nextTick(() => req.emit('error', new Error('private detail test-only')));
    return req;
  };
  const cfg = { base: 'http://127.0.0.1:1', token: 'test-only', timeout_ms: 1000 };
  try {
    await assert.rejects(ask(cfg, '测试问题', '', {
      confirmed: true, expected_preview_hash: preview(cfg, '测试问题').preview_hash
    }), err => {
      assert.match(err.message, /网络请求失败/);
      assert.doesNotMatch(err.message, /private detail|test-only/);
      return true;
    });
  } finally { http.request = original; }
});

// 网关在 60 秒无数据时直接 504，非流式长答案会撞上；流式让网关持续有数据下行。
test('answers are streamed and joined from content deltas only', async () => {
  const frames = [
    { chat_id: 'chat-test', chat_record_id: 'record-test', content: '', is_end: false, reasoning_content: '思维链片段', usage: { prompt_tokens: 0, completion_tokens: 0 } },
    { chat_id: 'chat-test', chat_record_id: 'record-test', content: '测试', is_end: false, reasoning_content: '思维链片段', usage: { prompt_tokens: 0, completion_tokens: 0 } },
    { chat_id: 'chat-test', chat_record_id: 'record-test', content: '答案', is_end: true, reasoning_content: '思维链片段', usage: { prompt_tokens: 11, completion_tokens: 7 } }
  ];
  const calls = [];
  let streamFlag = null;
  let storedAnswer = '测试答案';
  const server = http.createServer((req, res) => {
    calls.push([req.method, req.url]);
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      if (req.url.endsWith('/open')) {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        return res.end(JSON.stringify({ code: 200, data: 'chat-test' }));
      }
      if (req.method === 'POST') {
        streamFlag = JSON.parse(body).stream;
        res.writeHead(200, { 'Content-Type': 'text/event-stream;charset=utf-8' });
        return res.end(frames.map(frame => 'data: ' + JSON.stringify(frame) + '\n\n').join(''));
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ code: 200, data: [{ id: 'record-test', chat_id: 'chat-test', problem_text: '测试问题', answer_text: storedAnswer }] }));
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const cfg = { base: 'http://127.0.0.1:' + server.address().port, token: 'test-only', timeout_ms: 1000 };
  const authorization = { confirmed: true, expected_preview_hash: preview(cfg, '测试问题').preview_hash };
  try {
    const out = await ask(cfg, '测试问题', '', authorization);
    assert.equal(streamFlag, true);
    assert.equal(out.answer, '测试答案');
    assert.doesNotMatch(out.answer, /思维链/);
    assert.equal(out.tokens, 18);
    assert.equal(out.readback_verified, true);
    assert.equal(calls.length, 3);
    storedAnswer = '被改写的答案';
    await assert.rejects(ask(cfg, '测试问题', '', authorization), /读回不一致/);
  } finally { await new Promise(resolve => server.close(resolve)); }
});

test('gateway timeout reports a clean hint and keeps the created chat id', async () => {
  const server = http.createServer((req, res) => {
    req.resume();
    req.on('end', () => {
      if (req.url.endsWith('/open')) {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        return res.end(JSON.stringify({ code: 200, data: 'chat-test' }));
      }
      res.writeHead(504, { 'Content-Type': 'text/html' });
      res.end('<html>\n<head><title>504 Gateway Time-out</title></head>\n<body><center><h1>504 Gateway Time-out</h1></center><hr><center>nginx</center></body>\n</html>');
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const cfg = { base: 'http://127.0.0.1:' + server.address().port, token: 'test-only', timeout_ms: 1000 };
  const authorization = { confirmed: true, expected_preview_hash: preview(cfg, '测试问题').preview_hash };
  try {
    await assert.rejects(ask(cfg, '测试问题', '', authorization), err => {
      assert.match(err.message, /问答网关超时（HTTP 504）/);
      assert.doesNotMatch(err.message, /<html>|nginx/);
      assert.equal(err.chatId, 'chat-test');
      return true;
    });
  } finally { await new Promise(resolve => server.close(resolve)); }
});
