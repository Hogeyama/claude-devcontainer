'use strict';
// What the shell lets its pages load, as pure functions of the URL; main.js
// applies them, and tests/electron_shell_test.py checks them with Node.
//
// Every origin the shell may reach is http://localhost:<port>, and
// code-server-proxy decides where each port goes (../proxy.py): 18000 is the
// IDE and its webviews; any other port is the same port in the agent.
// One site for all of them, since Chromium sends SameSite=Lax cookies to an
// iframe only when every frame above it is of the top page's site, and the
// Simple Browser shows an app in an iframe of a webview's iframe.

const IDE_PORT = 18000;
// The only permission granted: copying in the IDE writes the clipboard.
// Reading it is denied. paste.js handles physical paste shortcuts through
// native paste events, including in the Terminal, without granting API access.
const PERMISSIONS = new Set(['clipboard-sanitized-write']);

function parse(url) {
  try {
    return new URL(url);
  } catch {
    return null;
  }
}

// A URL of http://localhost:<port> or ws://localhost:<port>, the only ones
// that go out (to the proxy).
function isAgent(url) {
  const parsed = parse(url);
  if (!parsed || !['http:', 'ws:'].includes(parsed.protocol)) {
    return false;
  }
  // An empty port is the scheme's default, 80; URL refuses one above 65535.
  return parsed.hostname === 'localhost' && parsed.port !== '0'
    && parsed.username === '' && parsed.password === '';
}

// blob: URLs name the origin that made them, and load nothing from the network.
function isAgentBlob(url) {
  return url.startsWith('blob:') && isAgent(url.slice('blob:'.length))
    && parse(url.slice('blob:'.length)).protocol === 'http:';
}

// A URL of the server's: the IDE or its webviews.
function isServer(url) {
  const parsed = isAgent(url) ? parse(url) : null;
  return parsed !== null && parsed.port === String(IDE_PORT);
}

// A frame may show the agent's pages, its own blob: URLs, and the empty
// documents that iframes start with. Nothing else: no other site, file: or
// data: document, and no external protocol (vscode:, ms-settings:, mailto:).
function allowsNavigation(url) {
  if (url === 'about:blank' || url === 'about:srcdoc' || isAgentBlob(url)) {
    return true;
  }
  return isAgent(url) && parse(url).protocol === 'http:';
}

// A request (fetch, WebSocket, subresource, a worker's) may go to the agent;
// data: and blob: are read in the browser.
function allowsRequest(url) {
  return isAgent(url) || isAgentBlob(url) || url.startsWith('data:');
}

// The Location of a redirect, relative to the URL that answered it.
function allowsRedirect(location, base) {
  let target;
  try {
    target = new URL(location, base);
  } catch {
    return false;
  }
  return allowsNavigation(target.toString());
}

function allowsPermission(permission) {
  return PERMISSIONS.has(permission);
}

// The first page opens the folder. main.js supplies authentication in a header.
function ideUrl(folder) {
  const url = new URL(`http://localhost:${IDE_PORT}/`);
  url.searchParams.set('folder', folder);
  return url.toString();
}

module.exports = {
  IDE_PORT, isServer, allowsNavigation, allowsRequest, allowsRedirect, allowsPermission, ideUrl,
};
