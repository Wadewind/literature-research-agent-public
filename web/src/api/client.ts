/** 最小 fetch 封装：统一错误形状与界面提示文案。 */

export class ApiError extends Error {
  /** HTTP 错误，携带状态码与后端 detail。 */
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(`${status}: ${detail}`);
    this.name = "ApiError";
  }
}

const BUSINESS_ERROR_MESSAGES: Record<string, string> = {
  conversation_busy: "当前对话正在生成回答，请稍后再试",
  project_not_indexed: "文献索引尚未就绪，请等待索引完成后再提问",
  invalid_scope: "提问范围无效，请重新选择当前项目中的文献",
  project_archived: "项目已归档，当前为只读状态",
  paper_archived: "文献已归档，请先恢复后再操作",
  project_has_active_runs: "项目仍有运行中的任务，请等待完成或先取消任务",
  conversation_not_found: "资源不存在或无权访问",
  evidence_not_found: "资源不存在或无权访问",
  review_cannot_cancel: "Review 当前状态不能取消，请刷新后查看最新状态",
  agent_session_not_found: "研究会话不存在或无权访问",
  agent_turn_not_found: "研究 Turn 不存在或无权访问",
  agent_session_busy: "当前会话已有研究任务在执行，请等待完成或先停止本轮",
  agent_browser_control_active: "请先在浏览器面板完成或结束人工操作",
  browser_control_not_found: "浏览器控制已结束、过期或无权访问",
  browser_control_unavailable: "当前会话没有可接管的浏览器，或浏览器正被其他视图控制",
  review_output_not_found: "所选 Evidence Matrix 不存在或不属于当前项目",
  mcp_profile_revision_conflict: "研究能力配置已变化，请刷新后重新选择",
  mcp_profile_invalid: "研究能力配置无效，请检查所填参数",
  skill_profile_locked: "首轮研究已经开始；更换研究方法需要新建会话",
  skill_revision_conflict: "研究方法配置已变化，请刷新后重新选择",
  skill_configuration_invalid: "研究方法与当前允许的能力不兼容",
};

/** 读取响应错误体中的 detail 字段（FastAPI 错误格式）。 */
async function readDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string") return detail;
      return JSON.stringify(detail);
    }
  } catch {
    // 忽略非 JSON 错误体
  }
  return response.statusText || "请求失败";
}

/** 发起 JSON API 请求；非 2xx 抛 ApiError。 */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    throw new ApiError(response.status, await readDetail(response));
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** 正式 Artifact 内容地址；Candidate 没有可下载 URL。 */
export function agentArtifactContentUrl(artifactId: string): string {
  return `/api/v1/agent-artifacts/${encodeURIComponent(artifactId)}/content`;
}

/** noVNC 只连接平台同源 WebSocket，不接收 Provider endpoint。 */
export function browserControlWebSocketUrl(viewPath: string): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${viewPath}`;
}

/** 把错误映射为面向用户的可见提示。 */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (BUSINESS_ERROR_MESSAGES[error.detail]) {
      return BUSINESS_ERROR_MESSAGES[error.detail];
    }
    if (error.status === 404) return "资源不存在或无权访问";
    if (error.status === 409) return `请求冲突：${error.detail}`;
    if (error.status === 400) return `请求被拒绝：${error.detail}`;
    if (error.status === 413) return "文件超过大小限制";
    return `请求失败（${error.status}）：${error.detail}`;
  }
  if (error instanceof Error) return `网络或客户端错误：${error.message}`;
  return "未知错误";
}
