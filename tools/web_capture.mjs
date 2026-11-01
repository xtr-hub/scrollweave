#!/usr/bin/env node
/* Headless-Chrome capture helper for tools/web_smoke.py.
 *
 * This intentionally uses only Node built-ins.  It drives Chrome over the
 * DevTools Protocol, so no Playwright/Puppeteer/npm install is required.
 *
 * Job file: { root: "...", pages: [{ id, html, viewport:[w,h],
 *            step_fraction, jitter }] }
 * Results: <root>/pages/<id>/capture/frame_NNN.png, truth_full.png,
 *          capture.json
 */
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const CHROME_CANDIDATES = [
  process.env.CHROME,
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  '/usr/bin/google-chrome',
  '/usr/bin/google-chrome-stable',
  '/usr/bin/chromium',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].filter(Boolean);

function findChrome() {
  for (const candidate of CHROME_CANDIDATES) {
    if (existsSync(candidate)) return candidate;
  }
  throw new Error('Chrome not found; set CHROME=/path/to/chrome');
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function waitForFile(path, timeoutMs = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try { return readFileSync(path, 'utf8'); } catch { await sleep(80); }
  }
  throw new Error(`timed out waiting for ${path}`);
}

function rpcClient(ws) {
  let next = 0;
  const pending = new Map();
  ws.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const resolve = pending.get(message.id);
      pending.delete(message.id);
      resolve(message);
    }
  };
  return (method, params = {}, sessionId = undefined) => new Promise((resolve, reject) => {
    const id = ++next;
    pending.set(id, message => {
      if (message.error) reject(new Error(`${method}: ${message.error.message}`));
      else resolve(message.result || {});
    });
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    ws.send(JSON.stringify(payload));
  });
}

async function evaluate(send, sessionId, expression, awaitPromise = false) {
  const result = await send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise,
    userGesture: true,
  }, sessionId);
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || 'Runtime.evaluate failed');
  }
  return result.result ? result.result.value : undefined;
}

async function screenshot(send, sessionId, path, clip = null) {
  const params = { format: 'png', fromSurface: true, captureBeyondViewport: !!clip };
  if (clip) params.clip = { x: 0, y: 0, width: clip.width, height: clip.height, scale: 1 };
  const result = await send('Page.captureScreenshot', params, sessionId);
  writeFileSync(path, Buffer.from(result.data, 'base64'));
}

async function waitForReady(send, sessionId) {
  for (let i = 0; i < 80; i++) {
    const state = await evaluate(send, sessionId, 'document.readyState');
    if (state === 'complete') {
      await evaluate(send, sessionId, 'document.fonts ? document.fonts.ready.then(() => true) : true', true);
      await sleep(120);
      return;
    }
    await sleep(80);
  }
  throw new Error('page did not reach readyState=complete');
}

function makeScrolls(scrollHeight, vh, fraction, jitter, seed) {
  const max = Math.max(0, Math.round(scrollHeight - vh));
  if (max <= 0) return [0];
  const minStep = Math.max(48, Math.round(vh * 0.28));
  // Keep a usable amount of overlap at the final capture too.  Fixed chrome can
  // reduce the content band below the viewport height, so cap the step at 48%
  // of the viewport instead of forcing one huge jump to the page bottom.
  const maxStep = Math.max(minStep, Math.round(vh * 0.48));
  const step = Math.max(minStep, Math.min(maxStep, Math.round(vh * fraction)));
  const ys = [0];
  while (max - ys[ys.length - 1] > maxStep) {
    let next = ys[ys.length - 1] + step;
    if (jitter > 0) {
      const pseudo = ((seed * 73 + ys.length * 151 + 19) % 101) / 100 - 0.5;
      next += Math.round(pseudo * 2 * jitter * vh);
    }
    next = Math.max(ys[ys.length - 1] + minStep, Math.min(max, next, ys[ys.length - 1] + maxStep));
    ys.push(next);
    if (ys.length >= 64) break;
  }
  if (ys[ys.length - 1] !== max) {
    // Append the final position; replacing the previous frame would create one
    // large gap at the page bottom and destroy overlap there.
    ys.push(max);
  }
  return [...new Set(ys)].sort((a, b) => a - b);
}

async function main() {
  const args = process.argv.slice(2);
  const jobPath = args[0];
  if (!jobPath) throw new Error('usage: node tools/web_capture.mjs jobs.json');
  const job = JSON.parse(readFileSync(jobPath, 'utf8'));
  const root = resolve(job.root);
  const chrome = findChrome();
  const profile = mkdtempSync(join(tmpdir(), 'scrollweave-chrome-'));
  const child = spawn(chrome, [
    '--headless=new',
    '--disable-gpu',
    '--hide-scrollbars',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-background-networking',
    '--disable-component-update',
    '--disable-sync',
    '--disable-extensions',
    '--force-device-scale-factor=1',
    '--force-color-profile=srgb',
    '--disable-lcd-text',
    '--font-render-hinting=none',
    '--remote-debugging-port=0',
    `--user-data-dir=${profile}`,
    'about:blank',
  ], { stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true });
  let stderr = '';
  child.stderr.on('data', chunk => { stderr = (stderr + chunk.toString()).slice(-4000); });
  try {
    const portText = await waitForFile(join(profile, 'DevToolsActivePort'));
    const port = portText.split(/\r?\n/)[0].trim();
    const version = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
    const ws = new WebSocket(version.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      ws.onopen = resolve;
      ws.onerror = () => reject(new Error('DevTools WebSocket failed'));
    });
    const send = rpcClient(ws);
    const target = await send('Target.createTarget', { url: 'about:blank' });
    const attached = await send('Target.attachToTarget', { targetId: target.targetId, flatten: true });
    const sessionId = attached.sessionId;
    await send('Page.enable', {}, sessionId);
    await send('Runtime.enable', {}, sessionId);
    const total = job.pages.length;
    for (let i = 0; i < total; i++) {
      const page = job.pages[i];
      const [vw, vh] = page.viewport;
      const pageDir = join(root, 'pages', page.id);
      const captureDir = join(pageDir, 'capture');
      mkdirSync(captureDir, { recursive: true });
      await send('Emulation.setDeviceMetricsOverride', {
        width: vw,
        height: vh,
        deviceScaleFactor: 1,
        mobile: false,
      }, sessionId);
      const url = pathToFileURL(join(root, page.html)).href;
      await send('Page.navigate', { url }, sessionId);
      await waitForReady(send, sessionId);
      await evaluate(send, sessionId, 'window.scrollTo(0, 0); true');
      await sleep(80);
      const info = await evaluate(send, sessionId, `(() => {
        const scroll = document.scrollingElement || document.documentElement;
        const rects = [...document.querySelectorAll('[data-chrome]')].map(el => {
          const r = el.getBoundingClientRect();
          return { kind: el.getAttribute('data-chrome'), x: r.x, y: r.y, width: r.width, height: r.height };
        });
        return {
          innerWidth, innerHeight,
          scrollWidth: scroll.scrollWidth,
          scrollHeight: scroll.scrollHeight,
          title: document.title,
          rects,
        };
      })()`);
      const scrolls = makeScrolls(info.scrollHeight, vh, page.step_fraction, page.jitter, page.index + 1);
      const frames = [];
      for (let k = 0; k < scrolls.length; k++) {
        const y = scrolls[k];
        await evaluate(send, sessionId, `window.scrollTo(0, ${y}); true`);
        await evaluate(send, sessionId, 'new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))', true);
        await sleep(70);
        const actual = await evaluate(send, sessionId, 'Math.round(window.scrollY)');
        const framePath = join(captureDir, `frame_${String(k).padStart(3, '0')}.png`);
        await screenshot(send, sessionId, framePath);
        frames.push({ file: `capture/frame_${String(k).padStart(3, '0')}.png`, requested: y, actual });
      }
      // Ground truth: fixed chrome is hidden (visibility preserves layout), then
      // one full-page capture.  The viewport geometry and scroll positions stay
      // exactly the same as in the frame captures.
      await evaluate(send, sessionId, `(() => {
        document.querySelectorAll('[data-chrome]').forEach(el => el.style.visibility = 'hidden');
        window.scrollTo(0, 0);
        return true;
      })()`);
      await evaluate(send, sessionId, 'new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))', true);
      await sleep(80);
      const fullHeight = Math.max(vh, Math.ceil(info.scrollHeight));
      await screenshot(send, sessionId, join(captureDir, 'truth_full.png'), { width: vw, height: fullHeight });
      await evaluate(send, sessionId, `(() => {
        document.querySelectorAll('[data-chrome]').forEach(el => el.style.visibility = '');
        window.scrollTo(0, 0);
        return true;
      })()`);
      const capture = {
        id: page.id,
        viewport: [vw, vh],
        inner: [info.innerWidth, info.innerHeight],
        scroll_size: [info.scrollWidth, info.scrollHeight],
        rects: info.rects,
        scrolls,
        frames,
        truth_height: fullHeight,
      };
      writeFileSync(join(captureDir, 'capture.json'), JSON.stringify(capture, null, 2));
      process.stderr.write(`[capture] ${i + 1}/${total} ${page.id} frames=${frames.length} h=${info.scrollHeight}\n`);
    }
    ws.close();
  } finally {
    child.kill();
    await sleep(200);
    try { rmSync(profile, { recursive: true, force: true }); } catch { /* Windows may hold a file briefly. */ }
  }
}

main().catch(error => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exit(1);
});
