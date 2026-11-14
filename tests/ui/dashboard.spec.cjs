const {test, expect} = require('@playwright/test');

test.beforeEach(async ({page, request}) => {
  await page.route('**/*', route => new URL(route.request().url()).origin === 'http://127.0.0.1:18766'
    ? route.continue() : route.abort());
  const info = await (await request.get('/api/launcher')).json();
  await request.post('/api/automation', {
    headers: {'Origin': 'http://127.0.0.1:18766', 'X-Launch-Token': info.token},
    data: {new_prs: false, updated_prs: false, non_draft_only: true},
  });
  await page.goto('/');
  await expect(page.locator('.markdown-body h1')).toHaveText('Summary');
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
