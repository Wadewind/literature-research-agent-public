import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  apiFetch,
  browserControlWebSocketUrl,
  errorMessage,
} from "../api/client";
import type {
  BrowserControl,
  BrowserControlStart,
  BrowserControlStatus,
} from "../api/types";

interface BrowserViewerProps {
  ticket: string;
  viewUrl: string;
  onState: (state: "connecting" | "connected" | "disconnected" | "failed") => void;
}

function BrowserViewer({ ticket, viewUrl, onState }: BrowserViewerProps) {
  const targetRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const target = targetRef.current;
    if (!target) return undefined;
    let disposed = false;
    let disconnect: (() => void) | undefined;
    onState("connecting");
    void import("@novnc/novnc").then(({ default: RFB }) => {
      if (disposed) return;
      const rfb = new RFB(target, browserControlWebSocketUrl(viewUrl), {
        shared: false,
        wsProtocols: ["binary", `browser-ticket.${ticket}`],
      });
      rfb.scaleViewport = true;
      rfb.resizeSession = false;
      rfb.viewOnly = false;
      rfb.focusOnClick = true;
      rfb.addEventListener("connect", () => onState("connected"));
      rfb.addEventListener("disconnect", () => onState("disconnected"));
      rfb.addEventListener("securityfailure", () => onState("failed"));
      rfb.addEventListener("credentialsrequired", () => onState("failed"));
      disconnect = () => rfb.disconnect();
    }).catch(() => onState("failed"));
    return () => {
      disposed = true;
      disconnect?.();
    };
  }, [onState, ticket, viewUrl]);

  return (
    <div
      ref={targetRef}
      className="agent-browser-canvas"
      role="application"
      aria-label="当前研究会话的交互式浏览器画面"
      tabIndex={0}
    />
  );
}

interface AgentBrowserPanelViewProps {
  control: BrowserControl | null;
  activeTurn: boolean;
  ticket: string | null;
  viewUrl: string | null;
  viewerState: "idle" | "connecting" | "connected" | "disconnected" | "failed";
  pending: boolean;
  error: string | null;
  onStart: () => void;
  onEnd: () => void;
  onViewerState: BrowserViewerProps["onState"];
}

export function AgentBrowserPanelView({
  control,
  activeTurn,
  ticket,
  viewUrl,
  viewerState,
  pending,
  error,
  onStart,
  onEnd,
  onViewerState,
}: AgentBrowserPanelViewProps) {
  const manual = control?.status === "active";
  const stateLabel = activeTurn
    ? "Agent 操作中"
    : manual
      ? "人工控制"
      : "等待接管";
  return (
    <section className="agent-browser-panel" aria-labelledby="agent-browser-title">
      <header>
        <div>
          <p className="eyebrow">SAME CHROMIUM · TURN BOUNDARY</p>
          <h3 id="agent-browser-title">浏览器</h3>
        </div>
        <span className={`browser-mode browser-mode-${activeTurn ? "agent" : manual ? "manual" : "idle"}`}>
          {stateLabel}
        </span>
      </header>
      {activeTurn ? (
        <p className="muted">本轮结束后可接管同一浏览器；人工与 Agent 不会同时操作。</p>
      ) : ticket && viewUrl && manual ? (
        <>
          <BrowserViewer ticket={ticket} viewUrl={viewUrl} onState={onViewerState} />
          <div className="agent-browser-status" aria-live="polite">
            {viewerState === "connecting" && "正在连接画面…"}
            {viewerState === "connected" && "已连接。可点击画面并完成登录或页面操作。"}
            {viewerState === "disconnected" && "画面已断开；控制权仍保留，可重新连接。"}
            {viewerState === "failed" && "画面连接失败；控制权仍保留，可结束后重试。"}
          </div>
          <button type="button" className="button-secondary" disabled={pending} onClick={onEnd}>
            {pending ? "正在结束…" : "完成操作"}
          </button>
        </>
      ) : (
        <>
          <p className="muted">
            {manual
              ? "控制权仍有效。刷新页面后可重新连接当前 generation。"
              : "先让 Agent 打开目标页面，再在两个研究 Turn 之间接管。"}
          </p>
          <button type="button" className="button-secondary" disabled={pending} onClick={onStart}>
            {pending ? "正在准备…" : manual ? "重新连接" : "开始接管"}
          </button>
        </>
      )}
      {control?.status === "expired" && (
        <p className="notice">
          {control.end_reason === "sandbox_generation_changed"
            ? "浏览器环境已换代，旧 generation 的画面票据已失效。"
            : control.end_reason === "ticket_signing_key_changed"
              ? "服务重启后旧画面票据已失效，请重新申请。"
              : "控制权已过期。Sandbox generation 未改变时可重新申请。"}
        </p>
      )}
      {error && <p className="error-text" role="alert">{error}</p>}
    </section>
  );
}

interface AgentBrowserPanelProps {
  sessionId: string;
  activeTurnRunId: string | null;
}

export default function AgentBrowserPanel({
  sessionId,
  activeTurnRunId,
}: AgentBrowserPanelProps) {
  const queryClient = useQueryClient();
  const [ticket, setTicket] = useState<string | null>(null);
  const [viewUrl, setViewUrl] = useState<string | null>(null);
  const [viewerState, setViewerState] = useState<AgentBrowserPanelViewProps["viewerState"]>("idle");
  const controlQuery = useQuery({
    queryKey: ["agent-browser-control", sessionId],
    queryFn: () => apiFetch<BrowserControlStatus>(
      `/api/v1/agent-sessions/${sessionId}/browser-control`,
    ),
    enabled: Boolean(sessionId),
    refetchInterval: (query) => query.state.data?.control?.status === "active" ? 3_000 : false,
  });
  const startMutation = useMutation({
    mutationFn: () => apiFetch<BrowserControlStart>(
      `/api/v1/agent-sessions/${sessionId}/browser-control`,
      { method: "POST" },
    ),
    onSuccess: (result) => {
      setTicket(result.ticket);
      setViewUrl(result.view_url);
      setViewerState("connecting");
      queryClient.setQueryData<BrowserControlStatus>(
        ["agent-browser-control", sessionId],
        { control: result.control },
      );
    },
  });
  const endMutation = useMutation({
    mutationFn: () => apiFetch<BrowserControl>(
      `/api/v1/agent-sessions/${sessionId}/browser-control`,
      { method: "DELETE" },
    ),
    onSuccess: (control) => {
      setTicket(null);
      setViewUrl(null);
      setViewerState("idle");
      queryClient.setQueryData<BrowserControlStatus>(
        ["agent-browser-control", sessionId],
        { control },
      );
      void queryClient.invalidateQueries({ queryKey: ["agent-session", sessionId] });
    },
  });
  const control = controlQuery.data?.control ?? null;
  useEffect(() => {
    if (control?.status !== "active") {
      setTicket(null);
      setViewUrl(null);
      setViewerState("idle");
    }
  }, [control?.status]);

  return (
    <AgentBrowserPanelView
      control={control}
      activeTurn={Boolean(activeTurnRunId)}
      ticket={ticket}
      viewUrl={viewUrl}
      viewerState={viewerState}
      pending={startMutation.isPending || endMutation.isPending}
      error={
        controlQuery.isError || startMutation.isError || endMutation.isError
          ? errorMessage(controlQuery.error ?? startMutation.error ?? endMutation.error)
          : null
      }
      onStart={() => startMutation.mutate()}
      onEnd={() => endMutation.mutate()}
      onViewerState={setViewerState}
    />
  );
}
