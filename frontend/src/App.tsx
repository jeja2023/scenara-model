import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  clearSessionToken,
  errorMessage,
  getHealth,
  getMe,
  getSessionToken,
  listExperiments,
  listPackageValidations,
  listPipelineRuns,
  logout,
  scanPackages
} from "./api";
import { Shell, type ViewKey } from "./components/Shell";
import { DataLabeling } from "./pages/DataLabeling";
import { Experiments } from "./pages/Experiments";
import { Login } from "./pages/Login";
import { Overview } from "./pages/Overview";
import { Packages } from "./pages/Packages";
import { Pipeline } from "./pages/Pipeline";
import type { ExperimentRecord, Health, PackageValidation, PackageValidationRecord, PipelineRunRecord } from "./types";

function dedupeExperiments(records: ExperimentRecord[], index: ExperimentRecord[]): ExperimentRecord[] {
  // DB 记录优先，index.yml 仅补充 DB 中不存在的实验，避免同 id 重复渲染。
  const merged = new Map<string, ExperimentRecord>();
  for (const record of index) {
    merged.set(record.id, record);
  }
  for (const record of records) {
    merged.set(record.id, record);
  }
  return Array.from(merged.values());
}

export function App() {
  const [activeView, setActiveView] = useState<ViewKey>("overview");
  const [health, setHealth] = useState<Health>();
  const [packages, setPackages] = useState<PackageValidation[]>([]);
  const [validations, setValidations] = useState<PackageValidationRecord[]>([]);
  const [experiments, setExperiments] = useState<ExperimentRecord[]>([]);
  const [pipelineRuns, setPipelineRuns] = useState<PipelineRunRecord[]>([]);
  const [apiStatus, setApiStatus] = useState("接口连接中");
  // undefined = 会话恢复中；"" = 未登录；非空 = 已登录用户名。
  const [currentUser, setCurrentUser] = useState<string | undefined>(undefined);

  const handleUnauthorized = useCallback(() => {
    clearSessionToken();
    setCurrentUser("");
  }, []);

  const refresh = useCallback(async () => {
    // allSettled：单个端点失败不影响其他数据更新。
    const [healthResult, packageResult, validationResult, experimentResult, pipelineResult] = await Promise.allSettled([
      getHealth(),
      scanPackages(),
      listPackageValidations(),
      listExperiments(),
      listPipelineRuns()
    ]);
    // 任一请求 401 说明会话已过期/被撤销，回到登录页。
    const results = [healthResult, packageResult, validationResult, experimentResult, pipelineResult];
    if (results.some((result) => result.status === "rejected" && result.reason instanceof ApiError && result.reason.status === 401)) {
      handleUnauthorized();
      return;
    }
    if (healthResult.status === "fulfilled") {
      setHealth(healthResult.value);
      setApiStatus("接口在线");
    } else {
      setApiStatus(errorMessage(healthResult.reason));
    }
    if (packageResult.status === "fulfilled") {
      setPackages(packageResult.value.packages);
    }
    if (validationResult.status === "fulfilled") {
      setValidations(validationResult.value.validations);
    }
    if (experimentResult.status === "fulfilled") {
      setExperiments(dedupeExperiments(experimentResult.value.experiments, experimentResult.value.index));
    }
    if (pipelineResult.status === "fulfilled") {
      setPipelineRuns(pipelineResult.value.runs);
    }
  }, [handleUnauthorized]);

  useEffect(() => {
    // 启动时用本地令牌恢复会话；无令牌或令牌失效则进入登录页。
    if (!getSessionToken()) {
      setCurrentUser("");
      return;
    }
    getMe()
      .then((meResult) => setCurrentUser(meResult.username))
      .catch(() => handleUnauthorized());
  }, [handleUnauthorized]);

  useEffect(() => {
    if (currentUser) {
      void refresh();
    }
  }, [currentUser, refresh]);

  const handleLogout = useCallback(async () => {
    try {
      await logout();
    } catch {
      // 会话可能已失效，本地令牌已在 logout() 中清除。
    }
    setCurrentUser("");
  }, []);

  if (currentUser === undefined) {
    return null; // 会话恢复中，避免登录页一闪而过。
  }

  if (!currentUser) {
    return <Login onLoggedIn={(session) => setCurrentUser(session.username)} />;
  }

  return (
    <Shell
      activeView={activeView}
      onViewChange={setActiveView}
      onRefresh={refresh}
      apiStatus={apiStatus}
      username={currentUser}
      onLogout={handleLogout}
    >
      {activeView === "overview" ? (
        <Overview
          health={health}
          packages={packages}
          validations={validations}
          experiments={experiments}
          pipelineRuns={pipelineRuns}
          onNavigate={setActiveView}
        />
      ) : null}
      {activeView === "packages" ? <Packages packages={packages} onRefresh={refresh} /> : null}
      {activeView === "pipeline" ? <Pipeline runs={pipelineRuns} onRefresh={refresh} /> : null}
      {activeView === "experiments" ? <Experiments experiments={experiments} onRefresh={refresh} /> : null}
      {activeView === "data" ? <DataLabeling /> : null}
    </Shell>
  );
}
