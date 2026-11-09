// Sets up the RFP Orchestrator agent in Copilot Studio (ADR-0030): adds the four workflow tools, writes the
// instructions, adds the hooks, then saves and publishes. Uses the persistent profile in scripts/ui/profile
// (sign in once in the window it opens). Screenshots go to scripts/ui/shots.
//   PP_ENV_ID=<environment id> BOT_ID=<agent id> [STEPS=tools,instructions,hooks,publish] node scripts/ui/setup-orchestrator.js
// The portal has no stable automation IDs; selectors are visible text, and the command bar's "..." is located by
// position. Re-run after a portal change and read the screenshots.
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const ENV = process.env.PP_ENV_ID, BOT = process.env.BOT_ID;
const STEPS = (process.env.STEPS || 'tools,instructions,hooks,publish').split(',');
const TOOLS = ['RFP - Get Run Status', 'RFP - Explain Flag', 'RFP - Explain Answer', 'RFP - Rerun Stage'];
const HOOKS = [
  { event: 'Pre tool use', name: 'Check a rerun before it runs', workflow: 'RFP - Hook Pre Tool Use', tools: ['RFP - Rerun Stage'] },
  { event: 'Post tool use', name: 'Audit every tool call', workflow: 'RFP - Hook Post Tool Use', tools: [] },
];
const INSTRUCTIONS = [
  'You are the RFP Orchestrator. You answer questions about RFP pipeline runs using only your tools.',
  '- An RFP is identified by its key. Never assume which keys exist: pass the key the user gives to your tool, and if the tool finds no RFP with it, say so. If the user names an RFP another way, ask for the key.',
  'Run status, what a run is waiting for, recent run log or credits: use RFP - Get Run Status.',
  'Why a requirement has a flag: use RFP - Explain Flag.',
  'Why a requirement was answered as it was, or which library answer it used: use RFP - Explain Answer, and always quote the answer ID.',
  'Rerun from a stage only when the user explicitly asks, and only in two turns. In the turn with the request, never use RFP - Rerun Stage: reply only by asking the user to confirm, naming the RFP key and the stage, and say that the rerun replaces that stage and every later stage. This applies even when the user already gave the key and the stage.',
  'Use RFP - Rerun Stage only in a later turn, after the user answers yes to that question, and report its reply as given. If the answer is anything else, do not rerun.',
  'State only what your tools returned. Never state company facts, answers or compliance values of your own. Never add causes, rules, limits or advice about how the system works that a tool did not state. If a tool finds nothing, repeat its sentence as given and add nothing else.',
  'What a run is waiting for comes only from the current-state lines of RFP - Get Run Status. Its run log is history: never describe a past wait as current, and never call its credit sum a total.',
].join('\n');  // the editor turns the first "- " into a list and Enter continues it, so later items carry no dash

const SHOTS = path.join(__dirname, 'shots');
fs.mkdirSync(SHOTS, { recursive: true });
let n = 0;
const log = (m) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${m}`);
const shot = async (page, name) => { const f = path.join(SHOTS, `orch-${String(++n).padStart(2, '0')}-${name}.png`); await page.screenshot({ path: f }); log(`SHOT ${f}`); };
const pause = (page, ms = 3000) => page.waitForTimeout(ms);

async function openAgent(page) {
  await page.goto(`https://copilotstudio.microsoft.com/environments/${ENV}/agents/${BOT}`, { waitUntil: 'domcontentloaded' });
  await page.getByText('Tools', { exact: true }).first().waitFor({ timeout: 120000 });
  await pause(page, 5000);
}

async function addTools(page) {
  for (const tool of TOOLS) {
    if (await page.getByRole('button', { name: `Remove ${tool}` }).count()) { log(`= tool ${tool}`); continue; }
    // The "+" at the right end of the Tools row of the side panel (1500-px viewport).
    const row = await page.getByText('Connect the agent to external systems and actions').boundingBox();
    await page.mouse.click(1461, row.y - 10);
    const dialog = page.getByRole('dialog');
    await dialog.getByText('Workflows', { exact: true }).first().click();
    await dialog.getByPlaceholder('Search').fill(tool);
    await pause(page, 3000);
    await dialog.getByText(tool, { exact: true }).first().click();
    await page.getByRole('button', { name: `Remove ${tool}` }).waitFor({ timeout: 60000 });
    await pause(page, 2000);
    if (await dialog.count()) { await page.keyboard.press('Escape'); await pause(page, 1000); }
    log(`+ tool ${tool}`);
  }
  await shot(page, 'tools');
}

async function writeInstructions(page) {
  const editor = page.locator('[contenteditable=true]').first();
  await editor.click();
  await page.keyboard.press('Control+A');
  await page.keyboard.press('Delete');
  const lines = INSTRUCTIONS.split('\n');
  for (const [i, line] of lines.entries()) { await page.keyboard.type(line); if (i < lines.length - 1) await page.keyboard.press('Enter'); }
  await page.keyboard.press('Control+S');  // the instructions editor saves on Ctrl+S; the save icon alone didn't keep them
  await pause(page, 6000);
  log('instructions written');
  await shot(page, 'instructions');
}

async function commandBarMenu(page, item) {
  // The "..." left of the share icons on the agent's command bar (no accessible name).
  const publish = await page.getByRole('button', { name: 'Publish', exact: true }).boundingBox();
  await page.mouse.click(publish.x - 238, publish.y + publish.height / 2);
  await pause(page, 1500);
  await page.getByText(item, { exact: true }).click();
  await pause(page, 3000);
}

async function addHooks(page) {
  // One visit to the Hooks dialog: after Done it returns to the list, where Create adds the next hook.
  await commandBarMenu(page, 'Hooks');
  const dialog = page.getByRole('dialog');
  for (const hook of HOOKS) {
    if (await dialog.getByText(hook.name, { exact: true }).count()) { log(`= hook ${hook.name}`); continue; }
    log('create'); await dialog.getByRole('button', { name: 'Create' }).first().click();
    await pause(page, 2000);
    log('event'); await dialog.getByText('Start (pre-loop)', { exact: true }).first().click();
    await pause(page, 1000);
    await page.getByText(hook.event, { exact: true }).last().click();
    await pause(page, 1500);
    log('name');
    const nameBox = dialog.getByRole('textbox', { name: /Name/ });
    if (await nameBox.count()) await nameBox.first().fill(hook.name);
    for (const tool of hook.tools) {
      log(`tool ${tool}`); await dialog.getByText('All tools', { exact: true }).first().click();
      await pause(page, 1000);
      await page.getByText(tool, { exact: true }).last().click();
      await page.keyboard.press('Escape');
      await pause(page, 1000);
    }
    log('workflow'); await dialog.getByText('Select a workflow or create new').click();
    await pause(page, 2000);
    await shot(page, `hook-${hook.event.replace(/ /g, '-')}-workflows`);
    await page.getByText(hook.workflow, { exact: true }).last().click();
    await pause(page, 1500);
    await shot(page, `hook-${hook.event.replace(/ /g, '-')}-filled`);
    log('done'); await dialog.getByRole('button', { name: 'Done' }).click();
    await pause(page, 3000);
    log(`+ hook ${hook.name}`);
  }
  await shot(page, 'hooks');
  await dialog.getByRole('button', { name: 'Close' }).first().click();
  await pause(page, 2000);
}

async function saveAndPublish(page, publish) {
  // The save icon left of Review (its accessible name isn't clickable as a button).
  const publishBox = await page.getByRole('button', { name: 'Publish', exact: true }).boundingBox();
  await page.mouse.click(publishBox.x - 130, publishBox.y + publishBox.height / 2);
  await pause(page, 8000);
  log('saved');
  if (publish) {
    await page.getByRole('button', { name: 'Publish', exact: true }).click();
    await pause(page, 3000);
    const confirm = page.getByRole('dialog').getByRole('button', { name: 'Publish' });
    if (await confirm.count()) await confirm.click();
    await pause(page, 30000);
    log('publish requested');
  }
  await shot(page, 'saved');
}

(async () => {
  if (!ENV || !BOT) throw new Error('Set PP_ENV_ID and BOT_ID');
  const ctx = await chromium.launchPersistentContext(path.join(__dirname, 'profile'), { headless: false, viewport: { width: 1500, height: 950 } });
  const page = ctx.pages()[0] || await ctx.newPage();
  try {
    await openAgent(page);
    if (STEPS.includes('tools')) await addTools(page);
    if (STEPS.includes('instructions')) await writeInstructions(page);
    if (STEPS.includes('tools') || STEPS.includes('instructions')) await saveAndPublish(page, false);
    if (STEPS.includes('hooks')) { await addHooks(page); await saveAndPublish(page, false); }
    if (STEPS.includes('publish')) await saveAndPublish(page, true);
  } catch (e) {
    log(`ERROR ${e.message.split('\n')[0]}`);
    await shot(page, 'error');
    process.exitCode = 1;
  } finally {
    await ctx.close();
  }
})();
