// A worker of the shell probe: its own fetches, from outside the page.
const attempt = async (url) => {
  try {
    return (await fetch(url)).status;
  } catch (error) {
    return 'error:' + error.name;
  }
};
(async () => {
  postMessage({ external: await attempt('http://example.com/worker'), app: await attempt('http://localhost:3000/worker') });
})();
