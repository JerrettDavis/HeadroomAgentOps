/* Local Markdown rendering for untrusted GitHub PR descriptions. */
function renderMarkdown(source, context = {}) {
  const parsed = marked.parse(String(source ?? ''), {gfm: true, breaks: false});
  const fragment = DOMPurify.sanitize(parsed, {
    RETURN_DOM_FRAGMENT: true,
    ALLOWED_TAGS: ['p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
      'blockquote', 'pre', 'code', 'em', 'strong', 'del', 'a', 'ul', 'ol', 'li',
      'table', 'thead', 'tbody', 'tr', 'th', 'td', 'img', 'details', 'summary',
      'sup', 'sub', 'kbd', 'samp', 'input'],
    ALLOWED_ATTR: ['href', 'title', 'src', 'alt', 'width', 'height', 'align',
      'start', 'reversed', 'type', 'checked', 'disabled', 'class'],
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
  });
  for (const input of fragment.querySelectorAll('input')) {
    if (input.type !== 'checkbox') {
      input.remove();
      continue;
    }
    input.disabled = true;
    input.tabIndex = -1;
  }
  for (const anchor of fragment.querySelectorAll('a[href]')) {
    // Resolve GitHub-relative links at their source rather than the local app.
    try {
      const base = context.repo && context.headSha
        ? `https://github.com/${context.repo}/blob/${context.headSha}/` : undefined;
      const url = new URL(anchor.getAttribute('href'), base);
      if (!['https:', 'http:', 'mailto:'].includes(url.protocol)) throw Error('Unsupported link');
      anchor.href = url.href;
    } catch {
      anchor.removeAttribute('href');
      continue;
    }
    anchor.target = '_blank';
    anchor.rel = 'noopener noreferrer';
  }
  for (const image of fragment.querySelectorAll('img')) {
    try {
      const base = context.repo && context.headSha
        ? `https://raw.githubusercontent.com/${context.repo}/${context.headSha}/` : undefined;
      const url = new URL(image.getAttribute('src') || '', base);
      if (url.protocol !== 'https:') throw Error('Unsupported image');
      image.src = url.href;
    } catch {
      image.remove();
      continue;
    }
    image.loading = 'lazy';
    image.referrerPolicy = 'no-referrer';
  }
  const container = document.createElement('div');
  container.append(fragment);
  return container.innerHTML;
}
