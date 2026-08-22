import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  use: {
    actionTimeout: 20_000,
    baseURL:
      process.env.MOLIGHT_BROWSER_HA_URL ?? "http://homeassistant:8123",
    navigationTimeout: 30_000,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
  expect: {
    timeout: 15_000,
  },
  outputDir: process.env.MOLIGHT_BROWSER_ARTIFACT_DIR ?? "/artifacts",
  reporter: "line",
});
