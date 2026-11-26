/* Visual design and exploration; provider-free examples remain explicit simulations. */
(() => {
  const host = document.getElementById('benchmark-view');
  if (!host) return;
  const root = document.createElement('section');
  root.className = 'studio';
  root.setAttribute('aria-label', 'Benchmark Studio');
  host.prepend(root);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let catalogue, design, jobs = [], draft, step = 0, result, selectedJob, active = false;
  let initialized = false, loading = false, busy = false, visible = !host.hidden;
  const terminal = new Set(['completed','failed','cancelled','interrupted','recoverable']);
  async function request(path, payload) {
    const auth = await fetch('/api/launcher');
    if (!auth.ok) throw Error('Dashboard authentication unavailable. Refresh and try again.');
    const {token, launch_token} = await auth.json();
    const response = await fetch(path, {method:payload === undefined ? 'GET' : 'POST',
      headers:{'Content-Type':'application/json','X-Launch-Token':token || launch_token || ''},
      ...(payload === undefined ? {} : {body:JSON.stringify(payload)})});
    const data = await response.json();
    if (!response.ok) throw Error(data.error || 'Studio request failed. Refresh saved jobs before retrying.');
    return data;
  }
  function status(message) { root.querySelector('[data-studio-status]').textContent = message; }
  const options = (items, key) => items.map(item => `<label class="studio-choice"><input type="checkbox" data-choice="${key}" value="${esc(item.id)}" ${design[key].includes(item.id) ? 'checked' : ''}><span><strong>${esc(item.name)}</strong>${item.description ? `<small>${esc(item.description)}</small>` : ''}</span></label>`).join('');
  function shell() {
    root.innerHTML = `<div class="studio-hero"><div><p class="studio-eyebrow">BENCHMARK STUDIO</p><h2>Build a comparison. See what changes.</h2><p>Start with a guided example, shape your experiment, and explore the evidence.</p></div><div><span class="studio-badge">Demos need no credentials</span><button type="button" data-demo>Run a demo now</button><button type="button" data-open-measured>Design a measured experiment</button></div></div><p data-studio-status role="status" aria-live="polite"></p><div data-studio-content></div><section data-studio-history aria-label="Saved Studio experiments"></section>`;
  }
  function render() {
    if (!root.querySelector('[data-studio-content]')) shell();
    root.dataset.mode = step === 4 ? 'results' : step ? 'design' : 'templates';
    const content = root.querySelector('[data-studio-content]');
    if (!step) {
      content.innerHTML = `<div class="studio-section-title"><h3>Choose a starting point</h3>${draft ? '<button type="button" data-continue>Continue saved draft</button>' : ''}</div><div class="studio-templates">${catalogue.templates.map(t => `<article><span class="studio-badge">Offline demo · v${t.version}</span><h4>${esc(t.name)}</h4><p>${esc(t.description)}</p><small>${t.design.scenarios.length * t.design.models.length} cells · ${t.design.repeats} repeats</small><button type="button" data-template="${esc(t.id)}">Use ${esc(t.name)}</button></article>`).join('')}</div><p class="studio-note">These examples use synthetic measurements to teach the workflow. Use the measured designer below to compare actual candidate sources and configured providers.</p>`;
    } else {
      content.innerHTML = `<div class="studio-designer"><nav aria-label="Experiment design steps">${['Name','Shape','Review','Results'].map((name,index) => `<button type="button" data-step="${index+1}" ${step === index+1 ? 'aria-current="step"' : ''} ${index === 3 && !result ? 'disabled' : ''}>${index+1}. ${name}</button>`).join('')}</nav><div data-studio-step tabindex="-1"></div><div class="studio-actions"><button type="button" data-home>Templates</button><button type="button" data-save>Save draft</button>${step > 1 ? '<button type="button" data-back>Back</button>' : ''}${step < 3 ? '<button type="button" data-next>Next</button>' : ''}</div></div>`;
      const panel = content.querySelector('[data-studio-step]');
      if (step === 1) panel.innerHTML = `<p class="studio-eyebrow">01 / INTENT</p><h3>Name your experiment</h3><p>Keep a reproducible record of what you compare and why.</p><label class="studio-input">Experiment name<input data-setting="label" maxlength="120" value="${esc(design.label)}" required></label><div class="studio-source"><strong>Source: bundled simulator v1</strong><p>No download, build, model account or local input files required. Candidate compression and baseline passthrough are simulated.</p></div>`;
      if (step === 2) panel.innerHTML = `<p class="studio-eyebrow">02 / DESIGN</p><h3>Shape your comparison</h3><p>Select scenarios and model profiles. Each combination becomes a paired comparison.</p><fieldset><legend>Scenarios</legend><div class="studio-options">${options(catalogue.scenarios,'scenarios')}</div></fieldset><fieldset><legend>Features to explore · simulated</legend><div class="studio-options">${options(catalogue.features,'features')}</div></fieldset><fieldset><legend>Model profiles · simulated</legend><div class="studio-options">${options(catalogue.models,'models')}</div></fieldset><fieldset><legend>Benchmark tools</legend>${catalogue.tools.map(t => `<label class="studio-choice"><input type="checkbox" disabled ${t.available ? 'checked' : ''} ${t.available ? 'aria-label="Native paired runner"' : ''}><span><strong>${esc(t.name)}</strong><small>${t.available ? 'Seeded baseline/candidate pairs with policy evaluation.' : esc(t.reason)}</small></span></label>`).join('')}</fieldset><div class="studio-numbers"><label class="studio-input">Repeats per cell<input data-setting="repeats" type="number" min="5" max="100" value="${design.repeats}"></label><label class="studio-input">Seed<input data-setting="seed" type="number" min="0" max="4294967295" value="${design.seed}"></label></div>`;
      if (step === 3) {
        const cells = design.scenarios.flatMap(s => design.models.map(m => ({s,m})));
        panel.innerHTML = `<p class="studio-eyebrow">03 / REVIEW</p><h3>Review your experiment</h3><h4>${esc(design.label)}</h4><p class="studio-count">${cells.length} cells · ${cells.length * design.repeats * 2} paired requests</p><div class="studio-matrix">${cells.map(c => `<article><span>${esc(c.s)}</span><strong>${esc(c.m)}</strong><small>Native · ${design.repeats} baseline/candidate pairs</small></article>`).join('')}</div><dl class="studio-settings"><div><dt>Provider</dt><dd>Offline simulator</dd></div><div><dt>Features</dt><dd>${esc(design.features.join(', ') || 'No optimization')}</dd></div><div><dt>Seed</dt><dd>${design.seed}</dd></div><div><dt>Thresholds</dt><dd>≥20% token savings · ≤1.2× latency · ≥90% success</dd></div></dl><p class="studio-note">Synthetic demonstration. No provider requests. Results do not establish measured performance.</p><p>Preflight checks supported choices, repeat counts and request limits before launching.</p><button type="button" data-launch ${busy || active ? 'disabled' : ''}>${active ? 'Another experiment is active' : 'Run this experiment'}</button>`;
      }
      if (step === 4) renderResults(panel);
    }
    renderHistory();
  }
  function renderHistory() {
    const section = root.querySelector('[data-studio-history]');
    section.innerHTML = `<h3>Saved experiments</h3>${jobs.length ? `<div class="studio-history">${jobs.map(job => `<article><div><strong>${esc(job.label || job.id.slice(0,8))}</strong><small>${esc(typeof job.created_at==='number'?new Date(job.created_at*1000).toLocaleString():job.created_at)}</small></div><span>${esc(job.state)} · simulator</span>${job.state === 'completed' ? `<button type="button" data-result="${esc(job.id)}">Explore results ${esc(job.id.slice(0,8))}</button>` : !terminal.has(job.state) ? `<button type="button" data-studio-cancel="${esc(job.id)}">Cancel experiment</button>` : '<small>Evidence unavailable. Inspect job diagnostics below.</small>'}</article>`).join('')}</div>` : '<p>No Studio runs yet. Start a demo above to create your first comparison.</p>'}`;
  }
  function renderResults(panel) {
    if (!result) { panel.innerHTML = '<h3>Experiment in progress</h3><p>Watching the durable job. You can leave this view and return to saved experiments.</p>'; return; }
    const rows = result.studio.cells.map(cell => {
      const samples = result.attempts.filter(a => a.task_id === cell.id);
      function mean(arm,key) { const values = samples.filter(a => a.arm === arm && Number.isFinite(a[key])).map(a=>a[key]); return values.length ? values.reduce((a,b)=>a+b,0) / values.length : null; }
      const a = mean('a1_passthrough','tokens'), b = mean('b_headroom','tokens');
      return {...cell,a,b,latencyA:mean('a1_passthrough','latency_ms'),latencyB:mean('b_headroom','latency_ms'),pairs:samples.length/2,
        savings:a > 0 && b !== null ? (a-b)/a * 100 : null};
    });
    const max = Math.max(1,...rows.flatMap(r=>[r.a || 0,r.b || 0]));
    const number = n => n === null ? 'Missing' : n.toFixed(1);
    const chart = `<div class="studio-chart" role="img" aria-label="Baseline and candidate tokens by scenario">${rows.map(r => `<div class="studio-chart-row"><strong>${esc(r.scenario)} · ${esc(r.model)}</strong><div class="studio-bar-line"><svg viewBox="0 0 510 12" aria-hidden="true"><rect class="studio-bar-baseline" width="${(r.a || 0)/max*510}" height="12"/></svg><span>${number(r.a)}</span></div><div class="studio-bar-line"><svg viewBox="0 0 510 12" aria-hidden="true"><rect class="studio-bar-candidate" width="${(r.b || 0)/max*510}" height="12"/></svg><span>${number(r.b)}</span></div></div>`).join('')}</div>`;
    const decision = result.decision.simulated_decision;
    panel.innerHTML = `<p class="studio-eyebrow">04 / EVIDENCE</p><h3>Paired results</h3><h4>${esc(result.experiment_id)}</h4><p class="studio-note">${esc(result.studio.notice)}</p><div class="studio-result-summary"><strong>Live qualification: inconclusive</strong><span>Simulated policy: ${esc(decision.status)}</span><span>${esc(decision.pairs || 0)} pairs</span></div><figure><figcaption>Input tokens per request · baseline <span class="studio-key baseline"></span> candidate <span class="studio-key candidate"></span></figcaption>${chart}</figure><div class="studio-table"><table aria-label="Paired scenario metrics"><thead><tr><th>Scenario / model</th><th>Pairs</th><th>Baseline tokens</th><th>Candidate tokens</th><th>Savings</th><th>Baseline ms</th><th>Candidate ms</th></tr></thead><tbody>${rows.map(r => `<tr><th>${esc(r.scenario)} / ${esc(r.model)}</th><td>${r.pairs}</td><td>${number(r.a)}</td><td>${number(r.b)}</td><td>${number(r.savings)}%</td><td>${number(r.latencyA)}</td><td>${number(r.latencyB)}</td></tr>`).join('')}</tbody></table></div><p class="studio-note">Simulator samples are deterministic; they do not estimate provider variability or real-world confidence.</p><details><summary>Reproducibility and evidence</summary><p>Seed ${result.seed} · Template version ${result.studio.template_version}</p><code>${esc(result.manifest_digest)}</code><p>Source: bundled synthetic simulator v1. No candidate build was executed.</p><button type="button" data-export>Export evidence JSON</button></details><button type="button" data-duplicate>Edit a copy</button>`;
  }
  function capture() {
    root.querySelectorAll('[data-setting]').forEach(input => { design[input.dataset.setting] = input.type === 'number' ? Number(input.value) : input.value; });
    for (const key of ['scenarios','models','features']) {
      const inputs = root.querySelectorAll(`[data-choice="${key}"]`);
      if (inputs.length) design[key] = [...inputs].filter(i=>i.checked).map(i=>i.value);
    }
  }
  async function showResult(id) {
    const data = await request(`/api/benchmarks/studio?job=${encodeURIComponent(id)}`);
    selectedJob = id; result = data.result; design = structuredClone(result.studio.design); step = 4; render();
  }
  async function launch(payload) {
    if (busy || active) return;
    busy = true;
    root.querySelectorAll('[data-demo],[data-launch]').forEach(b=>b.disabled=true);
    try {
      const checked = await request('/api/benchmarks/studio/preview',payload);
      design = checked.design;
      const job = await request('/api/benchmarks',{action:'demo',design});
      selectedJob = job.id; result = null; active = true; step = 4;
      status('Experiment submitted. Watching progress.'); render(); await refresh();
    } finally { busy = false; root.querySelectorAll('[data-demo]').forEach(b=>b.disabled=active); }
  }
  async function refresh() {
    if (loading || !visible) return;
    loading = true;
    try {
      const state = await request('/api/benchmarks/studio');
      catalogue = state.catalogue; draft = state.draft; jobs = state.jobs;
      active = state.active || jobs.some(j=>!terminal.has(j.state));
      if (!initialized) { initialized = true; design = structuredClone(draft || catalogue.templates[0].design); render(); }
      else renderHistory();
      root.querySelectorAll('[data-demo],[data-launch]').forEach(b=>b.disabled=active || busy);
      const selected = jobs.find(j=>j.id === selectedJob);
      if (selected?.state === 'completed' && !result) { await showResult(selectedJob); status('Experiment complete. Explore the paired evidence.'); }
      else if (selected && terminal.has(selected.state) && selected.state !== 'completed') status(`Experiment ${selected.state}. Saved diagnostics are available in jobs below.`);
    } catch (error) { if (!root.querySelector('[data-studio-status]')) shell(); status(error.message); }
    finally { loading = false; }
  }
  root.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button) return;
    try {
      if (button.hasAttribute('data-demo')) { if (!catalogue) return; await launch(catalogue.templates[0].design); }
      else if (button.dataset.template) { design = structuredClone(catalogue.templates.find(t=>t.id === button.dataset.template).design); result = null; selectedJob = null; step = 1; render(); }
      else if (button.hasAttribute('data-continue')) { design = structuredClone(draft); result = null; selectedJob = null; step = 1; render(); }
      else if (button.hasAttribute('data-home')) { capture(); step = 0; render(); }
      else if (button.hasAttribute('data-save')) { capture(); await request('/api/benchmarks/studio/draft',design); draft = structuredClone(design); status('Draft saved.'); }
      else if (button.hasAttribute('data-next') || button.hasAttribute('data-back') || button.dataset.step) {
        capture(); await request('/api/benchmarks/studio/preview',design);
        await request('/api/benchmarks/studio/draft',design); draft = structuredClone(design);
        step = button.dataset.step ? Number(button.dataset.step) : step + (button.hasAttribute('data-next') ? 1 : -1);
        render(); root.querySelector('[data-studio-step]').focus(); status('');
      }
      else if (button.hasAttribute('data-launch')) { capture(); await launch(design); }
      else if (button.dataset.result) await showResult(button.dataset.result);
      else if (button.dataset.studioCancel) { await request('/api/benchmarks/cancel',{id:button.dataset.studioCancel}); status('Cancellation requested.'); await refresh(); }
      else if (button.hasAttribute('data-duplicate')) { design = structuredClone(result.studio.design); design.label += ' (copy)'; result = null; selectedJob = null; step = 1; render(); }
      else if (button.hasAttribute('data-export')) {
        const url = URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));
        const link = document.createElement('a'); link.href=url; link.download=`benchmark-${selectedJob}.json`; link.click(); URL.revokeObjectURL(url);
      }
    } catch (error) { status(error.message); }
  });
  document.addEventListener('agentops-view', event => { visible = event.detail?.view === 'benchmarks'; if (visible) refresh(); });
  setInterval(()=> { if (visible && !document.hidden) refresh(); },2000);
  if (visible) refresh();
})();
