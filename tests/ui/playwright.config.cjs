const {defineConfig} = require('@playwright/test');
const path = require('node:path');

module.exports = defineConfig({
  testDir: '.',
  testMatch: '*.spec.cjs',
  workers: 1,
  timeout: 30000,
  use: {baseURL: 'http://127.0.0.1:18766', browserName: 'chromium'},
  webServer: {
    command: 'python tests/ui/serve.py',
    cwd: path.resolve(__dirname, '../..'),
    url: 'http://127.0.0.1:18766',
    reuseExistingServer: false,
  },
});
