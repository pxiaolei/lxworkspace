---
name: lx-update
description: "检查官方共享仓版本，确认完整 commit 后快进并保护本机资料。"
---

# 更新工作区

运行 `python tools/run_skill.py update check`。只接受随仓 `config/distribution.json` 绑定的官方仓库（FOG 为 pxiaolei/FOG，lxworkspace 为 pxiaolei/lxworkspace）、main 分支、干净普通 clone。远端检查使用隔离临时 Git 仓，结束清理，不修改当前工作区或业务文件。

展示完整 remote_commit 和新增/修改清单，用户确认后运行 `python tools/run_skill.py update apply --confirmed <40位COMMIT>`。确认目标已变、脏改动、分叉、本地冲突、未授权远端删除/符号链接/保护路径即停止。禁止 stash、reset、强制覆盖、删除、合并冲突。

保护根真实配置、自定义品牌 YAML/素材、workspace、缓存与 .venv。官方品牌文件只有通过远端 contracts/partner-brand-presets.json 的精确路径与 hash 校验才允许新增或升级；本机改动或 ignored 同名文件冲突仍停止。更新后读回 HEAD/origin/main、检查工作区和 Skill 索引/分发边界。预览不安装依赖；代码升级需要的新依赖单独通过初始化确认。

真实配置中的 token、API Key、账号、目录和个人偏好逐字段保留；更新不自动迁移、清空或重排字段。配置模板新增字段时另走初始化/配置迁移预览及明确授权。

FOG 原有精确退役清单继续有效；伙伴默认拒绝所有删除。首批伙伴从当前版本全新安装，无需旧包迁移。源仓 p-fog 不使用客户端更新；缺少或不匹配官方身份时停止。

兼容旧命令：`python .workbuddy/skills/lx-update/scripts/update_fog.py check`，旧 `update_workspace.py` 仅转发同一实现。

## 执行契约

### 输入

官方 main 普通 clone；apply 需要本次 check 的完整 commit。

### 输出

终端版本与变更预览，或已确认快进结果和读回验证。

### 验收

不允许未授权删除或保护路径变更；目标 commit 严格匹配，快进后独立验证；异常停止不自动回滚。
