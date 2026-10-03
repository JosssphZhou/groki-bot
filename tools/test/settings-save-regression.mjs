// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0

// Run with ego-browser nodejs. Prepend globalThis.settingsSaveQa = {repoRoot: ...}.
// The browser runtime does not inherit the shell's directory or environment.
// Optional fields: spaceId (resume), url (old HTML), output (JSON), case (one group).
const fs = await import('node:fs/promises');
const {pathToFileURL} = await import('node:url');
const options = globalThis.settingsSaveQa;
if (!options?.repoRoot) throw new Error('Set settingsSaveQa.repoRoot to the absolute checkout path');
const fixture = await fs.readFile(options.repoRoot + '/tools/test/settings-save-mock.js', 'utf8');
const task = await taskSpace(options.spaceId ?? 'Settings save regression');
console.log({spaceId: task.spaceId});
const page = task.page('p1');
const url = options.url || pathToFileURL(options.repoRoot + '/tools/settings.html').href;
const results = [];
const steps = [];
async function record(step) {
  const state = await page.evaluate(() => ({
    language: window.__settingsSaveMock.language,
    pending: window.__settingsSaveMock.pending,
    writes: window.__settingsSaveMock.writes,
    applied: window.__settingsSaveMock.applied,
    applyAttempts: window.__settingsSaveMock.applyAttempts,
    selected: document.getElementById('device-language').value,
    button: document.getElementById('btn-apply').textContent,
    log: document.getElementById('log').textContent,
  }));
  steps.push({step, ...state});
  console.log(JSON.stringify({step, ...state}));
  return state;
}
function check(ok, message) {
  results.push({check: message, passed: ok});
  console.log(`${ok ? 'PASS' : 'FAIL'}: ${message}`);
  if (!ok) throw new Error(message);
}
async function reset(lang = 'en') {
  await page.goto(url);
  await page.click(`loc=css:.lang-switch button[data-lang="${lang}"]`);
  await page.evaluate(fixture);
  await page.click('loc=css:[role="tab"][data-tab="conn"]');
}
async function languageRegression() {
  await reset();
  check(await page.evaluate(() => document.getElementById('device-language').value === 'en'), 'Active device language reads English');
  await page.selectOption('loc=css:#device-language', 'zh');
  await page.click('loc=css:[role="tab"][data-tab="chat"]');
  await page.selectOption('loc=css:#provider', '1');
  await page.evaluate(() => { window.__settingsSaveMock.failProvider = true; });
  await page.click('loc=css:#btn-apply');
  await page.waitForFunction(() => document.getElementById('log').textContent.includes('before provider'));
  const interrupted = await record('Chinese staged, then disconnected before provider and Apply');
  check(interrupted.pending === 'zh' && interrupted.language === 'en' && interrupted.applied === 0,
    'Interrupted save retains staged Chinese while active language stays English');
  await page.evaluate(async () => {
    window.__settingsSaveMock.failProvider = false;
    await window.__settingsSaveMock.connect();
  });
  await page.click('loc=css:[role="tab"][data-tab="conn"]');
  const reconnected = await record('Reconnect shows English; keep the selection unchanged');
  check(reconnected.selected === 'en' && reconnected.pending === 'zh', 'Reconnect reads active English without clearing staged Chinese');
  await page.click('loc=css:#btn-apply');
  await page.waitForFunction(() => window.__settingsSaveMock.applied === 1);
  const saved = await record('Save unchanged English');
  check(saved.writes.length === 3 && saved.writes[1].language === 'en' && saved.writes[2].apply === 1 &&
    saved.language === 'en' && saved.button === 'Restarting…',
    'Saving unchanged English overwrites staged Chinese with en before Apply');
  await page.evaluate(async () => { window.__settingsSaveMock.disconnect(); await window.__settingsSaveMock.connect(); });
  check(await page.evaluate(() => document.getElementById('device-language').value === 'en'), 'Readback after simulated restart stays English');
}
async function applyRegression() {
  let failed = false;
  for (const [phase, lang] of [['before transport', 'en'], ['before transport', 'zh'], ['before response', 'en'], ['before response', 'zh']]) {
    try {
      await reset(lang);
      await page.selectOption('loc=css:#device-language', 'zh');
      await page.evaluate((phase) => {
        if (phase === 'before transport') window.__settingsSaveMock.armBeforeApplyDisconnect();
        else window.__settingsSaveMock.failApplyWrite = true;
      }, phase);
      await page.click('loc=css:#btn-apply');
      await page.waitForFunction(() => !bleDevice.gatt.connected &&
        /Applied; restarting|已应用，正在重启|Save result unconfirmed|保存结果未确认/.test(document.getElementById('log').textContent));
      const disconnected = await record(`Apply disconnected ${phase} (${lang})`);
      check(disconnected.applied === 0 && disconnected.language === 'en' &&
        disconnected.applyAttempts === (phase === 'before transport' ? 0 : 1),
        `Apply is unconfirmed ${phase}`);
      const expected = lang === 'en' ? 'Save result unconfirmed. Reconnect to check.' : '保存结果未确认，请重新连接后检查';
      check(disconnected.button !== 'Restarting…' && disconnected.button !== '重启中…' &&
        !disconnected.log.includes('Applied; restarting') && !disconnected.log.includes('已应用，正在重启') && disconnected.log.includes(expected),
        `Disconnect ${phase} (${lang}) reports an unconfirmed save instead of restarting`);
    } catch (error) {
      failed = true;
      console.log(`Apply scenario failure (${phase}, ${lang}):`, String(error));
    }
  }
  if (failed) throw new Error('One or more unconfirmed Apply scenarios failed');
}
async function availabilityRegression() {
  for (const reason of ['old firmware', 'read failure']) {
    await reset();
    await page.evaluate(async (reason) => {
      window.__settingsSaveMock.language = 'zh';
      if (reason === 'old firmware') chrDeviceLanguage = null;
      else window.__settingsSaveMock.failRead = true;
      await loadDeviceLanguage();
    }, reason);
    check(await page.evaluate(() => document.getElementById('device-language').disabled &&
      document.getElementById('device-language').dataset.initial === undefined), `${reason} disables the language control`);
    await page.click('loc=css:#btn-apply');
    await page.waitForFunction(() => window.__settingsSaveMock.applied === 1);
    const saved = await record(`Save with unavailable language (${reason})`);
    check(saved.writes.length === 1 && saved.writes[0].apply === 1 && saved.language === 'zh',
      `${reason} skips language writes and preserves the device language`);
  }
}
for (const [name, run] of [['language', languageRegression], ['apply', applyRegression], ['availability', availabilityRegression]]) {
  if (options.case && options.case !== name) continue;
  try { await run(); }
  catch (error) {
    console.log(`REGRESSION FAILURE (${name}):`, String(error));
    process.exitCode = 1;
  }
}
if (options.output) {
  await fs.writeFile(options.output, JSON.stringify({url, simulation: 'BLE, active/staged device state and reboot are mocked; real HTML, clicks, disconnect handler and AES-GCM', results, steps}, null, 2));
}
console.log(`Regression exit code: ${process.exitCode || 0}`);
