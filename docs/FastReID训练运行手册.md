# FastReID 训练运行手册

FastReID 运行在独立 Linux GPU 容器中，不能使用管理 API 的 Windows/Python 3.12 虚拟环境。运行时固定为 FastReID 提交 `c9bc3ceb2f7a6438b62fb515ea3df6d1e999e95d`、CUDA 11.6、PyTorch 1.13.1、torchvision 0.14.1 和完整的基础镜像摘要，声明见 `deploy/training/fastreid-environment.lock.json`。

Windows 仅可作为非生产冒烟环境。当前工作站已在 RTX 3070 上通过 FastReID 模型构建与 CUDA embedding 前向验证；独立环境和结果保存于 `.venv-fastreid-smoke`、`third_party/fast-reid` 与 `artifacts/fastreid-windows-smoke.json`，均已排除出 Git。重新执行：

```powershell
.\.venv-fastreid-smoke\Scripts\python.exe scripts/fastreid/windows_smoke.py
```

该冒烟使用 PyTorch 2.5.1 CUDA 12.1 兼容层，不能替代 Linux 运行时、真实数据训练或生产验收。

构建并验证 GPU 运行时：

```powershell
docker compose -f deploy/training/docker-compose.fastreid.yml build
docker compose -f deploy/training/docker-compose.fastreid.yml run --rm fastreid
```

第二条命令必须显示 `cuda.available: true`，否则不得提交训练任务。当前数据契约要求训练 manifest 行含有本地 JPEG 的 `image`、正整数 `person_id`、`camera_id`；固定测试 manifest 还必须含 `reid_role: query|gallery`。转换脚本会拒绝远程 URI、缺失 gallery、没有匹配 gallery 的 query 和每个身份少于四张训练图像的集合。

生产训练由容器内执行：

```powershell
docker compose -f deploy/training/docker-compose.fastreid.yml run --rm fastreid `
  python scripts/run_pipeline.py --config configs/experiments/reid_fastreid_external.yml --package
```

这会先将清单物化为 `artifacts/fastreid-data/person_reid`，再调用 FastReID 训练、ONNX 导出和固定测试集 CMC/mAP 评估。没有 immutable Dataset Version、许可证原件、审批引用及真实样例时，生产打包仍会失败关闭。

不要把 FastReID 容器、数据物化目录或下载的预训练权重提交到 Git。
