import {
  Activity,
  AlertCircle,
  Boxes,
  Clock,
  Download,
  FileCode,
  FileSearch,
  Play,
  PlaySquare,
  RotateCcw,
  Search,
  Sliders,
  Terminal,
  UploadCloud,
  XCircle
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  analyzeErrors,
  artifactDownloadUrl,
  cancelPipelineJob,
  createPackage,
  downloadArtifact,
  errorMessage,
  getPipelineJob,
  listAdapters,
  listAuditEvents,
  listPipelineJobs,
  retryPipelineJob,
  runPipeline,
  uploadArtifact
} from "../api";
import { Pagination } from "../components/Pagination";
import { StatusBadge } from "../components/StatusBadge";
import { TabBar, type TabItem } from "../components/TabBar";
import { Tooltip } from "../components/Tooltip";
import { zhAction, zhArtifactKind, zhLogDetail, zhLogMessage, logMessageTone, zhMetricsSource, zhStatus, zhStream } from "../i18n";
import type { AdapterInfo, AuditEvent, ErrorAnalysis, PipelineArtifact, PipelineJobLog, PipelineJobRecord, PipelineRunRecord } from "../types";
import { formatBeijingTime } from "../utils/date";

const configOptions = [
  { label: "YOLO 目标检测基线 (detection_yolo_baseline.yml)", value: "configs/experiments/detection_yolo_baseline.yml" },
  { label: "ReID 行人重识别基线 (reid_baseline.yml)", value: "configs/experiments/reid_baseline.yml" },
  { label: "图像分类基线 (classification_baseline.yml)", value: "configs/experiments/classification_baseline.yml" },
  { label: "语义分割基线 (segmentation_baseline.yml)", value: "configs/experiments/segmentation_baseline.yml" }
];

const terminalStatuses = new Set(["completed", "failed", "cancelled"]);
const cancellableStatuses = new Set(["queued", "running"]);

type PipelineProps = {
  runs: PipelineRunRecord[];
  onRefresh: () => void;
};

function metricsText(run?: PipelineRunRecord) {
  const metrics = run?.report?.evaluation?.metrics;
  if (!metrics) {
    return "-";
  }
  return Object.entries(metrics)
    .map(([key, value]) => `${key}:${value}`)
    .join(" / ");
}

type StatusTone = "ok" | "warn" | "neutral" | "fail";

function jobStatusTone(status: string): StatusTone {
  if (status === "completed") {
    return "ok";
  }
  if (status === "cancelled") {
    return "neutral";
  }
  if (status === "cancellation_requested" || status === "running" || status === "queued") {
    return "warn";
  }
  return "fail";
}

function pipelineStageLabel(value?: string | null) {
  const map: Record<string, string> = {
    training: "训练",
    export: "导出",
    evaluation: "评估",
    package: "打包"
  };
  return value ? map[value] ?? value : "-";
}

function cancellationReasonLabel(value?: string | null) {
  const map: Record<string, string> = {
    "Cancellation requested before training started": "在训练开始前已请求取消",
    "Cancellation requested after training": "训练完成后已请求取消",
    "Cancellation requested after export": "导出完成后已请求取消",
    "Cancellation requested before package creation": "在打包开始前已请求取消",
    "Training was cancelled": "训练已取消",
    "Export was cancelled": "导出已取消",
    "Evaluation was cancelled": "评估已取消"
  };
  return value ? map[value] ?? value : "-";
}

function jobCancellationNote(job: PipelineJobRecord) {
  if (job.status === "cancellation_requested") {
    return "取消请求已提交，当前任务正在停止。";
  }
  if (job.status === "cancelled") {
    const stage = job.result?.cancelled_stage ? `，终止于${pipelineStageLabel(job.result.cancelled_stage)}阶段` : "";
    return `任务已取消${stage}。`;
  }
  return "";
}

function humanSize(size?: number | null) {
  if (!size && size !== 0) {
    return "-";
  }
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function logKey(log: PipelineJobLog) {
  return `${log.id}-${log.stream}-${log.created_at}`;
}

const pipelineTabs: TabItem[] = [
  { key: "tasks", label: "任务调度与监视器", icon: <Clock size={15} /> },
  { key: "launch", label: "启动流水线与产物", icon: <PlaySquare size={15} /> },
  { key: "history", label: "运行历史与指标", icon: <Activity size={15} /> },
  { key: "audit", label: "误差分析与审计日志", icon: <FileSearch size={15} /> }
];

export function Pipeline({ runs, onRefresh }: PipelineProps) {
  const [activeTab, setActiveTab] = useState("tasks");

  const [configPath, setConfigPath] = useState(configOptions[0].value);
  const [withPackage, setWithPackage] = useState(true);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [adapters, setAdapters] = useState<AdapterInfo[]>([]);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [jobs, setJobs] = useState<PipelineJobRecord[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [selectedJob, setSelectedJob] = useState<PipelineJobRecord | null>(null);
  const [errorPath, setErrorPath] = useState("data/manifests/example_train_v1.jsonl");
  const [analysis, setAnalysis] = useState<ErrorAnalysis | null>(null);

  // 分页状态
  const [jobPage, setJobPage] = useState(1);
  const [jobPageSize, setJobPageSize] = useState(10);

  const [runPage, setRunPage] = useState(1);
  const [runPageSize, setRunPageSize] = useState(10);

  const [auditPage, setAuditPage] = useState(1);
  const [auditPageSize, setAuditPageSize] = useState(10);

  const jobDetailRequestRef = useRef(0);
  const latest = runs[0];
  const hasActiveJob = useMemo(() => jobs.some((job) => !terminalStatuses.has(job.status)), [jobs]);

  async function refreshJobs() {
    try {
      const response = await listPipelineJobs();
      setJobs(response.jobs);
    } catch (error) {
      setMessage(errorMessage(error));
    }
  }

  useEffect(() => {
    void listAdapters()
      .then((response) => setAdapters(response.adapters))
      .catch(() => setAdapters([]));
    void listAuditEvents()
      .then((response) => setEvents(response.events))
      .catch(() => setEvents([]));
    void refreshJobs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedJobId && jobs[0]) {
      setSelectedJobId(jobs[0].id);
    }
  }, [jobs, selectedJobId]);

  useEffect(() => {
    if (!selectedJobId) {
      setSelectedJob(null);
      return;
    }
    const requestId = ++jobDetailRequestRef.current;
    void getPipelineJob(selectedJobId)
      .then((response) => {
        if (jobDetailRequestRef.current === requestId) {
          setSelectedJob(response.job);
        }
      })
      .catch(() => {
        if (jobDetailRequestRef.current === requestId) {
          setSelectedJob(null);
        }
      });
  }, [selectedJobId, jobs]);

  useEffect(() => {
    if (!hasActiveJob) {
      return;
    }
    const timer = window.setInterval(() => {
      void refreshJobs();
    }, 1500);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasActiveJob]);

  const prevActiveRef = useRef(hasActiveJob);
  useEffect(() => {
    if (prevActiveRef.current && !hasActiveJob) {
      onRefresh();
    }
    prevActiveRef.current = hasActiveJob;
  }, [hasActiveJob, onRefresh]);

  async function startPipeline() {
    setBusy(true);
    setMessage("正在提交调度流水线…");
    try {
      const response = await runPipeline({ config_path: configPath, package: withPackage, async_run: true });
      if (response.job) {
        setSelectedJobId(response.job.id);
        setMessage(`流水线任务 #${response.job.id} 已进入执行队列`);
        setActiveTab("tasks");
      } else {
        setMessage("调度提交完成");
      }
      await refreshJobs();
      onRefresh();
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function buildPackage() {
    setBusy(true);
    setMessage("正在打包生成交付物…");
    try {
      await createPackage({ config_path: configPath });
      setMessage("模型交付包已顺利生成");
      onRefresh();
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function submitUpload(file?: File | null) {
    if (!file) {
      return;
    }
    setBusy(true);
    setMessage("正在上传产物文件…");
    try {
      await uploadArtifact(file);
      setMessage("产物文件上传成功");
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function inspectErrors() {
    try {
      const response = await analyzeErrors(errorPath);
      setAnalysis(response.analysis);
    } catch (error) {
      setMessage(errorMessage(error));
    }
  }

  async function cancelJob(jobId: number) {
    try {
      await cancelPipelineJob(jobId);
      setSelectedJobId(jobId);
      await refreshJobs();
    } catch (error) {
      setMessage(errorMessage(error));
    }
  }

  async function retryJob(jobId: number) {
    try {
      const response = await retryPipelineJob(jobId);
      setSelectedJobId(response.job.id);
      await refreshJobs();
    } catch (error) {
      setMessage(errorMessage(error));
    }
  }

  const selectedJobCancellationNote = selectedJob ? jobCancellationNote(selectedJob) : "";

  // 分页切片计算
  const paginatedJobs = useMemo(() => {
    const start = (jobPage - 1) * jobPageSize;
    return jobs.slice(start, start + jobPageSize);
  }, [jobs, jobPage, jobPageSize]);

  const paginatedRuns = useMemo(() => {
    const start = (runPage - 1) * runPageSize;
    return runs.slice(start, start + runPageSize);
  }, [runs, runPage, runPageSize]);

  const paginatedEvents = useMemo(() => {
    const start = (auditPage - 1) * auditPageSize;
    return events.slice(start, start + auditPageSize);
  }, [events, auditPage, auditPageSize]);

  return (
    <div className="page-grid">
      {/* 选项卡栏 */}
      <TabBar
        tabs={pipelineTabs.map((t) => (t.key === "tasks" ? { ...t, badge: jobs.length } : t))}
        activeKey={activeTab}
        onChange={setActiveTab}
      />

      {/* Tab 1: 任务调度与监视器 (Master-Detail 工作台) */}
      {activeTab === "tasks" ? (
        <div className="grid-2col-balanced">
          {/* 左侧：任务队列表格 */}
          <section className="panel">
            <div className="panel-header">
              <div className="panel-header-left">
                <Clock size={18} color="#176b87" />
                <h1>流水线任务队列</h1>
                <span className="panel-count-tag">共 {jobs.length} 项</span>
              </div>
              {hasActiveJob ? <span className="badge warn">任务执行中</span> : <span className="badge ok">队列就绪</span>}
            </div>

            <div className="ui-table-container">
              <table className="ui-table">
                <thead>
                  <tr>
                    <th style={{ width: "55px", textAlign: "center" }}>序号</th>
                    <th style={{ width: "65px" }}>编号</th>
                    <th style={{ width: "40%" }}>配置模板</th>
                    <th style={{ width: "23%" }}>执行状态</th>
                    <th style={{ width: "20%", textAlign: "center" }}>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {paginatedJobs.map((job, index) => {
                    const isSelected = selectedJobId === job.id;
                    const configName = job.config_path.replace("configs/experiments/", "");
                    const seqNumber = (jobPage - 1) * jobPageSize + index + 1;
                    return (
                      <tr
                        key={job.id}
                        className={isSelected ? "selected" : ""}
                        onClick={() => setSelectedJobId(job.id)}
                        style={{ cursor: "pointer" }}
                      >
                        <td style={{ textAlign: "center", color: "#64748b", fontWeight: 500 }}>
                          {seqNumber}
                        </td>
                        <td style={{ fontWeight: 600, color: "#176b87" }}>#{job.id}</td>
                        <td>
                          <Tooltip content={job.config_path}>
                            <span className="cell-ellipsis mono">{configName}</span>
                          </Tooltip>
                        </td>
                        <td>
                          <StatusBadge tone={jobStatusTone(job.status)} label={zhStatus(job.status)} />
                        </td>
                        <td style={{ textAlign: "center" }}>
                          <span className="row-actions" style={{ justifyContent: "center" }}>
                            {cancellableStatuses.has(job.status) ? (
                              <button
                                type="button"
                                className="table-action-btn danger"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  void cancelJob(job.id);
                                }}
                              >
                                <XCircle size={13} />
                                <span>取消</span>
                              </button>
                            ) : null}
                            {job.status === "failed" || job.status === "cancelled" ? (
                              <button
                                type="button"
                                className="table-action-btn primary"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  void retryJob(job.id);
                                }}
                              >
                                <RotateCcw size={13} />
                                <span>重试</span>
                              </button>
                            ) : null}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                  {!paginatedJobs.length ? (
                    <tr>
                      <td colSpan={5} style={{ textAlign: "center", padding: "30px 12px", color: "#8a99ad" }}>
                        暂无任何调度任务，请点击「启动流水线与产物」开始训练
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>

              {jobs.length > 0 ? (
                <Pagination
                  currentPage={jobPage}
                  pageSize={jobPageSize}
                  total={jobs.length}
                  onPageChange={setJobPage}
                  onPageSizeChange={setJobPageSize}
                />
              ) : null}
            </div>
          </section>

          {/* 右侧：任务运行监视器 */}
          <section className="panel">
            <div className="panel-header">
              <div className="panel-header-left">
                <Terminal size={18} color="#176b87" />
                <h1>任务监视器 {selectedJob ? `(#${selectedJob.id})` : ""}</h1>
              </div>
              {selectedJob ? (
                <StatusBadge tone={jobStatusTone(selectedJob.status)} label={zhStatus(selectedJob.status)} />
              ) : (
                <span className="panel-count-tag">请选择任务</span>
              )}
            </div>

            {selectedJob ? (
              <div>
                {selectedJobCancellationNote ? (
                  <div className="issue warn" style={{ marginBottom: 12 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <AlertCircle size={15} color="#d97706" />
                      <strong>取消状态通知</strong>
                    </div>
                    <span>{selectedJobCancellationNote}</span>
                  </div>
                ) : null}

                {/* 元信息卡片网格 */}
                <div className="detail-grid">
                  <div className="detail-card-item">
                    <span>配置模板文件</span>
                    <Tooltip content={selectedJob.config_path}>
                      <strong className="mono cell-ellipsis">{selectedJob.config_path}</strong>
                    </Tooltip>
                  </div>
                  <div className="detail-card-item">
                    <span>开始时间</span>
                    <strong>{formatBeijingTime(selectedJob.started_at ?? selectedJob.created_at)}</strong>
                  </div>
                  <div className="detail-card-item">
                    <span>完成时间</span>
                    <strong>{formatBeijingTime(selectedJob.completed_at)}</strong>
                  </div>
                  {selectedJob.cancelled_at ? (
                    <div className="detail-card-item">
                      <span>取消时间</span>
                      <strong>{formatBeijingTime(selectedJob.cancelled_at)}</strong>
                    </div>
                  ) : null}
                  {selectedJob.result?.cancelled_reason ? (
                    <div className="detail-card-item">
                      <span>取消原因</span>
                      <strong>{cancellationReasonLabel(selectedJob.result.cancelled_reason)}</strong>
                    </div>
                  ) : null}
                  {selectedJob.error ? (
                    <div className="detail-card-item" style={{ borderLeft: "3px solid #b91c1c" }}>
                      <span>异常错误信息</span>
                      <strong style={{ color: "#b91c1c" }}>{selectedJob.error}</strong>
                    </div>
                  ) : null}
                </div>

                {/* 下方分栏：日志与产物 */}
                <div className="detail-columns">
                  <div>
                    <h2>
                      <Terminal size={15} /> 实时执行日志 ({selectedJob.logs?.length ?? 0})
                    </h2>
                    <div className="log-list">
                      {(selectedJob.logs ?? []).slice(-30).map((log) => {
                        const isConsole = log.stream === "stdout" || log.stream === "stderr";
                        const zhStage = zhStream(log.stream);
                        const zhMsg = zhLogMessage(log.message);
                        const detailStr = zhLogDetail(log.detail);
                        const tone = logMessageTone(log.message);
                        const fullTooltip = `[${zhStage}] ${zhMsg}${detailStr ? ` | ${detailStr}` : ""}`;
                        return (
                          <div className={`log-row${isConsole ? " is-console" : ""}`} key={logKey(log)}>
                            <span className="log-stage">[{zhStage}]</span>
                            <Tooltip content={fullTooltip}>
                              <strong className={`cell-ellipsis log-status ${tone}`}>{zhMsg}</strong>
                            </Tooltip>
                            {!isConsole ? (
                              <Tooltip content={detailStr || "-"}>
                                <code className="cell-ellipsis">{detailStr || "-"}</code>
                              </Tooltip>
                            ) : null}
                          </div>
                        );
                      })}
                      {!(selectedJob.logs ?? []).length ? (
                        <div className="empty-row" style={{ color: "#64748b", background: "transparent" }}>
                          暂无实时控制台日志
                        </div>
                      ) : null}
                    </div>
                  </div>

                  <div>
                    <h2>
                      <Download size={15} /> 生成产物归档 ({selectedJob.artifacts?.length ?? 0})
                    </h2>
                    <div className="artifact-list">
                      {(selectedJob.artifacts ?? []).map((artifact: PipelineArtifact) => (
                        <a
                          key={artifact.id}
                          href={artifactDownloadUrl(artifact.id)}
                          onClick={(e) => {
                            e.preventDefault();
                            void downloadArtifact(artifact.id, artifact.name);
                          }}
                          download
                        >
                          <div className="artifact-info">
                            <Tooltip content={artifact.name}>
                              <strong className="cell-ellipsis">{artifact.name}</strong>
                            </Tooltip>
                            <span>{zhArtifactKind(artifact.kind)}</span>
                          </div>
                          <span className="artifact-size-tag">{humanSize(artifact.size)}</span>
                        </a>
                      ))}
                      {!(selectedJob.artifacts ?? []).length ? (
                        <div className="empty-row">尚未生成产物文件</div>
                      ) : null}
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="empty-row" style={{ minHeight: 280 }}>
                <FileCode size={32} color="#94a3b8" />
                <strong style={{ marginTop: 8, color: "#475569" }}>未选中任何流水线任务</strong>
                <span>请在左侧任务队列中点击任意行，实时监视日志与生成产物。</span>
              </div>
            )}
          </section>
        </div>
      ) : null}

      {/* Tab 2: 启动流水线与产物工具 */}
      {activeTab === "launch" ? (
        <div className="grid-2col-balanced">
          <section className="panel">
            <div className="panel-header">
              <div className="panel-header-left">
                <PlaySquare size={18} color="#176b87" />
                <h1>启动训练流水线</h1>
              </div>
              <span className="panel-count-tag">全自动闭环</span>
            </div>

            <div className="form-grid single" style={{ gap: 14 }}>
              <label>
                <span>实验模板配置</span>
                <select value={configPath} onChange={(e) => setConfigPath(e.target.value)} disabled={busy}>
                  {configOptions.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="check-row" style={{ maxWidth: "fit-content" }}>
                <input
                  type="checkbox"
                  checked={withPackage}
                  onChange={(e) => setWithPackage(e.target.checked)}
                  disabled={busy}
                />
                <span>评估通过后自动打包生成交付物</span>
              </label>

              <div>
                <span style={{ fontSize: 13, color: "#5b6778", fontWeight: 500, display: "flex", alignItems: "center", gap: 6 }}>
                  <Sliders size={14} /> 已挂载任务适配器
                </span>
                <div className="split-chip-group" style={{ marginTop: 6 }}>
                  {adapters.map((ad) => (
                    <span className="split-chip" key={`${ad.task}-${ad.name}`}>
                      <span>{zhStatus(ad.task)}:</span>
                      <strong>{ad.name}</strong>
                    </span>
                  ))}
                  {!adapters.length ? <span style={{ fontSize: 12, color: "#8a99ad" }}>加载适配器列表中…</span> : null}
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
                  <span style={{ fontSize: 12.5, color: "#8a99ad" }}>就绪：点击右侧开始异步调度</span>
                )}
              </div>
              <button className="primary-button" onClick={startPipeline} disabled={busy}>
                <Play size={16} />
                <span>{busy ? "调度中…" : "启动训练流水线"}</span>
              </button>
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div className="panel-header-left">
                <Boxes size={18} color="#176b87" />
                <h1>产物管理与手动打包工具</h1>
              </div>
            </div>

            <div style={{ display: "grid", gap: 16 }}>
              <div className="overview-quick-card">
                <div className="overview-quick-header">
                  <strong>根据当前配置手动打包</strong>
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={buildPackage}
                    disabled={busy}
                    style={{ minHeight: 32, padding: "0 12px", fontSize: 12.5 }}
                  >
                    <Boxes size={14} />
                    <span>立即生成交付包</span>
                  </button>
                </div>
                <span style={{ fontSize: 12, color: "#64748b" }}>
                  跳过训练直接根据配置中的导出权重建立标准目录包与元数据。
                </span>
              </div>

              <div className="overview-quick-card">
                <div className="overview-quick-header">
                  <strong>上传外部产物 / 权重文件</strong>
                  <UploadCloud size={16} color="#176b87" />
                </div>
                <input
                  type="file"
                  onChange={(e) => void submitUpload(e.target.files?.item(0))}
                  disabled={busy}
                  style={{ width: "100%", marginTop: 4 }}
                />
                <span style={{ fontSize: 12, color: "#64748b" }}>
                  支持上传 ONNX 权重、报告或评估日志，将自动归档至平台存储池。
                </span>
              </div>
            </div>
          </section>
        </div>
      ) : null}

      {/* Tab 3: 运行历史与指标 (全边框表格、东八区时间格式化无T、分页) */}
      {activeTab === "history" ? (
        <section className="panel">
          <div className="panel-header">
            <div className="panel-header-left">
              <Activity size={18} color="#176b87" />
              <h1>流水线运行历史记录与指标</h1>
              <span className="panel-count-tag">共 {runs.length} 次记录</span>
            </div>
            {latest ? (
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ fontSize: 12, color: "#5b6778" }}>最新状态:</span>
                <StatusBadge tone={jobStatusTone(latest.status)} label={zhStatus(latest.status)} />
              </div>
            ) : null}
          </div>

          <div className="ui-table-container">
            <table className="ui-table">
              <thead>
                <tr>
                  <th style={{ width: "55px", textAlign: "center" }}>序号</th>
                  <th style={{ width: "32%" }}>配置路径</th>
                  <th style={{ width: "15%" }}>执行状态</th>
                  <th style={{ width: "25%" }}>核心评估指标</th>
                  <th style={{ width: "23%" }}>执行时间</th>
                </tr>
              </thead>
              <tbody>
                {paginatedRuns.map((run, index) => {
                  const cfg = run.config_path ?? run.report?.config ?? "-";
                  const src = run.report?.evaluation?.metrics_source;
                  const seqNumber = (runPage - 1) * runPageSize + index + 1;
                  return (
                    <tr key={`${run.id}-${run.created_at}`}>
                      <td style={{ textAlign: "center", color: "#64748b", fontWeight: 500 }}>
                        {seqNumber}
                      </td>
                      <td>
                        <Tooltip content={cfg}>
                          <span className="cell-ellipsis mono">{cfg}</span>
                        </Tooltip>
                      </td>
                      <td>
                        <StatusBadge tone={jobStatusTone(run.status)} label={zhStatus(run.status)} />
                      </td>
                      <td>
                        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                          <Tooltip content={metricsText(run)}>
                            <span className="cell-ellipsis mono" style={{ fontSize: 12 }}>
                              {metricsText(run)}
                            </span>
                          </Tooltip>
                          {src ? <span className="metrics-source">{zhMetricsSource(src)}</span> : null}
                        </div>
                      </td>
                      <td>
                        <span className="mono" style={{ fontSize: 12, color: "#475569" }}>
                          {formatBeijingTime(run.created_at)}
                        </span>
                      </td>
                    </tr>
                  );
                })}
                {!paginatedRuns.length ? (
                  <tr>
                    <td colSpan={5} style={{ textAlign: "center", padding: "30px 12px", color: "#8a99ad" }}>
                      暂无历史运行记录
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>

            {runs.length > 0 ? (
              <Pagination
                currentPage={runPage}
                pageSize={runPageSize}
                total={runs.length}
                onPageChange={setRunPage}
                onPageSizeChange={setRunPageSize}
              />
            ) : null}
          </div>
        </section>
      ) : null}

      {/* Tab 4: 误差分析与审计日志 (全边框表格、东八区时间无T、分页) */}
      {activeTab === "audit" ? (
        <div className="grid-2col">
          {/* 左侧：误差探测 */}
          <section className="panel">
            <div className="panel-header">
              <div className="panel-header-left">
                <FileSearch size={18} color="#176b87" />
                <h1>样本数据误差探测</h1>
              </div>
            </div>

            <div style={{ display: "grid", gap: 14 }}>
              <label>
                <span>样本清单文件路径</span>
                <input
                  value={errorPath}
                  onChange={(e) => setErrorPath(e.target.value)}
                  placeholder="例如: data/manifests/example_train_v1.jsonl"
                />
              </label>

              <div className="form-actions-bar" style={{ marginTop: 0, paddingTop: 8 }}>
                <span style={{ fontSize: 12.5, color: "#64748b" }}>
                  有效样本总数: <strong>{analysis?.total ?? 0}</strong>
                </span>
                <button type="button" className="primary-button" onClick={inspectErrors}>
                  <Search size={14} />
                  <span>执行分析</span>
                </button>
              </div>

              {analysis ? (
                <div className="issue-list" style={{ marginTop: 8 }}>
                  <div className="summary-line">
                    <span>错误分类统计:</span>
                    <strong>{Object.entries(analysis.by_type).map(([k, v]) => `${k}:${v}`).join(" / ") || "未检测到异常"}</strong>
                  </div>
                </div>
              ) : null}
            </div>
          </section>

          {/* 右侧：审计事件全边框表格 */}
          <section className="panel">
            <div className="panel-header">
              <div className="panel-header-left">
                <Clock size={18} color="#176b87" />
                <h1>平台操作安全审计日志</h1>
                <span className="panel-count-tag">共 {events.length} 项</span>
              </div>
            </div>

            <div className="ui-table-container">
              <table className="ui-table">
                <thead>
                  <tr>
                    <th style={{ width: "55px", textAlign: "center" }}>序号</th>
                    <th style={{ width: "22%" }}>动作类型</th>
                    <th style={{ width: "45%" }}>操作对象</th>
                    <th style={{ width: "28%" }}>记录时间</th>
                  </tr>
                </thead>
                <tbody>
                  {paginatedEvents.map((ev, index) => {
                    const seqNumber = (auditPage - 1) * auditPageSize + index + 1;
                    return (
                      <tr key={ev.id}>
                        <td style={{ textAlign: "center", color: "#64748b", fontWeight: 500 }}>
                          {seqNumber}
                        </td>
                        <td style={{ fontWeight: 500 }}>{zhAction(ev.action)}</td>
                        <td>
                          <Tooltip content={ev.target}>
                            <span className="cell-ellipsis mono">{ev.target}</span>
                          </Tooltip>
                        </td>
                        <td>
                          <span className="mono" style={{ fontSize: 12, color: "#475569" }}>
                            {formatBeijingTime(ev.created_at)}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                  {!paginatedEvents.length ? (
                    <tr>
                      <td colSpan={4} style={{ textAlign: "center", padding: "26px 12px", color: "#8a99ad" }}>
                        暂无审计事件
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>

              {events.length > 0 ? (
                <Pagination
                  currentPage={auditPage}
                  pageSize={auditPageSize}
                  total={events.length}
                  onPageChange={setAuditPage}
                  onPageSizeChange={setAuditPageSize}
                />
              ) : null}
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
