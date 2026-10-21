---
name: lx_shujuku
description: "使用自己的公司账号查询可见数据，保留完整取数证明和本地结果。"
---

# 公司数据库

只使用公司 dataReporting。先明确问题、日期、品牌城市筛选和指标口径，查本地 schema/metrics_catalog，编写只读、日期有界、稳定 ORDER BY 的 SQL。预览：

`python tools/run_skill.py database --sql "<SQL>" --question "<问题>"`

展示 SQL、口径、数据范围、行数上限和新建结果目录。执行时附 `--confirmed --expected-preview-hash <HASH>`。完整模式默认最多 10000 行、20 页，超限缩小范围，不能把 Top N 当完整结果。输出到 `workspace/04数据分析/数据库查询/YYYYMMDD_HHMMSS_query/`。不调用 RDS、不重建伙伴权限、不写公司数据。用户不需要提供主体码表。

## 执行契约

### 输入

业务问题、明确时间范围/筛选；本机公司账号。

### 输出

只读查询证据包 query-run.json；含 SQL、计数、schema/指标版本、hash 和完整性警告。

### 验收

预览零服务调用和零文件；确认 hash 匹配后查询。取数完整证明通过且结果独立读回；无结果、缺权限、超限分别说明。

