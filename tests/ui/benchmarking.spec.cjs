const {test, expect} = require('@playwright/test');

async function fixture(page, initial = []) {
  const state = {jobs: initial, posts: []};
  await page.route('**/api/launcher', route => route.fulfill({json:{token:'benchmark-test-token'}}));
  await page.route('**/api/benchmark-files?**', route => route.fulfill({json:{directory:'/fixture',parent:'/',roots:[{name:'Home',path:'/fixture'}],entries:['/candidate.whl','/missing','/keep-this'].map(path => ({name:path.slice(1),path,directory:false})),kind:'wheel'}}));
  await page.route('**/api/benchmarks', async route => {
    if (route.request().method() === 'POST') {
      const payload = route.request().postDataJSON();
      state.posts.push({payload,token:route.request().headers()['x-launch-token']});
      state.jobs.unshift({id:`job-${state.posts.length}`,action:payload.action,state:'completed',paths:{inspection:'/saved/inspection.json',manifest:'/saved/manifest.json'},decision:payload.action === 'run' ? {status:'pass',mean_savings:0.24,simulator:true} : null});
      await route.fulfill({json:{job:state.jobs[0]}});
    } else await route.fulfill({json:{jobs:state.jobs,defaults:{tasks:'/tasks.json',policy:'/policy.json'},active:false}});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'Benchmarking',exact:true}).click();
  await expect(page.locator('#benchmark-status')).toContainText('up to date');
  return state;
}

async function chooseArtifact(page, form, path) {
  await form.getByRole('button',{name:'Browse Candidate artifact path',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:`Select file: ${path.slice(1)}`,exact:true}).click();
}

test('guided inspect, create and run submits authenticated actions and shows measured results', async ({page}) => {
  const state = await fixture(page);
  const inspect = page.locator('form[data-action=inspect]');
  await chooseArtifact(page,inspect,'/candidate.whl');
  await inspect.getByRole('combobox',{name:'Transport',exact:true}).selectOption('docker');
  await inspect.getByLabel('Docker image').fill('candidate:test');
  await inspect.getByRole('button',{name:'Inspect candidate',exact:true}).click();
  await expect(inspect.locator('[role=status]')).toContainText('Job submitted');
  expect(state.posts[0]).toEqual({payload:{action:'inspect',artifact:'/candidate.whl',transport:'docker',image:'candidate:test'},token:'benchmark-test-token'});
  const create = page.locator('form[data-action=create]');
  await create.getByLabel('Completed inspection').selectOption('job-1');
  await create.getByLabel('Candidate Git SHA').fill('a'.repeat(40));
  await create.getByLabel('Model',{exact:true}).fill('test-model');
  await create.getByLabel('Experiment label').fill('candidate-check');
  await create.getByRole('button',{name:'Create experiment',exact:true}).click();
  await expect(create.locator('[role=status]')).toContainText('Job submitted');
  expect(state.posts[1].payload).toMatchObject({action:'create',inspection_id:'job-1',tasks:'/tasks.json',policy:'/policy.json',seed:42,repeats:5});
  const run = page.locator('form[data-action=run]');
  await run.getByRole('combobox',{name:'Saved experiment',exact:true}).selectOption('job-2');
  await chooseArtifact(page,run,'/candidate.whl');
  await run.getByRole('button',{name:'Run experiment',exact:true}).click();
  await expect(page.locator('.benchmark-decision')).toContainText('pass');
  await expect(page.locator('.benchmark-metrics')).toContainText('0.24');
  await expect(page.locator('.benchmark-metrics')).toContainText('simulator');
  expect(state.posts[2].payload).toMatchObject({action:'run',experiment_id:'job-2',artifact:'/candidate.whl',transport:'local'});
});

test('service failures are actionable and a form can be retried', async ({page}) => {
  await fixture(page);
  await page.route('**/api/benchmarks', route => route.request().method() === 'POST' ? route.fulfill({status:400,json:{error:'Artifact path does not exist.'}}) : route.fulfill({json:{jobs:[],defaults:{},active:false}}));
  const form = page.locator('form[data-action=inspect]');
  await chooseArtifact(page,form,'/missing');
  await form.getByRole('button',{name:'Inspect candidate',exact:true}).click();
  await expect(form.locator('[role=status]')).toHaveText('Artifact path does not exist.');
  await expect(form.getByRole('button',{name:'Inspect candidate',exact:true})).toBeEnabled();
});

test('navigation preserves fields and restores the view; unsafe job labels remain text', async ({page}) => {
  await fixture(page,[{id:'<img src=x onerror=alert(1)>',action:'inspect',state:'failed',error:'<script>alert(1)</script>',paths:{inspection:'<svg onload=alert(1)>'}}]);
  const input = page.locator('form[data-action=inspect]').getByLabel('Candidate artifact path',{exact:true});
  await chooseArtifact(page,page.locator('form[data-action=inspect]'),'/keep-this');
  await page.getByRole('button',{name:'Reviews',exact:true}).click();
  await page.getByRole('button',{name:'Benchmarking',exact:true}).click();
  await expect(input).toHaveValue('/keep-this');
  await expect(page.locator('#benchmark-jobs')).toContainText('<img src=x onerror=alert(1)>');
  await expect(page.locator('#benchmark-jobs img, #benchmark-jobs script, #benchmark-jobs svg')).toHaveCount(0);
  await page.reload();
  await expect(page.locator('#benchmark-view')).toBeVisible();
});

test('mobile workflow fits viewport and works with keyboard', async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  await fixture(page);
  const input = page.locator('form[data-action=inspect]').getByLabel('Candidate artifact path',{exact:true});
  await expect(input).toHaveAttribute('readonly','');
  const browse = page.locator('form[data-action=inspect]').getByRole('button',{name:'Browse Candidate artifact path',exact:true});
  await browse.focus(); await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('dialog').getByRole('button',{name:'Select file: candidate.whl',exact:true}).focus();
  await page.keyboard.press('Enter');
  await expect(input).toHaveValue('/candidate.whl');
  await expect(browse).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(await page.locator('#benchmark-view').evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
});

test('opening the view only reads state and refresh failure removes stale decisions', async ({page}) => {
  const state = await fixture(page,[{id:'run-1',action:'run',state:'completed',decision:{status:'pass',mean_savings:0.3},paths:{}}]);
  expect(state.posts).toEqual([]);
  await expect(page.locator('.benchmark-metrics')).toContainText('30.00%');
  await page.route('**/api/benchmarks',route => route.fulfill({status:503,json:{error:'Benchmark service unavailable.'}}));
  await page.getByRole('button',{name:'Refresh jobs',exact:true}).click();
  await expect(page.locator('#benchmark-status')).toHaveText('Benchmark service unavailable.');
  await expect(page.locator('.benchmark-decision')).toHaveCount(0);
  await expect(page.locator('#benchmark-jobs')).toContainText('could not be verified');
});

test('active jobs can be cancelled and interrupted runs offer result collection', async ({page}) => {
  const state = await fixture(page,[{id:'active-1',action:'run',state:'running',paths:{}},{id:'remote-1',action:'run',state:'interrupted',paths:{}}]);
  let cancel;
  await page.route('**/api/benchmarks/cancel',async route => {
    cancel = {payload:route.request().postDataJSON(),token:route.request().headers()['x-launch-token']};
    state.jobs[0].state = 'cancelled';
    await route.fulfill({json:{ok:true}});
  });
  await page.getByRole('button',{name:'Cancel job',exact:true}).click();
  await expect(page.getByRole('button',{name:'Cancel job',exact:true})).toHaveCount(0);
  expect(cancel).toEqual({payload:{id:'active-1'},token:'benchmark-test-token'});
  await page.locator('.benchmark-job').filter({hasText:'remote-1'}).getByRole('button',{name:'Collect results',exact:true}).click();
  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0].payload).toEqual({action:'collect',run_id:'remote-1'});
});

test('every file path uses the authenticated filesystem picker with folder navigation and Escape', async ({page}) => {
  await fixture(page);
  const requests = [];
  await page.route('**/api/benchmark-files?**', route => {
    const url = new URL(route.request().url());
    requests.push({path:url.searchParams.get('path'),kind:url.searchParams.get('kind'),token:route.request().headers()['x-launch-token']});
    const nested = url.searchParams.get('path') === '/fixture/folder';
    return route.fulfill({json:{directory:nested ? '/fixture/folder' : '/fixture',parent:nested ? '/fixture' : '/',roots:[{name:'Home',path:'/fixture'}],entries:nested ? [{name:'candidate.whl',path:'/fixture/folder/candidate.whl',directory:false}] : [{name:'folder',path:'/fixture/folder',directory:true}],kind:'wheel'}});
  });
  for (const field of ['artifact','config','inspection_path','tasks','policy','manifest','dataset','ssh_config']) {
    const inputs = page.locator(`#benchmark-view input[name="${field}"]`);
    for (let index = 0; index < await inputs.count(); index++) {
      await expect(inputs.nth(index)).toHaveAttribute('readonly','');
      await expect(inputs.nth(index).locator('xpath=../..').locator(`[data-browse="${field}"]`)).toHaveCount(1);
    }
  }
  const button = page.locator('form[data-action=inspect] [data-browse=artifact]');
  await button.click();
  await page.getByRole('dialog').getByRole('button',{name:'Open folder: folder',exact:true}).click();
  await expect(page.locator('#benchmark-picker-directory')).toHaveText('/fixture/folder');
  await page.getByRole('button',{name:'Up one folder',exact:true}).click();
  await expect(page.locator('#benchmark-picker-directory')).toHaveText('/fixture');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(button).toBeFocused();
  expect(requests.every(request => request.token === 'benchmark-test-token' && request.kind === 'wheel')).toBe(true);
  expect(requests.map(request => request.path)).toContain('/fixture/folder');
});
