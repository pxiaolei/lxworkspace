#!/usr/bin/env node
/**
 * lx-wenda MCP Server
 * 把 MaxKB 知识库问答封装成 MCP 工具，供 WorkBuddy 等 MCP 客户端调用。
 *
 * 协议：MCP over stdio（JSON-RPC 2.0，一行一条消息）
 * 依赖：仅 Node.js 内置模块，无需 npm install
 *
 * 重要：stdout 只允许输出 JSON-RPC 消息，任何日志必须走 stderr。
 */

'use strict';

const readline = require('readline');
const { loadConfig, preview, ask } = require('./client');
const SERVER_NAME = 'lx-wenda';
const SERVER_VERSION = '1.1.1';
const PROTOCOL_VERSION = '2024-11-05';
const startupArgs = process.argv.slice(2);
if (startupArgs.length && (startupArgs.length !== 2 || startupArgs[0] !== '--config' || !require('path').isAbsolute(startupArgs[1]))) {
  process.stderr.write('[lx-wenda] 仅接受 --config <维护配置绝对路径>\n');
  process.exit(2);
}
const configFile = startupArgs[1] || '';
function log(...args) { process.stderr.write('[lx-wenda] ' + args.join(' ') + '\n'); }

/* ------------------------------ MCP 协议层 ------------------------------ */

const TOOLS = [
  {
    name: 'ask_knowledge_base',
    description:
      '向「宏鹄 SaaS 产品知识库」提问，取回官方产品文档中的答案。' +
      '适用于：宏鹄工作台的操作步骤、功能说明、配置流程（开城配置、计价配置、城市规则、订单/司机/活动营销相关配置等）、' +
      '平台业务概念解释、以及平台使用中的常见问题。' +
      '不适用于：实时业务数据查询（那是数据接口的活）。',
    inputSchema: {
      type: 'object',
      properties: {
        question: {
          type: 'string',
          description: '要提问的问题，用自然语言，越具体越好。例如「B端开城怎么操作？」'
        },
        confirmed: { type: 'boolean', description: '确认本次服务端会话和消息写入' },
        expected_preview_hash: { type: 'string', description: '本次预览返回的 hash' },
        chat_id: {
          type: 'string',
          description:
            '可选。复用某个已有会话 ID 做多轮追问（上一轮返回的 chat_id）。' +
            '不传则每次自动开新会话，各次问答互不干扰。'
        }
      },
      required: ['question']
    }
  }
];

function send(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

function sendResult(id, result) {
  send({ jsonrpc: '2.0', id, result });
}

function sendError(id, code, message) {
  send({ jsonrpc: '2.0', id, error: { code, message } });
}

async function handleToolCall(id, params) {
  const name = params && params.name;
  const args = (params && params.arguments) || {};
  if (name !== 'ask_knowledge_base') {
    return sendError(id, -32602, '未知工具: ' + name);
  }
  const question = String(args.question || '').trim();
  if (!question) {
    return sendError(id, -32602, '参数 question 不能为空');
  }
  try {
    const started = Date.now();
    const cfg = loadConfig({ configFile });
    if (!args.confirmed) {
      const plan = preview(cfg, question, args.chat_id);
      return sendResult(id, { content: [{ type: 'text', text: JSON.stringify(plan) }], structuredContent: plan });
    }
    const out = await ask(cfg, question, args.chat_id, args);
    const cost = ((Date.now() - started) / 1000).toFixed(1);
    log('ask ok in ' + cost + 's, tokens=' + out.tokens);
    sendResult(id, {
      content: [
        {
          type: 'text',
          text: out.answer || '（知识库未返回内容）'
        }
      ],
      structuredContent: {
        answer: out.answer,
        chat_id: out.chatId,
        record_id: out.recordId,
        readback_verified: out.readback_verified,
        cost_seconds: Number(cost),
        tokens: out.tokens
      }
    });
  } catch (e) {
    log('ask failed: ' + e.message);
    sendResult(id, {
      content: [{ type: 'text', text: '知识库调用失败：' + e.message
        + (e.chatId ? '（会话已创建：' + e.chatId + '，可用 chat_id 追回本次记录）' : '') }],
      isError: true
    });
  }
}

async function handle(msg) {
  const { id, method, params } = msg;

  // 通知类消息（无 id）不需要回复
  const isNotification = id === undefined || id === null;

  switch (method) {
    case 'initialize':
      return sendResult(id, {
        protocolVersion: PROTOCOL_VERSION,
        capabilities: { tools: {} },
        serverInfo: { name: SERVER_NAME, version: SERVER_VERSION }
      });

    case 'notifications/initialized':
    case 'initialized':
      return; // 通知，不回复

    case 'ping':
      return sendResult(id, {});

    case 'tools/list':
      return sendResult(id, { tools: TOOLS });

    case 'tools/call':
      return handleToolCall(id, params);

    case 'resources/list':
      return sendResult(id, { resources: [] });

    case 'prompts/list':
      return sendResult(id, { prompts: [] });

    case 'shutdown':
      return sendResult(id, {});

    case 'exit':
      process.exit(0);
      return;

    default:
      if (isNotification) return;
      return sendError(id, -32601, '未实现的方法: ' + method);
  }
}

function main() {
  log('启动，默认 preview；配置仅在问答请求时读取');
  const rl = readline.createInterface({ input: process.stdin, terminal: false });
  let chain = Promise.resolve();
  rl.on('line', (line) => {
    const text = line.trim();
    if (!text) return;
    let msg;
    try {
      msg = JSON.parse(text);
    } catch (e) {
      log('忽略非 JSON 输入');
      return;
    }
    chain = chain.then(() => handle(msg)).catch((e) => log('处理异常: ' + e.message));
  });
  rl.on('close', () => chain.then(() => process.exit(0)));
}

process.on('uncaughtException', (e) => log('未捕获异常: ' + e.message));
process.on('unhandledRejection', (e) => log('未处理拒绝: ' + e));

main();
