"""A read-only HTTP server for the map page, and the single-file export.

Standard library only. The engine's dependency list is short on purpose, and a web
framework for five GET routes over a world that never changes while the server runs
would be most of the dependency list.

It binds to loopback unless told otherwise. A map of an estate is exactly the document an
attacker would want, and serving it to the network should be a decision somebody made.
"""
from __future__ import annotations

import json
import pathlib
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from orrery.world import World

from .api import blast_payload, simulate_payload, snapshot_payload, world_payload

STATIC = pathlib.Path(__file__).parent / "static"

# The only files served. A fixed table rather than a directory, so no request path can
# name anything else on the disk.
_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def _json_for_script(payload: dict) -> str:
    """JSON that cannot end the <script> element it is embedded in."""
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


def export_html(world: World, out: pathlib.Path) -> int:
    """Write the page with the world and every answer embedded. Returns bytes written."""
    snap = snapshot_payload(world)
    html = (STATIC / "index.html").read_text("utf-8")
    css = (STATIC / "app.css").read_text("utf-8")
    # A literal `</script` inside the code would end the element early.
    js = (STATIC / "app.js").read_text("utf-8").replace("</script", "<\\/script")
    html = html.replace('<link rel="stylesheet" href="app.css">', f"<style>\n{css}\n</style>")
    html = html.replace(
        '<script src="app.js"></script>',
        f"<script>window.ORRERY_SNAPSHOT={_json_for_script(snap)};</script>\n"
        f"<script>\n{js}\n</script>",
    )
    out.write_text(html, "utf-8")
    return len(html.encode("utf-8"))


def _handler(world: World, lock: threading.Lock) -> type[BaseHTTPRequestHandler]:
    # The world payload is the same for every request and the most expensive thing asked
    # for, so it is built once.
    cached: dict[str, bytes] = {}

    class Handler(BaseHTTPRequestHandler):
        server_version = "orrery"

        def log_message(self, fmt, *args):
            pass

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
            body = json.dumps({"schema": 1, **payload}, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                if url.path in _ASSETS:
                    name, ctype = _ASSETS[url.path]
                    return self._send(HTTPStatus.OK, (STATIC / name).read_bytes(), ctype)
                if url.path == "/api/world":
                    if "world" not in cached:
                        with lock:
                            cached["world"] = json.dumps(
                                {"schema": 1, "mode": "live", **world_payload(world)},
                                ensure_ascii=False,
                            ).encode("utf-8")
                    return self._send(
                        HTTPStatus.OK, cached["world"], "application/json; charset=utf-8"
                    )
                if url.path in ("/api/blast", "/api/simulate"):
                    eid = q.get("id", "")
                    if eid not in world:
                        return self._json(
                            {"error": f"no entity {eid!r} in this world"}, HTTPStatus.NOT_FOUND
                        )
                    if url.path == "/api/blast":
                        hops = int(q["max_hops"]) if q.get("max_hops") else None
                        return self._json(blast_payload(world, eid, hops))
                    elapsed = int(q["elapsed_s"]) if q.get("elapsed_s") else None
                    return self._json(
                        simulate_payload(world, eid, q.get("event", "down"), elapsed)
                    )
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    return Handler


def make_server(world: World, host: str = "127.0.0.1", port: int = 7777) -> ThreadingHTTPServer:
    """A server ready to `serve_forever()`. Port 0 picks a free one; read it back from
    `server.server_address`."""
    return ThreadingHTTPServer((host, port), _handler(world, threading.Lock()))


def serve(world: World, host: str = "127.0.0.1", port: int = 7777) -> None:
    make_server(world, host, port).serve_forever()
