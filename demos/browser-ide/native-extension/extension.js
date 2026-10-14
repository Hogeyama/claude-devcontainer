const vscode = require('vscode');
const fs = require('fs');
const path = require('path');
const cp = require('child_process');
const net = require('net');
async function run() {
  const root = process.env.BROWSER_IDE_PROBE_ROOT;
  if (!root) return;
  const report = {pid:process.pid,remoteName:vscode.env.remoteName || null,steps:[]};
  const save=()=>fs.writeFileSync(path.join(root,'native-extension.json'),JSON.stringify(report,null,2));
  save();
  for(const operation of ['read','write']) {
    try { if(operation==='read')fs.readFileSync(process.env.BROWSER_IDE_HOST_CANARY);else fs.writeFileSync(process.env.BROWSER_IDE_HOST_CANARY+'.attempt','probe');report[operation]='unexpected-success'; }
    catch(e){report[operation]=e.code;}
  }
  save();
  report.directTcp=await new Promise(resolve=>{
    const socket=net.connect(18082,'127.0.0.1');
    const finish=value=>{socket.destroy();resolve(value)};
    socket.on('connect',()=>finish('connected'));socket.on('error',e=>finish(e.code));setTimeout(()=>finish('timeout'),1500);
  });
  save();
  report.child=await new Promise(resolve=>{
    const child=cp.execFile('C:\\Windows\\System32\\cmd.exe',['/d','/c','echo child-ok'],{timeout:5000},(error,stdout,stderr)=>resolve({pid:child.pid,error:error&&error.message,stdout,stderr}));
  });save();
  if(vscode.env.remoteName){report.terminalTest='delegated-to-remote-extension';save();return;}
  try {
    const commands=await vscode.commands.getCommands(true);
    const command=commands.includes('workbench.action.terminal.newLocal')?'workbench.action.terminal.newLocal':'workbench.action.terminal.new';
    report.terminalCommand=command;save();
    await vscode.commands.executeCommand(command);report.steps.push(command);save();
    const terminal=vscode.window.activeTerminal;
    report.terminalPid=await Promise.race([terminal.processId,new Promise(resolve=>setTimeout(()=>resolve('timeout'),8000))]);
    await new Promise(resolve=>setTimeout(resolve,3000));
    await vscode.commands.executeCommand('workbench.action.terminal.sendSequence',{text:'echo terminal-ok > '+path.join(root,'terminal-result.txt')+'\r'});
    report.steps.push('sendSequence');
  }catch(e){report.terminalError=String(e);}
  save();
};
exports.activate = () => { setTimeout(() => run().catch(error => {
  fs.writeFileSync(path.join(process.env.BROWSER_IDE_PROBE_ROOT, "native-error.txt"),String(error.stack || error));
}), 2000); };
