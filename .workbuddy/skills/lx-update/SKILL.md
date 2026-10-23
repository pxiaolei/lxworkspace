---
name: lx-update
description: "检查当前工作区绑定仓库的版本，确认完整 commit 后更新并保护本机配置与业务文件。"
---

# 更新工作区

运行 `python tools/run_skill.py update check`。只接受随仓 `config/distribution.json` 绑定的官方仓库（FOG 为 pxiaolei/FOG，lxworkspace 为 pxiaolei/lxworkspace）、main 分支、干净普通 clone。远端检查使用隔离临时 Git 仓，结束清理，不修改当前工作区或业务文件。

展示当前版本、目标版本、完整 remote_commit、文件变更和逐项迁移，用户确认整个范围后运行 `python tools/run_skill.py update apply --confirmed <40位COMMIT>`。apply重新核对相同完整commit。确认目标已变、版本未递增/降级、未来协议、脏改动、分叉、本地冲突、未声明删除/符号链接/保护路径即停止。禁止 stash、reset、强制覆盖、任意删除或合并冲突。

保护根真实配置、自定义品牌 YAML/素材、workspace、缓存与 .venv。官方品牌文件只有通过远端 contracts/partner-brand-presets.json 的精确路径与 hash 校验才允许新增或升级；本机改动或 ignored 同名文件冲突仍停止。更新后读回 HEAD/origin/main、检查工作区和 Skill 索引/分发边界。预览不安装依赖；代码升级需要的新依赖单独通过初始化确认。

真实配置中的 token、API Key、账号、目录和个人偏好逐字段保留；更新不自动迁移、清空或重排字段。配置模板新增字段时另走初始化/配置迁移预览及明确授权。

## 版本与工程迁移

版本和迁移读取目标commit的 `config/workspace-release.json`；工程归属与hash沿用新旧 `config/source-lock.json`。只接受已声明的逐文件退役：旧文件属于旧锁且Git原文hash在允许集合，替代文件（如有）属于新锁且原文hash匹配，路径不受保护。声明只含数据，不执行脚本；累计声明支持跳版本。整项Skill退出时若旧目录含额外本机资料则停止，不整目录清空。

没有版本文件的历史安装显示legacy；旧版FOG历史例外只对未版本化发布兼容，新版按声明审核。源仓p-fog不使用客户端更新，身份缺失或不匹配时停止。2026-10-23起支持本人80d6179测试副本原地升级，替代此前“只需新装”的阶段约定；其他伙伴首次安装新机制版本。

## 旧更新器首次引导

旧更新器可能在取到新代码前拒绝改名，不能让用户手改白名单或直接merge。请对接人提供本仓固定发布的40位commit和 `tools/bootstrap_update.py` 的SHA256；将该固定commit下的引导脚本下载到工作区外的系统临时目录，核对SHA256后执行：

`python <临时目录>/bootstrap_update.py --repo <原工作区绝对路径> --commit <40位COMMIT> check`

先展示版本、完整commit、变更和退役清单，用户确认后同一命令改为 `apply --confirmed <同一COMMIT>`。引导在临时仓取得固定版本并按新版来源锁校验更新器及依赖，随后对原工作区运行新版工具；不覆盖旧脚本、不读取凭据、不重装环境。未给出固定commit/可信SHA256、校验失败或远端已变时停止，不执行从latest随意下载的脚本。确认后升级成功，后续仍使用 `/lx-update`。

失败时先区分“未执行”与“已快进但验收失败”，核对实际HEAD、状态和旧commit，保留现场联系对接人；不自动回滚。需恢复时根据Git保留的旧commit另行确认工程文件恢复，真实配置与业务资料不参与恢复操作。

兼容旧命令：`python .workbuddy/skills/lx-update/scripts/update_fog.py check`，旧 `update_workspace.py` 仅转发同一实现。

## 执行契约

### 输入

官方 main 普通 clone；apply 需要本次 check 的完整commit及已展示迁移范围。旧安装首次引导另需固定脚本SHA256核验。

### 输出

终端版本、变更与迁移预览，或已确认快进结果和HEAD/origin/main、干净状态、Skill索引及分发边界读回。

### 验收

未确认零原工作区写入；双锁与原文不符、未声明删除、保护路径、额外资料或未来协议阻断。固定commit严格匹配；快进后独立读回，失败不报成功、不自动回滚。离线测试覆盖真实80d6179与db13527树进入新版；Windows实机仍单独验收。
