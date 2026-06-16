import { test, expect, Page } from '@playwright/test';
import { batteryLow, batteryReplaced, openPanel, trackPanelErrors } from './helpers';

/**
 * End-to-end: drive the *real* stack. We fire Battery Notes events on the HA bus
 * and assert the glue's effect on the *real* Home Keeper panel — a battery task
 * appearing when a battery goes low and tucking into the Monitored section when
 * it's replaced. This exercises the whole chain: Battery Notes event → glue →
 * home_keeper.add_task / complete_task → panel.
 */

const DEVICE = 'e2e_front_door';
const DEVICE_NAME = 'Front door sensor';
const TASK_TEXT = `Replace battery: ${DEVICE_NAME}`;

/** Reload the panel until *predicate* holds (the glue + HK reload run async). */
async function reloadUntil(page: Page, predicate: () => Promise<boolean>, tries = 8): Promise<void> {
  for (let i = 0; i < tries; i++) {
    await openPanel(page);
    if (await predicate()) return;
    await page.waitForTimeout(1500);
  }
  throw new Error('panel did not reach the expected state in time');
}

test('battery low → glue creates a due task; replaced → it goes to Monitored', async ({
  page,
  request,
}) => {
  const errors = trackPanelErrors(page);
  const panel = page.locator('home-keeper-panel').first();
  const activeCard = panel.locator('ha-card.hk-card', { hasText: DEVICE_NAME }).first();

  // 1) Battery goes low → the glue creates an armed (due-now) task.
  await batteryLow(request, DEVICE, DEVICE_NAME);
  await reloadUntil(page, async () => (await activeCard.count()) > 0);

  await expect(activeCard).toContainText(TASK_TEXT);
  await expect(activeCard.locator('ha-assist-chip.hk-managed')).toContainText('Battery Notes');
  await expect(activeCard.locator('ha-assist-chip.hk-overdue')).toBeVisible();

  // 2) Battery replaced → the task records the change and goes dormant; it leaves
  //    the visible (overdue) list and lands in the collapsed "Monitored" section.
  const monitored = panel.locator('details.hk-group[data-group-key="status:monitored"]');
  await batteryReplaced(request, DEVICE);
  await reloadUntil(page, async () => (await monitored.count()) > 0);

  // Collapsed by default → its card isn't visible until expanded.
  await expect(monitored).not.toHaveAttribute('open', /.*/);
  await monitored.locator('summary').click();
  const dormantCard = monitored.locator('ha-card.hk-card', { hasText: DEVICE_NAME }).first();
  await expect(dormantCard).toBeVisible();
  await expect(dormantCard).toContainText('Monitored');
  // Dormant → no quick "Done" action (nothing to mark done until it's low again).
  await expect(dormantCard.locator('.done-btn')).toHaveCount(0);

  expect(errors, `panel errors:\n${errors.join('\n')}`).toHaveLength(0);
});
