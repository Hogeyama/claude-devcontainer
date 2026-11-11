'use strict';
// HTTP authentication that a page of the agent's asks for (a 401 with Basic or
// Digest, as an app behind a password): a small window asks the person for a
// user name and password, and Chromium sends them to that origin only.
// Electron cancels every sign-in unless the app answers 'login'.
//
// What policy.allowsLogin refuses stays cancelled, the proxy's included. The
// window names the origin that asks, as the server cannot spoof it; the realm
// is the server's own text, shown as such. Its page loads nothing but its own
// files, in a session of its own that keeps nothing, and hands the answer to
// this process through login-preload.js. Requests that wait for the same
// origin and realm share one answer, and one window is open at a time.
const path = require('node:path');
const { app, BrowserWindow, ipcMain, session } = require('electron');
const policy = require('./policy');
const { installPasteShortcut } = require('./paste');

const PAGE = path.join(__dirname, 'login.html');
// The page and its script, all that the window may load.
const FILES = new Set([PAGE, path.join(__dirname, 'login-page.js')]);
const CHANNEL = 'login-prompt';

function lockDown(ses) {
  ses.webRequest.onBeforeRequest((details, callback) => {
    let file = null;
    try {
      const url = new URL(details.url);
      file = url.protocol === 'file:' ? path.normalize(decodeURIComponent(url.pathname)) : null;
    } catch {
      file = null;
    }
    callback({ cancel: !FILES.has(file) });
  });
  ses.setPermissionRequestHandler((webContents, permission, callback) => callback(false));
  ses.setPermissionCheckHandler(() => false);
  ses.setDevicePermissionHandler(() => false);
  ses.on('will-download', (event) => event.preventDefault());
}

// parent() is the IDE's window, or null before it opens.
function installLoginPrompt(parent) {
  // {key: callbacks waiting for one answer}; key names the origin and realm.
  const waiting = new Map();
  const queue = [];
  let current = null;
  let ses = null;

  function settle(key, answer) {
    const callbacks = waiting.get(key) || [];
    waiting.delete(key);
    for (const callback of callbacks) {
      if (answer) {
        callback(answer.username, answer.password);
      } else {
        callback();
      }
    }
  }

  function next() {
    if (current || queue.length === 0) {
      return;
    }
    if (!ses) {
      // In memory: no partition name with persist:, so nothing is written.
      ses = session.fromPartition('login');
      lockDown(ses);
    }
    const key = queue.shift();
    const { origin, realm } = JSON.parse(key);
    const owner = parent();
    const window = new BrowserWindow({
      parent: owner || undefined,
      modal: Boolean(owner),
      width: 460,
      height: 340,
      resizable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      show: false,
      title: `Sign in to ${origin} [agent]`,
      backgroundColor: '#1f1f1f',
      webPreferences: {
        session: ses,
        preload: path.join(__dirname, 'login-preload.js'),
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: true,
        webviewTag: false,
        devTools: false,
        spellcheck: false,
        navigateOnDragDrop: false,
        safeDialogs: true,
      },
    });
    current = { key, window, answered: false };
    installPasteShortcut(window.webContents);
    window.on('page-title-updated', (event) => event.preventDefault());
    window.once('ready-to-show', () => window.show());
    window.on('closed', () => {
      if (!current.answered) {
        settle(key, null);
      }
      current = null;
      next();
    });
    window.loadFile(PAGE, { query: { origin, realm } });
  }

  ipcMain.on(CHANNEL, (event, answer) => {
    if (!current || current.answered || event.sender !== current.window.webContents) {
      return;
    }
    current.answered = true;
    const valid = answer && answer.submit === true
      && typeof answer.username === 'string' && typeof answer.password === 'string';
    settle(current.key, valid ? { username: answer.username, password: answer.password } : null);
    current.window.close();
  });

  app.on('login', (event, webContents, details, authInfo, callback) => {
    if (!policy.allowsLogin(details.url, authInfo)) {
      console.error(`claude-devcontainer: sign-in cancelled for ${authInfo && authInfo.isProxy ? 'the proxy' : details.url}`);
      return;
    }
    event.preventDefault();
    const key = JSON.stringify({ origin: policy.loginOrigin(details.url), realm: String(authInfo.realm || '') });
    if (waiting.has(key)) {
      waiting.get(key).push(callback);
      return;
    }
    waiting.set(key, [callback]);
    queue.push(key);
    next();
  });
}

module.exports = { installLoginPrompt, CHANNEL };
