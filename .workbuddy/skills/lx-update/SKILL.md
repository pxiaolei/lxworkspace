---
name: lx-update
description: "检查官方共享仓版本，确认完整 commit 后快进并保护伙伴本机资料。"
---

# 更新工作区

运行 `python tools/run_skill.py update check`。只接受官方 pxiaolei/lxworkspace、main 分支、干净普通 clone。远端检查使用隔离临时 Git 仓，结束清理，不修改当前工作区或业务文件。

展示完整 remote_commit 和新增/修改清单，用户确认后运行 `python tools/run_skill.py update apply --confirmed <40位COMMIT>`。确认目标已变、脏改动、分叉、本地冲突、远端删除/符号链接/保护路径即停止。禁止 stash、reset、强制覆盖、删除、合并冲突。

保护根真实配置、自定义品牌 YAML/素材、workspace、缓存与 .venv。官方品牌文件只有通过远端 contracts/partner-brand-presets.json 的精确路径与 hash 校验才允许新增或升级；本机改动或 ignored 同名文件冲突仍停止。更新后读回 HEAD/origin/main、检查工作区和 Skill 索引/分发边界。预览不安装依赖；代码升级需要的新依赖单独通过初始化确认。

## 执行契约

### 输入

官方 main 普通 clone；apply 需要本次 check 的完整 commit。

### 输出

终端版本与变更预览，或已确认快进结果和读回验证。

### 验收

不允许删除/保护路径变更；目标 commit 严格匹配，快进后独立验证；异常停止不自动回滚。

