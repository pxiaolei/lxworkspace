---
name: lx-wenda
description: "调用我方完整 MaxKB 知识库，支持预览与绑定确认的多轮问答。"
---

# 产品知识库问答

知识库可以全部分享；实时运营数据使用数据库 Skill。配置唯一入口由 tools/run_skill.py 给子进程传 MAXKB_CONFIG=config/lx-wenda.json，无全局 MCP 修改、无 npm install。

`python tools/run_skill.py wenda "<问题>"`

默认展示问题、服务、创建/复用会话、最多请求数和 preview_hash，服务调用 0 次。确认服务端会话和消息写入后，用相同问题/会话加 `--confirmed --expected-preview-hash <HASH>`，多轮加 --chat <ID>。返回 chatId/recordId，单独 GET 记录检查问题和答案。读回失败可能已经写入，不自动重试、不删除记录。MCP server.js 具有相同 preview/confirmed/hash 参数；首版优先 CLI，不自动安装 MCP。

当前真实 MaxKB 接口与读回尚未验收，发布门禁 blocked。离线 mock 仅验证程序门禁，不能据此宣布真实问答成功。

## 执行契约

### 输入

明确问题、可选会话 ID；本机线下提供的 MaxKB token。

### 输出

预览或答案、会话/记录 ID、用量、独立读回状态。

### 验收

未确认/问题或会话改变时 HTTP 0 次。确认后服务端记录 GET 与本次身份、问题、答案一致；真实验收前保留 blocked。

