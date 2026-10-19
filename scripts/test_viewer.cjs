// Run with a locally installed Playwright and Chromium/Chrome.
// This checks the viewer/transport, not the native Chromium capture service.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const root = path.resolve(__dirname, '..');
  const port = 18765;
  const base = `http://127.0.0.1:${port}`;
  const server = spawn('python3', ['-m', 'server.server', '--port', String(port)], {cwd: root, stdio: 'pipe'});
  let browser;
  try {
    await new Promise((resolve, reject) => {
      server.stdout.once('data', resolve);
      server.once('error', reject);
      server.once('exit', code => reject(new Error(`server exited ${code}`)));
    });
    browser = await chromium.launch({headless: true,
      executablePath: process.env.TEST_BROWSER || '/usr/bin/google-chrome'});
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base);
    await page.getByRole('status').filter({hasText: 'Ожидание первого кадра'}).waitFor();
    const source = await browser.newPage({viewport: {width: 900, height: 600}});
    await source.setContent('<body style="background:#dde8ff;font:32px sans-serif"><h1>Frame 1</h1>Native-size viewer test</body>');
    const jpeg = await source.screenshot({type: 'jpeg', quality: 80});
    const captured = Date.now();
    let response = await fetch(base + '/api/frame', {method: 'POST', body: jpeg, headers: {
      'Content-Type': 'image/jpeg', 'X-Frame-Session': 'viewer-test',
      'X-Frame-Sequence': '1', 'X-Captured-At': String(captured),
    }});
    assert.equal(response.status, 204);
    await page.waitForFunction(() => document.querySelector('#frame').naturalWidth === 900);
    await page.getByRole('status').filter({hasText: 'Обновляется'}).waitFor();
    const latency = Date.now() - captured;
    assert(latency < 2500, `viewer latency ${latency} ms exceeds 2500 ms`);
    const frameURL = await page.locator('#frame').getAttribute('src');
    await page.getByRole('status').filter({hasText: 'Нет свежих кадров'}).waitFor({timeout: 6000});
    assert.equal(await page.locator('#frame').getAttribute('src'), frameURL);
    await source.setContent('<body style="background:#ffe8dd;font:32px sans-serif"><h1>Frame 2</h1>Reconnected</body>');
    response = await fetch(base + '/api/frame', {method: 'POST', body: await source.screenshot({type: 'jpeg'}), headers: {
      'Content-Type': 'image/jpeg', 'X-Frame-Session': 'viewer-test',
      'X-Frame-Sequence': '2', 'X-Captured-At': String(Date.now()),
    }});
    assert.equal(response.status, 204);
    await page.getByRole('status').filter({hasText: 'Обновляется'}).waitFor();
    assert.notEqual(await page.locator('#frame').getAttribute('src'), frameURL);
    assert.deepEqual(errors, []);
    console.log(`Viewer passed: JPEG decode, native dimensions, stale status, recovery; latency ${latency} ms.`);
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
