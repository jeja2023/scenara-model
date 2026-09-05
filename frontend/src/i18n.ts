export function zhStatus(value?: string | null) {
  const map: Record<string, string> = {
    ok: "正常",
    planned: "计划中",
    packaged: "已打包",
    completed: "已完成",
    failed: "失败",
    queued: "排队中",
    cancelled: "已取消",
    cancellation_requested: "取消中",
    running: "运行中",
    detection: "目标检测",
    classification: "图像分类",
    segmentation: "图像分割",
    reid: "人员重识别",
    ocr: "OCR 文档识别",
    behavior: "行为识别",
    fashion: "服饰风格识别",
    reference: "参考流程"
  };
  return value ? map[value] ?? value : "-";
}

export function zhField(value: string) {
  const map: Record<string, string> = {
    id: "实验编号",
    task: "任务类型",
    dataset: "数据集",
    model: "模型",
    status: "状态",
    package: "模型包",
    image: "图片",
    split: "数据划分",
    source: "来源",
    dataset_version: "数据集版本",
    tags: "标签",
    models: "模型配置",
    decision: "上线判定",
    model_card: "模型卡",
    labels: "标签文件",
    artifact: "模型产物",
    input: "输入",
    output: "输出",
    runtime: "运行时",
    version: "版本",
    precision: "精度",
    type: "类型",
    recommendation: "上线建议",
    reason: "理由",
    required_service_checks: "必需服务检查",
    rollback_target: "回滚目标",
    sha256: "文件哈希",
    name: "名称",
    architecture: "架构",
    format: "格式",
    shape: "尺寸",
    dtype: "数据类型",
    color: "颜色",
    resize: "缩放",
    normalize: "归一化",
    classes: "类别",
    recommended_confidence: "推荐置信度",
    recommended_iou: "推荐重叠阈值",
    metrics: "指标",
    deployment: "部署",
    limitations: "限制",
    min_gpu_memory_mb: "最小显存",
    max_batch_size: "最大批量",
    supports_dynamic_batch: "动态批量"
  };
  return value
    .split(".")
    .map((part) => map[part] ?? part)
    .join(".");
}

export function zhContractKind(value: "models-fragment" | "release-decision") {
  return value === "models-fragment" ? "模型产物交付清单契约" : "发布决策门禁契约";
}

export function zhSplit(value: string) {
  const map: Record<string, string> = {
    train: "训练集",
    val: "验证集",
    test: "测试集",
    regression: "回归集",
    edge: "边界集"
  };
  return map[value] ?? value;
}

export function zhIssue(value: string) {
  const map: Record<string, string> = {
    "package.not_found": "模型包目录不存在",
    "package.model_ambiguous": "模型文件数量不明确",
    "package.model_not_found": "模型文件不存在",
    "package.invalid_model_name": "模型文件命名无效",
    "package.missing_model_card": "缺少模型卡",
    "package.missing_labels": "缺少标签文件",
    "package.empty_labels": "标签文件为空",
    "package.duplicate_labels": "标签重复",
    "package.missing_examples": "缺少样例目录",
    "package.invalid_examples": "样例目录无效",
    "package.missing_example_inputs": "缺少样例图片",
    "package.missing_expected_outputs": "缺少期望输出",
    "package.invalid_expected_json": "期望输出格式无效",
    "package.onnx_check_failed": "模型格式校验失败",
    "package.ort_load_failed": "推理运行时加载失败",
    "model_card.read_error": "模型卡读取失败",
    "model_card.missing_section": "模型卡缺少章节",
    "model_card.invalid_section": "模型卡章节格式无效",
    "model_card.missing_field": "模型卡缺少字段",
    "model_card.invalid_version": "模型版本格式无效",
    "model_card.invalid_format": "模型格式无效",
    "model_card.invalid_precision": "模型精度无效",
    "model_card.invalid_shape": "输入尺寸无效",
    "model_card.invalid_artifact_name": "模型产物命名无效",
    "model_card.version_mismatch": "模型版本不一致",
    "model_card.precision_mismatch": "模型精度不一致",
    "model_card.empty_sha256": "缺少文件哈希",
    "model_card.sha256_mismatch": "模型哈希不一致",
    "manifest.read_error": "数据清单读取失败",
    "manifest.missing_field": "缺少必填字段",
    "manifest.duplicate_image": "图片重复",
    "manifest.invalid_split": "数据划分无效",
    "manifest.invalid_dataset_version": "数据集版本无效",
    "manifest.invalid_tags": "标签格式无效",
    "contract.read_error": "契约读取失败",
    "contract.models_missing": "缺少模型配置",
    "contract.invalid_model_id": "模型编号无效",
    "contract.invalid_model_config": "模型配置格式无效",
    "contract.missing_model_field": "模型配置缺少字段",
    "contract.version_mismatch": "契约版本不一致",
    "contract.precision_mismatch": "契约精度不一致",
    "contract.missing_sidecar_reference": "缺少配套文件引用",
    "contract.decision_missing": "缺少上线判定",
    "contract.missing_decision_field": "上线判定缺少字段",
    "contract.invalid_recommendation": "上线建议无效",
    "contract.invalid_decision_model": "上线模型无效",
    "contract.invalid_decision_list": "上线判定列表无效",
    "contract.rollback_required": "缺少回滚目标"
  };
  return map[value] ?? value;
}

type IssueLike = {
  code: string;
  line?: number | null;
  field?: string | null;
  path?: string | null;
};

function zhLocation(value?: string | null) {
  if (!value) {
    return "";
  }
  if (/[\\/:]/.test(value)) {
    return value;
  }
  return zhField(value);
}

export function zhIssueDetail(issue: IssueLike) {
  const detailMap: Record<string, string> = {
    "manifest.read_error": "无法读取数据清单文件。",
    "manifest.missing_field": "请补齐数据清单中的必填字段。",
    "manifest.duplicate_image": "同一张图片在数据清单中重复出现。",
    "manifest.invalid_split": "数据划分必须使用训练集、验证集、测试集、回归集或边界集。",
    "manifest.invalid_dataset_version": "数据集版本需要使用语义化版本号，例如 1.2.0。",
    "manifest.invalid_tags": "标签字段需要使用数组格式。",
    "contract.read_error": "无法读取交付契约文件。",
    "contract.models_missing": "契约中需要提供至少一项模型配置。",
    "contract.invalid_model_id": "模型编号需要符合统一产物命名规范。",
    "contract.invalid_model_config": "模型配置需要使用对象结构。",
    "contract.missing_model_field": "请补齐模型配置中的必填字段。",
    "contract.version_mismatch": "契约版本需要和模型产物版本一致。",
    "contract.precision_mismatch": "契约精度需要和模型产物精度一致。",
    "contract.missing_sidecar_reference": "请补齐模型卡和标签文件等配套引用。",
    "contract.decision_missing": "契约中需要提供上线判定。",
    "contract.missing_decision_field": "请补齐上线判定中的必填字段。",
    "contract.invalid_recommendation": "上线建议需要使用允许的发布级别。",
    "contract.invalid_decision_model": "上线模型需要符合统一产物命名规范。",
    "contract.invalid_decision_list": "上线理由和服务检查需要使用非空列表。",
    "contract.rollback_required": "灰度或生产发布必须填写回滚目标。"
  };
  const suffix = [
    issue.line ? `第 ${issue.line} 行` : "",
    issue.field ? `字段：${zhField(issue.field)}` : "",
    issue.path ? `位置：${zhLocation(issue.path)}` : ""
  ].filter(Boolean);
  const detail = detailMap[issue.code] ?? "请检查对应文件和字段。";
  return suffix.length ? `${detail}（${suffix.join("，")}）` : detail;
}

export function zhMetricsSource(source?: string | null) {
  if (source === "measured") return "实测评估";
  if (source === "declared") return "自报声明";
  if (source === "baseline") return "基线指标";
  return source || "未声明";
}

export function zhStream(stream?: string | null) {
  if (stream === "stdout") return "标准输出";
  if (stream === "stderr") return "错误输出";
  return stream || "日志输出";
}

export function zhAction(action?: string | null) {
  const map: Record<string, string> = {
    "pipeline.start": "启动流水线",
    "pipeline.cancel": "取消流水线任务",
    "pipeline.retry": "重试流水线任务",
    "package.validate": "校验模型包",
    "package.build": "打包模型交付物",
    "artifact.upload": "上传产物文件",
    "experiment.save": "保存实验记录",
    "manifest.validate": "校验数据清单",
    "contract.validate": "校验交付契约",
    "auth.login": "用户登录",
    "auth.logout": "退出登录"
  };
  return action ? map[action] ?? action : "-";
}
