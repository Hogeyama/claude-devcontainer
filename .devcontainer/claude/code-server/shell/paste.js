'use strict';

// before-input-event comes from Chromium's input path, not DOM key events.
// Keep clipboard reads denied to pages; a physical shortcut instead pastes
// into the focused frame through Electron's native editing command. This
// also bypasses VS Code Terminal's Ctrl-V handler, which uses readText().
function installPasteShortcut(contents) {
  contents.on('before-input-event', (event, input) => {
    if (input.type !== 'keyDown' || input.alt || input.meta) {
      return;
    }
    const paste = (input.control && input.key.toLowerCase() === 'v')
      || (input.shift && !input.control && input.key === 'Insert');
    if (paste) {
      event.preventDefault();
      contents.paste();
    }
  });
}

module.exports = { installPasteShortcut };
