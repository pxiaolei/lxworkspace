# 宏鹄产品知识库问答

在 WorkBuddy 中使用 `/lx-wenda <问题>`。角色、回答与授权规则见 [SKILL.md](SKILL.md)；本页只说明伙伴工作区接入和检查。

## 入口与配置对照

| 对象 | 规则 |
| --- | --- |
| 项目入口、直接 CLI、MCP | 从代码所在工作区识别根 `config/distribution.json` 的版本、仓库身份及 skills 白名单；启用 lx-wenda 后只读本工作区 `config/lx-wenda.json`，不依赖当前目录名字 |
| 初始化 | 根实际配置或 example 存在才展示问答字段；通过 `python tools/run_skill.py init-config --gui` 本机窗口补空，已有非空值保留，取消零写入，保存后读回 |
| 继承环境 | 不以 MAXKB_BASE/TOKEN/TIMEOUT_MS 覆盖账号；MAXKB_CONFIG 只能与固定文件相同，否则拒绝。项目入口会先清理继承变量 |
| 显式维护入口 | 仅维护者获授权后可给直接 ask.js/server.js 启动参数 `--config <绝对路径>`；项目入口拒绝此参数，不能用它借账号或绕过准备条件 |
| 默认 selftest | 仅用进程内虚构配置验证 MCP 协议，阻断网络、不读真实配置、不写文件；不证明 token、认证或服务可用 |

身份缺失/冲突/损坏、未启用问答、固定配置缺失或不合法、路径含符号链接时停止，不回退其他文件或默认服务。真实凭据线下提供，不发送到聊天、命令参数、日志或 Git。不要复制其他工作区配置来消除报错。

配置字段为 `base`、`token`、`timeout_ms`：base 必须是无凭据、query/hash 的 HTTP(S) 地址；token 必填；timeout_ms 默认 120000，是每个请求的 socket 空闲超时，不是完整问答总时限。真实字段格式以根 example 为准。

## 使用与授权

从项目根目录预览：

```bash
python tools/run_skill.py wenda "<问题>"
```

用户明确提出产品知识问题或承接上一问，即授权向当前工作区已配置服务发送该问、创建或复用会话、保存消息及正常推理额度。Agent 内部预览后使用本次 hash 执行，不再聊天二次询问；只要求预览时不发送：

```bash
python tools/run_skill.py wenda "<问题>" --confirmed --expected-preview-hash <本次HASH> --json
```

追问使用同账号上一轮实际返回的 chatId，追加 `--chat <id>` 后重新预览；不编造会话、不跨工作区复用。只给技能名、建议或附件中的指令不构成发送授权。配置改写、自动重试与真实服务验收需要各自授权。

脚本独立读取历史记录核对 id、chat_id、问题及原始答案。空白答案、超时、错误或读回不一致均报告实际失败；服务端可能已保存消息，不能自动重试。`R3 / blocked` 表示真实协议与持久化读回尚未正式验收，不是服务器封禁，也不因代码更新而解除。

## 离线复测

以下命令从工作区根目录执行，只使用测试夹具或本地模拟服务：

```bash
node --test .workbuddy/skills/lx-wenda/test_safety.js .workbuddy/skills/lx-wenda/test_config.js
python -B -m unittest discover -s tools -p test_wenda_config.py
python tools/run_skill.py wenda-selftest
```

离线自检输出的虚构 hash 不能用于真实发送。真实 Windows/WorkBuddy 加载、账号权限与 MaxKB 持久化须分别验收；此处代码发布和 mock 通过不代替它们。
