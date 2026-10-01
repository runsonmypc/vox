// node capture-demo.mjs <puppeteer-core module path> <native frame directory> <output directory>
import assert from 'node:assert/strict';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
const [modulePath, nativeDir, out] = process.argv.slice(2);
const { default: puppeteer } = await import(pathToFileURL(path.resolve(modulePath)));
await mkdir(out, { recursive: true });
const browser = await puppeteer.launch({ executablePath: process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 768, height: 550, deviceScaleFactor: 2 });
  await page.goto(new URL('./demo.html', import.meta.url).href);
  await page.evaluate(() => document.fonts.ready);
  const scenes = await page.evaluate(() => window.scenes);
  const native = {};
  for (const phase of ['listening', 'processing']) {
    native[phase] = await Promise.all(Array.from({ length: 80 }, async (_, i) =>
      'data:image/png;base64,' + (await readFile(path.join(nativeDir, `${phase}-${String(i).padStart(3, '0')}.png`))).toString('base64')));
  }
  const frames = [];
  for (const [index, scene] of scenes.entries()) {
    // Animate the microphone/bead at 20 fps, then hold the pasted result without duplicate frames.
    const times = Array.from({ length: scene.pasteAt / 50 + 1 }, (_, i) => i * 50);
    for (const t of times) {
      const phase = t < scene.recordUntil ? 'listening' : 'processing';
      const n = Math.floor((phase === 'listening' ? t : t - scene.recordUntil) / 50) % 80;
      const state = await page.evaluate((i, t, url) => window.renderFrame(i, t, url), index, t, native[phase][n]);
      assert.equal(state.output, t >= scene.pasteAt ? scene.output : '');
      assert.equal(state.overlay, t >= 250 && t < scene.pasteAt - 50);
      const bounds = await page.evaluate(() => {
        const output = document.querySelector('.surface:not([hidden]) .output').getBoundingClientRect();
        const surface = document.querySelector('.surface:not([hidden])').getBoundingClientRect();
        const tools = document.querySelector('.mail-tools').getBoundingClientRect();
        return { fits: output.right <= surface.right && output.bottom <= (document.body.dataset.scene === 'context' ? tools.top - 8 : surface.bottom), overflow: document.documentElement.scrollWidth > innerWidth };
      });
      assert.ok(bounds.fits && !bounds.overflow, `${scene.id}: text fits`);
      if (scene.id === 'context') {
        const hints = await page.evaluate(() => ({
          phase: document.body.dataset.capture,
          visible: getComputedStyle(document.querySelector('.hint-words')).opacity === '1',
          reading: Number(getComputedStyle(document.querySelector('.thread'), '::after').opacity) > 0,
          source: [...document.querySelectorAll('.thread mark')].map(el => el.textContent),
          words: [...document.querySelectorAll('.hint-words span')].map(el => el.textContent),
          result: [...document.querySelectorAll('.mail-input mark')].map(el => el.textContent),
        }));
        assert.equal(hints.phase, t < 500 ? 'waiting' : t < 1600 ? 'reading' : 'hints');
        assert.equal(hints.reading, t >= 500 && t < 1600);
        assert.equal(hints.visible, t >= 1600);
        assert.deepEqual(hints.source, scene.emphasis);
        assert.deepEqual(hints.words, scene.emphasis);
        assert.deepEqual(hints.result, t >= scene.pasteAt ? scene.emphasis : []);
        if (t === 1000 || t === 2200) await page.screenshot({ path: path.join(out, `context-${hints.phase}.png`), omitBackground: true });
      }
      const file = `frame-${String(frames.length).padStart(4, '0')}.png`;
      await page.screenshot({ path: path.join(out, file), omitBackground: true });
      frames.push({ file, duration: t === scene.pasteAt ? scene.duration - t : 50, scene: scene.id, time: t });
      if (t === scene.pasteAt) await page.screenshot({ path: path.join(out, `${scene.id}-delivered.png`), omitBackground: true });
      if (t === 1000 || t === scene.recordUntil + 400) await page.screenshot({ path: path.join(out, `${scene.id}-${state.phase}.png`), omitBackground: true });
    }
    console.log(`${scene.id}: native overlay, batch paste, text bounds passed`);
  }
  await writeFile(path.join(out, 'frames.json'), JSON.stringify(frames, null, 2));
} finally { await browser.close(); }
