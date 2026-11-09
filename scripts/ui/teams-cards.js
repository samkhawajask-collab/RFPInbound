// Answers RFP Factory Adaptive Cards in Teams web (dev/test), for card types with no API.
// Uses the persistent profile in scripts/ui/profile; if it is not signed in, sign in in the visible window.
//
//   TEAMS_MODE=look      open the flow bot chat and screenshot each pending card (default)
//   TEAMS_MODE=answer    TEAMS_MATCH=<text the card must contain> TEAMS_BUTTON=<button title>
//                        [TEAMS_TEXT=<replacement for the card's text input>] [TEAMS_MAX=<cards to answer>]
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const DIR = __dirname;
const SHOTS = path.join(DIR, 'shots');
fs.mkdirSync(SHOTS, { recursive: true });
const MODE = process.env.TEAMS_MODE || 'look';
const MATCH = process.env.TEAMS_MATCH || 'Please verify text read from a scanned page';
const BUTTON = process.env.TEAMS_BUTTON || '';
const TEXT = process.env.TEAMS_TEXT;
const MAX = parseInt(process.env.TEAMS_MAX || '100', 10);
const CHAT = process.env.TEAMS_CHAT || 'Workflows';
let n = 0;
const log = (m) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${m}`);
const shot = async (target, name) => {
  const f = path.join(SHOTS, `teams-${String(++n).padStart(2, '0')}-${name}.png`);
  await target.screenshot({ path: f }).catch((e) => log(`SHOT_FAILED ${e.message.split('\n')[0]}`));
  log(`SHOT ${f}`);
};

async function openChat(page) {
  await page.goto('https://teams.microsoft.com/v2/', { waitUntil: 'domcontentloaded' });
  const deadline = Date.now() + 10 * 60 * 1000;
  let polls = 0;
  while (Date.now() < deadline) {
    if (await page.getByRole('treeitem').first().isVisible().catch(() => false)) break;
    if (polls++ % 6 === 0) {
      log(`WAITING url=${page.url().slice(0, 80)}${/login\.microsoftonline\.com/.test(page.url()) ? ' - sign in in the browser window' : ''}`);
      await shot(page, 'waiting');
    }
    await page.waitForTimeout(5000);
  }
  await page.waitForTimeout(5000);
  await shot(page, 'home');
  const item = page.getByRole('treeitem').filter({ hasText: CHAT }).first();
  if (!(await item.isVisible().catch(() => false))) {
    const names = await page.getByRole('treeitem').allInnerTexts();
    log(`CHAT_NOT_FOUND ${CHAT}; chats: ${names.map((s) => s.split('\n')[0]).slice(0, 30).join(' | ')}`);
    return false;
  }
  await item.click();
  await page.waitForTimeout(8000);
  await shot(page, 'chat');
  return true;
}

// Pending cards are the ones that still show the button; answered cards are replaced by the update message.
function pendingCards(page) {
  return page.locator('.ac-adaptiveCard').filter({ hasText: MATCH }).filter({ has: page.getByRole('button', { name: BUTTON || 'Text is correct' }) });
}

(async () => {
  const context = await chromium.launchPersistentContext(path.join(DIR, 'profile'), { headless: false, viewport: { width: 1400, height: 1000 } });
  const page = context.pages()[0] || (await context.newPage());
  try {
    if (!(await openChat(page))) return;
    // Load older messages so every pending card is in the DOM.
    for (let i = 0; i < 6; i++) { await page.mouse.move(800, 400); await page.mouse.wheel(0, -4000); await page.waitForTimeout(1500); }
    let cards = pendingCards(page);
    log(`PENDING ${await cards.count()}`);
    if (MODE === 'look') {
      const count = await cards.count();
      for (let i = 0; i < count; i++) {
        const c = cards.nth(i);
        await c.scrollIntoViewIfNeeded();
        await page.waitForTimeout(800);
        const req = ((await c.innerText()).match(/Requirement\s*\n?\s*(\S+)/) || [])[1] || `card${i}`;
        const img = c.locator('img').first();
        const loaded = await img.evaluate((el) => el.complete && el.naturalWidth > 0 ? `${el.naturalWidth}x${el.naturalHeight}` : 'not loaded').catch(() => 'no img');
        log(`CARD ${req} image ${loaded}`);
        await shot(c, `card-${req}`);
      }
      return;
    }
    let done = 0;
    while (done < MAX) {
      cards = pendingCards(page);
      if ((await cards.count()) === 0) break;
      const c = cards.first();
      await c.scrollIntoViewIfNeeded();
      const req = ((await c.innerText()).match(/Requirement\s*\n?\s*(\S+)/) || [])[1] || '?';
      if (TEXT !== undefined) {
        const box = c.locator('textarea, input[type="text"]').first();
        await box.fill(TEXT);
      }
      await c.getByRole('button', { name: BUTTON }).click();
      log(`ANSWERED ${req} with "${BUTTON}"`);
      done++;
      await page.waitForTimeout(6000);
    }
    await shot(page, 'after');
    log(`DONE ${done}`);
  } catch (e) {
    log(`ERROR ${e.message.split('\n')[0]}`);
    await shot(page, 'error');
  } finally {
    await context.close();
  }
})();
