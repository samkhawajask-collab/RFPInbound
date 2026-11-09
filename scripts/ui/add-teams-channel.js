// Adds the Teams channel to the RFP Orchestrator with availability "Microsoft Teams only" and publishes (ADR-0030).
//   PP_ENV_ID=<environment id> BOT_ID=<agent id> node scripts/ui/add-teams-channel.js
// No API found for this. Two portal behaviors shape the script:
// - Availability can't be changed after publishing, and the portal preselects Copilot and Teams, so the option is set
//   and checked before anything is added.
// - An added channel lives only on the open page until saved and published, so all of this happens in one session.
// If the channel already exists the script changes nothing. It reloads afterward and reads the saved option back.
// Screenshots go to scripts/ui/shots.
const { chromium } = require('playwright');
const path = require('path');

const ENV = process.env.PP_ENV_ID, BOT = process.env.BOT_ID;
const TEAMS_ONLY = 'Microsoft Teams only';
const log = (m) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${m}`);

(async () => {
  if (!ENV || !BOT) throw new Error('PP_ENV_ID and BOT_ID are required');
  const ctx = await chromium.launchPersistentContext(path.join(__dirname, 'profile'), { headless: false, viewport: { width: 1500, height: 950 } });
  const page = ctx.pages()[0] || await ctx.newPage();
  const shot = (name) => page.screenshot({ path: path.join(__dirname, 'shots', `channel-${name}.png`) });
  const open = async () => {
    await page.goto(`https://copilotstudio.microsoft.com/environments/${ENV}/agents/${BOT}`, { waitUntil: 'domcontentloaded' });
    await page.getByText('Tools', { exact: true }).first().waitFor({ timeout: 120000 });
    await page.waitForTimeout(6000);
  };
  const chip = () => page.getByText('Teams + Microsoft 365', { exact: true });
  try {
    await open();
    if (await chip().count()) {
      log('Channel already exists; nothing changed.');
    } else {
      await page.getByText(/^Channels$/).first().click();
      await page.waitForTimeout(4000);
      await chip().first().click();
      await page.waitForTimeout(6000);
      const radio = page.getByRole('radio', { name: TEAMS_ONLY });
      await radio.check();
      await page.waitForTimeout(1000);
      if (!(await radio.isChecked())) throw new Error('Teams only was not selected; stopping before adding');
      await shot('1-teams-only');
      await page.getByRole('button', { name: 'Add channel' }).click();
      await page.waitForTimeout(25000);
      if (!(await chip().count())) throw new Error('The channel was not added');
      log('channel added on the page');

      // Save, then publish: the save icon sits left of Publish (as in setup-orchestrator.js).
      const publish = await page.getByRole('button', { name: 'Publish', exact: true }).boundingBox();
      await page.mouse.click(publish.x - 130, publish.y + publish.height / 2);
      await page.waitForTimeout(8000);
      await page.getByRole('button', { name: 'Publish', exact: true }).click();
      await page.waitForTimeout(3000);
      await shot('2-publish');
      const confirm = page.getByRole('dialog').getByRole('button', { name: 'Publish' });
      if (await confirm.count()) await confirm.first().click();
      log('publish requested');
      await page.waitForTimeout(40000);
      await shot('3-published');
    }

    // Read the saved value from a fresh page.
    await open();
    if (!(await chip().count())) throw new Error('After reload there is no Teams channel: it was not saved');
    await chip().first().click();
    await page.waitForTimeout(6000);
    for (const name of [TEAMS_ONLY, 'Microsoft Copilot and Microsoft Teams']) {
      const r = page.getByRole('radio', { name });
      log(`${name}: ${(await r.count()) ? `checked=${await r.isChecked()} enabled=${await r.isEnabled()}` : 'not shown'}`);
    }
    await shot('4-saved');
    log(`PANEL: ${(await page.getByRole('dialog').last().innerText().catch(() => '')).replace(/\s*\n+\s*/g, ' | ').slice(0, 600)}`);
  } finally {
    await ctx.close();
  }
})().catch((e) => { log(`FATAL ${e.message.split('\n')[0]}`); process.exit(1); });
