# Scenara Model 1.0.0-dev.3 更新说明

发布日期：2026-09-02

## 变更摘要

- **跨仓契约升级**：锁定并消费 `@scenara/repository-contracts` `1.2.0`（包含 `dataset-version-input`、`deployment-feedback`、`model-package-admission`）。
- **多领域与标注规范校验**：Dataset Version 严格校验 `domain`（`portrait`、`ocr`、`behavior`、`fashion`）及匹配的 `annotation_schema_ids`；ReID 训练数据引用及人像 ReID bundle 适配 `scenara.portrait.surveillance-review.v1` 布控误报复核标注规范。
- **生产适配器接入**：新增 `paddleocr`、`paddlevideo`、`fashion_multihead` 适配器入口；缺少外部真实命令、checkpoint 或实测指标时强制失败关闭（fail-closed），杜绝假成功。
- **模型 Bundle 校验与准入载荷**：新增 `scenara_model.packaging.artifact_bundle` 模块，按 `bundle-manifest.json` 逐文件校验路径防逃逸、文件大小、SHA-256 指纹、媒体类型与模型卡引用，并生成 Core 模型准入载荷（`admission_payload`）。
- **前端工作台扩展**：迁移期管理台与实验表单新增 OCR 文档识别、行为识别、服饰风格识别任务类型及中文状态映射。
- **版本对齐**：Python 包、运行时 `__version__`、前端 `package.json` 与 `package-lock.json` 统一升级为 `1.0.0-dev.3` / `1.0.0.dev3`。

## 验证结果

- Python 单元与集成测试：`109 passed`（含多领域契约、Dataset Version 匹配、适配器 fail-closed 与 bundle 校验测试）。
- 静态类型检查：Pyright `0 errors, 0 warnings, 0 informations`。
- 代码风格检查：Ruff `All checks passed!`。
- 离线交付与契约验收：`scripts/acceptance_check.py --skip-pytest` 全部通过。
- 前端构建与安全审计：TypeScript 编译与 Vite 8 生产构建通过；生产依赖安全审计为 0 vulnerabilities。

## 资格边界

本版本生成的模型准入载荷默认标记为 `production_ready=false`。当前版本完成 OCR、行为识别、服饰风格等多领域接入与契约校验，但真实框架环境的 GPU 训练、真实权重和生产准入确权仍由外部资格流程严格控制。
