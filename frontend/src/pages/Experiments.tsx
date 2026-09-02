import { Save } from "lucide-react";
import { useState } from "react";
import { errorMessage, saveExperiment } from "../api";
import type { ExperimentRecord } from "../types";
import { zhStatus } from "../i18n";

type ExperimentsProps = {
  experiments: ExperimentRecord[];
  onRefresh: () => void;
};

// 表单存英文码、展示层用 zhStatus 翻译——避免中文显示值污染后端状态词汇表。
const taskOptions = ["detection", "classification", "segmentation", "reid", "ocr", "behavior", "fashion", "reference"] as const;
const statusOptions = ["planned", "running", "completed", "failed", "packaged"] as const;

export function Experiments({ experiments, onRefresh }: ExperimentsProps) {
  const [record, setRecord] = useState<ExperimentRecord>({
    id: "person_detector_20260603_001",
    task: "detection",
    dataset: "person_detection_dataset_v1.0.0",
    model: "yolov8n",
    status: "planned",
    package: ""
  });
  const [message, setMessage] = useState("");

  async function submit() {
    if (!record.id.trim()) {
      setMessage("实验编号不能为空");
      return;
    }
    setMessage("");
    try {
      await saveExperiment(record);
      setMessage("已保存");
      onRefresh();
    } catch (error) {
      setMessage(errorMessage(error));
    }
  }

  return (
    <div className="page-grid">
      <section className="panel">
        <div className="panel-header">
          <h1>实验记录</h1>
          <button className="primary-button" onClick={submit} title="保存">
            <Save size={17} />
            <span>保存</span>
          </button>
        </div>
        <div className="form-grid">
          <label>
            <span>实验编号</span>
            <input value={record.id} onChange={(event) => setRecord({ ...record, id: event.target.value })} />
          </label>
          <label>
            <span>任务类型</span>
            <select value={record.task} onChange={(event) => setRecord({ ...record, task: event.target.value })}>
              {taskOptions.map((option) => (
                <option key={option} value={option}>
                  {zhStatus(option)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>数据集</span>
            <input value={record.dataset} onChange={(event) => setRecord({ ...record, dataset: event.target.value })} />
          </label>
          <label>
            <span>模型</span>
            <input value={record.model} onChange={(event) => setRecord({ ...record, model: event.target.value })} />
          </label>
          <label>
            <span>状态</span>
            <select value={record.status} onChange={(event) => setRecord({ ...record, status: event.target.value })}>
              {statusOptions.map((option) => (
                <option key={option} value={option}>
                  {zhStatus(option)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>模型包</span>
            <input value={record.package ?? ""} onChange={(event) => setRecord({ ...record, package: event.target.value })} placeholder="可选" />
          </label>
        </div>
        {message ? <p className="inline-message">{message}</p> : null}
      </section>

      <section className="panel">
        <h1>实验列表</h1>
        <div className="table">
          <div className="table-row table-head">
            <span>编号</span>
            <span>任务</span>
            <span>状态</span>
          </div>
          {experiments.map((item) => (
            <div className="table-row" key={item.id}>
              <span className="mono">{item.id}</span>
              <span>{zhStatus(item.task)}</span>
              <span>{zhStatus(item.status)}</span>
            </div>
          ))}
          {!experiments.length ? <div className="empty-row">暂无实验</div> : null}
        </div>
      </section>
    </div>
  );
}
