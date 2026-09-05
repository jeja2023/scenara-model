import {
  Boxes,
  Database,
  Edit3,
  FlaskConical,
  PlusCircle,
  RotateCw,
  Save,
  Search,
  Tag
} from "lucide-react";
import { useMemo, useState } from "react";
import { errorMessage, saveExperiment } from "../api";
import { Pagination } from "../components/Pagination";
import { StatusBadge } from "../components/StatusBadge";
import { TabBar, type TabItem } from "../components/TabBar";
import { Tooltip } from "../components/Tooltip";
import { zhStatus } from "../i18n";
import type { ExperimentRecord } from "../types";

type ExperimentsProps = {
  experiments: ExperimentRecord[];
  onRefresh: () => void;
};

// 表单存英文码、展示层用 zhStatus 翻译——避免中文显示值污染后端状态词汇表。
const taskOptions = ["detection", "classification", "segmentation", "reid", "ocr", "behavior", "fashion", "reference"] as const;
const statusOptions = ["planned", "running", "completed", "failed", "packaged"] as const;

function statusToTone(status: string) {
  if (status === "completed" || status === "packaged") {
    return "ok";
  }
  if (status === "running" || status === "planned") {
    return "warn";
  }
  return "fail";
}

const experimentTabs: TabItem[] = [
  { key: "list", label: "实验知识库列表", icon: <FlaskConical size={15} /> },
  { key: "edit", label: "登记与编辑实验", icon: <PlusCircle size={15} /> }
];

export function Experiments({ experiments, onRefresh }: ExperimentsProps) {
  const [activeTab, setActiveTab] = useState("list");

  const [record, setRecord] = useState<ExperimentRecord>({
    id: "person_detector_20260603_001",
    task: "detection",
    dataset: "person_detection_dataset_v1.0.0",
    model: "yolov8n",
    status: "planned",
    package: ""
  });
  const [message, setMessage] = useState("");
  const [filterQuery, setFilterQuery] = useState("");
  const [busy, setBusy] = useState(false);

  // 分页状态
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  async function submit() {
    if (!record.id.trim()) {
      setMessage("实验编号不能为空");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      await saveExperiment(record);
      setMessage("实验记录已成功保存入库");
      onRefresh();
      // 保存成功后切回列表查看
      setActiveTab("list");
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  const filteredExperiments = useMemo(() => {
    if (!filterQuery.trim()) {
      return experiments;
    }
    const query = filterQuery.toLowerCase();
    return experiments.filter(
      (item) =>
        item.id.toLowerCase().includes(query) ||
        item.task.toLowerCase().includes(query) ||
        (item.model && item.model.toLowerCase().includes(query)) ||
        (item.dataset && item.dataset.toLowerCase().includes(query))
    );
  }, [experiments, filterQuery]);

  const paginatedExperiments = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    return filteredExperiments.slice(start, start + pageSize);
  }, [filteredExperiments, currentPage, pageSize]);

  return (
    <div className="page-grid">
      {/* Tab 切换栏 */}
      <TabBar
        tabs={experimentTabs.map((t) => (t.key === "list" ? { ...t, badge: experiments.length } : t))}
        activeKey={activeTab}
        onChange={setActiveTab}
      />

      {/* Tab 1: 实验知识库列表（全边框表格、42px行高、截断悬浮、统一26px按钮、分页） */}
      {activeTab === "list" ? (
        <section className="panel">
          <div className="panel-header">
            <div className="panel-header-left">
              <FlaskConical size={18} color="#176b87" />
              <h1>实验知识库全量列表</h1>
              <span className="panel-count-tag">共 {experiments.length} 组</span>
            </div>

            <div className="panel-actions">
              <div className="search-filter-box">
                <Search size={15} color="#8a99ad" />
                <input
                  value={filterQuery}
                  onChange={(event) => {
                    setFilterQuery(event.target.value);
                    setCurrentPage(1);
                  }}
                  placeholder="按编号、任务或模型过滤…"
                />
              </div>
              <button
                type="button"
                className="primary-button"
                onClick={() => {
                  setRecord({
                    id: `exp_${Date.now()}`,
                    task: "detection",
                    dataset: "custom_dataset_v1.0.0",
                    model: "yolov8n",
                    status: "planned",
                    package: ""
                  });
                  setActiveTab("edit");
                }}
                style={{ height: 36, fontSize: 13 }}
              >
                <PlusCircle size={15} />
                <span>新建实验</span>
              </button>
              <button className="icon-button" onClick={onRefresh} aria-label="刷新实验列表">
                <RotateCw size={16} />
              </button>
            </div>
          </div>

          <div className="ui-table-container">
            <table className="ui-table">
              <thead>
                <tr>
                  <th style={{ width: "55px", textAlign: "center" }}>序号</th>
                  <th style={{ width: "22%" }}>实验编号</th>
                  <th style={{ width: "14%" }}>任务类型</th>
                  <th style={{ width: "18%" }}>模型架构</th>
                  <th style={{ width: "18%" }}>关联数据集</th>
                  <th style={{ width: "16%" }}>当前阶段状态</th>
                  <th style={{ width: "80px", textAlign: "center" }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {paginatedExperiments.map((item, index) => {
                  const seqNumber = (currentPage - 1) * pageSize + index + 1;
                  return (
                    <tr key={item.id}>
                      <td style={{ textAlign: "center", color: "#64748b", fontWeight: 500 }}>
                        {seqNumber}
                      </td>
                      <td>
                        <Tooltip content={item.id}>
                          <span className="cell-ellipsis mono" style={{ fontWeight: 600 }}>
                            {item.id}
                          </span>
                        </Tooltip>
                      </td>
                      <td>
                        <span className="badge neutral" style={{ fontSize: 11.5 }}>
                          {zhStatus(item.task)}
                        </span>
                      </td>
                      <td>
                        <Tooltip content={item.model || "-"}>
                          <span className="cell-ellipsis mono">{item.model || "-"}</span>
                        </Tooltip>
                      </td>
                      <td>
                        <Tooltip content={item.dataset || "-"}>
                          <span className="cell-ellipsis">{item.dataset || "-"}</span>
                        </Tooltip>
                      </td>
                      <td>
                        <StatusBadge tone={statusToTone(item.status)} label={zhStatus(item.status)} />
                      </td>
                      <td style={{ textAlign: "center" }}>
                        <Tooltip content="载入并编辑此实验" placement="top">
                          <button
                            type="button"
                            className="table-action-btn primary"
                            onClick={() => {
                              setRecord({
                                id: item.id,
                                task: item.task,
                                dataset: item.dataset ?? "",
                                model: item.model ?? "",
                                status: item.status,
                                package: item.package ?? ""
                              });
                              setMessage(`已载入实验 #${item.id} 进行编辑`);
                              setActiveTab("edit");
                            }}
                          >
                            <Edit3 size={13} />
                            <span>编辑</span>
                          </button>
                        </Tooltip>
                      </td>
                    </tr>
                  );
                })}
                {!paginatedExperiments.length ? (
                  <tr>
                    <td colSpan={7} style={{ textAlign: "center", padding: "30px 12px", color: "#8a99ad" }}>
                      {filterQuery ? "未检索到匹配的实验记录" : "当前暂无实验台账记录"}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>

            {filteredExperiments.length > 0 ? (
              <Pagination
                currentPage={currentPage}
                pageSize={pageSize}
                total={filteredExperiments.length}
                onPageChange={setCurrentPage}
                onPageSizeChange={setPageSize}
              />
            ) : null}
          </div>
        </section>
      ) : (
        /* Tab 2: 登记与编辑实验表单 */
        <section className="panel" style={{ maxWidth: "900px", margin: "0 auto", width: "100%" }}>
          <div className="panel-header">
            <div className="panel-header-left">
              <FlaskConical size={18} color="#176b87" />
              <h1>登记与编辑实验台账</h1>
            </div>
            <span className="panel-count-tag">参数元数据</span>
          </div>

          <div style={{ display: "grid", gap: 18 }}>
            {/* 分组 1: 基本标识 */}
            <div>
              <span style={{ fontSize: 13, fontWeight: 600, color: "#475569", display: "flex", alignItems: "center", gap: 6 }}>
                <Tag size={14} /> 基本标识信息
              </span>
              <div className="form-grid" style={{ marginTop: 8, gap: 14 }}>
                <label>
                  <span>实验唯一编号 (ID)</span>
                  <input
                    value={record.id}
                    onChange={(event) => setRecord({ ...record, id: event.target.value })}
                    placeholder="例如: person_detector_20260603_001"
                  />
                </label>
                <label>
                  <span>视觉任务类型</span>
                  <select
                    value={record.task}
                    onChange={(event) => setRecord({ ...record, task: event.target.value })}
                  >
                    {taskOptions.map((option) => (
                      <option key={option} value={option}>
                        {zhStatus(option)} ({option})
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            </div>

            {/* 分组 2: 研发资产 */}
            <div>
              <span style={{ fontSize: 13, fontWeight: 600, color: "#475569", display: "flex", alignItems: "center", gap: 6 }}>
                <Database size={14} /> 训练研发资产
              </span>
              <div className="form-grid" style={{ marginTop: 8, gap: 14 }}>
                <label>
                  <span>关联训练数据集</span>
                  <input
                    value={record.dataset}
                    onChange={(event) => setRecord({ ...record, dataset: event.target.value })}
                    placeholder="例如: person_detection_dataset_v1.0.0"
                  />
                </label>
                <label>
                  <span>基础模型算法架构</span>
                  <input
                    value={record.model}
                    onChange={(event) => setRecord({ ...record, model: event.target.value })}
                    placeholder="例如: yolov8n / resnet50"
                  />
                </label>
              </div>
            </div>

            {/* 分组 3: 状态与交付 */}
            <div>
              <span style={{ fontSize: 13, fontWeight: 600, color: "#475569", display: "flex", alignItems: "center", gap: 6 }}>
                <Boxes size={14} /> 阶段状态与交付关联
              </span>
              <div className="form-grid" style={{ marginTop: 8, gap: 14 }}>
                <label>
                  <span>当前阶段状态</span>
                  <select
                    value={record.status}
                    onChange={(event) => setRecord({ ...record, status: event.target.value })}
                  >
                    {statusOptions.map((option) => (
                      <option key={option} value={option}>
                        {zhStatus(option)}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>关联交付模型包 (可选)</span>
                  <input
                    value={record.package ?? ""}
                    onChange={(event) => setRecord({ ...record, package: event.target.value })}
                    placeholder="例如: models/person_detector_v1.zip"
                  />
                </label>
              </div>
            </div>
          </div>

          <div className="form-actions-bar">
            <div>
              {message ? (
                <span className="inline-message" style={{ fontWeight: 500 }}>
                  {message}
                </span>
              ) : (
                <span style={{ fontSize: 12.5, color: "#8a99ad" }}>请核对实验编号与任务参数后保存</span>
              )}
            </div>
            <div style={{ display: "flex", gap: 10 }}>
              <button
                type="button"
                className="secondary-button"
                onClick={() => setActiveTab("list")}
              >
                返回列表
              </button>
              <button className="primary-button" onClick={submit} disabled={busy}>
                <Save size={16} />
                <span>{busy ? "保存中…" : "保存实验记录"}</span>
              </button>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
