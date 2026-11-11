'use strict';
// The sign-in page (login.js). The origin and realm come in the query; both
// are shown as text, the realm being whatever the server sent.
const query = new URLSearchParams(window.location.search);
document.getElementById('origin').textContent = query.get('origin') || '';
const realm = query.get('realm') || '';
if (realm) {
  document.getElementById('realm').textContent = realm;
  document.getElementById('realm-line').hidden = false;
}

document.getElementById('form').addEventListener('submit', (event) => {
  event.preventDefault();
  window.login.submit(document.getElementById('username').value, document.getElementById('password').value);
});
document.getElementById('cancel').addEventListener('click', () => window.login.cancel());
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    window.login.cancel();
  }
});
document.getElementById('username').focus();
