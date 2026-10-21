# dataReporting 索引友好 SQL 规则

本文件只约束通过 `lx_shujuku` 对公司 `dataReporting` 执行的只读 SQL，不适用于维护者的 RDS。目标是减少不必要的全表扫描和生产库负载，但任何“命中索引”结论都必须以当前执行计划为证据。

## 1. 真源与执行前检查

1. 先运行 `schema-diff`，确认本地 `assets/schema.json` 与线上没有未解释漂移。
2. 从 `assets/schema.json` 或 `references/table_catalog.md` 核对真实字段名和类型，不凭历史提示词猜字段。
3. `schema.json` 的字段级 `PRI/MUL` 只能说明字段出现在某类键中，不能还原索引名、完整列顺序、选择性或优化器是否会采用该索引。
4. 只有普通 `EXPLAIN` 显示实际 `key` 后，才能描述本次计划使用了哪个索引。完整索引列顺序只通过 `db_tools.py indexes <白名单表> --json` 固定入口读取；服务端拒绝时明确标记“待技术/DBA核验”，不得改扫 `information_schema` 或补写索引清单。

## 2. 强制生成规则

以下可静态判断的规则由 `lx_shujuku` 中央入口在发送 SQL 前强制执行：禁止 `SELECT *`、无业务范围的事实表查询、`WHERE 1=1`、谓词列函数/`COLLATE`/算术表达式、前导通配 `LIKE`，以及报表日期字段的 `<=`/`BETWEEN` 闭区间。`--no-whitelist` 只跳过本地表白名单，不能绕过这些性能门禁。

- 连接和过滤优先使用裸字段直接比较。候选字段上不得使用 `TRIM`、`CAST`、`CONVERT`、`COLLATE`、`UPPER`、`LOWER`、`YEAR`、`MONTH`、`DATE` 或算术表达式来临时兼容数据。
- JOIN 两侧先核对字段类型、字符集和排序规则。若不兼容，停止并报告 schema/数据质量问题，不生成带函数转换的生产 SQL。
- `LEFT JOIN`、`INNER JOIN` 按结果语义选择。只有确认未匹配行本就应被排除时，才能使用 `INNER JOIN`。
- 大表或行数未知的事实表必须带有能缩小业务范围的 `WHERE`。优先使用时间范围，再按问题添加品牌、城市等过滤；不能只写 `WHERE 1 = 1`。
- 日期范围优先使用半开区间，例如 `date_day >= '2026-07-01' AND date_day < '2026-08-01'`。对 `varchar` 日期字段，先确认线上值统一为 `YYYY-MM-DD` 等可按字典序比较的格式。
- 只选择任务需要的列，避免 `SELECT *`。普通样例或 Top N 使用合理 `LIMIT`；`LIMIT` 不能替代选择性过滤，也不能证明没有全表扫描。
- 用户要求全部、完整或准确导出时，SQL 不写顶层 `LIMIT`，改走 `query --full` 的前后计数、稳定唯一排序、单遍 keyset 游标、行数和 hash 验证。
- 聚合结果中的非聚合输出字段必须进入 `GROUP BY`；`MAX()`、`COUNT()` 等单值聚合无需强行分组。
- `COUNT(*)` 只用于统计源行数。完单量等可加总业务指标必须按 `references/metrics_catalog.json` 使用相应 `SUM(...)` 口径并运行交叉验证。
- 不主动生成 `FORCE INDEX`、索引 DDL、`ANALYZE TABLE` 或其他可能改变生产状态的语句。

## 3. 当前字段陷阱

以下只描述当前 schema 快照中的字段事实，执行前仍须检查漂移：

- `order_real_time_data` 使用 `tenant_name`，日期字段为 `datae_column_b4276e28f8_day_real`，不能假设存在 `brand_name` 或 `date_day`。
- `driver_real_time_data` 使用 `tenant_name`，日期字段为 `datae_column_bc7a384cd7_day_real`；当前 schema 没有 `city` 字段，不能按历史提示词直接生成城市过滤。
- 多张宏鹄表的 `date_day` 是 `varchar` 而非原生日期类型；不得在字段上套日期函数，且必须先确认实际值格式。
- `operator_brand` 与事实表的同名文本字段长度未必一致；相同列名不等于类型、字符集和排序规则已完全兼容。

## 4. EXPLAIN 门禁

新增或修改的大表 JOIN、聚合查询在真实执行前先运行普通 `EXPLAIN`，至少检查：

- `key`：是否为预期索引；`NULL` 必须解释。
- `type`：大事实表出现 `ALL` 时默认停止，除非技术确认扫描范围可接受。
- `rows` 与 `filtered`：估算扫描量是否与业务范围相称。
- `Extra`：关注 `Using temporary`、`Using filesort` 等信号，但不能脱离数据量和整体执行计划单独判死刑。

默认不在生产使用 `EXPLAIN ANALYZE`，因为它会真实执行查询。执行计划不明确、扫描范围明显失控或无法取得必要索引证据时，停止查询并把 SQL、业务范围及缺失证据交给技术/DBA。

## 5. 生成后自检

- [ ] 字段名和类型来自当前 schema，无未解释漂移。
- [ ] JOIN/WHERE 候选字段没有函数、`COLLATE`、算术表达式或前导通配。
- [ ] JOIN 类型符合业务语义，连接两侧兼容性已核对。
- [ ] 大表查询有明确业务范围，只选择必要字段。
- [ ] 普通查询有合理 `LIMIT`；完整结果明确走 `query --full`。
- [ ] 日期条件使用范围比较，字符串日期格式已确认。
- [ ] 指标聚合来自 metrics catalog，未用 `COUNT(*)` 冒充业务量。
- [ ] 大表 JOIN/聚合已通过普通 `EXPLAIN` 门禁，或已停止并升级给技术/DBA。
