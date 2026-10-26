'use strict';
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const http = require('http');
const https = require('https');

function regularFile(file, root) {
  // Reject both the selected file and linked parent directories, including links
  // back into the workspace. The workspace itself has already been canonicalized.
  let current = root || path.parse(file).root;
  for (const part of path.relative(current, file).split(path.sep)) {
    current = path.join(current, part);
    if (fs.lstatSync(current).isSymbolicLink()) throw new Error('linked path');
  }
  if (!fs.statSync(file).isFile()) throw new Error('not a regular file');
}

function workspaceConfig() {
  const root = fs.realpathSync(path.resolve(__dirname, '../../..'));
  if (process.env.MAXKB_WORKSPACE_ROOT && fs.realpathSync(process.env.MAXKB_WORKSPACE_ROOT) !== root) {
    throw new Error('问答工作区上下文不匹配');
  }
  const source = path.join(root, 'config/distribution-manifest.json');
  const managed = path.join(root, 'config/distribution.json');
  const present = file => { try { fs.lstatSync(file); return true; } catch (e) { if (e.code === 'ENOENT') return false; throw e; } };
  const isSource = present(source);
  if (isSource === present(managed)) throw new Error('问答工作区身份缺失或冲突');
  const marker = isSource ? source : managed;
  regularFile(marker, root);
  const identity = JSON.parse(fs.readFileSync(marker, 'utf8'));
  if (identity.version !== 1) throw new Error('问答工作区身份版本无效');
  if (isSource) {
    if (identity.targets?.FOG?.repository !== 'pxiaolei/FOG'
      || identity.targets?.lxworkspace?.repository !== 'pxiaolei/lxworkspace') throw new Error('问答源仓身份无效');
    return { root, file: path.join(root, '.workbuddy/skills/lx-wenda/config.json') };
  }
  if (!['FOG', 'lxworkspace'].includes(identity.kind) || identity.repository !== 'pxiaolei/' + identity.kind
    || !Array.isArray(identity.skills) || !identity.skills.includes('lx-wenda')) throw new Error('当前分发工作区未启用问答');
  return { root, file: path.join(root, 'config/lx-wenda.json') };
}

function loadConfig({ configFile = '' } = {}) {
  let cfg;
  try {
    let file, root;
    if (configFile) {
      if (process.env.MAXKB_WORKSPACE_ROOT || !path.isAbsolute(configFile)) throw new Error('维护配置需要绝对路径及直接入口');
      file = path.resolve(configFile);
    } else {
      ({ file, root } = workspaceConfig());
      if (process.env.MAXKB_CONFIG && (!path.isAbsolute(process.env.MAXKB_CONFIG)
        || path.resolve(process.env.MAXKB_CONFIG) !== file)) throw new Error('继承配置与工作区冲突');
    }
    regularFile(file, root);
    cfg = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (!cfg || typeof cfg !== 'object' || Array.isArray(cfg)
      || typeof cfg.base !== 'string' || typeof cfg.token !== 'string' || !cfg.token.trim()) throw new Error('配置必填项缺失');
  } catch (_) {
    throw new Error('问答配置不可用：请检查工作区身份、固定配置及路径；不回显配置内容，也不自动回退');
  }
  cfg = { base: cfg.base.replace(/\/+$/, ''), token: cfg.token, timeout_ms: cfg.timeout_ms ?? 120000 };
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

// 流式响应是 text/event-stream，每行 `data: {...}`；content 是增量片段，
// reasoning_content 是独立的思维链字段，绝不能拼进答案。
function parseFrames(buffer) {
  const frames = [];
  for (const line of buffer.split('\n')) {
    const text = line.trim();
    if (!text.startsWith('data:')) continue;
    const payload = text.slice(5).trim();
    if (!payload || payload === '[DONE]') continue;
    try { frames.push(JSON.parse(payload)); } catch (_) { /* 跳过无法解析的帧 */ }
  }
  return frames;
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
        if (res.statusCode !== 200) {
          const err = new Error('问答 HTTP ' + res.statusCode + hint);
          err.httpStatus = res.statusCode;
          return reject(err);
        }
        if (!parsed) {
          const frames = parseFrames(buffer);
          if (!frames.length) return reject(new Error('问答响应不是合法 JSON' + hint));
          return resolve({ kind: 'sse', frames });
        }
        if (parsed.code !== 200) return reject(new Error('问答业务状态失败' + hint));
        resolve({ kind: 'json', data: parsed.data });
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

// 长答案常超过 30 秒；MaxKB 前面的网关在 60 秒内没有数据下行就直接回 504。
// 改流式后首帧约 2 秒到达、之后持续有数据，网关读超时不会被触发。
// 返回体统一成 { id, chat_id, content, prompt_tokens, completion_tokens }，
// 非流式响应仍然兼容，便于离线测试与旧服务。
async function exchange(cfg, cid, question) {
  let res;
  try {
    res = await request(cfg, 'chat_message/' + encodeURIComponent(cid), 'POST',
      { message: question, stream: true, re_chat: false });
  } catch (e) {
    if (e.httpStatus === 504) {
      const err = new Error('问答网关超时（HTTP 504）：服务端 60 秒内没有返回内容，答案可能仍在生成或已写入；请勿立即重复提问');
      err.chatId = cid;
      throw err;
    }
    throw e;
  }
  if (res.kind === 'json') return res.data || {};
  const frames = res.frames;
  const usage = frames.map(f => f.usage).filter(Boolean).pop() || {};
  const withRecord = frames.find(f => f.chat_record_id) || {};
  const withChat = frames.find(f => f.chat_id) || {};
  return {
    id: withRecord.chat_record_id || '',
    chat_id: withChat.chat_id || cid,
    content: frames.map(f => (typeof f.content === 'string' ? f.content : '')).join(''),
    prompt_tokens: usage.prompt_tokens || 0,
    completion_tokens: usage.completion_tokens || 0
  };
}

async function ask(cfg, question, chatId = '', authorization = {}) {
  const plan = preview(cfg, question, chatId);
  if (!authorization.confirmed || authorization.expected_preview_hash !== plan.preview_hash) throw new Error('需要 confirmed 与本次 preview_hash，未调用服务');
  if (!cfg.token) throw new Error('缺少问答 token，请在本机配置');
  let cid = chatId || (await request(cfg, 'open')).data;
  if (typeof cid !== 'string' || !/^[a-zA-Z0-9-]{1,128}$/.test(cid)) throw new Error('创建会话返回无效 ID；不自动重试');
  const result = await exchange(cfg, cid, plan.question);
  if (!result || !result.id || (result.chat_id && result.chat_id !== cid) || typeof result.content !== 'string') throw new Error('问答结果缺少可读回记录；可能已写入，请人工检查，勿自动重试');
  const answer = result.content.trim();
  if (!answer) throw new Error('未获得有效答案；服务端可能已保存消息，请勿自动重试');
  // 上游异常会被服务端当消息正文存下来，不能进入读回校验后原样返回给用户。
  if (isErrorText(result.content)) throw new Error('问答返回异常文本，已拒绝当作答案 ' + clip(result.content, cfg.token) + '；可能已写入，请人工检查，勿自动重试');
  // The detail endpoint may read process cache. The official history-list
  // serializer queries ChatRecord, so use that independent stored-record surface.
  const records = (await request(cfg, 'historical_conversation_record/' + encodeURIComponent(cid))).data;
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
