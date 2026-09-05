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

/** A battery going low, with the spec Battery Notes reports for it. */
export const typedBatteryLow = (
  request: APIRequestContext,
  deviceId: string,
  deviceName: string,
  batteryType: string,
  batteryQuantity = 1,
) =>
  fireBatteryEvent(request, 'battery_notes_battery_threshold', {
    device_id: deviceId,
    device_name: deviceName,
    battery_low: true,
    battery_type: batteryType,
    battery_quantity: batteryQuantity,
    battery_level: 8,
  });

/** A rechargeable going low — what the `rechargeable_mode` option routes. */
export const rechargeableLow = (
  request: APIRequestContext,
  deviceId: string,
  deviceName: string,
) => typedBatteryLow(request, deviceId, deviceName, 'Rechargeable');

const authHeaders = () => ({ Authorization: `Bearer ${authToken()}` });

/** This glue's config entry id, looked up by domain rather than hard-coded. */
export async function glueEntryId(request: APIRequestContext): Promise<string> {
  const r = await request.get(`${HA_URL}/api/config/config_entries/entry`, {
    headers: authHeaders(),
  });
  expect(r.ok(), `listing config entries failed: ${r.status()}`).toBeTruthy();
  const entries = (await r.json()) as { domain: string; entry_id: string }[];
  const entry = entries.find((e) => e.domain === 'home_keeper_battery_notes');
  expect(entry, 'the glue has no config entry in this container').toBeTruthy();
  return entry!.entry_id;
}

/**
 * Drive the glue's real options flow, submitting the form a user would: start from
 * the schema's own defaults so every field round-trips, then apply *overrides*.
 */
export async function setGlueOptions(
  request: APIRequestContext,
  overrides: Record<string, unknown>,
): Promise<void> {
  const entryId = await glueEntryId(request);
  const start = await request.post(`${HA_URL}/api/config/config_entries/options/flow`, {
    headers: authHeaders(),
    data: { handler: entryId },
  });
  expect(start.ok(), `opening the options flow failed: ${start.status()}`).toBeTruthy();
  const flow = (await start.json()) as {
    flow_id: string;
    data_schema: { name: string; default?: unknown }[];
  };
  const data: Record<string, unknown> = {};
  for (const field of flow.data_schema) {
    if ('default' in field) data[field.name] = field.default;
  }
  const submit = await request.post(
    `${HA_URL}/api/config/config_entries/options/flow/${flow.flow_id}`,
    { headers: authHeaders(), data: { ...data, ...overrides } },
  );
  expect(submit.ok(), `saving options failed: ${submit.status()}`).toBeTruthy();
}

/** The glue's Home Keeper task for *deviceId*, or null — matched by our source ns. */
export async function glueTask(
  request: APIRequestContext,
  deviceId: string,
): Promise<{ id: string; name: string } | null> {
  const r = await request.post(`${HA_URL}/api/services/home_keeper/list_tasks?return_response`, {
    headers: authHeaders(),
    data: {},
  });
  if (!r.ok()) return null;
  const body = (await r.json()) as { service_response: { tasks: Record<string, any>[] } };
  const match = body.service_response.tasks.find(
    (t) => t?.source?.home_keeper_battery_notes?.device_id === deviceId,
  );
  return match ? { id: match.id, name: match.name } : null;
}

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
