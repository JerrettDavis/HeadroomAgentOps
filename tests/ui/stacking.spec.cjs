const {test, expect} = require('@playwright/test');

test('selection prepares a stack, publication requires a separate human click', async ({page}) => {
  const calls = [];
  let result = {repo: 'example/repository', publish_enabled: true, stacks: []};
  await page.route('**/api/stacks', r => r.fulfill({json: result}));
  await page.route('**/api/stacks/*', async r => {
    const action = r.request().url().split('/').pop();
    const body = r.request().postDataJSON();
    calls.push({action, body});
    const record = {id: 'a'.repeat(64), repo: 'example/repository', state: 'prepared',
      selected: body.numbers || [101, 103], included_prerequisites: [], target_branch: 'main',
      target_sha: 'b'.repeat(40), revision: 'd'.repeat(40), fresh: true, verified_at: Date.now()/1000,
      members: [{number: 101, title: 'Repair cache', head_sha: '1'.repeat(40), author: 'alice'}],
      validation: {state: 'unconfigured', commands: []}};
    if (action === 'publish') Object.assign(record, {state: 'published', pr_url: 'https://github.com/example/repository/pull/190'});
    result.stacks = [record];
    await r.fulfill({json: record});
  });
  await page.goto('/');
  const prepare = page.getByRole('button', {name: 'Prepare selected PRs'});
  await expect(prepare).toBeDisabled();
  await page.getByRole('checkbox', {name: 'Include PR #101 in stack'}).check();
  await page.getByRole('checkbox', {name: 'Include PR #103 in stack'}).check();
  await prepare.click();
  await expect(page.locator('#prepared-stacks')).toContainText('prepared');
  expect(calls).toEqual([{action: 'prepare', body: {numbers: [101, 103]}}]);
  await expect(page.locator('#prepared-stacks')).toContainText('unconfigured');
  await page.getByRole('button', {name: 'Publish draft PR'}).click();
  await expect(page.getByRole('link', {name: 'Review and merge on GitHub'})).toHaveAttribute('href', 'https://github.com/example/repository/pull/190');
  expect(calls.map(c => c.action)).toEqual(['prepare', 'publish']);
});

test('conflicts, stale evidence and untrusted logs are safe and visible on mobile', async ({page}) => {
  const record = {id: 'a'.repeat(64), state: 'conflict', repo: 'example/repository',
    target_branch: 'main', target_sha: 'b'.repeat(40), members: [], fresh: false,
    conflicts: ['source.py'], conflicting_pr: 103,
    error: '<img src=x onerror="window.injected=true">',
    validation: {state: 'failed', commands: [{argv: ['python3'], exit_code: 1, log: '<script>bad()</script>'}]}};
  await page.route('**/api/stacks', r => r.fulfill({json: {publish_enabled: true, stacks: [record]}}));
  await page.setViewportSize({width: 390, height: 844});
  await page.goto('/');
  await expect(page.locator('#prepared-stacks')).toContainText('source.py');
  await expect(page.getByRole('button', {name: 'Publish draft PR'})).toHaveCount(0);
  await expect(page.locator('#prepared-stacks img')).toHaveCount(0);
  await expect(page.locator('#prepared-stacks')).toContainText('<img src=x');
  record.state = 'prepared';
  await page.getByRole('button', {name: 'Refresh dashboard', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Publish draft PR'})).toBeDisabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(await page.evaluate(() => window.injected)).toBeUndefined();
});
