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
import { batteryLow, batteryReplaced, openPanel } from './tests/helpers';

const OUT = process.env.SHOT_DIR || '/tmp/glue-shots';
const DEVICE = 'shot_front_door';
const DEVICE_NAME = 'Front door sensor';

async function reloadUntil(page: Page, predicate: () => Promise<boolean>, tries = 8): Promise<void> {
  for (let i = 0; i < tries; i++) {
    await openPanel(page);
    if (await predicate()) return;
    await page.waitForTimeout(1500);
  }
}

test('capture the glue flow', async ({ page, request }) => {
  const panel = page.locator('home-keeper-panel').first();
  const card = panel.locator('ha-card.hk-card', { hasText: DEVICE_NAME }).first();

  // 1. Battery low → a due task appears, "Managed by Battery Notes".
  await batteryLow(request, DEVICE, DEVICE_NAME);
  await reloadUntil(page, async () => (await card.count()) > 0);
  await expect(card.locator('ha-assist-chip.hk-managed')).toContainText('Battery Notes');
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/flow-1-battery-low.png`, fullPage: true });

  // 2. Battery replaced → the task moves to the collapsed Monitored section. Expand
  //    it so the shot shows the dormant, history-bearing task.
  const monitored = panel.locator('details.hk-group[data-group-key="status:monitored"]');
  await batteryReplaced(request, DEVICE);
  await reloadUntil(page, async () => (await monitored.count()) > 0);
  await monitored.locator('summary').click();
  await expect(monitored.locator('ha-card.hk-card', { hasText: DEVICE_NAME }).first()).toBeVisible();
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/flow-2-monitored.png`, fullPage: true });
});
