/* Durable maintenance controls. Render untrusted repository text as text nodes. */
(() => {
  const root = document.getElementById('maintenance-view');
  if (!root) return;
  const selected = new Set(), secondary = new Set();
  let data = null, busy = false, polling = false, view = 'updates', signature = '';
  let token = null, message = '';
  try { if (localStorage.getItem('agentops-view') === 'main-ci') view = 'main-ci'; } catch {}
  const el = (tag, value, cls) => {
    const item = document.createElement(tag);
    if (value !== undefined) item.textContent = value;
    if (cls) item.className = cls;
    return item;
  };
  function button(label, action, disabled = false) {
    const b = el('button', label); b.type = 'button'; b.disabled = disabled || busy;
    b.onclick = action; return b;
  }
  function link(url, label) {
    try {
      const u = new URL(url);
      if (u.protocol === 'https:' && u.hostname === 'github.com' && !u.username && !u.password) {
        const a = el('a', label); a.href = u.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a;
      }
    } catch {}
    return el('span', label);
  }
  function checkbox(label, checked, change, disabled = false) {
    const wrapper = el('label'), input = el('input');
    input.type = 'checkbox'; input.checked = checked; input.disabled = disabled || busy;
    input.setAttribute('aria-label', label); input.onchange = () => change(input.checked);
    wrapper.append(input, document.createTextNode(label)); return wrapper;
  }
  async function post(action, payload) {
    if (busy) return;
    busy = true; message = 'Submitting…'; root.setAttribute('aria-busy', 'true'); render(true);
    try {
      if (!token) {
        const r = await fetch('/api/launcher'); if (!r.ok) throw Error('Dashboard connection unavailable');
        token = (await r.json()).token;
      }
      const r = await fetch('/api/maintenance/' + action, {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Launch-Token': token}, body: JSON.stringify(payload)});
      const result = await r.json(); if (!r.ok) throw Error(result.error || 'Maintenance request failed');
      data = result; message = action === 'refresh' ? 'Refreshing PR inventory…' : 'Request saved. Progress appears below.';
      window.agentopsFeedback.notify(message);
    } catch (error) { message = error.message; window.agentopsFeedback.notify(message, 'error'); }
    finally { busy = false; root.removeAttribute('aria-busy'); render(true); }
  }
  function render(force = false) {
    if (!data) {
      const status = el('p', message || 'Loading maintenance status…', 'maintenance-status');
      status.setAttribute('role', 'status');
      root.replaceChildren(el('h1', view === 'updates' ? 'PR updates' : 'Main CI'), status);
      return;
    }
    const alert = document.getElementById('main-ci-alert');
    alert.hidden = !data.alert;
    const current = JSON.stringify([data, view, busy, message, [...selected], [...secondary]]);
    if (!force && current === signature) return;
    signature = current;
    const scroll = root.scrollTop;
    const active = document.activeElement;
    const focus = root.contains(active) ? {tag: active.tagName,
      label: active.getAttribute('aria-label') || active.textContent} : null;
    root.replaceChildren(el('h1', view === 'updates' ? 'PR updates' : 'Main CI'));
    const status = el('p', message, 'maintenance-status'); status.setAttribute('role', 'status'); root.append(status);
    if (busy) status.classList.add('is-submitting');
    if (view === 'updates') renderUpdates(); else renderMain();
    root.scrollTop = scroll;
    if (focus) {
      const replacement = [...root.querySelectorAll('input,button,a')].find(item => !item.disabled &&
        item.tagName === focus.tag && (item.getAttribute('aria-label') || item.textContent) === focus.label);
      replacement?.focus({preventScroll: true});
    }
  }
  function renderUpdates() {
    root.append(el('p', 'Rebase PRs behind main. Verified mechanical updates reuse existing review evidence silently.'));
    const toolbar = el('div', undefined, 'maintenance-toolbar');
    const rows = data.inventory?.prs || [];
    const eligible = rows.filter(r => r.behind > 0).map(r => r.number);
    for (const n of [...selected]) if (!eligible.includes(n)) selected.delete(n);
    toolbar.append(button('Refresh outdated PRs', () => post('refresh', {})),
      button('Select all outdated PRs', () => { eligible.forEach(n => selected.add(n)); render(true); }, !eligible.length),
      button('Clear PR selection', () => { selected.clear(); render(true); }, !selected.size),
      button('Update selected PRs', () => post('update', {numbers: [...selected].sort((a,b) => a-b),
        reapprove: data.settings.reapprove}), !selected.size || !data.fresh),
      checkbox('Reapprove previously approved workflows', data.settings.reapprove,
        checked => post('settings', {reapprove: checked})));
    root.append(toolbar, el('p', `${selected.size} selected · ${rows.length} outdated or unknown. ` +
      (data.fresh ? 'Current inventory.' : 'Refresh required before updates.'), 'maintenance-status'));
    if (data.inventory?.error) root.append(el('p', data.inventory.error, 'maintenance-error'));
    const list = el('div', undefined, 'maintenance-card'); list.setAttribute('aria-label', 'Outdated pull requests');
    for (const row of rows) {
      const item = el('div', undefined, 'maintenance-row');
      const check = checkbox(`Update PR #${row.number}`, selected.has(row.number), checked => {
        checked ? selected.add(row.number) : selected.delete(row.number); render(true);
      }, !row.behind);
      const body = el('div', undefined, 'maintenance-body');
      body.append(link(row.url, `#${row.number} ${row.title}`),
        el('small', `${row.author || ''} · ${row.head_repo || 'Unavailable fork'}:${row.head_ref || ''}`),
        el('small', `Conflicts: ${row.conflict || 'unknown'} · Permission: ${row.permission || 'unknown'}` +
          (row.excluded ? ' · Author excluded' : '') + (row.last_update ? ` · Last update: ${row.last_update}` : '')),
        el('small', row.reason || ''));
      item.append(check, body, el('span', row.behind == null ? 'Unknown drift' : `${row.behind} behind`, 'maintenance-state'));
      list.append(item);
    }
    if (!rows.length) list.append(el('p', data.fresh ? 'No PRs behind main.' : 'Refresh to inspect every open main PR.'));
    root.append(list, el('h2', 'Needs remediation'));
    const queue = el('div', undefined, 'maintenance-card');
    const queueKeys = new Set((data.secondary || []).map(row => row.key));
    const agentKeys = (data.secondary || []).filter(row => row.state !== 'source_held').map(row => row.key);
    for (const k of [...secondary]) if (!queueKeys.has(k)) secondary.delete(k);
    const queueTools = el('div', undefined, 'maintenance-toolbar');
    queueTools.append(button('Select all remediation entries', () => { agentKeys.forEach(k => secondary.add(k)); render(true); }, !agentKeys.length),
      button('Clear remediation selection', () => { secondary.clear(); render(true); }, !secondary.size),
      button('Send selected to agent', () => post('remediate', {keys: [...secondary]}), !secondary.size));
    queue.append(queueTools);
    for (const row of data.secondary || []) {
      const item = el('div', undefined, 'maintenance-row'), body = el('div', undefined, 'maintenance-body');
      body.append(el('strong', `#${row.number}`), el('span', row.reason || row.state));
      item.append(checkbox(`Remediate PR #${row.number}`, secondary.has(row.key), checked => {
        checked ? secondary.add(row.key) : secondary.delete(row.key); render(true);
      }, row.state === 'source_held'), body, button(`Retry PR #${row.number}`, () => post('retry', {key: row.key}), row.state === 'source_held'));
      queue.append(item);
    }
    if (!queueKeys.size) queue.append(el('p', 'No updates need remediation.'));
    root.append(queue, el('h2', 'Update history'));
    for (const row of data.updates || []) {
      const card = el('div', undefined, 'maintenance-card');
      card.append(el('strong', `#${row.number} · ${row.state.replaceAll('_', ' ')}`),
        el('p', row.reason || row.summary || row.phase || ''), el('small', row.approval_status || ''));
      root.append(card);
    }
  }
  function renderMain() {
    root.append(el('p', 'Monitor main independently of this browser. Repair agents diagnose failures, validate a fix, and open a PR for your review.'));
    const toolbar = el('div', undefined, 'maintenance-toolbar');
    toolbar.append(checkbox('Watch main CI', data.settings.watch, checked => post('settings', {watch: checked})),
      checkbox('Autofix main CI', data.settings.autofix, checked => post('settings', {autofix: checked})));
    root.append(toolbar, el('p', 'Autofix starts repair automatically and suppresses alerts. Repair PRs still require your review.', 'maintenance-status'));
    const monitor = data.monitor || {}, card = el('div', undefined, 'maintenance-card');
    card.append(el('strong', `Main CI: ${(monitor.state || 'unknown').replaceAll('_', ' ')}`),
      el('p', `Main ${(monitor.head_sha || 'not observed').slice(0,12)}`),
      el('small', monitor.last_poll ? `Last checked ${new Date(monitor.last_poll * 1000).toLocaleString()}` : 'Waiting for first check.'));
    if (monitor.error) card.append(el('p', `Status stale: ${monitor.error}`, 'maintenance-error'));
    for (const run of monitor.workflows || []) {
      const p = el('p'); p.append(link(run.html_url, `${run.name || 'Workflow'}: ${run.conclusion || run.status}`));
      card.append(p);
    }
    root.append(card, el('h2', 'CI incidents'));
    for (const incident of data.incidents || []) {
      const item = el('div', undefined, 'maintenance-card');
      item.append(el('strong', `${incident.state} · ${(incident.head_sha || incident.key).slice(0,12)}`));
      for (const run of incident.failures || []) {
        const p = el('p'); p.append(link(run.html_url, `${run.name || 'Workflow'}: ${run.conclusion || run.status}`)); item.append(p);
      }
      if (incident.pr) item.append(link(incident.pr.html_url, `Review repair PR #${incident.pr.number}`));
      else if (incident.state === 'approval_needed') item.append(el('p', 'Approve the observed workflow on GitHub; a code fix is not needed.'));
      else if (incident.repair_state === 'source_held') item.append(button('Review protected source repair', () => reviewSource(incident)));
      else if (incident.state !== 'resolved') item.append(button(['agent_failed','interrupted','publication_unknown'].includes(incident.repair_state) ? 'Retry main repair' : 'Fix main',
        () => post('fix-main', {key: incident.key, retry: ['agent_failed', 'interrupted','publication_unknown'].includes(incident.repair_state)}),
        ['agent_queued', 'agent_running'].includes(incident.repair_state)));
      item.append(el('p', incident.summary || incident.reason || incident.repair_state || ''));
      root.append(item);
    }
    if (!(data.incidents || []).length) root.append(el('p', 'No recorded main CI failures.'));
  }
  async function reviewSource(incident) {
    try {
      if (!token) {
        const r = await fetch('/api/launcher'); if (!r.ok) throw Error('Dashboard connection unavailable');
        token = (await r.json()).token;
      }
      const r = await fetch('/api/maintenance/source?key=' + encodeURIComponent(incident.key),
        {headers: {'X-Launch-Token': token}});
      const evidence = await r.json(); if (!r.ok) throw Error(evidence.error || 'Source evidence unavailable');
      const dialog = el('dialog', undefined, 'maintenance-source-dialog');
      dialog.setAttribute('aria-label', 'Review protected main source repair');
      const diff = el('pre', evidence.diff);
      const close = button('Close source review', () => dialog.close());
      const publish = button('Publish this reviewed source repair', () => {
        dialog.close(); post('authorize-source', {key: incident.key, candidate: evidence.candidate,
                                                source_manifest: evidence.source_manifest});
      }, evidence.truncated);
      dialog.append(el('h2', 'Review protected main source repair'),
        el('p', evidence.summary), el('p', `Candidate ${evidence.candidate}`),
        el('p', 'Publication can start GitHub Actions from this source. Review the diff and validation before authorizing.'),
        el('p', 'Validation: ' + (evidence.validation || []).join('; ')), diff, close, publish);
      document.body.append(dialog); dialog.addEventListener('close', () => dialog.remove(), {once: true});
      dialog.showModal(); close.focus();
    } catch (error) { message = error.message; render(true); }
  }
  async function poll() {
    if (polling || busy) return;
    polling = true;
    try {
      const r = await fetch('/api/maintenance', {signal: AbortSignal.timeout(15000)});
      if (!r.ok) throw Error('Maintenance status unavailable');
      data = await r.json(); render();
    } catch {
      message = 'Maintenance status unavailable. Saved operations are retained.';
      if (data) data = {...data, fresh: false, inventory: {...data.inventory, error: message}};
      render(true);
    }
    finally { polling = false; }
  }
  document.getElementById('main-ci-open').onclick = () => window.agentopsSelectView('main-ci');
  document.addEventListener('agentops-view', event => {
    if (!['updates', 'main-ci'].includes(event.detail.view)) return;
    view = event.detail.view; render(true);
    poll().then(() => { if (view === 'updates' && data && !data.inventory?.fetched) post('refresh', {}); });
  });
  poll(); setInterval(() => { if (!document.hidden) poll(); }, 5000);
})();
