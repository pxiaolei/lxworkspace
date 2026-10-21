# lx-zuotu 运行契约

## 环境和权限

Python >=3.10；依赖为requests、Pillow、PyYAML，测试使用unittest。伙伴使用项目根tools/run_skill.py，复用现有lx-haibao/.venv；所有入口禁用字节码，不为本Skill建立第二个环境。检查不自动修复环境。

初始化先运行 `python tools/run_skill.py init`，展示环境与三份依赖文件hash；确认后运行 `python tools/run_skill.py init --install --confirmed --expected-preview-hash <HASH>`。仅在既有项目虚拟环境安装，端内图与海报复用同一份KIE配置，不新增Key或全局依赖。Skill内check_runtime入口只委派同一个lx-init；缺hash或旧hash拒绝安装。普通检查不联网、不创建环境或业务文件。

KIE 复用相邻 `lx-haibao/scripts/image2_client.py` 的 `KIEProvider` 上传、轮询、下载实现，以及 `lxx_share` 的统一配置读取；因此两个 Skill 及 lxx_share 必须同时存在。仅实例化 KIE，不调用带多 provider 回退的 `generate_image`。从本工作区配置读取 `lx_haibao.image_api.kie`，统一入口不继承其他工作区KIE变量，不修改真实配置，不输出值，不运行 provider health check。

适配已核对的 GPT Image 2 图生图协议：参考图 `input_urls`、生成比例 `aspect_ratio`、显式 `resolution`；透明弹窗在 1K 使用 `background=transparent`，其他 1K 图使用 opaque；不转发旧 callback，以免触发额外外部通知。3:1 横条使用 1K；竖版开屏使用 2K。实际像素和清晰度仍须真实验收，不把请求分辨率当成成品证据。

官方依据（2026-10-20 只读核对，未提交任务）：[图生图 API](https://docs.kie.ai/market/gpt/gpt-image-2-image-to-image)、[GPT Image 2 参数限制](https://kie.ai/gpt-image-2)。后者明确 2K/4K 不支持部分比例，背景选项仅支持 1K。规格中配置生成比例，后处理才决定精确像素。

## 输入格式

TXT 和 YAML 都采用确定性字段映射语法；不使用模型解析输入，dry-run 因此完全离线。任意自然语言散文若无法映射，应先由执行助手在聊天中整理并让用户核对，不能默默猜字段。英文/中文字段对照：

| 英文 | 中文 | 说明 |
| --- | --- | --- |
| brand | 品牌 | 必填，作为品牌语境，不自动增加可见 Logo 或文字 |
| activity_name | 活动名 | 必填，用于目录和文件名，不能含路径字符 |
| reference_set | 参考风格 | mayday（默认）或 newyear69；仅决定风格 |
| types | 图片类型 | all 或类型 key 列表/逗号分隔文本 |
| subtitle | 副标题 | 可选文本或文本列表 |
| title | 标题 | 可选文本或文本列表 |
| selling_points | 卖点 | 可选文本或列表 |
| amounts | 金额 | 完整展示文本或列表；纯数字必须加引号 |
| dates | 日期 | 原文文本或列表，不补年份 |
| rules | 规则小字 | 原文文本或列表 |
| button | 按钮文案 | 可选，仅允许对应版式展示 |
| type_text | 分类型文案 | 按类型 key 明确覆盖上述展示字段 |

可直接使用 `tests/fixtures/mayday_activity.yaml` 或同名 TXT 查看完整格式。它们逐项转录历史五一样图，用于开发验收，不表示现行活动有效。横条短标题由输入明确提供，不自动缩写；缺字段不显示，某一规格没有任何展示文案则报错。

## CLI 和确认流程

从项目根目录执行；输入路径使用根目录相对路径或绝对路径。Windows可将 `python tools/run_skill.py` 换成 `tools\lx.cmd`。Skill内包装入口也转发该统一入口，不直接安装或读私人配置。

```sh
python tools/run_skill.py zuotu --check
python tools/run_skill.py zuotu --dry-run --file .workbuddy/skills/lx-zuotu/tests/fixtures/mayday_activity.yaml --types all
python tools/run_skill.py zuotu --dry-run --file .workbuddy/skills/lx-zuotu/tests/fixtures/mayday_activity.yaml --types popup,splash
python tools/run_skill.py zuotu --dry-run --file .workbuddy/skills/lx-zuotu/tests/fixtures/mayday_activity.yaml --sizes 884x1178
python tools/run_skill.py zuotu --sample-only --confirmed --file .workbuddy/skills/lx-zuotu/tests/fixtures/mayday_activity.yaml --types all
```

`--types` 优先于输入的 types，未传时使用输入或 all。每类默认全部尺寸，`--sizes` 与类型取交集；未知值或空交集报错。后续样张、核验、批量命令必须使用相同需求文件和筛选范围。新增尺寸只改 specs.yaml，不改脚本；新增类型需明确 family、layout、展示字段及参考图。

样张从所选范围各取一张 horizontal/vertical，最多两张；scope 只有弹窗则样张一张，最终两张。默认 full scope 的样张是首页横条和 1242×2208 开屏。已生成活动母版始终排在历史风格图之前；其余图使用同 family 母版。提示词给出中心裁切安全区，最终还必须目视验证原图和压缩候选，切到文字、按钮或主体就判失败并重新生成，不能补画修补。

样张生成只产生待视觉核验候选。核验通过并向用户展示样张后，用户确认批量才执行：

```sh
python tools/run_skill.py zuotu --confirmed --file .workbuddy/skills/lx-zuotu/tests/fixtures/mayday_activity.yaml --types all
```

通过的两张样张按相同最终字节复用到正式规格，不额外重复付费生成。批量只生成其余目标。批量前打印目标张数；manifest 另外记录提交尝试数，张数不等于 API 请求数。候选等待核验期间重复命令不重复生图；发现视觉失败后可在同一已确认范围内再次执行相同生成命令进行重试。

每目标最多3次生成（首次+2次重试），已用次数保存在 manifest。物理失败自动继续，视觉失败由执行助手如实写回再重试；次数用尽停止。KIE 轮询沿用初等30秒、每30秒一次、最多40次的慢任务配置。提交前后保存状态；有 task ID 的未决任务只恢复轮询，不新建付费任务；未知提交且无 task ID 则 blocked，停止并报告，先由用户在官方后台确认，不猜测失败、不自动重提。原始 provider 异常及签名下载 URL 不写日志/manifest。

confirmed 生成和核验写回取得同一活动的跨进程锁，锁内才读取和更新 manifest；第二个进程立即拒绝，避免重复提交和丢失次数。`.activity.lock` 是持久锁载体，结束时只释放系统锁，不自动删除文件；check/dry-run 不触碰它。中断后优先重新验证已有候选或原图，不因“候选已存在”重新付费；已复制成功但账本未更新时，重新核验可接纳哈希完全相同的已有文件而不改写，不同内容仍拒绝。

`--confirmed` 是脚本闸门，不代表本次开发获得真实付费授权；执行助手必须先核对当前用户确认。真实验收单独授权。本轮实施和所有自动测试不得调用真实 KIE。

## 视觉核验写回

使用当前能看图的执行助手读取每个 `临时图/.../attempt-N.<ext>` 最终文件，同时对照原图、母版和需求文件。逐字核对标题、卖点、金额、日期、规则、按钮；检查新增文案、清晰度、裁切、形状、透明边缘和跨尺寸主体配色。不能通过复制 manifest 中的期望文字伪造“实际观察”。模型标识不可得时如实说明，不能声称使用了某个具体模型。视觉能力不可用时保持 pending_review，报告缺口，不切换到额外付费服务。

在同一活动目录的 `处理日志/` 保存 review JSON。格式如下（下面是未通过的结构示意，所有 observed_text 和判断必须由实际看图获得）：

```json
{
  "plan_hash": "从本次manifest读取",
  "images": [{
    "target_id": "home_banner:690x160",
    "image_sha256": "从实际候选文件核对",
    "viewed": false,
    "reviewer": "实际核验者或可核实的模型标识",
    "observed_text": {},
    "unexpected_text": [],
    "checks": {
      "text_exact": false,
      "amounts_dates_exact": false,
      "no_extra_copy": false,
      "no_crop": false,
      "subject_consistent": false,
      "layout_correct": false,
      "transparency_correct": false
    },
    "notes": "填写实际观察、对照的母版及具体问题，不能空泛声明全部正确"
  }]
}
```

`observed_text` 的 key、列表顺序和文本与该图的展示清单逐项比较；换行按输入列表组织，金额/日期不得改动。`unexpected_text` 必须列出实际额外文字。JPEG 的 transparency_correct 表示正确采用满版不透明；弹窗还要检查真实透明外沿。所有检查只能为实际核实的布尔 true。

```sh
python tools/run_skill.py zuotu --review-file <本次review.json> --confirmed --file <同一需求文件> --types all
```

该模式只核验/写本地结果，绝不调用 KIE。程序重新读取最终字节，验证 SHA256 和物理规格，再接受视觉核验。只有全部通过才复制到 `样张/` 或 `产出图/`；缺结果不算通过，哈希变化不得沿用旧核验，已交付文件和记录不覆盖。

## 输出、压缩及状态

输出固定为 `workspace/08端内宣传图/<活动名>/`；子目录责任见该区域 README 和 SKILL.md。需求文件留存到 `输入/`，所有尝试保存在 `临时图/`，失败文件不进入正式输出。`manifest.json` 保存当前状态，日志只记录非敏感阶段和计数，不保存 Key、完整 HTTP 响应、Authorization、上传/下载 URL。

文件名为 `<规格中文名>-<宽>x<高>-<活动名>.<扩展名>`。精确尺寸、格式和严格字节上限取自 specs.yaml；JPEG 从95逐步下降到60，仍超限失败，不缩小尺寸。PNG 使用保透明的调色板量化及无损编码优化，量化后仍须视觉检查色彩和文字清晰度。EXIF、XMP 等元数据不带入交付文件。

状态包括 not_generated、pending_task、pending_review、passed_sample、passed、failed_validation、failed_review、exhausted、blocked。只有 passed/passed_sample 算对应阶段通过。CLI 0 表示阶段完成，3 表示有待核验/失败等未完成项，2 表示输入或确认被拒绝，1 表示运行异常。最终回复展示逐图汇总表、manifest 路径和真实未验项。

## 本地验证

```sh
python -B tools/run_tests.py
```

Windows使用同一项目Python环境。测试 provider 为 synthetic-offline，核验结果是显式测试夹具；即使模拟8张全部通过，也不证明 KIE 中文绘制、透明能力、跨比例主体一致性或最终商业可用。Windows .cmd 的静态检查同样不代表实机执行通过。
