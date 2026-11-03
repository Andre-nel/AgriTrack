/* Requires the existing Playwright installation through NODE_PATH. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');
const evidence = path.join(root, 'sandbox', 'weir-evidence');
const vendor = process.env.LEAFLET_TEST_DIR || path.join(evidence, 'vendor');
fs.mkdirSync(evidence, {recursive:true});

async function main() {
  const server = spawn(path.join(root,'.venv','Scripts','python.exe'), ['-X','utf8',path.join(__dirname,'weir_server.py')], {cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
  let log = '', browser;
  server.stderr.on('data', data => {log += data;});
  try {
    const info = await new Promise((resolve,reject) => {
      let output = '';
      const timer = setTimeout(() => reject(new Error(log || 'Server timeout')),30000);
      server.on('error',error => {clearTimeout(timer); reject(error);});
      server.on('exit',code => {clearTimeout(timer); reject(new Error('Server exited '+code+': '+log));});
      server.stdout.on('data',data => {
        output += data; const match = output.match(/WEIR_READY (.+)\r?\n/);
        if(match) {clearTimeout(timer); resolve(JSON.parse(match[1]));}
      });
    });
    const host = `http://127.0.0.1:${info.port}`;
    browser = await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});
    const context = await browser.newContext({viewport:{width:1280,height:1000}});
    // Serve cached, pinned Leaflet locally; block every external request,
    // including tiles. See the water module README for cache setup.
    for (const file of ['leaflet.js', 'leaflet.css', 'chart.umd.min.js']) assert.ok(fs.existsSync(path.join(vendor,file)), 'Missing browser test cache: '+file);
    await context.route('**/*', route => {
      const url = route.request().url();
      if (url.startsWith(host)) return route.continue();
      if (url === 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js') return route.fulfill({path:path.join(vendor,'leaflet.js'),contentType:'application/javascript'});
      if (url === 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css') return route.fulfill({path:path.join(vendor,'leaflet.css'),contentType:'text/css'});
      if (url === 'https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js') return route.fulfill({path:path.join(vendor,'chart.umd.min.js'),contentType:'application/javascript'});
      return route.abort();
    });
    const page = await context.newPage(), errors=[];
    page.on('pageerror',error => errors.push(error.message));
    page.on('dialog', dialog => dialog.accept());
    const read = async () => (await context.request.get(host+'/api/water-assets/'+info.id)).json();
    const readSuggested = async () => (await context.request.get(host+'/api/water-assets/'+info.suggested_id)).json();
    const waterDepth = page.locator('[name=preview_level]');
    const waitForWater = async level => {
      await page.locator('#weir-water-status').filter({hasText:new RegExp('at '+level+'% depth\\.$')}).waitFor();
    };
    // Sample the actual rendered pixels, including SVG clipping, rather than
    // merely checking that a water path exists in the DOM.
    const mapColors = async samples => {
      const bytes = await page.locator('#weir-geometry-map').screenshot();
      return page.evaluate(async ({bytes,samples}) => {
        const bitmap = await createImageBitmap(new Blob([new Uint8Array(bytes)],{type:'image/png'}));
        const canvas = document.createElement('canvas');
        canvas.width=bitmap.width; canvas.height=bitmap.height;
        const context=canvas.getContext('2d'); context.drawImage(bitmap,0,0);
        const svg=document.getElementById('weir-geometry-map'), rect=svg.getBoundingClientRect();
        const bounds=svg.querySelector('path').getBBox();
        return samples.map(([x,y]) => {
          const p=new DOMPoint(bounds.x+bounds.width*x,bounds.y+bounds.height*y).matrixTransform(svg.getScreenCTM());
          return Array.from(context.getImageData(Math.round(p.x-rect.x),Math.round(p.y-rect.y),1,1).data).slice(0,3);
        });
      },{bytes:Array.from(bytes),samples});
    };
    await page.goto(host+info.suggested_path);
    assert.match(await page.locator('#weir-drawing-status').textContent(), /Suggested centreline, not yet saved/);
    assert.equal(await page.locator('#weir-geometry-map [data-point]').count(), 2);
    await waitForWater(50);
    assert.notEqual(await page.locator('#weir-water-fill').getAttribute('d'),'');
    assert.equal((await readSuggested()).weir_centerline,null);
    const suggestedPoints = await page.locator('#weir-geometry-map polyline').getAttribute('points');
    await page.getByRole('button',{name:'Preview estimate',exact:true}).click();
    await page.locator('#weir-result').filter({hasText:'Capacity 500'}).waitFor();
    assert.equal((await readSuggested()).weir_centerline,null);
    await page.getByRole('button',{name:'Save geometry',exact:true}).click();
    await page.locator('#weir-result').filter({hasText:'Geometry saved.'}).waitFor();
    assert.equal((await readSuggested()).weir_centerline.coordinates.length,2);
    await page.reload();
    assert.match(await page.locator('#weir-drawing-status').textContent(), /^Line ready/);
    assert.equal(await page.locator('#weir-geometry-map polyline').getAttribute('points'),suggestedPoints);
    const previewUrl=host+'/api/water-assets/'+info.suggested_id+'/volume-preview';
    let releaseOld, oldStarted, oldDone;
    const oldResponse=new Promise(resolve => {releaseOld=resolve;});
    const oldRequest=new Promise(resolve => {oldStarted=resolve;});
    const oldFinished=new Promise(resolve => {oldDone=resolve;});
    await page.route(previewUrl, async route => {
      if (Number(route.request().postDataJSON().water_level_percent)!==25) return route.continue();
      const response=await route.fetch(); oldStarted();
      await oldResponse;
      try { await route.fulfill({response}); } finally { oldDone(); }
    });
    await waterDepth.fill('25');
    await oldRequest;
    await waterDepth.fill('75');
    await waitForWater(75);
    releaseOld(); await oldFinished;
    await page.unroute(previewUrl);
    assert.match(await page.locator('#weir-water-status').textContent(), /at 75% depth/);
    assert.equal((await readSuggested()).water_level_percent,50);
    await waterDepth.fill('101');
    assert.equal(await page.locator('#weir-water-fill').getAttribute('d'),'');
    assert.match(await page.locator('#weir-water-status').textContent(), /valid wall depth and water depth/);
    await page.route(previewUrl, route => route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Preview unavailable for test'})}));
    await waterDepth.fill('42');
    await page.locator('#weir-water-status').filter({hasText:'Preview unavailable for test'}).waitFor();
    assert.equal(await page.locator('#weir-water-fill').getAttribute('d'),'');
    await page.unroute(previewUrl);
    await page.getByRole('button',{name:'Start new line',exact:true}).click();
    assert.equal(await page.locator('#weir-geometry-map [data-point]').count(),0);
    assert.match(await page.locator('#weir-drawing-status').textContent(), /wall boundary to start/);
    await page.goto(host+info.island_path);
    assert.equal(await page.locator('#weir-geometry-map [data-point]').count(),1);
    assert.match(await page.locator('#weir-drawing-status').textContent(), /manual tracing/);
    assert.equal(await page.getByRole('button',{name:'Wall point',exact:true}).count(),1);
    await page.getByRole('button',{name:'Preview estimate',exact:true}).click();
    assert.match(await page.locator('#weir-result').textContent(), /Trace at least a wall point and an upstream endpoint/);
    await page.goto(host+info.curved_path);
    assert.match(await page.locator('#weir-drawing-status').textContent(), /Suggested centreline/);
    await page.getByRole('button',{name:'Preview estimate',exact:true}).click();
    await page.locator('#weir-result').filter({hasText:'Preview: Capacity'}).waitFor();
    await page.evaluate(() => window.scrollTo(0,0));
    await page.screenshot({path:path.join(evidence,'geometry-default-curved.png'),fullPage:true});
    const originalCurve = await page.locator('#weir-geometry-map polyline').getAttribute('points');
    await page.locator('#weir-geometry-map [data-point="20"]').focus();
    await page.keyboard.press('ArrowRight');
    assert.notEqual(await page.locator('#weir-geometry-map polyline').getAttribute('points'),originalCurve);
    await page.goto(host+info.shaded_island_path);
    const blue=[23,107,155], pale=[200,229,236], background=[237,245,244];
    await waitForWater(50);
    assert.deepEqual(await mapColors([[0.1,0.8],[0.25,0.8],[0.75,0.8]]),[blue,background,pale]);
    await waterDepth.fill('100');
    await waitForWater(100);
    assert.deepEqual(await mapColors([[0.1,0.8],[0.25,0.8],[0.75,0.8]]),[blue,background,blue]);
    await waterDepth.fill('0');
    await waitForWater(0);
    assert.equal(await page.locator('#weir-water-fill').getAttribute('d'),'');
    assert.deepEqual(await mapColors([[0.1,0.8],[0.25,0.8],[0.75,0.8]]),[pale,background,pale]);
    await page.goto(host+info.path);
    assert.match(await page.locator('#weir-drawing-status').textContent(), /No weir point/);
    const clicks = await page.locator('#weir-geometry-map').evaluate(svg => {
      const box = svg.querySelector('path').getBBox();
      return [0,0.5,1].map(fraction => {
        const p = new DOMPoint(box.x + box.width*fraction,box.y + box.height/2).matrixTransform(svg.getScreenCTM());
        return {x:p.x,y:p.y};
      });
    });
    for (const p of clicks) await page.mouse.click(p.x,p.y);
    await page.getByRole('button',{name:'Finish line',exact:true}).click();
    await page.getByRole('button',{name:'Preview estimate',exact:true}).click();
    await page.locator('#weir-result').filter({hasText:'Capacity 500'}).waitFor();
    assert.equal((await read()).weir_centerline,null);
    await page.locator('[name=height]').fill('2');
    await page.getByRole('button',{name:'Save geometry',exact:true}).click();
    await page.locator('#weir-result').filter({hasText:'Geometry saved.'}).waitFor();
    const saved = await read();
    assert.equal(saved.weir_wall_height_m,2);
    assert.equal(saved.water_level_percent,50);
    assert.equal(saved.capacity_m3,1000);
    assert.equal(saved.estimated_volume_m3,250);
    await page.evaluate(() => window.scrollTo(0,0));
    await page.screenshot({path:path.join(evidence,'geometry-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(() => window.scrollTo(0,0));
    await page.screenshot({path:path.join(evidence,'geometry-mobile.png'),fullPage:true});
    await page.setViewportSize({width:1280,height:1000});
    await page.goto(host+`/farms/${info.farm}`);
    await page.locator('#farm-map path.leaflet-interactive').first().click();
    const popup = page.locator('.leaflet-popup-content');
    await popup.getByText('Estimated volume (m³)', {exact:true}).waitFor();
    assert.match(await popup.textContent(), /50%/);
    assert.match(await popup.textContent(), /1000\.00/);
    assert.match(await popup.textContent(), /250/);
    assert.equal(await popup.getByRole('link',{name:'Configure weir geometry'}).getAttribute('href'), info.path);
    const detailPath=`/farms/${info.farm}/water/assets/${info.id}`;
    assert.equal(await popup.getByRole('link',{name:'Open asset',exact:true}).getAttribute('href'), detailPath);
    await page.locator('#farm-map').screenshot({path:path.join(evidence,'map-popup.png')});
    await page.goto(host+`/farms/${info.farm}/water`);
    await page.locator('#farm-map path.leaflet-interactive').first().click();
    await page.waitForURL(host+detailPath);
    await page.locator('[data-water-asset-modal-form]').waitFor({state:'visible'});
    assert.equal(await page.getByRole('link',{name:'Configure weir geometry'}).getAttribute('href'),info.path);
    await page.getByRole('link',{name:'Back to Water Network',exact:true}).click();
    await page.locator(`#asset-${info.id} a.water-asset-button`).click();
    await page.waitForURL(host+detailPath);
    const modal=page.locator('#water-asset-modal');
    await modal.locator('[data-water-asset-modal-form]').waitFor({state:'visible'});
    assert.equal(await modal.evaluate(element => element.tagName),'SECTION');
    await page.evaluate(() => window.scrollTo(0,0));
    await page.screenshot({path:path.join(evidence,'asset-detail-desktop.png'),fullPage:true});
    await modal.locator('[name=water_level_percent]').fill('42.75');
    await modal.locator('[name=is_overflowing]').selectOption('1');
    assert.equal(await modal.locator('[name=water_level_percent]').inputValue(),'100');
    assert.equal(await modal.locator('[name=capacity_m3]').inputValue(),'1000');
    assert.equal(await modal.locator('[name=capacity_m3]').evaluate(input => input.readOnly),true);
    await modal.locator('form[data-water-asset-modal-form] button[type=submit]').click();
    await page.waitForURL(host+detailPath);
    assert.equal((await read()).is_overflowing,true);
    await page.getByRole('link',{name:'Asset analytics',exact:true}).click();
    await page.waitForFunction(() => window.Chart && Object.keys(Chart.instances).length === 3);
    const charts=await page.evaluate(() => {
      const panels=JSON.parse(document.getElementById('waterAssetAnalyticsData').textContent).panels;
      return panels.map((panel,index) => ({field:panel.field,chart:!!Chart.getChart(document.getElementById('waterAssetChart'+index))}));
    });
    assert.deepEqual(charts.map(row => row.field),['water_level','capacity_m3','estimated_volume_m3']);
    assert.ok(charts.every(row => row.chart));
    const depthSection=page.locator('details.grazing-chart-card').filter({has:page.locator('summary',{hasText:'Weir Depth (%) Over Time'})});
    await depthSection.locator('summary').click();
    assert.equal(await depthSection.evaluate(element => element.open),false);
    await depthSection.locator('summary').click();
    await page.waitForFunction(() => Chart.getChart(document.getElementById('waterAssetChart0')).width > 100);
    const trendSection=page.locator('details.analytics-section').filter({has:page.locator(':scope > summary > h2',{hasText:'Trend Charts'})});
    await trendSection.locator(':scope > summary').click();
    await trendSection.locator(':scope > summary').click();
    await page.waitForFunction(() => Object.values(Chart.instances).every(chart => chart.width > 100));
    await page.evaluate(() => window.scrollTo(0,0));
    await page.screenshot({path:path.join(evidence,'asset-analytics-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(() => document.documentElement.scrollWidth <= window.innerWidth);
    await page.screenshot({path:path.join(evidence,'asset-analytics-mobile.png'),fullPage:true});
    await page.setViewportSize({width:1280,height:1000});
    await page.goto(host+`/farms/${info.farm}/water/mass-update?filters_applied=1&asset_type=weir`);
    assert.equal(await page.locator(`[name="capacity_m3__${info.id}"]`).evaluate(input => input.readOnly),true);
    await page.locator(`[name="is_overflowing__${info.id}"]`).selectOption('0');
    await page.locator(`[name="water_level_percent__${info.id}"]`).fill('37.25');
    await page.getByRole('button',{name:'Save Mass Update',exact:true}).click();
    await page.waitForLoadState('networkidle');
    assert.equal((await read()).water_level_percent,37.25);
    assert.equal((await read()).is_overflowing,false);
    assert.deepEqual(errors,[]);
    console.log('Weir browser acceptance passed; screenshots: '+evidence);
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
}
main().catch(error => {console.error(error);process.exitCode=1;});
