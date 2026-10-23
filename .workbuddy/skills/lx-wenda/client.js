'use strict';
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const http = require('http');
const https = require('https');

function loadConfig() {
  const file = process.env.MAXKB_CONFIG || path.join(__dirname, 'config.json');
  let cfg = { base: 'http://maxkb.sfczhushou.com', token: '', timeout_ms: 120000 };
  if (fs.existsSync(file)) {
    try { Object.assign(cfg, JSON.parse(fs.readFileSync(file, 'utf8'))); }
    catch (_) { throw new Error('问答配置不是合法 JSON，请在本机检查；不回显内容'); }
  }
  if (process.env.MAXKB_BASE) cfg.base = process.env.MAXKB_BASE;
  if (process.env.MAXKB_TOKEN) cfg.token = process.env.MAXKB_TOKEN;
  if (process.env.MAXKB_TIMEOUT_MS) cfg.timeout_ms = Number(process.env.MAXKB_TIMEOUT_MS);
  cfg.base = String(cfg.base).replace(/\/+$/, '');
  let url;
  try { url = new URL(cfg.base); } catch (_) { throw new Error('问答服务地址无效'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error('问答地址必须为不含凭据的 HTTP(S) 地址');
  if (!Number.isFinite(cfg.timeout_ms) || cfg.timeout_ms <= 0) throw new Error('问答 timeout_ms 无效');
  return cfg;
}

function preview(cfg, question, chatId = '') {
  question = String(question || '').trim();
  if (!question) throw new Error('问题不能为空');
  if (chatId && !/^[a-zA-Z0-9-]{1,128}$/.test(chatId)) throw new Error('会话 ID 无效');
  const plan = { action: 'maxkb.ask', service: cfg.base, question, chat_id: chatId || '',
    creates_chat: !chatId, creates_message: true, readback: 'historical_conversation_record',
    max_requests: chatId ? 2 : 3 };
  return { ...plan, preview_hash: crypto.createHash('sha256').update(JSON.stringify(plan)).digest('hex') };
}

const HINT_LIMIT = 300;

// 服务端把上游异常写进 data.content；非 200 时也要带出来。
// 只取一层、压缩空白、截断，并且绝不回显 token。
function clip(text, token) {
  let s = String(text || '').replace(/\s+/g, ' ').trim();
  if (token) s = s.split(token).join('[redacted]');
  return s.length > HINT_LIMIT ? s.slice(0, HINT_LIMIT) + '…' : s;
}

function errorHint(buffer, parsed, token) {
  let text = buffer;
  if (parsed) {
    const data = parsed.data;
    text = (data && (data.content || data.message)) || parsed.message || '';
  }
  const s = clip(text, token);
  return s ? '：' + s : '';
}

// MaxKB 会把上游异常（如模型 403）写进 content 当消息正文保存，这类文本不是答案。
function isErrorText(text) {
  // 只识别已知的裸异常封套，不按正常答案中提到的错误码判错。
  return /^\s*(?:Exception\s*:\s*)?(?:Error code:\s*\d{3}\s*-\s*(?:\{|AccessDenied\.)|AccessDenied\.\w+\s*$)/i.test(text);
}

function request(cfg, route, method = 'GET', body = null) {
  return new Promise((resolve, reject) => {
    const url = new URL(cfg.base + '/chat/api/' + route);
    const payload = body === null ? null : JSON.stringify(body);
    const headers = { Authorization: 'Bearer ' + cfg.token };
    if (payload) { headers['Content-Type'] = 'application/json'; headers['Content-Length'] = Buffer.byteLength(payload); }
    const req = (url.protocol === 'https:' ? https : http).request(url, { method, headers, timeout: cfg.timeout_ms }, res => {
      let buffer = '';
      res.setEncoding('utf8');
      res.on('data', chunk => { buffer += chunk; if (buffer.length > 4000000) req.destroy(new Error('问答响应超过限制')); });
      res.on('end', () => {
        let parsed = null;
        try { parsed = JSON.parse(buffer); } catch (_) { parsed = null; }
        const hint = errorHint(buffer, parsed, cfg.token);
        if (res.statusCode !== 200) return reject(new Error('问答 HTTP ' + res.statusCode + hint));
        if (!parsed) return reject(new Error('问答响应不是合法 JSON' + hint));
        if (parsed.code !== 200) return reject(new Error('问答业务状态失败' + hint));
        resolve(parsed.data);
      });
    });
    req.on('timeout', () => req.destroy(Object.assign(new Error('问答请求超时'), { code: 'MAXKB_TIMEOUT' })));
    req.on('error', err => reject(new Error(err.code === 'MAXKB_TIMEOUT'
      ? '问答请求超时；服务端可能仍在处理，请勿自动重试'
      : '问答网络请求失败；请检查本机网络和服务配置')));
    if (payload) req.write(payload);
    req.end();
  });
}

async function ask(cfg, question, chatId = '', authorization = {}) {
  const plan = preview(cfg, question, chatId);
  if (!authorization.confirmed || authorization.expected_preview_hash !== plan.preview_hash) throw new Error('需要 confirmed 与本次 preview_hash，未调用服务');
  if (!cfg.token) throw new Error('缺少问答 token，请在本机配置');
  let cid = chatId || await request(cfg, 'open');
  if (typeof cid !== 'string' || !/^[a-zA-Z0-9-]{1,128}$/.test(cid)) throw new Error('创建会话返回无效 ID；不自动重试');
  const result = await request(cfg, 'chat_message/' + encodeURIComponent(cid), 'POST', { message: plan.question, stream: false, re_chat: false });
  if (!result || !result.id || (result.chat_id && result.chat_id !== cid) || typeof result.content !== 'string') throw new Error('问答结果缺少可读回记录；可能已写入，请人工检查，勿自动重试');
  const answer = result.content.trim();
  if (!answer) throw new Error('未获得有效答案；服务端可能已保存消息，请勿自动重试');
  // 上游异常会被服务端当消息正文存下来，不能进入读回校验后原样返回给用户。
  if (isErrorText(result.content)) throw new Error('问答返回异常文本，已拒绝当作答案 ' + clip(result.content, cfg.token) + '；可能已写入，请人工检查，勿自动重试');
  // The detail endpoint may read process cache. The official history-list
  // serializer queries ChatRecord, so use that independent stored-record surface.
  const records = await request(cfg, 'historical_conversation_record/' + encodeURIComponent(cid));
  const record = Array.isArray(records) ? records.find(item => item.id === result.id) : null;
  // 返回值与读回比对使用同一归一化口径：服务端存储时可能对首尾空白做处理，
  // 若比对用原始值而返回用 trim 后的值，会把一次成功写入误报成读回不一致。
  if (!record || record.id !== result.id || record.chat_id !== cid
    || String(record.problem_text || '').trim() !== plan.question
    || String(record.answer_text || '').trim() !== answer) throw new Error('问答独立读回不一致；可能已写入，请人工检查，勿自动重试');
  return { answer, chatId: cid, recordId: result.id,
    tokens: (result.prompt_tokens || 0) + (result.completion_tokens || 0), readback_verified: true };
}

module.exports = { loadConfig, preview, ask };
