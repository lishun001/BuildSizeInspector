import { test, expect } from "@playwright/test";
test("生产静态包同源登录、CSP、pin 和保留策略", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (e) => {
    if (/Content Security Policy|Refused to execute|unsafe-eval/.test(e.text()))
      errors.push(e.text());
  });
  await page.goto("/login");
  await page.getByLabel("用户名", { exact: true }).fill("stack-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.BUILDSIZE_BROWSER_PASSWORD!);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "选择项目", exact: true }),
  ).toBeVisible();
  await page.goto(`/builds/${process.env.BUILDSIZE_STACK_BUILD_ID}`);
  await expect(
    page.getByRole("heading", { name: "构建 #2", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "保留保护（pin）", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "取消保留保护", exact: true }),
  ).toBeVisible();
  await page.goto("/projects/stack-game/settings");
  await expect(
    page.getByText("保留策略（运行配置）", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(
      "所有 current 修订、固定基线、pin 和活跃任务/上传受保护；到期并不保证删除。",
      { exact: true },
    ),
  ).toBeVisible();
  await page.screenshot({
    path: "../.buildsize/production-web.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
