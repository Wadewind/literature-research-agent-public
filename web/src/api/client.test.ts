/** API 客户端错误处理测试：ApiError 解析与界面提示映射。 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, agentArtifactContentUrl, apiFetch, errorMessage } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

it("为 Artifact ID 生成编码后的正式内容地址", () => {
  expect(agentArtifactContentUrl("artifact/一")).toBe(
    "/api/v1/agent-artifacts/artifact%2F%E4%B8%80/content",
  );
});

function mockResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText: "Error",
    headers: { "Content-Type": "application/json" },
  });
}

describe("apiFetch", () => {
  it("2xx 返回解析后的 JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => mockResponse(200, { ok: true })),
    );

    await expect(apiFetch<{ ok: boolean }>("/api/v1/x")).resolves.toEqual({ ok: true });
  });

  it("非 2xx 抛带状态码与 detail 的 ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => mockResponse(409, { detail: "幂等键冲突" })),
    );

    const error = await apiFetch("/api/v1/x").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).detail).toBe("幂等键冲突");
  });
});

describe("errorMessage", () => {
  it("404 映射为不泄漏所有权的提示", () => {
    expect(errorMessage(new ApiError(404, "Run 不存在"))).toBe("资源不存在或无权访问");
  });

  it("400/409 展示后端 detail", () => {
    expect(errorMessage(new ApiError(400, "仅接受 PDF 文件"))).toContain("仅接受 PDF 文件");
    expect(errorMessage(new ApiError(409, "冲突"))).toContain("冲突");
  });

  it.each([
    ["conversation_busy", "当前对话正在生成回答，请稍后再试"],
    ["project_not_indexed", "文献索引尚未就绪，请等待索引完成后再提问"],
    ["invalid_scope", "提问范围无效，请重新选择当前项目中的文献"],
    ["project_archived", "项目已归档，当前为只读状态"],
    ["paper_archived", "文献已归档，请先恢复后再操作"],
    ["review_cannot_cancel", "Review 当前状态不能取消，请刷新后查看最新状态"],
    ["agent_session_busy", "当前会话已有研究任务在执行，请等待完成或先停止本轮"],
    ["skill_profile_locked", "首轮研究已经开始；更换研究方法需要新建会话"],
  ])("稳定业务码 %s 映射为操作指引", (detail, expected) => {
    expect(errorMessage(new ApiError(detail === "invalid_scope" ? 422 : 409, detail))).toBe(
      expected,
    );
  });

  it("413 映射为大小限制提示", () => {
    expect(errorMessage(new ApiError(413, ""))).toBe("文件超过大小限制");
  });

  it("普通 Error 与未知错误有兜底文案", () => {
    expect(errorMessage(new Error("boom"))).toContain("boom");
    expect(errorMessage("weird")).toBe("未知错误");
  });
});
