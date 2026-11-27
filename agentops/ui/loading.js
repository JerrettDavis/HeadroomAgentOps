/* Placeholders describe pending data; they never stand in for empty results. */
(() => {
  function skeleton(label, rows = 3) {
    const box = document.createElement('div');
    box.className = 'loading-placeholder';
    const status = document.createElement('p');
    status.className = 'loading-caption';
    status.setAttribute('role', 'status');
    status.textContent = label;
    box.append(status);
    const shapes = document.createElement('div');
    shapes.setAttribute('aria-hidden', 'true');
    for (let i = 0; i < rows; i++) {
      const row = document.createElement('div');
      row.className = 'skeleton-row';
      row.innerHTML = '<span class="skeleton-line"></span><span class="skeleton-line short"></span>';
      shapes.append(row);
    }
    box.append(shapes);
    return box.outerHTML;
  }
  window.agentopsLoading = {skeleton};
  const metrics = document.getElementById('metrics');
  if (metrics && !metrics.hasChildNodes()) {
    for (const label of ['Review progress', 'PR states', 'Agent activity', 'Token usage', 'Recorded spend']) {
      const metric = document.createElement('div');
      metric.className = 'metric';
      metric.innerHTML = '<span class="skeleton-line" aria-hidden="true"></span>';
      const caption = document.createElement('small');
      caption.textContent = label + ' · loading';
      metric.append(caption);
      metrics.append(metric);
    }
  }
  for (const [id, label, rows] of [
    ['queue', 'Loading review queue…', 4],
    ['detail', 'Loading review details…', 3],
    ['agents', 'Loading agent sessions…', 2],
    ['usage', 'Loading token and spend report…', 2],
    ['activity', 'Loading publication activity…', 2]
  ]) {
    const target = document.getElementById(id);
    if (target && !target.hasChildNodes()) target.innerHTML = skeleton(label, rows);
  }
})();
