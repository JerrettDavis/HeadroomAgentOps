const {test, expect} = require('@playwright/test');

function status() {
  return {settings: {watch: true, autofix: false, reapprove: true}, fresh: true,
    inventory: {prs: [1, 2].map(n => ({number: n, title: `Drifted ${n}`, author: 'contributor',
      behind: n, head_ref: `topic${n}`, head_repo: 'fork/repo'})), fetched: Date.now()/1000},
    updates: [], secondary: [], monitor: {state: 'broken', head_sha: 'a'.repeat(40)},
    incidents: [{key: 'incident', state: 'broken', failures: [{name: 'CI', conclusion: 'failure',
      html_url: 'https://github.com/o/r/actions/runs/1'}]}], alert: true};
}

test('bulk update selection retains partial failures and explicit remediation', async ({page}) => {
  const data = status();
  const calls = [];
  await page.route('**/api/maintenance**', async route => {
    const request = route.request();
    if (request.method() === 'POST') {
      const body = request.postDataJSON(); calls.push({url: request.url(), body});
      if (request.url().endsWith('/update')) {
        data.updates = [{key: 'one', number: 1, state: 'updated'},
                        {key: 'two', number: 2, state: 'needs_remediation', reason: 'Rebase conflict'}];
        data.secondary = [data.updates[1]];
      }
    }
    await route.fulfill({json: data});
  });
  await page.goto('/');
  await page.getByRole('button', {name: 'PR updates', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'PR updates', exact: true})).toBeVisible();
  const first = page.getByRole('checkbox', {name: 'Update PR #1'});
  await expect(first).toBeEnabled();
  await first.focus(); await page.keyboard.press('Space');
  await expect(first).toBeFocused(); await expect(first).toBeChecked();
  await page.getByRole('button', {name: 'Select all outdated PRs'}).click();
  await page.getByRole('button', {name: 'Update selected PRs'}).click();
  await expect.poll(() => calls.find(c => c.url.endsWith('/update'))?.body.numbers).toEqual([1,2]);
  await expect(page.getByText('Rebase conflict', {exact: true}).first()).toBeVisible();
  await page.getByRole('checkbox', {name: 'Remediate PR #2'}).check();
  await page.getByRole('button', {name: 'Send selected to agent'}).click();
  await expect.poll(() => calls.find(c => c.url.endsWith('/remediate'))?.body.keys).toEqual(['two']);
});

test('main CI alert opens monitor and autofix persists without extra alert', async ({page}) => {
  const data = status();
  await page.route('**/api/maintenance**', async route => {
    if (route.request().method() === 'POST' && route.request().url().endsWith('/settings')) {
      Object.assign(data.settings, route.request().postDataJSON());
      data.alert = !data.settings.autofix;
    }
    await route.fulfill({json: data});
  });
  await page.goto('/');
  await page.getByRole('button', {name: 'Main CI needs attention — open monitor'}).click();
  await expect(page.getByRole('heading', {name: 'Main CI', exact: true})).toBeVisible();
  await expect(page.getByRole('link', {name: 'CI: failure'})).toBeVisible();
  await page.getByRole('checkbox', {name: 'Autofix main CI'}).check();
  await expect(page.getByRole('alert').filter({hasText: 'Main CI needs attention'})).toBeHidden();
  await expect(page.getByRole('checkbox', {name: 'Autofix main CI'})).toBeChecked();
  await page.reload();
  await expect(page.getByRole('heading', {name: 'Main CI', exact: true})).toBeVisible();
  await expect(page.getByRole('checkbox', {name: 'Autofix main CI'})).toBeChecked();
});

test('first-load maintenance error remains visible and never enables updates', async ({page}) => {
  await page.route('**/api/maintenance**', route => route.fulfill({status: 503, json: {error: 'offline'}}));
  await page.goto('/');
  await page.getByRole('button', {name: 'PR updates', exact: true}).click();
  await expect(page.locator('#maintenance-view').getByRole('status')).toContainText('Maintenance status unavailable');
});

test('protected main source repair shows exact diff before candidate authorization', async ({page}) => {
  const data = status();
  data.incidents[0].repair_state = 'source_held';
  data.incidents[0].candidate = 'b'.repeat(40);
  data.incidents[0].source_manifest = 'c'.repeat(64);
  let authorization;
  await page.route('**/api/maintenance**', async route => {
    if (route.request().url().includes('/source?')) {
      await route.fulfill({json: {candidate: 'b'.repeat(40), source_manifest: 'c'.repeat(64),
        diff: 'diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n-old\n+fixed',
        summary: 'Repair workflow configuration', validation: ['python3 -m unittest']}}); return;
    }
    if (route.request().url().endsWith('/authorize-source')) authorization = route.request().postDataJSON();
    await route.fulfill({json: data});
  });
  await page.goto('/');
  await page.getByRole('button', {name: 'Main CI', exact: true}).click();
  await page.getByRole('button', {name: 'Review protected source repair'}).click();
  const dialog = page.getByRole('dialog', {name: 'Review protected main source repair'});
  await expect(dialog).toContainText('+fixed');
  await dialog.getByRole('button', {name: 'Publish this reviewed source repair'}).click();
  await expect.poll(() => authorization?.candidate).toBe('b'.repeat(40));
  await expect.poll(() => authorization?.source_manifest).toBe('c'.repeat(64));
});
