# Bundled browser libraries

These files are served locally. The dashboard makes no CDN requests.

- `marked.js`: marked 18.2.0, `lib/marked.umd.js`, MIT license.
  Source: https://registry.npmjs.org/marked/18.2.0
  License: `marked-LICENSE.txt`.
- `purify.js`: DOMPurify 3.4.17, `dist/purify.min.js`, Apache-2.0 OR MPL-2.0.
  Source: https://registry.npmjs.org/dompurify/3.4.17
  License: `dompurify-LICENSE.txt`.

Each tarball's SHA-512 digest was checked against its npm registry integrity
metadata before copying these specific files. Keep versions pinned and licenses
alongside the libraries when updating. The app's own `markdown.js` applies a
restricted HTML allowlist after parsing and disables task-list checkboxes.
