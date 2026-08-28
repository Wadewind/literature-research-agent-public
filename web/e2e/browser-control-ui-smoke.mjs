import { chromium } from "playwright";

const [url, marker] = process.argv.slice(2);
if (!url || !marker) {
  throw new Error("用法: node browser-control-ui-smoke.mjs <url> <marker>");
}

const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
const browser = await chromium.launch({
  headless: true,
  ...(executablePath ? { executablePath } : { channel: "chrome" }),
});
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const consoleMessages = [];
  page.on("console", (message) => consoleMessages.push(`${message.type()}: ${message.text()}`));
  page.on("pageerror", (error) => consoleMessages.push(`pageerror: ${error.message}`));
  await page.goto(url);
  try {
    await page.getByText("已连接。可点击画面并完成登录或页面操作。").waitFor({
      state: "visible",
      timeout: 20_000,
    });
  } catch (error) {
    const body = await page.locator("body").innerText().catch(() => "<body unavailable>");
    throw new Error(
      `BrowserViewer 未连接：${error.message}\nbody=${body}\nconsole=${consoleMessages.join(" | ")}`,
    );
  }
  const viewer = page.getByRole("application", {
    name: "当前研究会话的交互式浏览器画面",
  });
  await viewer.locator("canvas").waitFor({ state: "visible", timeout: 10_000 });
  const canvas = viewer.locator("canvas");
  const box = await canvas.boundingBox();
  if (!box) throw new Error("noVNC canvas 没有可点击区域");
  // 合成页使用覆盖整个 viewport 且 autofocus 的 input；画面内任一点都确定命中该 input。
  await canvas.click({ position: { x: box.width / 2, y: box.height / 2 } });
  await page.keyboard.type(marker);
  await page.getByRole("button", { name: "完成操作" }).click();
  await page.getByRole("status").filter({ hasText: "人工操作已结束" }).waitFor();
  process.stdout.write(JSON.stringify({ injected: marker, manualControlEnded: true }));
} finally {
  await browser.close();
}
