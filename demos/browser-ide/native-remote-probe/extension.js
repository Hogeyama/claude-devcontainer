const vscode=require('vscode');
const fs=require('fs');
exports.activate=()=>{setTimeout(async()=>{
  const report={pid:process.pid,platform:process.platform,remoteName:vscode.env.remoteName,container:fs.existsSync('/.dockerenv'),steps:[]};
  const save=()=>fs.writeFileSync('/tmp/remote-terminal-probe.json',JSON.stringify(report,null,2));
  save();
  try {
    await vscode.commands.executeCommand('workbench.action.terminal.newLocal');report.steps.push('newLocal');save();
    report.localTerminalPid=await Promise.race([vscode.window.activeTerminal.processId,new Promise(r=>setTimeout(()=>r('timeout'),8000))]);
    await new Promise(resolve=>setTimeout(resolve,5000));
    await vscode.commands.executeCommand('workbench.action.terminal.sendSequence',{text:"[IO.File]::WriteAllText((Join-Path $env:TEMP 'remote-triggered.txt'),'remote-triggered')\r"});
    report.steps.push('sendSequence');
  }catch(e){report.error=String(e);}
  save();
},3000)};
