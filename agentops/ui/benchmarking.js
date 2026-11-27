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
  controls.innerHTML = `<button type="button" class="benchmark-guide-trigger" data-guide-open aria-label="Benchmark help and guided tutorial" title="Guided setup"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m16 8-2.5 5.5L8 16l2.5-5.5L16 8Z"/></svg><span>Guided setup</span></button><button type="button" class="icon-button benchmark-help-toggle" data-help-toggle aria-label="Show help" aria-pressed="true" title="Show field help"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 2-3 4"/><path d="M12 17h.01"/></svg></button>`;
  root.querySelector('.benchmark-heading').append(controls);
  let helpEnabled = true;
  try { helpEnabled = localStorage.getItem('agentops.benchmark.help') !== 'off'; } catch {}
  const helpToggle = controls.querySelector('[data-help-toggle]');
  helpToggle.setAttribute('aria-pressed', String(helpEnabled));
  let activeHelp = null;
  let activeHelpField = null;
  let helpDismissTimer;
  function hideHelp() {
    clearTimeout(helpDismissTimer);
    if (activeHelp) activeHelp.hidePopover();
    activeHelp = null;
    activeHelpField = null;
  }
  function showHelp(input, hint) {
    clearTimeout(helpDismissTimer);
    if (!helpEnabled || guideActive || !input.getClientRects().length) return;
    if (activeHelp !== hint) { hideHelp(); hint.showPopover(); activeHelp = hint; }
    activeHelpField = input.closest('.benchmark-field') || input.parentElement;
    const anchor = input.getBoundingClientRect();
    const box = hint.getBoundingClientRect();
    const gap = 8;
    const left = Math.max(gap, Math.min(anchor.left, window.innerWidth - box.width - gap));
    const below = anchor.bottom + gap;
    const top = below + box.height <= window.innerHeight - gap
      ? below : Math.max(gap, anchor.top - box.height - gap);
    hint.style.left = `${left}px`;
    hint.style.top = `${top}px`;
  }
  function deferHideHelp() {
    clearTimeout(helpDismissTimer);
    helpDismissTimer = setTimeout(() => {
      if (activeHelpField?.contains(document.activeElement) || activeHelpField?.matches(':hover') || activeHelp?.matches(':hover')) return;
      hideHelp();
    }, 150);
  }
  root.querySelectorAll('input[name], select[name="transport"]').forEach((input, index) => {
    if (!helpCopy[input.name]) return;
    const hint = document.createElement('div');
    hint.id = `benchmark-help-${index}`;
    hint.className = 'benchmark-field-help';
    hint.setAttribute('popover', 'manual');
    hint.setAttribute('role', 'tooltip');
    hint.textContent = helpCopy[input.name];
    input.setAttribute('aria-describedby', hint.id);
    // Keep help outside the form's flex/grid layout and overflow container.
    document.body.append(hint);
    const field = input.closest('.benchmark-field') || input.parentElement;
    field.addEventListener('pointerenter', () => {
      // Scrolling a focused field into view can put another field under the mouse.
      // Keep keyboard guidance attached to the focused field in that case.
      const focused = document.activeElement;
      if (focused !== input && root.contains(focused) && focused?.hasAttribute('aria-describedby')) return;
      showHelp(input, hint);
    });
    field.addEventListener('pointerleave', deferHideHelp);
    field.addEventListener('focusin', () => showHelp(input, hint));
    field.addEventListener('focusout', event => { if (!field.contains(event.relatedTarget)) deferHideHelp(); });
    hint.addEventListener('pointerenter', () => clearTimeout(helpDismissTimer));
    hint.addEventListener('pointerleave', deferHideHelp);
  });
  helpToggle.title = 'Show floating help when you hover or focus a field';
  helpToggle.addEventListener('click', () => {
    helpEnabled = !helpEnabled;
    helpToggle.setAttribute('aria-pressed', String(helpEnabled));
    hideHelp();
    try { localStorage.setItem('agentops.benchmark.help', helpEnabled ? 'on' : 'off'); } catch {}
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') hideHelp(); });
document.addEventListener('scroll', () => {
if (activeHelpField?.contains(document.activeElement) && activeHelp) {
const input = document.activeElement, hint = activeHelp;
requestAnimationFrame(() => {
if (activeHelp !== hint) return;
const bounds = input.getBoundingClientRect();
if (bounds.bottom < 0 || bounds.top > window.innerHeight) hideHelp();
else showHelp(input, hint);
});
} else hideHelp();
}, true);
  window.addEventListener('resize', hideHelp);
  const steps = [
    ['Prepare your comparison', 'Compare a candidate build with a baseline using the same requests. Have a candidate wheel (.whl), taskset JSON and policy JSON ready. Files must be accessible to the selected runner. Start with Local if the dependencies and provider credentials are configured on this machine.', 'inspect'],
    ['Inspect the candidate', 'Choose the candidate wheel and transport, then select Inspect candidate. Wait for its job to complete. Inspection produces the candidate details needed to create an experiment; it does not run the comparison.', 'inspect'],
    ['Create a repeatable experiment', 'Select the completed inspection. Enter the candidate build’s full Git SHA and model identifier, then choose your taskset and policy files. Keep the seed fixed for repeatability. Create experiment saves a manifest describing the comparison.', 'create'],
    ['Run the comparison', 'Select the saved experiment and the same candidate wheel. Choose where to run it, then select Run experiment. This can send requests to the configured model provider and incur usage charges. Jobs below show progress; you can cancel active work.', 'run'],
    ['Read the results', 'After the run completes, read its decision and metrics in Benchmark jobs. A completed job alone does not mean the candidate passed: check the decision and any errors. Simulator results are labeled and do not establish measured provider performance. Copy saved artifact paths to examine the evidence or reuse the experiment.', null]
  ];
  const guide = document.createElement('div');
  guide.className = 'benchmark-guide';
  guide.setAttribute('popover', 'manual');
  guide.setAttribute('role', 'dialog');
  guide.setAttribute('aria-modal', 'false');
  guide.setAttribute('aria-labelledby', 'benchmark-guide-title');
  guide.innerHTML = `<div class="benchmark-heading"><h3 id="benchmark-guide-title">Benchmark guide</h3><button type="button" data-guide-close aria-label="Close benchmark guide">Close</button></div><p data-guide-progress class="muted"></p><div data-guide-content aria-live="polite"><h4 data-guide-heading></h4><p data-guide-copy></p></div><p data-guide-status role="status" class="muted"></p><div class="benchmark-guide-actions"><button type="button" data-guide-back>Back</button><button type="button" data-guide-go>Go to this step</button><button type="button" data-guide-next>Next</button></div>`;
  document.body.append(guide);
  let guideStep = 0;
  let guideActive = false;
  let guideFrame;
  const originalGuideViewport = {height: root.style.height, maxHeight: root.style.maxHeight, minHeight: root.style.minHeight};
  function restoreGuideViewport() {
    Object.assign(root.style, originalGuideViewport);
    root.classList.remove('benchmark-tour-docked');
  }
  function guideTarget() {
    const action = steps[guideStep][2];
    return action ? root.querySelector(`form[data-action="${action}"]`) : root.querySelector('#benchmark-jobs');
  }
  function positionGuide() {
    if (!guide.matches(':popover-open')) return;
    const focusedForm = root.contains(document.activeElement) ? document.activeElement.closest('form') : null;
    const target = focusedForm || guideTarget();
    const viewport = window.visualViewport;
    const width = viewport?.width || window.innerWidth;
    const height = viewport?.height || window.innerHeight;
    const originX = viewport?.offsetLeft || 0;
    const originY = viewport?.offsetTop || 0;
    const edge = 12, gap = 12;
    if (width < 760) {
      guide.style.width = `${width - edge * 2}px`;
      guide.style.maxHeight = `${Math.max(140, Math.min(320, height * .4))}px`;
      const dock = guide.getBoundingClientRect();
      const top = originY + height - dock.height - edge;
      guide.style.left = `${originX + edge}px`;
      guide.style.top = `${top}px`;
      root.classList.add('benchmark-tour-docked');
      const available = Math.max(80, top - Math.max(originY, root.getBoundingClientRect().top) - gap);
      root.style.height = `${available}px`;
      root.style.maxHeight = `${available}px`;
      root.style.minHeight = '0';
      if (target.contains(document.activeElement)) {
        const focused = document.activeElement.getBoundingClientRect();
        const region = root.getBoundingClientRect();
        if (focused.bottom > region.bottom - gap) root.scrollTop += focused.bottom - region.bottom + gap;
        else if (focused.top < region.top + gap) root.scrollTop -= region.top + gap - focused.top;
      }
      return;
    }
    restoreGuideViewport();
    const region = target.getBoundingClientRect();
    const focused = target.contains(document.activeElement) ? document.activeElement : null;
    const visibleField = [...target.querySelectorAll('input, select, button')].find(el => {
      const r = el.getBoundingClientRect(); return r.height && r.top >= originY && r.bottom <= originY + height;
    });
    const anchor = (focused || visibleField || target).getBoundingClientRect();
    guide.style.width = `${Math.min(340, width - edge * 2)}px`;
    guide.style.maxHeight = `${height - edge * 2}px`;
    const box = guide.getBoundingClientRect();
    let left, top;
    if (region.right + gap + box.width <= originX + width - edge) {
      left = region.right + gap;
      top = Math.max(originY + edge, Math.min(anchor.top, originY + height - box.height - edge));
    } else if (region.left - gap - box.width >= originX + edge) {
      left = region.left - gap - box.width;
      top = Math.max(originY + edge, Math.min(anchor.top, originY + height - box.height - edge));
    } else {
      left = Math.max(originX + edge, Math.min(anchor.left, originX + width - box.width - edge));
      const above = Math.max(0, Math.min(height - edge * 2, anchor.top - originY - gap - edge));
      const below = Math.max(0, Math.min(height - edge * 2, originY + height - anchor.bottom - gap - edge));
      const useAbove = above >= box.height || above > below;
      const available = useAbove ? above : below;
      guide.style.maxHeight = `${Math.max(48, available)}px`;
      const actualHeight = guide.getBoundingClientRect().height;
      top = useAbove ? anchor.top - gap - actualHeight : anchor.bottom + gap;
      top = Math.max(originY + edge, Math.min(top, originY + height - actualHeight - edge));
    }
    guide.style.left = `${left}px`;
    guide.style.top = `${top}px`;
  }
  function scheduleGuidePosition() {
    cancelAnimationFrame(guideFrame);
    guideFrame = requestAnimationFrame(positionGuide);
  }
  function updateGuideStatus() {
    if (!guideActive) return;
    const action = steps[guideStep][2];
    const job = jobs.find(item => item.action === (action || 'run'));
    const message = guide.querySelector('[data-guide-status]');
    const next = action === 'inspect' ? 'Create experiment' : action === 'create' ? 'Run experiment' : 'Read the results';
    message.textContent = guideStep === 0 ? 'Keep this guide open while you work. Click Next when your files are ready.'
      : job ? `${action || 'run'} job: ${job.state}.${job.state === 'completed' ? ` Continue to ${next} when ready.` : ' Follow its progress in Benchmark jobs; you can keep using the forms.'}`
      : 'Complete the highlighted form, or click Next if you already have its saved output.';
    scheduleGuidePosition();
  }
  function renderGuide() {
    guide.querySelector('[data-guide-progress]').textContent = `Step ${guideStep + 1} of ${steps.length}`;
    guide.querySelector('[data-guide-heading]').textContent = steps[guideStep][0];
    guide.querySelector('[data-guide-copy]').textContent = steps[guideStep][1];
    guide.querySelector('[data-guide-back]').disabled = guideStep === 0;
    guide.querySelector('[data-guide-next]').textContent = guideStep === steps.length - 1 ? 'Finish' : 'Next';
    root.querySelectorAll('.benchmark-tour-target').forEach(el => el.classList.remove('benchmark-tour-target'));
    guideTarget().classList.add('benchmark-tour-target');
    updateGuideStatus();
  }
  function closeGuide() {
    guideActive = false;
    restoreGuideViewport();
    if (guide.matches(':popover-open')) guide.hidePopover();
    root.querySelectorAll('.benchmark-tour-target').forEach(el => el.classList.remove('benchmark-tour-target'));
    controls.querySelector('[data-guide-open]').focus({preventScroll: true});
  }
  function focusGuideTarget() {
    const target = guideTarget();
    root.scrollTop += target.getBoundingClientRect().top - root.getBoundingClientRect().top - 12;
    const focusTarget = target.querySelector('input, select, button') || target;
    if (focusTarget === target) target.setAttribute('tabindex', '-1');
    focusTarget.focus({preventScroll: true});
    positionGuide();
  }
  controls.querySelector('[data-guide-open]').addEventListener('click', () => {
    hideHelp();
    if (guideActive) { guide.querySelector('[data-guide-next]').focus(); return; }
    guideActive = true;
    guideStep = 0;
    guide.showPopover();
    renderGuide();
    positionGuide();
  });
  guide.querySelector('[data-guide-close]').addEventListener('click', closeGuide);
  guide.querySelector('[data-guide-back]').addEventListener('click', () => {
    if (guideStep > 0) guideStep--;
    renderGuide(); focusGuideTarget();
  });
  guide.querySelector('[data-guide-next]').addEventListener('click', () => {
    if (guideStep === steps.length - 1) closeGuide();
    else { guideStep++; renderGuide(); focusGuideTarget(); }
  });
  guide.querySelector('[data-guide-go]').addEventListener('click', focusGuideTarget);
  root.addEventListener('focusin', () => { if (guideActive) { hideHelp(); scheduleGuidePosition(); } });
  document.addEventListener('scroll', scheduleGuidePosition, true);
  window.addEventListener('resize', scheduleGuidePosition);
  window.visualViewport?.addEventListener('resize', scheduleGuidePosition);
  window.visualViewport?.addEventListener('scroll', scheduleGuidePosition);
  new ResizeObserver(scheduleGuidePosition).observe(guide);
  document.addEventListener('agentops-view', () => {
    if (!guideActive) return;
    if (root.hidden) { restoreGuideViewport(); if (guide.matches(':popover-open')) guide.hidePopover(); }
    else { if (!guide.matches(':popover-open')) guide.showPopover(); scheduleGuidePosition(); }
  });

  const status = root.querySelector('#benchmark-status');
  const picker = document.createElement('dialog');
  picker.className = 'benchmark-picker';
  picker.setAttribute('aria-labelledby','benchmark-picker-title');
  picker.innerHTML = '<div class="benchmark-heading"><h3 id="benchmark-picker-title">Choose file</h3><button type="button" data-picker-close>Close</button></div><p id="benchmark-picker-status" role="status"></p><nav class="benchmark-picker-roots" aria-label="Filesystem roots"></nav><div class="benchmark-picker-location"><button type="button" data-picker-up>Up one folder</button><code id="benchmark-picker-directory"></code></div><div class="benchmark-picker-entries"></div>';
  document.body.append(picker);
let pickerInput, pickerKind, pickerParent, pickerDirectory, pickerBusy = false;
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
    updateGuideStatus();
  }
  async function refresh() {
    if (loading) return;
    loading = true;
    status.textContent = initialized ? 'Refreshing saved jobs… Previous results remain visible.' : 'Loading saved benchmark jobs…';
    if (!initialized) root.querySelector('#benchmark-jobs').innerHTML = window.agentopsLoading.skeleton('Loading saved benchmark jobs…', 3);
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
    button.disabled = true; window.agentopsFeedback.busy(button, true); message.textContent = 'Submitting…';
    try { await request('/api/benchmarks',payload); message.textContent = 'Job submitted. Follow its progress below.'; window.agentopsFeedback.notify(message.textContent); await refresh(); }
    catch (error) { message.textContent = error.message; window.agentopsFeedback.notify(error.message, 'error'); }
    finally { button.disabled = false; window.agentopsFeedback.busy(button, false); }
  });
  root.addEventListener('click', async event => {
    const button = event.target.closest('button'); if (!button) return;
    if (button.dataset.browse) {
      pickerInput = button.closest('.benchmark-field').querySelector('input');
pickerKind = button.dataset.kind;
      picker.querySelector('#benchmark-picker-title').textContent = `Choose ${button.closest('.benchmark-field').querySelector('label').childNodes[0].textContent.toLowerCase()}`;
      picker.showModal(); await browse(pickerDirectory || ''); return;
    }
    if (button.id === 'benchmark-refresh') { await refresh(); return; }
    if (button.dataset.copy !== undefined) {
      try { await navigator.clipboard.writeText(button.dataset.copy); button.textContent = 'Copied'; }
      catch { status.textContent = 'Copy is unavailable. Select and copy the displayed path.'; } return;
    }
    if (!button.dataset.cancel && !button.dataset.collect) return;
    button.disabled = true; window.agentopsFeedback.busy(button, true);
    try { await request(button.dataset.cancel ? '/api/benchmarks/cancel' : '/api/benchmarks', button.dataset.cancel ? {id:button.dataset.cancel} : {action:'collect',run_id:button.dataset.collect}); window.agentopsFeedback.notify(button.dataset.cancel ? 'Cancellation requested.' : 'Collection submitted. Follow its progress below.'); await refresh(); }
    catch (error) { status.textContent = error.message; status.classList.add('error'); window.agentopsFeedback.notify(error.message, 'error'); }
    finally { button.disabled = false; window.agentopsFeedback.busy(button, false); }
  });
  document.addEventListener('agentops-view', event => { visible = event.detail?.view === 'benchmarks'; if (visible) refresh(); });
document.addEventListener('agentops-source-ready', event => {
const source=event.detail;
if (!source?.artifact || !/^[a-f0-9]{40}$/.test(source.sha || '')) return;
root.querySelectorAll('form[data-action="inspect"] input[name="artifact"],form[data-action="run"] input[name="artifact"]').forEach(input=>input.value=source.artifact);
root.querySelector('form[data-action="create"] input[name="candidate_sha"]').value=source.sha;
const inspect=root.querySelector('form[data-action="inspect"]');
inspect.querySelector('.benchmark-message').textContent=`Prepared ${source.wheel_name || 'candidate wheel'}. Choose a runner compatible with ${source.platform_tag || 'the wheel platform'}, then inspect the candidate.`;
inspect.scrollIntoView({block:'center'});
inspect.querySelector('button[type="submit"]').focus({preventScroll:true});
});
  visible = !root.hidden;
  if (visible) refresh();
  setInterval(() => { if (visible && !document.hidden) refresh(); }, 2500);
})();
