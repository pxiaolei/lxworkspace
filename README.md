# lxworkspace

Windows + WorkBuddy 合作伙伴工作区。提供公司库查询、基础异动、端外海报、端内宣传图、问答、卡券文件、调账文件，以及初始化、更新。数据库使用你自己的公司账号，可见数据由公司服务端控制。卡券与调账只生成本地文件，不上传后台。

## 接收工作区

将下面整段发给 WorkBuddy。仓库首次公开发布后才可使用；本地发布准备已开展，Windows 安装与真实服务需在试点机验证。

```text
请帮我安装合作伙伴工作区 https://github.com/pxiaolei/lxworkspace。
先检查当前工作目录，以及 Python >=3.10、Node.js、Git 是否可用。
在我选定的工作目录下创建新的 lxworkspace 子目录，从这个公开仓库 main 分支 clone，不要求 GitHub 登录。
如果目录已有内容、缺少软件或下载失败，说明具体情况并停在该步骤，不覆盖、删除或全局安装。
先读取 lxworkspace/AGENTS.md、README.md、SKILLS.md，再把 lxworkspace 设为 WorkBuddy 的工作区，不把 Skill 安装到全局目录。
如需我在客户端选择工作目录，告诉我选中下载得到的 lxworkspace 文件夹；不要假定你已经替我切换了目录。
运行 python tools/run_skill.py init，展示缺项和项目虚拟环境安装范围，确认后才运行安装。
10 个预设品牌的配置、Logo、二维码随仓库提供，不要求我额外添加；缺少品牌时报告缺口，不编造素材。
账号密码、图片 Key、问答 token 由我方线下提供，只填写本机配置，不让我发到聊天里，不修改系统级 MCP 配置。
完成后运行 Skill 索引检查和本地检查，区分“已安装”和“真实服务已验收”，告诉我可以怎样调用这七项业务能力。
检查当前客户端是否加载 .codebuddy/skills 中的九个项目入口；未显示时报告版本和实际情况，不把读取了文件说成已注册。
```

## 本机准备

我方帮助完成运行环境、项目虚拟环境和线下账号配置。`config/fog_config.yaml.example`、`config/lx-wenda.json.example` 对应本机真实文件；填账号前需用户授权，现有文件不覆盖。公司库、图片服务、问答各自使用独立凭据。

10 个官方预设品牌的配置、Logo 和二维码已随包提供：幸福千万家、哈啰、安安用车、蔚蓝滴仕、旗妙出行极速版、易达出行、江南出行、土生途行、网路优行、日初出行。调用时仍需伙伴提供活动内容。

品牌配置在 `.workbuddy/skills/lx-haibao/brands/<brand_id>.yaml`，素材在该 Skill 的 `brand-assets/<brand_id>/`。官方文件由 `contracts/partner-brand-presets.json` 精确登记，后续新增或升级通过“更新 Skill”接收。不要手改官方预设；自定义品牌使用独立 ID，继续留本机并受保护，同名冲突时停止更新并联系我方。

## 日常说法

- “查我账号下某品牌某城市 10 月 1 日到 7 日的完单数据。”
- “分析 10 月 1 日到 7 日与 9 月 24 日到 30 日的异动。”未说周期或比较规则时先补齐，不默认日期。
- “用这份活动内容生成某品牌某城市海报。”先提供活动文字，预览后再生成。
- “用这份活动内容做司机端首页、开屏和透明弹窗图。”端内图先预览范围，确认样张并逐张看图，用户再确认批量；无法看图时保留待核验。
- “问答：司机端活动配置怎么操作？”问答先预览，确认后调用。
- “按这些活动信息生成免佣卡/飞涨卡文件。”收集必填项、预检后生成 XLSX，并读回验证；不创建真实卡券。
- “把这份司机补款/扣款数据生成调账文件。”预检后生成 CSV 并读回验证；不执行真实调账。
- “更新 Skill。”先展示远端完整 commit 与变化，再确认快进。

命令统一通过 `python tools/run_skill.py ...`（Windows 可用 `tools\lx.cmd ...`）；WorkBuddy 按各 Skill 契约组织参数。你不需要操作 Git 命令。

当前[官方项目文档](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Project)列出 `.codebuddy/skills`。本仓在那里提供九个薄入口，指向 `.workbuddy/skills` 中的原始契约和脚本，品牌资料位置不变。数据库的加载别名为 `lx-shujuku`；Windows 客户端实际加载仍需验收。

卡券、调账分别通过 `python tools/run_skill.py kaquan ...`、`python tools/run_skill.py diaozhang ...` 执行。卡券依赖 openpyxl，由初始化入口经确认后装入现有项目虚拟环境；调账不增加第三方依赖。任务材料放 `workspace/06后台操作/卡券生成/` 或 `调账文件/` 下的新任务目录，细则见 `workspace/README.md`。

端内宣传图通过 `python tools/run_skill.py zuotu ...` 执行，覆盖首页、开屏、透明弹窗、侧边栏和权益中心5类8规格。lx-init将端内图依赖合并到已有项目虚拟环境，不另建环境或新增Key；继续使用本工作区的KIE配置。活动输入、样张、正式图和核验证据放 `workspace/08端内宣传图/<活动名>/`，目录规则见该区域README。代码与14张必要风格图已分发并获公开授权；参考图只指导风格，不得作为当前活动事实。

## 维护与验证

```text
python -B tools/generate_skill_index.py --check
python -B tools/generate_workbuddy_entries.py --check
python -B tools/check_distribution.py
python -B tools/run_tests.py
```

公共代码真源在 p-fog，文件和 hash 见 `config/source-lock.json`；伙伴入口和文档由本仓维护。代码不在伙伴本机手改，使用更新入口接收升级。更新禁止删除、强制覆盖或改真实配置；遇本地改动联系我方处理。

当前试点只验证安装/能力发现、公司库查询、基础异动、MaxKB 问答和更新。用户已决定端外海报、端内图、卡券、调账免专项验收，日常生成时的确认与文件/二维码校验仍保留；免验不代表真实服务已验收。具体步骤见 [PILOT.md](PILOT.md)。

维护者新增官方品牌时：先确认公开范围，检查配置/模板/素材，再更新源仓的品牌清单及 hash、伙伴同步白名单、本仓 `.gitignore` 的精确例外；运行同步预览与确认、分发检查和更新测试后发布。伙伴只需运行更新入口，无需手动复制品牌包。

本次实现范围、测试结果和未验证项见 [VALIDATION.md](VALIDATION.md)。
