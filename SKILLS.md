# lxworkspace Skill 索引

> 本文件由 `python3 tools/generate_skill_index.py` 根据真实目录生成；不要手工维护 Skill 清单。具体流程、输入输出和安全边界以各 Skill 的 `SKILL.md` 为准。

当前共 10 个 Skill。`显式` 表示 `allow_implicit_invocation: false`，必须由用户明确点名或确认后使用。

| Skill | 显示名 | 调用策略 | 入口 | 描述 |
| --- | --- | --- | --- | --- |
| `lx-diaozhang` | 调账 CSV 生成 | 可隐式路由 | [`SKILL.md`](.workbuddy/skills/lx-diaozhang/SKILL.md) | 生成司机补款或扣款调账 CSV，按自带模板校验必填字段、金额范围与精度，移除输出中的说明行并读回验证。用户要求调账模板、补款文件、扣款文件或 lx-diaozhang 时使用。 |
| `lx-haibao` | 活动海报 | 可隐式路由 | [`SKILL.md`](.workbuddy/skills/lx-haibao/SKILL.md) | 使用伙伴提供的活动内容与我方品牌资料，预览后生成并验证海报。 |
| `lx-init` | 初始化工作区 | 显式 | [`SKILL.md`](.workbuddy/skills/lx-init/SKILL.md) | 检查 Windows 工作区和运行环境，确认后只安装项目虚拟环境依赖。 |
| `lx-kaquan` | 卡券文件生成 | 可隐式路由 | [`SKILL.md`](.workbuddy/skills/lx-kaquan/SKILL.md) | 按宏鹄原模板生成飞涨卡或免佣卡的批量创建 XLSX，使用已确认默认值，校验用户必填字段和条件约束，保留表头及重要说明并读回验收。用户要求卡券文件、飞涨卡模板、免佣卡模板或 lx-kaquan 时使用。 |
| `lx-update` | 更新工作区 | 显式 | [`SKILL.md`](.workbuddy/skills/lx-update/SKILL.md) | 检查官方共享仓版本，确认完整 commit 后快进并保护伙伴本机资料。 |
| `lx-wenda` | 产品知识库问答 | 可隐式路由 | [`SKILL.md`](.workbuddy/skills/lx-wenda/SKILL.md) | 调用我方完整 MaxKB 知识库，支持预览与绑定确认的多轮问答。 |
| `lx-yidongfenxi` | 基础异动分析 | 可隐式路由 | [`SKILL.md`](.workbuddy/skills/lx-yidongfenxi/SKILL.md) | 按明确本期与对比期分析完单、发单、运力、补贴力度及效率变化。 |
| `lx-zuotu` | 端内宣传图生成 | 显式 | [`SKILL.md`](.workbuddy/skills/lx-zuotu/SKILL.md) | 从活动 TXT 或 YAML 生成司机端首页、开屏、透明弹窗、侧边栏和权益中心宣传图。按 check、dry-run、confirmed sample/full 执行，仅用现有 KIE；模型绘制全部文字和按钮，脚本严格处理尺寸、字节上限和格式，并绑定最终图片哈希逐张视觉验收。 |
| `lx_shujuku` | 公司数据库 | 可隐式路由 | [`SKILL.md`](.workbuddy/skills/lx_shujuku/SKILL.md) | 使用自己的公司账号查询可见数据，保留完整取数证明和本地结果。 |
| `lxx_share` | 内部配置基础库 | 显式 | [`SKILL.md`](.workbuddy/skills/lxx_share/SKILL.md) | 供业务 Skill 读取本机统一配置与项目路径，不作为伙伴业务入口。 |

## 校验

```bash
python3 tools/generate_skill_index.py --check
```

该命令同时校验：Skill 名不重复、frontmatter 精确为 `name`/`description`、执行契约结构唯一、`agents/openai.yaml` 元数据和调用策略完整，以及本索引未漂移。
