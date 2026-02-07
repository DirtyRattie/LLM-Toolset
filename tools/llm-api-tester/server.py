#!/usr/bin/env python3
"""
LLM API Tester — Local CORS Proxy Server
==========================================
Solves browser CORS restrictions by proxying API requests through localhost.

Usage:
    python server.py              # Start on default port 7860
    python server.py --port 8080  # Start on custom port

Then open http://localhost:7860 in your browser.
"""

import http.server
import http.client
import json
import os
import sys
import ssl
import argparse
from pathlib import Path
from urllib.parse import urlparse

# ─── Configuration ───
HTML_FILE = "llm-api-tester.html"
DEFAULT_PORT = 7860

# Try to use requests library for best compatibility (optional)
try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


def _build_ssl_context():
    """Build a permissive SSL context that works with most servers."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # Compatibility: enable legacy renegotiation for servers behind Cloudflare, etc.
    ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0)
    # Broad cipher + protocol support
    ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def _upstream_request_stdlib(target_url, method, headers, body_bytes):
    """Make upstream request using http.client (stdlib, no deps)."""
    parsed = urlparse(target_url)
    is_https = parsed.scheme == "https"
    host = parsed.hostname
    port = parsed.port or (443 if is_https else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    if is_https:
        ctx = _build_ssl_context()
        conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=180)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=180)

    try:
        conn.request(method, path, body=body_bytes, headers=headers)
        resp = conn.getresponse()
        resp_body = resp.read()
        resp_status = resp.status
        resp_headers = {k: v for k, v in resp.getheaders()}
        return resp_status, resp.reason, resp_headers, resp_body
    finally:
        conn.close()


def _upstream_request_requests(target_url, method, headers, body_bytes):
    """Make upstream request using the `requests` library (best compat)."""
    resp = _requests.request(
        method,
        target_url,
        headers=headers,
        data=body_bytes,
        timeout=180,
        verify=False,
        allow_redirects=True,
    )
    return resp.status_code, resp.reason, dict(resp.headers), resp.content


def upstream_request(target_url, method, headers, body_bytes):
    """Route to best available HTTP client."""
    if HAS_REQUESTS:
        return _upstream_request_requests(target_url, method, headers, body_bytes)
    return _upstream_request_stdlib(target_url, method, headers, body_bytes)


def _upstream_stream_stdlib(target_url, method, headers, body_bytes):
    """Stream upstream SSE response using http.client. Returns (status, reason, headers, generator)."""
    parsed = urlparse(target_url)
    is_https = parsed.scheme == "https"
    host = parsed.hostname
    port = parsed.port or (443 if is_https else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    if is_https:
        ctx = _build_ssl_context()
        conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=300)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=300)

    conn.request(method, path, body=body_bytes, headers=headers)
    resp = conn.getresponse()
    resp_headers = {k: v for k, v in resp.getheaders()}

    def chunk_gen():
        try:
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                yield chunk
        finally:
            conn.close()

    return resp.status, resp.reason, resp_headers, chunk_gen()


def _upstream_stream_requests(target_url, method, headers, body_bytes):
    """Stream upstream SSE response using requests library."""
    resp = _requests.request(
        method,
        target_url,
        headers=headers,
        data=body_bytes,
        timeout=300,
        verify=False,
        allow_redirects=True,
        stream=True,
    )
    resp_headers = dict(resp.headers)

    def chunk_gen():
        for chunk in resp.iter_content(chunk_size=4096):
            if chunk:
                yield chunk

    return resp.status_code, resp.reason, resp_headers, chunk_gen()


def upstream_stream(target_url, method, headers, body_bytes):
    """Route streaming to best available HTTP client. Returns (status, reason, headers, generator)."""
    if HAS_REQUESTS:
        return _upstream_stream_requests(target_url, method, headers, body_bytes)
    return _upstream_stream_stdlib(target_url, method, headers, body_bytes)


class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler that serves static files and proxies API requests."""

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_html()
        elif self.path == "/proxy/health":
            backend = "requests" if HAS_REQUESTS else "stdlib"
            self._send_json(200, {"status": "ok", "proxy": "llm-api-tester", "backend": backend})
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/proxy":
            self._handle_proxy()
        elif self.path == "/proxy/stream":
            self._handle_proxy_stream()
        else:
            self.send_error(404, "Not Found")

    def do_OPTIONS(self):
        self.send_response(200)
        self._set_cors_headers()
        self.end_headers()

    # ─── Internal Methods ───

    def _serve_html(self):
        html_path = Path(__file__).parent / HTML_FILE
        if not html_path.exists():
            self.send_error(404, f"{HTML_FILE} not found in {html_path.parent}")
            return
        content = html_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(content))
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(content)

    def _handle_proxy(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length) if content_length else b""
            payload = json.loads(raw_body)

            target_url = payload.get("target_url")
            method = payload.get("method", "GET").upper()
            headers = payload.get("headers", {})
            body = payload.get("body")

            if not target_url:
                self._send_json(400, {"error": "Missing target_url"})
                return

            # Serialize body
            req_body = None
            if body is not None:
                if isinstance(body, (dict, list)):
                    req_body = json.dumps(body).encode("utf-8")
                elif isinstance(body, str):
                    req_body = body.encode("utf-8")

            # Execute upstream request
            try:
                resp_status, resp_reason, resp_headers, resp_body = upstream_request(
                    target_url, method, headers, req_body
                )
            except Exception as e:
                err_type = type(e).__name__
                self._send_json(502, {
                    "error": f"Upstream connection failed: {e}",
                    "error_type": err_type,
                    "target_url": target_url,
                    "hint": _connection_hint(str(e)),
                })
                return

            # Forward response back to browser
            self.send_response(resp_status)
            ct = resp_headers.get("Content-Type", resp_headers.get("content-type", "application/json"))
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", len(resp_body))
            safe_headers = {k: v for k, v in resp_headers.items()
                           if k.lower() not in ("transfer-encoding", "content-encoding", "content-length")}
            self.send_header("X-Upstream-Headers", json.dumps(safe_headers))
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(resp_body)

        except json.JSONDecodeError:
            self._send_json(400, {"error": "Invalid JSON in proxy request body"})
        except Exception as e:
            self._send_json(500, {"error": f"Proxy internal error: {e}"})

    def _handle_proxy_stream(self):
        """Handle proxy request with streaming passthrough for SSE."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length) if content_length else b""
            payload = json.loads(raw_body)

            target_url = payload.get("target_url")
            method = payload.get("method", "POST").upper()
            headers = payload.get("headers", {})
            body = payload.get("body")

            if not target_url:
                self._send_json(400, {"error": "Missing target_url"})
                return

            req_body = None
            if body is not None:
                if isinstance(body, (dict, list)):
                    req_body = json.dumps(body).encode("utf-8")
                elif isinstance(body, str):
                    req_body = body.encode("utf-8")

            try:
                status, reason, resp_headers, chunks = upstream_stream(
                    target_url, method, headers, req_body
                )
            except Exception as e:
                self._send_json(502, {
                    "error": f"Upstream stream failed: {e}",
                    "error_type": type(e).__name__,
                    "target_url": target_url,
                    "hint": _connection_hint(str(e)),
                })
                return

            # Send HTTP headers to client
            self.send_response(status)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            safe_headers = {k: v for k, v in resp_headers.items()
                           if k.lower() not in ("transfer-encoding", "content-encoding", "content-length")}
            self.send_header("X-Upstream-Headers", json.dumps(safe_headers))
            self._set_cors_headers()
            self.end_headers()

            # Stream body chunks through
            for chunk in chunks:
                self.wfile.write(chunk)
                self.wfile.flush()

        except json.JSONDecodeError:
            self._send_json(400, {"error": "Invalid JSON in proxy request body"})
        except (BrokenPipeError, ConnectionResetError):
            pass  # Client disconnected
        except Exception as e:
            # If headers already sent, we can't send an error response
            try:
                self._send_json(500, {"error": f"Proxy stream error: {e}"})
            except Exception:
                pass

    def _send_json(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _set_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Expose-Headers", "X-Upstream-Headers")

    def log_message(self, format, *args):
        method = args[0].split()[0] if args else ""
        path = args[0].split()[1] if args and len(args[0].split()) > 1 else ""
        status = args[1] if len(args) > 1 else ""
        if path == "/proxy/health":
            return
        if path == "/proxy":
            print(f"  ↗ PROXY  {status}")
        else:
            print(f"  ↗ {method:6s} {status}  {path}")


def _connection_hint(msg):
    m = msg.lower()
    if "eof" in m or "unexpected_eof" in m:
        return "The remote server closed the TLS connection unexpectedly. Try: pip install requests"
    if "certificate" in m or "ssl" in m:
        return "SSL/TLS handshake failed. The server may use a non-standard TLS configuration."
    if "refused" in m:
        return "Connection refused. The server may be down or the port is wrong."
    if "timeout" in m:
        return "Connection timed out. The server may be overloaded."
    if "name" in m and "resolve" in m:
        return "DNS resolution failed. Check if the hostname is correct."
    return None


def try_bind_server(host, preferred_port, scan_range=20):
    """Try to create HTTPServer, scanning ports if preferred is occupied."""
    for offset in range(scan_range):
        port = preferred_port + offset
        try:
            server = http.server.HTTPServer((host, port), ProxyHandler)
            return server, port
        except OSError:
            continue
    return None, None


def main():
    parser = argparse.ArgumentParser(description="LLM API Tester — Local CORS Proxy Server")
    parser.add_argument("--port", "-p", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT}, auto-finds if occupied)")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    args = parser.parse_args()

    os.chdir(Path(__file__).parent)

    if not Path(HTML_FILE).exists():
        print(f"  ✕ Error: {HTML_FILE} not found in {os.getcwd()}")
        sys.exit(1)

    # Find available port and bind
    server, port = try_bind_server(args.host, args.port)
    if server is None:
        print(f"  ✕ Error: No available port found in range {args.port}–{args.port + 19}")
        sys.exit(1)

    if port != args.port:
        print(f"  ! Port {args.port} is in use, using {port} instead")

    backend = "requests" if HAS_REQUESTS else "stdlib (http.client)"
    url = f"http://{args.host}:{port}"
    print()
    print("  ╔══════════════════════════════════════════════╗")
    print("  ║       LLM API Tester — CORS Proxy Server     ║")
    print("  ╠══════════════════════════════════════════════╣")
    print(f"  ║  Open:  {url:<37s} ║")
    print(f"  ║  HTTP:  {backend:<37s} ║")
    print("  ║  Stop:  Ctrl + C                             ║")
    print("  ╚══════════════════════════════════════════════╝")
    if not HAS_REQUESTS:
        print()
        print("  TIP: pip install requests  — for best TLS compatibility")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
