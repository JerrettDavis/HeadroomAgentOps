/* Read-only source discovery. No source search downloads or executes code. */
(() => {
  const host = document.getElementById('benchmark-view');
  const studio = host?.querySelector('.studio');
  if (!studio) return;
  const section = document.createElement('details');
  section.className = 'studio studio-source-finder';
  section.innerHTML = `<summary>Find commit and build sources</summary><p>Search commits by SHA prefix, message or author, or find a pull request by title. Use <code>author:login</code> to filter an author, or <code>#123</code> to list a pull request’s commits.</p><div class="studio-source-controls"><label class="studio-input">Repository<input name="source-repository" placeholder="owner/repository" autocomplete="off"></label><label class="studio-input">Search type<select name="source-kind"><option value="commits">Commits</option><option value="prs">Pull requests</option></select></label><label class="studio-input">Find a source<input name="source-query" type="search" placeholder="Partial SHA, commit message, author:login, or #PR" role="combobox" aria-autocomplete="list" aria-controls="studio-source-results" aria-expanded="false" autocomplete="off"></label><button type="button" data-source-search>Search sources</button></div><p data-source-status role="status"></p><div id="studio-source-results" role="listbox" aria-label="Matching sources"></div><button type="button" data-source-more hidden>Next search page</button><section data-source-selection aria-label="Selected source"></section>`;
  studio.after(section);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const input = name => section.querySelector(`[name="source-${name}"]`);
  const results = section.querySelector('[role=listbox]');
  const status = section.querySelector('[data-source-status]');
  let initialized = false, page = 1, timer, controller, generation = 0, selection;
  async function request(path, data, signal) {
    const auth = await fetch('/api/launcher',{signal});
    if (!auth.ok) throw Error('Dashboard authentication unavailable. Refresh and try again.');
    const token = await auth.json();
    const response = await fetch(path,{signal,method:data ? 'POST':'GET',headers:{'Content-Type':'application/json','X-Launch-Token':token.token || token.launch_token || ''},...(data ? {body:JSON.stringify(data)} : {})});
    const value = await response.json();
    if (!response.ok) throw Error(value.error || 'Source search unavailable. Check repository access and GitHub authentication.');
    return value;
  }
  function invalidate() {
    ++generation; controller?.abort(); clearTimeout(timer); page=1;
    results.replaceChildren(); input('query').setAttribute('aria-expanded','false');
    input('query').removeAttribute('aria-activedescendant');
    section.querySelector('[data-source-more]').hidden=true;
    section.querySelector('[data-source-selection]').replaceChildren(); selection=null;
  }
  async function search() {
    const version=++generation;
    controller?.abort(); controller=new AbortController();
    status.textContent='Searching repository sources…';
    try {
      const data=await request('/api/benchmarks/studio/sources',{repository:input('repository').value.trim(),query:input('query').value.trim(),kind:input('kind').value,page},controller.signal);
      if (version !== generation) return;
      results.innerHTML=data.items.map((item,index)=>`<button type="button" role="option" aria-selected="false" id="studio-source-${index}" ${item.sha ? `data-sha="${esc(item.sha)}"` : `data-pr="${esc(item.number)}"`}><strong>${esc(item.sha ? item.sha.slice(0,10) : '#'+item.number)}</strong><span>${esc(item.subject)}</span><small>${esc(item.author)} · ${esc(item.date)}</small></button>`).join('');
      status.textContent=data.items.length ? `${data.items.length} matches on page ${page}. Select a commit to inspect available artifacts.` : 'No matching sources on this page. Try another query or repository.';
      input('query').setAttribute('aria-expanded',String(Boolean(data.items.length)));
      input('query').removeAttribute('aria-activedescendant');
      section.querySelector('[data-source-more]').hidden=!data.has_more;
    } catch(error) { if (error.name !== 'AbortError' && version===generation) status.textContent=error.message; }
  }
  async function artifacts(artifactPage=1) {
    const version=++generation;
    controller?.abort(); controller=new AbortController();
    const selected=section.querySelector('[data-source-selection]');
    selected.innerHTML=`<h4>Pinned commit</h4><code>${esc(selection.sha)}</code><p>Loading GitHub Actions artifacts…</p>`;
    try {
      const data=await request('/api/benchmarks/studio/artifacts',{repository:selection.repository,sha:selection.sha,page:artifactPage},controller.signal);
      if(version!==generation) return;
      selected.innerHTML=`<h4>Pinned commit</h4><p>${esc(selection.repository)}</p><code>${esc(selection.sha)}</code><p>Available GitHub Actions artifacts</p>${data.items.map(a=>`<article class="studio-source-artifact"><strong>${esc(a.name)}</strong><span>${esc(a.size_in_bytes)} bytes · run ${esc(a.run_id)}</span><small>${a.expired ? 'Expired' : 'Available'} · expires ${esc(a.expires_at)}</small></article>`).join('') || '<p>No matching artifacts on this page.</p>'}<p class="studio-note">${esc(data.notice)}</p>${data.has_more ? `<button type="button" data-artifact-page="${artifactPage+1}">Search older artifacts</button>` : ''}<button type="button" data-copy-source>Copy full SHA</button>`;
    } catch(error) { if(error.name!=='AbortError' && version===generation) selected.textContent=error.message; }
  }
  section.addEventListener('toggle', async()=>{
    if (!section.open || initialized) return;
    try { const state=await request('/api/benchmarks/studio'); input('repository').value=state.default_repository || ''; initialized=true; }
    catch(error) { status.textContent=error.message; }
  });
  input('query').addEventListener('input',()=>{
    invalidate();
    if(input('query').value.trim().length>=2) timer=setTimeout(search,300);
    else status.textContent='Type at least two characters, or use Search sources to browse recent commits.';
  });
  input('repository').addEventListener('input',invalidate);
  input('kind').addEventListener('change',()=>{invalidate(); search();});
  input('query').addEventListener('keydown',event=>{
    const items=[...results.querySelectorAll('[role=option]')];
    let index=items.findIndex(item=>item.id===input('query').getAttribute('aria-activedescendant'));
    if(event.key==='ArrowDown' || event.key==='ArrowUp') {
      event.preventDefault(); if(!items.length)return;
      index=(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;
      items.forEach((item,i)=>item.setAttribute('aria-selected',String(i===index)));
      input('query').setAttribute('aria-activedescendant',items[index].id);items[index].scrollIntoView({block:'nearest'});
    } else if(event.key==='Enter') {event.preventDefault(); if(index>=0)items[index].click();else search();}
    else if(event.key==='Escape') {invalidate();}
  });
  section.addEventListener('click',async event=>{
    const button=event.target.closest('button');if(!button)return;
    if(button.hasAttribute('data-source-search')){invalidate();search();}
    else if(button.hasAttribute('data-source-more')){++page;search();}
    else if(button.dataset.pr){input('kind').value='commits';input('query').value='#'+button.dataset.pr;invalidate();search();}
    else if(button.dataset.sha){selection={repository:input('repository').value.trim(),sha:button.dataset.sha};results.querySelectorAll('[role=option]').forEach(item=>item.setAttribute('aria-selected',String(item===button)));await artifacts();}
    else if(button.dataset.artifactPage)await artifacts(Number(button.dataset.artifactPage));
    else if(button.hasAttribute('data-copy-source')){try{await navigator.clipboard.writeText(selection.sha);status.textContent='Full SHA copied.';}catch{status.textContent='Select and copy the full SHA shown below.';}}
  });
})();
