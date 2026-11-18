import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  testMatch: "stack.spec.ts",
  workers: 1,
  outputDir: "../.buildsize/stack-browser-artifacts",
  retries: 0,
  timeout: 30000,
  reporter: [
    ["list"],
    ["junit", { outputFile: "../.buildsize/stack-browser.xml" }],
  ],
  use: {
    baseURL: process.env.BUILDSIZE_BROWSER_ORIGIN,
    headless: true,
    viewport: { width: 1440, height: 1000 },
    launchOptions: { executablePath: process.env.BUILDSIZE_BROWSER_EXECUTABLE },
    trace: "off",
  },
});
