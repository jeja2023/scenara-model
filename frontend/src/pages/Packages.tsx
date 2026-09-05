import {
  AlertTriangle,
  Boxes,
  CheckCircle2,
  FileCheck,
  PackageCheck,
  Play,
  RotateCw,
  Search,
  ShieldCheck,
  Sliders
} from "lucide-react";
import { useMemo, useState } from "react";
import { errorMessage, validatePackage } from "../api";
import { Pagination } from "../components/Pagination";
import { StatusBadge } from "../components/StatusBadge";
import { TabBar, type TabItem } from "../components/TabBar";
import { Tooltip } from "../components/Tooltip";
import { zhIssue, zhIssueDetail } from "../i18n";
import type { PackageValidation, PackageValidationRecord } from "../types";

type PackagesProps = {
  packages: PackageValidation[];
  onRefresh: () => void;
};

const packageDirectoryOptions = [
  { label: "默认模型仓库 (shared-models)", value: "shared-models" },
  { label: "自定义目录路径", value: "custom" }
] as const;

function isValidationRecord(value: PackageValidation | PackageValidationRecord): value is PackageValidationRecord {
  return "report" in value;
}

const packageTabs: TabItem[] = [
  { key: "verify", label: "模型包交付校验工作台", icon: <PackageCheck size={15} /> },
  { key: "registry", label: "模型仓库全量扫描库", icon: <Boxes size={15} /> }
];

export function Packages({ packages, onRefresh }: PackagesProps) {
  const [activeTab, setActiveTab] = useState("verify");
  const [packageDir, setPackageDir] = useState("shared-models");
  const [packageDirMode, setPackageDirMode] = useState<(typeof packageDirectoryOptions)[number]["value"]>("shared-models");
  const [modelId, setModelId] = useState("");
  const [strictHash, setStrictHash] = useState(true);
  const [strictExamples, setStrictExamples] = useState(true);
  const [strictOnnx, setStrictOnnx] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<PackageValidation | null>(null);

  // 搜索与分页状态
  const [filterQuery, setFilterQuery] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  async function runValidation() {
    setBusy(true);
    setMessage("正在执行严格校验…");
    try {
      const response = await validatePackage({
        package_dir: packageDir,
        model_id: modelId || undefined,
        strict_hash: strictHash,
        strict_examples: strictExamples,
        strict_onnx: strictOnnx
      });
      const validation = isValidationRecord(response.validation) ? response.validation.report : response.validation;
      setResult(validation);
      setMessage(validation.ok ? "校验完成：各项指标均符合交付标准" : "校验完成：发现潜在不合规项，请查看右侧诊断");
      onRefresh();
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  const filteredPackages = useMemo(() => {
    if (!filterQuery.trim()) {
      return packages;
    }
    const query = filterQuery.toLowerCase();
    return packages.filter((item) => {
      const name = (item.model_file ?? item.package_dir).toLowerCase();
      return name.includes(query);
    });
  }, [packages, filterQuery]);

  // 分页数据切片
  const paginatedPackages = useMemo(() => {
    const startIndex = (currentPage - 1) * pageSize;
    return filteredPackages.slice(startIndex, startIndex + pageSize);
  }, [filteredPackages, currentPage, pageSize]);

  return (
    <div className="page-grid">
      {/* 选项卡切换栏 */}
      <TabBar
        tabs={packageTabs.map((t) => (t.key === "registry" ? { ...t, badge: packages.length } : t))}
        activeKey={activeTab}
        onChange={(key) => {
          setActiveTab(key);
          setCurrentPage(1);
        }}
      />

      {/* Tab 1: 校验工作台 */}
      {activeTab === "verify" ? (
        <div className="grid-2col-balanced">
          {/* 左侧：参数配置 */}
          <section className="panel">
            <div className="panel-header">
              <div className="panel-header-left">
                <PackageCheck size={18} color="#176b87" />
                <h1>交付校验参数配置</h1>
              </div>
              <span className="panel-count-tag">准入规范</span>
            </div>

            <div className="form-grid single" style={{ gap: 14 }}>
              <label>
                <span>模型仓库目录</span>
                <select
                  value={packageDirMode}
                  onChange={(event) => {
                    const nextMode = event.target.value as (typeof packageDirectoryOptions)[number]["value"];
                    setPackageDirMode(nextMode);
                    if (nextMode !== "custom") {
                      setPackageDir(nextMode);
                    }
                  }}
                >
                  {packageDirectoryOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>

              {packageDirMode === "custom" ? (
                <label>
                  <span>自定义目录相对路径</span>
                  <input
                    value={packageDir}
                    onChange={(event) => setPackageDir(event.target.value)}
                    placeholder="例如: shared-models/yolov8"
                  />
                </label>
              ) : null}

              <label>
                <span>目标模型文件名 (选填)</span>
                <input
                  value={modelId}
                  onChange={(event) => setModelId(event.target.value)}
                  placeholder="例如: yolov8n.onnx，留空则校验目录下全部文件"
                />
              </label>

              <div>
                <span style={{ fontSize: 13, color: "#5b6778", fontWeight: 500, display: "flex", alignItems: "center", gap: 6 }}>
                  <Sliders size={14} /> 校验规则开关
                </span>
                <div className="check-pill-strip" style={{ marginTop: 8 }}>
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={strictHash}
                      onChange={(event) => setStrictHash(event.target.checked)}
                    />
                    <span>文件哈希比对</span>
                  </label>
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={strictExamples}
                      onChange={(event) => setStrictExamples(event.target.checked)}
                    />
                    <span>样例推理测试</span>
                  </label>
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={strictOnnx}
                      onChange={(event) => setStrictOnnx(event.target.checked)}
                    />
                    <span>ONNX 算子集格式</span>
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
                  <span style={{ fontSize: 12.5, color: "#8a99ad" }}>就绪：选择规则后点击开始执行校验</span>
                )}
              </div>
              <button className="primary-button" onClick={runValidation} disabled={busy}>
                <Play size={16} />
                <span>{busy ? "校验中…" : "开始执行校验"}</span>
              </button>
            </div>
          </section>

          {/* 右侧：实时诊断卡片 */}
          <section className="panel">
            <div className="panel-header">
              <div className="panel-header-left">
                <ShieldCheck size={18} color="#176b87" />
                <h1>校验实时诊断报告</h1>
              </div>
              {result ? <StatusBadge ok={result.ok} /> : <span className="panel-count-tag">等待触发</span>}
            </div>

            {result ? (
              <div>
                <div className="summary-line">
                  <StatusBadge ok={result.ok} />
                  <Tooltip content={result.model_file ?? result.package_dir}>
                    <span className="cell-ellipsis mono" style={{ fontWeight: 600, maxWidth: "260px" }}>
                      {result.model_file ?? result.package_dir}
                    </span>
                  </Tooltip>
                  {result.sha256 ? (
                    <Tooltip content={`完整哈希: ${result.sha256}`}>
                      <span className="mono" style={{ color: "#64748b", cursor: "pointer" }}>
                        哈希: {result.sha256.slice(0, 12)}…
                      </span>
                    </Tooltip>
                  ) : null}
                </div>

                <div className="issue-list" style={{ marginTop: 16 }}>
                  {result.issues.map((issue) => (
                    <div className="issue" key={`${issue.code}-${issue.path}`}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <AlertTriangle size={15} color="#b91c1c" />
                        <strong>{zhIssue(issue.code)}</strong>
                      </div>
                      <span>{zhIssueDetail(issue)}</span>
                    </div>
                  ))}
                  {!result.issues.length ? (
                    <div className="empty-row" style={{ color: "#166534", background: "#f0fdf4", borderRadius: 6 }}>
                      <CheckCircle2 size={24} color="#16a34a" />
                      <strong style={{ marginTop: 4 }}>所有合规检查项完全通过</strong>
                      <span style={{ fontSize: 12.5, color: "#15803d" }}>模型文件结构完整，哈希与元数据契约完全匹配。</span>
                    </div>
                  ) : null}
                </div>
              </div>
            ) : (
              <div className="empty-row" style={{ minHeight: 220 }}>
                <FileCheck size={32} color="#94a3b8" />
                <strong style={{ marginTop: 6, color: "#475569" }}>等待执行校验</strong>
                <span>请在左侧选择目录或模型文件，点击「开始执行校验」查看详细结果。</span>
              </div>
            )}
          </section>
        </div>
      ) : (
        /* Tab 2: 模型仓库全量扫描库（全边框表格、统一42px行高、截断悬浮、统一26px按钮、分页） */
        <section className="panel">
          <div className="panel-header">
            <div className="panel-header-left">
              <Boxes size={18} color="#176b87" />
              <h1>模型仓库全量扫描库</h1>
              <span className="panel-count-tag">共 {packages.length} 项</span>
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
                  placeholder="按模型名称过滤…"
                />
              </div>
              <button className="icon-button" onClick={onRefresh} aria-label="重新扫描模型仓库">
                <RotateCw size={16} />
              </button>
            </div>
          </div>

          <div className="ui-table-container">
            <table className="ui-table">
              <thead>
                <tr>
                  <th style={{ width: "60px", textAlign: "center" }}>序号</th>
                  <th style={{ width: "35%" }}>模型文件名 / 所在目录</th>
                  <th style={{ width: "16%" }}>交付合规状态</th>
                  <th style={{ width: "33%" }}>合规诊断详情</th>
                  <th style={{ width: "16%", textAlign: "center" }}>操作动作</th>
                </tr>
              </thead>
              <tbody>
                {paginatedPackages.map((item, index) => {
                  const displayName = item.model_file ?? item.package_dir;
                  const issueText = item.issues.length ? zhIssue(item.issues[0].code) : "完全符合规范";
                  const seqNumber = (currentPage - 1) * pageSize + index + 1;
                  return (
                    <tr key={displayName}>
                      <td style={{ textAlign: "center", color: "#64748b", fontWeight: 500 }}>
                        {seqNumber}
                      </td>
                      <td>
                        <Tooltip content={displayName}>
                          <span className="cell-ellipsis mono">{displayName}</span>
                        </Tooltip>
                      </td>
                      <td>
                        <StatusBadge ok={item.ok} label={item.ok ? "校验通过" : "存在问题"} />
                      </td>
                      <td>
                        <Tooltip content={issueText}>
                          <span
                            className="cell-ellipsis"
                            style={{ color: item.issues.length ? "#b91c1c" : "#475569" }}
                          >
                            {issueText}
                          </span>
                        </Tooltip>
                      </td>
                      <td style={{ textAlign: "center" }}>
                        <button
                          type="button"
                          className="table-action-btn primary"
                          onClick={() => {
                            if (item.model_file) {
                              setModelId(item.model_file);
                            }
                            setActiveTab("verify");
                          }}
                        >
                          填入校验
                        </button>
                      </td>
                    </tr>
                  );
                })}
                {!paginatedPackages.length ? (
                  <tr>
                    <td colSpan={5} style={{ textAlign: "center", padding: "30px 12px", color: "#8a99ad" }}>
                      {filterQuery ? "未检索到匹配的模型包" : "当前仓库暂无模型包数据"}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>

            {/* 分页组件 */}
            {filteredPackages.length > 0 ? (
              <Pagination
                currentPage={currentPage}
                pageSize={pageSize}
                total={filteredPackages.length}
                onPageChange={setCurrentPage}
                onPageSizeChange={setPageSize}
              />
            ) : null}
          </div>
        </section>
      )}
    </div>
  );
}
