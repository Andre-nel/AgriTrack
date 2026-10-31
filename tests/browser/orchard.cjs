/* Run with NODE_PATH pointing at the existing Playwright installation.
 * Starts a real Flask server with an in-memory database; never touches farm data.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');
const python = process.env.ORCHARD_PYTHON || path.join(root, '.venv', 'Scripts', 'python.exe');
const evidence = path.join(root, 'sandbox', 'orchard-evidence');
fs.mkdirSync(evidence, {recursive: true});

async function main() {
  const server = spawn(python, [path.join(__dirname, 'orchard_server.py')], {cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']});
  let serverLog = '';
  server.stderr.on('data', data => { serverLog += data; });
  let browser;
  try {
    const info = await new Promise((resolve, reject) => {
      let output = '';
      const timer = setTimeout(() => reject(new Error('Server did not start: ' + serverLog)), 30000);
      server.once('error', error => { clearTimeout(timer); reject(error); });
      server.once('exit', code => { clearTimeout(timer); reject(new Error('Server exited ' + code + ': ' + serverLog)); });
      server.stdout.on('data', data => {
        output += data;
        const match = output.match(/ORCHARD_READY (.+)\r?\n/);
        if (match) { clearTimeout(timer); resolve(JSON.parse(match[1])); }
      });
    });
    const host = `http://127.0.0.1:${info.port}`;
    const chrome = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
    browser = await chromium.launch({headless: true, ...(fs.existsSync(chrome) ? {executablePath: chrome} : {})});
    const context = await browser.newContext({viewport: {width: 1600, height: 1100}});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let acceptDialogs = true;
    const dialogs = [];
    page.on('dialog', dialog => { dialogs.push(dialog.message()); return acceptDialogs ? dialog.accept() : dialog.dismiss(); });
    const api = host + info.api;
    async function snapshot() { const response = await context.request.get(api); assert.equal(response.status(), 200); return response.json(); }
    async function saved() {
      await page.locator('#orchard-status').filter({hasText: 'Saved. The orchard is up to date.'}).waitFor();
    }
    await page.goto(host + info.path);
    await page.locator('#orchard-status').filter({hasText: 'Select a space'}).waitFor();
    assert.equal(await page.locator('.orchard-marker').count(), 80);
    assert.equal(await page.locator('.orchard-marker.empty').count(), 11);
    await page.screenshot({path: path.join(evidence, 'desktop-initial.png'), fullPage: true});
    await page.locator('[title^="Row 1 · space 1:"] .orchard-space-symbol').click();
    assert.equal(await page.locator('#orchard-panel-title').textContent(), 'Row 1 · space 1');
    await page.locator('[data-open="browse"]').click();
    let data = await snapshot();
    const planted = data.plants.find(p => p.status === 'planted');
    await page.locator('[data-select="space"]').selectOption(planted.space_id);
    await page.locator('[data-form="plant"] [name="name"]').fill('Corrected variety');
    await page.locator('[data-form="plant"] button[type="submit"]').click();
    await saved();
    assert.equal((await snapshot()).plants.find(p => p.id === planted.id).name, 'Corrected variety');

    // Plan, move, cancel, reserve again, then confirm planting.
    const empty = data.spaces.filter(s => !data.plants.some(p => p.space_id === s.id));
    await page.locator('[data-open="browse"]').click();
    await page.locator('[data-select="space"]').selectOption(empty[0].id);
    const available = data.plants.find(p => p.status === 'unassigned');
    await page.locator('[data-form="reserve"] [name="plant_id"]').selectOption(available.id);
    await page.locator('[data-form="reserve"] button[type="submit"]').click(); await saved();
    assert.equal((await snapshot()).summary.planned, 1);
    await page.locator('[data-form="plant"] [name="action"]').selectOption('reserve');
    await page.locator('[data-form="plant"] [name="space_id"]').selectOption(empty[1].id);
    await page.locator('[data-form="plant"] button[type="submit"]').click(); await saved();
    await page.locator('[data-open="stock"]').click();
    await page.locator('[data-select="plant"]').selectOption(available.id);
    await page.locator('[data-form="plant"] [name="action"]').selectOption('cancel');
    await page.locator('[data-form="plant"] button[type="submit"]').click(); await saved();
    assert.equal((await snapshot()).summary.unassigned, 23);
    await page.locator('[data-form="plant"] [name="space_id"]').selectOption(empty[0].id);
    await page.locator('[data-form="plant"] button[type="submit"]').click(); await saved();
    await page.locator('[data-form="plant"] [name="planted_on"]').fill('2026-10-21');
    await page.locator('[data-form="plant"] button[type="submit"]').click(); await saved();
    assert.equal((await snapshot()).summary.planted, 70);

    // New column, default spacing, extension, trimming and layout movement.
    await page.locator('[data-open="row"]').click();
    const rowForm = page.locator('[data-form="row"]');
    await rowForm.locator('[name="name"]').fill('Browser column');
    await rowForm.locator('[name="orientation"]').selectOption('vertical');
    assert.equal(await rowForm.locator('[name="spacing_m"]').inputValue(), '3');
    await rowForm.locator('[name="x_m"]').fill('-4');
    await rowForm.locator('[name="y_m"]').fill('-3');
    await rowForm.locator('[name="count"]').fill('4');
    await rowForm.locator('button[type="submit"]').click(); await saved();
    data = await snapshot();
    const row = data.rows.find(r => r.name === 'Browser column');
    await page.locator('[data-select="row"]').selectOption(row.id);
    await rowForm.locator('[name="resize"]').selectOption('extend:start');
    await rowForm.locator('[name="count"]').fill('2');
    await rowForm.locator('button[type="submit"]').click(); await saved();
    assert.equal((await snapshot()).spaces.filter(s => s.row_id === row.id).length, 6);
    await rowForm.locator('[name="resize"]').selectOption('trim:end');
    await rowForm.locator('[name="count"]').fill('1');
    await rowForm.locator('[name="x_m"]').fill('-8');
    await rowForm.locator('button[type="submit"]').click(); await saved();
    assert.equal((await snapshot()).spaces.filter(s => s.row_id === row.id).length, 5);

    // Labels move independently, with drag, coordinates, reset, discard and failed-save recovery.
    const beforeLabels = await snapshot();
    await page.locator('[data-label]').click();
    const labelForm = page.locator('[data-form="label"]');
    await labelForm.locator('[name="x_m"]').fill('-5');
    await labelForm.locator('[name="y_m"]').fill('12');
    assert.equal(await labelForm.locator('[name="automatic"]').isChecked(), false);
    await labelForm.locator('button[type="submit"]').click(); await saved();
    let movedRow = (await snapshot()).rows.find(r => r.id === row.id);
    assert.equal(movedRow.label_x_m, -5);
    assert.equal(movedRow.label_y_m, 12);
    const labelBox = await page.locator('.orchard-row-label-marker.editing .orchard-row-label').boundingBox();
    assert.ok(labelBox);
    await page.mouse.move(labelBox.x + labelBox.width / 2, labelBox.y + labelBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(labelBox.x + labelBox.width / 2 + 65, labelBox.y + labelBox.height / 2 - 40, {steps: 8});
    await page.mouse.up();
    await page.locator('#orchard-unsaved').waitFor({state: 'visible'});
    await labelForm.locator('button[type="submit"]').click(); await saved();
    movedRow = (await snapshot()).rows.find(r => r.id === row.id);
    assert.ok(movedRow.label_x_m > -5 && movedRow.label_y_m > 12);
    await labelForm.locator('[name="x_m"]').fill('15');
    await page.locator('#orchard-discard').click();
    assert.equal(Number(await labelForm.locator('[name="x_m"]').inputValue()), movedRow.label_x_m);
    await labelForm.locator('[name="x_m"]').fill('18');
    await page.route(api + '/rows/' + row.id, route => route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: 'Label save failed'})}));
    await labelForm.locator('button[type="submit"]').click();
    await page.locator('#orchard-status').filter({hasText: 'Label save failed'}).waitFor();
    assert.equal(await labelForm.locator('[name="x_m"]').inputValue(), '18');
    assert.equal((await snapshot()).rows.find(r => r.id === row.id).label_x_m, movedRow.label_x_m);
    await page.unroute(api + '/rows/' + row.id);
    await labelForm.locator('button[type="submit"]').click(); await saved();
    await page.reload();
    await page.locator('#orchard-status').filter({hasText: 'Select a space'}).waitFor();
    await page.locator('.orchard-row-label').filter({hasText: 'Browser column'}).click();
    assert.equal(await labelForm.locator('[name="x_m"]').inputValue(), '18');
    await page.locator('[data-reset-label]').click();
    assert.equal(await labelForm.locator('[name="automatic"]').isChecked(), true);
    await labelForm.locator('button[type="submit"]').click(); await saved();
    assert.equal((await snapshot()).rows.find(r => r.id === row.id).label_x_m, null);
    await page.locator('[data-pick="label"]').click();
    await page.locator('#orchard-map').click({position: {x: 180, y: 140}});
    assert.equal(await labelForm.locator('[name="automatic"]').isChecked(), false);
    await labelForm.locator('button[type="submit"]').click(); await saved();
    const afterLabels = await snapshot();
    for (const key of ['spaces', 'plants', 'lines', 'emitters', 'summary']) assert.deepEqual(afterLabels[key], beforeLabels[key]);
    await page.screenshot({path: path.join(evidence, 'desktop-label-moved.png'), fullPage: true});

    // Draw with actual map clicks, then add/edit an emitter and reshape its line.
    await page.locator('[data-open="line"]').click();
    await page.locator('[data-form="line"] [name="name"]').fill('Browser route');
    await page.locator('[data-draw="start"]').click();
    await page.locator('#orchard-map').click({position: {x: 120, y: 100}});
    await page.locator('#orchard-map').click({position: {x: 260, y: 100}});
    await page.locator('#orchard-map').click({position: {x: 300, y: 150}});
    await page.locator('[data-draw="finish"]').click();
    await page.locator('[data-form="line"] button[type="submit"]').click(); await saved();
    data = await snapshot();
    const line = data.lines.find(line => line.name === 'Browser route');
    assert.equal(line.vertices.length, 3);
    await page.locator('[data-select="line"]').selectOption(line.id);
    await page.locator('[data-form="line"] [name="vertices"]').fill('0, -3\n12, -3');
    await page.locator('[data-form="line"] button[type="submit"]').click(); await saved();
    await page.locator('[data-new-emitter]').click();
    await page.locator('[data-form="emitter"] [name="x_m"]').fill('5');
    await page.locator('[data-form="emitter"] [name="y_m"]').fill('-1');
    await page.locator('[data-form="emitter"] [name="kind"]').selectOption('sprinkler');
    await page.locator('[data-form="emitter"] button[type="submit"]').click(); await saved();
    assert.equal((await snapshot()).emitters[0].y_m, -3);
    await page.locator('[data-form="line"] [name="vertices"]').fill('0, -6\n12, -6');
    await page.locator('[data-preview]').click();
    assert.ok(await page.locator('.orchard-emitter').count() >= 2);
    await page.locator('[data-form="line"] button[type="submit"]').click(); await saved();
    assert.equal((await snapshot()).emitters[0].y_m, -6);

    // Removal asks first, retains failed requests, deletes only one plant and frees reservations.
    const beforeRemoval = await snapshot();
    await page.locator('[data-open="stock"]').click();
    await page.screenshot({path: path.join(evidence, 'desktop-stock-removal.png'), fullPage: true});
    await page.locator('summary').filter({hasText: 'Add purchased plants'}).click();
    const addRemoval = page.locator('[data-form="stock"]');
    await addRemoval.locator('[name="name"]').fill('Removal test');
    await addRemoval.locator('[name="quantity"]').fill('2');
    await addRemoval.locator('[name="supplier"]').fill('Test supplier');
    await addRemoval.locator('button[type="submit"]').click(); await saved();
    const stockItem = page.locator('.orchard-stock-list li').filter({hasText: 'Removal test'});
    const remove = stockItem.locator('[data-remove-plant]');
    const removedId = await remove.getAttribute('data-remove-plant');
    const beforeDelete = await snapshot();
    acceptDialogs = false;
    await remove.click();
    assert.deepEqual(await snapshot(), beforeDelete);
    assert.match(dialogs.at(-1), /Remove one “Removal test”/);
    assert.match(dialogs.at(-1), /Test supplier/);
    acceptDialogs = true;
    await page.route(api + '/plants/' + removedId, route => route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: 'Removal failed'})}));
    await remove.click();
    await page.locator('#orchard-status').filter({hasText: 'Removal failed'}).waitFor();
    assert.deepEqual(await snapshot(), beforeDelete);
    assert.equal(await remove.isEnabled(), true);
    await page.unroute(api + '/plants/' + removedId);
    await remove.click(); await saved();
    assert.equal((await snapshot()).plants.some(p => p.id === removedId), false);
    assert.match(await stockItem.textContent(), /× 1/);
    await stockItem.locator('[data-plant]').click();
    const plannedId = await page.locator('[data-form="plant"]').getAttribute('data-id');
    const reservedSpace = await page.locator('[data-form="plant"] [name="space_id"]').inputValue();
    await page.locator('[data-form="plant"] button[type="submit"]').click(); await saved();
    await page.locator('[data-remove-plant]').click(); await saved();
    const afterRemoval = await snapshot();
    assert.equal(afterRemoval.plants.some(p => p.id === plannedId || p.space_id === reservedSpace), false);
    assert.deepEqual(afterRemoval.summary, beforeRemoval.summary);
    assert.deepEqual(afterRemoval.spaces, beforeRemoval.spaces);
    await page.locator('[data-open="browse"]').click();
    await page.locator('[data-select="space"]').selectOption(planted.space_id);
    assert.equal(await page.locator('[data-remove-plant]').count(), 0);

    // Failed requests retain forms; a competing save never overwrites data.
    await page.locator('[data-open="stock"]').click();
    await page.locator('summary').filter({hasText: 'Add purchased plants'}).click();
    const stockForm = page.locator('[data-form="stock"]');
    await stockForm.locator('[name="name"]').fill('Retained after failure');
    await stockForm.locator('[name="quantity"]').fill('2');
    await page.route(api + '/plants', route => route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: 'Simulated connection failure'})}));
    await stockForm.locator('button[type="submit"]').click();
    await page.locator('#orchard-status').filter({hasText: 'Simulated connection failure'}).waitFor();
    assert.equal(await stockForm.locator('[name="name"]').inputValue(), 'Retained after failure');
    assert.equal(await page.locator('#orchard-unsaved').isVisible(), true);
    await page.unroute(api + '/plants');
    data = await snapshot();
    const competing = await context.request.patch(api + '/rows/' + row.id, {data: {revision: data.revision, name: 'Another session'}});
    assert.equal(competing.status(), 200);
    await stockForm.locator('button[type="submit"]').click();
    await page.locator('#orchard-status').filter({hasText: 'another session'}).waitFor();
    assert.equal(await stockForm.locator('[name="name"]').inputValue(), 'Retained after failure');
    assert.equal((await snapshot()).plants.filter(p => p.name === 'Retained after failure').length, 0);
    await page.locator('#orchard-refresh').click();
    await page.locator('#orchard-status').filter({hasText: 'Loaded the latest'}).waitFor();
    await page.reload();
    await page.locator('#orchard-status').filter({hasText: 'Select a space'}).waitFor();
    assert.equal((await snapshot()).summary.planted, 70);
    await page.screenshot({path: path.join(evidence, 'desktop-edited.png'), fullPage: true});

    // A touch viewport uses the same saved data and form controls.
    const mobileContext = await browser.newContext({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
    const mobile = await mobileContext.newPage();
    mobile.on('pageerror', error => errors.push(error.message));
    mobile.on('dialog', dialog => dialog.accept());
    await mobile.goto(host + info.path);
    await mobile.locator('#orchard-status').filter({hasText: 'Select a space'}).waitFor();
    assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await mobile.locator('[data-open="stock"]').tap();
    assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await mobile.screenshot({path: path.join(evidence, 'phone-stock-removal.png'), fullPage: true});
    await mobile.locator('.orchard-stock-list button').first().tap();
    await mobile.locator('[data-form="plant"] button[type="submit"]').tap();
    await mobile.locator('#orchard-status').filter({hasText: 'Saved. The orchard is up to date.'}).waitFor();
    assert.equal((await snapshot()).summary.planned, 1);
    await mobile.locator('[data-remove-plant]').tap();
    await mobile.locator('#orchard-status').filter({hasText: 'Saved. The orchard is up to date.'}).waitFor();
    assert.equal((await snapshot()).summary.planned, 0);
    await mobile.locator('[data-open="browse"]').tap();
    await mobile.locator('[data-select="row"]').selectOption(row.id);
    await mobile.locator('[data-label]').tap();
    await mobile.locator('[data-pick="label"]').tap();
    await mobile.locator('#orchard-map').tap({position: {x: 230, y: 220}});
    const phoneLabel = mobile.locator('[data-form="label"]');
    const phoneX = Number(await phoneLabel.locator('[name="x_m"]').inputValue());
    const phoneY = Number(await phoneLabel.locator('[name="y_m"]').inputValue());
    await phoneLabel.locator('button[type="submit"]').tap();
    await mobile.locator('#orchard-status').filter({hasText: 'Saved. The orchard is up to date.'}).waitFor();
    const phoneSaved = (await snapshot()).rows.find(r => r.id === row.id);
    assert.equal(phoneSaved.label_x_m, phoneX);
    assert.equal(phoneSaved.label_y_m, phoneY);
    assert.deepEqual((await snapshot()).spaces, beforeLabels.spaces);
    await mobile.screenshot({path: path.join(evidence, 'phone-edited.png'), fullPage: true});
    await mobileContext.close();
    assert.deepEqual(errors, []);
    console.log('Orchard browser acceptance passed: persistence, stock removal and protection, label drag/pick/reset, placement, row edits, irrigation, failures, conflicts, reload and phone touch.');
    console.log('Screenshots: ' + evidence);
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
