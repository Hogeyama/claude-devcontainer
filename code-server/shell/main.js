'use strict';
// The isolated editor's window (README.md): the agent's VS Code, which
// Coder code-server serves in the agent, through code-server-proxy and nothing
// else. The page is the container's, so its JavaScript counts as the
// attacker's; every decision here is made in the browser process and the
// network service, outside the renderer, where the page cannot change it.
//
// Usage: electron <this directory> --proxy-port=<port> [--folder=/workspace]
//          [--user-data-dir=<profile>]
// with the server's connection token in CLAUDE_DEVCONTAINER_VSCODE_TOKEN.
// The proxy's port is on the host's 127.0.0.1 on both Windows and Linux.
const { app, BrowserWindow, Menu, session } = require('electron');
const policy = require('./policy');

const PARTITION = 'persist:ide';

function option(name, fallback) {
  const prefix = `--${name}=`;
  const found = process.argv.find((arg) => arg.startsWith(prefix));
  return found === undefined ? fallback : found.slice(prefix.length);
}

const port = Number(option('proxy-port', ''));
const folder = option('folder', '/workspace');
const token = process.env.CLAUDE_DEVCONTAINER_VSCODE_TOKEN || '';
// Chromium's child processes inherit the environment; none needs the token.
delete process.env.CLAUDE_DEVCONTAINER_VSCODE_TOKEN;
if (!Number.isInteger(port) || port < 1 || port > 65535 || !token) {
  console.error('usage: electron <shell> --proxy-port=<port> [--folder=<path>], '
    + 'with the connection token in CLAUDE_DEVCONTAINER_VSCODE_TOKEN');
  process.exit(2);
}
const proxy = `http://127.0.0.1:${port}`;

// The same proxy for every session, from the start. Chromium sends
// localhost, *.localhost and 127.0.0.1 around a proxy unless <-loopback>
// says otherwise, which would reach any port of the host's loopback directly.
app.commandLine.appendSwitch('proxy-server', proxy);
app.commandLine.appendSwitch('proxy-bypass-list', '<-loopback>');
// No name is resolved here: the proxy takes host names as they are, and a
// lookup (a DNS prefetch, say) would itself reach the network.
app.commandLine.appendSwitch('host-resolver-rules', 'MAP * ~NOTFOUND, EXCLUDE 127.0.0.1');
// WebRTC may use UDP only through a proxy, and an HTTP proxy carries none.
app.commandLine.appendSwitch('force-webrtc-ip-handling-policy', 'disable_non_proxied_udp');
app.enableSandbox();

// One window per profile; the client gives each workspace its own.
const first = app.requestSingleInstanceLock();
if (!first) {
  app.quit();
}

async function lockDown(ses) {
  ses.webRequest.onBeforeRequest((details, callback) => {
    callback({ cancel: !policy.allowsRequest(details.url) });
  });
  // The connection token goes with every request to the server, also those
  // that a page sends without cookies, as a module service worker's script.
  ses.webRequest.onBeforeSendHeaders((details, callback) => {
    const headers = details.requestHeaders;
    if (policy.isServer(details.url)) {
      const name = Object.keys(headers).find((key) => key.toLowerCase() === 'cookie') || 'Cookie';
      const cookies = (headers[name] || '').split(';').map((cookie) => cookie.trim())
        .filter((cookie) => cookie && !cookie.startsWith('code-server-session='));
      headers[name] = [...cookies, `code-server-session=${token}`].join('; ');
    }
    callback({ requestHeaders: headers });
  });
  // A redirect is checked as the response arrives, before it is followed.
  ses.webRequest.onHeadersReceived((details, callback) => {
    const location = Object.entries(details.responseHeaders || {})
      .find(([name]) => name.toLowerCase() === 'location');
    const redirect = details.statusCode >= 300 && details.statusCode < 400 && location;
    callback({ cancel: Boolean(redirect) && !policy.allowsRedirect(location[1][0], details.url) });
  });
  // External protocols reach the OS through this permission, whether a
  // navigation or a redirect asked for them; refused, nothing is launched.
  ses.setPermissionRequestHandler((webContents, permission, callback) => {
    callback(policy.allowsPermission(permission));
  });
  ses.setPermissionCheckHandler((webContents, permission) => policy.allowsPermission(permission));
  // WebHID, WebUSB, Web Serial and Web Bluetooth ask these, not the handlers above.
  ses.setDevicePermissionHandler(() => false);
  ses.setBluetoothPairingHandler((details, callback) => callback({ confirmed: false }));
  ses.on('select-hid-device', (event, details, callback) => {
    event.preventDefault();
    callback();
  });
  ses.on('select-usb-device', (event, details, callback) => {
    event.preventDefault();
    callback();
  });
  ses.on('select-serial-port', (event, portList, webContents, callback) => {
    event.preventDefault();
    callback('');
  });
  ses.setDisplayMediaRequestHandler((request, callback) => callback({}));
  ses.on('will-download', (event) => event.preventDefault());
  await ses.setProxy({ mode: 'fixed_servers', proxyRules: proxy, proxyBypassRules: '<-loopback>' });
}

// A new session's spellchecker downloads dictionaries at once, from Google,
// unless it has no language; disabling it does not stop the download.
app.on('session-created', (ses) => {
  ses.setSpellCheckerEnabled(false);
  ses.setSpellCheckerLanguages([]);
});

app.on('web-contents-created', (event, contents) => {
  // Every frame's navigations, the main frame's included, and the redirects
  // that a server answers them with, which raise no navigation event.
  contents.on('will-frame-navigate', (details) => {
    if (!policy.allowsNavigation(details.url)) {
      details.preventDefault();
    }
  });
  contents.on('will-redirect', (details) => {
    if (!policy.allowsNavigation(details.url)) {
      details.preventDefault();
    }
  });
  // An app of the agent opens in VS Code's Simple Browser, not a window.
  contents.setWindowOpenHandler(() => ({ action: 'deny' }));
  contents.on('will-attach-webview', (details) => details.preventDefault());
  contents.on('select-bluetooth-device', (details, devices, callback) => {
    details.preventDefault();
    callback('');
  });
  contents.setWebRTCIPHandlingPolicy('disable_non_proxied_udp');
  // Closing the window closes it. VS Code's page refuses to unload when a
  // modifier key is down, as with a compositor's close shortcut, to keep a
  // browser tab from closing by mistake; Electron would then keep the window
  // open, without a word.
  contents.on('will-prevent-unload', (details) => details.preventDefault());
});

app.on('second-instance', () => {
  const [window] = BrowserWindow.getAllWindows();
  if (window) {
    if (window.isMinimized()) {
      window.restore();
    }
    window.focus();
  }
});

app.on('window-all-closed', () => app.quit());

app.whenReady().then(async () => {
  if (!first) {
    return;
  }
  // No menu: the default one would offer reloading and developer tools.
  Menu.setApplicationMenu(null);
  await lockDown(session.defaultSession);
  await lockDown(session.fromPartition(PARTITION));
  const window = new BrowserWindow({
    width: 1400,
    height: 900,
    backgroundColor: '#1f1f1f',
    webPreferences: {
      partition: PARTITION,
      nodeIntegration: false,
      nodeIntegrationInSubFrames: false,
      nodeIntegrationInWorker: false,
      contextIsolation: true,
      sandbox: true,
      webviewTag: false,
      devTools: false,
      spellcheck: false,
      navigateOnDragDrop: false,
      safeDialogs: true,
    },
  });
  // The page names itself after the workspace; the suffix tells this window
  // from the host's own VS Code.
  window.on('page-title-updated', (details, title) => {
    details.preventDefault();
    window.setTitle(`${title} [agent]`);
  });
  window.loadURL(policy.ideUrl(folder));
});
