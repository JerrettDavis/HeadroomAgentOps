/* Real candidate experiments: drafts, preflight, durable matrix jobs and evidence. */
(() => {
  const host=document.getElementById('benchmark-view'),studio=host?.querySelector('.studio');
  if(!studio)return;
  const root=document.createElement('section');
  root.className='studio measured-studio';root.setAttribute('aria-label','Measured experiment designer');
  studio.after(root);
  root.innerHTML='<div class="studio-section-title"><div><p class="studio-eyebrow">MEASURED EXPERIMENTS</p><h2>Design a real comparison</h2></div><button type="button" data-m-refresh>Refresh experiments</button></div><p data-measured-status role="status" aria-live="polite"></p><div data-measured-content></div><section data-measured-history aria-label="Measured experiment history"></section><section data-tool-history aria-label="Tool runtime setup history"></section>';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let catalogue,design,draft,sources=[],groups=[],active=false,step=0,report,result,qualification,selected,recovering=null,toolSetups=[];
  const retryCells=new Set();
  let visible=!host.hidden,loading=false,busy=false,initialized=false;
  let restorePending=true,watchPending=false,navigationVersion=0;
  const pendingKey='agentops-measured-submission';
  const models={};const filters={};
let comparison=null,comparisonId='',comparisonVersion=0;
let savedDesigns=[];
  const terminal=new Set(['completed','cancelled','failed','interrupted','recoverable']);
  async function request(path,payload){
    const auth=await fetch('/api/launcher');if(!auth.ok)throw Error('Dashboard authentication unavailable. Refresh and try again.');
    const token=await auth.json();
    const response=await fetch(path,{method:payload===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Launch-Token':token.token||token.launch_token||''},...(payload===undefined?{}:{body:JSON.stringify(payload)})});
    const data=await response.json();if(!response.ok)throw Error(data.error||'Measured experiment request failed. Refresh saved groups before retrying.');return data;
  }
  function pending(){try{return JSON.parse(localStorage.getItem(pendingKey)||'null');}catch{return null;}}
  function clearPending(){try{localStorage.removeItem(pendingKey);}catch{}watchPending=false;}
  function ordered(value){if(Array.isArray(value))return value.map(ordered);if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(k=>[k,ordered(value[k])]));return value;}
  async function submit(payload){
    const intent=structuredClone(payload);if(intent.recover)intent.recover.cells.sort();
    const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(ordered(intent))));
    const signature=[...new Uint8Array(bytes)].map(x=>x.toString(16).padStart(2,'0')).join('');
    const previous=pending();const id=previous?.signature===signature?previous.id:crypto.randomUUID().replaceAll('-','');
    try{localStorage.setItem(pendingKey,JSON.stringify({id,signature}));}catch{}
    watchPending=true;
    const job=await request('/api/benchmarks',{...intent,client_request_id:id});clearPending();return job;
  }
  function status(message){root.querySelector('[data-measured-status]').textContent=message;}
  const value=(key)=>root.querySelector(`[data-m-setting="${key}"]`)?.value;
  const input=(key,label,current,type='text')=>`<label class="studio-input">${esc(label)}<input data-m-setting="${key}" type="${type}" value="${esc(current)}"></label>`;
  const pick=(key,label,current,kind)=>`<div class="benchmark-field"><label>${esc(label)}<input data-m-setting="${key}" name="${kind==='wheel'?'artifact':'config'}" value="${esc(current)}" readonly></label><button type="button" data-browse="${key}" data-kind="${kind}" aria-label="Browse ${esc(label)}">Browse</button></div>`;
  function recoveryActions(){
    return `<p class="studio-note"><span data-retry-count>${retryCells.size}</span> selected cells across all filters. Retrying may send new provider requests; collection only retrieves existing execution evidence. Completed cells outside the selection are reused.</p><button type="button" data-m-retry ${!retryCells.size||active||busy?'disabled':''}>Retry selected cells</button><button type="button" data-m-collect ${!retryCells.size||active||busy?'disabled':''}>Collect selected remote evidence</button>`;
  }
  function sourceFields(key,label){
    const source=design[key];
    return `<fieldset class="measured-source"><legend>${label}</legend><label class="studio-input">${key==='candidate'?'Candidate source':'Baseline mode'}<select data-source-mode="${key}">${key==='baseline'?`<option value="passthrough" ${source.kind==='passthrough'?'selected':''}>Same build · optimization disabled</option>`:''}<option value="prepared" ${source.kind==='prepared'?'selected':''}>Prepared commit or remote artifact</option><option value="local" ${source.kind==='local'?'selected':''}>Local wheel and pinned commit</option></select></label>${source.kind==='prepared'?`<label class="studio-input">Prepared ${key} wheel<select aria-label="Prepared ${key} wheel" data-source-job="${key}"><option value="">Choose a verified prepared source</option>${sources.map(s=>`<option value="${esc(s.id)}" ${source.job_id===s.id?'selected':''}>${esc(s.label||s.id.slice(0,8))}</option>`).join('')}</select></label><button type="button" data-m-find-source>Find or build a commit</button>`:source.kind==='local'?pick(key+'-artifact',label+' wheel',source.artifact,'wheel')+input(key+'-sha',label+' full commit SHA',source.sha):'<p class="studio-note">Both arms use the candidate build. The baseline disables optimization; the candidate uses the selected feature settings.</p>'}</fieldset>`;
  }
  function render(){
    const content=root.querySelector('[data-measured-content]');
    if(!step){
      content.innerHTML=`<p class="studio-note">Choose actual candidate sources, scenarios, feature variants, tools, providers and models. Preflight shows prerequisites before any measured requests.</p>${draft?'<button type="button" data-m-continue>Continue measured draft</button>':''}<div class="studio-templates">${catalogue.templates.map(t=>`<article><span class="studio-badge">Measured template · v${t.version}</span><h3>${esc(t.name)}</h3><p>${esc(t.description)}</p><button type="button" data-m-template="${esc(t.id)}">Use ${esc(t.name)}</button></article>`).join('')}</div>`;
    }else{
      content.innerHTML=`<div class="studio-designer"><nav aria-label="Measured experiment steps">${['Sources','Design','Preflight','Results'].map((name,index)=>`<button type="button" data-m-step="${index+1}" ${step===index+1?'aria-current="step"':''} ${index===3&&!result?'disabled':''}>${index+1}. ${name}</button>`).join('')}</nav><div data-measured-step tabindex="-1"></div><div class="studio-actions"><button type="button" data-m-home>Measured templates</button><button type="button" data-m-save>Save measured draft</button>${step>1?'<button type="button" data-m-back>Back</button>':''}${step<3?'<button type="button" data-m-next>Next</button>':''}</div></div>`;
      const panel=content.querySelector('[data-measured-step]');
      if(step===1){
        const runner=design.runner;
        const limits=runner.transport==='docker'?`<div class="studio-numbers">${input('cpus','CPU limit',runner.cpus||2,'number')}${input('memory_gib','Memory limit (GiB)',runner.memory_gib||4,'number')}</div>`:'<p class="studio-note">Local and SSH resources are observed on the runner. CPU and memory limits are configurable for Docker.</p>';
        panel.innerHTML=`<p class="studio-eyebrow">01 / PIN THE COMPARISON</p><h3>Select sources and runner</h3>${input('label','Measured experiment name',design.label)}<div class="measured-source-grid">${sourceFields('candidate','Candidate')}${sourceFields('baseline','Baseline')}</div>${design.baseline.kind!=='passthrough'?`<div class="studio-options"><label class="studio-choice"><input type="checkbox" data-m-flag="baseline_optimization" ${design.baseline_optimization!==false?'checked':''}><span>Enable baseline build optimization</span></label><label class="studio-choice"><input type="checkbox" data-m-flag="candidate_optimization" ${design.candidate_optimization!==false?'checked':''}><span>Enable candidate build optimization</span></label></div>`:''}<fieldset><legend>Execution runner</legend><label class="studio-input">Measured transport<select data-m-setting="transport">${['local','docker','ssh'].map(t=>`<option ${runner.transport===t?'selected':''}>${t}</option>`).join('')}</select></label>${runner.transport==='docker'?input('image','Benchmark runner Docker image',runner.image||''):runner.transport==='ssh'?input('host','Benchmark SSH host',runner.host||'')+input('ssh_port','Benchmark SSH port',runner.ssh_port||22,'number')+pick('ssh_config','Benchmark SSH config',runner.ssh_config||'','ssh'):''}${pick('config_path','Optional runner configuration',runner.config_path||'','json')}<p class="studio-note">A run installs the selected wheel on this runner, then sends the selected requests. Local runs use this machine. Docker needs a benchmark runner image; SSH needs Python and the benchmark dependencies on the remote host.</p><details><summary>Resources and environment</summary>${limits}${input('env','Additional environment variable names',runner.env.join(', '))}<p class="studio-note">Names only. Values stay in the configured environment. Docker forwards configured provider variables; SSH can use credentials already on the remote host.</p></details></fieldset>`;
      }
      if(step===2){
        panel.innerHTML=`<p class="studio-eyebrow">02 / EXPERIMENT MATRIX</p><h3>Select what to measure</h3><fieldset><legend>Providers and models</legend>${design.targets.map((t,i)=>`<div class="measured-target" data-target-row="${i}"><label class="studio-input">Provider ${i+1}<select data-target-provider="${i}">${catalogue.providers.map(p=>`<option value="${p.id}" ${p.id===t.provider?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label><label class="studio-input">Model ID ${i+1}<input data-target-model="${i}" list="measured-models-${i}" value="${esc(t.model)}" placeholder="Pinned model version"><datalist id="measured-models-${i}">${(models[t.provider]?.models||[]).map(m=>`<option value="${esc(m.id)}">${esc(m.name||m.id)}</option>`).join('')}</datalist></label>${t.provider==='azure'?`<label class="studio-input">Azure deployment ${i+1}<input data-target-request="${i}" value="${esc(t.request_model||'')}"></label>`:''}<button type="button" data-load-models="${i}">Load models for target ${i+1}</button>${design.targets.length>1?`<button type="button" data-remove-target="${i}">Remove target ${i+1}</button>`:''}<small>${esc(models[t.provider]?.message||catalogue.providers.find(p=>p.id===t.provider).model_hint)}</small></div>`).join('')}<button type="button" data-add-target>Add provider/model target</button></fieldset><fieldset><legend>Scenarios</legend><div class="studio-options">${choices(catalogue.scenarios,'scenarios')}</div></fieldset><fieldset><legend>Feature variants</legend><div class="studio-options">${choices(catalogue.features,'features')}</div><p class="studio-note">Each variant runs as a separate cell. Preflight checks CLI capabilities declared by the actual selected wheel; unsupported features cannot launch.</p></fieldset><fieldset><legend>Benchmark tools</legend><div class="studio-options">${choices(catalogue.tools,'tools')}</div><div class="studio-actions">${design.tools.filter(t=>t!=='native').map(t=>`<button type="button" data-prepare-runtime="${t}" ${active?'disabled':''}>Prepare ${esc(catalogue.tools.find(x=>x.id===t).name)} on ${esc(design.runner.transport)}</button>`).join('')}</div><p class="studio-note">Tool preparation installs pinned packages only after you choose Prepare. Docker preparation creates a new image; SSH preparation uses a managed remote environment. No provider requests are sent by setup.</p></fieldset><div class="studio-numbers">${input('repeats','Measured repeats',design.repeats,'number')}${input('seed','Measured seed',design.seed,'number')}</div><details><summary>Decision thresholds</summary><div class="studio-numbers">${input('minimum_savings','Minimum token savings (%)',design.policy.minimum_savings*100,'number')}${input('maximum_accuracy_regression','Maximum accuracy regression (%)',design.policy.maximum_accuracy_regression*100,'number')}${input('maximum_latency_ratio','Maximum latency ratio',design.policy.maximum_latency_ratio,'number')}${input('minimum_accuracy','Minimum success rate (%)',design.policy.minimum_accuracy*100,'number')}</div></details>`;
      }
      if(step===3)renderPreflight(panel);
      if(step===4)renderResults(panel);
    }
    if(step){
      const save=document.createElement('button');save.type='button';save.dataset.mSaveDesign='';save.textContent='Save experiment design';
      content.querySelector('.studio-designer > .studio-actions').append(save);
    }else{
      content.insertAdjacentHTML('beforeend',`<section aria-label="Saved experiment designs"><h3>Your experiment library</h3>${savedDesigns.length?savedDesigns.map(item=>`<article class="measured-history-item"><h4>${esc(item.design.label)}</h4><p>${item.design.scenarios.length} scenarios · ${item.design.targets.length} targets · ${item.design.repeats} repeats</p><button type="button" data-open-design="${esc(item.id)}">Open saved design ${esc(item.design.label)}</button><button type="button" data-remove-design="${esc(item.id)}">Remove saved design ${esc(item.design.label)}</button></article>`).join(''):'<p>Save a named design while editing an experiment to keep it here.</p>'}</section>`);
    }
    renderHistory();
  }
  function choices(items,key){return items.map(item=>`<label class="studio-choice"><input type="checkbox" aria-label="${esc(item.name)}" aria-describedby="measured-choice-${key}-${item.id}" data-m-choice="${key}" value="${item.id}" ${design[key].includes(item.id)?'checked':''} ${item.available===false?'disabled':''}><span><strong>${esc(item.name)}</strong><small id="measured-choice-${key}-${item.id}">${esc(item.description||item.reason||'Available on the selected runner')}</small></span></label>`).join('');}
  function capture(){
    if(!design)return;
    if(value('label')!==undefined)design.label=value('label');
    root.querySelectorAll('[data-m-flag]').forEach(input=>design[input.dataset.mFlag]=input.checked);
    for(const key of ['candidate','baseline']){
      const mode=root.querySelector(`[data-source-mode="${key}"]`)?.value;
      if(!mode)continue;
      design[key]=mode==='passthrough'?{kind:'passthrough'}:mode==='prepared'?{kind:'prepared',job_id:root.querySelector(`[data-source-job="${key}"]`)?.value||''}:{kind:'local',artifact:value(key+'-artifact')??(design[key].artifact||''),sha:value(key+'-sha')??(design[key].sha||'')};
    }
    if(value('transport')!==undefined){
      const runner=design.runner;runner.transport=value('transport');
      for(const key of ['config_path','image','host','ssh_config'])if(value(key)!==undefined)runner[key]=value(key);
      for(const key of ['ssh_port','cpus','memory_gib'])if(value(key)!==undefined)runner[key]=Number(value(key));
      runner.env=(value('env')||'').split(',').map(x=>x.trim()).filter(Boolean);
    }
    const targets=root.querySelectorAll('[data-target-row]');
    if(targets.length)design.targets=[...targets].map(row=>{
      const target={provider:row.querySelector('[data-target-provider]').value,model:row.querySelector('[data-target-model]').value};
      if(row.querySelector('[data-target-request]'))target.request_model=row.querySelector('[data-target-request]').value;
      return target;
    });
    for(const key of ['scenarios','features','tools']){
      const inputs=root.querySelectorAll(`[data-m-choice="${key}"]`);
      if(inputs.length)design[key]=[...inputs].filter(i=>i.checked).map(i=>i.value);
    }
    for(const key of ['repeats','seed'])if(value(key)!==undefined)design[key]=Number(value(key));
    for(const key of ['minimum_savings','maximum_accuracy_regression','maximum_latency_ratio','minimum_accuracy'])if(value(key)!==undefined)design.policy[key]=Number(value(key))/(key==='maximum_latency_ratio'?1:100);
  }
  function renderPreflight(panel){
    const cells=report?.cells||[];
    panel.innerHTML=`<p class="studio-eyebrow">03 / CHECK AND REVIEW</p><h3>Review every planned cell</h3><p class="studio-count">${cells.length} cells · ${report?.requests||0} provider requests</p><p class="studio-note">${report?.charges===false?'Offline fixture · no provider charges':esc(report?.notice||'Measured requests can incur provider charges.')}</p><div class="studio-matrix">${cells.map(c=>`<article>${recovering?`<label><input type="checkbox" data-m-retry-cell="${esc(c.id)}" ${retryCells.has(c.id)?'checked':''}> Retry ${esc(c.scenario)} / ${esc(c.model)}</label>`:''}<span>${esc(c.scenario)} · ${esc(c.feature)}</span><strong>${esc(c.provider)} / ${esc(c.model)}</strong><small>${esc(c.tool)} · ${design.repeats} baseline/candidate pairs</small></article>`).join('')}</div><h4>Pinned sources</h4><div class="measured-source-grid">${Object.entries(report?.resolved||{}).map(([key,source])=>`<article class="studio-source"><strong>${esc(key)}</strong><p><code>${esc(source.sha)}</code></p><small>${esc(source.provenance?.strategy||'local')}</small><p>Wheel <code>${esc(source.artifact_sha256)}</code></p></article>`).join('')}</div><h4>Preflight checks</h4><ul class="measured-checks">${(report?.checks||[]).map(c=>`<li data-ready="${c.ready}"><strong>${c.ready?'Ready':'Needs attention'}</strong> ${esc(c.description)}</li>`).join('')}</ul><p>Sources and configuration are reverified when the group is submitted. Cells run sequentially with a saved manifest and hashed evidence for each comparison.</p><button type="button" data-m-preflight>Recheck preflight</button>${recovering?recoveryActions():`<button type="button" data-m-launch ${!report?.ready||active||busy?'disabled':''}>Run measured experiment</button>`}`;
  }
  function renderHistory(){
    root.querySelector('[data-measured-history]').innerHTML=`<h3>Measured experiment history</h3>${groups.length?groups.map(g=>`<article class="measured-history-item"><div class="studio-section-title"><div><strong>${esc(g.label||g.id.slice(0,8))}</strong><small>${esc(g.group_state)} · ${esc(typeof g.created_at==='number'?new Date(g.created_at*1000).toLocaleString():g.created_at)}</small></div>${g.state==='completed'||g.state==='cancelled'?`<button type="button" data-open-group="${esc(g.id)}">Explore experiment ${esc(g.id.slice(0,8))}</button>`:!terminal.has(g.state)?`<button type="button" data-cancel-group="${esc(g.id)}">Cancel measured experiment</button>`:`<button type="button" data-recover-group="${esc(g.id)}">Review stopped experiment ${esc(g.id.slice(0,8))}</button>`}</div>${g.cells.length?`<div class="measured-cell-progress">${g.cells.map(c=>`<span data-cell-state="${esc(c.state)}">${esc(c.scenario)} · ${esc(c.provider)} · ${esc(c.feature)}: <strong>${esc(c.state)}</strong></span>`).join('')}</div>`:''}</article>`).join(''):'<p>No measured groups yet. Choose a template and preflight your sources to begin.</p>'}`;
    renderSetups();
  }
  function renderSetups(){
    root.querySelector('[data-tool-history]').innerHTML=toolSetups.length?`<h3>Tool runtime setup</h3>${toolSetups.map(job=>`<article class="measured-history-item"><strong>${esc(job.label||job.id.slice(0,8))}</strong><p>${esc(job.state)}${job.setup?.available?' · pinned runtime ready':''}</p>${job.setup?.reason?`<p class="studio-note">${esc(job.setup.reason)}</p>`:''}${job.setup?.available&&job.setup.image_identity?`<p>Prepared image <code>${esc(job.setup.image_identity)}</code></p><button type="button" data-use-tool-image="${esc(job.id)}">Use prepared Docker runner</button>`:''}${job.collectable&&terminal.has(job.state)?`<button type="button" data-collect-tool="${esc(job.id)}">Collect existing tool setup</button>`:''}${!terminal.has(job.state)?`<button type="button" data-cancel-group="${esc(job.id)}">Cancel tool setup</button>`:''}</article>`).join('')}`:'';
  }
  function samples(cell,arm,key){
    const arms=cell.evidence?.schema_version===2?['baseline','candidate']:['a1_passthrough','b_headroom'];
    return (cell.evidence?.attempts||[]).filter(a=>a.arm===arms[arm]&&Number.isFinite(a[key])).map(a=>a[key]).sort((a,b)=>a-b);
  }
  function average(values){return values.length?values.reduce((a,b)=>a+b,0)/values.length:null;}
  function number(value){return Number.isFinite(value)?value.toFixed(1):'—';}
  function distributions(cells){
    return cells.map(cell=>{
      const summaries=[];
      const plots=[['tokens','Input token'],['latency_ms','Latency (ms)']].map(([key,label])=>{
        const arms=[samples(cell,0,key),samples(cell,1,key)];
        if(arms.some(values=>values.length<2))return `<p>${esc(label)} distribution needs at least two measurements in each arm.</p>`;
        const minimum=Math.min(...arms.flat()),maximum=Math.max(...arms.flat());
        const x=value=>100+(value-minimum)/(maximum-minimum||1)*350;
        const quantile=(values,p)=>{const index=(values.length-1)*p,low=Math.floor(index);return values[low]+(values[Math.ceil(index)]-values[low])*(index-low);};
        arms.forEach((values,i)=>summaries.push(`<tr><th>${esc(label)}</th><td>${i?'Candidate':'Baseline'}</td><td>${values.length}</td>${[values[0],quantile(values,.25),quantile(values,.5),quantile(values,.75),values.at(-1)].map(v=>`<td>${number(v)}</td>`).join('')}</tr>`));
        const heading=`<h5>${esc(label)} samples</h5>`;
        return heading+`<svg class="measured-distribution" viewBox="0 0 480 110" role="img" aria-label="${esc(label)} sample distribution: ${esc(cell.scenario)}"><line x1="100" x2="450" y1="82" y2="82" stroke="currentColor"/><text x="100" y="102">${number(minimum)}</text><text x="450" y="102" text-anchor="end">${number(maximum)}</text>${arms.map((values,i)=>`<text x="0" y="${29+i*34}">${i?'Candidate':'Baseline'}</text>${values.map((v,j)=>`<circle cx="${x(v)}" cy="${24+i*34+(j%3-1)*4}" r="3" class="${i?'measured-dot-candidate':'measured-dot-baseline'}"><title>${i?'Candidate':'Baseline'}: ${number(v)}</title></circle>`).join('')}`).join('')}</svg>`;
      }).join('');
      return `<section class="measured-distributions"><h4>Sample distributions</h4><p>${esc(cell.scenario)} · ${esc(cell.provider)} / ${esc(cell.model)} · ${esc(cell.tool)} · ${esc(cell.feature)}</p>${plots}${summaries.length?`<div class="studio-table"><table aria-label="Sample distribution summary"><thead><tr><th>Metric</th><th>Arm</th><th>Samples</th><th>Min</th><th>25%</th><th>Median</th><th>75%</th><th>Max</th></tr></thead><tbody>${summaries.join('')}</tbody></table></div>`:''}</section>`;
    }).join('');
  }
  function comparisonView(cells){
    const options=groups.filter(g=>g.id!==selected&&['completed','cancelled'].includes(g.state));
    const selector=`<label class="studio-input">Compare with saved experiment<select data-m-compare aria-label="Compare with saved experiment"><option value="">Choose a saved run</option>${options.map(g=>`<option value="${esc(g.id)}" ${comparisonId===g.id?'selected':''}>${esc(g.label||g.id.slice(0,8))}</option>`).join('')}</select></label>`;
    if(!comparison)return `<section><h4>Compare saved experiments</h4>${selector}</section>`;
    const signature=c=>JSON.stringify([c.scenario,c.provider,c.model,c.tool,c.feature]);
    const settings=run=>Object.fromEntries(Object.entries(run.design||{}).filter(([key])=>key!=='label'));
    const differences=result.lane!==comparison.lane||JSON.stringify(ordered(settings(result)))!==JSON.stringify(ordered(settings(comparison)));
    const rows=cells.map(cell=>{
      const peer=comparison.cells.find(c=>signature(c)===signature(cell));
      const current=average(samples(cell,1,'tokens')),previous=peer?average(samples(peer,1,'tokens')):null;
      const latency=average(samples(cell,1,'latency_ms')),oldLatency=peer?average(samples(peer,1,'latency_ms')):null;
      return `<tr><th>${esc(cell.scenario)} / ${esc(cell.provider)} / ${esc(cell.model)} / ${esc(cell.tool)} / ${esc(cell.feature)}</th><td>${number(previous)}</td><td>${number(current)}</td><td>${number(current!==null&&previous!==null?current-previous:null)}</td><td>${number(latency!==null&&oldLatency!==null?latency-oldLatency:null)}</td><td>${peer?esc(peer.state):'No matching cell'}</td></tr>`;
    }).join('');
    const identity=run=>Object.entries(run.sources||{}).map(([arm,source])=>`${arm}: ${source.sha||'unavailable'}`).join('; ');
    return `<section><h4>Compare saved experiments</h4>${selector}<p>Current: ${esc(result.experiment_id)} (${esc(result.lane)}) · ${esc(identity(result))}</p><p>Saved: ${esc(comparison.experiment_id)} (${esc(comparison.lane)}) · ${esc(identity(comparison))}</p><p>${differences?'Run settings differ. These changes are descriptive and do not establish a regression.':'Descriptive comparison of matching cells; qualification remains separate for each experiment.'}</p><div class="studio-table"><table aria-label="Saved experiment comparison"><thead><tr><th>Matching cell</th><th>Saved candidate tokens</th><th>Current candidate tokens</th><th>Token change</th><th>Latency change (ms)</th><th>Saved state</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
  }
  function renderResults(panel){
    if(!result){panel.innerHTML='<h3>Experiment is running</h3><p>Follow cell progress below. Drafts and jobs survive reloads; cancellation keeps available evidence.</p>';return;}
    const keys=['scenario','provider','model','tool','feature'];
    const displayed=result.cells.filter(c=>keys.every(k=>!filters[k]||c[k]===filters[k]));
    const rows=displayed.map(c=>{
      const attempts=c.evidence?.attempts||[];
      const mean=(arm,key)=>{const samples=attempts.filter(a=>a.arm===arm&&Number.isFinite(a[key])).map(a=>a[key]);return samples.length?samples.reduce((a,b)=>a+b,0)/samples.length:null;};
      const arms=c.evidence?.schema_version===2?['baseline','candidate']:['a1_passthrough','b_headroom'];
      const baseline=mean(arms[0],'tokens'),candidate=mean(arms[1],'tokens');
      const decision=c.evidence?.decision?.simulated_decision||c.evidence?.decision||{};
      return {...c,baseline,candidate,baselineMs:mean(arms[0],'latency_ms'),candidateMs:mean(arms[1],'latency_ms'),savings:baseline>0&&candidate!==null?(1-candidate/baseline)*100:null,stats:decision,attempts};
    });
    const n=value=>Number.isFinite(value)?value.toFixed(1):'—';
    const interval=values=>Array.isArray(values)&&values.length===2?values.map(x=>n(x*100)+'%').join(' to '):'Insufficient evidence';
    const maximum=Math.max(1,...rows.flatMap(r=>[r.baseline||0,r.candidate||0]));
    panel.innerHTML=`<p class="studio-eyebrow">04 / EXPLORE</p><h3>Experiment results</h3><h4>${esc(result.experiment_id)}</h4><div class="studio-result-summary"><strong>Qualification: ${esc(qualification?.status||'inconclusive')}</strong><span>${result.cells.filter(c=>c.state==='completed').length} / ${result.cells.length} cells completed</span><span>${esc(result.lane)}</span></div>${result.lane==='simulator'?'<p class="studio-note">At least one selected build is a simulator. These observations do not establish live provider performance.</p>':''}<div class="measured-filters">${keys.map(key=>`<label>${esc(key)}<select data-m-filter="${key}"><option value="">All</option>${[...new Set(result.cells.map(c=>c[key]))].map(v=>`<option ${filters[key]===v?'selected':''}>${esc(v)}</option>`).join('')}</select></label>`).join('')}</div><div class="studio-chart" role="img" aria-label="Measured baseline and candidate input tokens">${rows.map(r=>`<div class="studio-chart-row"><strong>${esc(r.scenario)} · ${esc(r.provider)} / ${esc(r.model)} · ${esc(r.feature)} · ${esc(r.tool)}</strong><div class="studio-bar-line"><svg viewBox="0 0 510 12" aria-hidden="true"><rect class="studio-bar-baseline" width="${(r.baseline||0)/maximum*510}" height="12"/></svg><span>${n(r.baseline)}</span></div><div class="studio-bar-line"><svg viewBox="0 0 510 12" aria-hidden="true"><rect class="studio-bar-candidate" width="${(r.candidate||0)/maximum*510}" height="12"/></svg><span>${n(r.candidate)}</span></div></div>`).join('')}</div><div class="studio-table"><table aria-label="Experiment cell metrics"><thead><tr><th>Select for retry</th><th>Scenario / provider / model</th><th>Tool / feature</th><th>Baseline tokens</th><th>Candidate tokens</th><th>Savings</th><th>Baseline ms</th><th>Candidate ms</th><th>State</th></tr></thead><tbody>${rows.map(r=>`<tr><td><input type="checkbox" data-m-retry-cell="${esc(r.id)}" aria-label="Select ${esc(r.scenario)} ${esc(r.provider)} ${esc(r.model)} ${esc(r.feature)} for retry" ${retryCells.has(r.id)?'checked':''}></td><th>${esc(r.scenario)} / ${esc(r.provider)} / ${esc(r.model)}</th><td>${esc(r.tool)} / ${esc(r.feature)}</td><td>${n(r.baseline)}</td><td>${n(r.candidate)}</td><td>${n(r.savings)}%</td><td>${n(r.baselineMs)}</td><td>${n(r.candidateMs)}</td><td>${esc(r.state)} · ${esc(r.decision?.status||'inconclusive')}</td></tr>`).join('')}</tbody></table></div>${rows.map(r=>`<details><summary>Samples, uncertainty and evidence</summary><p>${esc(r.scenario)} · ${esc(r.provider)} / ${esc(r.model)} · ${esc(r.tool)} · ${esc(r.feature)}</p><p>${esc(r.stats.pairs||0)} pairs · token savings 95% interval: ${esc(interval(r.stats.savings_interval))}</p>${r.error?`<p>${esc(r.error)}</p>`:''}<p>${esc(r.evidence?.decision?.reason||'')}</p>${r.evidence?.identity_verification?.mismatches?.length?`<p>Identity checks need attention: ${esc(r.evidence.identity_verification.mismatches.join(', '))}</p>`:''}<div class="studio-table"><table aria-label="Paired request samples"><thead><tr><th>Pair</th><th>Arm</th><th>Tokens</th><th>Latency ms</th><th>Success</th></tr></thead><tbody>${r.attempts.map(a=>`<tr><td>${esc(a.pair_id)}</td><td>${esc(a.arm)}</td><td>${n(a.tokens)}</td><td>${n(a.latency_ms)}</td><td>${esc(a.success)}</td></tr>`).join('')}</tbody></table></div><p>Manifest <code>${esc(r.manifest_digest||'Unavailable')}</code></p><p>Evidence <code>${esc(r.evidence_sha256||'Unavailable')}</code></p></details>`).join('')}${recoveryActions()}<div class="studio-actions"><button type="button" data-m-export>Export experiment evidence</button><button type="button" data-m-duplicate>Edit experiment copy</button></div>`;
    panel.insertAdjacentHTML('beforeend',distributions(displayed)+comparisonView(displayed));
  }
  async function showGroup(id){
    comparison=null;comparisonId='';++comparisonVersion;
    const data=await request('/api/benchmarks/measured?group='+encodeURIComponent(id));selected=id;result=data.result;qualification=data.qualification;design=structuredClone(result.design);recovering=null;retryCells.clear();
    result.cells.filter(c=>c.state!=='completed'||c.decision?.status==='inconclusive').forEach(c=>retryCells.add(c.id));step=4;render();
  }
  async function refresh(){
    if(!visible||loading)return;loading=true;
    try{
      const state=await request('/api/benchmarks/measured');catalogue=state.catalogue;draft=state.draft;sources=state.sources;groups=state.groups;active=state.active;toolSetups=state.tool_setups||[];
      savedDesigns=state.designs||[];
      if(!initialized){initialized=true;design=structuredClone(draft||catalogue.templates[0].design);render();}else renderHistory();
      const unfinished=pending();const accepted=unfinished&&groups.find(g=>g.client_request_id===unfinished.id);
      if(accepted&&(restorePending||watchPending)){selected=accepted.id;result=null;step=4;render();clearPending();status('Recovered the existing launch. No requests were replayed.');}
      restorePending=false;
      root.querySelectorAll('[data-m-launch]').forEach(b=>b.disabled=!report?.ready||active||busy);
      const watched=groups.find(g=>g.id===selected);
      if(watched?.state==='completed'&&!result&&step===4){await showGroup(selected);status('Experiment finished. Explore verified cell evidence.');}
      else if(watched&&terminal.has(watched.state)&&watched.state!=='completed')status('Group process '+watched.state+'. Available evidence and progress are retained.');
    }catch(error){status(error.message);}finally{loading=false;}
  }
  root.addEventListener('change',event=>{
    if(event.target.hasAttribute('data-m-compare')){
      const id=event.target.value,version=++comparisonVersion,group=selected;
      comparison=null;comparisonId=id;
      if(!id){render();return;}
      request('/api/benchmarks/measured?group='+encodeURIComponent(id)).then(data=>{
        if(version!==comparisonVersion||group!==selected||step!==4)return;
        comparison=data.result;render();
      }).catch(error=>{if(version===comparisonVersion)status(error.message);});
      return;
    }
    if(event.target.dataset.mRetryCell){
      if(event.target.checked)retryCells.add(event.target.dataset.mRetryCell);else retryCells.delete(event.target.dataset.mRetryCell);
      root.querySelectorAll('[data-retry-count]').forEach(node=>node.textContent=String(retryCells.size));
      root.querySelectorAll('[data-m-retry],[data-m-collect]').forEach(button=>button.disabled=!retryCells.size||active||busy);
      return;
    }
    if(event.target.dataset.mChoice==='tools'){capture();const chosen=event.target.value;render();root.querySelector(`[data-m-choice="tools"][value="${chosen}"]`)?.focus({preventScroll:true});return;}
    if(event.target.dataset.mFilter){filters[event.target.dataset.mFilter]=event.target.value;render();return;}
    if(event.target.hasAttribute('data-source-mode')||event.target.dataset.mSetting==='transport'){const before=design.baseline.kind;capture();if(event.target.dataset.sourceMode==='baseline'){design.baseline_optimization=design.baseline.kind!=='passthrough';if(before==='passthrough'||design.baseline.kind==='passthrough')design.candidate_optimization=true;}render();}
    if(event.target.hasAttribute('data-target-provider')){capture();const i=Number(event.target.dataset.targetProvider);design.targets[i].model='';delete design.targets[i].request_model;render();}
  });
  root.addEventListener('click',async event=>{
    const button=event.target.closest('button');if(!button)return;
    try{
      if(button.dataset.mTemplate){++navigationVersion;watchPending=false;design=structuredClone(catalogue.templates.find(t=>t.id===button.dataset.mTemplate).design);selected=null;recovering=null;result=null;report=null;step=1;render();}
      else if(button.hasAttribute('data-m-continue')){++navigationVersion;watchPending=false;design=structuredClone(draft);selected=null;recovering=null;result=null;report=null;step=1;render();}
      else if(button.hasAttribute('data-m-home')){++navigationVersion;watchPending=false;capture();step=0;render();}
      else if(button.hasAttribute('data-m-refresh'))await refresh();
      else if(button.hasAttribute('data-m-find-source')){const finder=host.querySelector('.studio-source-finder');finder.open=true;finder.scrollIntoView({block:'start'});finder.querySelector('[name=source-query]').focus();}
      else if(button.hasAttribute('data-m-save')){capture();await request('/api/benchmarks/measured/draft',design);draft=structuredClone(design);status('Measured draft saved.');}
      else if(button.hasAttribute('data-m-save-design')){
        capture();const data=await request('/api/benchmarks/measured/designs',{action:'save',design});
        savedDesigns=data.designs;status('Experiment design saved to your library.');
      }
      else if(button.dataset.openDesign){
        const saved=savedDesigns.find(item=>item.id===button.dataset.openDesign);
        if(!saved)throw Error('Saved design is unavailable. Refresh the library.');
        ++navigationVersion;watchPending=false;design=structuredClone(saved.design);selected=null;recovering=null;result=null;report=null;step=1;render();
      }
      else if(button.dataset.removeDesign){
        const data=await request('/api/benchmarks/measured/designs',{action:'remove',id:button.dataset.removeDesign});
        savedDesigns=data.designs;render();status('Saved design removed from your library.');
      }
      else if(button.hasAttribute('data-m-next')||button.hasAttribute('data-m-back')||button.dataset.mStep){
        capture();await request('/api/benchmarks/measured/preview',design);await request('/api/benchmarks/measured/draft',design);draft=structuredClone(design);
        const next=button.dataset.mStep?Number(button.dataset.mStep):step+(button.hasAttribute('data-m-next')?1:-1);
        if(next===3)report=await request('/api/benchmarks/measured/preflight',design);
        step=next;render();root.querySelector('[data-measured-step]').focus();status('');
      }
      else if(button.hasAttribute('data-add-target')){capture();if(design.targets.length>=24)throw Error('At most 24 targets are supported.');design.targets.push({provider:'openai',model:''});render();}
      else if(button.hasAttribute('data-remove-target')){capture();design.targets.splice(Number(button.dataset.removeTarget),1);render();}
      else if(button.hasAttribute('data-load-models')){
        capture();const index=Number(button.dataset.loadModels),provider=design.targets[index].provider;button.disabled=true;status('Loading available '+provider+' models…');
        const data=await request('/api/benchmarks/measured/models',{provider});models[provider]=data;
        if(design.targets[index]?.provider===provider){if(!design.targets[index].model&&data.models.length===1){design.targets[index].model=data.models[0].id;if(data.models[0].request_model)design.targets[index].request_model=data.models[0].request_model;}render();}
        status(data.message);
      }
      else if(button.hasAttribute('data-m-preflight')){capture();report=await request('/api/benchmarks/measured/preflight',design);render();}
      else if(button.dataset.prepareRuntime){
        if(busy||active)return;capture();busy=true;button.disabled=true;
        try{
          await request('/api/benchmarks/measured/draft',design);draft=structuredClone(design);
          await request('/api/benchmarks',{action:'tool',tool:button.dataset.prepareRuntime,runner:design.runner});
          status('Tool preparation submitted. Follow its durable setup job below, then recheck preflight.');await refresh();
        }finally{busy=false;}
      }
      else if(button.dataset.collectTool){
        if(busy||active)return;busy=true;button.disabled=true;
        try{await request('/api/benchmarks',{action:'tool',recover:button.dataset.collectTool});status('Collecting the existing setup. No installation was restarted.');await refresh();}finally{busy=false;}
      }
      else if(button.dataset.useToolImage){
        capture();const setup=toolSetups.find(job=>job.id===button.dataset.useToolImage)?.setup;
        if(!setup?.available||!setup.image_identity)throw Error('Prepared runner image is unavailable. Refresh setup status.');
        design.runner.transport='docker';design.runner.image=setup.image_identity;report=null;
        await request('/api/benchmarks/measured/draft',design);draft=structuredClone(design);render();
        status('Prepared Docker runner selected. Recheck preflight before launching requests.');
      }
      else if(button.hasAttribute('data-m-retry')||button.hasAttribute('data-m-collect')){
        if(busy||active||!retryCells.size)return;busy=true;button.disabled=true;
        const version=++navigationVersion;
        try{
          const mode=button.hasAttribute('data-m-collect')?'collect':'retry';
          const job=await submit({action:'group',recover:{id:recovering||selected,mode,cells:[...retryCells]}});
          if(version!==navigationVersion)return;
          selected=job.id;recovering=null;result=null;qualification=null;step=4;active=true;render();status('Recovery submitted. Verified cells are preserved; existing remote work will not be replayed.');await refresh();
        }finally{busy=false;}
      }
      else if(button.dataset.recoverGroup){
        const previous=await request('/api/benchmarks/measured?recover='+encodeURIComponent(button.dataset.recoverGroup));
        design=previous.design;recovering=previous.id;selected=previous.id;retryCells.clear();
        previous.cells.filter(c=>c.state!=='completed').forEach(c=>retryCells.add(c.id));
        if(!retryCells.size)previous.cells.forEach(c=>retryCells.add(c.id));
        report=await request('/api/benchmarks/measured/preview',design);
        report.checks=[{ready:true,description:'Saved configuration loaded. Select cells to retry or collect. Recovery rechecks ownership and immutable evidence before starting.'}];
        step=3;render();
      }
      else if(button.hasAttribute('data-m-launch')){
        if(busy||active)return;busy=true;button.disabled=true;
        const version=++navigationVersion;
        try{const job=await submit({action:'group',design});if(version!==navigationVersion)return;selected=job.id;result=null;qualification=null;step=4;active=true;render();status('Group submitted. Following cell progress.');await refresh();}finally{busy=false;}
      }
      else if(button.dataset.openGroup)await showGroup(button.dataset.openGroup);
      else if(button.dataset.cancelGroup){await request('/api/benchmarks/cancel',{id:button.dataset.cancelGroup});status('Cancellation requested. Keeping available evidence.');await refresh();}
      else if(button.hasAttribute('data-m-duplicate')){design=structuredClone(result.design);design.label+=' (copy)';selected=null;recovering=null;result=null;report=null;step=1;render();}
      else if(button.hasAttribute('data-m-export')){const url=URL.createObjectURL(new Blob([JSON.stringify({result,qualification},null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='experiment-'+selected+'.json';a.click();URL.revokeObjectURL(url);}
    }catch(error){status(error.message);}
  });
  document.addEventListener('agentops-view',event=>{visible=event.detail?.view==='benchmarks';if(visible)refresh();});
  document.addEventListener('click',event=>{if(event.target.closest('[data-open-measured]'))root.scrollIntoView({block:'start'});});
  setInterval(()=>{if(visible&&!document.hidden)refresh();},2000);if(visible)refresh();
})();
