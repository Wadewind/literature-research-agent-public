import { expect, test } from "@playwright/test";
import path from "node:path";

const fixture = path.resolve("../backend/tests/fixtures/pdfs/blank.pdf");

async function createProject(page: import("@playwright/test").Page, name: string) {
  await page.goto("/");
  // 空态时创建 Modal 自动打开，否则点击幽灵卡展开
  const nameInput = page.getByLabel("项目名称");
  const createButton = page.getByRole("button", { name: "新建项目" });
  if (await createButton.getAttribute("aria-expanded") !== "true") {
    await createButton.click();
  }
  await expect(nameInput).toBeVisible();
  await nameInput.fill(name);
  await page.getByLabel("研究说明 可选").fill("Phase 2 Playwright 验收项目");
  await page.getByRole("button", { name: "创建 Project" }).click();
  const project = page.getByRole("link", { name: new RegExp(name) });
  await expect(project).toBeVisible();
  await project.click();
}

test("Project RAG、引用回跳、单篇范围与归档只读形成完整闭环", async ({ page }) => {
  test.setTimeout(90_000);
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await createProject(page, "E2E Phase 2 RAG");
  await page.locator('input[type="file"]').setInputFiles(fixture);
  await page.getByRole("button", { name: "导入到当前项目" }).click();
  await expect(page.getByText("已创建导入任务")).toBeVisible();
  await page.getByRole("link", { name: "查看进度" }).click();
  await expect(page.getByText("成功", { exact: true })).toBeVisible({ timeout: 30_000 });

  await page.getByRole("link", { name: "文献库", exact: true }).click();
  await expect(page.getByText("索引已就绪")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("link", { name: "询问整个 Project", exact: true }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]+\/chat$/);
  await page.getByRole("button", { name: /创建问答/ }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]+\/chat\/[0-9a-f-]+$/);
  await expect(page.getByText(/CITED RAG \/ 整个项目/)).toBeVisible();

  await page.getByPlaceholder("提出一个需要文献证据回答的问题…").fill("fake 论文讲了什么？");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("基于给定证据的回答（fake 模型确定性生成）。")).toBeVisible({ timeout: 30_000 });

  await page.reload();
  await expect(page.getByLabel("消息时间线").getByText("fake 论文讲了什么？")).toBeVisible();
  await expect(page.getByText("基于给定证据的回答（fake 模型确定性生成）。")).toBeVisible();
  await page.locator(".citation-marker").first().click();
  await expect(page.getByRole("heading", { name: "来源证据" })).toBeVisible();
  await expect(page.locator(".evidence-drawer blockquote")).not.toBeEmpty();
  await expect(page.getByTitle("Evidence 来源 PDF")).toHaveAttribute("src", /#page=\d+$/);

  await page
    .getByRole("navigation", { name: "应用导航" })
    .getByRole("link", { name: "文献库", exact: true })
    .click();
  await page.getByRole("button", { name: "询问此篇" }).click();
  await page.locator("summary").filter({ hasText: "选择证据范围" }).click();
  await expect(page.getByRole("checkbox").first()).toBeChecked();
  await page.getByRole("button", { name: /创建问答/ }).click();
  await expect(page.getByText(/CITED RAG \/ 1 篇文献/)).toBeVisible();

  await page
    .getByRole("navigation", { name: "应用导航" })
    .getByRole("link", { name: "文献库", exact: true })
    .click();
  await page.getByRole("button", { name: "归档 Project" }).click();
  await expect(page.getByText(/该 Project 当前只读/)).toBeVisible();
  await page
    .getByRole("navigation", { name: "应用导航" })
    .getByRole("link", { name: "文献问答", exact: true })
    .click();
  await expect(page.getByText(/历史问答仍可查看/)).toBeVisible();
  await expect(page.getByRole("button", { name: /创建问答/ })).toBeDisabled();
  expect(pageErrors).toEqual([]);
});
