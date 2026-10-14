const vscode = require('vscode');
const fs = require('node:fs');
exports.activate = async function (context) {
  const commands = await vscode.commands.getCommands(true);
  const report = {
    uiKind: vscode.env.uiKind,
    remoteName: vscode.env.remoteName,
    newLocalRegistered: commands.includes('workbench.action.terminal.newLocal'),
  };
  try {
    await vscode.commands.executeCommand('workbench.action.terminal.newLocal');
    report.newLocal = 'resolved';
  } catch (e) { report.newLocal = String(e); }
  // Only a fixed disposable sentinel is probed. Never read real host files.
  try {
    await vscode.workspace.fs.readFile(vscode.Uri.file('/tmp/browser-ide-host-sentinel'));
    report.localFile = 'readable';
  } catch (e) { report.localFile = String(e); }
  fs.writeFileSync('/tmp/boundary-report.json', JSON.stringify(report, null, 2));
  context.subscriptions.push(vscode.commands.registerCommand('demo.boundaryProbe', () => {
    const panel = vscode.window.createWebviewPanel('boundaryProbe', 'Boundary probe', vscode.ViewColumn.One, {enableScripts:true});
    // Deliberately no restrictive CSP: exercise a compromised remote extension.
    panel.webview.html = `<!doctype html><h1>Remote extension boundary probe</h1>
      <pre>${JSON.stringify(report, null, 2)}</pre>
      <button id="post">Send harmless POST from webview</button><pre id="result"></pre>
      <script>document.querySelector('#post').onclick=async()=>{try{const r=await fetch('http://receiver.localhost:9000/webview',{method:'POST',mode:'no-cors',body:'DEMO_ONLY'});document.querySelector('#result').textContent=r.type}catch(e){document.querySelector('#result').textContent=String(e)}};</script>`;
  }));
};
