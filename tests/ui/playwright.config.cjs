const {defineConfig} = require('@playwright/test');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
process.env.AGENTOPS_UI_PORT ||= execFileSync('python', ['-c',
  "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1]); s.close()"],
  {encoding: 'utf8'}).trim();
const port = Number(process.env.AGENTOPS_UI_PORT);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw Error('Invalid UI test port');
const baseURL = `http://127.0.0.1:${port}`;

module.exports = defineConfig({
  testDir: '.',
  testMatch: '*.spec.cjs',
  workers: 1,
  timeout: 30000,
  use: {baseURL, browserName: 'chromium'},
  webServer: {
    command: `python tests/ui/serve.py --port ${port}`,
    cwd: path.resolve(__dirname, '../..'),
    url: baseURL,
    reuseExistingServer: false,
  },
});
