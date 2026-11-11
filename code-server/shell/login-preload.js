'use strict';
// The sign-in page's only way out (login.js): its answer, to the main process.
const { contextBridge, ipcRenderer } = require('electron');

const CHANNEL = 'login-prompt';

contextBridge.exposeInMainWorld('login', {
  submit(username, password) {
    ipcRenderer.send(CHANNEL, { submit: true, username: String(username), password: String(password) });
  },
  cancel() {
    ipcRenderer.send(CHANNEL, { submit: false });
  },
});
