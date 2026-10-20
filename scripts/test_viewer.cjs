// Run with a locally installed Playwright and Chromium/Chrome.
// This checks the viewer/transport, not the native Chromium capture service.
const { chromium } = require('playwright');
const { spawn, execFileSync } = require('node:child_process');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const root = path.resolve(__dirname, '..');
  const port = 18765;
  const base = `http://127.0.0.1:${port}`;
  const server = spawn('python3', ['-m', 'server.server', '--port', String(port), '--udp-port', String(port + 1)], {cwd: root, stdio: 'pipe'});
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
    function send(jpeg, sequence, captured) {
      execFileSync('python3', ['-m', 'server.send_frame', '--port', String(port + 1),
        '--session', '00000000-0000-4000-8000-000000000001',
        '--sequence', String(sequence), '--captured', String(captured)], {cwd: root, input: jpeg});
    }
    send(jpeg, 1, captured);
    await page.waitForFunction(() => document.querySelector('#frame').naturalWidth === 900);
    await page.getByRole('status').filter({hasText: 'Обновляется'}).waitFor();
    const latency = Date.now() - captured;
    assert(latency < 2500, `viewer latency ${latency} ms exceeds 2500 ms`);
    const frameURL = await page.locator('#frame').getAttribute('src');
    await page.getByRole('status').filter({hasText: 'Нет свежих кадров'}).waitFor({timeout: 6000});
    assert.equal(await page.locator('#frame').getAttribute('src'), frameURL);
    await source.setContent('<body style="background:#ffe8dd;font:32px sans-serif"><h1>Frame 2</h1>Reconnected</body>');
    send(await source.screenshot({type: 'jpeg'}), 2, Date.now());
    await page.getByRole('status').filter({hasText: 'Обновляется'}).waitFor();
    assert.notEqual(await page.locator('#frame').getAttribute('src'), frameURL);
    assert.deepEqual(errors, []);
    console.log(`UDP viewer passed: JPEG decode, native dimensions, stale status, recovery; latency ${latency} ms.`);
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
