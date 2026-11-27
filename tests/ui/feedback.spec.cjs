const {test, expect} = require('@playwright/test');

test.beforeEach(async ({page}) => {
  await page.addInitScript(() => localStorage.setItem('agentops-view', 'reviews'));
  await page.goto('/');
  await expect(page.locator('#queue .job').first()).toBeVisible();
});

test('manual refresh exposes busy state, prevents repeats and reports completion quietly', async ({page}) => {
  await page.locator('#auto').evaluate(button => button.setAttribute('aria-pressed', 'false'));
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/report*', async route => { await gate; await route.continue(); });
  await page.locator('#refresh').click();
  await expect(page.locator('#refresh')).toHaveAttribute('aria-busy', 'true');
  await expect(page.locator('#refresh')).toBeDisabled();
  release();
  await expect(page.locator('#refresh')).toBeEnabled();
  await expect(page.locator('#action-notification')).toContainText('Dashboard updated');
  await page.screenshot({path:'../../.agentops/feedback-desktop.png',animations:'disabled'});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'../../.agentops/feedback-mobile.png',animations:'disabled'});
  await page.getByRole('button', {name:'Dismiss notification'}).click();
  await expect(page.locator('#action-notification')).toBeHidden();
});

test('refresh failure keeps saved view and a dismissible error; background polling stays quiet', async ({page}) => {
  await expect(page.locator('#action-notification')).toBeHidden();
  await page.route('**/api/report*', route => route.fulfill({status:503,json:{error:'Temporarily unavailable'}}));
  await page.locator('#refresh').click();
  await expect(page.locator('#action-notification')).toContainText('Temporarily unavailable');
  await expect(page.locator('#queue .job').first()).toBeVisible();
  await expect(page.locator('#refresh')).toBeEnabled();
});

test('manual refresh during polling stays busy and completes after the pending read', async ({page}) => {
  await page.locator('#auto').evaluate(button => button.setAttribute('aria-pressed','true'));
  let release, reads = 0;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/report*', async route => { reads++; await gate; await route.continue(); });
  await expect.poll(() => reads, {timeout:7000}).toBe(1);
  await expect(page.locator('#action-notification')).toBeHidden();
  await page.locator('#refresh').click();
  await expect(page.locator('#refresh')).toHaveAttribute('aria-busy','true');
  release();
  await expect(page.locator('#action-notification')).toContainText('Dashboard updated');
  await expect(page.locator('#refresh')).toBeEnabled();
  expect(reads).toBe(2);
});

test('reduced motion suppresses view and busy animations', async ({page}) => {
  await page.emulateMedia({reducedMotion:'reduce'});
  const animation = await page.locator('#refresh').evaluate(button => {
    button.setAttribute('aria-busy','true');
    return getComputedStyle(button, '::after').animationName;
  });
  expect(animation).toBe('none');
});

test('Studio draft save ignores repeated clicks and errors remain readable as text', async ({page}) => {
  await page.getByRole('button', {name:'Benchmarking',exact:true}).click();
  const studio = page.getByRole('region', {name:'Benchmark Studio',exact:true});
  await studio.getByRole('button', {name:'Use Explore context sizes',exact:true}).click();
  let release, calls = 0;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/benchmarks/studio/draft', async route => {
    calls++; await gate;
    await route.fulfill({status:503,json:{error:'<strong>Save unavailable</strong>'}});
  });
  const save = studio.getByRole('button', {name:'Save draft',exact:true});
  await save.click();
  await expect(save).toHaveAttribute('aria-busy','true');
  await save.click();
  expect(calls).toBe(1);
  release();
  await expect(page.locator('#action-notification')).toContainText('<strong>Save unavailable</strong>');
  await expect(page.locator('#action-notification strong')).toHaveCount(0);
  await expect(save).not.toHaveAttribute('aria-busy','true');
  await page.waitForTimeout(7200);
  await expect(page.locator('#action-notification')).toBeVisible();
});
