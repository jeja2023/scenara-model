import {
  Boxes,
  Database,
  FlaskConical,
  LayoutDashboard,
  LogOut,
  PlaySquare,
  RefreshCw,
  UserRound
} from "lucide-react";
import type { ReactNode } from "react";
import brandMark from "../assets/scenara-mark.svg";
import { Tooltip } from "./Tooltip";

export type ViewKey = "overview" | "packages" | "pipeline" | "experiments" | "data";

const navItems: Array<{ key: ViewKey; label: string; title: string; icon: ReactNode }> = [
  { key: "overview", label: "概览", title: "平台运行概览", icon: <LayoutDashboard size={18} /> },
  { key: "packages", label: "模型包", title: "模型包交付校验与管理", icon: <Boxes size={18} /> },
  { key: "pipeline", label: "流水线", title: "训练流水线与任务调度", icon: <PlaySquare size={18} /> },
  { key: "experiments", label: "实验", title: "实验记录与知识库台账", icon: <FlaskConical size={18} /> },
  { key: "data", label: "数据标注", title: "数据样本清单与交付契约", icon: <Database size={18} /> }
];

type ShellProps = {
  activeView: ViewKey;
  onViewChange: (view: ViewKey) => void;
  onRefresh: () => void;
  apiStatus: string;
  username: string;
  onLogout: () => void;
  children: ReactNode;
};

export function Shell({ activeView, onViewChange, onRefresh, apiStatus, username, onLogout, children }: ShellProps) {
  const currentNav = navItems.find((item) => item.key === activeView) ?? navItems[0];
  const isOnline = apiStatus.includes("在线");

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <img className="brand-mark" src={brandMark} alt="景枢模型平台" />
          <div>
            <strong>scenara model</strong>
            <span>景枢模型平台</span>
          </div>
        </div>
        <nav className="nav-list" aria-label="主要导航">
          {navItems.map((item) => (
            <button
              key={item.key}
              className={activeView === item.key ? "nav-item active" : "nav-item"}
              onClick={() => onViewChange(item.key)}
            >
              {item.icon}
              <span>{item.label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span>景枢模型平台</span>
          <span>v1.0.0</span>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="topbar-left">
            <div className="topbar-breadcrumb">
              <span className="platform-name">模型平台</span>
              <span className="separator">/</span>
              <span className="current-page-title">{currentNav.title}</span>
            </div>
            <div className="status-line">
              <span className={`pulse-dot ${isOnline ? "" : "error"}`} aria-hidden="true" />
              <span>{apiStatus}</span>
            </div>
          </div>
          <div className="topbar-actions">
            <Tooltip content="当前登录操作账号" placement="bottom">
              <span className="current-user">
                <UserRound size={16} />
                <span>{username}</span>
              </span>
            </Tooltip>
            <Tooltip content="刷新平台全部数据" placement="bottom">
              <button className="icon-button" onClick={onRefresh} aria-label="刷新">
                <RefreshCw size={17} />
              </button>
            </Tooltip>
            <Tooltip content="退出当前登录会话" placement="bottom">
              <button className="icon-button" onClick={onLogout} aria-label="退出登录">
                <LogOut size={17} />
              </button>
            </Tooltip>
          </div>
        </header>
        <section className="content">{children}</section>
      </main>
    </div>
  );
}
