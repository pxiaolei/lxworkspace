---
name: lx-init
description: "检查 Windows 工作区和运行环境，确认后只安装项目虚拟环境依赖。"
---

# 初始化工作区

运行 `python tools/run_skill.py init`，只检查 Python/Node/Git、项目虚拟环境、配置文件存在情况，展示 preview_hash。缺全局运行环境联系我方，不自动安装；不建日志/目录/配置，不调用业务服务。展示安装范围后显式确认，再运行 `python tools/run_skill.py init --install --confirmed --expected-preview-hash <HASH>`。沿用 lx-haibao/.venv 作为现有项目运行环境，安装海报、卡券及端内宣传图的requirements，并将三份依赖文件hash绑定到预览；安装后独立import检查。端内图不另建虚拟环境，复用现有KIE配置；调账仅使用标准库，无全局安装。

十个官方品牌的配置、Logo、二维码随仓就绪，不另装品牌包；我方线下帮助用户填写根账号配置。真实配置的创建/填写另需用户明确授权，现有文件不覆盖。不让用户把账号/Key/token 发送到聊天，不建立系统级 MCP。初始化完成分别报告环境、缺项、本地检查与真实验收状态。

## 执行契约

### 输入

选定的新工作区、已有 Python >=3.10/Node/Git；安装时当前预览 hash。

### 输出

终端检查/安装结果；仅已确认安装才创建项目 .venv。

### 验收

check 未确认零安装/零配置写入/零服务调用；确认 hash 不符即停止，安装后独立 import 检查。

