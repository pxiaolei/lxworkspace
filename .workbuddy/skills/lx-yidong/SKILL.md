---
name: lx-yidong
description: "按明确本期与对比期分析完单、发单、运力、补贴力度及效率变化。"
---

# 基础异动分析

先询问本期、对比期起止日期，或让用户明确比较规则后展示实际日期。不能默认昨日、D7/D1、上周。支持 daily（双方各一天）、weekly（双方各七天）、range（双方连续日期）。品牌城市仅为筛选及结果标识；不要求主体，不分析开城/同城背景、渠道、小时、天气、私人事件。基础层依次为发单、运力、补贴力度、效率，先识别完单变化。

`python tools/run_skill.py anomaly --period-type weekly --start 2026-10-01 --end 2026-10-07 --compare-start 2026-09-24 --compare-end 2026-09-30`

默认预览，展示公司 SQL、日期、阈值和产物目录；确认后追加 `--confirmed --expected-preview-hash <HASH>`。固定 `partner_basic`，不接受 RDS/private/input-json 入口。阈值读取根配置 lx_yidongfenxi，可显式调整；默认变化率 5%、绝对变化 500、最低完单 0，变化率或绝对变化命中即可。多日绝对量为日均，比例为周期总分子/总分母。500 是可调整初值，不继承私人规模门槛；零基期无法计算变化率，只按绝对量识别。

## 执行契约

### 输入

明确本期和对比期、周期类型；可选品牌城市；本机公司账号。

### 输出

新任务目录中的 input.json、analysis.json、analysis.md、query-runs.json。

### 验收

缺日期先询问；缺天/重复日粒度/完单 NULL 阻断，不补零；辅助指标缺失仅降级相关解释。不得出现禁用归因或其缺项，保留实际零和小规模变化。


配置节继续使用 `lx_yidongfenxi` 兼容既有配置；Skill 改名不要求修改真实配置。
