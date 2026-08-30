import { expect, test, type Page } from "@playwright/test";

const reviewQuestion = "离线研究助手如何复用项目证据并保持多轮上下文？";

async function createProject(page: Page) {
  await page.goto("/");
  // 空态时创建 Modal 自动打开，否则点击幽灵卡展开
  const nameInput = page.getByLabel("项目名称");
  if (!(await nameInput.isVisible())) {
    await page.getByRole("button", { name: "新建项目" }).click();
  }
  await nameInput.fill("E2E Phase 5 Agent");
  await page.getByLabel("研究说明 可选").fill("Phase 5 离线 Agent Chat UI 验收项目");
  await page.getByRole("button", { name: "创建 Project" }).click();
  await page.getByRole("link", { name: /E2E Phase 5 Agent/ }).click();
}

async function completeReview(page: Page) {
  await page
    .getByRole("navigation", { name: "应用导航" })
    .getByRole("link", { name: "综述", exact: true })
    .click();
  await page.getByLabel("研究问题").fill(reviewQuestion);
  await page.getByRole("button", { name: "开始文献综述" }).click();
  await expect(page.getByText("等待大纲确认")).toBeVisible({ timeout: 45_000 });
  await page.getByRole("button", { name: "批准服务端当前大纲" }).click();
  await expect(page.getByText("Review 已完成")).toBeVisible({ timeout: 45_000 });
}

test("离线 Research Agent 完成配置、两轮恢复与候选成果展示", async ({ page }) => {
  test.setTimeout(150_000);
  const pageErrors: string[] = [];
  const externalRequests: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname === "127.0.0.1" || url.hostname === "localhost") {
      await route.continue();
      return;
    }
    externalRequests.push(url.href);
    await route.abort("blockedbyclient");
  });

  await createProject(page);
  await completeReview(page);
  await page
    .getByRole("navigation", { name: "应用导航" })
    .getByRole("link", { name: "研究助手", exact: true })
    .click();
  await expect(page.getByRole("heading", { name: "研究会话" })).toBeVisible();
  await page.getByLabel("新会话标题").fill("证据综合会话");
  await page.getByRole("button", { name: "新建研究会话" }).click();
  await expect(page).toHaveURL(/\/agent\/[0-9a-f-]+$/);

  await page.getByText("研究能力", { exact: true }).click();
  const skillSection = page.getByRole("heading", { name: "研究方法" }).locator("..");
  await skillSection.getByRole("checkbox").first().check();
  await page.getByRole("button", { name: "保存能力配置" }).click();
  await expect(page.getByText(/研究方法已锁定/)).toHaveCount(0);

  await page.getByLabel("本轮 Evidence Matrix").selectOption({ index: 1 });
  await page.getByLabel("研究消息").fill("请综合这些证据并指出主要研究缺口。");
  await page.getByRole("button", { name: "开始本轮研究" }).click();
  await expect(page.getByText("当前授权上下文证据不足。")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("tab", { name: /成果/ }).click();
  await page.getByText("内部候选 · 1", { exact: true }).click();
  const candidateArtifact = page.getByText("research-note.md");
  await candidateArtifact.scrollIntoViewIfNeeded();
  await expect(candidateArtifact).toBeVisible();
  await expect(candidateArtifact).toBeInViewport();
  await expect(page.getByText("staged", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: /证据/ }).click();
  await expect(page.getByText(/本轮索引快照 · \d+ 篇文献/)).toBeVisible();

  await page.reload();
  await expect(page.getByText("请综合这些证据并指出主要研究缺口。")).toBeVisible();
  await expect(page.getByText("当前授权上下文证据不足。")).toBeVisible();
  await expect(page.getByLabel("本轮 Evidence Matrix")).toHaveValue("");
  await expect(page.getByLabel("本轮 Evidence Matrix").locator("option:checked")).toHaveText("沿用上一轮 Evidence Matrix");
  await expect(page.getByText("研究方法已锁定 · 每条消息创建独立 Turn")).toBeVisible();

  await page.getByLabel("研究消息").fill("继续比较不同方法，并给出下一步研究建议。");
  await page.getByRole("button", { name: "开始本轮研究" }).click();
  await expect(page.getByText("继续比较不同方法，并给出下一步研究建议。")).toBeVisible();
  await expect(page.locator(".message-assistant")).toHaveCount(2, { timeout: 30_000 });
  await page.getByRole("tab", { name: /成果/ }).click();
  await page.getByText("内部候选 · 1", { exact: true }).click();
  await expect(page.getByText("research-note.md")).toBeVisible();

  expect(externalRequests).toEqual([]);
  expect(pageErrors).toEqual([]);
});
