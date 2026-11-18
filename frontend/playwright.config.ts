import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests/browser",
  workers: 1,
  retries: 0,
  timeout: 45000,
  reporter: [
    ["list"],
    ["junit", { outputFile: "../.buildsize/browser-results.xml" }],
  ],
  use: {
    baseURL: process.env.BUILDSIZE_BROWSER_ORIGIN,
    headless: true,
    actionTimeout: 10000,
    viewport: { width: 1440, height: 1000 },
    launchOptions: { executablePath: process.env.BUILDSIZE_BROWSER_EXECUTABLE },
    screenshot: "only-on-failure",
    trace: "off",
  },
});
