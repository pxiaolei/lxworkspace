---
name: lx-zuotu
description: 从活动 TXT 或 YAML 生成司机端首页、开屏、透明弹窗、侧边栏和权益中心宣传图。按 check、dry-run、confirmed sample/full 执行，仅用现有 KIE；模型绘制全部文字和按钮，脚本严格处理尺寸、字节上限和格式，并绑定最终图片哈希逐张视觉验收。
---

# lx-zuotu

伙伴入口与本文档由lxworkspace维护；生成代码、规格和风格参考素材按source-lock记录公共真源。

先读 [运行契约](references/runtime.md)。本 Skill 只处理端内宣传图；所有文字、按钮、规则小字由模型生成，不补画文字、Logo 或二维码。

## 执行顺序

1. 读取真实需求文件；仅需求文件提供活动事实。样图只能提供风格，不得复制旧金额、日期或承诺。
2. 在项目根目录运行 `python tools/run_skill.py init`、`python tools/run_skill.py zuotu --check`。缺依赖时先报告；安装必须由lx-init绑定当前preview_hash并显式确认，不使用全局pip，不建立第二个虚拟环境。
3. 运行 `python tools/run_skill.py zuotu --dry-run --file <需求文件>`，展示每张图的类型、尺寸、格式、严格字节上限和全部文案。
4. 用户确认后运行 `python tools/run_skill.py zuotu --sample-only --confirmed --file <需求文件>`。默认是所选范围的一张竖版和一张横条。
5. 必须实际读取最终候选图片，逐项比对文案、金额、日期、按钮、裁切、透明边缘和主体一致性；按 runtime 的 review JSON 契约记录，使用 `--review-file ... --confirmed` 写回核验。未看图、无法识别或缺字段只能待核验/失败，禁止伪造通过。
6. 展示样张和结果，得到用户“确认批量”后才运行 `python tools/run_skill.py zuotu --confirmed --file <需求文件>`。通过视觉核验的样张作为锁定母版。
7. 对每个最终候选执行同样视觉核验；失败后仅在当前确认范围内由同一 KIE 重试，单个目标首次加最多两次重试。最后展示汇总表和 manifest 路径，区分物理通过、待视觉核验、最终通过、失败。

以上命令从项目根目录运行，输入路径用根目录相对路径或绝对路径。Windows可用 `tools\lx.cmd zuotu ...`；Skill内同名sh/cmd只转发统一入口。无 `--confirmed` 不上传参考图、不提交 KIE、不下载、不写输出；check/dry-run 不联网、不建文件，包括日志和 Python 缓存。

## 目录约定

- `specs.yaml`：类型、尺寸、格式、字节上限、生成比例和样张顺序的唯一真源。
- `scripts/`：输入、KIE 适配、编排、后处理、独立验证；不改动 lx-haibao。
- `assets/references/{newyear69,mayday}/`：用户授权样图，英文稳定命名；GIF 仅用静态帧，原 ZIP 不改动。`index.yaml` 记录来源和哈希。
- `assets/runtime/requirements.txt`：端内图依赖声明，统一由lx-init装入既有的lx-haibao/.venv；该Skill不另建环境。
- `tests/fixtures/`：五一样图的明确测试需求，历史日期不补年份；不代表本次正式运营活动。
- `workspace/08端内宣传图/<活动名>/`：输入副本、样张、正式图、临时尝试、日志、manifest；业务产物不进入 Git，不自动清理。
- 同名正式图不覆盖；失败图保留在临时图，每次尝试有唯一文件名。确认样张不授权改变需求或规格。

## 执行契约

### 输入

- 文档化的 TXT/YAML 活动需求，CLI 可覆盖类型并筛选尺寸。
- 本工作区 `lx_haibao.image_api.kie` 配置；统一入口隔离其他项目环境变量，只读取、不修改、不输出密钥。
- 用户对样张、批量以及具体重试范围的确认。选择 Skill 不等于付费授权。

### 输出

- 严格匹配规格的 JPG 或透明 PNG 候选、按 SHA256 绑定的核验结果、逐图汇总和 `manifest.json`。
- 只有物理校验和真实视觉核验均通过的文件才进入正式输出；否则显示待核验或失败及原因。

### 验收

- 配置中的全套共 5 类 8 张；popup 两张；指定尺寸不扩大范围。
- 精确尺寸、真实格式、严格低于上限、元数据清理、弹窗真实透明；JPG 不低于质量 60，不缩小交付尺寸来达标。
- 最终文字、金额、日期和按钮与需求一致，主体/文字不被裁切，各尺寸主体配色一致；缺核验结果不算通过。
- check/dry-run/未确认路径零联网零写入；本地模拟测试不能作为真实 KIE 验收。
