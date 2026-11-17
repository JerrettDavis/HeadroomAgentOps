const {test, expect} = require('@playwright/test');

test('conflicts and failed CI disappear from the list and stack outline on refresh', async ({page, request}) => {
  const result = await (await request.get('/api/merge-priority')).json();
  result.prs.find(row => row.number === 101).ci = 'failed';
  result.prs.find(row => row.number === 102).mergeable = 'CONFLICTING';
  await page.route('**/api/merge-priority', route => route.fulfill({json: result}));
  await page.goto('/');
  await expect(page.locator('.priority-row')).toHaveCount(2);
  await expect(page.locator('#priority-stacks')).toBeHidden();
  result.prs.find(row => row.number === 103).merge_state = 'DIRTY';
  await expect(page.locator('.priority-row')).toHaveCount(1, {timeout: 12000});
  await expect(page.locator('.priority-row')).toContainText('CI pending');
});

test('only approved PRs appear and a changed approval disappears on refresh', async ({page, request}) => {
  const result = await (await request.get('/api/merge-priority')).json();
  result.prs.find(row => row.number === 102).review_decision = 'REVIEW_REQUIRED';
  result.prs.find(row => row.number === 104).review_decision = 'CHANGES_REQUESTED';
  await page.route('**/api/merge-priority', route => route.fulfill({json: result}));
  await page.goto('/');
  await expect(page.locator('.priority-row')).toHaveCount(2);
  await expect(page.locator('#priority-stacks')).toBeHidden();
  result.prs[0].review_decision = null;
  await expect(page.locator('.priority-row')).toHaveCount(1, {timeout: 12000});
});

test('default view gives linked merge order with visibly nested stacks', async ({page}) => {
  await page.goto('/');
  await expect(page.getByRole('heading', {name: 'Merge priority', exact: true})).toBeVisible();
  const rows = page.locator('.priority-row');
  await expect(rows).toHaveCount(4);
  await expect(rows.nth(0)).toContainText('#101');
  await expect(rows.nth(1)).toHaveAttribute('data-number', '103');
  await expect(rows.nth(2)).toHaveAttribute('data-parent', '101');
  await expect(rows.nth(2)).toContainText('After prerequisites');
  await expect(rows.nth(2).getByRole('link', {name: '#101', exact: true})).toHaveAttribute('href', 'https://github.com/example/repository/pull/101');
  await expect(rows.nth(0).getByRole('link', {name: /fix: repair cache/})).toHaveAttribute('href', 'https://github.com/example/repository/pull/101');
  const parent = await rows.nth(0).boundingBox(), child = await rows.nth(2).boundingBox();
  expect(child.x).toBeGreaterThan(parent.x);
  await expect(page.locator('#priority-stacks li li')).toContainText('#102');
  await expect(rows.nth(3)).toContainText('CI pending');
  await page.getByRole('button', {name: 'Reviews', exact: true}).click();
  await expect(page.locator('.workspace')).toBeVisible();
  await page.reload();
  await expect(page.locator('.workspace')).toBeVisible();
});

test('refresh preserves the heading position and expiry clears readiness while paused', async ({page, request}) => {
  const result = await (await request.get('/api/merge-priority')).json();
  result.observed_at = Date.now() / 1000;
  result.freshness_seconds = 2;
  await page.route('**/api/merge-priority', route => route.fulfill({json: result}));
  await page.goto('/');
  await expect(page.locator('.priority-row[data-ready=true]')).toHaveCount(2);
  await page.getByRole('button', {name: 'Automatic dashboard refresh', exact: true}).click();
  await expect(page.locator('.priority-row[data-ready=true]')).toHaveCount(0, {timeout: 10000});
  const heading = await page.locator('#priority-title').boundingBox();
  const nav = await page.locator('.dashboard-views').boundingBox();
  expect(heading.y).toBeGreaterThanOrEqual(nav.y + nav.height);
});

test('automatic updates change order and errors invalidate ready badges', async ({page, request}) => {
  let result = await (await request.get('/api/merge-priority')).json();
  await page.route('**/api/merge-priority', route => route.fulfill({json: result}));
  await page.goto('/');
  await expect(page.locator('.priority-row')).toHaveCount(4);
  result.prs = result.prs.filter(r => r.number !== 101 && r.number !== 102);
  await expect(page.locator('.priority-row')).toHaveCount(2, {timeout: 12000});
  result = {...result, fresh: false, error: 'GitHub refresh failed. Check GitHub sign-in.'};
  await expect(page.locator('#priority-status')).toContainText('GitHub refresh failed', {timeout: 12000});
  await expect(page.locator('.priority-state')).not.toContainText(['Ready now']);
  await page.route('**/api/merge-priority', route => route.fulfill({status: 503, json: {error: 'offline'}}));
  await page.getByRole('button', {name: 'Refresh dashboard', exact: true}).click();
  await expect(page.locator('#priority-status')).toContainText('Unable to refresh');
  await expect(page.locator('.priority-row')).toHaveCount(2);
});

test('untrusted titles and dependency labels stay text; narrow layout stays inside viewport', async ({page, request}) => {
  const result = await (await request.get('/api/merge-priority')).json();
  result.prs[0].title = '<img src=x onerror="window.injected=true">';
  result.prs[0].url = 'javascript:window.injected=true';
  result.prs[0].author = '<script>bad()</script>';
  await page.route('**/api/merge-priority', route => route.fulfill({json: result}));
  await page.setViewportSize({width: 390, height: 844});
  await page.goto('/');
  await expect(page.locator('.priority-row').first()).toContainText('<img src=x');
  await expect(page.locator('.priority-row img, .priority-row script')).toHaveCount(0);
  await expect(page.locator('.priority-row a[href^="javascript:"]')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', {name: 'Merge priority', exact: true}).focus();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', {name: 'Reviews', exact: true})).toBeFocused();
});

test('deep stacks and multiple prerequisites remain linked and keyboard reachable', async ({page, request}) => {
  const result = await (await request.get('/api/merge-priority')).json();
  const parent = result.prs[0];
  result.prs = Array.from({length: 9}, (_, index) => ({...parent,
    number: 101 + index, title: `Stack level ${index}`, depth: index,
    url: `https://github.com/example/repository/pull/${101 + index}`,
    merge_order: index + 1, ready: index === 0, ready_after_dependencies: index > 0,
    parent: index ? 100 + index : null,
    dependencies: index ? [{repo: result.repo, number: 100 + index,
      url: `https://github.com/example/repository/pull/${100 + index}`, state: 'open'}] : [],
  }));
  result.prs[8].dependencies.push({repo: result.repo, number: 101,
    url: 'https://github.com/example/repository/pull/101', state: 'open'});
  await page.route('**/api/merge-priority', route => route.fulfill({json: result}));
  await page.setViewportSize({width: 390, height: 844});
  await page.goto('/');
  await expect(page.locator('#priority-stacks li li li')).toHaveCount(7);
  const last = page.locator('.priority-row').last();
  await expect(last.locator('.priority-dependencies a')).toHaveCount(2);
  const lastLink = last.getByRole('link', {name: '#101', exact: true});
  await lastLink.focus();
  await expect(lastLink).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
