import {
  useRef,
  type KeyboardEvent,
  type ReactNode,
} from "react";

export type AgentInspectorTab = "evidence" | "browser" | "outputs";

const tabs: Array<{ id: AgentInspectorTab; label: string; index: string }> = [
  { id: "evidence", label: "证据", index: "01" },
  { id: "browser", label: "浏览器", index: "02" },
  { id: "outputs", label: "成果", index: "03" },
];

export function nextAgentInspectorTab(
  current: AgentInspectorTab,
  key: string,
): AgentInspectorTab | null {
  if (key === "Home") return tabs[0].id;
  if (key === "End") return tabs[tabs.length - 1].id;
  if (key !== "ArrowLeft" && key !== "ArrowRight") return null;
  const direction = key === "ArrowRight" ? 1 : -1;
  const currentIndex = tabs.findIndex((tab) => tab.id === current);
  return tabs[(currentIndex + direction + tabs.length) % tabs.length].id;
}

interface AgentInspectorProps {
  evidence: ReactNode;
  browser: ReactNode;
  outputs: ReactNode;
  activeTab: AgentInspectorTab;
  onTabChange: (tab: AgentInspectorTab) => void;
  onClose: () => void;
}

type AgentInspectorViewProps = AgentInspectorProps;

export function AgentInspectorView({
  activeTab,
  onTabChange,
  evidence,
  browser,
  outputs,
  onClose,
}: AgentInspectorViewProps) {
  const tabRefs = useRef<Record<AgentInspectorTab, HTMLButtonElement | null>>({
    evidence: null,
    browser: null,
    outputs: null,
  });
  const panels: Record<AgentInspectorTab, ReactNode> = { evidence, browser, outputs };
  const activeLabel = tabs.find((tab) => tab.id === activeTab)?.label ?? "检查器";

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const next = nextAgentInspectorTab(activeTab, event.key);
    if (!next) return;
    event.preventDefault();
    onTabChange(next);
    tabRefs.current[next]?.focus();
  };

  return (
    <aside className="agent-inspector" aria-label="研究 Turn 检查器">
      <header className="agent-inspector-header">
        <div>
          <p className="eyebrow">TURN INSPECTOR</p>
          <h2>{activeLabel}</h2>
        </div>
        <button
          type="button"
          className="inspector-close"
          aria-label="关闭检查器"
          onClick={onClose}
        >
          <span aria-hidden="true">×</span>
        </button>
      </header>
      <div className="agent-inspector-tabs" role="tablist" aria-label="研究 Turn 信息">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            id={`agent-inspector-tab-${tab.id}`}
            ref={(element) => { tabRefs.current[tab.id] = element; }}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.id}
            aria-controls={`agent-inspector-panel-${tab.id}`}
            tabIndex={activeTab === tab.id ? 0 : -1}
            onClick={() => onTabChange(tab.id)}
            onKeyDown={handleKeyDown}
          >
            <span className="mono" aria-hidden="true">{tab.index}</span>
            {tab.label}
          </button>
        ))}
      </div>
      {tabs.map((tab) => (
        <div
          key={tab.id}
          className="agent-inspector-panel"
          id={`agent-inspector-panel-${tab.id}`}
          role="tabpanel"
          aria-labelledby={`agent-inspector-tab-${tab.id}`}
          tabIndex={activeTab === tab.id ? 0 : -1}
          hidden={activeTab !== tab.id}
        >
          {panels[tab.id]}
        </div>
      ))}
    </aside>
  );
}

export default function AgentInspector(props: AgentInspectorProps) {
  return <AgentInspectorView {...props} />;
}
