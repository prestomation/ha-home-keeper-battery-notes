/**
 * One-off screenshot capture for PR/README documentation — not part of the e2e
 * suite (filename is not *.spec.ts). Run with:
 *   SHOT_DIR=../../docs/images npx playwright test screenshots.capture.ts \
 *     --config=screenshots.config.ts
 *
 * Captures the real flow: a Battery Notes event creating a Home Keeper task, then
 * the task tucked into the Monitored section after replacement.
 */
import { test, expect, Page } from '@playwright/test';
import {
  batteryLow,
  batteryReplaced,
  glueEntryId,
  glueTask,
  openPanel,
  rechargeableLow,
  setGlueOptions,
  typedBatteryLow,
} from './tests/helpers';

const OUT = process.env.SHOT_DIR || '/tmp/glue-shots';
const DEVICE = 'shot_front_door';
const DEVICE_NAME = 'Front door sensor';
const RECHARGEABLE = 'shot_hallway_lock';
const RECHARGEABLE_NAME = 'Hallway lock';
const DISPOSABLE = 'shot_kitchen_remote';
const DISPOSABLE_NAME = 'Kitchen remote';

async function reloadUntil(page: Page, predicate: () => Promise<boolean>, tries = 8): Promise<void> {
  for (let i = 0; i < tries; i++) {
    await openPanel(page);
    if (await predicate()) return;
    await page.waitForTimeout(1500);
  }
}

/**
 * Dismiss Home Keeper's first-run welcome card. It fills the top half of a fresh
 * panel, which buries the very rows these shots are about.
 */
async function dismissWelcome(page: Page): Promise<void> {
  const gotIt = page.locator('home-keeper-panel').first().getByText('Got it', { exact: true });
  if ((await gotIt.count()) > 0) {
    await gotIt.first().click();
    await page.waitForTimeout(500);
  }
}

test('capture the glue flow', async ({ page, request }) => {
  const panel = page.locator('home-keeper-panel').first();
  const card = panel.locator('ha-card.hk-card', { hasText: DEVICE_NAME }).first();

  // 1. Battery low → a due task appears, "Managed by Battery Notes".
  await batteryLow(request, DEVICE, DEVICE_NAME);
  await reloadUntil(page, async () => (await card.count()) > 0);
  await dismissWelcome(page);
  await expect(card.locator('ha-assist-chip.hk-managed')).toContainText('Battery Notes');
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/flow-1-battery-low.png`, fullPage: true });

  // 2. Battery replaced → the task moves to the collapsed Monitored section. Expand
  //    it so the shot shows the dormant, history-bearing task.
  const monitored = panel.locator('details.hk-group[data-group-key="status:monitored"]');
  await batteryReplaced(request, DEVICE);
  await reloadUntil(page, async () => (await monitored.count()) > 0);
  await dismissWelcome(page);
  await monitored.locator('summary').click();
  await expect(monitored.locator('ha-card.hk-card', { hasText: DEVICE_NAME }).first()).toBeVisible();
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/flow-2-monitored.png`, fullPage: true });
});

test('capture the rechargeable modes', async ({ page, request }) => {
  const panel = page.locator('home-keeper-panel').first();

  // 1. The option itself — a dropdown with the three answers, plus the charge task's
  //    own name template. Shot with the select open so all three read at a glance.
  await page.goto('/config/integrations/integration/home_keeper_battery_notes');
  const configure = page.getByRole('button', { name: /configure/i }).first();
  await configure.waitFor({ state: 'visible', timeout: 30_000 });
  await configure.click();
  // The dialog's own element has no layout box (its content is in a shadow surface),
  // so wait on the field's label rather than asserting the dialog is "visible", and
  // let the open animation settle before measuring anything inside it.
  const label = page.getByText('Rechargeable batteries').first();
  await label.waitFor({ state: 'visible', timeout: 20_000 });
  await page.waitForTimeout(1000);
  // Open the dropdown by clicking its caret. Two traps: a click on the middle of the
  // control lands on the value text and does nothing, and ha-select's box spans its
  // helper paragraph too — so its vertical centre is somewhere in the prose. Anchor
  // on the field's own label row instead, which is always the top of the control.
  const box = await page.locator('ha-select').first().boundingBox();
  const labelBox = await label.boundingBox();
  expect(box && labelBox, 'the rechargeable-mode select has no layout box').toBeTruthy();
  await page.mouse.click(box!.x + box!.width - 22, labelBox!.y + 28);
  // The opened menu renders into a closed shadow root, so nothing inside it is
  // reachable from the DOM — the open state can't be asserted and has to be confirmed
  // by looking at the PNG. Read the file before committing it.
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${OUT}/rechargeable-1-modes.png`, fullPage: true });
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
  await page.keyboard.press('Escape');

  // 2. Charge mode → a low rechargeable becomes a "Charge battery: …" task. Raise a
  //    disposable alongside it, because the contrast is the feature: same
  //    integration, two verbs, two chip icons.
  await setGlueOptions(request, { rechargeable_mode: 'charge' });
  await typedBatteryLow(request, DISPOSABLE, DISPOSABLE_NAME, 'AAA', 2);
  await rechargeableLow(request, RECHARGEABLE, RECHARGEABLE_NAME);
  const card = panel.locator('ha-card.hk-card', { hasText: `Charge battery: ${RECHARGEABLE_NAME}` }).first();
  await reloadUntil(page, async () => (await card.count()) > 0);
  await dismissWelcome(page);
  await expect(card).toContainText('Rechargeable');
  await expect(
    panel.locator('ha-card.hk-card', { hasText: `Replace battery: ${DISPOSABLE_NAME}` }).first(),
  ).toContainText('AAA');
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/rechargeable-2-charge-task.png`, fullPage: true });

  // 3. Its detail page, where Home Keeper renders the completion prompt the glue
  //    set — "Mark battery as charged?", not "replaced".
  const task = await glueTask(request, RECHARGEABLE);
  expect(task, 'no charge task to open').toBeTruthy();
  await page.goto(`/home-keeper/tasks/${task!.id}`);
  const prompt = panel.locator('.hk-managed-prompt');
  await expect(prompt).toContainText('charged', { timeout: 20_000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/rechargeable-3-charge-prompt.png`, fullPage: true });

  // Leave the container on the default, so a re-run starts where a user would.
  await setGlueOptions(request, { rechargeable_mode: 'skip' });
});
