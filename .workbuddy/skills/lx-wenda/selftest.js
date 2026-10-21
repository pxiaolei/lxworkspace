#!/usr/bin/env node
/**
 * lx-wenda 端到端自测
 * 直接以子进程方式启动 server.js，按 MCP 协议喂消息，验证整条链路。
 *
 * 用法: node selftest.js [问题]
 */

'use strict';

const { spawn } = require('child_process');
const path = require('path');

const QUESTION = process.argv[2] || 'B端开城怎么操作？';
const SERVER = path.join(__dirname, 'server.js');

function send(child, obj) {
  child.stdin.write(JSON.stringify(obj) + '\n');
}

const child = spawn(process.execPath, [SERVER], {
  stdio: ['pipe', 'pipe', 'pipe'],
  env: { ...process.env }
});

child.on('error', () => { console.log('MCP 子进程无法启动'); finish(1); });
child.on('exit', (code) => { if (!seen.call) { console.log('MCP 子进程提前退出'); finish(code || 1); } });

let buf = '';
const seen = { init: false, list: false, call: false };
let timer = null;

child.stdout.on('data', (chunk) => {
  buf += chunk.toString();
  let idx;
  while ((idx = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, idx).trim();
    buf = buf.slice(idx + 1);
    if (!line) continue;
    let msg;
    try {
      msg = JSON.parse(line);
    } catch (e) {
      console.log('⚠️  非 JSON 输出:', line.slice(0, 120));
      continue;
    }
    handle(msg);
  }
});

child.stderr.on('data', (d) => {
  const t = d.toString().trim();
  if (t) console.log('   [server] ' + t);
});

function handle(msg) {
  if (msg.id === 1) {
    seen.init = true;
    console.log('① initialize  ->', msg.result && msg.result.serverInfo && msg.result.serverInfo.name,
      '协议', msg.result && msg.result.protocolVersion);
    send(child, { jsonrpc: '2.0', method: 'notifications/initialized' });
    send(child, { jsonrpc: '2.0', id: 2, method: 'tools/list' });
  } else if (msg.id === 2) {
    seen.list = true;
    const tools = (msg.result && msg.result.tools) || [];
    console.log('② tools/list  -> 暴露工具:', tools.map((t) => t.name).join(', '));
    console.log('   检查问题范围："' + QUESTION + '"；默认仅预览');
    send(child, {
      jsonrpc: '2.0',
      id: 3,
      method: 'tools/call',
      params: { name: 'ask_knowledge_base', arguments: { question: QUESTION, confirmed: process.argv.includes('--confirmed'), expected_preview_hash: process.argv.includes('--expected-preview-hash') ? process.argv[process.argv.indexOf('--expected-preview-hash') + 1] : '' } }
    });
  } else if (msg.id === 3) {
    seen.call = true;
    if (timer) clearTimeout(timer);
    const r = msg.result || {};
    if (r.isError || (r.content && r.content[0] && r.content[0].text && r.content[0].text.startsWith('知识库调用失败'))) {
      console.log('❌ 调用失败:', (r.content && r.content[0] && r.content[0].text) || JSON.stringify(r).slice(0, 300));
      finish(1);
      return;
    }
    const sc = r.structuredContent || {};
    if (sc.preview_hash) { console.log('③ tools/call -> preview，服务调用 0 次'); console.log(JSON.stringify(sc, null, 2)); finish(0); return; }
    console.log('③ tools/call  -> 成功，用时 ' + sc.cost_seconds + 's，tokens=' + sc.tokens);
    console.log('   会话 ID:', sc.chat_id);
    console.log('   ---- 答案 ----');
    console.log('   ' + String(sc.answer || '').replace(/\n/g, '\n   '));
    console.log('');
    console.log('✅ 链路正常');
    finish(0);
  }
}

function finish(code) {
  try {
    child.kill();
  } catch (e) {}
  process.exit(code);
}

timer = setTimeout(() => {
  console.log('❌ 超时：120 秒内没收到完整响应');
  console.log('   已完成步骤 -> initialize:' + seen.init + ' tools/list:' + seen.list + ' tools/call:' + seen.call);
  finish(1);
}, 130000);

send(child, {
  jsonrpc: '2.0',
  id: 1,
  method: 'initialize',
  params: {
    protocolVersion: '2024-11-05',
    capabilities: {},
    clientInfo: { name: 'selftest', version: '1.0.0' }
  }
});
