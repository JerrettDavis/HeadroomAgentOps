/* Current merge suggestions are independent of the saved review session. */
(() => {
  const byId = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const githubLink = (url, label, className = '') => {
    try {
      const target = new URL(url);
      if (target.protocol === 'https:' && target.hostname === 'github.com' && !target.username && !target.password)
        return `<a class="${className}" href="${escape(target.href)}" target="_blank" rel="noopener noreferrer">${escape(label)}</a>`;
    } catch {}
    return `<span class="${className}">${escape(label)}</span>`;
  };
  const tag = (text, kind = '') => `<span class="badge ${kind}">${escape(text)}</span>`;
  let snapshot = null, busy = false, lastRender = '', view = 'priority';
  try {
    const saved = localStorage.getItem('agentops-view');
    if (['reviews', 'benchmarks'].includes(saved)) view = saved;
  } catch {}

  function render() {
    if (!snapshot) return;
    const d = snapshot, fresh = d.fresh === true;
    const signature = JSON.stringify(d);
    if (signature === lastRender) return;
    lastRender = signature;
    const scrollTop = byId('priority-view').scrollTop;
    const rows = (d.prs || []).filter(row => row.review_decision === 'APPROVED'
      && row.ci !== 'failed' && row.mergeable !== 'CONFLICTING' && row.merge_state !== 'DIRTY');
    const visible = new Set(rows.map(row => row.number));
    const ready = fresh ? rows.filter(r => r.ready).length : 0;
    byId('priority-count').textContent = `${ready} ready now / ${rows.length} approved`;
    const observation = d.observed_at ? `Observed ${new Date(d.observed_at * 1000).toLocaleString()}` : 'No GitHub observation yet';
    byId('priority-status').textContent = [d.repo, observation,
      d.error || (!fresh ? (d.refreshing ? 'Refreshing; previous evidence is unverified.' : 'Evidence is unverified.') :
        `Checks GitHub every ${d.refresh_seconds || 60}s while this view is open.`),
      byId('auto').getAttribute('aria-pressed') === 'false' ? 'Automatic refresh paused.' : ''].filter(Boolean).join(' · ');
    byId('priority-status').classList.toggle('error', !!d.error);
    // A compact, actually nested stack outline preserves the full visual tree
    // even when other higher-priority PRs intervene in the numbered merge order.
    const children = new Map();
    for (const row of rows) {
      if (row.parent != null && visible.has(row.parent)) {
        if (!children.has(row.parent)) children.set(row.parent, []);
        children.get(row.parent).push(row);
      }
    }
    const roots = rows.filter(row => !visible.has(row.parent) && children.has(row.number));
    const outline = [];
    const tasks = [{kind: 'text', value: '</ul>'}, ...roots.slice().reverse().map(row => ({kind: 'row', row})), {kind: 'text', value: '<ul>'}];
    while (tasks.length) {
      const item = tasks.pop();
      if (item.kind === 'text') { outline.push(item.value); continue; }
      const row = item.row;
      outline.push(`<li><small>${fresh && row.merge_order != null ? 'Merge ' + escape(row.merge_order) : 'Waiting'}</small>${githubLink(row.url, '#' + row.number + ' ' + row.title)}`);
      tasks.push({kind: 'text', value: '</li>'});
      const next = children.get(row.number) || [];
      if (next.length) tasks.push({kind: 'text', value: '</ul>'}, ...next.slice().reverse().map(row => ({kind: 'row', row})), {kind: 'text', value: '<ul>'});
    }
    byId('priority-stacks').hidden = !roots.length;
    byId('priority-stack-list').innerHTML = outline.join('');
    byId('priority-list').innerHTML = rows.map(row => {
      const readyNow = fresh && row.ready, after = fresh && row.ready_after_dependencies;
      const state = readyNow ? 'Ready now' : after ? 'After prerequisites' : 'Needs attention';
      const blockers = fresh ? row.blockers || [] : ['Evidence is unverified; waiting for a successful refresh'];
      const deps = (row.dependencies || []).map(dep => githubLink(dep.url,
        `${dep.repo === d.repo?.toLowerCase() ? '' : dep.repo}#${dep.number}`) +
        (dep.state === 'merged' ? ' (merged)' : dep.approved === false ? ' (not approved)' : dep.state !== 'open' ? ` (${escape(dep.state)})` : '')).join(', ');
      const depth = Math.min(5, Math.max(0, Number(row.depth) || 0));
      const title = `#${row.number} ${row.title}`;
      return `<li class="priority-row" data-number="${escape(row.number)}" data-parent="${escape(row.parent ?? '')}" data-ready="${readyNow}" style="--depth:${depth}">
        <span class="priority-order" aria-label="${readyNow || after ? 'Suggested merge position ' + escape(row.merge_order) : 'No merge position yet'}">${readyNow || after ? escape(row.merge_order) : '—'}</span>
        <div class="priority-body"><label class="stack-select"><input type="checkbox" data-stack-number="${escape(row.number)}" aria-label="Include PR #${escape(row.number)} in stack" ${readyNow || after ? '' : 'disabled'} ${window.agentopsStacks?.selected.has(row.number) ? 'checked' : ''}> Include in stack</label>${githubLink(row.url, title, 'priority-title')}
        <p class="priority-meta"><span>${escape(row.author)}</span><span>${escape(row.head_ref)} → ${escape(row.base_ref)}</span>${row.depth > 5 ? `<span>Stack depth ${escape(row.depth)}</span>` : ''}</p>
        <div class="badges"><span class="priority-state ${readyNow ? 'good' : ''}">${state}</span>${tag(row.category)}${tag('CI ' + (row.ci || 'unknown'), row.ci === 'failed' ? 'bad' : '')}${tag(row.up_to_date === true ? 'Up to date with base' : row.up_to_date === false ? 'Behind base' : 'Freshness unknown')}${row.draft ? tag('Draft') : ''}${row.review_decision ? tag(row.review_decision.replaceAll('_', ' ').toLowerCase()) : ''}</div>
        ${deps ? `<p class="priority-dependencies">Prerequisites: ${deps}</p>` : ''}
        <p class="priority-reasons">${escape(blockers.length ? blockers.join(' · ') : row.category_reason)}${row.category === 'Main CI fix (declared)' ? ' · Main CI repair is declared; verify the fix during review.' : ''}</p></div></li>`;
    }).join('') || `<li class="empty">${fresh ? 'No approved open PRs without conflicts or failed CI.' : 'Waiting for current approved GitHub PRs. This view needs GitHub sign-in and repository access.'}</li>`;
    byId('priority-view').scrollTop = scrollTop;
    document.dispatchEvent(new CustomEvent('priority-rendered', {detail: {fresh,
      eligible: rows.filter(r => fresh && (r.ready || r.ready_after_dependencies)).map(r => r.number)}}));
  }

  async function refreshPriority() {
    if (busy || view !== 'priority') return;
    busy = true;
    try {
      const response = await fetch('/api/merge-priority', {signal: AbortSignal.timeout(15000)});
      if (!response.ok) throw Error('Refresh unavailable');
      snapshot = await response.json();
    } catch {
      snapshot = {...(snapshot || {prs: []}), fresh: false, refreshing: false,
        error: 'Unable to refresh merge priority. Check the panel connection; retrying automatically.'};
    } finally { busy = false; render(); }
  }

  function selectView(next) {
    view = ['priority', 'reviews', 'benchmarks'].includes(next) ? next : 'priority';
    const priority = view === 'priority';
    const reviews = view === 'reviews';
    byId('priority-view').hidden = !priority;
    byId('reviews-workspace').hidden = !reviews;
    byId('benchmark-view').hidden = view !== 'benchmarks';
    byId('metrics').hidden = !reviews;
    document.querySelector('.statusline').hidden = !reviews;
    byId('run').hidden = !reviews;
    byId('newreview').hidden = view === 'benchmarks';
    byId('view-priority').setAttribute('aria-pressed', String(priority));
    byId('view-reviews').setAttribute('aria-pressed', String(reviews));
    byId('view-benchmarks').setAttribute('aria-pressed', String(view === 'benchmarks'));
    document.title = `Headroom AgentOps · ${{priority: 'Merge priority', reviews: 'Reviews', benchmarks: 'Benchmarking'}[view]}`;
    try { localStorage.setItem('agentops-view', view); } catch {}
    document.dispatchEvent(new CustomEvent('agentops-view', {detail: {view}}));
    if (priority) refreshPriority();
  }
  window.agentopsSelectView = selectView;
  byId('view-priority').onclick = () => selectView('priority');
  byId('view-reviews').onclick = () => selectView('reviews');
  byId('view-benchmarks').onclick = () => selectView('benchmarks');
  byId('refresh').addEventListener('click', refreshPriority);
  byId('auto').addEventListener('click', () => { lastRender = ''; render(); });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && byId('auto').getAttribute('aria-pressed') === 'true') refreshPriority();
  });
  setInterval(() => {
    // Expire ready indicators locally even if polling is paused or disconnected.
    if (snapshot?.fresh && Date.now() / 1000 - snapshot.observed_at >= (snapshot.freshness_seconds || 180)) {
      snapshot = {...snapshot, fresh: false};
      render();
    }
    if (!document.hidden && byId('auto').getAttribute('aria-pressed') === 'true') refreshPriority();
  }, 5000);
  selectView(view);
})();
