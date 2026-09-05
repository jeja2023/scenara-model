import type { ReactNode } from "react";

export type TabItem = {
  key: string;
  label: string;
  icon?: ReactNode;
  badge?: string | number;
};

type TabBarProps = {
  tabs: TabItem[];
  activeKey: string;
  onChange: (key: string) => void;
  className?: string;
};

export function TabBar({ tabs, activeKey, onChange, className = "" }: TabBarProps) {
  return (
    <div className={`tab-bar-container ${className}`} role="tablist">
      <div className="tab-bar">
        {tabs.map((tab) => {
          const isActive = tab.key === activeKey;
          return (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={isActive}
              className={`tab-btn ${isActive ? "active" : ""}`}
              onClick={() => onChange(tab.key)}
            >
              {tab.icon ? <span className="tab-icon">{tab.icon}</span> : null}
              <span className="tab-label">{tab.label}</span>
              {tab.badge !== undefined && tab.badge !== "" ? (
                <span className={`tab-badge ${isActive ? "active" : ""}`}>
                  {tab.badge}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}
