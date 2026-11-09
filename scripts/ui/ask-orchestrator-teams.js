// Asks the RFP Orchestrator questions in Microsoft Teams, the channel users get, and prints its replies (ADR-0030).
//   PP_ENV_ID=<environment id> BOT_ID=<agent id> node scripts/ui/ask-orchestrator-teams.js "What is the status of RFP A?" ["..."]
// Dev and test agents share a name in Teams, so the chat is opened from that agent's own Copilot Studio channel panel.
// With no questions it only opens the chat and reports what it found. Each question waits up to WAIT_S seconds
// (default 120) for the reply to stop changing. Screenshots go to scripts/ui/shots.
const { chromium } = require('playwright');
const path = require('path');

const ENV = process.env.PP_ENV_ID, BOT = process.env.BOT_ID;
const WAIT = Number(process.env.WAIT_S || 120) * 1000;
const log = (m) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${m}`);

(async () => {
  if (!ENV || !BOT) throw new Error('PP_ENV_ID and BOT_ID are required');
  const questions = process.argv.slice(2);
  const ctx = await chromium.launchPersistentContext(path.join(__dirname, 'profile'), { headless: false, viewport: { width: 1500, height: 950 } });
  const page = ctx.pages()[0] || await ctx.newPage();
  const shot = (p, name) => p.screenshot({ path: path.join(__dirname, 'shots', `teams-orch-${name}.png`) });
  try {
    // TEAMS_DEEP (printed as DEEP LINK by an earlier run) skips the portal, whose button often opens nothing.
    // TEAMS_CHAT=1 opens the agent's existing chat from the chat list instead. Use it only when one environment's agent is added.
    let teams = page, deep = process.env.TEAMS_DEEP || (process.env.TEAMS_CHAT ? '/' : '');
    if (!deep) {
      await page.goto(`https://copilotstudio.microsoft.com/environments/${ENV}/agents/${BOT}`, { waitUntil: 'domcontentloaded' });
      await page.getByText('Tools', { exact: true }).first().waitFor({ timeout: 120000 });
      await page.waitForTimeout(6000);
      await page.getByText('Teams + Microsoft 365', { exact: true }).first().click();
      await page.waitForTimeout(6000);
      const panel = page.getByRole('dialog').last();
      await panel.getByText('Use and share', { exact: true }).first().click();
      await page.waitForTimeout(14000);
      const opener = panel.getByRole('button', { name: 'View in Microsoft Teams' }).first();
      teams = null;
      for (let i = 0; i < 3 && !teams; i++) {
        [teams] = await Promise.all([ctx.waitForEvent('page', { timeout: 30000 }).catch(() => null), opener.click()]);
      }
      if (!teams) throw new Error('View in Microsoft Teams opened nothing');
      await teams.waitForLoadState('domcontentloaded');
      // The link lands on the "open in" chooser, whose web app link doesn't navigate here. Its url parameter is the deep link.
      await teams.getByText('Use the web app instead').first().waitFor({ timeout: 45000 });
      deep = new URL(teams.url()).searchParams.get('url');
      if (!deep) throw new Error('The chooser has no deep link');
      log(`DEEP LINK: ${deep}`);
    }
    await teams.goto(`https://teams.microsoft.com${deep}`, { waitUntil: 'domcontentloaded', timeout: 120000 });
    await teams.waitForTimeout(40000);
    await shot(teams, '2-opened');
    log(`OPENED: ${teams.url().split('?')[0]}`);
    // The install prompt shows Add the first time and Open after that; the chat behind it is whichever was last open.
    // The dialog's own button: the page behind it has an Open button too.
    const install = teams.locator('.fui-DialogSurface button', { hasText: /^\s*(Add|Open)\s*$/ }).first();
    await install.waitFor({ timeout: 60000 }).catch(() => log('no install prompt'));
    if (await install.count()) {
      log(`clicked ${await install.innerText()}`);
      await install.evaluate((b) => b.click()); // The dialog's backdrop fails Playwright's pointer check.
      await teams.waitForTimeout(15000);
      const again = teams.locator('.fui-DialogSurface button', { hasText: /^\s*Open\s*$/ }).first();
      if (await again.count()) { await again.evaluate((b) => b.click()); log('clicked Open'); }
      await teams.waitForTimeout(25000);
    }
    if (process.env.TEAMS_CHAT) {
      await teams.getByText('RFP Orchestrator', { exact: true }).first().click();
      await teams.waitForTimeout(15000);
    }
    await shot(teams, '3-chat');
    // Never type into another chat, such as Workflows.
    const title = await teams.locator('[data-tid="chat-title"], [data-tid="chat-header-title"], h2').first().innerText().catch(() => '');
    log(`CHAT TITLE: ${title}`);
    if (!/RFP Orchestrator/i.test(title)) throw new Error('The open chat is not the RFP Orchestrator; nothing typed');
    log(`SCREEN: ${(await teams.innerText('body')).replace(/\s*\n+\s*/g, ' | ').slice(0, 500)}`);
    // The editor can refuse Playwright's click checks, so give it focus directly.
    const editor = teams.locator('[data-tid="ckeditor"], [contenteditable="true"]').first();
    await editor.waitFor({ timeout: 60000 });
    await teams.waitForTimeout(5000);
    const box = { click: () => editor.focus() };
    for (const [i, q] of questions.entries()) {
      await box.click();
      await teams.keyboard.type(q);
      await teams.keyboard.press('Enter');
      log(`ASKED ${q}`);
      let last = '', stable = 0, changed = false;
      const before = await teams.innerText('body');
      const deadline = Date.now() + WAIT;
      while (Date.now() < deadline && stable < 4) {
        await teams.waitForTimeout(5000);
        const now = await teams.innerText('body');
        if (now !== before) changed = true;
        stable = now === last && changed ? stable + 1 : 0;
        last = now;
      }
      console.log(`REPLY ${i + 1}:\n${last.slice(last.lastIndexOf(q) + q.length).trim().slice(0, 2500)}\n`);
      await shot(teams, `reply-${i + 1}`);
    }
  } finally {
    await ctx.close();
  }
})().catch((e) => { log(`FATAL ${e.message.split('\n').slice(0, 12).join(' / ')}`); process.exit(1); });
