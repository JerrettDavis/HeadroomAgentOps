const {test, expect} = require('@playwright/test');

async function open(page) {
  await page.goto('/');
  await page.getByRole('button', {name:'Benchmarking',exact:true}).click();
  await expect(page.getByRole('region',{name:'Benchmark Studio',exact:true})).toBeVisible();
}

test('fresh user launches a real credential-free demo and explores paired evidence', async ({page}) => {
  await open(page);
  const studio = page.getByRole('region',{name:'Benchmark Studio',exact:true});
  await studio.getByRole('button',{name:'Run a demo now',exact:true}).click();
  await expect(studio.getByRole('heading',{name:'Paired results',exact:true})).toBeVisible({timeout:20000});
  await expect(studio.getByText('Synthetic demonstration. No provider requests. Results do not establish measured performance.',{exact:true}).first()).toBeVisible();
  const table = studio.getByRole('table',{name:'Paired scenario metrics'});
  await expect(table).toContainText('30.0%');
  await expect(table).toContainText('conversation');
  await expect(studio.getByRole('img',{name:'Baseline and candidate tokens by scenario'})).toBeVisible();
  await expect(studio.getByText('Live qualification: inconclusive',{exact:true})).toBeVisible();
  await page.screenshot({path:'../../.agentops/studio-results-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  await expect(studio.getByRole('table',{name:'Paired scenario metrics'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:'../../.agentops/studio-results-mobile.png'});
});

test('template wizard edits a matrix, saves a draft and restores it after reload', async ({page}) => {
  await open(page);
  const studio = page.getByRole('region',{name:'Benchmark Studio',exact:true});
  await studio.getByRole('button',{name:'Use Explore context sizes',exact:true}).click();
  await studio.getByLabel('Experiment name',{exact:true}).fill('Saved visual design');
  await studio.getByRole('button',{name:'Next',exact:true}).click();
  await expect(studio.getByRole('heading',{name:'Shape your comparison',exact:true})).toBeVisible();
  await studio.getByLabel('Fast simulator',{exact:true}).check();
  await studio.getByRole('button',{name:'Next',exact:true}).click();
  await expect(studio.getByText('6 cells · 60 paired requests',{exact:true})).toBeVisible();
  await studio.getByRole('button',{name:'Save draft',exact:true}).click();
  await expect(studio.locator('[data-studio-status]')).toHaveText('Draft saved.');
  await page.reload();
  await page.getByRole('button',{name:'Benchmarking',exact:true}).click();
  await studio.getByRole('button',{name:'Continue saved draft',exact:true}).click();
  await expect(studio.getByLabel('Experiment name',{exact:true})).toHaveValue('Saved visual design');
  await studio.getByRole('button',{name:'Next',exact:true}).click();
  await expect(studio.getByLabel('Fast simulator',{exact:true})).toBeChecked();
});

test('source selector debounces search, ignores stale responses and selects a pinned SHA by keyboard', async ({page}) => {
  const queries=[];
  await page.route('**/api/benchmarks/studio/sources',async route=>{
    const data=route.request().postDataJSON();queries.push(data);
    if(data.query==='slow')await new Promise(resolve=>setTimeout(resolve,900));
    try {await route.fulfill({json:{items:[{sha:(data.query==='slow'?'a':'b').repeat(40),subject:data.query==='slow'?'Old response':'Current match',author:'ada',date:'2026-11-25'}],has_more:false}});}catch{}
  });
  await page.route('**/api/benchmarks/studio/artifacts',route=>route.fulfill({json:{items:[{id:12,name:'candidate-wheel',size_in_bytes:4096,run_id:9,expired:false,expires_at:'2026-12-25'}],has_more:false,notice:'Pinned artifacts.'}}));
  await open(page);
  await page.getByText('Find commit and build sources',{exact:true}).click();
  await expect(page.getByLabel('Repository',{exact:true})).not.toHaveValue('');
  const input=page.getByRole('combobox',{name:'Find a source',exact:true});
  await input.fill('slow');
  await expect.poll(()=>queries.length).toBe(1);
  await input.fill('new query');
  await expect(page.getByRole('option',{name:/Current match/})).toBeVisible();
  await expect(page.getByRole('option',{name:/Old response/})).toHaveCount(0);
  await input.press('ArrowDown');await input.press('Enter');
  const selected=page.getByRole('region',{name:'Selected source',exact:true});
  await expect(selected).toContainText('b'.repeat(40));
  await expect(selected).toContainText('candidate-wheel');
  expect(queries[1]).toMatchObject({query:'new query',page:1,kind:'commits'});
});
