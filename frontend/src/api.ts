import type {
  ExperimentRecord,
  Health,
  AdapterInfo,
  AuditEvent,
  ContractValidation,
  ErrorAnalysis,
  ManifestValidation,
  PackageValidation,
  PackageValidationRecord,
  PipelineArtifact,
  PipelineJobLog,
  PipelineJobRecord,
  PipelineRunRecord,
  DatasetVersion,
  DeploymentRollout,
  ModelRegistryEntry,
  ReleaseApproval
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const API_TOKEN = import.meta.env.VITE_API_TOKEN ?? "";

const TOKEN_STORAGE_KEY = "scenara-model_token";

export function getSessionToken(): string {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setSessionToken(token: string) {
  try {
    localStorage.setItem(TOKEN_STORAGE_KEY, token);
  } catch {
    // 隐私模式等场景下 localStorage 不可用，令牌仅存在于本次会话内存中。
  }
}

export function clearSessionToken() {
  try {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // 同上，忽略不可用的存储。
  }
}

function authToken(): string {
  // 登录会话令牌优先；VITE_API_TOKEN 仅作为无登录部署的兜底。
  return getSessionToken() || API_TOKEN;
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function errorMessage(error: unknown) {
  if (error instanceof ApiError) {
    return `请求失败（${error.status}）：${error.message}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "请求失败";
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const token = authToken();
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options?.headers ?? {})
    },
    ...options
  });
  if (!response.ok) {
    const text = await response.text();
    // FastAPI 错误体为 {"detail": ...}，提取出来给用户更友好的提示。
    let message = text || `HTTP ${response.status}`;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (typeof parsed.detail === "string") {
        message = parsed.detail;
      }
    } catch {
      // 非 JSON 错误体，保留原文。
    }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

export type LoginResponse = { token: string; username: string; role: string; expires_at: string };
export type MeResponse = { username: string; role: string; expires_at: string | null };

export async function login(username: string, password: string) {
  const result = await request<LoginResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password })
  });
  setSessionToken(result.token);
  return result;
}

export async function logout() {
  try {
    await request<{ ok: boolean }>("/api/auth/logout", { method: "POST" });
  } finally {
    clearSessionToken();
  }
}

export function getMe() {
  return request<MeResponse>("/api/auth/me");
}

export function getHealth() {
  return request<Health>("/health");
}

export function scanPackages(root = "shared-models") {
  return request<{ root: string; packages: PackageValidation[] }>(`/api/packages/scan?root=${encodeURIComponent(root)}`);
}

export function validatePackage(payload: {
  package_dir: string;
  model_id?: string;
  strict_hash: boolean;
  strict_examples: boolean;
  strict_onnx: boolean;
}) {
  return request<{ validation: PackageValidation | PackageValidationRecord }>("/api/packages/validate", {
    method: "POST",
    body: JSON.stringify({ ...payload, persist: true })
  });
}

export function listPackageValidations() {
  return request<{ validations: PackageValidationRecord[] }>("/api/package-validations");
}

export function validateManifest(path: string) {
  return request<{ manifest: ManifestValidation }>("/api/manifests/validate", {
    method: "POST",
    body: JSON.stringify({ path })
  });
}

export function validateContract(kind: "models-fragment" | "release-decision", path: string) {
  return request<{ contract: ContractValidation }>("/api/contracts/validate", {
    method: "POST",
    body: JSON.stringify({ kind, path })
  });
}

export function listExperiments() {
  return request<{ experiments: ExperimentRecord[]; index: ExperimentRecord[] }>("/api/experiments");
}

export function saveExperiment(record: ExperimentRecord) {
  return request<{ experiment: ExperimentRecord }>("/api/experiments", {
    method: "POST",
    body: JSON.stringify(record)
  });
}

export function listAdapters() {
  return request<{ adapters: AdapterInfo[] }>("/api/adapters");
}

export function runPipeline(payload: { config_path: string; package: boolean; output_root?: string; async_run?: boolean }) {
  return request<{ run?: PipelineRunRecord; job?: PipelineJobRecord }>("/api/pipelines/run", {
    method: "POST",
    body: JSON.stringify({
      config_path: payload.config_path,
      package: payload.package,
      output_root: payload.output_root ?? "shared-models",
      persist: true,
      async: payload.async_run ?? false
    })
  });
}

export function listPipelineRuns() {
  return request<{ runs: PipelineRunRecord[] }>("/api/pipelines/runs");
}

export function listPipelineJobs() {
  return request<{ jobs: PipelineJobRecord[] }>("/api/pipelines/jobs");
}

export function getPipelineJob(jobId: number) {
  return request<{ job: PipelineJobRecord }>(`/api/pipelines/jobs/${jobId}`);
}

export function cancelPipelineJob(jobId: number) {
  return request<{ job: PipelineJobRecord }>(`/api/pipelines/jobs/${jobId}/cancel`, { method: "POST" });
}

export function listPipelineJobLogs(jobId: number, options?: { sinceId?: number; tail?: boolean; limit?: number }) {
  const params = new URLSearchParams();
  if (options?.sinceId !== undefined) {
    params.set("since_id", String(options.sinceId));
  }
  if (options?.tail) {
    params.set("tail", "true");
  }
  if (options?.limit) {
    params.set("limit", String(options.limit));
  }
  const query = params.toString();
  return request<{ logs: PipelineJobLog[] }>(`/api/pipelines/jobs/${jobId}/logs${query ? `?${query}` : ""}`);
}

export function artifactDownloadUrl(artifactId: number) {
  return `${API_BASE}/api/pipelines/artifacts/${artifactId}/download`;
}

export async function downloadArtifact(artifactId: number, filename: string) {
  // 下载接口现在也要求认证，普通 <a href> 不会携带 Authorization 头，
  // 因此以 fetch 拉取 blob 后再触发浏览器保存。
  const token = authToken();
  const response = await fetch(artifactDownloadUrl(artifactId), {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined
  });
  if (!response.ok) {
    throw new ApiError(response.status, await response.text());
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function listPipelineJobArtifacts(jobId: number) {
  return request<{ artifacts: PipelineArtifact[] }>(`/api/pipelines/jobs/${jobId}/artifacts`);
}

export function retryPipelineJob(jobId: number) {
  return request<{ job: PipelineJobRecord }>(`/api/pipelines/jobs/${jobId}/retry`, { method: "POST" });
}

export function createPackage(payload: { config_path: string; onnx_path?: string; project_name?: string; output_root?: string }) {
  return request<{ package: { artifact_name: string; validation: PackageValidation } }>("/api/packages/create", {
    method: "POST",
    body: JSON.stringify({ ...payload, output_root: payload.output_root ?? "shared-models", overwrite: true })
  });
}

export function uploadArtifact(file: File, targetDir = "artifacts/uploads") {
  const form = new FormData();
  form.append("file", file);
  const token = authToken();
  return fetch(`${API_BASE}/api/uploads?target_dir=${encodeURIComponent(targetDir)}`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: form
  }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json() as Promise<{ upload: { path: string } }>;
  });
}

export function analyzeErrors(path: string) {
  return request<{ analysis: ErrorAnalysis }>("/api/error-analysis", {
    method: "POST",
    body: JSON.stringify({ path })
  });
}

export function listAuditEvents() {
  return request<{ events: AuditEvent[] }>("/api/audit-events");
}
export function listDatasetVersions() {
  return request<{ datasets: DatasetVersion[] }>("/api/datasets/versions");
}

export function listModelRegistry() {
  return request<{ models: ModelRegistryEntry[] }>("/api/models/registry");
}

export function listReleaseApprovals() {
  return request<{ approvals: ReleaseApproval[] }>("/api/releases/approvals");
}

export function listDeploymentRollouts() {
  return request<{ rollouts: DeploymentRollout[] }>("/api/deployments/rollouts");
}
