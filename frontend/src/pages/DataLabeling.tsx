import {
  AlertTriangle,
  CheckCircle2,
  Database,
  FileCheck,
  FileSearch,
  FileText,
  Play,
  ShieldCheck
} from "lucide-react";
import { useState } from "react";
import { errorMessage, validateContract, validateManifest } from "../api";
import { Pagination } from "../components/Pagination";
import { StatusBadge } from "../components/StatusBadge";
import { TabBar, type TabItem } from "../components/TabBar";
import { Tooltip } from "../components/Tooltip";
import { zhContractKind, zhIssue, zhIssueDetail, zhSplit } from "../i18n";
import type { ContractValidation, ManifestValidation } from "../types";

const manifestOptions = [
  { label: "示例训练样本清单 (data/manifests/example_train_v1.jsonl)", value: "data/manifests/example_train_v1.jsonl" },
  { label: "自定义清单文件路径", value: "custom" }
] as const;

const contractTemplates: Record<"models-fragment" | "release-decision", string> = {
  "models-fragment": "configs/export/models.fragment.template.yml",
  "release-decision": "configs/export/release-decision.template.yml"
};

const dataLabelingTabs: TabItem[] = [
  { key: "manifest", label: "数据清单校验 (JSONL 样本)", icon: <Database size={15} /> },
  { key: "contract", label: "交付契约校验 (YAML 契约)", icon: <ShieldCheck size={15} /> }
];

export function DataLabeling() {
  const [activeTab, setActiveTab] = useState("manifest");

  // 数据清单状态
  const [manifestMode, setManifestMode] = useState<(typeof manifestOptions)[number]["value"]>("data/manifests/example_train_v1.jsonl");
  const [manifestPath, setManifestPath] = useState<string>(manifestOptions[0].value);
  const [manifestResult, setManifestResult] = useState<ManifestValidation | null>(null);
  const [manifestBusy, setManifestBusy] = useState(false);
  const [manifestMessage, setManifestMessage] = useState("");
  const [manifestPage, setManifestPage] = useState(1);
  const [manifestPageSize, setManifestPageSize] = useState(10);

  // 交付契约状态
  const [contractKind, setContractKind] = useState<"models-fragment" | "release-decision">("models-fragment");
  const [contractPath, setContractPath] = useState<string>(contractTemplates["models-fragment"]);
  const [customContractPath, setCustomContractPath] = useState(false);
  const [contractResult, setContractResult] = useState<ContractValidation | null>(null);
  const [contractBusy, setContractBusy] = useState(false);
  const [contractMessage, setContractMessage] = useState("");
  const [contractPage, setContractPage] = useState(1);
  const [contractPageSize, setContractPageSize] = useState(10);

  async function submitManifest() {
    setManifestBusy(true);
    setManifestMessage("");
    try {
      const response = await validateManifest(manifestPath);
      setManifestResult(response.manifest);
      setManifestMessage(response.manifest.ok ? "数据清单行格式与切分验证通过" : "数据清单存在不合规行，请查看下方诊断");
      setManifestPage(1);
    } catch (error) {
      setManifestMessage(errorMessage(error));
    } finally {
      setManifestBusy(false);
    }
  }

  async function submitContract() {
    setContractBusy(true);
    setContractMessage("");
    try {
      const response = await validateContract(contractKind, contractPath);
      setContractResult(response.contract);
      setContractMessage(response.contract.ok ? "契约元数据与决策节点验证通过" : "契约字段校验未通过，请查看下方诊断");
      setContractPage(1);
    } catch (error) {
      setContractMessage(errorMessage(error));
    } finally {
      setContractBusy(false);
    }
  }

  // 分页数据
  const paginatedManifestIssues = manifestResult?.issues
    ? manifestResult.issues.slice((manifestPage - 1) * manifestPageSize, manifestPage * manifestPageSize)
    : [];

  const paginatedContractIssues = contractResult?.issues
    ? contractResult.issues.slice((contractPage - 1) * contractPageSize, contractPage * contractPageSize)
    : [];

  return (
    <div className="page-grid">
      {/* Tab 选项卡切换栏 */}
      <TabBar tabs={dataLabelingTabs} activeKey={activeTab} onChange={setActiveTab} />

      {/* Tab 1: 数据清单校验 */}
      {activeTab === "manifest" ? (
        <div style={{ display: "grid", gap: 20 }}>
          <section className="panel">
            <div className="panel-header">
              <div className="panel-header-left">
                <Database size={18} color="#176b87" />
                <h1>数据样本清单校验工作台</h1>
              </div>
              <span className="panel-count-tag">JSONL 格式</span>
            </div>

            <div className="form-grid single" style={{ gap: 14 }}>
              <label>
                <span>数据清单来源文件</span>
                <select
                  value={manifestMode}
                  onChange={(event) => {
                    const nextMode = event.target.value as (typeof manifestOptions)[number]["value"];
                    setManifestMode(nextMode);
                    if (nextMode !== "custom") {
                      setManifestPath(nextMode);
                    }
                  }}
                >
                  {manifestOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>

              {manifestMode === "custom" ? (
                <label>
                  <span>自定义清单文件相对路径</span>
                  <input
                    value={manifestPath}
                    onChange={(event) => setManifestPath(event.target.value)}
                    placeholder="例如: data/manifests/train_v1.jsonl"
                  />
                </label>
              ) : null}
            </div>

            <div className="form-actions-bar">
              <div>
                {manifestMessage ? (
                  <span className="inline-message" style={{ fontWeight: 500 }}>
                    {manifestMessage}
                  </span>
                ) : (
                  <span style={{ fontSize: 12.5, color: "#8a99ad" }}>就绪：检查样本键值结构与数据集切分</span>
                )}
              </div>
              <button className="primary-button" onClick={submitManifest} disabled={manifestBusy}>
                <Play size={16} />
                <span>{manifestBusy ? "校验中…" : "开始校验数据清单"}</span>
              </button>
            </div>
          </section>

          {/* 实时清单诊断结果 */}
          <section className="panel">
            <div className="panel-header">
              <div className="panel-header-left">
                <FileCheck size={18} color="#176b87" />
                <h1>清单诊断报告</h1>
              </div>
              {manifestResult ? <StatusBadge ok={manifestResult.ok} /> : <span className="panel-count-tag">待触发</span>}
            </div>

            {manifestResult ? (
              <div>
                <div className="summary-line">
                  <StatusBadge ok={manifestResult.ok} label={manifestResult.ok ? "清单校验通过" : "存在不合规行"} />
                  <span style={{ fontWeight: 600 }}>样本总行数：{manifestResult.total_rows} 行</span>
                </div>

                {/* 切分集展示 */}
                <div style={{ marginTop: 12, marginBottom: 14 }}>
                  <span style={{ fontSize: 12.5, color: "#64748b", fontWeight: 500 }}>数据集划分分布 (Splits)</span>
                  <div className="split-chip-group" style={{ marginTop: 6 }}>
                    {Object.entries(manifestResult.split_counts).map(([key, value]) => (
                      <span className="split-chip" key={key}>
                        <span>{zhSplit(key)}:</span>
                        <strong>{value}</strong>
                      </span>
                    ))}
                  </div>
                </div>

                {/* 问题全边框表格 */}
                <div className="ui-table-container">
                  <table className="ui-table">
                    <thead>
                      <tr>
                        <th style={{ width: "55px", textAlign: "center" }}>序号</th>
                        <th style={{ width: "90px" }}>所在行号</th>
                        <th style={{ width: "180px" }}>规则代码</th>
                        <th style={{ width: "140px" }}>对应字段</th>
                        <th>合规诊断说明</th>
                      </tr>
                    </thead>
                    <tbody>
                      {paginatedManifestIssues.map((issue, idx) => {
                        const seqNumber = (manifestPage - 1) * manifestPageSize + idx + 1;
                        return (
                          <tr key={`${issue.code}-${issue.line}-${idx}`}>
                            <td style={{ textAlign: "center", color: "#64748b", fontWeight: 500 }}>
                              {seqNumber}
                            </td>
                            <td style={{ fontWeight: 600, color: "#176b87" }}>
                              {issue.line ? `第 ${issue.line} 行` : "-"}
                            </td>
                            <td>
                              <Tooltip content={zhIssue(issue.code)}>
                                <span className="cell-ellipsis" style={{ color: "#b91c1c", fontWeight: 500 }}>
                                  {zhIssue(issue.code)}
                                </span>
                              </Tooltip>
                            </td>
                            <td>
                              <span className="mono">{issue.field || "-"}</span>
                            </td>
                            <td>
                              <Tooltip content={zhIssueDetail(issue)}>
                                <span className="cell-ellipsis">{zhIssueDetail(issue)}</span>
                              </Tooltip>
                            </td>
                          </tr>
                        );
                      })}
                      {!manifestResult.issues.length ? (
                        <tr>
                          <td colSpan={5} style={{ textAlign: "center", padding: "26px 12px", color: "#166534" }}>
                            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}>
                              <CheckCircle2 size={18} color="#16a34a" />
                              <strong>清单全部行与字段均符合训练集标准规范</strong>
                            </div>
                          </td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>

                  {manifestResult.issues.length > 0 ? (
                    <Pagination
                      currentPage={manifestPage}
                      pageSize={manifestPageSize}
                      total={manifestResult.issues.length}
                      onPageChange={setManifestPage}
                      onPageSizeChange={setManifestPageSize}
                    />
                  ) : null}
                </div>
              </div>
            ) : (
              <div className="empty-row" style={{ minHeight: 180 }}>
                <FileSearch size={28} color="#94a3b8" />
                <span style={{ marginTop: 6 }}>请在上方选择清单并点击「开始校验数据清单」以查看解析结果</span>
              </div>
            )}
          </section>
        </div>
      ) : (
        /* Tab 2: 交付契约校验 */
        <div style={{ display: "grid", gap: 20 }}>
          <section className="panel">
            <div className="panel-header">
              <div className="panel-header-left">
                <ShieldCheck size={18} color="#176b87" />
                <h1>算法交付契约校验工作台</h1>
              </div>
              <span className="panel-count-tag">YAML 规范</span>
            </div>

            <div className="form-grid" style={{ gap: 14 }}>
              <label>
                <span>契约规范类型</span>
                <select
                  value={contractKind}
                  onChange={(event) => {
                    const nextKind = event.target.value as "models-fragment" | "release-decision";
                    setContractKind(nextKind);
                    if (!customContractPath) {
                      setContractPath(contractTemplates[nextKind]);
                    }
                  }}
                >
                  <option value="models-fragment">{zhContractKind("models-fragment")}</option>
                  <option value="release-decision">{zhContractKind("release-decision")}</option>
                </select>
              </label>

              <label>
                <span>契约文件来源模式</span>
                <select
                  value={customContractPath ? "custom" : "template"}
                  onChange={(event) => {
                    const useCustom = event.target.value === "custom";
                    setCustomContractPath(useCustom);
                    if (!useCustom) {
                      setContractPath(contractTemplates[contractKind]);
                    }
                  }}
                >
                  <option value="template">平台默认规范模板</option>
                  <option value="custom">指定自定义契约路径</option>
                </select>
              </label>

              {customContractPath ? (
                <label style={{ gridColumn: "1 / -1" }}>
                  <span>自定义契约文件相对路径</span>
                  <input
                    value={contractPath}
                    onChange={(event) => setContractPath(event.target.value)}
                    placeholder="例如: configs/export/my_release_contract.yml"
                  />
                </label>
              ) : null}
            </div>

            <div className="form-actions-bar">
              <div>
                {contractMessage ? (
                  <span className="inline-message" style={{ fontWeight: 500 }}>
                    {contractMessage}
                  </span>
                ) : (
                  <span style={{ fontSize: 12.5, color: "#8a99ad" }}>就绪：验证算法产物模型卡与发布决策节点</span>
                )}
              </div>
              <button className="primary-button" onClick={submitContract} disabled={contractBusy}>
                <Play size={16} />
                <span>{contractBusy ? "校验中…" : "开始校验交付契约"}</span>
              </button>
            </div>
          </section>

          {/* 实时契约诊断结果 */}
          <section className="panel">
            <div className="panel-header">
              <div className="panel-header-left">
                <FileText size={18} color="#176b87" />
                <h1>契约合规诊断报告</h1>
              </div>
              {contractResult ? <StatusBadge ok={contractResult.ok} /> : <span className="panel-count-tag">待校验</span>}
            </div>

            {contractResult ? (
              <div>
                <div className="summary-line">
                  <StatusBadge ok={contractResult.ok} label={contractResult.ok ? "契约验证通过" : "契约存在缺陷"} />
                  <Tooltip content={contractResult.path}>
                    <span className="cell-ellipsis mono" style={{ fontWeight: 600 }}>
                      {contractResult.path}
                    </span>
                  </Tooltip>
                </div>

                <div className="ui-table-container" style={{ marginTop: 16 }}>
                  <table className="ui-table">
                    <thead>
                      <tr>
                        <th style={{ width: "55px", textAlign: "center" }}>序号</th>
                        <th style={{ width: "200px" }}>规范代码</th>
                        <th style={{ width: "260px" }}>契约节点路径</th>
                        <th>合规诊断详情</th>
                      </tr>
                    </thead>
                    <tbody>
                      {paginatedContractIssues.map((issue, idx) => {
                        const seqNumber = (contractPage - 1) * contractPageSize + idx + 1;
                        return (
                          <tr key={`${issue.code}-${issue.path}-${idx}`}>
                            <td style={{ textAlign: "center", color: "#64748b", fontWeight: 500 }}>
                              {seqNumber}
                            </td>
                            <td>
                              <Tooltip content={zhIssue(issue.code)}>
                                <span className="cell-ellipsis" style={{ color: "#b91c1c", fontWeight: 500 }}>
                                  {zhIssue(issue.code)}
                                </span>
                              </Tooltip>
                            </td>
                            <td>
                              <Tooltip content={issue.path || "-"}>
                                <span className="cell-ellipsis mono">{issue.path || "-"}</span>
                              </Tooltip>
                            </td>
                            <td>
                              <Tooltip content={zhIssueDetail(issue)}>
                                <span className="cell-ellipsis">{zhIssueDetail(issue)}</span>
                              </Tooltip>
                            </td>
                          </tr>
                        );
                      })}
                      {!contractResult.issues.length ? (
                        <tr>
                          <td colSpan={4} style={{ textAlign: "center", padding: "26px 12px", color: "#166534" }}>
                            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}>
                              <CheckCircle2 size={18} color="#16a34a" />
                              <strong>契约模型架构完全符合交付与发布规范</strong>
                            </div>
                          </td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>

                  {contractResult.issues.length > 0 ? (
                    <Pagination
                      currentPage={contractPage}
                      pageSize={contractPageSize}
                      total={contractResult.issues.length}
                      onPageChange={setContractPage}
                      onPageSizeChange={setContractPageSize}
                    />
                  ) : null}
                </div>
              </div>
            ) : (
              <div className="empty-row" style={{ minHeight: 180 }}>
                <FileSearch size={28} color="#94a3b8" />
                <span style={{ marginTop: 6 }}>请在上方选择契约并点击「开始校验交付契约」以查看架构字段诊断</span>
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
