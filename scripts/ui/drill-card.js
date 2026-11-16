// PR 114 drills: acts on the newest open RFP card in the Teams Workflows chat that contains MATCH, as the signed-in
// pilot recipient (profile in scripts/ui/profile).
//   MATCH=<text on the card> BUTTON=<button title> [TEXT=<value for the card's text box; empty string clears it>] node scripts/ui/drill-card.js
//   MATCH=<text> MODE=look node scripts/ui/drill-card.js     report the newest matching cards and the last messages, click nothing
// Prints what the card showed after the click (Teams may refuse a submit, for example a required field left empty).
// Screenshots go to scripts/ui/shots/drill-*.png.
const { chromium } = require('playwright');
const path = require('path');

const MATCH = process.env.MATCH || '', BUTTON = process.env.BUTTON || '', MODE = process.env.MODE || 'click';
const TEXT = process.env.TEXT;
const log = (m) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${m}`);
const shot = (page, name) => page.screenshot({ path: path.join(__dirname, 'shots', `drill-${name}.png`) });

(async () => {
  if (!MATCH || (MODE === 'click' && !BUTTON)) throw new Error('MATCH and BUTTON are required');
  const ctx = await chromium.launchPersistentContext(path.join(__dirname, 'profile'), { headless: false, viewport: { width: 1500, height: 1000 } });
  const page = ctx.pages()[0] || await ctx.newPage();
  try {
    await page.goto('https://teams.cloud.microsoft/', { waitUntil: 'domcontentloaded', timeout: 120000 });
    await page.getByText('Workflows', { exact: true }).first().waitFor({ timeout: 120000 });
    await page.getByText('Workflows', { exact: true }).first().click();
    await page.waitForTimeout(15000);
    // The chat opens at the last-read marker; today's cards are below it.
    const jump = page.getByRole('button', { name: /Jump to newest/i });
    if (await jump.isVisible().catch(() => false)) { await jump.click(); await page.waitForTimeout(5000); }
    for (let i = 0; i < 5; i++) { await page.mouse.move(900, 500); await page.mouse.wheel(0, 6000); await page.waitForTimeout(1200); }
    const cards = page.locator('[data-tid="adaptive-card"]').filter({ hasText: MATCH });
    const n = await cards.count();
    log(`cards matching "${MATCH}": ${n}`);
    if (MODE === 'look') {
      for (let i = Math.max(0, n - 3); i < n; i++) log(`CARD ${i}: ${(await cards.nth(i).innerText()).replace(/\s+/g, ' ').slice(0, 400)}`);
      const msgs = await page.locator('[data-tid="chat-pane-message"], [data-tid="chat-pane-item"]').allInnerTexts().catch(() => []);
      for (const m of msgs.slice(-6)) log(`MSG ${m.replace(/\s+/g, ' ').slice(0, 300)}`);
      await shot(page, 'look');
      return;
    }
    const open = cards.filter({ has: page.getByRole('button', { name: BUTTON, exact: true }) });
    if (!(await open.count())) { log(`NO_OPEN_CARD with button "${BUTTON}"`); await shot(page, 'none'); process.exitCode = 2; return; }
    const card = open.last();
    await card.scrollIntoViewIfNeeded();
    if (TEXT !== undefined) {
      const box = card.locator('textarea, input[type="text"]').first();
      await box.fill(TEXT);
      log(`text box set to ${JSON.stringify(TEXT.slice(0, 80))}`);
    }
    await shot(page, 'before');
    await card.getByRole('button', { name: BUTTON, exact: true }).click();
    log(`CLICKED "${BUTTON}"`);
    await page.waitForTimeout(8000);
    const after = await card.innerText().catch(() => '(card replaced)');
    log(`CARD_AFTER ${after.replace(/\s+/g, ' ').slice(0, 300)}`);
    log(`STILL_OPEN ${await card.getByRole('button', { name: BUTTON, exact: true }).count() > 0}`);
    await shot(page, 'after');
  } catch (e) {
    log(`ERROR ${e.message.split('\n')[0]}`);
    await shot(page, 'error').catch(() => {});
    process.exitCode = 1;
  } finally {
    await ctx.close();
  }
})();
