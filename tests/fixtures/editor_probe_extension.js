'use strict';
// Runs in the real remote extension host, after the workbench connects.
const vscode = require('vscode');
const fs = require('fs');

exports.activate = async function activate(context) {
  const result = { connected: true };
  const save = () => fs.writeFileSync(process.env.EDITOR_PROBE_RESULT, JSON.stringify(result));
  save();
  try {
    const panel = vscode.window.createWebviewPanel('migrationProbe', 'Webview probe', vscode.ViewColumn.One,
      { enableScripts: true });
    context.subscriptions.push(panel);
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('webview did not answer')), 30000);
      panel.webview.onDidReceiveMessage((message) => {
        if (message === 'webview-ready') {
          clearTimeout(timeout);
          resolve();
        }
      });
      panel.webview.html = '<!doctype html><h1>Webview probe</h1>'
        + '<script>acquireVsCodeApi().postMessage("webview-ready")</script>';
    });
    result.webview = true;
    save();
    panel.dispose();
    const app = vscode.Uri.parse('http://localhost:3000/');
    result.externalUri = (await vscode.env.asExternalUri(app)).toString();
    save();
    await vscode.commands.executeCommand('simpleBrowser.show', app.toString());
    await vscode.commands.executeCommand('~remote.forwardedPorts.focus');
    await vscode.commands.executeCommand('remote.tunnel.forwardInline');
    // Wait until the browser has actually rendered the direct app URL.
    const deadline = Date.now() + 30000;
    while (!fs.existsSync(process.env.EDITOR_PROBE_DIRECT)) {
      if (Date.now() > deadline) throw new Error('direct preview did not render');
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    await vscode.commands.executeCommand('simpleBrowser.show', result.externalUri + '?forwarded=1');
    result.commands = true;
    save();
  } catch (error) {
    result.error = String(error.stack || error);
    save();
  }
};
