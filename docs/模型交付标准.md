# 模型交付标准

## 必交付物

每个候选模型版本必须提供：

- ONNX 模型文件。
- 模型卡 `.model-card.yml`。
- labels 文件 `.labels.txt`。
- 样例目录 `.examples/`。
- 至少一份期望输出 JSON。
- sha256。
- 评估报告。
- 导出日志。
- `models.yml` 建议片段。
- 与上一线上版本的对比结论。

## 验收门禁

- 模型命名符合 `<task>_<architecture>_v<semver>_<precision>.onnx`。
- 模型卡包含输入、输出、指标、部署和限制。
- `model.sha256` 与 ONNX 文件一致。
- labels 非空且无重复。
- 样例图片和 expected JSON 存在。
- 严格模式下 ONNX 可以通过 `onnx.checker` 和 ONNX Runtime CPU 加载。
- 指标不低于当前线上版本，或有业务确认的例外说明。

## 自动化命令

```powershell
python scripts/train.py --config configs/experiments/reference_identity.yml
python scripts/export_onnx.py --config configs/experiments/reference_identity.yml
python scripts/evaluate.py --config configs/experiments/reference_identity.yml
python scripts/validate_model_package.py shared-models/<project> --model-id <artifact>.onnx --strict-hash --strict-onnx
python scripts/validate_contract.py models-fragment configs/export/models.fragment.template.yml
python scripts/validate_contract.py release-decision configs/export/release-decision.template.yml
python scripts/acceptance_check.py
```

`models.yml` 建议片段必须保证：

- 每个模型 ID 指向标准 ONNX 文件。
- `version` 与文件名版本一致。
- `precision` 与文件名精度一致。
- `artifact.model_card` 和 `artifact.labels` 明确存在。

上线判定必须保证：

- `recommendation` 只能是 `reject`、`lab_only`、`gray_release`、`production`。
- `gray_release` 和 `production` 必须提供 `rollback_target`。
- `reason` 和 `required_service_checks` 必须是非空列表。

## 上线结论

上线判定只能是：

- `reject`
- `lab_only`
- `gray_release`
- `production`

缺少模型卡、labels、sha256、样例或对比报告时，不允许直接进入生产。
