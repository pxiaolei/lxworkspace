#!/usr/bin/env node
/**
 * lx-wenda CLI
 * 命令行调用宏鹄 SaaS 产品知识库，供 Skill 方式使用（无需 MCP）。
 *
 * 用法：
 *   node ask.js "B端开城怎么操作？"
 *   node ask.js "那关城呢？" --chat 01a09008-46bc-7220-840e-10bd02a96cd4
 *   node ask.js "计价配置怎么做" --json
 *
 * 输出：默认纯文本答案；--json 输出完整结构化结果。
 * 首行附带会话信息时请看 stderr，stdout 只给答案，方便管道使用。
 */

'use strict';

const { loadConfig, preview, ask } = require('./client');

function logErr(...a) { process.stderr.write('[lx-wenda] ' + a.join(' ') + '\n'); }

function parseArgs(argv) {
  const out = { question: '', chatId: '', json: false, help: false, confirmed: false, expected_preview_hash: '' };
  const rest = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--chat') out.chatId = argv[++i] || '';
    else if (a === '--confirmed') out.confirmed = true;
    else if (a === '--expected-preview-hash') out.expected_preview_hash = argv[++i] || '';
    else if (a === '--json') out.json = true;
    else if (a.startsWith('--') && a !== '--help') throw new Error('未知参数');
    else if (a === '-h' || a === '--help') out.help = true;
    else rest.push(a);
  }
  out.question = rest.join(' ').trim();
  return out;
}

const USAGE = [
  '用法: node ask.js "<问题>" [--chat <会话ID>] [--json]',
  '',
  '示例:',
  '  node ask.js "B端开城怎么操作？"',
  '  node ask.js "那关城呢？" --chat 01a09008-46bc-7220-840e-10bd02a96cd4',
  '',
  '默认只预览。确认后追加 --confirmed --expected-preview-hash <HASH>；每次请求绑定问题和会话。'
].join('\n');

(async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help || !args.question) {
    process.stdout.write(USAGE + '\n');
    process.exit(args.help ? 0 : 1);
  }
  const cfg = loadConfig();
  if (!args.confirmed) { process.stdout.write(JSON.stringify(preview(cfg, args.question, args.chatId), null, 2) + '\n'); return; }
  const started = Date.now();
  try {
    const out = await ask(cfg, args.question, args.chatId, args);
    const cost = ((Date.now() - started) / 1000).toFixed(1);
    logErr('用时 ' + cost + 's，tokens=' + out.tokens + '，chat_id=' + out.chatId);
    if (args.json) {
      process.stdout.write(JSON.stringify(out, null, 2) + '\n');
    } else {
      process.stdout.write(out.answer + '\n');
    }
    process.exit(0);
  } catch (e) {
    logErr('失败: ' + e.message);
    process.exit(2);
  }
})();
