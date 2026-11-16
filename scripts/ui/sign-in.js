// Opens the persistent profile (scripts/ui/profile) so a person can sign in once, as the pilot recipient, to Teams and
// Copilot Studio. Waits up to 15 minutes for both, then closes; the other UI scripts reuse the saved session.
//   PP_ENV_ID=<environment id> BOT_ID=<agent id> [TENANT_ID=<tenant id>] [LOGIN_HINT=<upn>] node scripts/ui/sign-in.js
// TENANT_ID and LOGIN_HINT open Teams on that tenant with the account pre-filled. A screenshot is saved every 30 seconds
// while it waits (shots/sign-in.png).
const { chromium } = require('playwright');
const path = require('path');

const ENV = process.env.PP_ENV_ID, BOT = process.env.BOT_ID;
const log = (m) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${m}`);

(async () => {
  if (!ENV || !BOT) throw new Error('PP_ENV_ID and BOT_ID are required');
  const ctx = await chromium.launchPersistentContext(path.join(__dirname, 'profile'), { headless: false, viewport: { width: 1400, height: 950 } });
  const page = ctx.pages()[0] || await ctx.newPage();
  try {
    log('Opening Teams: sign in in the browser window as the pilot recipient');
    const q = [process.env.TENANT_ID && `tenantId=${process.env.TENANT_ID}`, process.env.LOGIN_HINT && `login_hint=${encodeURIComponent(process.env.LOGIN_HINT)}`].filter(Boolean).join('&');
    await page.goto(`https://teams.microsoft.com/v2/${q ? '?' + q : ''}`, { waitUntil: 'domcontentloaded', timeout: 120000 });
    const timer = setInterval(() => page.screenshot({ path: path.join(__dirname, 'shots', 'sign-in.png') }).then(() => log(`WAITING ${page.url().slice(0, 70)}`)).catch(() => {}), 30000);
    try { await page.getByText('Workflows', { exact: true }).first().waitFor({ timeout: 15 * 60 * 1000 }); } finally { clearInterval(timer); }
    log('TEAMS_OK');
    await page.goto(`https://copilotstudio.microsoft.com/environments/${ENV}/agents/${BOT}`, { waitUntil: 'domcontentloaded', timeout: 120000 });
    await page.getByText('Tools', { exact: true }).first().waitFor({ timeout: 15 * 60 * 1000 });
    log('COPILOT_STUDIO_OK');
  } catch (e) {
    log(`SIGN_IN_FAILED ${e.message.split('\n')[0]}`);
    process.exitCode = 1;
  } finally {
    await ctx.close();
  }
})();
