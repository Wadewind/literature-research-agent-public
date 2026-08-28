import { useState } from "react";
import { createRoot } from "react-dom/client";

import { AgentBrowserPanelView } from "../src/components/AgentBrowserPanel";
import type { BrowserControl } from "../src/api/types";
import "../src/styles.css";

const params = new URLSearchParams(window.location.search);
const ticket = params.get("ticket");
const viewUrl = params.get("viewUrl");

if (!ticket || !viewUrl) {
  throw new Error("Browser control smoke 缺少 ticket/viewUrl");
}

const activeControl: BrowserControl = {
  session_id: "browser-smoke-session",
  project_id: "browser-smoke-project",
  anchor_turn_run_id: "browser-smoke-turn",
  sandbox_generation: 1,
  revision: 1,
  status: "active",
  mode: "manual",
  expires_at: new Date(Date.now() + 60_000).toISOString(),
  ended_at: null,
  end_reason: null,
};

function BrowserControlHarness() {
  const [ended, setEnded] = useState(false);
  const [viewerState, setViewerState] = useState<
    "idle" | "connecting" | "connected" | "disconnected" | "failed"
  >("idle");

  if (ended) {
    return <p role="status">人工操作已结束</p>;
  }
  return (
    <AgentBrowserPanelView
      control={activeControl}
      activeTurn={false}
      ticket={ticket}
      viewUrl={viewUrl}
      viewerState={viewerState}
      pending={false}
      error={null}
      onStart={() => undefined}
      onEnd={() => setEnded(true)}
      onViewerState={setViewerState}
    />
  );
}

createRoot(document.getElementById("root")!).render(<BrowserControlHarness />);
