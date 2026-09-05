import {
  ArrowRight,
  Boxes,
  Clock,
  Cpu,
  FlaskConical,
  FolderGit2,
  HardDrive,
  PackageCheck,
  PlaySquare,
  ShieldCheck,
  Zap
} from "lucide-react";
import { useState } from "react";
import type { ViewKey } from "../components/Shell";
import { StatusBadge } from "../components/StatusBadge";
import { TabBar, type TabItem } from "../components/TabBar";
import { Tooltip } from "../components/Tooltip";
import { zhStatus } from "../i18n";
import type { ExperimentRecord, Health, PackageValidation, PackageValidationRecord, PipelineRunRecord } from "../types";
import { formatBeijingTime } from "../utils/date";

type OverviewProps = {
  health?: Health;
  packages: PackageValidation[];
  validations: PackageValidationRecord[];
  experiments: ExperimentRecord[];
  pipelineRuns: PipelineRunRecord[];
  onNavigate?: (view: ViewKey) => void;
};

const overviewTabs: TabItem[] = [
  { key: "dynamic", label: "核心动态与快捷操作", icon: <Zap size={15} /> },
  { key: "workspace", label: "工作区与系统环境", icon: <FolderGit2 size={15} /> }
];

export function Overview({ health, packages, validations, experiments, pipelineRuns, onNavigate }: OverviewProps) {
  const [activeTab, setActiveTab] = useState("dynamic");

  const validPackages = packages.filter((item) => item.ok).length;
  const passRate = packages.length > 0 ? Math.round((validPackages / packages.length) * 100) : 100;
  const lastValidation = validations[0];
  const lastRun = pipelineRuns[0];

  return (
    <div className="page-grid">
      {/* 顶部 KPI 核心指标卡片 */}
      <section className="metric-strip" aria-label="核心统计指标">
        <div className="metric">
          <div className="metric-header">
            <span>已注册模型包</span>
            <span className="metric-icon-wrap" aria-hidden="true">
              <PackageCheck size={18} />
            </span>
          </div>
          <div className="metric-body">
            <strong>{packages.length}</strong>
            <span className="metric-badge-tag">共 {packages.length} 个模型</span>
          </div>
        </div>

        <div className="metric">
          <div className="metric-header">
            <span>校验合规率</span>
            <span className="metric-icon-wrap" aria-hidden="true">
              <ShieldCheck size={18} />
            </span>
          </div>
          <div className="metric-body">
            <strong>{passRate}%</strong>
            <span className="metric-badge-tag">{validPackages} 通过 / {packages.length - validPackages} 需关注</span>
          </div>
        </div>

        <div className="metric">
          <div className="metric-header">
            <span>流水线运行</span>
            <span className="metric-icon-wrap" aria-hidden="true">
              <PlaySquare size={18} />
            </span>
          </div>
          <div className="metric-body">
            <strong>{pipelineRuns.length}</strong>
            <span className="metric-badge-tag">
              {lastRun ? `最近: ${zhStatus(lastRun.status)}` : "暂无历史记录"}
            </span>
          </div>
        </div>

        <div className="metric">
          <div className="metric-header">
            <span>实验记录库</span>
            <span className="metric-icon-wrap" aria-hidden="true">
              <FlaskConical size={18} />
            </span>
          </div>
          <div className="metric-body">
            <strong>{experiments.length}</strong>
            <span className="metric-badge-tag">覆盖多视觉任务</span>
          </div>
        </div>
      </section>

      {/* 区域 Tab 切换栏 */}
      <TabBar tabs={overviewTabs} activeKey={activeTab} onChange={setActiveTab} />

      {/* Tab 内容区 */}
      {activeTab === "dynamic" ? (
        <div className="grid-2col-balanced">
          <section className="panel">
            <div className="panel-header">
              <div className="panel-header-left">
                <Zap size={18} color="#176b87" />
                <h1>近期核心运行动态</h1>
              </div>
            </div>

            <div style={{ display: "grid", gap: 14 }}>
              <div className="overview-quick-card">
                <div className="overview-quick-header">
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <Boxes size={16} color="#176b87" />
                    <strong>最近模型包交付校验</strong>
                  </div>
                  {lastValidation ? (
                    <StatusBadge ok={lastValidation.ok} label={lastValidation.ok ? "校验通过" : "校验未通过"} />
                  ) : (
                    <span className="badge neutral">暂无记录</span>
                  )}
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 4 }}>
                  <Tooltip content={lastValidation ? `${lastValidation.package_dir} (${lastValidation.model_file ?? "全目录"})` : "暂无最近校验记录"}>
                    <span className="cell-ellipsis mono" style={{ fontSize: 12.5, color: "#475569" }}>
                      {lastValidation ? `${lastValidation.package_dir} (${lastValidation.model_file ?? "全部文件"})` : "暂无最近校验记录"}
                    </span>
                  </Tooltip>
                  {lastValidation ? (
                    <span style={{ fontSize: 11.5, color: "#8a99ad", flexShrink: 0 }}>
                      {formatBeijingTime(lastValidation.created_at)}
                    </span>
                  ) : null}
                </div>
              </div>

              <div className="overview-quick-card">
                <div className="overview-quick-header">
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <Clock size={16} color="#176b87" />
                    <strong>最近训练流水线运行</strong>
                  </div>
                  {lastRun ? (
                    <StatusBadge ok={lastRun.status === "completed"} label={zhStatus(lastRun.status)} />
                  ) : (
                    <span className="badge neutral">未执行</span>
                  )}
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 4 }}>
                  <Tooltip content={lastRun ? (lastRun.config_path ?? lastRun.report?.config ?? "-") : "暂无最近流水线运行记录"}>
                    <span className="cell-ellipsis mono" style={{ fontSize: 12.5, color: "#475569" }}>
                      {lastRun ? (lastRun.config_path ?? lastRun.report?.config ?? "-") : "暂无最近流水线运行记录"}
                    </span>
                  </Tooltip>
                  {lastRun ? (
                    <span style={{ fontSize: 11.5, color: "#8a99ad", flexShrink: 0 }}>
                      {formatBeijingTime(lastRun.created_at)}
                    </span>
                  ) : null}
                </div>
              </div>
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div className="panel-header-left">
                <Cpu size={18} color="#176b87" />
                <h1>快捷工作流导航</h1>
              </div>
            </div>

            <div className="overview-action-strip" style={{ marginTop: 0 }}>
              <button
                type="button"
                className="action-card-btn"
                onClick={() => onNavigate?.("packages")}
              >
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
                  <Boxes size={16} color="#176b87" />
                  <ArrowRight size={13} color="#8a99ad" />
                </div>
                <strong>模型包交付校验</strong>
                <span>检查文件哈希与样例格式规范</span>
              </button>

              <button
                type="button"
                className="action-card-btn"
                onClick={() => onNavigate?.("pipeline")}
              >
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
                  <PlaySquare size={16} color="#176b87" />
                  <ArrowRight size={13} color="#8a99ad" />
                </div>
                <strong>调度训练流水线</strong>
                <span>端到端训练评估与自动打包</span>
              </button>

              <button
                type="button"
                className="action-card-btn"
                onClick={() => onNavigate?.("experiments")}
              >
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
                  <FlaskConical size={16} color="#176b87" />
                  <ArrowRight size={13} color="#8a99ad" />
                </div>
                <strong>实验台账知识库</strong>
                <span>多任务参数登记与资产追溯</span>
              </button>
            </div>
          </section>
        </div>
      ) : (
        <section className="panel">
          <div className="panel-header">
            <div className="panel-header-left">
              <FolderGit2 size={18} color="#176b87" />
              <h1>工作区与系统环境参数</h1>
            </div>
            <span className="panel-count-tag">在线节点</span>
          </div>

          <div className="ui-table-container">
            <table className="ui-table">
              <thead>
                <tr>
                  <th style={{ width: "55px", textAlign: "center" }}>序号</th>
                  <th style={{ width: "180px" }}>环境参数项目</th>
                  <th>当前配置值与状态</th>
                  <th style={{ width: "260px" }}>参数说明</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td style={{ textAlign: "center", color: "#64748b", fontWeight: 500 }}>1</td>
                  <td style={{ fontWeight: 500 }}>平台内核版本</td>
                  <td><span className="mono">{health?.version ?? "-"}</span></td>
                  <td>
                    <Tooltip content="当前服务运行版本">
                      <span className="cell-ellipsis">当前服务运行版本</span>
                    </Tooltip>
                  </td>
                </tr>
                <tr>
                  <td style={{ textAlign: "center", color: "#64748b", fontWeight: 500 }}>2</td>
                  <td style={{ fontWeight: 500 }}>工作区物理根路径</td>
                  <td>
                    <Tooltip content={health?.workspace ?? "-"}>
                      <span className="cell-ellipsis mono">{health?.workspace ?? "-"}</span>
                    </Tooltip>
                  </td>
                  <td>
                    <Tooltip content="本地模型存储基准目录">
                      <span className="cell-ellipsis">本地模型存储基准目录</span>
                    </Tooltip>
                  </td>
                </tr>
                <tr>
                  <td style={{ textAlign: "center", color: "#64748b", fontWeight: 500 }}>3</td>
                  <td style={{ fontWeight: 500 }}>存储引擎后端</td>
                  <td>
                    <span className="badge neutral">
                      <HardDrive size={12} style={{ marginRight: 4 }} />
                      {health?.storage_backend === "local" ? "本地文件对象池 (local)" : (health?.storage_backend ?? "-")}
                    </span>
                  </td>
                  <td>
                    <Tooltip content="模型权重与产物持久化方式">
                      <span className="cell-ellipsis">模型权重与产物持久化方式</span>
                    </Tooltip>
                  </td>
                </tr>
                <tr>
                  <td style={{ textAlign: "center", color: "#64748b", fontWeight: 500 }}>4</td>
                  <td style={{ fontWeight: 500 }}>核心服务健康状态</td>
                  <td>
                    <StatusBadge ok={health?.status === "ok"} label={zhStatus(health?.status)} />
                  </td>
                  <td>
                    <Tooltip content="API 服务通信响应状态">
                      <span className="cell-ellipsis">API 服务通信响应状态</span>
                    </Tooltip>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
