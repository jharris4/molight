import { expect, test } from "@playwright/test";

const USERNAME = "molight-e2e";
const PASSWORD = "molight-e2e-only";
const LIGHT_NAME = "Browser Scheduled";
const EDITED_LIGHT_NAME = "Browser Scheduled Edited";
const LIGHT_ENTITY_ID = "light.browser_scheduled";

test.describe.configure({ mode: "serial" });

async function login(page) {
  await page.goto("/");
  const username = page.getByRole("textbox", { name: "Username" });
  await expect(username).toBeVisible();
  await username.fill(USERNAME);
  await page.getByRole("textbox", { name: "Password" }).fill(PASSWORD);
  await page.getByRole("button", { name: /log in/i }).click();

  await expect(page.locator("home-assistant")).toBeVisible();
}

// Dialogs on some frontend versions keep hidden duplicates of their text; pick the last visible one.
function visibleText(page, text) {
  return page.getByText(text, { exact: true }).filter({ visible: true }).last();
}

async function openAddFlow(page) {
  // Loading /add?domain= directly makes the integrations page handle the
  // route twice (firstUpdated and updated) and stack two confirmation
  // dialogs, which the dialog manager then closes on its own on slower or
  // older frontends. Navigate in-app instead, as the frontend's navigate() does.
  await page.goto("/config/integrations/dashboard");
  await expect(page.getByRole("button", { name: "Add integration" })).toBeVisible();
  await page.evaluate(() => {
    history.pushState(null, "", "/config/integrations/dashboard/add?domain=molight");
    window.dispatchEvent(
      new CustomEvent("location-changed", {
        detail: { replace: false },
        bubbles: true,
        composed: true,
      }),
    );
  });
  await expect(visibleText(page, "Do you want to set up MoLight?")).toBeVisible();
  await page.getByRole("button", { name: "OK", exact: true }).click();
  await expect(visibleText(page, "Add MoLight entities")).toBeVisible();
}

async function expectFlowTitle(page, title) {
  await expect(visibleText(page, title)).toBeVisible();
}

async function clickFlowChoice(page, name) {
  await page.getByText(name, { exact: true }).click();
}

async function selectHaOption(page, label, option) {
  const field = page.getByLabel(label, { exact: true });
  await field.click();
  // Older frontends expose the menu entries as nameless options whose text
  // also appears in the closed select, so prefer the option role when present.
  const entries = page.getByRole("option").filter({ hasText: option }).filter({ visible: true });
  await expect(entries.or(visibleText(page, option)).first()).toBeVisible();
  if ((await entries.count()) > 0) {
    await entries.last().click();
  } else {
    await visibleText(page, option).click();
  }
}

async function selectHaEntity(page, fieldIndex, entityName) {
  await page
    .locator("ha-selector-entity")
    .nth(fieldIndex)
    .getByRole("listitem")
    .click();
  await visibleText(page, entityName).click();
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
  await visibleText(page, confirmationLabel).click();
  await expect(confirmation).toBeChecked();
  await submit(page);
  await expect(page.getByText(/converted 1 virtual light/i)).toBeVisible();
  await page.getByRole("button", { name: "Close", exact: true }).filter({ visible: true }).last().click();
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

async function sectionPanel(page, title) {
  const panel = page.locator("ha-expansion-panel").filter({ hasText: title }).first();
  await expect(panel).toBeVisible();
  if ((await panel.getAttribute("expanded")) === null) {
    await panel.getByText(title, { exact: true }).first().click();
    await expect(panel).toHaveAttribute("expanded", "");
    // Let the expand animation finish so the pickers inside stay put.
    await page.waitForTimeout(500);
  }
  return panel;
}

async function pickEntity(page, scope, label, entityName) {
  const picker = scope.locator("ha-selector-entity").filter({ hasText: label }).first();
  await expect(picker).toBeVisible();
  await picker.scrollIntoViewIfNeeded();
  // Multi-entity pickers list chosen entities first and an empty row last.
  await picker.getByRole("listitem").last().click();
  await visibleText(page, entityName).click();
}

async function finishCreated(page, name) {
  await expect(page.getByText(new RegExp(`created configuration for ${name}`, "i"))).toBeVisible();
  await page.getByRole("button", { name: /finish/i }).click();
}

async function closeAbort(page, pattern) {
  await expect(page.getByText(pattern)).toBeVisible();
  await page.getByRole("button", { name: "Close", exact: true }).filter({ visible: true }).last().click();
}

async function startCreate(page, entityType) {
  await openAddFlow(page);
  await clickFlowChoice(page, "Create a single entity");
  await selectHaOption(page, "Entity type", entityType);
  await submit(page);
}

test("create an occupancy sensor, then hit the entity-id conflict step", async ({ page }) => {
  await login(page);
  await startCreate(page, "Virtual Occupancy Sensor");
  await expectFlowTitle(page, "Virtual Occupancy Sensor");
  await page.getByRole("textbox", { name: /^Name/ }).fill("Browser Occupancy");
  await pickEntity(page, page, "Source sensor", "E2E Motion");
  await submit(page);
  await finishCreated(page, "Browser Occupancy");
  const state = await entityState(page, "binary_sensor.browser_occupancy");
  expect(state.attributes.friendly_name).toBe("Browser Occupancy");

  // The same name with a blank entity id: the flow warns and offers to proceed.
  await startCreate(page, "Virtual Occupancy Sensor");
  await page.getByRole("textbox", { name: /^Name/ }).fill("Browser Occupancy");
  await pickEntity(page, page, "Source sensor", "E2E Raw Occupancy");
  await submit(page);
  await expectFlowTitle(page, "Entity ID already exists");
  await clickFlowChoice(page, "Create it anyway");
  await finishCreated(page, "Browser Occupancy");
  const second = await entityState(page, "binary_sensor.browser_occupancy_2");
  expect(second.attributes.friendly_name).toBe("Browser Occupancy");
});

test("discover sensors and lights in bulk", async ({ page }) => {
  await login(page);
  // Occupancy candidates: removal motion only (the others are wrapped above,
  // the disabled one is hidden).
  await openAddFlow(page);
  await clickFlowChoice(page, "Discover occupancy sensors");
  await expectFlowTitle(page, "Discover occupancy sensors");
  await submit(page);
  const removalMotion = page.getByRole("checkbox", { name: /E2E Removal Motion/ });
  await expect(removalMotion).toBeVisible();
  await expect(removalMotion).toBeChecked();
  await submit(page);
  await expectFlowTitle(page, "Occupancy sensor defaults");
  await submit(page);
  await closeAbort(page, /Created 1 virtual MoLight entit/);

  // Lights: every unwrapped light is offered; the scheduled light's member is not.
  await openAddFlow(page);
  await clickFlowChoice(page, "Discover lights");
  await expectFlowTitle(page, "Discover lights");
  await submit(page);
  const timerTarget = page.getByRole("checkbox", { name: /E2E Timer Target/ });
  await expect(timerTarget).toBeVisible();
  await expect(timerTarget).toBeChecked();
  await expect(page.getByRole("checkbox", { name: /E2E Main Light/ })).toHaveCount(0);
  await submit(page);
  await expectFlowTitle(page, "Light defaults");
  await submit(page);
  await closeAbort(page, /Created 5 virtual MoLight entit/);
  const wrapped = await entityState(page, "light.e2e_timer_target_2");
  expect(wrapped.attributes.friendly_name).toBe("E2E Timer Target");
});

test("assign an occupancy sensor to several lights", async ({ page }) => {
  await login(page);
  await openAddFlow(page);
  await clickFlowChoice(page, "Assign a sensor to several lights");
  await clickFlowChoice(page, "Assign an occupancy sensor");
  await expectFlowTitle(page, "Assign an occupancy sensor");
  await pickEntity(page, page, "Occupancy sensor", "Browser Occupancy");
  await submit(page);
  await expectFlowTitle(page, "Choose the lights");
  await pickEntity(page, page, "Lights", "E2E Timer Target");
  await submit(page);
  await closeAbort(page, /Assigned the sensor to 1 light\(s\) and removed it from 0/);
});

test("create a remote through its form", async ({ page }) => {
  await login(page);
  await startCreate(page, "Virtual Remote");
  await expectFlowTitle(page, "Virtual Remote");
  await page.getByRole("textbox", { name: /^Name/ }).fill("Browser Remote");
  await pickEntity(page, page, "Lights to control", EDITED_LIGHT_NAME);
  const turnOn = await sectionPanel(page, "Turn on");
  await pickEntity(page, turnOn, "Single-click buttons", "E2E Button");
  await submit(page);
  await finishCreated(page, "Browser Remote");
  const sensor = await entityState(page, "sensor.browser_remote_last_action");
  expect(sensor.state).toBe("unknown");
});

test("a light with a turn-on selection gets the selection page", async ({ page }) => {
  await login(page);
  await startCreate(page, "Virtual Light");
  await expectFlowTitle(page, "Virtual Light");
  await page.getByRole("textbox", { name: /^Name/ }).fill("Browser Selected");
  await pickEntity(page, page, "Lights to control", "E2E CT Light");
  const behavior = await sectionPanel(page, "Turn-on & turn-off behavior");
  await pickEntity(page, behavior, "Turn-on selection entity", "E2E Target Mode");
  await submit(page);
  await expectFlowTitle(page, "Turn-on selection");
  await selectHaOption(page, "Fixed/fallback option", "Cozy");
  await submit(page);
  await finishCreated(page, "Browser Selected");
  const state = await entityState(page, "light.browser_selected");
  expect(state.attributes.friendly_name).toBe("Browser Selected");
});
