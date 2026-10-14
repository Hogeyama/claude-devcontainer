const vscode=require('vscode');
const net=require('net');
exports.activate=context=>context.subscriptions.push(vscode.workspace.registerRemoteAuthorityResolver('isolated-demo',{
  resolve:()=>new vscode.ManagedResolvedAuthority(()=>new Promise((resolve,reject)=>{
    const socket=net.connect(18081,'127.0.0.1');
    let header=Buffer.alloc(0);
    const timeout=setTimeout(()=>{socket.destroy();reject(new Error('Proxy CONNECT timeout'))},10000);
    socket.once('error',reject);
    socket.once('connect',()=>socket.write('CONNECT native.localhost:8002 HTTP/1.1\r\nHost: native.localhost:8002\r\n\r\n'));
    function handshake(chunk){
      header=Buffer.concat([header,chunk]);const end=header.indexOf('\r\n\r\n');if(end<0)return;
      clearTimeout(timeout);socket.removeListener('data',handshake);
      if(!header.toString('ascii').startsWith('HTTP/1.1 200')){socket.destroy();reject(new Error('Proxy rejected native backend'));return;}
      const data=new vscode.EventEmitter(),close=new vscode.EventEmitter(),ended=new vscode.EventEmitter();
      socket.on('data',chunk=>data.fire(chunk));socket.on('error',e=>close.fire(e));socket.on('end',()=>ended.fire());
      resolve({onDidReceiveMessage:data.event,onDidClose:close.event,onDidEnd:ended.event,send:bytes=>socket.write(bytes),end:()=>socket.end()});
    }
    socket.on('data',handshake);
  }),'demo-native-token')
}));
