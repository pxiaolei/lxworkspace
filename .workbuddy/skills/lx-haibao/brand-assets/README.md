# 品牌资料

官方预设的十品牌配置、Logo 和二维码已随仓库交付，精确文件与 SHA256 见根目录 `contracts/partner-brand-presets.json`。Logo 按配置可能为 PNG 或 JPG；不要擅自改名。用户无需额外添加，后续官方品牌通过“更新 Skill”接收。

品牌展示名、别名、二维码期望值和模板 ID 保存在对应 YAML，模板图在 `assets/templates/`。不提供猜测地址、空二维码或自动选其他品牌的兜底，生成时仍校验素材和二维码。

本机自定义品牌使用独立 ID，放入 `brands/<brand_id>.yaml` 与本目录 `<brand_id>/`，继续忽略、不进 Git。不要手改官方文件；官方新增文件与本机同名资料冲突时更新停止，联系我方处理，不覆盖或删除。
