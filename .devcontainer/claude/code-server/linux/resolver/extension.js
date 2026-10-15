// Resolves the remote authority claude-devcontainer+agent to the agent's VS
// Code server, through code-server-proxy on the loopback relay that client.py
// runs in the sandbox. The proxy's allowlist (../../proxy.py) is what decides
// where this ends up; the connection token comes from server.py by way of the
// environment.
//
// VS Code opens a managed connection with one HTTP upgrade request in
// absolute form, "GET ws://localhost/...", and then speaks its own protocol
// on the raw stream. The request is rewritten to a proxy request for
// vscode.localhost, so that the proxy routes it as it routes the browser's.
const net = require('net');
const vscode = require('vscode');

const PROXY = { host: '127.0.0.1', port: 18086 };
const TARGET = 'http://vscode.localhost:8000';
const HANDSHAKE = /^GET ws:\/\/localhost(\/[^ ]*) HTTP\/1\.1\r\n/;
const END = Buffer.from('\r\n\r\n');

function connect() {
  return new Promise((resolve, reject) => {
    const socket = net.connect(PROXY.port, PROXY.host);
    socket.once('error', reject);
    socket.once('connect', () => {
      socket.removeListener('error', reject);
      const data = new vscode.EventEmitter();
      const close = new vscode.EventEmitter();
      const ended = new vscode.EventEmitter();
      socket.on('data', bytes => data.fire(bytes));
      socket.on('error', error => close.fire(error));
      socket.on('close', () => close.fire(undefined));
      socket.on('end', () => ended.fire());
      let request = Buffer.alloc(0);  // The upgrade request, until its end.
      resolve({
        onDidReceiveMessage: data.event,
        onDidClose: close.event,
        onDidEnd: ended.event,
        send: bytes => {
          if (request === null) {
            socket.write(bytes);
            return;
          }
          request = Buffer.concat([request, Buffer.from(bytes)]);
          const end = request.indexOf(END);
          if (end < 0) return;
          const head = request.subarray(0, end + END.length).toString('latin1');
          const rest = request.subarray(end + END.length);
          request = null;
          socket.write(head.replace(HANDSHAKE, (match, path) =>
            `GET ${TARGET}${path} HTTP/1.1\r\nHost: vscode.localhost:8000\r\n`), 'latin1');
          if (rest.length) socket.write(rest);
        },
        end: () => socket.end(),
      });
    });
  });
}

exports.activate = context => {
  context.subscriptions.push(vscode.workspace.registerRemoteAuthorityResolver('claude-devcontainer', {
    resolve: () => new vscode.ManagedResolvedAuthority(connect, process.env.CLAUDE_DEVCONTAINER_VSCODE_TOKEN),
  }));
};

exports.deactivate = () => {};
