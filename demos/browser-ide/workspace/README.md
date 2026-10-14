# Browser IDE boundary demo

This folder is writable and shared with code-server. No clone or review gate is
required to edit it. The browser itself has a separate, restricted network.

## Try it

1. Open this Markdown preview with **Ctrl+Shift+V**.
2. Follow [the interactive preview](http://preview.localhost:8001/).
3. Press **One-click form POST**. The destination should be blocked by the proxy.
4. Open the command palette and run **Demo: Open Boundary Probe** to test a
   remote extension's webview and see the local-terminal probe result.
5. Open a terminal and run `node hello.js`.

[Direct external link](http://receiver.localhost:9000/markdown-link)

![Automatic remote image](http://receiver.localhost:9000/markdown-image)

All markers are `DEMO_ONLY`. The receiver is a disposable local container.
