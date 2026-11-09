// Answers RFP cards in the Teams Workflows chat as the signed-in pilot recipient, for a run with nobody at the cards.
//   KIND=ocr      CARD_KEYS=THPA-005,THPA-018 SINCE=<ISO time> node scripts/ui/answer-cards-in-teams.js
//   KIND=gap      CARD_KEYS=THPA-010          SINCE=<ISO time> [ANSWER=ok] node scripts/ui/answer-cards-in-teams.js
//   KIND=approve  CARD_KEYS="THPA RFP 2026-09" SINCE=<ISO time> node scripts/ui/answer-cards-in-teams.js
// ocr clicks "Text is correct" and never corrects the text. gap types ANSWER and clicks "Submit answer". approve clicks "Approve".
// CARD_KEYS is text that identifies each card: the requirement ID, or for approve the RFP number as printed on the card.
// Dev and test post to the same chat with the same IDs, and old cards still accept answers. So the script takes the newest
// matching card and refuses it unless it was posted at or after SINCE, the run's start. It can't see which environment
// a card came from: check the target's rows afterward. Add DRY_RUN=1 to report without clicking. Screenshots go to scripts/ui/shots.
const { chromium } = require('playwright');
const path = require('path');

const KIND = process.env.KIND, DRY = process.env.DRY_RUN === '1', ANSWER = process.env.ANSWER || 'ok';
const KEYS = (process.env.CARD_KEYS || '').split(',').map((k) => k.trim()).filter(Boolean);
const SINCE = new Date(process.env.SINCE);
const BUTTON = { ocr: 'Text is correct', gap: 'Submit answer', approve: 'Approve' }[KIND];
const log = (m) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${m}`);

// The message shows only a time for today's messages; anything older shows a date and is refused.
async function postedAt(card) {
  const text = await card.evaluate((el) => {
    let e = el;
    while (e && !/\d:\d\d\s*[AP]M/.test(e.innerText || '')) e = e.parentElement;
    return e ? e.innerText : '';
  });
  const head = text.slice(0, Math.max(0, text.indexOf(card_marker(text))));
  const m = head.match(/(\d{1,2}):(\d\d)\s*([AP])M/);
  if (!m || /\d\/\d|yesterday|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|mon|tue|wed|thu|fri|sat|sun)/i.test(head.replace(/Workflows/g, ''))) {
    log(`  message header not read as today: ${JSON.stringify(head.slice(0, 80))}`);
    return null;
  }
  const d = new Date();
  d.setHours((Number(m[1]) % 12) + (m[3] === 'P' ? 12 : 0), Number(m[2]), 59, 999);
  return d;
}
function card_marker(text) {
  return ['Please verify', 'No approved library answer', 'Proposal approval required'].find((s) => text.includes(s)) || text;
}

(async () => {
  if (!BUTTON || !KEYS.length || isNaN(SINCE)) throw new Error('KIND (ocr|gap|approve), CARD_KEYS and SINCE are required');
  const ctx = await chromium.launchPersistentContext(path.join(__dirname, 'profile'), { headless: false, viewport: { width: 1500, height: 950 } });
  const page = ctx.pages()[0] || await ctx.newPage();
  let failed = 0;
  try {
    await page.goto('https://teams.cloud.microsoft/', { waitUntil: 'domcontentloaded', timeout: 120000 });
    await page.getByText('Workflows', { exact: true }).first().waitFor({ timeout: 120000 });
    await page.getByText('Workflows', { exact: true }).first().click();
    await page.waitForTimeout(15000);
    for (const key of KEYS) {
      const button = page.getByRole('button', { name: BUTTON, exact: true });
      const card = page.locator('[data-tid="adaptive-card"]').filter({ hasText: key }).filter({ has: button }).last();
      if (!(await card.count())) { log(`${key}: no open card in view`); failed++; continue; }
      await card.scrollIntoViewIfNeeded();
      const at = await postedAt(card);
      if (!at || at < SINCE) { log(`${key}: newest card is older than SINCE or has no time; nothing clicked`); failed++; continue; }
      if (KIND === 'ocr') {
        const images = await card.locator('img').evaluateAll((els) => els.map((e) => `${e.naturalWidth}x${e.naturalHeight}`));
        log(`${key}: card images ${images.length ? images.join(', ') : 'none'}`);
      }
      if (DRY) { log(`${key}: would answer the card posted ${at.toLocaleTimeString()}`); continue; }
      if (KIND === 'gap') await card.getByRole('textbox', { name: 'Your answer for this proposal' }).fill(ANSWER);
      await card.getByRole('button', { name: BUTTON, exact: true }).click();
      await page.waitForTimeout(6000);
      log(`${key}: ${BUTTON} clicked`);
    }
    await page.screenshot({ path: path.join(__dirname, 'shots', `cards-${KIND}.png`) });
  } finally {
    await ctx.close();
  }
  if (failed) { log(`${failed} card(s) not answered`); process.exit(1); }
})().catch((e) => { log(`FATAL ${e.message.split('\n')[0]}`); process.exit(1); });
