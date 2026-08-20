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

/** 把错误映射为面向用户的可见提示。 */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 404) return "资源不存在或无权访问";
    if (error.status === 409) return `请求冲突：${error.detail}`;
    if (error.status === 400) return `请求被拒绝：${error.detail}`;
    if (error.status === 413) return "文件超过大小限制";
    return `请求失败（${error.status}）：${error.detail}`;
  }
  if (error instanceof Error) return `网络或客户端错误：${error.message}`;
  return "未知错误";
}
