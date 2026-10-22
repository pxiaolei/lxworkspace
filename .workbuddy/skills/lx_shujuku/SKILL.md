---
name: lx_shujuku
description: 出行数据报表平台（dataReporting）公司数据库只读访问工具，提供只读 SQL、有界单遍游标分页、结构化证据包、表结构与白名单表索引元数据浏览、operator_brand 码表和业务查询模板。当用户要求查询公司数据库、dataReporting、宏鹄活动/运力/订单/卡券/详情、接起率或 TR 值时使用；不用于本地 RDS 查询或写库。
---

# lx_shujuku — 出行数据报表平台只读访问

## 功能

提供对 `datareporting.sfczhushou.com` 当前白名单业务表的只读查询能力：

1. **自动登录鉴权**：通过 `/dataReporting/user/login` 获取 Token，缓存复用
2. **当前 22 张业务表覆盖**：活动、免佣卡、运力、订单、卡券、详情汇总、分时明细、接起率、TR配置、毛利、对账、数据校验、传输统计、新渠道、特惠联盟等
3. **表结构与索引元数据浏览**：`describe <table>` 查看字段、类型、注释；`indexes <table>` 只查看白名单表的当前完整索引列顺序
4. **只读 SQL 查询**：代码层强制只允许 `SELECT` / `SHOW` / `DESCRIBE` / `EXPLAIN`
5. **operator_brand 码表接口**：提供品牌、城市、运营主体、对接人的稳定映射
6. **业务查询模板**：预置常用查询（按品牌/城市/日期筛选、多表关联等）

## 数据库信息

| 项目 | 值 |
|------|-----|
| 地址 | `http://datareporting.sfczhushou.com` |
| 登录接口 | `POST /dataReporting/user/login` |
| 查询接口 | `POST /dataReporting/sql-query/execute` |
| 鉴权方式 | Header: `token: {token}` |

## 当前 22 张业务表

| 表名 | 说明 | 字段数 |
|------|------|--------|
| `activity_data` | 活动信息主表 | 28 |
| `answer_rate_data` | 接起率数据表 | 10 |
| `brand_city_tr_data` | 品牌城市TR值配置表 | 8 |
| `card_data` | 免佣卡信息表 | 30 |
| `driver_real_time_data` | 运力实时累计数据表 | 23 |
| `honghu_activity_marketing_data` | 宏鹄活动营销数据表 | 32 |
| `honghu_capacity_data` | 宏鹄运力数据离线看板-运力数据表 | 26 |
| `honghu_check_data` | 宏鹄数据校验表 | 31 |
| `honghu_coupon_marketing_data` | 宏鹄卡券营销数据表 | 37 |
| `honghu_data_connect` | 鸿鹄数据对接列表 | 21 |
| `honghu_detail_data` | 宏鹄详情数据汇总表（汇总数据） | 44 |
| `honghu_driver_evaluation_data` | 宏鹄司机考核数据表 | 41 |
| `honghu_new_channel_data` | 宏鹄新渠道数据表 | 28 |
| `honghu_order_data` | 宏鹄订单数据离线看板-订单数据表 | 25 |
| `honghu_profit_data` | 宏鹄毛利数据表 | 22 |
| `honghu_recon_data` | 账单对账数据表 | 69 |
| `honghu_special_offer_data` | 宏鹄特惠联盟数据表 | 30 |
| `honghu_time_split_data` | 宏鹄订单运力分时-供需分时明细数据表 | 24 |
| `honghu_xf_driver_data` | 先锋司机数据 | 8 |
| `operator_brand` | 运营主体-品牌名称城市对照表 | 11 |
| `order_real_time_data` | 订单实时累计数据表 | 44 |
| `transport_data_report` | 鸿鹄传输数据统计明细 | 10 |

完整表结构定义见 `assets/schema.json`，人可读版本见 `references/table_catalog.md`。生成或修改生产查询 SQL 前，还必须完整读取 `references/sql-query-performance.md`。

## 新分享表如何支持

公司数据库持续新增分享表时，按以下步骤同步到本地白名单：

1. **先对比**：`db_tools.py schema-diff`
2. **确认后刷新**：`db_tools.py refresh-schema --confirmed`
3. 刷新后会自动备份旧的 `schema.json` 和 `table_catalog.md`。

本地文件输出统一执行覆盖门禁：目标不存在时属于 R1，可直接新建；目标已存在时属于 R2，必须同时传 `--overwrite --confirmed`。适用于 `query --output`、`query --audit` 解析出的证据包路径、`schema --output` 和 `schema-diff --output`。`refresh-schema` 固定覆盖 schema/catalog，必须显式 `--confirmed`；旧 `--yes` 仅保留兼容，等同 confirmed，不形成绕过入口。

如果临时需要查询一张尚未刷新进白名单的新表，可先使用：

```bash
"$WB_PYTHON" scripts/db_tools.py describe new_table --no-whitelist
"$WB_PYTHON" scripts/db_tools.py query "SELECT 1 FROM new_table LIMIT 1" --no-whitelist
# 按 describe 返回的真实字段改成明确列名和业务范围后再查，例如：
"$WB_PYTHON" scripts/db_tools.py query \
  "SELECT id, start_time, status FROM new_table WHERE start_time >= '2026-08-01 00:00:00' AND start_time < '2026-08-02 00:00:00' LIMIT 5" \
  --no-whitelist
```

> `--no-whitelist` 会跳过本地 schema 白名单校验，但仍受只读 SQL 策略和公司服务端允许表列表保护；如果服务端尚未授权该表，本地无法绕过。

## CLI 用法

运行前设置 Python 入口：

```bash
WB_PYTHON=python
# Windows PowerShell 使用 python scripts/db_tools.py；工作区日常查询优先用根 tools/run_skill.py database
```

```bash
# 列出所有表
"$WB_PYTHON" scripts/db_tools.py list-tables

# 查看指定表结构
"$WB_PYTHON" scripts/db_tools.py describe card_data

# 查看白名单表的索引元数据；固定 SHOW INDEX，不支持绕过白名单
"$WB_PYTHON" scripts/db_tools.py indexes honghu_capacity_data --json

# 查看全部表结构概览
"$WB_PYTHON" scripts/db_tools.py catalog

# 受限查询：明确字段和业务范围；命中 LIMIT 时会标记“可能截断”
"$WB_PYTHON" scripts/db_tools.py query \
  "SELECT id, brand_name, date_day, city_name, completed_order_count FROM honghu_order_data WHERE brand_name = '品牌A' AND date_day = '2026-08-01' LIMIT 5"

# 受限查询并保存结构化证据包
"$WB_PYTHON" scripts/db_tools.py query \
  "SELECT id, brand_name, date_day, city_name, completed_order_count FROM honghu_order_data WHERE brand_name = '品牌A' AND date_day = '2026-08-01' LIMIT 5" \
  --audit --question "查询订单样例"

# 显式输出目标已存在时，必须同时确认覆盖
"$WB_PYTHON" scripts/db_tools.py query \
  "SELECT id, brand_name, date_day, city_name, completed_order_count FROM honghu_order_data WHERE brand_name = '品牌A' AND date_day = '2026-08-01' LIMIT 5" \
  --output workspace/query.json --overwrite --confirmed

# 完整查询：SQL 不写顶层 LIMIT，必须用可唯一定位结果行的简单字段稳定排序
"$WB_PYTHON" scripts/db_tools.py query \
  "SELECT id, operator_entity, brand_name, city_name, contact_person FROM operator_brand ORDER BY id" \
  --full --page-size 500 --max-rows 10000 --audit \
  --question "导出当前完整运营主体品牌城市码表"

# 查询 operator_brand 码表
"$WB_PYTHON" scripts/db_tools.py operator-brands --operator "方舟行（上海）"

# 浏览指标口径目录
"$WB_PYTHON" scripts/db_tools.py metrics
"$WB_PYTHON" scripts/db_tools.py metrics brand_city_daily_completed_orders

# 输出兼容本地 Excel 码表的映射统计
"$WB_PYTHON" scripts/db_tools.py mabiao

# 按品牌和日期查询（业务模板）
"$WB_PYTHON" scripts/db_tools.py template capacity-by-brand --brand "方舟行车主" --date "2025-05-12"

# 查询限定业务范围的记录数
"$WB_PYTHON" scripts/db_tools.py count honghu_order_data \
  --where "brand_name = '品牌A' AND date_day = '2026-08-01'"

# 对比线上表结构和本地 schema
"$WB_PYTHON" scripts/db_tools.py schema-diff

# 预览并确认刷新 schema.json 与 table_catalog.md
"$WB_PYTHON" scripts/db_tools.py refresh-schema
"$WB_PYTHON" scripts/db_tools.py refresh-schema --confirmed
```

`activity-by-operator`、`activity-by-brand`、`card-by-brand` 和仅按商品名查询卡券的旧模板已失败关闭：当前线上索引不能支持这些过滤条件，普通 `EXPLAIN` 会出现 `type=ALL/key=NULL`。需要恢复时，先由技术/DBA确认可用索引，再补模板和执行计划测试。

## 配置

在项目根目录配置文件中填入**你自己的账号**：

```bash
config/fog_config.yaml
```

`lx_shujuku` 段需包含：

```yaml
lx_shujuku:
  api:
    base_url: "http://datareporting.sfczhushou.com"
    username: "你的用户名"
    password: "你的密码"
  query:
    default_limit: 100
    max_limit: 1000
```

⚠️ `config/fog_config.yaml` 已加入 `.gitignore`，凭证不会提交到版本控制。每位同事使用自己的账号。

## 快速上手（给同事）

```bash
# 1. 编辑 config/fog_config.yaml，填入你的用户名和密码

# 2. 验证连接
"$WB_PYTHON" scripts/db_tools.py health

# 3. 浏览数据库
"$WB_PYTHON" scripts/db_tools.py list-tables
"$WB_PYTHON" scripts/db_tools.py describe card_data

# 4. 查询样例数据；明确列名，命中 LIMIT 时只能称为受限结果
"$WB_PYTHON" scripts/db_tools.py query \
  "SELECT id, operator_entity, brand_name, city_name, contact_person FROM operator_brand ORDER BY id LIMIT 10"

# 5. 查询公司库码表
"$WB_PYTHON" scripts/db_tools.py operator-brands --limit 10

# 6. 查看指标口径目录
"$WB_PYTHON" scripts/db_tools.py metrics
```

## 给其他 Skill 的 import 入口

其他 Skill 应优先复用 `lx_shujuku` 包，不要直接拼接 `operator_brand` SQL：

```python
from lx_shujuku import create_client

client = create_client()
rows = client.get_operator_brands(operator="方舟行（上海）")
mapping = client.load_mabiao_mapping()
```

`load_mabiao_mapping()` 返回兼容 `lxx_share.excel_utils.load_mabiao()` 的结构，后续可用于替换本地 Excel 码表。

## 准确查询与证据包流程

查询结果分成两种模式，不能混用口径：

- **受限模式**：默认自动追加顶层 `LIMIT`。适合样例和 Top N；返回行数命中上限时，`is_complete=null`、`possible_truncation=true`，不得声称“全部”。
- **完整模式**：显式 `--full`。SQL 不得带顶层 `LIMIT`；除小码表 `operator_brand` 外必须包含顶层 `WHERE` 业务范围。结果超过一行时，必须使用简单字段或别名组成的顶层 `ORDER BY`，且排序字段组合必须唯一。脚本先计数，最多允许 20 页，再用单遍 keyset 游标读取并执行查询后计数；行数或边界不一致即停止。0/1 行聚合结果不要求排序。

完整模式必须同时使用 `--audit`、`--json` 或 `--output`，避免终端为了可读性截断长字段。业务查询示例：

```bash
"$WB_PYTHON" scripts/db_tools.py query "SELECT city_name, SUM(completed_order_count) AS completed_order_count FROM honghu_order_data WHERE brand_name = '拼哒出行' AND date_day = '2026-06-02' GROUP BY city_name ORDER BY completed_order_count DESC, city_name ASC" \
  --full --audit \
  --question "拼哒出行 2026-06-02 每个城市的完单数" \
  --metric brand_city_daily_completed_orders
```

完整模式生成版本 3 证据包，至少记录：原始问题、指标口径及目录 hash、原始/安全 SQL、执行时间、模式、返回/总行数、完整性状态、schema 快照时间及 hash、结果 SHA-256、单遍游标验证方法、实际页数、页数上限、结果行和风险提示。默认保存到 `assets/query_runs/`，该目录已加入 `.gitignore`，不要直接分享含敏感明细的数据包。若自动生成的目标名或显式 `--output` 已存在，默认停止；只有 `--overwrite --confirmed` 才可覆盖。

指标口径目录在：

```bash
references/metrics_catalog.json
```

传入 `--metric` 时，脚本会验证指标 ID 是否存在并把目录版本/hash 固化到证据包；未知 ID 会在查询前停止。AI 写文档前仍须读取该指标定义，确认表、日期字段、聚合方式和适用边界，并实际执行其中的 `verification_queries` 做业务口径交叉验证。

最终呈现数据时必须同时说明：

1. 数据源与查询时间。
2. 查询对象、日期范围、过滤条件和结果粒度。
3. 指标字段及聚合方式，尤其区分 `SUM(...)` 与 `COUNT(*)`。
4. 返回行数、完整性状态和验证方法。
5. 无结果、失败、权限不足、schema 漂移和可能截断必须原样说明。

不得把受限结果冒充完整结果，不得把终端截断后的长字段当作原值，也不得只凭 SQL 看起来正确就跳过结果读回和指标交叉验证。

## 数据来源

- API 网关: `http://datareporting.sfczhushou.com`
- 数据库: `dataReporting`（MySQL 兼容）
- Token 有效期: 服务端控制，脚本自动监测 401 后重新登录

## 依赖

- Python 3.9+，纯标准库（`urllib.request`），无需安装第三方包
- 无跨 Skill 依赖

## 限制

- 仅支持只读 SQL（代码层拦截写库/DDL/多语句）
- 默认普通 SQL 查询的表名必须存在于 `assets/schema.json` 白名单；临时新表可用 `--no-whitelist` 跳过本地白名单
- `describe` 会在本地白名单缺失时尝试读取线上结构；`count` 临时新表需显式追加 `--no-whitelist`
- `indexes` 只接受本地 schema 白名单中的一个简单表名，内部固定生成 `SHOW INDEX FROM <table>`；没有 `--no-whitelist`，不接受数据库限定名、SQL 片段、后缀条件或多语句
- `--no-whitelist` 不能绕过公司 dataReporting 服务端授权；服务端未加入允许查询范围的物理表仍然不可访问
- 普通 `SELECT` 未写顶层 `LIMIT` 时会自动追加限制；字符串和子查询中的 `LIMIT` 不再误判为顶层限制
- 普通查询的顶层 `OFFSET` 不能超过 10000；更深分页必须改用稳定游标或缩小范围
- `--full` 最大行数默认 10000，可按任务显式调低或调高，但无论行数配置如何都不能超过 20 页；超过门禁先缩小日期或业务范围
- 除 `operator_brand` 外，完整查询必须带顶层 `WHERE`；全历史和无界事实表查询失败关闭
- 多行完整模式要求排序字段组合唯一，并进行单遍 keyset 游标读取、前后计数和行数校验；0/1 行聚合结果免排序。查询服务不提供事务快照，无法排除查询期间非排序字段原地变更，证据包会保留这一边界说明
- `SHOW` 仅允许表、字段元数据，以及白名单表的精确 `SHOW INDEX FROM <table>` 查询
- Token 过期时有自动刷新机制
- 刷新 schema 前先运行 `schema-diff`；`refresh-schema` 默认只预览，只有追加 `--confirmed`（旧 `--yes` 兼容）才会写入并自动备份旧文件

## 执行契约

### 输入

- 原始业务问题、公司 dataReporting 查询对象、日期范围、筛选条件和所需粒度。
- 查看索引时只提供本地 schema 白名单表名，不提供 SQL；服务端拒绝时停止，不改走 `information_schema`。
- 结果模式：样例/Top N 使用受限模式；要求“全部、完整、准确导出”时使用 `--full`。
- 涉及标准指标时提供 `--metric`，并以 `references/metrics_catalog.json` 为口径真源。
- 可选本地证据包/schema/diff 输出目标；已有目标覆盖必须同时给出 `--overwrite --confirmed`。

### 输出

- 实际执行的安全 SQL、查询时间、返回行数、完整性状态、结果 SHA-256 和真实结果行。
- `indexes --json` 返回数据库、表名、查询时间及按索引名和列顺序排序的唯一性、字段、基数、可空性、类型、可见性等元数据。
- `--audit/--json/--output` 的完整模式生成版本 3 证据包；失败、无结果、权限不足和漂移单独呈现。

### 验收

- 查询前 schema 与线上无未解释漂移；字段、过滤条件、粒度和聚合方式与业务问题一致。
- 索引元数据入口必须在发送前拒绝未知表和任何 `SHOW INDEX` 后缀，且没有白名单绕过参数。
- 受限查询命中 LIMIT 时不得报告完整；深 `OFFSET` 必须在发送前拒绝；多行完整查询必须通过顶层范围、20 页上限、唯一稳定排序、单遍 keyset 游标、前后计数和行数一致性校验，0/1 行聚合结果免排序但仍执行单次读取与前后计数。
- 标准指标实际执行 catalog 中的交叉验证 SQL，主结果与验证结果可对账后才用于报告或对外同步。
- 新输出可直接创建；已有输出未同时确认 overwrite 时，客户端写命令为零且目标文件不变。
- `refresh-schema` 未 confirmed 时 schema/catalog 与备份集合不变；confirmed 后两份旧文件均有备份。

## 共享工作区查询入口

`python tools/run_skill.py database --sql "<只读SQL>" --question "<业务问题>"` 默认预览；确认后使用同一 preview_hash。入口仅使用本工作区公司账号，完整结果经中央审计分页与读回，输出新任务目录。伙伴基础异动使用 lx-yidong，不调用私人 RDS。FOG 的性能检查已合入公共实现，原严格 SQL、唯一排序、分页校验继续有效；operator_brand 码表全量例外保留，事实表不得因此豁免。
