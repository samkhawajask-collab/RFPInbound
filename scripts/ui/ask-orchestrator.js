// Asks the RFP Orchestrator questions in Copilot Studio's test chat and prints its replies (ADR-0030, drill h).
//   PP_ENV_ID=<environment id> BOT_ID=<agent id> node scripts/ui/ask-orchestrator.js "What is the status of RFP A?" ["..."]
// Uses the persistent profile in scripts/ui/profile. Each question waits up to WAIT_S seconds (default 120) for the
// reply to stop changing. Screenshots go to scripts/ui/shots.
const { chromium } = require('playwright');
const path = require('path');

const ENV = process.env.PP_ENV_ID, BOT = process.env.BOT_ID;
const WAIT = Number(process.env.WAIT_S || 120) * 1000;
const log = (m) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${m}`);

(async () => {
  const questions = process.argv.slice(2);
  const ctx = await chromium.launchPersistentContext(path.join(__dirname, 'profile'), { headless: false, viewport: { width: 1500, height: 950 } });
  const page = ctx.pages()[0] || await ctx.newPage();
  try {
    await page.goto(`https://copilotstudio.microsoft.com/environments/${ENV}/agents/${BOT}`, { waitUntil: 'domcontentloaded' });
    await page.getByText('Tools', { exact: true }).first().waitFor({ timeout: 120000 });
    await page.waitForTimeout(5000);
    // The Preview tab's chat, never the Build tab's instructions editor (which is also contenteditable).
    await page.getByText('Preview', { exact: true }).first().click();
    await page.getByText('Message RFP Orchestrator').waitFor({ timeout: 60000 });
    const box = page.getByTestId('chat-input-textarea');
    for (const [i, q] of questions.entries()) {
      const before = await page.innerText('body');
      await box.click();
      await page.keyboard.type(q);
      // Enter leaves the text in the box, and the next question would be appended to it. Send with the arrow button,
      // which has no stable name: it's the circle at the right end of the input (1500-px viewport).
      const send = page.getByRole('button', { name: /send/i }).last();
      if (await send.count()) await send.click();
      else await page.mouse.click(1223, 837);
      log(`ASKED ${q}`);
      let last = '', stable = 0;
      const deadline = Date.now() + WAIT;
      while (Date.now() < deadline && stable < 4) {
        await page.waitForTimeout(5000);
        const now = await page.innerText('body');
        // A blank bubble appears first, so a reply is only ready once it has text after the question and the footer.
        const tail = now.slice(now.lastIndexOf(q) + q.length).replace(/AI-generated content.*|Message RFP Orchestrator|[​-‍﻿\s]/gs, '');
        stable = now === last && now !== before && tail.length > 0 ? stable + 1 : 0;
        last = now;
      }
      const reply = last.slice(last.lastIndexOf(q) + q.length).trim().slice(0, 3000);
      console.log(`REPLY ${i + 1}:\n${reply}\n`);
      await page.screenshot({ path: path.join(__dirname, 'shots', `ask-${i + 1}.png`) });
    }
  } finally {
    await ctx.close();
  }
})();
