// Render WGPlayground catalogue pages in a real browser (the catalogue is
// built by JavaScript, so plain HTML has no game links), scroll until no new
// games appear, and write every /game/<studio>/<slug> link with its visible
// title to wg_links.tsv for find_wg_games.py to filter. JSON API calls the
// page makes are printed too, in case a direct feed is easier next time.
import { writeFileSync } from 'node:fs';
import { chromium } from 'playwright';

const urls = (process.env.URLS || '').split(',').map((u) => u.trim()).filter(Boolean);
const maxScrolls = Number(process.env.MAX_PAGES || 30);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
const apis = new Set();
page.on('response', (r) => {
  if ((r.headers()['content-type'] || '').includes('json')) apis.add(r.url().split('?')[0]);
});

const links = new Map();
for (const url of urls) {
  try {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  } catch (e) {
    console.log(`${url}: ${e.message.split('\n')[0]}`);
  }
  let last = -1;
  for (let i = 0; i < maxScrolls; i++) {
    const found = await page.$$eval('a[href*="/game/"]', (as) =>
      as.map((a) => [a.href, (a.innerText || a.getAttribute('title') || '').trim().replace(/\s+/g, ' ')]));
    for (const [href, title] of found) {
      if (!links.has(href) || (title && title.length > (links.get(href) || '').length)) links.set(href, title);
    }
    if (links.size === last) {
      // Try a "load more" style button before giving up.
      const more = await page.$('button:has-text("more"), a:has-text("Load more"), button:has-text("Show more")');
      if (!more) break;
      await more.click().catch(() => {});
    }
    last = links.size;
    await page.mouse.wheel(0, 4000);
    await page.waitForTimeout(1500);
  }
  console.log(`${url}: ${links.size} game links so far`);
}
await browser.close();

writeFileSync('wg_links.tsv', [...links].map(([u, t]) => `${u}\t${t}`).join('\n'));
console.log('JSON endpoints seen:');
for (const a of apis) console.log(`  ${a}`);
