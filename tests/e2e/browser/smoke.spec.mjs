import { expect, test } from "@playwright/test";

const USERNAME = "molight-e2e";
const PASSWORD = "molight-e2e-only";
const LIGHT_NAME = "Browser Scheduled";
const EDITED_LIGHT_NAME = "Browser Scheduled Edited";
const LIGHT_ENTITY_ID = "light.browser_scheduled";

async function login(page) {
  await page.goto("/");
  const username = page.getByRole("textbox", { name: "Username" });
  await expect(username).toBeVisible();
  await username.fill(USERNAME);
  await page.getByRole("textbox", { name: "Password" }).fill(PASSWORD);
  await page.getByRole("button", { name: /log in/i }).click();

  await expect(page.locator("home-assistant")).toBeVisible();
}

async function openAddFlow(page) {
  await page.goto("/config/integrations/dashboard/add?domain=molight");
  const confirmation = page.getByRole("heading", {
    name: "Do you want to set up MoLight?",
  });
  await expect(confirmation).toBeVisible();
  await page.getByRole("button", { name: "OK", exact: true }).click();
  await expect(page.getByText("Add MoLight entities", { exact: true }).last()).toBeVisible();
}

async function expectFlowTitle(page, title) {
  await expect(page.getByText(title, { exact: true }).last()).toBeVisible();
}

async function clickFlowChoice(page, name) {
  await page.getByText(name, { exact: true }).click();
}

async function selectHaOption(page, label, option) {
  const field = page.getByLabel(label, { exact: true });
  await field.click();
  await page.getByText(option, { exact: true }).last().click();
}

async function selectHaEntity(page, fieldIndex, entityName) {
  await page
    .locator("ha-selector-entity")
    .nth(fieldIndex)
    .getByRole("listitem")
    .click();
  const option = page.getByText(entityName, { exact: true }).last();
  await expect(option).toBeVisible();
  await option.click();
}

async function submit(page) {
  await page.getByRole("button", { name: /submit|next/i }).click();
}

async function fillTimeout(page, seconds) {
  const timeout = page.getByRole("spinbutton", { name: /^Turn-off timeout/ });
  await timeout.fill(String(seconds));
}

async function createScheduledLight(page) {
  await openAddFlow(page);
  await clickFlowChoice(page, "Create a single entity");
  await selectHaOption(page, "Entity type", "Virtual Scheduled Light");
  await submit(page);

  await expectFlowTitle(page, "Virtual Scheduled Light");
  await page.getByRole("textbox", { name: /^Name/ }).fill(LIGHT_NAME);
  await selectHaEntity(page, 0, "E2E Main Light");
  await selectHaEntity(page, 1, "E2E Schedule");
  await submit(page);

  await expectFlowTitle(page, "Outside-schedule settings");
  await fillTimeout(page, 31);
  await submit(page);

  await expectFlowTitle(page, "Inside-schedule settings");
  await fillTimeout(page, 62);
  await submit(page);

  await expect(page.getByText(/created configuration for Browser Scheduled/i)).toBeVisible();
  await page.getByRole("button", { name: /finish/i }).click();
}

async function entryCard(page, name) {
  await page.goto("/config/integrations/integration/molight");
  const card = page.locator("ha-config-entry-row").filter({ hasText: name });
  await expect(card).toBeVisible();
  return card;
}

async function editScheduledLight(page) {
  const card = await entryCard(page, LIGHT_NAME);
  await card.getByRole("button", { name: /configure/i }).click();

  await expectFlowTitle(page, "Virtual Scheduled Light");
  await page.getByRole("textbox", { name: /^Name/ }).fill(EDITED_LIGHT_NAME);
  await submit(page);
  await expectFlowTitle(page, "Outside-schedule settings");
  await fillTimeout(page, 32);
  await submit(page);
  await expectFlowTitle(page, "Inside-schedule settings");
  await fillTimeout(page, 63);
  await submit(page);
  await expect(page.getByText(/options successfully saved/i)).toBeVisible();
  await page.getByRole("button", { name: /finish/i }).click();
  await expect((await entryCard(page, EDITED_LIGHT_NAME))).toBeVisible();
}

async function convert(page, menuChoice, entityLabel) {
  await openAddFlow(page);
  await clickFlowChoice(page, "Convert virtual lights");
  await clickFlowChoice(page, menuChoice);
  const light = page.getByRole("checkbox", {
    name: new RegExp(entityLabel, "i"),
  });
  await expect(light).toBeVisible();
  await light.check();
  await submit(page);
  const confirmationLabel = "I understand and want to convert these lights";
  const confirmation = page.getByRole("checkbox", {
    name: confirmationLabel,
    exact: true,
  });
  await page.getByText(confirmationLabel, { exact: true }).last().click();
  await expect(confirmation).toBeChecked();
  await submit(page);
  await expect(page.getByText(/converted 1 virtual light/i)).toBeVisible();
  await page.getByRole("button", { name: "Close", exact: true }).last().click();
}

async function entityState(page, entityId) {
  return page.evaluate(async (id) => {
    const tokens = JSON.parse(localStorage.getItem("hassTokens"));
    const response = await fetch(`/api/states/${id}`, {
      headers: { Authorization: `Bearer ${tokens.access_token}` },
    });
    if (!response.ok) {
      throw new Error(`Entity request failed: ${response.status}`);
    }
    return response.json();
  }, entityId);
}

test("create, edit, and round-trip-convert a scheduled light", async ({ page }) => {
  await login(page);
  await createScheduledLight(page);
  await editScheduledLight(page);

  await convert(
    page,
    "Convert scheduled lights to gated lights",
    EDITED_LIGHT_NAME,
  );
  await convert(
    page,
    "Convert gated lights to scheduled lights",
    EDITED_LIGHT_NAME,
  );

  const state = await entityState(page, LIGHT_ENTITY_ID);
  expect(state.entity_id).toBe(LIGHT_ENTITY_ID);
  expect(state.attributes.friendly_name).toBe(EDITED_LIGHT_NAME);
  expect(state.attributes.active_settings).toBeDefined();
  const card = await entryCard(page, EDITED_LIGHT_NAME);
  await expect(card).toBeVisible();
});
