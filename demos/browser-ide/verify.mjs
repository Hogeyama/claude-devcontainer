// Run against local-browser.py --test. Never points probes at real services.
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, mkdirSync, unlinkSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const dir = fileURLToPath(new URL('.', import.meta.url));
const out = process.env.DEMO_EVIDENCE || '/tmp/browser-ide-evidence';
mkdirSync(out, { recursive: true });
const compose = (...args) => execFileSync('docker', ['compose', '-f', dir + 'compose.yaml', ...args], { encoding: 'utf8' });
const receiver = () => JSON.parse(compose('exec', '-T', 'receiver', 'python3', '-c',
  'import json,pathlib;p=pathlib.Path("/tmp/received.jsonl");print(json.dumps([json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []))'));
const results = { date: new Date().toISOString(), phases: {}, limitations: ['IME needs native manual verification; automation is not an IME test.'] };
let browser;
let sentinelCreated = false;
async function command(page, title) {
  await page.bringToFront();
  await page.keyboard.press('Escape');
  await page.keyboard.press('Control+Shift+P');
  await page.getByRole('textbox', { name: 'Type the name of a command to run.' }).fill('>' + title);
  await page.locator('.quick-input-list .monaco-list-row').filter({hasText: title}).first().click();
}
async function frameWith(page, text) {
  for (let n = 0; n < 60; n++) {
    for (const f of page.frames()) {
      if (f === page.mainFrame()) continue;
      if ((await f.locator('body').innerText({timeout: 1000}).catch(() => '')).includes(text)) return f;
    }
    await page.waitForTimeout(250);
  }
  throw Error('Missing frame: ' + text);
}
try {
  writeFileSync('/tmp/browser-ide-host-sentinel', 'DEMO_ONLY\n', {flag:'wx', mode:0o600});
  sentinelCreated = true;
  compose('restart', 'ide');
  browser = await chromium.connectOverCDP('http://127.0.0.1:' + (process.env.DEMO_CDP_PORT || 19222));
  const context = browser.contexts()[0];
  context.setDefaultTimeout(10000);
  const ide = context.pages().find(p => p.url().includes('ide.localhost')) || await context.newPage();
  for (let n=0; n<30; n++) {
    try { const r = await ide.goto('http://ide.localhost:8000/?folder=/workspace'); if(r.status()===200) break; }
    catch {}
    await ide.waitForTimeout(300);
  }
  await ide.getByRole('treeitem', { name: /README.md/ }).waitFor({ timeout: 30000 });
  await ide.getByRole('treeitem', { name: /README.md/ }).dblclick();
  await ide.waitForTimeout(1500);
  await command(ide, 'Markdown: Open Preview');
  const markdown = await frameWith(ide, 'Browser IDE boundary demo');
  results.markdownPreview = (await markdown.locator('h1').innerText()) === 'Browser IDE boundary demo';
  await ide.screenshot({ path: out + '/markdown.png' });
  // Test the normal IDE terminal, including execution of shared workspace code.
  await command(ide, 'Terminal: Create New Terminal');
  await ide.waitForTimeout(1500);
  await ide.keyboard.type('node /workspace/hello.js > /tmp/demo-terminal-result');
  await ide.keyboard.press('Enter');
  for (let i = 0; i < 30; i++) {
    try {
      results.terminal = compose('exec', '-T', 'ide', 'cat', '/tmp/demo-terminal-result').trim();
      break;
    } catch { await ide.waitForTimeout(250); }
  }
  assert.equal(results.terminal, 'Hello from the isolated code-server terminal');
  const sandboxPage = await context.newPage();
  await sandboxPage.goto('chrome://sandbox');
  results.chromiumSandbox = await sandboxPage.locator('body').innerText();
  assert.match(results.chromiumSandbox, /Seccomp-BPF sandbox\s+Yes/);
  await sandboxPage.close();
  for (const control of [true, false]) {
    const phase = control ? 'control' : 'restricted';
    execFileSync('docker', ['compose', '-f', dir + 'compose.yaml', 'up', '-d', 'proxy'], {
      env: { ...process.env, DEMO_CONTROL: control ? '1' : '0' }, stdio: 'pipe',
    });
    await ide.waitForTimeout(1500);
    const before = receiver().length;
    const page = await context.newPage();
    await page.goto('http://preview.localhost:8001/');
    for (const id of ['form', 'link', 'redirect']) {
      const popupPromise = context.waitForEvent('page');
      await page.locator('#' + id).click();
      const popup = await popupPromise;
      await popup.waitForLoadState('domcontentloaded');
      if (!control) assert.match(await popup.locator('body').innerText(), /Blocked by demo proxy/);
      await popup.close();
    }
    await page.locator('#fetch').click();
    await page.locator('#beacon').click();
    await page.goto('http://preview.localhost:8001/auto');
    await page.waitForTimeout(500);
    await ide.reload();
    await ide.getByRole('treeitem', { name: /README.md/ }).waitFor({ timeout: 30000 });
    await command(ide, 'Demo: Open Boundary Probe');
    const webview = await frameWith(ide, 'Remote extension boundary probe');
    await webview.getByRole('button', { name: 'Send harmless POST from webview' }).click();
    await ide.waitForTimeout(1000);
    const events = receiver().slice(before);
    results.phases[phase] = { receiverEvents: events, count: events.length };
    if (control) {
      for (const path of ['/form', '/fetch', '/beacon', '/link', '/redirected', '/auto-beacon', '/image?marker=DEMO_ONLY', '/webview']) {
        assert.ok(events.some(e => e.path === path), 'Positive control did not reach receiver: ' + path);
      }
    } else {
      assert.equal(events.length, 0, 'A forbidden request reached the receiver');
      await ide.screenshot({ path: out + '/webview.png' });
      await page.goto('http://preview.localhost:8001/');
      await page.screenshot({ path: out + '/preview.png' });
      const popPromise = context.waitForEvent('page');
      await page.locator('#form').click();
      const pop = await popPromise;
      await pop.waitForLoadState('domcontentloaded');
      await pop.screenshot({ path: out + '/blocked.png' });
      await pop.close();
    }
    await page.close();
  }
  results.remoteExtension = JSON.parse(compose('exec', '-T', 'ide', 'cat', '/tmp/boundary-report.json'));
  assert.equal(results.remoteExtension.newLocalRegistered, false);
  assert.match(results.remoteExtension.newLocal, /not found/);
  assert.match(results.remoteExtension.localFile, /ENOENT|EntryNotFound/);
  if (process.env.DEMO_RUNTIME) {
    results.osBoundary = JSON.parse(readFileSync(process.env.DEMO_RUNTIME + '/home/boundary.json'));
    results.launcher = JSON.parse(readFileSync(process.env.DEMO_RUNTIME + '/launcher.json'));
    assert.notEqual(results.osBoundary.network_namespace, results.launcher.host_network_namespace);
    assert.equal(results.osBoundary.proxy, 'connected');
    for (const name of ['host_loopback', 'direct_external', 'direct_dns']) assert.notEqual(results.osBoundary[name], 'connected');
    assert.equal(results.osBoundary.external_opener_exit, 126);
    assert.equal(results.osBoundary.session_bus_present, false);
    assert.equal(results.osBoundary.host_home_present, false);
    for (const name of ['udp_dns', 'ipv6_direct']) assert.notEqual(results.osBoundary[name], 'sent');
  }
  results.passed = true;
  console.log(JSON.stringify(results, null, 2));
} catch (e) {
  results.passed = false;
  results.error = String(e);
  console.error(e);
  process.exitCode = 1;
} finally {
  // Never leave the positive-control receiver permission enabled after failure.
  execFileSync('docker', ['compose', '-f', dir + 'compose.yaml', 'up', '-d', 'proxy'], {
    env: { ...process.env, DEMO_CONTROL: '0' }, stdio: 'pipe',
  });
  writeFileSync(out + '/verification.json', JSON.stringify(results, null, 2));
  if (browser) await browser.close();
  if (sentinelCreated) unlinkSync('/tmp/browser-ide-host-sentinel');
}
