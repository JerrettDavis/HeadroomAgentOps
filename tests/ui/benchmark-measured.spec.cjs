const {test,expect}=require('@playwright/test');

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

test('measured wizard previews every cell, preflights fixture and restores its draft',async({page})=>{
  const root=await start(page);
  await root.getByLabel('Repeated context',{exact:true}).check();
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
