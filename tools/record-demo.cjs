// Records a silent walkthrough of docs/index.html to docs/demo.webm and a hero screenshot.
// Usage: python -m http.server 8765 -d docs   (in another shell)  then  node tools/record-demo.cjs
const { chromium } = require("playwright");
const fs = require("fs"), path = require("path");
const URL = process.env.DEMO_URL || "http://localhost:8765/";
const DOCS = path.join(__dirname, "..", "docs");
const pause = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {});
  // Hero screenshot
  const shot = await browser.newPage({ viewport: { width: 1280, height: 760 }, deviceScaleFactor: 1.25, colorScheme: "light" });
  await shot.goto(URL); await shot.waitForTimeout(3500);
  await clickZone(shot, "Low"); await shot.waitForTimeout(800);
  await shot.screenshot({ path: path.join(DOCS, "hero.png") });
  await shot.close();

  // Video
  const ctx = await browser.newContext({ viewport: { width: 1120, height: 630 }, colorScheme: "light",
    recordVideo: { dir: DOCS, size: { width: 960, height: 540 } } });
  const p = await ctx.newPage();
  await p.goto(URL); await pause(5000);                       // overview: whole CBD
  await clickZone(p, "Low"); await pause(5000);                // a hard block -> red chip
  const go = await p.$("#go"); if (go) { await go.click(); await pause(5000); } // follow "better bet"
  await clickZone(p, "Medium"); await pause(4500);
  await p.mouse.move(800, 400); await p.mouse.wheel(0, -300); await pause(3000);
  await clickZone(p, "High"); await pause(4500);
  await p.click("#bLive"); await pause(9000);                 // live API
  await clickZone(p, "Low"); await pause(5000);
  await p.setViewportSize({ width: 390, height: 720 }); await pause(4000); // mobile layout
  const v = p.video(); await ctx.close();
  fs.renameSync(await v.path(), path.join(DOCS, "demo.webm"));
  await browser.close();
})();

// Click a marker of the given score, preferring one near the map centre.
async function clickZone(page, score) {
  const box = await page.evaluate(score => {
    const col = getComputedStyle(document.documentElement).getPropertyValue({High:"--high",Medium:"--med",Low:"--low"}[score]).trim();
    const map = document.getElementById("map").getBoundingClientRect();
    const cx = map.left + map.width / 2, cy = map.top + map.height / 2;
    const els = [...document.querySelectorAll("path.leaflet-interactive")]
      .filter(e => e.getAttribute("fill").toLowerCase() === col.toLowerCase())
      .map(e => e.getBoundingClientRect())
      .filter(r => r.left > map.left + 20 && r.right < map.right - 20 && r.top > map.top + 20 && r.bottom < map.bottom - 20)
      .sort((a, b) => Math.hypot(a.x - cx, a.y - cy) - Math.hypot(b.x - cx, b.y - cy));
    return els[0] ? { x: els[0].x + els[0].width / 2, y: els[0].y + els[0].height / 2 } : null;
  }, score);
  if (box) { await page.mouse.move(box.x, box.y, { steps: 20 }); await pause(500); await page.mouse.click(box.x, box.y); }
}
