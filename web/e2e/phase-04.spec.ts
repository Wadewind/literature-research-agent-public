import { expect, test, type Page, type Response } from "@playwright/test";
import { readFile } from "node:fs/promises";

const question = "可靠的文献综述 Workflow 如何处理证据、暂停恢复与部分来源失败？";
const expectedArtifacts = new Map([
  ["review markdown", ".md"],
  ["search strategy", ".json"],
  ["source manifest", ".json"],
  ["evidence matrix", ".json"],
  ["bibliography", ".json"],
  ["run summary", ".json"],
]);

type ExpectedNotReady404s = Map<string, number>;

function currentReviewReadUrls(page: Page): Set<string> {
  const pageUrl = new URL(page.url());
  const match = pageUrl.pathname.match(/^\/projects\/([0-9a-f-]+)\/reviews\/([0-9a-f-]+)$/);
  if (!match) return new Set();
  const [, projectId, runId] = match;
  const prefix = `${pageUrl.origin}/api/v1/projects/${projectId}/reviews/${runId}`;
  return new Set([`${prefix}/outline`, `${prefix}/evidence-matrix`]);
}

function trackExpectedNotReady404(page: Page, response: Response, expected404s: ExpectedNotReady404s) {
  if (response.status() !== 404 || response.request().method() !== "GET") return;
  if (!currentReviewReadUrls(page).has(response.url())) return;
  expected404s.set(response.url(), (expected404s.get(response.url()) ?? 0) + 1);
}

function collectCriticalConsoleError(
  message: import("@playwright/test").ConsoleMessage,
  errors: string[],
  expected404s: ExpectedNotReady404s,
) {
  if (message.type() !== "error") return;
  const locationUrl = message.location().url;
  const expectedCount = expected404s.get(locationUrl) ?? 0;
  // 只消费当前 Review 的 Outline/Matrix GET 404；其他 404 与所有脚本错误都必须进入失败列表。
  if (
    message.text().includes("Failed to load resource")
    && message.text().includes("404")
    && expectedCount > 0
  ) {
    if (expectedCount === 1) expected404s.delete(locationUrl);
    else expected404s.set(locationUrl, expectedCount - 1);
    return;
  }
  errors.push(message.text());
}

function observeBrowserErrors(page: Page, pageErrors: string[], consoleErrors: string[]) {
  const expected404s: ExpectedNotReady404s = new Map();
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("response", (response) => trackExpectedNotReady404(page, response, expected404s));
  page.on("console", (message) => collectCriticalConsoleError(message, consoleErrors, expected404s));
}

async function rejectExternalRequests(page: Page, externalRequests: string[]) {
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname === "127.0.0.1" || url.hostname === "localhost") {
      await route.continue();
      return;
    }
    externalRequests.push(url.href);
    await route.abort("blockedbyclient");
  });
}

async function createProject(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("项目名称").fill(name);
  await page.getByLabel("研究说明 可选").fill("Phase 4 离线 Review Playwright 验收项目");
  await page.getByRole("button", { name: "创建 Project" }).click();
  await page.getByRole("link", { name: new RegExp(name) }).click();
  return page.url().match(/\/projects\/([^/#]+)/)?.[1] ?? "";
}

async function createReview(page: Page, researchQuestion: string) {
  await page
    .getByRole("navigation", { name: "应用导航" })
    .getByRole("link", { name: "综述", exact: true })
    .click();
  await page.getByLabel("研究问题").fill(researchQuestion);
  await page.getByRole("button", { name: "开始文献综述" }).click();
  await expect(page).toHaveURL(/\/reviews\/[0-9a-f-]+$/);
  return page.url().match(/\/reviews\/([0-9a-f-]+)$/)?.[1] ?? "";
}

test("离线 Review 从部分来源失败、两轮 HITL 到六类 Artifact 可刷新恢复", async ({ page }) => {
  test.setTimeout(120_000);
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  const externalRequests: string[] = [];
  observeBrowserErrors(page, pageErrors, consoleErrors);
  await rejectExternalRequests(page, externalRequests);

  const projectId = await createProject(page, "E2E Phase 4 Review");
  expect(projectId).not.toBe("");
  const runId = await createReview(page, question);
  expect(runId).not.toBe("");

  await expect(page.getByText("等待大纲确认")).toBeVisible({ timeout: 45_000 });
  await expect(page.locator(".review-source-list > li")).toHaveCount(4);
  await expect(page.locator(".source-ready")).toHaveCount(3);
  await expect(page.locator(".source-failed")).toHaveCount(1);
  await expect(page.getByText("fake_arxiv_pdf_unavailable", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "结构化大纲" })).toBeVisible();
  await expect(page.locator(".outline-section-list > li")).not.toHaveCount(0);
  await expect(page.getByText("证据不足", { exact: true }).first()).toBeVisible();

  const eventResponse = await page.request.get(`/api/v1/runs/${runId}/events?limit=100`);
  expect(eventResponse.status()).toBe(200);
  const eventTypes = ((await eventResponse.json()) as Array<{ event_type: string }>).map((event) => event.event_type);
  expect(eventTypes).toContain("dependency_wait_started");
  expect(eventTypes).toContain("dependency_wait_completed");
  await page.getByText(/最近事件/).click();
  await expect(page.getByText("human_input_requested", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText("等待大纲确认")).toBeVisible();
  await expect(page.locator(".review-source-list > li")).toHaveCount(4);
  await expect(page.locator(".outline-version-row")).toContainText("outline.v1");

  await page.getByPlaceholder("说明希望补充、删减或重排的内容").fill("请突出失败恢复，并保持证据边界。");
  await page.getByRole("button", { name: "提交反馈并暂停等待新版本" }).click();
  await expect(page.locator(".outline-version-row")).toContainText("outline.v2", { timeout: 45_000 });
  await expect(page.locator(".outline-version-row")).toContainText("Request v2");
  await page.reload();
  await expect(page.locator(".outline-version-row")).toContainText("outline.v2");
  await expect(page.getByText("等待大纲确认")).toBeVisible();

  await page.getByRole("button", { name: "批准服务端当前大纲" }).click();
  await expect(page.getByText("Review 已完成")).toBeVisible({ timeout: 45_000 });
  await expect(page.getByText("成功", { exact: true }).first()).toBeVisible();
  await expect(page.locator(".review-section-stack article")).not.toHaveCount(0);
  await expect(page.getByText("引用已校验", { exact: true }).first()).toBeVisible();

  const evidenceButton = page.locator(".review-evidence-locator .evidence-chip").first();
  await evidenceButton.click();
  const pdfLink = page.getByRole("link", { name: "跳到 PDF 原页" }).first();
  await expect(pdfLink).toHaveAttribute(
    "href",
    new RegExp(`^/api/v1/projects/${projectId}/paper-versions/[0-9a-f-]+/file#page=\\d+$`),
  );
  const pdfHref = await pdfLink.getAttribute("href");
  expect(pdfHref).not.toBeNull();
  const pdfResponse = await page.request.get(pdfHref!.split("#")[0]);
  expect(pdfResponse.status()).toBe(200);
  expect(pdfResponse.headers()["content-type"]).toContain("application/pdf");
  const deniedHref = pdfHref!.replace(projectId, "00000000-0000-0000-0000-000000000000");
  expect((await page.request.get(deniedHref.split("#")[0])).status()).toBe(404);

  const artifactItems = page.locator(".artifact-list > li");
  await expect(artifactItems).toHaveCount(6);
  for (const [label, extension] of expectedArtifacts) {
    const item = artifactItems.filter({ hasText: label });
    await expect(item).toHaveCount(1);
    const downloadPromise = page.waitForEvent("download");
    await item.getByRole("link", { name: "下载" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(new RegExp(`${extension.replace(".", "\\.")}$`));
    const path = await download.path();
    expect(path).not.toBeNull();
    const content = await readFile(path!);
    expect(content.byteLength).toBeGreaterThan(10);
    if (extension === ".json") JSON.parse(content.toString("utf8"));
    if (extension === ".md") expect(content.toString("utf8")).toContain(question);
  }

  const artifactHref = await artifactItems.first().getByRole("link", { name: "下载" }).getAttribute("href");
  expect(artifactHref).not.toBeNull();
  expect(
    (await page.request.get(artifactHref!.replace(projectId, "00000000-0000-0000-0000-000000000000"))).status(),
  ).toBe(404);

  await page.reload();
  await expect(page.getByText("Review 已完成")).toBeVisible();
  await expect(page.locator(".review-source-list > li")).toHaveCount(4);
  await expect(page.locator(".review-section-stack article")).not.toHaveCount(0);
  await expect(page.locator(".artifact-list > li")).toHaveCount(6);
  expect(consoleErrors).toEqual([]);
  await page.evaluate(async () => {
    await fetch("/api/v1/e2e-unexpected-404");
  });
  await expect.poll(() => consoleErrors.length).toBe(1);
  expect(consoleErrors[0]).toContain("404");
  consoleErrors.length = 0;
  expect(externalRequests).toEqual([]);
  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
});

test("Review 取消从 UI 收敛为可刷新恢复的终态", async ({ page }) => {
  test.setTimeout(60_000);
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  const externalRequests: string[] = [];
  observeBrowserErrors(page, pageErrors, consoleErrors);
  await rejectExternalRequests(page, externalRequests);

  await createProject(page, "E2E Phase 4 Cancel");
  await createReview(page, "如何验证 Review 的协作式取消？");
  const cancel = page.getByRole("button", { name: "取消 Review" });
  await expect(cancel).toBeVisible();
  await cancel.click();
  await expect(page.getByText("Review 已取消")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("已取消", { exact: true }).first()).toBeVisible();
  await page.reload();
  await expect(page.getByText("Review 已取消")).toBeVisible();
  await expect(page.getByRole("button", { name: "取消 Review" })).toHaveCount(0);
  expect(externalRequests).toEqual([]);
  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
