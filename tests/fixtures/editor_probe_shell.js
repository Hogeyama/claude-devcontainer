'use strict';
// Test-only observation in the browser process; production has no preload or IPC.
const { app, BrowserWindow } = require('electron');
const fs = require('fs');
app.requestSingleInstanceLock = () => true;
require(process.env.EDITOR_PROBE_SHELL);
app.whenReady().then(() => {
  const timer = setInterval(async () => {
    const [window] = BrowserWindow.getAllWindows();
    if (!window) return;
    // Forward a port through the same editable row the user sees.
    const portInput = await window.webContents.executeJavaScript(`(() => {
      const input = document.querySelector('.ports-view input');
      if (!input) return false;
      input.focus();
      return true;
    })()`);
    if (portInput) {
      await window.webContents.insertText('3000');
      window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Return' });
      window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Return' });
    }
    const frames = [];
    for (const frame of window.webContents.mainFrame.framesInSubtree) {
      try {
        frames.push(await frame.executeJavaScript(`({
          url: location.href, text: document.body.innerText.slice(0, 4000),
          links: [...document.querySelectorAll('a')].map(a => ({text: a.innerText, href: a.href}))
        })`));
      } catch {
        // Frames can disappear while a webview is being replaced.
      }
    }
    if (frames.some(frame => frame.url.startsWith('http://localhost:3000/')
        && !frame.url.includes('forwarded=1') && frame.text.includes('Asset loaded'))) {
      fs.writeFileSync(process.env.EDITOR_PROBE_DIRECT, 'rendered');
    }
    fs.writeFileSync(process.env.EDITOR_PROBE_FRAMES, JSON.stringify(frames));
  }, 500);
  app.on('before-quit', () => clearInterval(timer));
});
