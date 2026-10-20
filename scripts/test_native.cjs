// Native capture end-to-end test. Run on Linux with:
//   xvfb-run -a node scripts/test_native.cjs
// Requires Playwright, Python Pillow and openbox; uses the actual fork binary.
const { chromium } = require('playwright');
const { spawn, execFileSync } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const http = require('node:http');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const executable = process.env.TEST_BROWSER || path.join(root, 'chromium/src/out/Observer/chrome');
const base = 'http://127.0.0.1:8765';
const scale = Number(process.env.TEST_FRAME_SCALE || '1.0');
assert(Number.isFinite(scale) && scale > 0 && scale <= 1, 'Invalid TEST_FRAME_SCALE');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let server, browser, context, wm, fixture, profile;

async function startServer() {
  server = spawn('python3', ['-m', 'server.server'], {cwd: root, stdio: 'pipe'});
  await new Promise((resolve, reject) => {
    server.stdout.once('data', resolve);
    server.once('error', reject);
    server.once('exit', code => reject(new Error(`server exited ${code}`)));
  });
}

async function frame() {
  const response = await fetch(base + '/api/frame');
  if (response.status === 204) return null;
  assert.equal(response.status, 200);
  const bytes = Buffer.from(await response.arrayBuffer());
  const decoded = JSON.parse(execFileSync('python3', ['-c',
    'import sys,io,json;from PIL import Image;im=Image.open(io.BytesIO(sys.stdin.buffer.read())).convert("RGB");print(json.dumps({"width":im.width,"height":im.height,"color":im.getpixel((im.width//2,im.height//2))}))'], {input: bytes}));
  return {...decoded, bytes,
    sequence: Number(response.headers.get('X-Frame-Sequence')),
    captured: Number(response.headers.get('X-Captured-At')),
    received: Number(response.headers.get('X-Received-At'))};
}

async function waitFrame(predicate, timeout = 12000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    const current = await frame();
    if (current && predicate(current)) return current;
    await sleep(100);
  }
  throw new Error('Native capture did not produce the expected frame');
}

const matches = (actual, expected) => actual.every((value, i) => Math.abs(value - expected[i]) <= 8);

(async () => {
  assert(fs.existsSync(executable), `Missing native build: ${executable}`);
  if (process.platform === 'linux') {
    assert(process.env.DISPLAY, 'Run with xvfb-run -a');
    wm = spawn('openbox', [], {stdio: 'ignore'});
    await sleep(500);
  }
  await startServer();
  profile = fs.mkdtempSync(path.join(os.tmpdir(), 'chromium-observer-test-'));
  context = await chromium.launchPersistentContext(profile, {executablePath: executable, headless: false,
    viewport: {width: 900, height: 600},
    chromiumSandbox: true, ignoreDefaultArgs: ['--enable-automation'],
    args: ['--no-first-run', '--no-default-browser-check', '--disable-dev-shm-usage']});
  browser = context.browser();
  const page = context.pages()[0] || await context.newPage();
  await page.setContent('<body style="margin:0;background:rgb(20,80,200)">');
  await page.bringToFront();
  let current = await waitFrame(f => matches(f.color, [20,80,200]));
  assert.equal(current.width, Math.max(1, Math.round(900 * scale)));
  assert.equal(current.height, Math.max(1, Math.round(600 * scale)));
  assert(Date.now() - current.captured < 2500);
  const samples = [];
  for (let i = 0; i < 6; ++i) {
    const next = await waitFrame(f => f.sequence > current.sequence);
    samples.push(next.received - current.received);
    assert(Date.now() - next.captured < 2500);
    current = next;
  }
  assert(samples.every(ms => ms >= 650 && ms <= 1600), `Capture cadence: ${samples}`);
  await page.setContent('<body style="margin:0"><div style="height:900px;background:rgb(200,20,20)"></div><div style="height:900px;background:rgb(20,160,60)"></div>');
  current = await waitFrame(f => matches(f.color, [200,20,20]));
  await page.evaluate(() => scrollTo(0, 1000));
  current = await waitFrame(f => matches(f.color, [20,160,60]));
  const second = await context.newPage();
  await second.setContent('<body style="background:rgb(240,160,20)">');
  await second.bringToFront();
  current = await waitFrame(f => matches(f.color, [240,160,20]));
  await second.close();
  await page.bringToFront();
  current = await waitFrame(f => matches(f.color, [20,160,60]));
  fixture = http.createServer((request, response) => {
    response.setHeader('Content-Type', 'text/html');
    response.end(request.url === '/child'
      ? '<body style="margin:0;background:rgb(160,20,200)">'
      : '<body style="margin:0"><iframe style="border:0;width:100vw;height:100vh" src="http://localhost:18767/child"></iframe>');
  });
  await new Promise(resolve => fixture.listen(18767, '127.0.0.1', resolve));
  await page.goto('http://127.0.0.1:18767/parent');
  current = await waitFrame(f => matches(f.color, [160,20,200]));
  const cdp = await context.newCDPSession(page);
  const {windowId} = await cdp.send('Browser.getWindowForTarget');
  await cdp.send('Browser.setWindowBounds', {windowId, bounds: {windowState: 'minimized'}});
  await sleep(2500);
  const minimized = await frame();
  await sleep(1500);
  assert.equal((await frame()).sequence, minimized.sequence, 'Minimized window should keep its last frame');
  await cdp.send('Browser.setWindowBounds', {windowId, bounds: {windowState: 'normal'}});
  await page.bringToFront();
  current = await waitFrame(f => f.sequence > minimized.sequence && matches(f.color, [160,20,200]));
  // The producer must reconnect after a receiver restart, without user action.
  server.kill('SIGTERM');
  await new Promise(resolve => server.once('exit', resolve));
  await sleep(250);
  await startServer();
  current = await waitFrame(f => matches(f.color, [160,20,200]));
  fs.mkdirSync(path.join(root, 'artifacts'), {recursive: true});
  fs.writeFileSync(path.join(root, 'artifacts/native-last-frame.jpg'), current.bytes);
  const result = {passed: true, cadence_ms: samples, scale, viewport: [current.width, current.height],
    final_frame_age_ms: Date.now() - current.captured,
    checks: ['rendered pixels', 'viewport dimensions', '1 Hz cadence', 'scroll', 'tab switch', 'tab close', 'cross-origin iframe', 'minimize/restore', 'server restart'],
    binary: executable};
  fs.writeFileSync(path.join(root, 'artifacts/native-validation.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
})().catch(error => {console.error(error); process.exitCode = 1;}).finally(async () => {
  if (context) await context.close();
  if (server) server.kill('SIGTERM');
  if (wm) wm.kill('SIGTERM');
  if (fixture) fixture.close();
  if (profile) fs.rmSync(profile, {recursive: true, force: true});
});
