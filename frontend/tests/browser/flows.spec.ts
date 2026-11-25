import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
const fixture = JSON.parse(
  readFileSync(process.env.BUILDSIZE_BROWSER_FIXTURE!, "utf8"),
);
async function login(
  page: import("@playwright/test").Page,
  username = "admin",
) {
  await page.goto("/login");
  await page.getByLabel("用户名", { exact: true }).fill(username);
  await page
    .getByLabel("密码", { exact: true })
    .fill(
      username === "viewer"
        ? process.env.BUILDSIZE_BROWSER_VIEWER_PASSWORD!
        : process.env.BUILDSIZE_BROWSER_PASSWORD!,
    );
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "选择项目", exact: true }),
  ).toBeVisible();
}
test("登录、浏览器授权上传、异步分析、比较、证据、复核与设置", async ({
  page,
}) => {
  await login(page);
  const csrf = await page.request
    .get("/api/v1/auth/me")
    .then((r) => r.json())
    .then((v) => v.csrf_token);
  const headers = {
    "X-CSRF-Token": csrf,
    Origin: process.env.BUILDSIZE_BROWSER_ORIGIN!,
  };
  const initialized = await page.request.post(
    "/api/v1/projects/demo-game/builds",
    {
      headers: { ...headers, "Idempotency-Key": fixture.manifest.run_id },
      data: fixture.manifest,
    },
  );
  expect(initialized.status()).toBe(201);
  const id = (await initialized.json()).build_id;
  for (const artifact of fixture.manifest.artifacts) {
    const response = await page.request.put(
      `/api/v1/builds/${id}/artifacts/${artifact.role}`,
      {
        headers: {
          ...headers,
          "Content-Type": "application/octet-stream",
          "X-Content-SHA256": artifact.sha256,
        },
        data: readFileSync(fixture.paths[artifact.role]),
      },
    );
    expect(response.status()).toBe(201);
  }
  const sealed = await page.request.post(`/api/v1/builds/${id}/finalize`, {
    headers,
    data: {},
  });
  expect(sealed.status()).toBe(202);
  await expect
    .poll(
      async () =>
        (await page.request.get(`/api/v1/builds/${id}`).then((r) => r.json()))
          .analysis_state,
      { timeout: 30000 },
    )
    .toBe("PARTIAL");
  const built = await page.request
    .get(`/api/v1/builds/${id}`)
    .then((r) => r.json());
  await page.goto(`/builds/${id}`);
  await expect(
    page.getByRole("heading", { name: "构建 #browser-target" }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "APK 文件", exact: true }).click();
  await expect(page.getByText(fixture.path, { exact: true })).toBeVisible();
  expect(
    await page.evaluate(() => Reflect.get(globalThis, "__xss")),
  ).toBeUndefined();
  expect(await page.locator("img").count()).toBe(0);
  await page.getByRole("button", { name: /下\s*页/ }).click();
  await expect(page.getByText("第 2 页", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/offset=25/);
  await page.getByRole("tab", { name: "Unity 资源", exact: true }).click();
  await expect(
    page.getByText(
      "Unity packed bytes：独立序列化指标，不能与 APK 压缩增量相加。",
    ),
  ).toBeVisible();
  await page.getByRole("button", { name: "查看版本差异", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "看清这次变化" }),
  ).toBeVisible();
  const baselineHeader = page
    .locator(".page-head p")
    .filter({ hasText: "基线构建" });
  await expect(
    baselineHeader.getByRole("link", {
      name: "构建 #browser-base",
      exact: true,
    }),
  ).toBeVisible();
  const targetHeader = page
    .locator(".page-head p")
    .filter({ hasText: "目标构建" });
  await expect(
    targetHeader.getByRole("link", {
      name: "构建 #browser-target",
      exact: true,
    }),
  ).toHaveAttribute("href", `/builds/${id}`);
  await expect(targetHeader).toContainText(built.metadata.version_name);
  await expect(targetHeader).toContainText(built.metadata.branch);
  await expect(
    page
      .getByText("APK 压缩贡献（互斥类别与容器开销）", { exact: true })
      .first(),
  ).toBeVisible();
  await page.getByRole("tab", { name: "原因与证据", exact: true }).click();
  await expect(
    page.getByText("数据层或来源证据不完整", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "查看证据", exact: true })
    .first()
    .click();
  await expect(
    page
      .getByText("证据 1 · apk", { exact: true })
      .or(page.getByText("证据 1 · derived_analysis", { exact: true })),
  ).toBeVisible();
  await page.getByRole("button", { name: "确认发现", exact: true }).click();
  await expect(page.getByText("已确认", { exact: true }).last()).toBeVisible();
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await page.goto("/projects/demo-game/settings");
  await page.getByLabel("净增长阈值", { exact: true }).fill("1");
  await page.getByRole("button", { name: "保存规则", exact: true }).click();
  await expect(
    page.getByText("配置已保存；旧比较保留其冻结版本。", { exact: true }),
  ).toBeVisible();
  await page.goto("/projects/demo-game");
  await expect(
    page.getByRole("heading", { name: "测试项目", exact: true }),
  ).toBeVisible();
  await page.getByRole("combobox", { name: "基线构建", exact: true }).click();
  await page
    .getByText("#browser-base · android-release-arm64", { exact: true })
    .filter({ visible: true })
    .click();
  const targetSelect = page.getByRole("combobox", {
    name: "目标构建",
    exact: true,
  });
  await targetSelect.click();
  await targetSelect.press("Enter");
  await expect(page).toHaveURL(new RegExp("target=" + id));
  await page.getByRole("button", { name: "创建比较", exact: true }).click();
  await expect(page.getByText("基线选择理由", { exact: true })).toBeVisible({
    timeout: 15000,
  });
  await expect(
    baselineHeader.getByRole("link", {
      name: "构建 #browser-base",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    targetHeader.getByRole("link", {
      name: "构建 #browser-target",
      exact: true,
    }),
  ).toBeVisible();
  await page.goto("/projects/demo-game");
  await expect(
    page.getByRole("heading", { name: "测试项目", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: process.env.BUILDSIZE_BROWSER_SCREENSHOT,
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("heading", { name: "测试项目", exact: true }),
  ).toBeVisible();
  // 图表使用 ResizeObserver，需等待其处理视口变化；仍严格检查无页面横向溢出。
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    )
    .toBe(true);
  await page.goto(
    `/comparisons/${built.result.comparison_id}?tab=findings&confidence=hypothesis`,
  );
  await expect(targetHeader).toContainText("构建 #browser-target");
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    )
    .toBe(true);
  await expect(
    page.getByText("待验证假设", { exact: true }).first(),
  ).toBeVisible();
});
test("viewer 只读、跨项目隐藏及无基线/错误空态", async ({ page }) => {
  await login(page, "viewer");
  await page.goto("/projects/demo-game/settings");
  await expect(
    page.getByText("只读：只有项目管理员可修改规则及查看审计。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "保存规则", exact: true }),
  ).toBeDisabled();
  await page.goto(`/builds/${fixture.foreign}`);
  await expect(
    page.getByText("资源不存在或无项目权限", { exact: true }),
  ).toBeVisible();
  await page.goto(`/comparisons/${fixture.first_comparison}`);
  await expect(
    page.getByText("首版无基线：可以查看当前包，增量保持未知。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.locator(".page-head p").filter({ hasText: "基线构建" }),
  ).toContainText("无基线");
  await expect(
    page
      .locator(".page-head p")
      .filter({ hasText: "目标构建" })
      .getByRole("link", { name: "构建 #browser-base", exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "原因与证据", exact: true }).click();
  await page
    .getByRole("button", { name: "查看证据", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("button", { name: "确认发现", exact: true }),
  ).toBeDisabled();
});
