/** API 客户端错误处理测试：ApiError 解析与界面提示映射。 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch, errorMessage } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
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

  it("413 映射为大小限制提示", () => {
    expect(errorMessage(new ApiError(413, ""))).toBe("文件超过大小限制");
  });

  it("普通 Error 与未知错误有兜底文案", () => {
    expect(errorMessage(new Error("boom"))).toContain("boom");
    expect(errorMessage("weird")).toBe("未知错误");
  });
});
