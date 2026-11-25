const {test, expect} = require('@playwright/test');

test('automatic review status distinguishes reused analysis from unresolved holds', async ({page, request}) => {
  const info=await (await request.get('/api/launcher')).json();
  info.automation.settings.new_prs=true;
  info.automation.attention=[{state:'held'},{state:'target_qualification_required'},
    {state:'publication_qualification_required'},{state:'review_reused'},{state:'review_reused'}];
  await page.route('**/api/launcher', route=>route.fulfill({json:info}));
  await page.reload();
  await expect(page.locator('#autostatus')).toContainText('3 unresolved holds');
  await expect(page.locator('#autostatus')).toContainText('2 reused');
});

test('stopped queued records are historical, not pending live review work', async ({page, request}) => {
  const all = await (await request.get('/api/report')).json();
  const run = all.runs.find(r => r.total === 30);
  const report = await (await request.get('/api/report?run=' + run.id)).json();
  report.runs.find(r => r.id === run.id).stopped = true;
  report.jobs.forEach(job => { job.state = 'queued'; job.stale = 0; });
  report.states = {queued: report.jobs.length};
  report.finished = 0;
  report.active_prs = 0;
  await page.route('**/api/report*', route => route.fulfill({json: report}));
  await page.reload();
  await expect(page.locator('#metrics .metric').first()).toContainText('Queued for dispatch');
  await expect(page.locator('#metrics .metric').first().locator('strong')).toHaveText('0');
  await expect(page.locator('#metrics')).toContainText('30 stopped before dispatch');
  await expect(page.locator('.job').first()).toContainText('Stopped before dispatch');
  await expect(page.locator('#metrics')).not.toContainText('Processed');
});

test.beforeEach(async ({page, request, baseURL}) => {
  await page.addInitScript(() => localStorage.setItem('agentops-view', 'reviews'));
  await page.route('**/*', route => new URL(route.request().url()).origin === baseURL
    ? route.continue() : route.abort());
  const info = await (await request.get('/api/launcher')).json();
  await request.post('/api/automation', {
    headers: {'Origin': baseURL, 'X-Launch-Token': info.token},
    data: {new_prs: false, updated_prs: false, non_draft_only: true},
  });
  await request.post('/api/allowances', {
    headers: {'Origin': baseURL, 'X-Launch-Token': info.token},
    data: {provider: 'codex', stop_percent: 80, enabled: true},
  });
  await page.goto('/');
  await expect(page.locator('.markdown-body h1').first()).toHaveText('Summary');
  const report = await (await request.get('/api/report')).json();
  await page.locator('#run').selectOption(report.runs.find(r => r.total === 30).id);
  await expect(page.locator('.job')).toHaveCount(30);
  await expect(page.locator('#detail>.pr-description')).toBeVisible();
  await expect(page.locator('.markdown-body h1')).toHaveText('Summary');
});

test('Devin is available as a reviewer and has allowance controls', async ({page}) => {
  await page.getByRole('button', {name: 'New review', exact: true}).click();
  await page.locator('#launchharness').selectOption('devin');
  await expect(page.locator('#launchharness')).toHaveValue('devin');
  await page.locator('#closewizard').click();
  const card = page.locator('.allowance-card[data-provider="devin"]');
  await expect(card.getByRole('heading', {name: 'Devin', exact: true})).toBeVisible();
  await card.getByText('Review stop point', {exact: true}).click();
  await expect(card.getByRole('spinbutton', {name: 'Devin stop percentage'})).toBeVisible();
});

test('provider allowance shows real windows and persists an inclusive stop point', async ({page}) => {
  const card = page.locator('.allowance-card[data-provider="codex"]');
  await expect(card).toContainText('60% used');
  await expect(card).toContainText('Weekly');
  await expect(card.getByRole('progressbar')).toHaveAttribute('value', '60');
  await card.getByText('Review stop point', {exact: true}).click();
  const threshold = card.getByRole('spinbutton', {name: 'Codex stop percentage'});
  await threshold.fill('60');
  await page.evaluate(() => refreshAllowances());
  await expect(threshold).toHaveValue('60');
  await card.getByRole('button', {name: 'Save limits'}).click();
  await expect(card.locator('.allowance-state')).toHaveText('Paused');
  await page.reload();
  await expect(card.locator('.allowance-state')).toHaveText('Paused');
  await card.getByText('Review stop point', {exact: true}).click();
  await expect(card.getByRole('spinbutton')).toHaveValue('60');
  await card.getByRole('spinbutton').fill('80');
  await card.getByRole('button', {name: 'Save limits'}).click();
  await expect(card.locator('.allowance-state')).toHaveText('Available');
});

test('provider allowance unknown state never renders as zero usage', async ({page, request}) => {
  const status = await (await request.get('/api/allowances')).json();
  Object.assign(status.providers[0], {state: 'unavailable', allowed: false, fresh: false,
    windows: [], observed_at: null, reason: 'Account allowance unavailable; new reviews wait.'});
  await page.route('**/api/allowances', route => route.fulfill({json: status}));
  await page.reload();
  const card = page.locator('.allowance-card[data-provider="codex"]');
  await expect(card.locator('.allowance-state')).toHaveText('Unavailable');
  await expect(card).toContainText('new reviews wait');
  await expect(card.getByRole('progressbar')).toHaveCount(0);
  await expect(card).not.toContainText('0% used');
});

test('provider allowance controls are keyboard accessible and override remains visible', async ({page}) => {
  const card = page.locator('.allowance-card[data-provider="codex"]');
  await expect(card.locator('.allowance-state')).toHaveText('Available');
  await card.locator('summary').focus();
  await page.keyboard.press('Enter');
  await page.keyboard.press('Tab');
  await expect(card.getByRole('spinbutton')).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(card.getByRole('spinbutton')).toHaveValue('79.9');
  await page.keyboard.press('Tab');
  await expect(card.getByRole('checkbox', {name: 'Enforce limits'})).toBeFocused();
  await page.keyboard.press('Space');
  await page.keyboard.press('Tab');
  await expect(card.getByRole('button', {name: 'Save limits'})).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(card.locator('.allowance-state')).toHaveText('Enforcement off');
  await page.reload();
  await expect(card.locator('.allowance-state')).toHaveText('Enforcement off');
  await card.locator('summary').click();
  await expect(card.getByRole('spinbutton')).toHaveValue('79.9');
  await expect(card.getByRole('checkbox')).not.toBeChecked();
});

test('saving one provider preserves another provider draft and live status updates', async ({page}) => {
  const codex = page.locator('.allowance-card[data-provider="codex"]');
  const claude = page.locator('.allowance-card[data-provider="claude"]');
  await expect(claude).toBeVisible();
  await codex.locator('summary').click();
  await claude.locator('summary').click();
  await claude.getByRole('spinbutton').fill('55');
  await codex.getByRole('spinbutton').fill('70');
  await codex.getByRole('button', {name: 'Save limits'}).click();
  await expect(page.locator('#allowance-message')).toContainText('Review limits saved.');
  await expect(claude.getByRole('spinbutton')).toHaveValue('55');
  await expect(claude.locator('details')).toHaveAttribute('open', '');
  await page.locator('#search').focus();
  await page.route('**/api/allowances', async route => {
    const response = await route.fetch();
    const body = await response.json();
    Object.assign(body.providers.find(p => p.provider === 'claude'), {state: 'paused', allowed: false});
    await route.fulfill({json: body});
  });
  await page.evaluate(() => refreshAllowances());
  await expect(claude.getByRole('spinbutton')).toHaveValue('55');
  await expect(claude.locator('.allowance-state')).toHaveText('Paused');
  await expect(codex.locator('.allowance-state')).toHaveText('Available');
});

test('ALL is default and combines a PR once with labeled content from both sessions', async ({page, request}) => {
  await page.reload();
  await expect(page.locator('#run')).toHaveValue('all');
  await expect(page.locator('#captured')).toContainText('2 sessions');
  await expect(page.locator('.job')).toHaveCount(31);
  const row = page.locator('.job[data-pr-number="1"]');
  await expect(row).toHaveCount(1);
  await expect(row).toContainText('2 sessions');
  await row.click();
  await expect(page.locator('.session-record')).toHaveCount(2);
  await expect(page.locator('#detail')).toContainText('Initial session review');
  await expect(page.locator('#detail')).toContainText('Follow-up session review');
  const report = await (await request.get('/api/report')).json();
  const sourceRuns = report.jobs.find(j => j.number === 1).sessions.map(s => s.run_id);
  expect(await page.locator('.session-record').evaluateAll(cards => cards.map(c => c.dataset.sourceRun))).toEqual(sourceRuns);
  await page.locator('[data-tab=diff]').click();
  await expect(page.locator('.diffline.addition')).toHaveText(['+followup', '+new']);
  await page.locator('[data-tab=evidence]').click();
  await expect(page.locator('#detail')).toContainText('Initial session evidence');
  await expect(page.locator('#detail')).toContainText('Follow-up session evidence');
  await page.locator('[data-tab=comments]').click();
  await expect(page.locator('#detail .comment')).toHaveCount(2);
  await expect(page.locator('#detail .session-tag')).toHaveCount(2);
  await page.locator('[data-tab=sessions]').click();
  await expect(page.locator('.session-record')).toHaveCount(2);
  await expect(page.locator('#detail')).toContainText('Input 100');
  await expect(page.locator('#detail')).toContainText('Input 200');
  await expect(page.locator('#agents .session-tag')).toHaveCount(1);
  await expect(page.locator('#usage')).toContainText('300');
  await expect(page.locator('.usage-session')).toHaveCount(2);
});

test('choosing a specific session filters the aggregate back to that run', async ({page, request}) => {
  const report = await (await request.get('/api/report')).json();
  await page.locator('#run').selectOption(report.runs.find(r => r.total === 2).id);
  await expect(page.locator('.job')).toHaveCount(2);
  await page.locator('.job[data-pr-number="1"]').click();
  await expect(page.locator('#detail')).toContainText('Follow-up session review');
  await expect(page.locator('#detail')).not.toContainText('Initial session review');
});

test('session selection survives an in-flight background refresh', async ({page}) => {
  await page.route('**/api/report**', async route => {
    await new Promise(resolve => setTimeout(resolve, 200));
    await route.continue();
  });
  await page.evaluate(() => {refresh();});
  await page.locator('#run').selectOption('all');
  await expect(page.locator('#run')).toHaveValue('all');
  await expect(page.locator('.job')).toHaveCount(31);
});

test('Markdown supports formatting and rejects active or app-spoofing HTML', async ({page}) => {
  const result = await page.evaluate(() => {
    const source = '# Heading\n\n**Bold** and _emphasis_.\n\n- [x] Done\n- [ ] Pending\n\n' +
      '| A | B |\n| --- | --- |\n| 1 | 2 |\n\n```python\nprint(1)\n```\n\n' +
      '[Guide](docs/guide.md)\n\n<script>window.markdownXss=true</script>' +
      '<img src=x onerror="window.markdownXss=true"><a href="javascript:alert(1)">bad</a>' +
      '<svg onload="window.markdownXss=true"></svg><iframe src="https://example.com"></iframe>' +
      '<form id=theme><input type=text><button>spoof</button></form>';
    const host = document.createElement('div');
    host.innerHTML = renderMarkdown(source, {repo: 'example/repository', headSha: 'abc123'});
    document.body.append(host);
    const result = {
      heading: host.querySelector('h1')?.textContent,
      table: !!host.querySelector('table'), bold: !!host.querySelector('strong'),
      code: host.querySelector('pre code')?.textContent.trim(),
      checks: [...host.querySelectorAll('input')].map(e => [e.type, e.disabled, e.checked]),
      href: host.querySelector('a')?.href,
      unsafe: host.querySelectorAll('script,svg,iframe,form,button,[onerror],[onload],[id]').length,
      unsafeLinks: [...host.querySelectorAll('a[href]')].filter(a => a.href.startsWith('javascript:')).length,
      executed: !!window.markdownXss,
    };
    host.remove();
    return result;
  });
  expect(result).toEqual({heading: 'Heading', table: true, bold: true, code: 'print(1)',
    checks: [['checkbox', true, true], ['checkbox', true, false]],
    href: 'https://github.com/example/repository/blob/abc123/docs/guide.md',
    unsafe: 0, unsafeLinks: 0, executed: false});
});

test('PR diffs use recorded patches and omit an empty agent-edits section', async ({page}) => {
  await page.locator('[data-tab=diff]').click();
  await expect(page.locator('.filename')).toHaveText('source.py');
  await expect(page.locator('.diffline.addition')).toHaveText('+new');
  await expect(page.locator('.diffline.deletion')).toHaveText('-old');
  await expect(page.locator('#detail')).not.toContainText('No text patch available');
  await expect(page.locator('#detail')).not.toContainText('No agent edits exported');
  await expect(page.locator('#detail h3', {hasText: 'Agent edits'})).toHaveCount(0);
});

for (const viewport of [{width: 1440, height: 900}, {width: 1024, height: 768}, {width: 960, height: 600}]) {
  test(`three columns fill ${viewport.width} viewport without page overflow`, async ({page}) => {
    await page.setViewportSize(viewport);
    const geometry = await page.evaluate(() => ({
      x: document.documentElement.scrollWidth > innerWidth,
      y: document.documentElement.scrollHeight > innerHeight,
      bottoms: [...document.querySelector('.workspace').children].map(e => Math.round(e.getBoundingClientRect().bottom)),
    }));
    expect(geometry.x).toBe(false);
    expect(geometry.y).toBe(false);
    expect(new Set(geometry.bottoms).size).toBe(1);
    expect(geometry.bottoms[0]).toBeGreaterThan(viewport.height - 32);
  });
}

test('mobile layout and options stay within the viewport', async ({page}) => {
  await page.setViewportSize({width: 375, height: 812});
  await page.locator('#automation-toggle').click();
  await expect(page.locator('#automation-panel')).toBeVisible();
  const bounds = await page.locator('#automation-panel').boundingBox();
  expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(375);
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(812);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('narrow detail tabs stay on one line and keyboard selection scrolls into view', async ({page}) => {
  await page.setViewportSize({width: 960, height: 600});
  await page.locator('.tabs').evaluate(element => {element.style.width = '280px';});
  const rows = await page.locator('[data-tab]').evaluateAll(tabs => new Set(tabs.map(t => Math.round(t.getBoundingClientRect().top))).size);
  expect(rows).toBe(1);
  await page.locator('[data-tab=details]').focus();
  await page.keyboard.press('End');
  await expect(page.locator('[data-tab=sessions]')).toHaveAttribute('aria-selected', 'true');
  await expect.poll(async () => page.locator('[data-tab=sessions]').evaluate(tab => {
    const strip = tab.parentElement.getBoundingClientRect(), bounds = tab.getBoundingClientRect();
    return bounds.left >= strip.left && bounds.right <= strip.right;
  })).toBe(true);
  await page.keyboard.press('Home');
  await expect(page.locator('[data-tab=details]')).toHaveAttribute('aria-selected', 'true');
});

test('options persist and dismiss with Escape; tray hash opens popup', async ({page}) => {
  await page.locator('#automation-toggle').click();
  await page.locator('#autonew').check();
  await expect(page.locator('#automation-toggle')).toHaveAttribute('data-state', 'on');
  await page.keyboard.press('Escape');
  await expect(page.locator('#automation-panel')).toBeHidden();
  await page.reload();
  await page.goto('/#autostatus');
  await expect(page.locator('#automation-panel')).toBeVisible();
  await expect(page.locator('#autonew')).toBeChecked();
  await page.getByRole('button', {name: 'Close automatic review options'}).click();
  await expect(page.locator('#automation-panel')).toBeHidden();
});

test('dashboard refresh icon persists paused state without changing review settings', async ({page, request}) => {
  await page.locator('#auto').click();
  await expect(page.locator('#auto')).toHaveAttribute('aria-pressed', 'false');
  await page.reload();
  await expect(page.locator('#auto')).toHaveAttribute('aria-pressed', 'false');
  const info = await (await request.get('/api/launcher')).json();
  expect(info.automation.settings).toEqual({new_prs: false, updated_prs: false, non_draft_only: true});
  await page.locator('#auto').click();
  await expect(page.locator('#auto')).toHaveAttribute('aria-pressed', 'true');
});

test('description grows past old cap and short content fills remaining column', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 1200});
  const sizes = await page.evaluate(() => {
    const body = document.querySelector('.pr-description .bodytext');
    const longHeight = body.clientHeight;
    body.textContent = 'Short description';
    const detail = document.querySelector('#detail');
    return {longHeight, bottom: Math.round(body.getBoundingClientRect().bottom),
      expected: Math.round(detail.getBoundingClientRect().bottom - parseFloat(getComputedStyle(detail).paddingBottom))};
  });
  expect(sizes.longHeight).toBeGreaterThan(330);
  expect(sizes.bottom).toBe(sizes.expected);
});

test('scroll thumb appears while scrolling and fades when idle', async ({page}) => {
  const states = await page.evaluate(async () => {
    const probe = document.createElement('div');
    probe.style.cssText = 'position:fixed;left:-9999px;width:60px;height:60px;overflow:auto';
    probe.innerHTML = '<div style="height:600px"></div>';
    document.body.append(probe);
    const color = () => getComputedStyle(probe).scrollbarColor;
    const idle = color();
    probe.scrollTop = 80;
    await new Promise(r => setTimeout(r, 300));
    const active = color();
    await new Promise(r => setTimeout(r, 1100));
    const faded = color();
    probe.remove();
    return {idle, active, faded};
  });
  expect(states.active).not.toBe(states.idle);
  expect(states.faded).toBe(states.idle);
});
