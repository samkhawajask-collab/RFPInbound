// Creates Power Platform connections through the maker portal UI.
// A visible browser opens with a persistent profile; the user signs in once, the script does the rest.
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const ENV = process.env.PP_ENV_ID;
const WANT = (process.env.PP_CONNECTORS || 'SharePoint|Word Online (Business)').split('|');
const DIR = __dirname;
const SHOTS = path.join(DIR, 'shots');
fs.mkdirSync(SHOTS, { recursive: true });
let n = 0;
const log = (m) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${m}`);
const shot = async (page, name) => {
  const f = path.join(SHOTS, `${String(++n).padStart(2, '0')}-${name}.png`);
  await page.screenshot({ path: f, fullPage: false }).catch(() => {});
  log(`SHOT ${f}`);
};

// Fills the Entra ID sign-in form when credentials are supplied through the environment (never logged).
async function trySignIn(page) {
  const user = process.env.PP_USER, pass = process.env.PP_PASSWORD;
  if (!user || !pass || !/login\.microsoftonline\.com/.test(page.url())) return;
  try {
    const email = page.locator('input[type="email"]');
    if (await email.isVisible({ timeout: 3000 }).catch(() => false)) {
      await email.fill(user);
      await page.locator('#idSIButton9, input[type="submit"]').first().click();
      log('SIGNIN_EMAIL_SUBMITTED');
      await page.waitForTimeout(3000);
    }
    const pwd = page.locator('input[type="password"]');
    if (await pwd.isVisible({ timeout: 5000 }).catch(() => false)) {
      await pwd.fill(pass);
      await page.locator('#idSIButton9, input[type="submit"]').first().click();
      log('SIGNIN_PASSWORD_SUBMITTED');
      await page.waitForTimeout(4000);
    }
    // "Stay signed in?" prompt
    const yes = page.locator('#idSIButton9');
    if (/kmsi|login\.srf|ProcessAuth/i.test(page.url()) || await page.getByText(/stay signed in/i).isVisible({ timeout: 2000 }).catch(() => false)) {
      await yes.click({ timeout: 5000 }).catch(() => {});
      log('SIGNIN_KMSI_ACCEPTED');
    }
    if (await page.getByText(/approve sign in|verify your identity|enter code|more information required/i).isVisible({ timeout: 2000 }).catch(() => false)) {
      log('SIGNIN_MFA_REQUIRED - complete the verification in the browser window');
      await shot(page, 'mfa');
    }
  } catch (e) { log(`SIGNIN_STEP_ERROR ${e.message.split('\n')[0]}`); await shot(page, 'signin-error'); }
}

async function waitForPortal(page) {
  const deadline = Date.now() + 10 * 60 * 1000;
  let warned = 0;
  while (Date.now() < deadline) {
    await trySignIn(page);
    const url = page.url();
    if (url.startsWith('https://make.powerapps.com/') && !/login|oauth/.test(url)) {
      await page.waitForLoadState('domcontentloaded');
      return true;
    }
    if (Date.now() - warned > 20000) { log('WAITING_FOR_SIGNIN - please sign in in the browser window'); warned = Date.now(); }
    await page.waitForTimeout(2000);
  }
  return false;
}

// The portal URL can appear briefly before a redirect to sign-in, so readiness is the
// "New connection" button actually being on screen; sign-in is completed whenever the login page shows.
async function openConnectionsPage(page) {
  await page.goto(`https://make.powerapps.com/environments/${ENV}/connections`, { waitUntil: 'domcontentloaded' });
  const button = page.getByRole('button', { name: /new connection/i }).first();
  const deadline = Date.now() + 10 * 60 * 1000;
  let warned = 0;
  while (Date.now() < deadline) {
    if (/login\.microsoftonline\.com|login\.live\.com/.test(page.url())) {
      await trySignIn(page);
      if (Date.now() - warned > 30000) { log('WAITING_FOR_SIGNIN'); warned = Date.now(); }
    }
    if (await button.isVisible().catch(() => false)) return button;
    await page.waitForTimeout(2000);
  }
  throw new Error('Connections page did not become ready');
}

async function createConnection(context, page, connector) {
  log(`START ${connector}`);
  const newConnection = await openConnectionsPage(page);
  log('PORTAL_READY');
  await shot(page, `list-${connector.split(' ')[0]}`);
  await newConnection.click({ timeout: 30000 });
  await page.waitForTimeout(4000);
  // The page's own connector search sits in the command bar; the first "search" box is the global header search.
  const search = page.locator('input[placeholder="Search"]').last();
  await search.fill(connector.replace(/\s*\(.*\)$/, ''));
  await page.waitForTimeout(4000);
  await shot(page, `search-${connector.split(' ')[0]}`);
  // Pick the tile whose visible name matches exactly.
  const tile = page.getByText(connector, { exact: true }).first();
  await tile.click({ timeout: 30000 });
  await page.waitForTimeout(4000);
  await shot(page, `dialog-${connector.split(' ')[0]}`);
  const popupPromise = context.waitForEvent('page', { timeout: 60000 }).catch(() => null);
  await page.getByRole('button', { name: /^create$/i }).first().click({ timeout: 30000 });
  const popup = await popupPromise;
  if (popup) {
    log(`POPUP ${popup.url().slice(0, 80)}`);
    // Pick the signed-in account if the account picker appears; otherwise SSO completes on its own.
    try {
      const tileAcct = popup.locator('[data-test-id], .table-row, div[role="button"]').filter({ hasText: /@/ }).first();
      await tileAcct.click({ timeout: 10000 });
      log('ACCOUNT_PICKED');
    } catch { /* no picker */ }
    await popup.waitForEvent('close', { timeout: 120000 }).catch(() => log('POPUP_STILL_OPEN - complete sign-in/consent in the popup if asked'));
  }
  await page.waitForTimeout(8000);
  await shot(page, `done-${connector.split(' ')[0]}`);
  log(`DONE ${connector}`);
}

(async () => {
  if (!ENV) throw new Error('PP_ENV_ID not set');
  const context = await chromium.launchPersistentContext(path.join(DIR, 'profile'), { headless: false, viewport: { width: 1400, height: 900 } });
  const page = context.pages()[0] || await context.newPage();
  await page.goto(`https://make.powerapps.com/environments/${ENV}/connections`, { waitUntil: 'domcontentloaded' });
  if (!(await waitForPortal(page))) { log('SIGNIN_TIMEOUT'); await context.close(); process.exit(2); }
  log('SIGNED_IN');
  for (const c of WANT) {
    try { await createConnection(context, page, c); }
    catch (e) { log(`STEP_FAIL ${c}: ${e.message.split('\n')[0]}`); await shot(page, `fail-${c.split(' ')[0]}`); }
  }
  log('ALL_DONE');
  await context.close();
})().catch((e) => { log(`FATAL ${e.message}`); process.exit(1); });
