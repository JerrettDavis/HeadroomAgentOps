const {test,expect}=require('@playwright/test');

test('saved experiments show sample distributions and descriptive cross-run changes',async({page})=>{
  const ids=['a'.repeat(32),'b'.repeat(32)];
  const cell=(tokens)=>({id:'cell',state:'completed',scenario:'smoke',provider:'openai',model:'pinned',tool:'native',feature:'default',concurrency:2,
    decision:{status:'inconclusive'},evidence:{schema_version:2,load_batches:['a1_passthrough','b_headroom'].map(arm=>({arm,
      concurrency:2,completed_requests:5,elapsed_seconds:1,requests_per_second:5,prompt_tokens:500})),attempts:['baseline','candidate'].flatMap(arm=>
      [0,1,2,3,4].map(i=>({arm,pair_id:String(i),tokens:arm==='baseline'?100:tokens+i,latency_ms:10+i,success:true})))}});
  await page.route('**/api/benchmarks/measured**',async route=>{
    const group=new URL(route.request().url()).searchParams.get('group');
    if(group){const index=ids.indexOf(group);return route.fulfill({json:{qualification:{status:'inconclusive'},result:{
      experiment_id:index?'Previous run':'Current run',design:{label:'Saved design',seed:index?9:42},lane:'simulator',
      sources:{candidate:{sha:(index?'c':'d').repeat(40)}},cells:[cell(index?80:70)]}}});}
    const response=await route.fetch();const state=await response.json();
    state.groups=ids.map((id,i)=>({id,label:i?'Previous run':'Current run',state:'completed',group_state:'completed',created_at:1,cells:[]}));
    await route.fulfill({json:state});
  });
  await page.goto('/');await page.getByRole('button',{name:'Benchmarking',exact:true}).click();
  const root=page.getByRole('region',{name:'Measured experiment designer',exact:true});
  await root.getByRole('button',{name:'Explore experiment aaaaaaaa',exact:true}).click();
  await expect(root.getByRole('img',{name:'Input token sample distribution: smoke'})).toBeVisible();
  await expect(root.getByRole('table',{name:'Sample distribution summary'})).toContainText('72.0');
  await expect(root.getByRole('table',{name:'Task outcome summary'})).toContainText('100.0%');
  await expect(root.getByRole('table',{name:'Measured load throughput'})).toContainText('5.0');
  await root.locator('[data-m-filter="concurrency"]').selectOption('2');
  await expect(root.locator('[data-m-filter="concurrency"]')).toHaveValue('2');
  await root.getByLabel('Compare with saved experiment',{exact:true}).selectOption(ids[1]);
  const comparison=root.getByRole('table',{name:'Saved experiment comparison'});
  await expect(comparison).toContainText('-10.0');
  await expect(root.getByText('Run settings differ. These changes are descriptive and do not establish a regression.',{exact:true})).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await root.locator('.measured-distributions').screenshot({path:'../../.agentops/measured-distributions-mobile.png'});
  await root.getByRole('heading',{name:'Compare saved experiments',exact:true}).locator('..').screenshot({path:'../../.agentops/measured-comparison-mobile.png'});
});

async function start(page){
  await page.goto('/');
  await page.getByRole('button',{name:'Benchmarking',exact:true}).click();
  const root=page.getByRole('region',{name:'Measured experiment designer',exact:true});
  await root.getByRole('button',{name:'Use Measured regression',exact:true}).click();
  const candidate=root.getByRole('combobox',{name:'Prepared candidate wheel',exact:true});
  await expect(candidate.locator('option[value="'+ 'f'.repeat(32)+'"]')).toHaveCount(1);
  await candidate.selectOption('f'.repeat(32));
  await root.getByRole('button',{name:'Next',exact:true}).click();
  await root.getByLabel('Model ID 1',{exact:true}).fill('pinned-model');
  return root;
}

test('named design library survives reload without submitting an experiment',async({page})=>{
  let launches=0;
  page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/benchmarks')++launches;});
  const root=await start(page);
  await root.getByRole('button',{name:'Back',exact:true}).click();
  await root.getByLabel('Measured experiment name',{exact:true}).fill('Nightly candidate checks');
  await root.getByRole('button',{name:'Save experiment design',exact:true}).click();
  await expect(root.locator('[data-measured-status]')).toHaveText('Experiment design saved to your library.');
  await page.reload();await page.getByRole('button',{name:'Benchmarking',exact:true}).click();
  await root.getByRole('button',{name:'Open saved design Nightly candidate checks',exact:true}).click();
  await expect(root.getByLabel('Measured experiment name',{exact:true})).toHaveValue('Nightly candidate checks');
  await root.getByRole('button',{name:'Next',exact:true}).click();
  await expect(root.getByLabel('Model ID 1',{exact:true})).toHaveValue('pinned-model');
  expect(launches).toBe(0);
});

test('load template previews every concurrency level and its full request budget',async({page})=>{
  await page.goto('/');await page.getByRole('button',{name:'Benchmarking',exact:true}).click();
  const root=page.getByRole('region',{name:'Measured experiment designer',exact:true});
  await root.getByRole('button',{name:'Use Latency and throughput sweep',exact:true}).click();
  await root.getByRole('combobox',{name:'Prepared candidate wheel',exact:true}).selectOption('f'.repeat(32));
  await root.getByRole('button',{name:'Next',exact:true}).click();
  await expect(root.getByLabel('Load concurrency levels',{exact:true})).toHaveValue('1, 2, 4');
  await root.getByLabel('Model ID 1',{exact:true}).fill('pinned-model');
  await root.getByRole('button',{name:'Next',exact:true}).click();
  await expect(root.getByText('3 cells · 120 provider requests',{exact:true})).toBeVisible();
  await expect(root.locator('.studio-matrix')).toContainText('4 concurrent');
});

test('wizard keeps invalid settings beside the field and focuses the first correction',async({page})=>{
  const root=await start(page);
  const model=root.getByLabel('Model ID 1',{exact:true});await model.fill('');
  await root.getByRole('button',{name:'Next',exact:true}).click();
  await expect(model).toBeFocused();await expect(model).toHaveAttribute('aria-invalid','true');
  await expect(root.getByRole('alert')).toHaveText('Enter the pinned model identifier.');
  await model.fill('pinned-model');await root.getByRole('button',{name:'Next',exact:true}).click();
  await expect(root.getByRole('heading',{name:'Review every planned cell',exact:true})).toBeVisible();
});

test('measured wizard previews every cell, preflights fixture and restores its draft',async({page})=>{
  const root=await start(page);
  await root.getByLabel('Repeated context',{exact:true}).check();
  await root.getByText('Preview Protocol smoke test tasks',{exact:true}).click();
  await expect(root.getByText('Expected answer: 42',{exact:true}).first()).toBeVisible();
  await root.getByRole('button',{name:'Next',exact:true}).click();
  await expect(root.getByText('2 cells · 20 provider requests',{exact:true})).toBeVisible();
  await expect(root.getByText('Offline fixture · no provider charges',{exact:true})).toBeVisible();
  await root.getByRole('button',{name:'Save measured draft',exact:true}).click();
  await expect(root.locator('[data-measured-status]')).toHaveText('Measured draft saved.');
  await page.reload();
  await page.getByRole('button',{name:'Benchmarking',exact:true}).click();
  await root.getByRole('button',{name:'Continue measured draft',exact:true}).click();
  await expect(root.getByLabel('Prepared candidate wheel',{exact:true})).toHaveValue('f'.repeat(32));
  await root.getByRole('button',{name:'Next',exact:true}).click();
  await expect(root.getByLabel('Model ID 1',{exact:true})).toHaveValue('pinned-model');
  await expect(root.getByLabel('Repeated context',{exact:true})).toBeChecked();
});

test('real measured group executes paired fixture requests and exposes traceable results',async({page})=>{
  test.setTimeout(120000);
  let posts=0;
  await page.route('**/api/benchmarks',async route=>{
    if(route.request().method()==='POST'&&route.request().postDataJSON().action==='group'){
      ++posts;await route.fetch();await route.abort('connectionfailed');
    }else await route.continue();
  });
  const root=await start(page);
  await root.getByRole('button',{name:'Next',exact:true}).click();
  await root.getByRole('button',{name:'Run measured experiment',exact:true}).click();
  await expect(root.getByRole('heading',{name:'Experiment results',exact:true})).toBeVisible({timeout:100000});
  await expect(root.getByText('Qualification: inconclusive',{exact:true})).toBeVisible();
  await expect(root.getByRole('table',{name:'Experiment cell metrics'})).toContainText('30.0%');
  expect(posts).toBe(1);
  await root.getByText('Samples, uncertainty and evidence',{exact:true}).click();
  await expect(root.getByRole('table',{name:'Paired request samples'})).toContainText('70');
  await page.screenshot({path:'../../.agentops/measured-results-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:'../../.agentops/measured-results-mobile.png'});
});
