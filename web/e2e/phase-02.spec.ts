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

test("应用侧栏支持有界调整、偏好恢复与窄屏图标栏", async ({ page }) => {
  await page.goto("/library");
  const resizeHandle = page.getByRole("separator", { name: "调整侧栏宽度" });

  await expect(resizeHandle).toHaveAttribute("aria-valuenow", "232");
  await resizeHandle.focus();
  await resizeHandle.press("ArrowRight");
  await expect(resizeHandle).toHaveAttribute("aria-valuenow", "240");

  await page.reload();
  await expect(resizeHandle).toHaveAttribute("aria-valuenow", "240");
  const handleBox = await resizeHandle.boundingBox();
  if (!handleBox) throw new Error("侧栏宽度控制不可见");
  await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + 180);
  await page.mouse.down();
  await page.mouse.move(handleBox.x + handleBox.width / 2 + 24, handleBox.y + 180);
  await page.mouse.up();
  await expect(resizeHandle).toHaveAttribute("aria-valuenow", "264");

  await page.reload();
  await expect(resizeHandle).toHaveAttribute("aria-valuenow", "264");
  await resizeHandle.press("End");
  await expect(resizeHandle).toHaveAttribute("aria-valuenow", "288");
  await resizeHandle.press("ArrowRight");
  await expect(resizeHandle).toHaveAttribute("aria-valuenow", "288");
  await resizeHandle.press("Home");
  await expect(resizeHandle).toHaveAttribute("aria-valuenow", "216");
  await resizeHandle.dblclick();
  await expect(resizeHandle).toHaveAttribute("aria-valuenow", "232");

  await page.getByRole("button", { name: "收起侧栏" }).click();
  await expect(resizeHandle).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("button", { name: "展开侧栏" })).toBeVisible();
  await page.getByRole("button", { name: "展开侧栏" }).click();
  await expect(resizeHandle).toHaveAttribute("aria-valuenow", "232");

  await page.setViewportSize({ width: 880, height: 720 });
  await expect(resizeHandle).toBeHidden();
  await expect(page.locator(".app-sidebar")).toHaveCSS("width", "56px");
  await page.setViewportSize({ width: 1280, height: 720 });
  await expect(resizeHandle).toBeVisible();
});

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
  const starterButton = page.getByRole("button", { name: /当前范围内的文献采用了哪些核心方法/ });
  await starterButton.click();
  const scopeDialog = page.getByRole("dialog", { name: "确认检索边界" });
  await expect(scopeDialog).toBeVisible();
  await expect(scopeDialog.getByText("当前范围内的文献采用了哪些核心方法？")).toBeVisible();
  await scopeDialog.getByRole("checkbox").first().check();
  await scopeDialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(scopeDialog).not.toBeVisible();
  await expect(starterButton).toBeFocused();
  await expect(page.getByPlaceholder("提出一个需要文献证据回答的问题…")).toHaveValue(
    "当前范围内的文献采用了哪些核心方法？",
  );
  await page.getByRole("button", { name: "创建问答", exact: true }).click();
  await expect(scopeDialog).toBeVisible();
  await expect(scopeDialog.getByRole("checkbox").first()).not.toBeChecked();
  await scopeDialog.getByRole("button", { name: "确认并创建问答" }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]+\/chat\/[0-9a-f-]+$/);
  await expect(page.getByText(/CITED RAG \/ 整个项目/)).toBeVisible();

  const questionInput = page.getByPlaceholder("提出一个需要文献证据回答的问题…");
  await expect(questionInput).toHaveValue("当前范围内的文献采用了哪些核心方法？");
  await questionInput.fill("fake 论文讲了什么？");
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
  await page.getByPlaceholder("提出一个需要文献证据回答的问题…").fill("这篇论文使用了哪些实验设置？");
  await page.getByRole("button", { name: /创建问答/ }).click();
  await expect(scopeDialog).toBeVisible();
  await expect(scopeDialog.getByRole("checkbox").first()).toBeChecked();
  await scopeDialog.getByRole("button", { name: "确认并创建问答" }).click();
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
