/* Requires Playwright through NODE_PATH and a cached Chart.js 4.4.1 bundle.
 * See app/modules/finance/README.md. Uses only an in-memory test database.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');
const evidence = path.join(root, 'sandbox', 'livestock-trade-evidence');
const chartBundle = process.env.CHART_JS_TEST_PATH || path.join(evidence, 'vendor', 'chart.umd.min.js');
const python = process.env.TRADE_PYTHON || path.join(root, '.venv', 'Scripts', 'python.exe');
const chartUrl = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js';

async function main() {
  assert.ok(fs.existsSync(chartBundle), 'Cache Chart.js 4.4.1 or set CHART_JS_TEST_PATH');
  fs.mkdirSync(evidence, {recursive: true});
  const server = spawn(python, ['-X', 'utf8', path.join(__dirname, 'livestock_trade_server.py')], {
    cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  });
  let log = '', browser;
  server.stderr.on('data', data => { log += data; });
  try {
    const info = await new Promise((resolve, reject) => {
      let output = '';
      const timer = setTimeout(() => reject(new Error('Server timeout: ' + log)), 30000);
      server.once('error', error => { clearTimeout(timer); reject(error); });
      server.once('exit', code => { clearTimeout(timer); reject(new Error('Server exited ' + code + ': ' + log)); });
      server.stdout.on('data', data => {
        output += data;
        const match = output.match(/TRADE_READY (.+)\r?\n/);
        if (match) { clearTimeout(timer); resolve(JSON.parse(match[1])); }
      });
    });
    const host = `http://127.0.0.1:${info.port}`;
    const chrome = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
    browser = await chromium.launch({headless: true, ...(fs.existsSync(chrome) ? {executablePath: chrome} : {})});
    const context = await browser.newContext({viewport: {width: 1280, height: 1000}});
    let blockChart = false;
    await context.route('**/*', route => {
      const url = route.request().url();
      if (url.startsWith(host)) return route.continue();
      if (url === chartUrl && !blockChart) return route.fulfill({path: chartBundle, contentType: 'application/javascript'});
      return route.abort();
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const section = page.locator('[data-trade-weight-chart]');
    const controls = section.locator('input[type=checkbox]');
    const summaries = section.locator('.trade-weight-group-stats');
    const originalFirstSummary = ['270.375 kg', 'R10 815,00', 'R40,0000'];
    const updatedFirstSummary = ['263.117 kg', 'R11 182,48', 'R42,5000'];
    async function waitForChart() {
      await page.waitForFunction(() => window.Chart && Chart.getChart('tradeWeightChart')?.width > 100);
    }
    async function datasets() {
      return page.evaluate(() => Chart.getChart('tradeWeightChart').data.datasets.map(set => ({label: set.label, data: set.data})));
    }
    async function pointPosition(datasetIndex, index) {
      await page.locator('#tradeWeightChart').scrollIntoViewIfNeeded();
      return page.evaluate(({datasetIndex, index}) => {
        const chart = Chart.getChart('tradeWeightChart');
        const point = chart.getDatasetMeta(datasetIndex).data[index];
        const rect = chart.canvas.getBoundingClientRect();
        return {x: rect.left + point.x, y: rect.top + point.y};
      }, {datasetIndex, index});
    }
    await page.goto(host + info.path);
    await waitForChart();
    const original = await datasets();
    assert.deepEqual(original.map(set => set.data.length), [3, 5, 210]);
    assert.deepEqual(original[0].data, [{x: 1, y: 250.125}, {x: 2, y: 250.125}, {x: 3, y: 310.875}]);
    assert.equal(original[2].label, 'Group 3: Heifers');
    assert.equal(original[2].data[209].x, 210);
    assert.equal(await controls.count(), 3);
    assert.deepEqual(await summaries.first().locator('dd').allTextContents(), originalFirstSummary);
    assert.deepEqual(await summaries.nth(1).locator('dd').allTextContents(), ['288.025 kg', 'R12 961,13', 'R45,0000']);
    await controls.nth(2).focus();
    await page.keyboard.press('Space');
    assert.equal(await page.evaluate(() => Chart.getChart('tradeWeightChart').isDatasetVisible(2)), false);
    assert.ok(await page.evaluate(() => Chart.getChart('tradeWeightChart').scales.x.max < 10));
    await page.keyboard.press('Space');
    assert.equal(await page.evaluate(() => Chart.getChart('tradeWeightChart').isDatasetVisible(2)), true);
    // Nearest-point interaction must include both groups when points overlap exactly.
    const point = await pointPosition(0, 0);
    await page.mouse.move(point.x, point.y);
    await page.waitForFunction(() => Chart.getChart('tradeWeightChart').tooltip.dataPoints?.length === 2);
    const tooltip = await page.evaluate(() => Chart.getChart('tradeWeightChart').tooltip.body.map(item => item.lines).flat());
    assert.ok(tooltip.some(line => line.includes('Group 1: Heifers')));
    assert.ok(tooltip.some(line => line.includes('Animal 1: 250.125 kg')));
    assert.ok(tooltip.some(line => line.includes('Group 2: Bull calves')));
    await page.mouse.move(0, 0);
    await section.screenshot({path: path.join(evidence, 'detail-desktop.png'), style: '.topbar { visibility: hidden; }'});

    // Preview, filter without invalidating confirmation, edit, re-preview, save.
    await page.goto(host + '/cash-flow/livestock-trades/import');
    await page.locator('[name=trade_json]').fill(JSON.stringify(info.document));
    await page.getByRole('button', {name: 'Preview import', exact: true}).click();
    await waitForChart();
    assert.deepEqual(await datasets(), original);
    assert.deepEqual(await summaries.first().locator('dd').allTextContents(), originalFirstSummary);
    await page.locator('[name=farm_ids]').selectOption(info.farm_id);
    await page.getByRole('button', {name: 'Preview changes', exact: true}).click();
    await waitForChart();
    assert.equal(await page.locator('[data-confirm-import]').isEnabled(), true);
    await controls.first().uncheck();
    assert.equal(await page.locator('[data-confirm-import]').isEnabled(), true);
    assert.equal(await summaries.first().isVisible(), true);
    assert.deepEqual(await summaries.first().locator('dd').allTextContents(), originalFirstSummary);
    await controls.first().check();
    await page.locator('[name=group_0_weights_kg]').fill('271.234\n255');
    await page.locator('[name=group_0_price_per_kg]').fill('42.5');
    assert.equal(await page.locator('[data-confirm-import]').isEnabled(), false);
    assert.match(await page.locator('[data-weight-preview-status]').textContent(), /Details changed/);
    assert.deepEqual(await datasets(), original);
    assert.deepEqual(await summaries.first().locator('dd').allTextContents(), originalFirstSummary);
    await page.getByRole('button', {name: 'Preview changes', exact: true}).click();
    await waitForChart();
    const updated = await datasets();
    assert.deepEqual(updated[0].data, [{x: 1, y: 271.234}, {x: 2, y: 255}]);
    assert.deepEqual(await summaries.first().locator('dd').allTextContents(), updatedFirstSummary);
    assert.match(await page.locator('[data-weight-preview-status]').textContent(), /Showing weights/);
    await section.screenshot({path: path.join(evidence, 'preview-desktop.png'), style: '.topbar { visibility: hidden; }'});
    await page.locator('[data-confirm-import]').click();
    await waitForChart();
    assert.deepEqual(await datasets(), updated);
    assert.deepEqual(await summaries.first().locator('dd').allTextContents(), updatedFirstSummary);
    const savedUrl = page.url();

    // Touch input and narrow layout on a saved trade and its preview.
    const mobile = await browser.newContext({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
    await mobile.route('**/*', route => {
      if (route.request().url().startsWith(host)) return route.continue();
      if (route.request().url() === chartUrl) return route.fulfill({path: chartBundle, contentType: 'application/javascript'});
      return route.abort();
    });
    const phone = await mobile.newPage();
    phone.on('pageerror', error => errors.push(error.message));
    await phone.goto(savedUrl);
    await phone.waitForFunction(() => window.Chart && Chart.getChart('tradeWeightChart')?.width > 100);
    await phone.locator('#tradeWeightChart').scrollIntoViewIfNeeded();
    const touchPoint = await phone.evaluate(() => {
      const chart = Chart.getChart('tradeWeightChart');
      const point = chart.getDatasetMeta(0).data[0];
      const rect = chart.canvas.getBoundingClientRect();
      return {x: rect.left + point.x, y: rect.top + point.y};
    });
    await phone.touchscreen.tap(touchPoint.x, touchPoint.y);
    await phone.waitForFunction(() => Chart.getChart('tradeWeightChart').tooltip.dataPoints?.some(point => point.parsed.y === 271.234));
    assert.ok(await phone.evaluate(() => document.querySelector('[data-trade-weight-chart]').getBoundingClientRect().right <= innerWidth));
    assert.ok(await phone.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.deepEqual(await phone.locator('.trade-weight-group-stats').first().locator('dd').allTextContents(), updatedFirstSummary);
    await phone.locator('[data-trade-weight-chart]').screenshot({path: path.join(evidence, 'detail-mobile.png'), style: '.topbar { visibility: hidden; }'});
    await phone.goto(host + '/cash-flow/livestock-trades/import');
    // A single point must still have usable axes and remain visible.
    const single = {...info.document, groups: [{...info.document.groups[0], weights_kg: [250.125]}]};
    await phone.locator('[name=trade_json]').fill(JSON.stringify(single));
    await phone.getByRole('button', {name: 'Preview import', exact: true}).click();
    await phone.waitForFunction(() => window.Chart && Chart.getChart('tradeWeightChart')?.width > 100);
    assert.equal(await phone.evaluate(() => Chart.getChart('tradeWeightChart').data.datasets[0].data.length), 1);
    assert.ok(await phone.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await phone.locator('[data-trade-weight-chart]').screenshot({path: path.join(evidence, 'preview-mobile-single.png'), style: '.topbar { visibility: hidden; }'});
    await mobile.close();

    // Loss of the CDN must leave weights and form interactions available.
    blockChart = true;
    await page.reload();
    assert.equal(await section.locator('[data-weight-chart-fallback]').isVisible(), true);
    assert.equal(await section.locator('.trade-weight-chart-wrap').isVisible(), false);
    assert.equal(await summaries.first().isVisible(), true);
    assert.deepEqual(await summaries.first().locator('dd').allTextContents(), updatedFirstSummary);
    await page.getByText('Individual Weights (2)', {exact: true}).click();
    assert.deepEqual(await page.locator('.livestock-weight-list').first().locator('span').allTextContents(), ['271.234 kg', '255.000 kg']);
    assert.deepEqual(errors, []);
    console.log('Passed: ordered weights, duplicates, 210 animals, group means and VAT-exclusive prices, overlapping tooltips, keyboard filters, preview/save/edit, mobile touch, single point, CDN fallback.');
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
