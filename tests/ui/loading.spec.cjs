const {test, expect} = require('@playwright/test');

test('a slow full ledger finishes instead of replacing valid data with a timeout', async ({page}) => {
  await page.addInitScript(() => localStorage.setItem('agentops-view', 'reviews'));
  let delayed = false;
  await page.route('**/api/report*', async route => {
    if (!delayed) {
      delayed = true;
      await new Promise(resolve => setTimeout(resolve, 16000));
    }
    await route.continue();
  });
  await page.goto('/');
  await expect(page.locator('#queue .loading-placeholder')).toBeVisible();
  await expect(page.locator('#queue .job').first()).toBeVisible({timeout: 25000});
  await expect(page.locator('#connection')).not.toHaveClass('error');
});

test('removing a pending selection clears detail busy state', async ({page, request}) => {
  await page.addInitScript(() => localStorage.setItem('agentops-view', 'reviews'));
  await page.goto('/');
  await expect(page.locator('#detail')).toHaveAttribute('aria-busy', 'false');
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  await page.route('**/api/job?*', async route => { await pending; await route.continue(); });
  await page.locator('#queue .job').nth(1).click();
  await expect(page.locator('#detail')).toHaveAttribute('aria-busy', 'true');
  const report = await (await request.get('/api/report')).json();
  report.jobs = [];
  await page.route('**/api/report*', route => route.fulfill({json: report}));
  await page.locator('#refresh').click();
  await expect(page.locator('#detail')).toContainText('Choose a PR');
  await expect(page.locator('#detail')).toHaveAttribute('aria-busy', 'false');
  release();
});

for (const [view, endpoint, target] of [
  ['priority', '**/api/merge-priority', '#priority-list'],
  ['benchmarks', '**/api/benchmarks', '#benchmark-jobs'],
  ['updates', '**/api/maintenance', '#maintenance-view'],
  ['main-ci', '**/api/maintenance', '#maintenance-view']
]) {
  test(`${view} explains its initial wait and replaces skeletons after loading`, async ({page}) => {
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    await page.route(endpoint, async route => { await pending; await route.continue(); });
    await page.addInitScript(view => localStorage.setItem('agentops-view', view), view);
    await page.goto('/');
    await expect(page.locator(`${target} .loading-placeholder`)).toBeVisible();
    await expect(page.locator(`${target} .loading-caption`)).toContainText('Loading');
    release();
    await expect(page.locator(`${target} .loading-placeholder`)).toHaveCount(0);
  });
}

test('initial review loading has skeletons and recovers after failure', async ({page}) => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  await page.route('**/api/report*', async route => {
    await pending;
    await route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: 'Ledger unavailable'})});
  });
  await page.addInitScript(() => localStorage.setItem('agentops-view', 'reviews'));
  await page.goto('/');
  await expect(page.locator('#queue .loading-caption')).toHaveText('Loading review queue…');
  await expect(page.locator('#agents .skeleton-row')).toHaveCount(2);
  await page.emulateMedia({reducedMotion: 'reduce'});
  await expect(page.locator('#queue .skeleton-line').first()).toHaveCSS('animation-name', 'none');
  release();
  await expect(page.locator('#connection')).toContainText('unable to load review ledger');
  await expect(page.locator('#queue .loading-placeholder')).toHaveCount(0);
  await expect(page.locator('#metrics .skeleton-line')).toHaveCount(0);
  await expect(page.locator('#queue')).toContainText('Use Refresh to retry');
  await page.unroute('**/api/report*');
  await page.locator('#refresh').click();
  await expect(page.locator('#queue .job').first()).toBeVisible();
  await expect(page.locator('#connection')).toContainText('Updated');
});
