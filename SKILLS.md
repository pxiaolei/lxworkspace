# lxworkspace Skill 索引

> 本文件由 `python3 tools/generate_skill_index.py` 根据真实目录生成；不要手工维护 Skill 清单。具体流程、输入输出和安全边界以各 Skill 的 `SKILL.md` 为准。

当前共 10 个 Skill。`显式` 表示 `allow_implicit_invocation: false`，必须由用户明确点名或确认后使用。

| Skill | 显示名 | 调用策略 | 入口 | 描述 |
| --- | --- | --- | --- | --- |
| `lx-haibao` | 端外海报生成 | 显式 | [`SKILL.md`](.workbuddy/skills/lx-haibao/SKILL.md) | 司机活动海报生成。用于从城市活动 TXT 生成已配置品牌的司机活动海报；按 check、dry-run、confirmed sample/full generation 执行；自动按 TXT 文件名识别品牌；共享工作区默认只使用 KIE 图片 Provider；默认 hybrid 模式把模板图和真实 Logo 传给模型生成海报主体，再由脚本贴入真实二维码并逐张验证。 |
| `lx-init` | 初始化工作区 | 显式 | [`SKILL.md`](.workbuddy/skills/lx-init/SKILL.md) | 工作区初始化：确认目录，缺则补齐 Python/Node/Git，安装项目依赖，并启动本机窗口填写账号配置。仅在用户明确要求初始化、安装工作区或 lx-init 时使用。 |
| `lx-kaquan` | 卡券文件生成 | 可隐式路由 | [`SKILL.md`](.workbuddy/skills/lx-kaquan/SKILL.md) | 按宏鹄原模板生成飞涨卡或免佣卡的批量创建 XLSX，使用已确认默认值，校验用户必填字段和条件约束，保留表头及重要说明并读回验收。用户要求卡券文件、飞涨卡模板、免佣卡模板或 lx-kaquan 时使用。 |
| `lx-tiaozhang` | 调账 CSV 生成 | 可隐式路由 | [`SKILL.md`](.workbuddy/skills/lx-tiaozhang/SKILL.md) | 生成司机补款或扣款调账 CSV，按自带模板校验必填字段、金额范围与精度，移除输出中的说明行并读回验证。用户要求调账模板、补款文件、扣款文件或 lx-tiaozhang 时使用。 |
| `lx-update` | FOG 安全更新 | 显式 | [`SKILL.md`](.workbuddy/skills/lx-update/SKILL.md) | 检查官方共享仓版本，确认完整 commit 后快进并保护本机资料。 |
| `lx-wenda` | 宏鹄产品问答 | 显式 | [`SKILL.md`](.workbuddy/skills/lx-wenda/SKILL.md) | 宏鹄 SaaS 产品知识库问答。当用户询问宏鹄工作台（honghusaas.com/workbench）的产品使用方法、操作步骤、菜单路径、功能说明或后台配置流程时使用。触发词：宏鹄知识库、产品文档、工作台怎么用、配置中心、B端开城、开城/关城、计价配置、城市规则配置、活动怎么配、司机端功能、后台在哪里设置。 |
| `lx-yidong` | 基础异动分析 | 可隐式路由 | [`SKILL.md`](.workbuddy/skills/lx-yidong/SKILL.md) | 按明确本期与对比期分析完单、发单、运力、补贴力度及效率变化。 |
| `lx-zuotu` | 端内宣传图生成 | 显式 | [`SKILL.md`](.workbuddy/skills/lx-zuotu/SKILL.md) | 从活动 TXT 或 YAML 生成司机端首页、开屏、透明弹窗、侧边栏和权益中心宣传图。按 check、dry-run、confirmed sample/full 执行，仅用现有 KIE；模型绘制全部文字和按钮，脚本严格处理尺寸、字节上限和格式，并绑定最终图片哈希逐张视觉验收。 |
| `lx_shujuku` | 公司数据库只读查询 | 可隐式路由 | [`SKILL.md`](.workbuddy/skills/lx_shujuku/SKILL.md) | 出行数据报表平台（dataReporting）公司数据库只读访问工具，提供只读 SQL、有界单遍游标分页、结构化证据包、表结构与白名单表索引元数据浏览、operator_brand 码表和业务查询模板。当用户要求查询公司数据库、dataReporting、宏鹄活动/运力/订单/卡券/详情、接起率或 TR 值时使用；不用于本地 RDS 查询或写库。 |
| `lxx_share` | 内部配置基础库 | 显式 | [`SKILL.md`](.workbuddy/skills/lxx_share/SKILL.md) | 供业务 Skill 读取本机统一配置与项目路径，不作为伙伴业务入口。 |

## 校验

```bash
python3 tools/generate_skill_index.py --check
```

该命令同时校验：Skill 名不重复、frontmatter 精确为 `name`/`description`、执行契约结构唯一、`agents/openai.yaml` 元数据和调用策略完整，以及本索引未漂移。
