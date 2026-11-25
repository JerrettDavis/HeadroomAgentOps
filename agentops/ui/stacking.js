/* Mechanical PR preparation; publication always requires a separate human action. */
(() => {
  const selected = new Set();
  window.agentopsStacks = {selected};
  const byId = id => document.getElementById(id);
  let eligible = new Set(), inventoryFresh = false, busy = false, polling = false;
  let snapshot = {stacks: [], publish_enabled: false}, signature = '', connected = false;
  const text = (tag, value, cls) => {
    const el = document.createElement(tag);
    el.textContent = value ?? '';
    if (cls) el.className = cls;
    return el;
  };
  function link(url, label) {
    try {
      const target = new URL(url);
      if (target.protocol === 'https:' && target.hostname === 'github.com' && !target.username && !target.password) {
        const el = text('a', label);
        el.href = target.href; el.target = '_blank'; el.rel = 'noopener noreferrer';
        return el;
      }
    } catch {}
    return text('span', label);
  }
  function selection() {
    const numbers = [...selected].sort((a, b) => a - b);
    byId('stack-selection').textContent = numbers.length ? numbers.map(n => '#' + n).join(', ') : 'Select PRs below. Prerequisites are included automatically.';
    byId('stack-prepare').disabled = busy || !inventoryFresh || !numbers.length || numbers.some(n => !eligible.has(n));
  }
  document.addEventListener('priority-rendered', event => {
    eligible = new Set(event.detail.eligible); inventoryFresh = event.detail.fresh;
    selection();
  });
  document.addEventListener('change', event => {
    const input = event.target.closest('[data-stack-number]');
    if (!input) return;
    const number = Number(input.dataset.stackNumber);
    input.checked ? selected.add(number) : selected.delete(number);
    selection();
  });
  function render(force = false) {
    selection();
    const next = JSON.stringify([snapshot, busy, connected]);
    if (!force && next === signature) return;
    signature = next;
    const list = byId('prepared-stacks');
    list.replaceChildren();
    for (const record of snapshot.stacks || []) {
      const card = text('article', '', 'prepared-stack');
      card.append(text('h3', `${record.id.slice(0, 10)} · ${record.state}`));
      card.append(text('p', record.phase || ''));
      card.append(text('p', `Target: ${record.target_branch} @ ${record.target_sha}`, 'stack-revision'));
      if (record.revision) card.append(text('p', `Combined: ${record.revision}`, 'stack-revision'));
      if (record.verified_at) card.append(text('p', `Evidence checked ${new Date(record.verified_at * 1000).toLocaleString()}${record.fresh ? '' : ' · Recheck required'}`, 'muted'));
      const members = text('ol', '');
      for (const p of record.members || []) {
        const row = text('li', '');
        row.append(link(`https://github.com/${record.repo}/pull/${p.number}`, `#${p.number} ${p.title || ''}`));
        row.append(text('small', ` @${p.author || ''} · ${p.head_sha} · source CI ${p.ci || 'unknown'} · ${p.review_decision || 'approval unknown'}`));
        members.append(row);
      }
      card.append(members);
      if (record.included_prerequisites?.length) card.append(text('p', 'Included prerequisites: ' + record.included_prerequisites.map(n => '#' + n).join(', ')));
      for (const blocker of record.blockers || []) card.append(text('p', blocker, 'error'));
      if (record.conflicts?.length) card.append(text('p', `Conflict at #${record.conflicting_pr}: ${record.conflicts.join(', ')}`, 'error'));
      if (record.error) card.append(text('p', record.error, 'error'));
      if (record.validation) {
        card.append(text('p', 'Combined validation: ' + record.validation.state));
        for (const result of record.validation.commands || []) {
          const details = text('details', '');
          details.append(text('summary', `${result.argv.join(' ')} · ${result.timed_out ? 'timed out' : 'exit ' + result.exit_code}`));
          details.append(text('pre', result.log)); card.append(details);
        }
      }
      if (record.pr_url) {
        card.append(link(record.pr_url, 'Review and merge on GitHub'));
        if (record.aggregate) card.append(text('p', `Aggregate: ${record.aggregate.merged ? 'merged' : record.aggregate.state}${record.aggregate.draft ? ' · draft' : ''}`));
        if (record.source_cleanup?.length) card.append(text('p', 'Source PRs still open; review for cleanup: ' + record.source_cleanup.map(n => '#' + n).join(', ')));
      }
      const controls = text('div', '', 'stack-actions');
      if (['prepared', 'publication_unknown'].includes(record.state)) {
        const publish = text('button', record.state === 'publication_unknown' ? 'Recover draft publication' : 'Publish draft PR');
        publish.disabled = busy || !connected || !snapshot.publish_enabled || (record.state !== 'publication_unknown' && !record.fresh);
        publish.onclick = () => action('publish', {id: record.id});
        controls.append(publish);
      }
      if (['prepared', 'stale', 'published', 'publication_unknown'].includes(record.state)) {
        const refresh = text('button', 'Recheck stack');
        refresh.disabled = busy || !connected;
        refresh.onclick = () => action('refresh', {id: record.id}); controls.append(refresh);
      }
      if (['failed', 'interrupted', 'conflict', 'blocked', 'validation_failed', 'stale'].includes(record.state)) {
        const retry = text('button', 'Prepare again');
        retry.disabled = busy || !connected;
        retry.onclick = () => action('prepare', {numbers: record.selected}); controls.append(retry);
      }
      card.append(controls); list.append(card);
    }
  }
  async function refresh() {
    if (polling) return;
    polling = true;
    try {
      const response = await fetch('/api/stacks', {signal: AbortSignal.timeout(15000)});
      if (!response.ok) throw Error('Stack status unavailable');
      snapshot = await response.json(); connected = true;
      for (const r of snapshot.stacks || []) if (r.verified_at && Date.now()/1000 - r.verified_at >= 180) r.fresh = false;
    } catch {
      connected = false;
      byId('stack-status').textContent = 'Stack status unavailable. Reconnect and refresh before publishing.';
    } finally { polling = false; render(); }
  }
  async function action(name, payload) {
    if (busy) return;
    busy = true; render();
    byId('stack-status').textContent = name === 'prepare' ? 'Checking source PRs and preparing stack…' : 'Checking current evidence…';
    try {
      const launcherResponse = await fetch('/api/launcher', {signal: AbortSignal.timeout(15000)});
      if (!launcherResponse.ok) throw Error('Panel authorization unavailable');
      const launcher = await launcherResponse.json();
      const response = await fetch('/api/stacks/' + name, {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Launch-Token': launcher.token},
        body: JSON.stringify(payload), signal: AbortSignal.timeout(180000)});
      const result = await response.json();
      if (!response.ok) throw Error(result.error || 'Stack action failed');
      byId('stack-status').textContent = result.state === 'preparing' ? 'Preparing in the background. Progress appears below.' : 'Stack ' + result.state + '.';
    } catch (error) {
      byId('stack-status').textContent = error.name === 'TimeoutError' ?
        'Observation timed out. The operation may still be running; inspect saved status before retrying.' : error.message;
    } finally { busy = false; await refresh(); render(); }
  }
  byId('stack-prepare').onclick = () => action('prepare', {numbers: [...selected].sort((a,b) => a-b)});
  byId('refresh').addEventListener('click', refresh);
  setInterval(() => {
    for (const record of snapshot.stacks || []) {
      if (record.fresh && Date.now()/1000 - record.verified_at >= 180) { record.fresh = false; render(); }
    }
    if (!document.hidden && !byId('priority-view').hidden && byId('auto').getAttribute('aria-pressed') === 'true') refresh();
  }, 5000);
  refresh(); selection();
})();
