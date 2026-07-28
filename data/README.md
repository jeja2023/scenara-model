# 数据目录

此目录只保存轻量索引和划分文件。原始图片、视频、权重和大型数据集应放在 NAS、MinIO、S3 或其他制品存储中。

推荐分层：

```text
datasets/
  raw/
  labeled/
  curated/
  evaluation/
  manifests/
```

Git 中只提交：

- manifest JSONL。
- split 定义。
- 数据集配置。
- 质检报告。

Ultralytics 生产示例使用 `configs/experiments/ultralytics_data.yaml`，部署时需要准备：

```text
data/datasets/person_detection/
  images/{train,val,test}/
  labels/{train,val,test}/
data/manifests/person_detection_{train,val,test}_v1.jsonl
data/model_examples/person_detector_yolov8n_v1.1.0_fp32/
  input_001.jpg
  input_001.expected.json
```

这些路径缺失、manifest 非法或生产指标未达到配置阈值时，流水线会在打包前失败。
