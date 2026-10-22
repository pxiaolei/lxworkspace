---
name: lx-init
description: "工作区初始化：确认目录，缺则直接补齐 Python/Node/Git，安装项目依赖，并用表单写入本机账号配置。仅在用户明确要求初始化、安装工作区或 lx-init 时使用。"
---

# 初始化工作区

同一套初始化用于 FOG、lxworkspace 及源工作区；读取随仓 `config/distribution.json` 的能力清单，路径不得根据文件夹名字猜测。只准备当前工作区已分发的能力。

## 目标流程（必须按序，未跑完不算完成）

### 1. 目录确认（要选项）

- 默认路径：Windows `D:\Work\lxworkspace`；Mac `~/Documents/Work/lxworkspace`。
- 用选项确认：当前工作区是否为用户选定目标；若安装提示词阶段已选过路径，这里核对一致即可。
- 不一致：停住，请用户重指工作区；不自动改到别的盘乱建。

### 2. 必备软件（不问，缺了直接补）

检查这三样：

- Python >= 3.10
- Node.js
- Git

规则（用户已定 B2）：

- 缺了由 Agent **直接安装补上**，尽量不要再弹窗询问。
- 只装这三样，不装其它系统软件。
- 优先用系统包管理（Windows：`winget`；Mac：用户已有 Homebrew 则用 brew，否则用官方安装包途径并在结果里说明）。
- 需要系统权限弹窗时，让用户在系统对话框里点允许；那是系统行为，不是本 Skill 的「再问要不要装」。
- 装完立刻重检；仍失败则人话说明原因并停止后续步骤。
- **最终汇报必须列出**：本来就有的 / 本次新装的。

命令（默认会补齐缺失项，不要先只做检查来代替安装）：

`python tools/run_skill.py init-runtime`

只读查看计划、不安装时用 `python tools/run_skill.py init-runtime --check-only`。结果是 JSON，字段为 `present`、`newly_installed`、`failed`。安装器的原始输出不回显。

对人话术示例：「电脑里跑工作区需要的三样软件已经齐了；其中 Node 是刚帮你装上的。」

### 3. 项目依赖（不弹窗）

- 入口仍为：`python tools/run_skill.py init` 预览 → 环境与 hash 通过后执行
  `python tools/run_skill.py init --install --confirmed --expected-preview-hash <HASH>`。
- 只装项目内虚拟环境依赖（海报/卡券/端内图等），不弹 ask-user-question。Agent 自己读预览里的 `preview_hash` 填进命令，不要再问用户一次。
- 不另建第二套虚拟环境；端内图复用现有图片配置键。
- 装完一句话结果 + 独立 import 检查。

### 4. 本机配置（要表单）

仅展示已启用能力的字段；没有问答配置示例的工作区不要求问答 token。同事侧飞书等额外配置继续通过本仓配置示例与 `python tools/fog.py check` 核对，不把基础三项就绪当成全部集成已验收。

收集并写入（值由我方线下提前发给用户，用户对着表单填）：

| 表单字段 | 写入位置 |
| --- | --- |
| 公司库账号、密码 | `config/fog_config.yaml` → `lx_shujuku.api.username` / `password` |
| 图片 Key | 同文件 → `lx_haibao.image_api.kie.api_key` |
| 问答 token | `config/lx-wenda.json` → `token` |

规则：

- 正式文件已存在：**不覆盖已有非空内容**。只报告已存在、是否缺字段；空着的必填项可以补上，非空账号、密码、Key、token 保持原样。
- 不存在：从对应 `.example` 复制出正式文件再写入表单值。
- 表单先落到一个工作区外的临时文件，再运行 `python tools/run_skill.py init-config --from-json <表单.json>`，或 `--env-file <表单.env>`。JSON 键为 `username`、`password`、`image_key`、`wenda_token`。不要把值写进命令参数。
- **禁止**把密码、Key、token 写入聊天、日志、命令行回显、Git；脚本最多打印「已写入 / 仍缺哪一项」。
- 不修改系统级 MCP。

### 完成标准

同时满足才可称「初始化完成（已安装）」：

1. 工作区路径正确
2. 三样必备软件检查通过（含本轮新装说明）
3. 项目依赖安装并通过读回检查
4. 本机配置：已写入或确认已存在且必填项齐全

「真实服务已验收」另论：不因初始化完成而宣称公司库/问答真实联调已通过。

## 旧命令兼容

`python tools/fog.py init/check/migrate-config` 与 `scripts/config_wizard.py` 保留既有预览、确认和备份约束；不再创建 per-Skill 真实配置。新安装使用上面的统一向导。真实配置迁移仍须另行授权。

## 执行契约

### 输入

- 用户明确要求初始化/安装工作区；当前 WorkBuddy 工作区根目录。
- 目录选项结果；配置表单结果（仅用于本机落盘）。
- 安装项目依赖时的 `expected-preview-hash`。

### 输出

- 人话进度与最终汇总：路径、新装软件清单、项目依赖结果、配置就绪情况。
- 终端/脚本：检查与安装结果；配置写入成功与否（无密钥明文）。

### 验收

- 目录错误时零安装、零配置写入。
- 必备软件：缺则直接补；最终汇总含新装项；失败则停止后续。
- 项目依赖：无弹窗；未确认 hash 前（脚本层）不安装；确认后只改项目虚拟环境。
- 配置：无正式文件才创建；已有不覆盖非空凭据；全程无密钥回显。
- 不覆盖、不删除用户业务文件；不装本契约以外的系统软件。
