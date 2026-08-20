/** Run 生命周期状态判断（与后端 domain/run.py 的状态机一致）。 */

export const TERMINAL_STATUSES = ["succeeded", "failed", "cancelled"] as const;

/** 终态事件类型：收到后前端应主动关闭 SSE 流。

 * 注意：Ingestion Run 的成功终态事件是 ``result_committed``
 * （与 Run → SUCCEEDED 同事务提交，见切片 6 契约）；
 * ``run_completed`` 只出现在通用 RunService.complete_run 路径。
 */
export const TERMINAL_EVENT_TYPES = [
  "run_completed",
  "run_failed",
  "run_cancelled",
  "result_committed",
] as const;

/** 可请求取消的状态：终态与已在取消流程中的状态不可重复取消。 */
const CANCELLABLE_STATUSES = ["queued", "running", "retry_wait"] as const;

export function isTerminal(status: string): boolean {
  return (TERMINAL_STATUSES as readonly string[]).includes(status);
}

export function isCancellable(status: string): boolean {
  return (CANCELLABLE_STATUSES as readonly string[]).includes(status);
}

export function isTerminalEventType(eventType: string): boolean {
  return (TERMINAL_EVENT_TYPES as readonly string[]).includes(eventType);
}

/** 状态的中文展示文案。 */
export function statusLabel(status: string): string {
  switch (status) {
    case "queued":
      return "排队中";
    case "running":
      return "运行中";
    case "retry_wait":
      return "等待重试";
    case "cancel_requested":
      return "取消中";
    case "succeeded":
      return "成功";
    case "failed":
      return "失败";
    case "cancelled":
      return "已取消";
    default:
      return status;
  }
}
