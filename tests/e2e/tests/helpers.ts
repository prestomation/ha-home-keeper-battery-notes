import { APIRequestContext, Page, expect } from '@playwright/test';
import { readFileSync } from 'fs';
import { resolve } from 'path';

/** Route for the Home Keeper sidebar panel (registered by the home_keeper integration). */
export const PANEL_URL = '/home-keeper';

const HA_URL = process.env.HA_URL || 'http://localhost:8123';

/** The access token global-setup persisted, for REST calls from specs. */
export function authToken(): string {
  return readFileSync(resolve(__dirname, '..', '.auth', 'token'), 'utf8').trim();
}

/**
 * Fire a Battery Notes event on the HA bus over the REST API — the same events the
 * real Battery Notes integration emits. This is how the e2e drives the glue: the
 * glue listens for these and creates/clears Home Keeper tasks.
 */
export async function fireBatteryEvent(
  request: APIRequestContext,
  eventType: string,
  data: Record<string, unknown>,
): Promise<void> {
  const r = await request.post(`${HA_URL}/api/events/${eventType}`, {
    headers: { Authorization: `Bearer ${authToken()}` },
    data,
  });
  expect(r.ok(), `firing ${eventType} failed: ${r.status()}`).toBeTruthy();
}

/** Convenience: a battery going low / being replaced for a device. */
export const batteryLow = (request: APIRequestContext, deviceId: string, deviceName: string) =>
  fireBatteryEvent(request, 'battery_notes_battery_threshold', {
    device_id: deviceId,
    device_name: deviceName,
    battery_low: true,
  });

export const batteryReplaced = (request: APIRequestContext, deviceId: string) =>
  fireBatteryEvent(request, 'battery_notes_battery_replaced', { device_id: deviceId });

/** Navigate to the Home Keeper panel and wait for the custom element to upgrade. */
export async function openPanel(page: Page): Promise<void> {
  await page.goto(PANEL_URL, { waitUntil: 'domcontentloaded' });
  await page.locator('home-keeper-panel').first().waitFor({ state: 'attached', timeout: 45_000 });
  await expect(page.locator('home-keeper-panel').first()).toBeVisible();
}

/** Collect panel-relevant console/page errors. Attach BEFORE navigating. */
export function trackPanelErrors(page: Page): string[] {
  const errors: string[] = [];
  const isRelated = (s: string) => /home.?keeper|battery.?notes/i.test(s);
  page.on('pageerror', (e) => {
    const text = `${e.message}\n${e.stack || ''}`;
    if (isRelated(text)) errors.push(`pageerror: ${text}`);
  });
  page.on('console', (msg) => {
    if (msg.type() === 'error' && isRelated(msg.text())) {
      errors.push(`console.error: ${msg.text()}`);
    }
  });
  return errors;
}
