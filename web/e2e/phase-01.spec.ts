import { expect, test } from "@playwright/test";
import path from "node:path";

const fixture = path.resolve("../backend/tests/fixtures/pdfs/text_two_pages.pdf");
const filename = "text_two_pages.pdf";

async function createProject(page: import("@playwright/test").Page, name: string) {
  await page.goto("/");
  // 空态时创建 Modal 自动打开，否则点击幽灵卡展开
  const nameInput = page.getByLabel("项目名称");
  if (!(await nameInput.isVisible())) {
    await page.getByRole("button", { name: "新建项目" }).click();
  }
  await nameInput.fill(name);
  await page.getByLabel("研究说明 可选").fill("Phase 1 Playwright 验收项目");
  await page.getByRole("button", { name: "创建 Project" }).click();
  const project = page.getByRole("link", { name: new RegExp(name) });
  await expect(project).toBeVisible();
  await project.click();
}

test("新文献解析、跨 Project 复用和收录关系形成完整闭环", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await createProject(page, "E2E 主项目");
  await page.locator('input[type="file"]').setInputFiles(fixture);
  await page.getByRole("button", { name: "导入到当前项目" }).click();
  await expect(page.getByText("已创建导入任务")).toBeVisible();
  await page.getByRole("link", { name: "查看进度" }).click();

  await expect(page.getByText("成功", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("result_committed", { exact: true })).toBeVisible();

  await page.reload();
  await expect(page.getByText("成功", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "查看文档结构预览" }).click();
  await expect(page.getByText(/8 个 Element/)).toBeVisible();
  await page.locator(".element").first().click();
  await expect(page.getByText(/已定位到第 1 页/)).toBeVisible();

  await createProject(page, "E2E 复用项目");
  await page.locator('input[type="file"]').setInputFiles(fixture);
  await page.getByRole("button", { name: "导入到当前项目" }).click();
  await expect(page.getByText("已复用完成的解析结果")).toBeVisible();
  await expect(page.locator(".project-paper-list").getByText(filename)).toBeVisible();

  await page.getByRole("button", { name: "移出项目" }).click();
  await expect(page.locator(".project-paper-list")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "+收录" })).toBeVisible();

  await page.goto("/library");
  await expect(page.getByRole("heading", { name: filename })).toBeVisible();
  await expect(page.getByRole("link", { name: "E2E 主项目" })).toBeVisible();
  await expect(page.getByRole("link", { name: "E2E 复用项目" })).toHaveCount(0);

  await page.goBack();
  await page.getByRole("button", { name: "+收录" }).click();
  await expect(page.locator(".project-paper-list").getByText(filename)).toBeVisible();
  expect(pageErrors).toEqual([]);
});
