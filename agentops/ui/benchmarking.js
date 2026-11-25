/* Benchmark jobs are explicit actions; opening this view only reads saved state. */
(() => {
  const root = document.getElementById('benchmark-view');
  if (!root) return;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let jobs = [], visible = false, loading = false, initialized = false;
  const pathKinds = {artifact:'wheel',config:'json',inspection_path:'json',tasks:'json',policy:'json',manifest:'json',dataset:'dataset',ssh_config:'ssh'};
  const field = (name, label, options = {}) => `<div class="benchmark-field"><label>${label}<input name="${name}" ${pathKinds[name] ? 'readonly' : ''} ${options.required ? 'required' : ''} ${options.type ? `type="${options.type}"` : ''} ${options.value !== undefined ? `value="${esc(options.value)}"` : ''} ${options.min !== undefined ? `min="${options.min}"` : ''}></label>${pathKinds[name] ? `<button type="button" data-browse="${name}" data-kind="${pathKinds[name]}" aria-label="Browse ${esc(label)}">Browse</button>` : ''}</div>`;
  const transport = () => `<label>Transport<select name="transport" aria-label="Transport"><option value="local">Local</option><option value="docker">Docker</option><option value="ssh">SSH</option></select></label><div data-transport="docker" hidden>${field('image','Docker image')}</div><div data-transport="ssh" hidden>${field('host','SSH host')}${field('ssh_port','SSH port',{type:'number',value:22,min:1})}${field('ssh_config','SSH config path (optional)')}</div>`;
  const form = (action, title, content, button, hint = '') => `<form data-action="${action}" class="benchmark-form"><h3>${title}</h3>${hint ? `<p class="muted">${hint}</p>` : ''}<div class="benchmark-fields">${content}</div><button type="submit">${button}</button><p class="benchmark-message" role="status"></p></form>`;
  root.innerHTML = `<div class="benchmark-heading"><div><h2 id="benchmark-title">Benchmarking</h2><p class="muted">Compare a candidate with its baseline using paired tasks and a saved experiment.</p></div><button type="button" id="benchmark-refresh">Refresh jobs</button></div><p id="benchmark-status" role="status">Open Benchmarking to load saved jobs.</p><div class="benchmark-workflow">${form('inspect','1. Inspect candidate',field('artifact','Candidate artifact path',{required:true})+field('config','Config path (optional)')+transport(),'Inspect candidate')}${form('create','2. Create experiment',`<label>Completed inspection<select aria-label="Completed inspection" name="inspection_id" data-job-options="inspect"><option value="">Use inspection path below</option></select></label>`+field('inspection_path','Inspection path (if no saved inspection)')+field('candidate_sha','Candidate Git SHA',{required:true})+field('model','Model',{required:true})+field('tasks','Taskset path',{required:true})+field('policy','Policy path',{required:true})+field('experiment_id','Experiment label',{required:true})+field('seed','Seed',{type:'number',value:42})+field('repeats','Repeats',{type:'number',value:5,min:1}),'Create experiment')}${form('run','3. Run experiment',`<label>Saved experiment<select aria-label="Saved experiment" name="experiment_id" data-job-options="create"><option value="">Use manifest path below</option></select></label>`+field('manifest','Manifest path (if no saved experiment)')+field('artifact','Candidate artifact path',{required:true})+field('config','Config path (optional)')+transport(),'Run experiment','Running may send requests to the model provider configured for this experiment and incur usage charges. Results distinguish simulator runs from measured provider runs.')}</div><details class="benchmark-datasets"><summary>Dataset import and export</summary><div class="benchmark-dataset-forms">${form('export','Export taskset',field('tasks','Taskset path',{required:true})+'<label>Format<select name="format"><option value="inspect">Inspect</option><option value="aiperf">AIPerf</option></select></label>','Export dataset')}${form('import','Import dataset',field('dataset','Dataset path',{required:true})+field('taskset_id','Taskset ID',{required:true})+field('revision','Revision',{required:true}),'Import dataset')}</div></details><section aria-labelledby="benchmark-jobs-title"><h3 id="benchmark-jobs-title">Jobs and results</h3><div id="benchmark-jobs"></div></section>`;

  // Guidance stays local to this browser and never submits benchmark work.
  const helpCopy = {
    transport: 'Local runs on this machine; Docker uses a container; SSH uses a remote machine. Choose a runner with access to your files and provider configuration.',
    artifact: 'Choose the candidate Python wheel (.whl) to compare with the baseline. Browse files on the machine running AgentOps.',
    config: 'Optional configuration file for the candidate and model provider. Paths are on the AgentOps host.',
    inspection_path: 'Use the inspection.json produced by Inspect candidate, or choose a completed inspection above.',
    candidate_sha: 'Full Git commit SHA of the candidate build, so results can be traced to the exact source.',
    model: 'Provider model identifier available to your benchmark configuration. Provider credentials must already be configured on the runner.',
    tasks: 'Taskset JSON containing the requests to replay for both baseline and candidate. Use the same tasks for a fair comparison.',
    policy: 'Policy JSON defining the checks and thresholds used to decide whether the candidate passes.',
    experiment_id: 'A descriptive label for this comparison; saved experiments can be selected in Run experiment.',
    seed: 'Randomizes the order of task pairs and baseline/candidate execution. Keep this value fixed when comparing runs.',
    repeats: 'Number of repetitions per comparison. More repetitions improve confidence and take longer.',
    manifest: 'Use manifest.json from Create experiment, or select a saved experiment above.',
    image: 'Docker image available to the runner, with the benchmark dependencies installed.',
    host: 'SSH destination for the benchmark runner (for example user@host). The runner needs access to the benchmark files and credentials.',
    ssh_port: 'SSH port on the remote runner; normally 22.',
    ssh_config: 'Optional SSH configuration file for host aliases, keys and connection settings.',
    dataset: 'Dataset file to convert into a taskset. Import and export prepare data; they do not run a comparison.',
    taskset_id: 'Stable name for the imported taskset.',
    revision: 'Dataset revision to record for reproducibility.'
  };
  const controls = document.createElement('div');
  controls.className = 'benchmark-help-controls';
  controls.innerHTML = `<button type="button" data-guide-open aria-label="Benchmark help and guided tutorial"><span aria-hidden="true">?</span> Guided setup</button><label><input type="checkbox" data-help-toggle> Show help</label>`;
  root.querySelector('.benchmark-heading').append(controls);
  let helpEnabled = true;
  try { helpEnabled = localStorage.getItem('agentops.benchmark.help') !== 'off'; } catch {}
  const helpToggle = controls.querySelector('[data-help-toggle]');
  helpToggle.checked = helpEnabled;
  root.querySelectorAll('input[name], select[name="transport"]').forEach((input, index) => {
    if (!helpCopy[input.name]) return;
    const hint = document.createElement('small');
    hint.id = `benchmark-help-${index}`;
    hint.className = 'benchmark-field-help';
    hint.textContent = helpCopy[input.name];
    hint.hidden = !helpEnabled;
    input.setAttribute('aria-describedby', hint.id);
    (input.closest('.benchmark-field') || input.parentElement).append(hint);
  });
  helpToggle.addEventListener('change', () => {
    root.querySelectorAll('.benchmark-field-help').forEach(hint => { hint.hidden = !helpToggle.checked; });
    try { localStorage.setItem('agentops.benchmark.help', helpToggle.checked ? 'on' : 'off'); } catch {}
  });
  const steps = [
    ['Prepare your comparison', 'Compare a candidate build with a baseline using the same requests. Have a candidate wheel (.whl), taskset JSON and policy JSON ready. Files must be accessible to the selected runner. Start with Local if the dependencies and provider credentials are configured on this machine.', 'inspect'],
    ['Inspect the candidate', 'Choose the candidate wheel and transport, then select Inspect candidate. Wait for its job to complete. Inspection produces the candidate details needed to create an experiment; it does not run the comparison.', 'inspect'],
    ['Create a repeatable experiment', 'Select the completed inspection. Enter the candidate build’s full Git SHA and model identifier, then choose your taskset and policy files. Keep the seed fixed for repeatability. Create experiment saves a manifest describing the comparison.', 'create'],
    ['Run the comparison', 'Select the saved experiment and the same candidate wheel. Choose where to run it, then select Run experiment. This can send requests to the configured model provider and incur usage charges. Jobs below show progress; you can cancel active work.', 'run'],
    ['Read the results', 'After the run completes, read its decision and metrics in Benchmark jobs. A completed job alone does not mean the candidate passed: check the decision and any errors. Simulator results are labeled and do not establish measured provider performance. Copy saved artifact paths to examine the evidence or reuse the experiment.', null]
  ];
  const guide = document.createElement('dialog');
  guide.className = 'benchmark-guide';
  guide.setAttribute('aria-labelledby', 'benchmark-guide-title');
  guide.innerHTML = `<div class="benchmark-heading"><h3 id="benchmark-guide-title">Benchmark guide</h3><button type="button" data-guide-close aria-label="Close benchmark guide">Close</button></div><p data-guide-progress class="muted"></p><div aria-live="polite"><h4 data-guide-heading></h4><p data-guide-copy></p></div><div class="benchmark-guide-actions"><button type="button" data-guide-back>Back</button><button type="button" data-guide-go>Go to this step</button><button type="button" data-guide-next>Next</button></div>`;
  document.body.append(guide);
  let guideStep = 0;
  function renderGuide() {
    guide.querySelector('[data-guide-progress]').textContent = `Step ${guideStep + 1} of ${steps.length}`;
    guide.querySelector('[data-guide-heading]').textContent = steps[guideStep][0];
    guide.querySelector('[data-guide-copy]').textContent = steps[guideStep][1];
    guide.querySelector('[data-guide-back]').disabled = guideStep === 0;
    guide.querySelector('[data-guide-next]').textContent = guideStep === steps.length - 1 ? 'Finish' : 'Next';
  }
  controls.querySelector('[data-guide-open]').addEventListener('click', () => { guideStep = 0; renderGuide(); guide.showModal(); });
  guide.querySelector('[data-guide-close]').addEventListener('click', () => guide.close());
  guide.querySelector('[data-guide-back]').addEventListener('click', () => { if (guideStep > 0) guideStep--; renderGuide(); });
  guide.querySelector('[data-guide-next]').addEventListener('click', () => { if (guideStep === steps.length - 1) guide.close(); else { guideStep++; renderGuide(); } });
  guide.querySelector('[data-guide-go]').addEventListener('click', () => {
    const action = steps[guideStep][2];
    guide.close();
    const target = action ? root.querySelector(`form[data-action="${action}"]`) : root.querySelector('#benchmark-jobs');
    target.scrollIntoView({block: 'center'});
    const focusTarget = target.querySelector('input, select, button') || target;
    if (focusTarget === target) target.setAttribute('tabindex', '-1');
    focusTarget.focus({preventScroll: true});
  });

  const status = root.querySelector('#benchmark-status');
  const picker = document.createElement('dialog');
  picker.className = 'benchmark-picker';
  picker.setAttribute('aria-labelledby','benchmark-picker-title');
  picker.innerHTML = '<div class="benchmark-heading"><h3 id="benchmark-picker-title">Choose file</h3><button type="button" data-picker-close>Close</button></div><p id="benchmark-picker-status" role="status"></p><nav class="benchmark-picker-roots" aria-label="Filesystem roots"></nav><div class="benchmark-picker-location"><button type="button" data-picker-up>Up one folder</button><code id="benchmark-picker-directory"></code></div><div class="benchmark-picker-entries"></div>';
  document.body.append(picker);
  let pickerInput, pickerTrigger, pickerKind, pickerParent, pickerDirectory, pickerBusy = false;
  const pickerStatus = picker.querySelector('#benchmark-picker-status');
  async function browse(path = '') {
    if (pickerBusy) return;
    pickerBusy = true;
    pickerStatus.textContent = 'Loading folder...';
    picker.querySelector('.benchmark-picker-entries').replaceChildren();
    try {
      const auth = await fetch('/api/launcher');
      if (!auth.ok) throw Error('Could not open the file browser. Refresh and try again.');
      const token = await auth.json();
      const response = await fetch(`/api/benchmark-files?path=${encodeURIComponent(path)}&kind=${encodeURIComponent(pickerKind)}`,{headers:{'X-Launch-Token':token.token || token.launch_token || ''}});
      const data = await response.json();
      if (!response.ok) throw Error(data.error || 'Could not read this folder.');
      pickerParent = data.parent;
      pickerDirectory = data.directory;
      picker.querySelector('#benchmark-picker-directory').textContent = data.directory;
      picker.querySelector('[data-picker-up]').disabled = !data.parent;
      picker.querySelector('.benchmark-picker-roots').innerHTML = (data.roots || []).map(item => `<button type="button" data-picker-folder="${esc(item.path)}">${esc(item.name)}</button>`).join('');
      picker.querySelector('.benchmark-picker-entries').innerHTML = (data.entries || []).map(item => `<button type="button" ${item.directory ? `data-picker-folder="${esc(item.path)}"` : `data-picker-file="${esc(item.path)}"`}>${item.directory ? 'Open folder: ' : 'Select file: '}${esc(item.name)}</button>`).join('');
      pickerStatus.textContent = data.entries?.length ? 'Open a folder or select a file.' : 'No matching files in this folder. Choose another folder.';
    } catch (error) { pickerStatus.textContent = error.message; }
    finally { pickerBusy = false; }
  }
  picker.addEventListener('close',() => pickerTrigger?.focus());
  picker.addEventListener('click', event => {
    const button = event.target.closest('button'); if (!button) return;
    if (button.hasAttribute('data-picker-close')) picker.close();
    else if (button.hasAttribute('data-picker-up') && pickerParent) browse(pickerParent);
    else if (button.dataset.pickerFolder !== undefined) browse(button.dataset.pickerFolder);
    else if (button.dataset.pickerFile !== undefined && pickerInput) {
      pickerInput.value = button.dataset.pickerFile;
      pickerInput.dispatchEvent(new Event('change',{bubbles:true}));
      picker.close();
    }
  });
  async function request(url, payload) {
    const options = payload ? {method:'POST', headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)} : {};
    if (payload) {
      const auth = await fetch('/api/launcher');
      if (!auth.ok) throw Error('Could not obtain permission to submit a benchmark. Refresh and try again.');
      const data = await auth.json();
      options.headers['X-Launch-Token'] = data.token || data.launch_token || '';
    }
    const response = await fetch(url, options);
    let data; try { data = await response.json(); } catch { throw Error(`Benchmark service returned ${response.status}.`); }
    if (!response.ok) throw Error(data.error || `Benchmark request failed (${response.status}).`);
    return data;
  }
  function renderJobs() {
    root.querySelector('#benchmark-jobs').innerHTML = jobs.length ? jobs.map(job => {
      const decision = job.decision;
      const metricEntries = decision ? Object.entries(decision).flatMap(([key,value]) => key === 'simulated_decision' && value && typeof value === 'object' ? Object.entries(value).map(([name,metric]) => [`simulated ${name}`,metric]) : [[key,value]]) : [];
      const metrics = metricEntries.filter(([key, value]) => key !== 'status' && value !== null).map(([key,value]) => {
        const formatted = key.endsWith('mean_savings') && typeof value === 'number' ? `${(value * 100).toFixed(2)}% (${value})` : typeof value === 'object' ? JSON.stringify(value) : value;
        return `<div><dt>${esc(key.replaceAll('_',' '))}</dt><dd>${esc(formatted)}</dd></div>`;
      }).join('');
      return `<article class="benchmark-job" data-state="${esc(job.state)}"><div class="benchmark-job-heading"><h4>${esc(job.action)} <span class="muted">${esc(job.id)}</span></h4><strong>${esc(job.state)}</strong></div>${job.created_at ? `<p class="muted">${esc(new Date(job.created_at * 1000).toLocaleString())}</p>` : ''}${decision ? `<p class="benchmark-decision">Decision: <strong>${esc(decision.status)}</strong></p><dl class="benchmark-metrics">${metrics}</dl>` : ''}${job.error ? `<p class="error">${esc(job.error)}</p>` : ''}${Object.entries(job.paths || {}).filter(([,path]) => path).map(([key,path]) => `<div class="benchmark-path"><span>${esc(key)}: <code>${esc(path)}</code></span><button type="button" data-copy="${esc(path)}" aria-label="Copy ${esc(key)} path">Copy path</button></div>`).join('')}${['queued','running'].includes(job.state) ? `<button type="button" data-cancel="${esc(job.id)}">Cancel job</button>` : ''}${['run','inspect'].includes(job.action) && ['completed','recoverable','cancelled','interrupted'].includes(job.state) ? `<button type="button" data-collect="${esc(job.id)}">Collect results</button>` : ''}</article>`;
    }).join('') : '<p class="muted">No benchmark jobs yet. Start by inspecting a candidate artifact.</p>';
    root.querySelectorAll('[data-job-options]').forEach(select => {
      const selected = select.value;
      const first = select.options[0].outerHTML;
      select.innerHTML = first + jobs.filter(job => job.action === select.dataset.jobOptions && job.state === 'completed').map(job => `<option value="${esc(job.id)}">${esc(job.id)}</option>`).join('');
      if ([...select.options].some(option => option.value === selected)) select.value = selected;
    });
  }
  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const data = await request('/api/benchmarks');
      jobs = data.jobs || [];
      if (!initialized) {
        for (const [name,value] of Object.entries(data.defaults || {})) root.querySelectorAll(`input[name="${name}"]`).forEach(input => { if (!input.value) input.value = value || ''; });
        initialized = true;
      }
      renderJobs();
      status.textContent = data.active ? 'Benchmark jobs are active. Status updates automatically.' : 'Saved benchmark jobs are up to date.';
      status.classList.remove('error');
    } catch (error) { status.textContent = error.message; status.classList.add('error'); root.querySelector('#benchmark-jobs').textContent = 'Saved results could not be verified. Refresh jobs to try again.'; }
    finally { loading = false; }
  }
  root.addEventListener('change', event => {
    if (event.target.name === 'transport') event.target.closest('form').querySelectorAll('[data-transport]').forEach(group => {
      group.hidden = group.dataset.transport !== event.target.value;
      group.querySelectorAll('input').forEach(input => { input.disabled = group.hidden; input.required = !group.hidden && ['host','image'].includes(input.name); });
    });
  });
  root.querySelectorAll('[data-transport] input').forEach(input => { input.disabled = true; });
  root.querySelectorAll('form[data-action="inspect"],form[data-action="run"]').forEach(target => {
    target.querySelector('.benchmark-fields').insertAdjacentHTML('beforeend',field('env','Environment variable names (optional)'));
    target.querySelector('[name="env"]').placeholder = 'OPENAI_API_KEY, ANTHROPIC_API_KEY';
    const note = document.createElement('small');
    note.textContent = 'Names only. Values come from the server environment; never enter API keys here.';
    target.querySelector('[name="env"]').closest('.benchmark-field').append(note);
  });
  root.addEventListener('submit', async event => {
    event.preventDefault();
    const target = event.target, button = target.querySelector('[type="submit"]'), message = target.querySelector('.benchmark-message');
    if (button.disabled) return;
    const missing = [...target.querySelectorAll('input[required]')].find(input => !input.disabled && !input.value.trim());
    if (missing) { message.textContent = `Choose ${missing.closest('label').childNodes[0].textContent.toLowerCase()}.`; return; }
    const payload = {action:target.dataset.action};
    for (const [key,value] of new FormData(target)) if (String(value).trim()) payload[key] = ['seed','repeats','ssh_port'].includes(key) ? Number(value) : String(value).trim();
    if (payload.env) payload.env = payload.env.split(',').map(name => name.trim()).filter(Boolean);
    if (payload.action === 'create' && !payload.inspection_id && !payload.inspection_path || payload.action === 'run' && !payload.experiment_id && !payload.manifest) {
      message.textContent = payload.action === 'create' ? 'Choose a completed inspection or enter its path.' : 'Choose a saved experiment or enter its manifest path.'; return;
    }
    button.disabled = true; message.textContent = 'Submitting...';
    try { await request('/api/benchmarks',payload); message.textContent = 'Job submitted. Follow its progress below.'; await refresh(); }
    catch (error) { message.textContent = error.message; }
    finally { button.disabled = false; }
  });
  root.addEventListener('click', async event => {
    const button = event.target.closest('button'); if (!button) return;
    if (button.dataset.browse) {
      pickerInput = button.closest('.benchmark-field').querySelector('input');
      pickerTrigger = button; pickerKind = button.dataset.kind;
      picker.querySelector('#benchmark-picker-title').textContent = `Choose ${button.closest('.benchmark-field').querySelector('label').childNodes[0].textContent.toLowerCase()}`;
      picker.showModal(); await browse(pickerDirectory || ''); return;
    }
    if (button.id === 'benchmark-refresh') { await refresh(); return; }
    if (button.dataset.copy !== undefined) {
      try { await navigator.clipboard.writeText(button.dataset.copy); button.textContent = 'Copied'; }
      catch { status.textContent = 'Copy is unavailable. Select and copy the displayed path.'; } return;
    }
    if (!button.dataset.cancel && !button.dataset.collect) return;
    button.disabled = true;
    try { await request(button.dataset.cancel ? '/api/benchmarks/cancel' : '/api/benchmarks', button.dataset.cancel ? {id:button.dataset.cancel} : {action:'collect',run_id:button.dataset.collect}); await refresh(); }
    catch (error) { status.textContent = error.message; status.classList.add('error'); }
    finally { button.disabled = false; }
  });
  document.addEventListener('agentops-view', event => { visible = event.detail?.view === 'benchmarks'; if (visible) refresh(); });
  visible = !root.hidden;
  if (visible) refresh();
  setInterval(() => { if (visible && !document.hidden) refresh(); }, 2500);
})();
