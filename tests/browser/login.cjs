/* Run with node tests/browser/login.cjs after installing Playwright for tests.
 * BRAIN_PLAYWRIGHT_MODULE and BRAIN_CHROMIUM_PATH can point to an existing install.
 * All HTTP is intercepted. These tests use synthetic credentials and no vault.
 */
const { chromium } = require(process.env.BRAIN_PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const token = 'test-access-token';
const overview = {counts:{attention:0,pending:0,projects:0,commitments:0},items:[],coverage:{state:'ready',sources:0,skipped:[],latest_source_date:null}};

(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.BRAIN_CHROMIUM_PATH ? {executablePath:process.env.BRAIN_CHROMIUM_PATH} : {})});
  let passed = 0;
  async function fixture(options={}) {
    const context = await browser.newContext();
    if(options.storageBlocked) await context.addInitScript(() => Object.defineProperty(window, 'sessionStorage', {get(){throw new DOMException('Storage blocked','SecurityError');}}));
    if(options.savedToken) await context.addInitScript(value => {if(!sessionStorage.getItem('test_seeded')){sessionStorage.setItem('brain_token',value);sessionStorage.setItem('test_seeded','1');}},options.savedToken);
    const page = await context.newPage();
    const errors=[], requests=[];
    page.on('pageerror',error=>errors.push(error.message));
    let release;
    const held = new Promise(resolve=>release=resolve);
    await page.route('https://brain.test/**', async route => {
      const url = new URL(route.request().url());
      const assets={'/ui':'index.html','/ui/app.js':'app.js','/ui/style.css':'style.css'};
      if(assets[url.pathname]) return route.fulfill({path:path.join(root,'web',assets[url.pathname])});
      if(url.pathname==='/favicon.ico') return route.fulfill({status:204});
      const authorization=route.request().headers().authorization||'';
      requests.push({path:url.pathname,authorization});
      if(options.holdSaved && authorization==='Bearer expired-token') await held;
      if(authorization!=='Bearer '+token) return route.fulfill({status:401,json:{error:'unauthorized'}});
      if(options.holdLogin && requests.filter(r=>r.authorization==='Bearer '+token).length===1) await held;
      const data=url.pathname.endsWith('/workspace')?overview:url.pathname.endsWith('/records')?{records:[]}:{jobs:[]};
      return route.fulfill({status:200,json:data});
    });
    await page.goto('https://brain.test/ui');
    return {page,context,errors,requests,release};
  }
  async function connect(page,value) {
    await page.locator('#connection[open]').waitFor();
    await page.locator('#token').fill(value);
    await page.locator('#connect-form button[type=submit]').click();
  }
  async function dashboard(page) {
    await page.locator('#content h1').filter({hasText:'Your attention, today'}).waitFor();
    assert.equal(await page.locator('#connection').evaluate(e=>e.open),false);
  }
  async function finish(f) { assert.deepEqual(f.errors,[]);await f.context.close();passed++; }
  try {
    // Merely visiting or reloading the sign-in page must generate no failed
    // authentication requests; a five-strike proxy previously banned the user.
    let f=await fixture();
    await f.page.locator('#connection[open]').waitFor();
    await f.page.reload();
    await f.page.locator('#connection[open]').waitFor();
    assert.equal(f.requests.length,0);
    await connect(f.page,'wrong-token');
    await f.page.waitForFunction(()=>document.querySelector('#connect-error').textContent.includes('rejected'));
    assert.equal(f.requests.length,1);
    assert.equal(await f.page.evaluate(()=>sessionStorage.getItem('brain_token')),null);
    await connect(f.page,'Authorization: Bearer '+token);
    await dashboard(f.page);
    await finish(f);

    f=await fixture({storageBlocked:true});
    await connect(f.page,token);
    await dashboard(f.page);
    await f.page.locator('[data-view=review]').click();
    await f.page.locator('#content h1').filter({hasText:'Review what changed'}).waitFor();
    await finish(f);

    f=await fixture({savedToken:'expired-token'});
    await f.page.locator('#connection[open]').waitFor();
    assert.equal(f.requests.length,1);
    assert.equal(await f.page.evaluate(()=>sessionStorage.getItem('brain_token')),null);
    await f.page.reload();
    await f.page.locator('#connection[open]').waitFor();
    assert.equal(f.requests.length,1);
    await finish(f);

    f=await fixture({savedToken:'expired-token',holdSaved:true});
    await f.page.locator('#account').click();
    await connect(f.page,token);
    await dashboard(f.page);
    f.release();
    await f.page.waitForLoadState('networkidle');
    await dashboard(f.page);
    await finish(f);

    f=await fixture({holdLogin:true});
    await connect(f.page,token);
    assert.equal(await f.page.locator('#connect-form button[type=submit]').isDisabled(),true);
    await f.page.evaluate(()=>document.querySelector('#connect-form').requestSubmit());
    assert.equal(f.requests.length,1);
    f.release();
    await dashboard(f.page);
    await finish(f);
    console.log(`${passed} browser login regressions passed.`);
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
