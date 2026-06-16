/**
 * Playwright global setup — bootstrap Home Assistant auth.
 *
 * 1. Complete HA onboarding via the onboarding API (creates the `test` user) — or
 *    no-op + log in if onboarding was already done.
 * 2. Wait for Home Keeper's entities to appear (both integrations are up).
 * 3. Drive a real browser login once and persist the storage state so every spec
 *    starts authenticated.
 * 4. Persist the access token to `.auth/token` so specs can fire Battery Notes
 *    events over the REST API.
 *
 * Mirrors ha-home-keeper/tests/e2e/global-setup.ts.
 */
import { chromium } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'fs';
import { dirname, resolve } from 'path';

const HA_URL = process.env.HA_URL || 'http://localhost:8123';
const CLIENT_ID = `${HA_URL}/`;
const AUTH_DIR = resolve(__dirname, '.auth');
const STATE_PATH = resolve(AUTH_DIR, 'state.json');
const TOKEN_PATH = resolve(AUTH_DIR, 'token');
const USERNAME = 'test';
const PASSWORD = 'testtest1';

async function waitForHA(timeoutMs = 180_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`${HA_URL}/api/`);
      if (r.status === 200 || r.status === 401) return;
    } catch {
      /* not up yet */
    }
    await new Promise((res) => setTimeout(res, 2000));
  }
  throw new Error(`Home Assistant did not respond within ${timeoutMs}ms at ${HA_URL}`);
}

interface Tokens {
  access_token: string;
}

async function exchangeCode(code: string): Promise<Tokens> {
  const r = await fetch(`${HA_URL}/auth/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'authorization_code', code, client_id: CLIENT_ID }),
  });
  if (!r.ok) throw new Error(`token exchange failed: ${r.status} ${await r.text()}`);
  return (await r.json()) as Tokens;
}

async function ensureOnboarded(): Promise<string> {
  const r = await fetch(`${HA_URL}/api/onboarding/users`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      client_id: CLIENT_ID,
      name: 'Test',
      username: USERNAME,
      password: PASSWORD,
      language: 'en',
    }),
  });

  if (r.status === 403 || r.status === 404) {
    let lf = await fetch(`${HA_URL}/auth/login_flow`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        client_id: CLIENT_ID,
        handler: ['homeassistant', null],
        redirect_uri: `${HA_URL}/?auth_callback=1`,
      }),
    });
    const flowId = (await lf.json()).flow_id;
    lf = await fetch(`${HA_URL}/auth/login_flow/${flowId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: USERNAME, password: PASSWORD, client_id: CLIENT_ID }),
    });
    const access = (await exchangeCode((await lf.json()).result)).access_token;
    // Onboarding may have been left half-done by another tier — finish it.
    await completeRemainingOnboarding(access);
    return access;
  }
  if (!r.ok) throw new Error(`onboarding users failed: ${r.status} ${await r.text()}`);

  const access = (await exchangeCode((await r.json()).auth_code)).access_token;
  await completeRemainingOnboarding(access);
  return access;
}

/**
 * Finish the post-user onboarding steps (location / analytics / integration). Run
 * unconditionally and ignore per-step errors so it's safe whether onboarding is
 * fresh or was left half-done by another tier sharing this config dir — otherwise
 * HA redirects to the onboarding wizard instead of the panel.
 */
async function completeRemainingOnboarding(accessToken: string): Promise<void> {
  const headers = { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' };
  for (const [endpoint, payload] of [
    ['core_config', {}],
    ['analytics', {}],
    ['integration', { client_id: CLIENT_ID, redirect_uri: `${HA_URL}/?auth_callback=1` }],
  ] as const) {
    try {
      await fetch(`${HA_URL}/api/onboarding/${endpoint}`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
      });
    } catch {
      /* already done or not applicable */
    }
  }
}

async function waitForPanel(accessToken: string, timeoutMs = 120_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`${HA_URL}/api/states`, {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (r.ok) {
        const states: Array<{ entity_id: string }> = await r.json();
        if (states.some((s) => s.entity_id.startsWith('todo.home_keeper'))) return;
      }
    } catch {
      /* retry */
    }
    await new Promise((res) => setTimeout(res, 2000));
  }
  throw new Error('Home Keeper entities did not appear in time');
}

export default async function globalSetup(): Promise<void> {
  await waitForHA();
  const token = await ensureOnboarded();
  await waitForPanel(token);

  mkdirSync(AUTH_DIR, { recursive: true });
  writeFileSync(TOKEN_PATH, token, 'utf8');

  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = await context.newPage();
  try {
    await page.goto(`${HA_URL}/`, { waitUntil: 'domcontentloaded' });
    const username = page.locator('input[autocomplete="username"]');
    await username.waitFor({ state: 'visible', timeout: 30_000 });
    await username.fill(USERNAME);
    await page.locator('input[autocomplete="current-password"]').fill(PASSWORD);
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => !!window.localStorage.getItem('hassTokens'), null, {
      timeout: 30_000,
    });
    await page.waitForLoadState('networkidle');

    mkdirSync(dirname(STATE_PATH), { recursive: true });
    await context.storageState({ path: STATE_PATH });
    // eslint-disable-next-line no-console
    console.log(`[global-setup] saved auth state + token to ${AUTH_DIR}`);
  } finally {
    await browser.close();
  }
}
