"""A tiny client-side rendered site for browser tests.

`/` is an empty shell; `/app.js` fetches `/api/items?page=N` and renders cards.
`/go-private` redirects to `http://127.0.0.1:<port>/secret`, which SSRF tests use.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ITEMS = [
    {
        "id": f"sku-{n}",
        "name": f"Handmade ceramic mug number {n}",
        "price": {"amount": 10 + n, "currency": "EUR"},
        "href": f"/product/{n}",
        "city": "Belgrade",
    }
    for n in range(1, 9)
]

FEED = [
    {"id": f"feed-{n}", "name": f"Lazy loaded lamp number {n}", "href": f"/lamp/{n}"}
    for n in range(1, 31)
]
FEED_BATCH = 6

SCROLL_PAGE = """<!doctype html><html><head><title>Lamp feed</title>
<style>article{height:220px}</style></head>
<body><section class="feed"></section><script>
let offset = 0, busy = false, done = false;
async function more() {
  if (busy || done) return;
  busy = true;
  const data = await fetch('/api/feed?offset=' + offset).then(r => r.json());
  document.querySelector('.feed').insertAdjacentHTML('beforeend', data.items.map(i =>
    `<article class="lamp"><a href="${i.href}">${i.name}</a></article>`).join(''));
  offset += data.items.length; done = !data.more; busy = false;
}
more();
window.addEventListener('scroll', () => {
  if (window.innerHeight + window.scrollY >= document.body.offsetHeight - 50) more();
});
</script></body></html>"""

SHELL = """<!doctype html><html><head><title>Mug shop</title></head>
<body><div id="root"></div><noscript>Please enable JavaScript.</noscript>
<script src="/app.js"></script></body></html>"""

APP_JS = """
const page = new URLSearchParams(location.search).get('page') || '1';
fetch('/api/items?page=' + page, {headers: {'Accept': 'application/json'}})
  .then(r => r.json())
  .then(data => {
    const root = document.getElementById('root');
    root.innerHTML = '<section class="grid">' + data.data.items.map(item =>
      `<article class="card"><a href="${item.href}"><h2>${item.name}</h2></a>` +
      `<span class="price">${item.price.amount} ${item.price.currency}</span></article>`
    ).join('') + '</section>';
  });
"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep test output quiet
        return

    def _send(self, status: int, body: str, content_type: str, headers=None) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        port = self.server.server_address[1]
        if parsed.path == "/":
            self._send(200, SHELL, "text/html; charset=utf-8")
        elif parsed.path == "/app.js":
            self._send(200, APP_JS, "application/javascript")
        elif parsed.path == "/api/items":
            page = int(parse_qs(parsed.query).get("page", ["1"])[0])
            items = ITEMS if page == 1 else []
            self._send(200, json.dumps({"data": {"items": items}, "page": page}), "application/json")
        elif parsed.path == "/scroll":
            self._send(200, SCROLL_PAGE, "text/html; charset=utf-8")
        elif parsed.path == "/api/feed":
            offset = int(parse_qs(parsed.query).get("offset", ["0"])[0])
            batch = FEED[offset : offset + FEED_BATCH]
            more = offset + FEED_BATCH < len(FEED)
            self._send(200, json.dumps({"items": batch, "more": more}), "application/json")
        elif parsed.path == "/go-private":
            self._send(302, "", "text/plain", {"Location": f"http://127.0.0.1:{port}/secret"})
        elif parsed.path == "/secret":
            self.server.secret_hits += 1
            self._send(200, '{"secret": "metadata-token"}', "application/json")
        elif parsed.path == "/robots.txt":
            self._send(200, "User-agent: *\nAllow: /\n", "text/plain")
        else:
            self._send(404, "not found", "text/plain")


class CsrSite:
    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.secret_hits = 0
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def url(self, path: str = "/", host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.port}{path}"

    @property
    def secret_hits(self) -> int:
        return self.server.secret_hits

    def __enter__(self) -> CsrSite:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
