from __future__ import annotations

import argparse
import base64
import hashlib
import http.cookiejar
import ipaddress
import json
import logging
import os
import queue
import re
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from websockets.sync.client import connect as proxy_aware_websocket_connect
from datetime import datetime, timedelta
from pathlib import Path
from typing import IO, Any
from urllib.parse import urlsplit, urlunsplit

import share_control as control
import share_assets
from share_auth import SHARE_COOKIE_NAME, session_cookie_value


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
TOKEN_PATH = DATA_DIR / "public_share_token.txt"
CURRENT_URL_PATH = DATA_DIR / "current_share_url.txt"
LOCATOR_CONFIG_PATH = DATA_DIR / "public_share_locator.json"
BAT_PATH = ROOT / "启动只读分享.bat"
VISITOR_BAT_PATH = ROOT / "给访客打开 Yang-gumi.bat"
VISITOR_URL_PATH = ROOT / "给访客打开 Yang-gumi.url"
COMPAT_RUNTIME = ROOT / "tools" / "streamlit_modern_compat"
CLOUDFLARED_PATH = ROOT / "tools" / "cloudflared.exe"
EXPOSE_PATH = ROOT / "tools" / "expose" / "bin" / "expose.exe"
WORMHOLE_PATH = ROOT / "tools" / "wormhole" / "bin" / "wormhole.exe"
XPOS_IDENTITY_PATH = ROOT / "tools" / "xpos" / "id_ed25519"
XPOS_SERVER_IP = "188.245.157.245"
HOSTC_RUNTIME_PATH = ROOT / "tools" / "hostc_runtime" / "node_modules" / "hostc" / "dist" / "index.js"
PINGGY_ASKPASS_PATH = ROOT / "tools" / "pinggy_empty_password.cmd"
RUNLOCAL_CLIENT_PATH = ROOT / "tools" / "runlocal_client.py"
PLAIN_TUNNEL_CLIENT_PATH = ROOT / "tools" / "plain_tunnel_client.py"
PLAIN_TUNNEL_STATE_DIR = Path(os.getenv("LOCALAPPDATA") or DATA_DIR) / "YangGumi"
PLAIN_TUNNEL_SUBDOMAIN_PATH = PLAIN_TUNNEL_STATE_DIR / "plain_tunnel_subdomain.txt"
INSTANCE_LOCK_PATH = DATA_DIR / "remote_share.instance.lock"
PREFERRED_PORT = 18632
MAIN_APP_PORT = 8501
FALLBACK_APP_PORT = 8502
FALLBACK_APP_PORT_ATTEMPTS = 100
PORT_ATTEMPTS = 20
PORT = PREFERRED_PORT
MAX_RECONNECTS = 3
PUBLIC_HEALTH_INTERVAL_SECONDS = 20.0
PUBLIC_HEALTH_DEGRADED_AFTER = 1
PUBLIC_HEALTH_RECYCLE_AFTER = 3
LOCATOR_RETRY_SECONDS = 60.0
LOCATOR_REFRESH_SECONDS = 600.0
LOCATOR_API_URL = "https://jsonblob.com/api/jsonBlob"
LOCATOR_ATTEMPTS = 1
LOCATOR_REQUEST_TIMEOUT_SECONDS = 2.5
STALE_TUNNEL_RECYCLE_PROVIDERS = {
    "PlainTunnel",
    "Wormhole",
    "Hostc",
    "localhost.run",
}
SAME_PROVIDER_RECONNECT_PROVIDERS = {"PlainTunnel", "localhost.run"}
# Providers rejected by live end-to-end verification:
# - Hostc returns "Tunnel not ready" after initially rendering.
# - Wormhole is blocked by Chrome Safe Browsing and strips the access query.
# - XPOS expires the public edge after roughly 30-36 minutes while its SSH
#   process remains alive.
# - Expose never completes its relay handshake on the current route.
# Keep the bundled clients for compatibility. New shares first use the
# project-scoped Plain Tunnel client; no account, VPS, domain, API key,
# private key, Node.js installation, or machine-specific tunnel credential is
# required.
DISABLED_TUNNEL_PROVIDERS = {"Expose", "Hostc", "Wormhole", "XPOS"}
TUNNEL_URL_RE = re.compile(
    r"https://[a-z0-9.-]+\.(?:plaintunnel\.com|serveousercontent\.com|runlocal\.eu|xpos\.to|wormhole\.bar|expose\.host|hostc\.dev|free\.pinggy\.net|run\.pinggy-free\.link|lhr\.life|trycloudflare\.com)",
    re.IGNORECASE,
)
LEGACY_FRONTEND_MARKER = "/* yanggumi-old-edge-compat-v2 */"
STREAMLIT_WEBSOCKET_CONSTRUCTOR = 'this.websocket=new WebSocket(n,["streamlit",...t])'
TUNNEL_COMPAT_WEBSOCKET_CONSTRUCTOR = (
    'this.websocket=/(?:\\.plaintunnel\\.com|\\.wormhole\\.bar|\\.runlocal\\.eu)$/i.test(location.hostname)'
    '?new WebSocket(n):new WebSocket(n,["streamlit",...t])'
)
PREVIOUS_TUNNEL_COMPAT_WEBSOCKET_CONSTRUCTOR = (
    'this.websocket=/(?:\\.wormhole\\.bar|\\.runlocal\\.eu)$/i.test(location.hostname)'
    '?new WebSocket(n):new WebSocket(n,["streamlit",...t])'
)
LEGACY_FRONTEND_POLYFILLS = r"""/* yanggumi-old-edge-compat-v2 */
(function () {
  if (!Object.hasOwn) {
    Object.hasOwn = function (object, property) {
      if (object === null || object === undefined) throw new TypeError("Object.hasOwn called on null or undefined");
      return Object.prototype.hasOwnProperty.call(Object(object), property);
    };
  }
  var at = function (index) {
    var length = this.length >>> 0;
    var position = Number(index) || 0;
    position = position < 0 ? Math.ceil(position) : Math.floor(position);
    if (position < 0) position += length;
    return position < 0 || position >= length ? undefined : this[position];
  };
  var arrayTypes = ["Array", "Int8Array", "Uint8Array", "Uint8ClampedArray", "Int16Array", "Uint16Array", "Int32Array", "Uint32Array", "Float32Array", "Float64Array", "BigInt64Array", "BigUint64Array"];
  for (var i = 0; i < arrayTypes.length; i += 1) {
    var constructor = window[arrayTypes[i]];
    if (constructor && constructor.prototype && !constructor.prototype.at) {
      Object.defineProperty(constructor.prototype, "at", { configurable: true, writable: true, value: at });
    }
  }
  if (!String.prototype.replaceAll) {
    Object.defineProperty(String.prototype, "replaceAll", {
      configurable: true,
      writable: true,
      value: function (search, replacement) {
        var text = String(this);
        if (search instanceof RegExp) {
          if (!search.global) throw new TypeError("replaceAll requires a global regular expression");
          return text.replace(search, replacement);
        }
        return text.split(String(search)).join(replacement);
      }
    });
  }
  if (typeof AbortSignal !== "undefined" && typeof AbortController !== "undefined" && !AbortSignal.timeout) {
    AbortSignal.timeout = function (milliseconds) {
      var controller = new AbortController();
      window.setTimeout(function () { controller.abort(); }, Number(milliseconds) || 0);
      return controller.signal;
    };
  }
})();
"""

STOP_EVENT = threading.Event()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _configure_logger() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now() - timedelta(days=7)
    for path in LOG_DIR.glob("remote_share_*.log"):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                path.unlink()
        except OSError:
            pass
    logger = logging.getLogger("yanggumi.remote_share")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        path = LOG_DIR / f"remote_share_{datetime.now():%Y%m%d}.log"
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        logger.addHandler(handler)
    return logger


LOGGER = _configure_logger()


def _write_status(**changes: Any) -> dict[str, Any]:
    payload = control.read_status()
    payload.update(changes)
    if "pid" not in changes:
        payload["pid"] = os.getpid()
    payload["updated_at"] = _now()
    control._atomic_json(control.STATUS_PATH, payload)
    return payload


def plain_tunnel_subdomain(path: Path | None = None) -> str:
    """Return a stable, per-machine Plain Tunnel name without online accounts."""
    path = PLAIN_TUNNEL_SUBDOMAIN_PATH if path is None else Path(path)
    pattern = re.compile(r"^yanggumi-[a-f0-9]{16}$")
    try:
        existing = path.read_text(encoding="ascii").strip().lower()
    except OSError:
        existing = ""
    if pattern.fullmatch(existing):
        return existing

    value = f"yanggumi-{secrets.token_hex(8)}"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(value + "\n", encoding="ascii")
    os.replace(temporary, path)
    return value


def ensure_legacy_frontend_compatibility(runtime: Path | None = None) -> bool:
    """Patch the bundled frontend once so an older Edge can render it."""
    runtime = runtime or COMPAT_RUNTIME
    static_root = runtime / "streamlit" / "static"
    bundles = list((static_root / "static" / "js").glob("index.*.js"))
    if len(bundles) != 1:
        return False
    changed = False
    bundle = bundles[0]
    source = bundle.read_text(encoding="utf-8")
    if not source.startswith(LEGACY_FRONTEND_MARKER):
        source = LEGACY_FRONTEND_POLYFILLS + source
        changed = True
    if STREAMLIT_WEBSOCKET_CONSTRUCTOR in source:
        source = source.replace(
            STREAMLIT_WEBSOCKET_CONSTRUCTOR,
            TUNNEL_COMPAT_WEBSOCKET_CONSTRUCTOR,
            1,
        )
        changed = True
    elif PREVIOUS_TUNNEL_COMPAT_WEBSOCKET_CONSTRUCTOR in source:
        source = source.replace(
            PREVIOUS_TUNNEL_COMPAT_WEBSOCKET_CONSTRUCTOR,
            TUNNEL_COMPAT_WEBSOCKET_CONSTRUCTOR,
            1,
        )
        changed = True
    if changed:
        bundle.write_text(source, encoding="utf-8")
    index_path = static_root / "index.html"
    if index_path.exists():
        index_source = index_path.read_text(encoding="utf-8")
        script_pattern = re.compile(rf'(src="\./static/js/{re.escape(bundle.name)})(?:\?[^\"]*)?(\")')
        # ES-module chunks import this entry bundle by its bare hashed URL. Adding
        # a query here gives React two module identities and triggers invalid-hook
        # errors as soon as a lazy Streamlit component loads.
        updated_index = script_pattern.sub(r'\1\2', index_source, count=1)
        if updated_index != index_source:
            index_path.write_text(updated_index, encoding="utf-8")
            changed = True
    return changed


def share_token() -> str:
    """Create a fresh 256-bit token for every sharing session."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(32)
    TOKEN_PATH.write_text(value, encoding="utf-8")
    return value


def public_access_url(base_url: str, token: str) -> str:
    """Build the visitor URL, including provider-specific warning bypasses."""
    hostname = (urlsplit(base_url).hostname or "").lower()
    if hostname.endswith(".serveousercontent.com"):
        return (
            f"{base_url.rstrip('/')}/_yanggumi_share/{token}"
            "?serveo-skip-browser-warning=true"
        )
    separator = "&" if "?" in base_url else "?"
    if hostname.endswith(".xpos.to"):
        return f"{base_url.rstrip('/')}/{separator}_xpos_continue=1&access={token}"
    return f"{base_url.rstrip('/')}/{separator}access={token}"


def lan_address() -> str:
    """Legacy helper retained for compatibility; remote sharing does not bind to it."""
    try:
        addresses = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        addresses = []
    candidates = [ip for ip in addresses if ip and not ip.startswith(("127.", "169.254."))]
    for prefix in ("192.168.", "10."):
        match = next((ip for ip in candidates if ip.startswith(prefix)), None)
        if match:
            return match
    private_172 = next((ip for ip in candidates if ip.startswith("172.") and 16 <= int(ip.split(".")[1]) <= 31), None)
    if private_172:
        return private_172
    if candidates:
        return candidates[0]
    raise RuntimeError("没有找到可用的 IPv4 地址。")


def find_available_port(preferred: int = PREFERRED_PORT, attempts: int = PORT_ATTEMPTS) -> int:
    for port in range(preferred, preferred + attempts):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            probe.close()
    raise RuntimeError(f"端口 {preferred} 至 {preferred + attempts - 1} 均被占用，无法启动只读站。")


def find_available_streamlit_port() -> int:
    return find_available_port(FALLBACK_APP_PORT, FALLBACK_APP_PORT_ATTEMPTS)


def wait_for_streamlit(timeout: float = 45.0, port: int | None = None) -> None:
    port = PORT if port is None else port
    deadline = time.monotonic() + timeout
    health = f"http://127.0.0.1:{port}/_stcore/health"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline and not STOP_EVENT.is_set():
        try:
            with opener.open(health, timeout=1.5) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError("只读网站启动超时，请稍后重试。")


def port_is_open(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_proxy(timeout: float = 20.0, port: int | None = None) -> None:
    port = PORT if port is None else port
    deadline = time.monotonic() + timeout
    health = f"http://127.0.0.1:{port}/__yanggumi_share_health"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline and not STOP_EVENT.is_set():
        try:
            with opener.open(health, timeout=1.5) as response:
                if response.status == 200 and response.read(32).strip().lower() == b"ok":
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError("只读安全代理启动超时，请稍后重试。")


def streamlit_server_ready(timeout: float = 1.5, port: int | None = None) -> bool:
    port = PORT if port is None else port
    health = f"http://127.0.0.1:{port}/_stcore/health"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(health, timeout=timeout) as response:
            return response.status == 200
    except OSError:
        return False


def public_route_url(base_url: str, route: str) -> str:
    """Build a public probe URL without treating access query parameters as path text."""
    parsed = urlsplit(base_url)
    base_path = parsed.path.rstrip("/")
    route_path = f"/{route.lstrip('/')}"
    return urlunsplit((parsed.scheme, parsed.netloc, f"{base_path}{route_path}", "", ""))


def public_streamlit_server_ready(base_url: str, timeout: float = 5.0) -> bool:
    """Verify that a public tunnel reaches Streamlit, not a tunnel error page."""
    parsed = urlsplit(base_url)
    if (parsed.hostname or "").lower().endswith(".xpos.to"):
        connection: socket.socket | ssl.SSLSocket | None = None
        try:
            port = parsed.port or 443
            connection = socket.create_connection((XPOS_SERVER_IP, port), timeout=timeout)
            connection = ssl.create_default_context().wrap_socket(
                connection,
                server_hostname=parsed.hostname,
            )
            connection.settimeout(timeout)
            path = urlsplit(public_route_url(base_url, "/__yanggumi_share_health")).path
            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {parsed.hostname}\r\n"
                "Connection: close\r\n"
                "Cache-Control: no-cache\r\n"
                "User-Agent: Mozilla/5.0 Yang-gumi-share-health/1.0\r\n\r\n"
            )
            connection.sendall(request.encode("ascii"))
            response = b""
            while len(response) < 16384:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                response += chunk
            header, _, body = response.partition(b"\r\n\r\n")
            return header.startswith(b"HTTP/1.1 200") and body.strip().lower() == b"ok"
        except (OSError, ssl.SSLError, ValueError):
            return False
        finally:
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass
    health = public_route_url(base_url, "/__yanggumi_share_health")
    headers = {
        "User-Agent": "Mozilla/5.0 Yang-gumi-share-health/1.0",
        "Cache-Control": "no-cache",
    }
    if (urlsplit(base_url).hostname or "").lower().endswith(".serveousercontent.com"):
        headers["serveo-skip-browser-warning"] = "true"
    request = urllib.request.Request(
        health,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status == 200 and response.read(32).strip().lower() == b"ok"
    except (OSError, urllib.error.URLError):
        return False


def public_authorized_streamlit_ready(base_url: str, token: str, timeout: float = 5.0) -> bool:
    """Verify that a fresh visitor can exchange the access query for a session cookie."""
    if not token:
        return False
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    request = urllib.request.Request(
        public_access_url(base_url, token),
        headers={
            "User-Agent": "Mozilla/5.0 Yang-gumi-share-auth-health/1.0",
            "Cache-Control": "no-cache",
            **(
                {"serveo-skip-browser-warning": "true"}
                if (urlsplit(base_url).hostname or "").lower().endswith(
                    ".serveousercontent.com"
                )
                else {}
            ),
        },
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            body = response.read(32768).lower()
            return (
                response.status == 200
                and "text/html" in content_type
                and b"streamlit" in body
                and any(cookie.name == SHARE_COOKIE_NAME for cookie in jar)
            )
    except (OSError, urllib.error.URLError):
        return False


def _close_websocket_cleanly(connection: socket.socket | ssl.SSLSocket) -> None:
    """Finish a health-probe WebSocket without aborting a multiplexed tunnel."""
    payload = b"\x03\xe8"  # RFC 6455 normal-closure status code 1000.
    mask = secrets.token_bytes(4)
    masked_payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    frame = b"\x88" + bytes([0x80 | len(payload)]) + mask + masked_payload
    try:
        connection.settimeout(0.5)
        connection.sendall(frame)
        try:
            connection.recv(256)
        except (OSError, ssl.SSLError):
            pass
    except (OSError, ssl.SSLError):
        pass


def _public_streamlit_websocket_ready_direct(
    base_url: str, timeout: float = 5.0, token: str = "",
) -> bool:
    """Perform a real RFC 6455 upgrade against Streamlit's session endpoint."""
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    connection: socket.socket | ssl.SSLSocket | None = None
    try:
        connect_host = XPOS_SERVER_IP if parsed.hostname.lower().endswith(".xpos.to") else parsed.hostname
        connection = socket.create_connection((connect_host, port), timeout=timeout)
        if parsed.scheme == "https":
            connection = ssl.create_default_context().wrap_socket(connection, server_hostname=parsed.hostname)
        connection.settimeout(timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        host = parsed.hostname if port in {80, 443} else f"{parsed.hostname}:{port}"
        origin = f"{parsed.scheme}://{host}"
        # Wormhole forwards Streamlit WebSockets but currently drops the
        # Sec-WebSocket-Protocol response header.  The patched frontend omits
        # the protocol on that provider, so the health probe must do the same.
        offer_streamlit_protocol = not parsed.hostname.lower().endswith(
            (".plaintunnel.com", ".wormhole.bar", ".runlocal.eu")
        )
        protocol_header = "Sec-WebSocket-Protocol: streamlit\r\n" if offer_streamlit_protocol else ""
        cookie_header = (
            f"Cookie: {SHARE_COOKIE_NAME}={session_cookie_value(token)}\r\n" if token else ""
        )
        serveo_header = (
            "serveo-skip-browser-warning: true\r\n"
            if parsed.hostname.lower().endswith(".serveousercontent.com")
            else ""
        )
        request = (
            "GET /_stcore/stream HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Origin: {origin}\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"{protocol_header}"
            f"{cookie_header}"
            f"{serveo_header}"
            "User-Agent: Mozilla/5.0 Yang-gumi-share-health/1.0\r\n\r\n"
        )
        connection.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response and len(response) < 8192:
            chunk = connection.recv(2048)
            if not chunk:
                break
            response += chunk
        header = response.split(b"\r\n\r\n", 1)[0]
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        )
        protocol_ok = (
            b"sec-websocket-protocol: streamlit" in header.lower()
            if offer_streamlit_protocol
            else True
        )
        ready = (
            header.startswith(b"HTTP/1.1 101")
            and b"sec-websocket-accept: " + expected.lower() in header.lower()
            and protocol_ok
        )
        if ready:
            _close_websocket_cleanly(connection)
        return ready
    except (OSError, ssl.SSLError, ValueError):
        return False
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass


def public_streamlit_websocket_ready(base_url: str, timeout: float = 5.0, token: str = "") -> bool:
    """Verify Streamlit WebSocket through the same system proxy path as browsers."""
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    host = parsed.hostname if port in {80, 443} else f"{parsed.hostname}:{port}"
    scheme = "wss" if parsed.scheme == "https" else "ws"
    base_path = parsed.path.rstrip("/")
    websocket_url = f"{scheme}://{host}{base_path}/_stcore/stream"
    origin = f"{parsed.scheme}://{host}"
    offer_streamlit_protocol = not parsed.hostname.lower().endswith(
        (".plaintunnel.com", ".wormhole.bar", ".runlocal.eu")
    )
    headers = {"User-Agent": "Mozilla/5.0 Yang-gumi-share-health/1.0"}
    if token:
        headers["Cookie"] = f"{SHARE_COOKIE_NAME}={session_cookie_value(token)}"
    if parsed.hostname.lower().endswith(".serveousercontent.com"):
        headers["serveo-skip-browser-warning"] = "true"
    try:
        with proxy_aware_websocket_connect(
            websocket_url,
            origin=origin,
            subprotocols=["streamlit"] if offer_streamlit_protocol else None,
            additional_headers=headers,
            open_timeout=timeout,
            close_timeout=.5,
        ) as connection:
            return not offer_streamlit_protocol or connection.subprotocol == "streamlit"
    except Exception:
        # Direct sockets remain useful on machines without a configured proxy
        # and for legacy tunnel endpoints with unusual DNS routing.
        return _public_streamlit_websocket_ready_direct(base_url, timeout, token)


def wait_for_public_streamlit(
    base_url: str, process: subprocess.Popen[str], timeout: float = 15.0, token: str = ""
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not _stop_requested():
        if process.poll() is not None:
            return False
        remaining = max(1.0, deadline - time.monotonic())
        probe_timeout = min(4.0, remaining)
        if (
            public_streamlit_server_ready(base_url, timeout=probe_timeout)
            and public_authorized_streamlit_ready(base_url, token, timeout=probe_timeout)
            and public_streamlit_websocket_ready(base_url, timeout=probe_timeout, token=token)
        ):
            return True
        time.sleep(0.75)
    return False


def streamlit_environment(token: str = "") -> dict[str, str]:
    env = dict(os.environ)
    env.pop("YANGGUMI_READ_ONLY", None)
    env.pop("YANGGUMI_SHARE_TOKEN", None)
    env.pop("YANGGUMI_SHARE_ASSETS", None)
    env["STREAMLIT_GLOBAL_DEVELOPMENT_MODE"] = "false"
    if (COMPAT_RUNTIME / "streamlit").is_dir():
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(COMPAT_RUNTIME) + (os.pathsep + existing if existing else "")
    return env


def streamlit_command(port: int) -> list[str]:
    """Run the normal owner application when port 8501 is not already healthy."""
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(ROOT / "app.py"),
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--server.enableWebsocketCompression",
        "true",
        "--server.fileWatcherType",
        "none",
        "--browser.serverAddress",
        "127.0.0.1",
        "--browser.gatherUsageStats",
        "false",
    ]


def proxy_command(port: int, upstream_port: int = MAIN_APP_PORT) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "share_proxy_server.py"),
        "--port",
        str(port),
        "--upstream-port",
        str(upstream_port),
        "--token-file",
        str(TOKEN_PATH),
    ]


def replace_public_url(text: str, url: str) -> str:
    """Embed the current public URL in the dual-mode one-file launcher."""
    line = f'set "YANGGUMI_PUBLIC_URL={url}"'
    pattern = re.compile(r'^set "YANGGUMI_PUBLIC_URL=.*"$', re.MULTILINE)
    return pattern.sub(line, text, count=1) if pattern.search(text) else f"{line}\n{text}"


def replace_public_locator(text: str, locator_url: str) -> str:
    """Embed the stable locator used by already-copied visitor launchers."""
    line = f'set "YANGGUMI_SHARE_LOCATOR={locator_url}"'
    pattern = re.compile(r'^set "YANGGUMI_SHARE_LOCATOR=.*"$', re.MULTILINE)
    return pattern.sub(line, text, count=1) if pattern.search(text) else f"{line}\n{text}"


def _valid_locator_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == "jsonblob.com"
        and bool(re.fullmatch(r"/api/jsonBlob/[0-9a-f-]{20,}", parsed.path, re.IGNORECASE))
        and not parsed.query
        and not parsed.fragment
    )


def _stored_locator_url() -> str:
    try:
        payload = json.loads(LOCATOR_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    value = str(payload.get("url") or "")
    return value if _valid_locator_url(value) else ""


def _locator_request(
    url: str,
    *,
    method: str,
    payload: dict[str, Any] | None = None,
    timeout: float = LOCATOR_REQUEST_TIMEOUT_SECONDS,
) -> tuple[int, dict[str, str], dict[str, Any]]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        raw = response.read()
        body = json.loads(raw.decode("utf-8")) if raw else {}
        headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
        return int(response.status), headers, body


def _locator_is_missing(error: BaseException) -> bool:
    return isinstance(error, urllib.error.HTTPError) and error.code in {404, 410}


def _visitor_powershell_command() -> str:
    """Open a visitor URL without letting cmd.exe parse its query string."""
    script = (
        "$ProgressPreference='SilentlyContinue';"
        "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;"
        "$u=$env:YANGGUMI_PUBLIC_URL;"
        "try{"
        "$d=Invoke-RestMethod -UseBasicParsing -Uri $env:YANGGUMI_SHARE_LOCATOR -TimeoutSec 8;"
        "if($d.active -eq $false){exit 2};"
        "if($d.url -match '^https://[a-z0-9.-]+\\.(plaintunnel\\.com|serveousercontent\\.com|runlocal\\.eu|xpos\\.to|wormhole\\.bar|expose\\.host|hostc\\.dev|free\\.pinggy\\.net|run\\.pinggy-free\\.link|lhr\\.life|trycloudflare\\.com)/(?:.*[?&]access=|_yanggumi_share/[A-Za-z0-9_-]+\\?serveo-skip-browser-warning=true)'){"
        "$u=[string]$d.url"
        "}"
        "}catch{};"
        "if($u -notmatch '^https://[a-z0-9.-]+\\.(plaintunnel\\.com|serveousercontent\\.com|runlocal\\.eu|xpos\\.to|wormhole\\.bar|expose\\.host|hostc\\.dev|free\\.pinggy\\.net|run\\.pinggy-free\\.link|lhr\\.life|trycloudflare\\.com)/(?:.*[?&]access=|_yanggumi_share/[A-Za-z0-9_-]+\\?serveo-skip-browser-warning=true)'){exit 2};"
        "if($env:SHIKISHARE_VISITOR_DRY_RUN){[Console]::Write($u);exit 0};"
        "try{"
        "$p=New-Object System.Diagnostics.ProcessStartInfo;"
        "$p.FileName=$u;"
        "$p.UseShellExecute=$true;"
        "[Diagnostics.Process]::Start($p)|Out-Null;"
        "exit 0"
        "}catch{exit 3}"
    )
    return (
        "powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass "
        f'-Command "{script}"'
    )


def _visitor_batch_block(*, missing_label: str, failed_label: str) -> str:
    return (
        f"{_visitor_powershell_command()}\r\n"
        'set "YANGGUMI_VISITOR_EXIT=%ERRORLEVEL%"\r\n'
        f'if "%YANGGUMI_VISITOR_EXIT%"=="2" goto {missing_label}\r\n'
        f'if not "%YANGGUMI_VISITOR_EXIT%"=="0" goto {failed_label}\r\n'
        "exit /b 0\r\n"
    )


def _refresh_owner_visitor_block(source: str) -> str:
    """Refresh the standalone half without rewriting the owner's launcher."""
    replacement = (
        ":visitor\r\n"
        + _visitor_batch_block(
            missing_label="missing_public_url",
            failed_label="visitor_failed",
        )
        + "\r\n:owner"
    )
    updated, count = re.subn(
        r":visitor\r?\n.*?\r?\n:owner",
        lambda _match: replacement,
        source,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise RuntimeError("启动只读分享.bat 缺少 visitor/owner 标记，无法安全更新")
    return updated


def sync_public_locator(public_url: str) -> tuple[str, bool]:
    """Publish the current rotating tunnel URL behind one persistent locator.

    A copied visitor BAT keeps the opaque locator URL.  When a free tunnel
    rotates its hostname, the owner updates this small JSON document and old
    BAT copies immediately discover the new HTTPS address.
    """
    locator_url = _stored_locator_url()
    payload = {
        "version": 1,
        "active": bool(public_url),
        "url": public_url,
        "updated_at": _now(),
    }
    last_error: Exception | None = None
    for attempt in range(LOCATOR_ATTEMPTS):
        try:
            if locator_url:
                try:
                    _locator_request(locator_url, method="PUT", payload=payload)
                except urllib.error.HTTPError as exc:
                    if not _locator_is_missing(exc):
                        raise
                    # A provider can retire a blob. Recreate it immediately
                    # instead of embedding a permanently dead locator.
                    locator_url = ""
            if not locator_url:
                _status, headers, _body = _locator_request(
                    LOCATOR_API_URL,
                    method="POST",
                    payload=payload,
                )
                location = headers.get("location", "")
                locator_url = (
                    location
                    if location.startswith("https://")
                    else f"https://jsonblob.com/{location.lstrip('/')}"
                )
                if not _valid_locator_url(locator_url):
                    raise RuntimeError("分享定位地址格式无效")
            _status, _headers, confirmed = _locator_request(locator_url, method="GET")
            if (
                bool(confirmed.get("active")) != bool(public_url)
                or str(confirmed.get("url") or "") != public_url
            ):
                raise RuntimeError("分享定位地址写入后校验不一致")
            control._atomic_json(LOCATOR_CONFIG_PATH, {"url": locator_url})
            return locator_url, True
        except (OSError, ValueError, TypeError, RuntimeError, urllib.error.HTTPError) as exc:
            last_error = exc
            if _locator_is_missing(exc):
                locator_url = ""
            if attempt + 1 < LOCATOR_ATTEMPTS:
                time.sleep(0.4 * (attempt + 1))
    LOGGER.warning("固定分享定位地址同步失败，保留上次地址并稍后重试：%s", type(last_error).__name__)
    return locator_url, False


def vmware_host_address() -> str | None:
    if os.name != "nt":
        return None
    command = (
        "$ip=Get-NetIPAddress -InterfaceAlias 'VMware Network Adapter VMnet8' "
        "-AddressFamily IPv4 -ErrorAction SilentlyContinue | "
        "Select-Object -First 1 -ExpandProperty IPAddress; if($ip){$ip}"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
        )
        value = next((line.strip() for line in completed.stdout.splitlines() if line.strip()), "")
        address = ipaddress.ip_address(value)
        if address.version == 4 and not address.is_loopback and not address.is_link_local:
            return value
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


def replace_url_host(url: str, host: str) -> str:
    parsed = urlsplit(url)
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def update_launchers(url: str) -> bool:
    """Write one-file visitor launchers for the currently live share.

    The project launcher is deliberately dual-mode: beside ``share_public.py``
    it starts the owner process, while a copied standalone file only opens the
    embedded HTTPS URL.  The visitor therefore never needs Python, the project
    directory, the database, or any bundled assets.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CURRENT_URL_PATH.write_text(url, encoding="utf-8")
    locator_url, locator_synced = sync_public_locator(url)
    if BAT_PATH.exists():
        source = BAT_PATH.read_text(encoding="ascii")
        source = _refresh_owner_visitor_block(source)
        source = replace_public_url(source, url)
        source = replace_public_locator(source, locator_url)
        BAT_PATH.write_text(source, encoding="ascii", newline="\r\n")
    visitor = (
        "@echo off\r\n"
        "setlocal EnableExtensions DisableDelayedExpansion\r\n"
        f'set "YANGGUMI_PUBLIC_URL={url}"\r\n'
        f'set "YANGGUMI_SHARE_LOCATOR={locator_url}"\r\n'
        + _visitor_batch_block(missing_label="missing", failed_label="failed")
        + ":missing\r\n"
        "echo Start sharing on the owner PC, then copy this file again.\r\n"
        "pause\r\nexit /b 2\r\n"
        ":failed\r\n"
        "echo The default browser could not open the Yang-gumi read-only site.\r\n"
        "pause\r\nexit /b 3\r\n"
    )
    VISITOR_BAT_PATH.write_text(visitor, encoding="ascii", newline="")
    VISITOR_URL_PATH.write_text(f"[InternetShortcut]\r\nURL={url}\r\n", encoding="utf-8-sig")
    return locator_synced


class WindowsJob:
    """Kill child processes automatically when the sharing controller exits."""

    def __init__(self) -> None:
        self.handle: int | None = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount",
                "WriteTransferCount", "OtherTransferCount"
            )]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation), ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError("无法创建分享进程清理对象。")
        info = ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(handle)
            raise OSError("无法设置分享进程自动清理规则。")
        self.handle = handle

    def add(self, process: subprocess.Popen[str]) -> None:
        if os.name != "nt" or not self.handle:
            return
        import ctypes

        if not ctypes.windll.kernel32.AssignProcessToJobObject(self.handle, process._handle):
            raise OSError("无法把分享子进程加入自动清理范围。")

    def close(self) -> None:
        if os.name == "nt" and self.handle:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


def _child_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _terminate(process: subprocess.Popen[str] | None, timeout: float = 6.0) -> None:
    if not process or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW, check=False,
            )
        else:
            process.kill()


def _start_owned_streamlit(port: int, job: WindowsJob) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        streamlit_command(port),
        cwd=ROOT,
        env=streamlit_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=_child_flags(),
    )
    job.add(process)
    wait_for_streamlit(port=port)
    return process


def _start_proxy_process(
    port: int,
    upstream_port: int,
    job: WindowsJob,
) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        proxy_command(port, upstream_port),
        cwd=ROOT,
        env=streamlit_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=_child_flags(),
    )
    job.add(process)
    wait_for_proxy(port=port)
    return process


def _recover_local_upstream(
    proxy: subprocess.Popen[str],
    owned_streamlit: subprocess.Popen[str] | None,
    job: WindowsJob,
) -> tuple[subprocess.Popen[str], subprocess.Popen[str], int]:
    """Move sharing away from an occupied but unhealthy owner port."""
    replacement_port = find_available_streamlit_port()
    replacement_streamlit = _start_owned_streamlit(replacement_port, job)
    try:
        _terminate(proxy)
        replacement_proxy = _start_proxy_process(PORT, replacement_port, job)
    except Exception:
        _terminate(replacement_streamlit)
        raise
    if owned_streamlit is not None:
        _terminate(owned_streamlit)
    return replacement_proxy, replacement_streamlit, replacement_port


def _pump_tunnel_output(pipe: IO[str], found: queue.Queue[tuple[str, str]]) -> None:
    try:
        for line in iter(pipe.readline, ""):
            match = TUNNEL_URL_RE.search(line)
            if match:
                found.put(("url", match.group(0).rstrip("/")))
            if "Registered tunnel connection" in line:
                found.put(("connected", ""))
            if "ERR" in line or "error" in line.lower():
                LOGGER.info("tunnel: %s", line.strip()[:500])
    except (OSError, ValueError):
        pass
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _start_tunnel_process(
    command: list[str],
    job: WindowsJob,
    timeout: float,
    require_connection_event: bool,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.Popen[str], str]:
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=_child_flags(),
    )
    try:
        job.add(process)
    except Exception:
        _terminate(process)
        raise
    found: queue.Queue[tuple[str, str]] = queue.Queue()
    for pipe in (process.stdout, process.stderr):
        if pipe is not None:
            threading.Thread(target=_pump_tunnel_output, args=(pipe, found), daemon=True).start()
    deadline = time.monotonic() + timeout
    public_url = ""
    connected = False
    while time.monotonic() < deadline and not STOP_EVENT.is_set() and not control.STOP_PATH.exists():
        try:
            event, value = found.get(timeout=0.2)
            if event == "url":
                public_url = value
            elif event == "connected":
                connected = True
            if public_url and (connected or not require_connection_event):
                return process, public_url
        except queue.Empty:
            if process.poll() is not None:
                raise RuntimeError("公网隧道进程已提前退出。")
    _terminate(process)
    raise RuntimeError("等待公网隧道连接超时。")


def start_tunnel(
    port: int,
    job: WindowsJob,
    token: str,
    timeout: float = 45.0,
    excluded_providers: set[str] | None = None,
) -> tuple[subprocess.Popen[str], str, str]:
    """Start a verified HTTPS tunnel, using the fastest reachable provider first."""
    failures: list[str] = []
    excluded = set(excluded_providers or ()) | DISABLED_TUNNEL_PROVIDERS

    # Plain Tunnel is vendored as a small Python client and keeps a stable
    # per-machine hostname across restarts. It forwards the original Streamlit
    # HTTP and WebSocket traffic, so the share remains the exact same live app.
    if "PlainTunnel" not in excluded and PLAIN_TUNNEL_CLIENT_PATH.is_file():
        command = [
            sys.executable,
            str(PLAIN_TUNNEL_CLIENT_PATH),
            f"http://127.0.0.1:{port}",
            "--subdomain",
            plain_tunnel_subdomain(),
            "--exit-on-disconnect",
        ]
        try:
            process, url = _start_tunnel_process(
                command, job, min(timeout, 20.0), require_connection_event=False
            )
            if wait_for_public_streamlit(url, process, timeout=20.0, token=token):
                return process, url, "PlainTunnel"
            _terminate(process)
            raise RuntimeError(
                "Plain Tunnel did not preserve the Streamlit HTTP and WebSocket session"
            )
        except Exception as exc:
            failures.append(f"PlainTunnel: {exc}")
            LOGGER.warning(
                "Plain Tunnel startup or public verification failed; trying a fallback: %s",
                type(exc).__name__,
            )

    # Serveo is a public SSH remote-forward service designed to work with the
    # OpenSSH client already included in supported Windows versions. Anonymous
    # tunnels need no registration or private credential and can remain
    # connected indefinitely with standard SSH keepalives.
    ssh = shutil.which("ssh.exe") or shutil.which("ssh")
    if "Serveo" not in excluded and ssh:
        command = [
            ssh,
            "-T",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ConnectionAttempts=3",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=12",
            "-o",
            "TCPKeepAlive=yes",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-R",
            f"80:127.0.0.1:{port}",
            "serveo.net",
        ]
        try:
            process, url = _start_tunnel_process(
                command, job, min(timeout, 20.0), require_connection_event=False
            )
            if wait_for_public_streamlit(url, process, timeout=20.0, token=token):
                return process, url, "Serveo"
            _terminate(process)
            raise RuntimeError(
                "Serveo did not preserve the Streamlit HTTP and WebSocket session"
            )
        except Exception as exc:
            failures.append(f"Serveo: {exc}")
            LOGGER.warning(
                "Serveo startup or public verification failed; trying a fallback: %s",
                type(exc).__name__,
            )

    # Runlocal is account-free and the client is implemented with dependencies
    # already required by Yang-gumi. This keeps the GitHub workflow portable:
    # no VPS, domain, Node.js installation, tunnel account, or machine-specific
    # credential is needed.
    if "Runlocal" not in excluded and RUNLOCAL_CLIENT_PATH.is_file():
        command = [sys.executable, str(RUNLOCAL_CLIENT_PATH), str(port)]
        try:
            process, url = _start_tunnel_process(
                command, job, min(timeout, 20.0), require_connection_event=False
            )
            if wait_for_public_streamlit(url, process, timeout=20.0, token=token):
                return process, url, "Runlocal"
            _terminate(process)
            raise RuntimeError("Runlocal did not preserve the Streamlit HTTP and WebSocket session")
        except Exception as exc:
            failures.append(f"Runlocal: {exc}")
            LOGGER.warning(
                "Runlocal startup or public verification failed; trying a fallback: %s",
                type(exc).__name__,
            )

    # Hostc has materially lower first-render and WebSocket latency on the
    # currently verified route.  Its hostname may rotate or occasionally go
    # stale while the process remains alive; the stable locator plus the
    # three-probe recycler below make that safe for already-copied launchers.
    node = shutil.which("node.exe") or shutil.which("node")
    if "Hostc" not in excluded and node and HOSTC_RUNTIME_PATH.is_file():
        command = [node, str(HOSTC_RUNTIME_PATH), str(port)]
        try:
            process, url = _start_tunnel_process(
                command, job, min(timeout, 20.0), require_connection_event=False
            )
            if wait_for_public_streamlit(url, process, timeout=15.0, token=token):
                return process, url, "Hostc"
            _terminate(process)
            raise RuntimeError("Hostc did not preserve the Streamlit HTTP and WebSocket session")
        except Exception as exc:
            failures.append(f"Hostc: {exc}")
            LOGGER.warning("Hostc startup or public verification failed; trying a fallback: %s", type(exc).__name__)

    # XPOS is reached with stock OpenSSH over port 443.  A project-scoped
    # identity satisfies its anonymous public-key handshake without depending
    # on the user's SSH profile or an online account.  It has sustained the
    # required one-hour Streamlit HTTP/WebSocket verification, so it remains
    # the durable fallback whenever the faster Hostc endpoint is unavailable.
    ssh = shutil.which("ssh.exe") or shutil.which("ssh")
    if "XPOS" not in excluded and ssh and XPOS_IDENTITY_PATH.is_file():
        command = [
            ssh, "-T", "-p", "443", "-i", str(XPOS_IDENTITY_PATH),
            "-o", "IdentitiesOnly=yes",
            "-o", "ConnectTimeout=10",
            "-o", "ConnectionAttempts=3",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=12",
            "-o", "TCPKeepAlive=yes",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "BatchMode=yes",
            "-o", "HostKeyAlias=go.xpos.dev",
            "-o", "StrictHostKeyChecking=accept-new",
            "-R", f"0:127.0.0.1:{port}", f"x@{XPOS_SERVER_IP}",
        ]
        try:
            process, url = _start_tunnel_process(
                command, job, min(timeout, 20.0), require_connection_event=False
            )
            if wait_for_public_streamlit(url, process, timeout=20.0, token=token):
                return process, url, "XPOS"
            _terminate(process)
            raise RuntimeError("XPOS did not preserve the Streamlit HTTP and WebSocket session")
        except Exception as exc:
            failures.append(f"XPOS: {exc}")
            LOGGER.warning(
                "XPOS startup or public verification failed; trying a fallback: %s",
                type(exc).__name__,
            )

    # Wormhole tunnels over an outbound WebSocket on port 443 and can preserve
    # its hostname while the process reconnects.  Keep it as a fallback, but
    # recycle it after three failed probes instead of waiting forever on a dead
    # relay connection.
    if "Wormhole" not in excluded and WORMHOLE_PATH.is_file():
        command = [
            str(WORMHOLE_PATH), "http", str(port), "--headless", "--no-inspect",
        ]
        try:
            process, url = _start_tunnel_process(
                command, job, min(timeout, 20.0), require_connection_event=False
            )
            if wait_for_public_streamlit(url, process, timeout=20.0, token=token):
                return process, url, "Wormhole"
            _terminate(process)
            raise RuntimeError("Wormhole did not preserve the Streamlit HTTP and WebSocket session")
        except Exception as exc:
            failures.append(f"Wormhole: {exc}")
            LOGGER.warning(
                "Wormhole startup or public verification failed; trying a fallback: %s",
                type(exc).__name__,
            )

    # Expose uses an outbound TLS connection on port 443, automatically
    # reconnects, and gives this machine/port a deterministic hostname.
    # Those properties avoid both blocked tunnel ports and rotating links.
    if "Expose" not in excluded and EXPOSE_PATH.is_file():
        command = [str(EXPOSE_PATH), "-port", str(port), "-sticky"]
        try:
            process, url = _start_tunnel_process(
                command, job, min(timeout, 20.0), require_connection_event=False
            )
            if wait_for_public_streamlit(url, process, timeout=20.0, token=token):
                return process, url, "Expose"
            _terminate(process)
            raise RuntimeError("Expose did not preserve the Streamlit HTTP and WebSocket session")
        except Exception as exc:
            failures.append(f"Expose: {exc}")
            LOGGER.warning(
                "Expose startup or public verification failed; trying a fallback: %s",
                type(exc).__name__,
            )

    # Keep the bundled Cloudflare client as the first fallback.  Some networks
    # block its edge port, but when reachable it supports Streamlit sessions.
    if "Cloudflare" not in excluded and CLOUDFLARED_PATH.is_file():
        command = [
            str(CLOUDFLARED_PATH), "tunnel", "--no-autoupdate",
            "--protocol", "http2", "--url", f"http://127.0.0.1:{port}",
        ]
        try:
            process, url = _start_tunnel_process(
                command, job, min(timeout, 30.0), require_connection_event=True
            )
            if wait_for_public_streamlit(url, process, token=token):
                return process, url, "Cloudflare"
            _terminate(process)
            raise RuntimeError("Cloudflare did not preserve the Streamlit HTTP and WebSocket session")
        except Exception as exc:
            failures.append(f"Cloudflare: {exc}")
            LOGGER.warning(
                "Cloudflare startup or public verification failed; trying a fallback: %s",
                type(exc).__name__,
            )

    if "Pinggy" not in excluded and ssh and PINGGY_ASKPASS_PATH.is_file():
        pinggy_env = os.environ.copy()
        pinggy_env.update(
            SSH_ASKPASS=str(PINGGY_ASKPASS_PATH),
            SSH_ASKPASS_REQUIRE="force",
            DISPLAY="yanggumi-share",
        )
        command = [
            ssh, "-T", "-p", "443",
            "-o", "PreferredAuthentications=password",
            "-o", "PubkeyAuthentication=no",
            "-o", "NumberOfPasswordPrompts=1",
            "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=12",
            "-o", "TCPKeepAlive=yes",
            "-o", "ConnectionAttempts=3",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-R", f"0:127.0.0.1:{port}", "free.pinggy.io",
        ]
        try:
            process, url = _start_tunnel_process(
                command, job, min(timeout, 25.0), require_connection_event=False, env=pinggy_env
            )
            if wait_for_public_streamlit(url, process, timeout=15.0, token=token):
                return process, url, "Pinggy"
            _terminate(process)
            raise RuntimeError("公网健康检查或 WebSocket 检查没有到达只读站")
        except Exception as exc:
            failures.append(f"Pinggy: {exc}")
            LOGGER.warning("Pinggy 启动或公网验证失败，准备切换备用隧道：%s", type(exc).__name__)

    if "localhost.run" not in excluded and ssh:
        command = [
            ssh, "-T", "-p", "22",
            "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=12",
            "-o", "TCPKeepAlive=yes",
            "-o", "ConnectionAttempts=3",
            "-o", "IPQoS=none",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-R", f"80:127.0.0.1:{port}", "nokey@localhost.run",
        ]
        try:
            process, url = _start_tunnel_process(
                command, job, min(timeout, 25.0), require_connection_event=False
            )
            if wait_for_public_streamlit(url, process, timeout=12.0, token=token):
                return process, url, "localhost.run"
            _terminate(process)
            raise RuntimeError("公网健康检查或 WebSocket 检查没有到达只读站")
        except Exception as exc:
            failures.append(f"localhost.run: {exc}")
            LOGGER.warning("localhost.run 启动或公网验证失败，准备切换备用隧道：%s", type(exc).__name__)

    detail = "；".join(failures) or "没有找到可用的公网隧道程序"
    raise RuntimeError(f"公网链接建立失败：{detail}")


def _stop_requested() -> bool:
    return STOP_EVENT.is_set() or control.STOP_PATH.exists()


def _acquire_instance_lock() -> IO[bytes] | None:
    """Acquire a one-byte cross-process lock for the share singleton."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handle = INSTANCE_LOCK_PATH.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        handle.close()
        return None
    return handle


def _release_instance_lock(handle: IO[bytes] | None) -> None:
    if handle is None or handle.closed:
        return
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except (OSError, IOError):
        pass
    finally:
        handle.close()


def _update_public_health_status(healthy: bool, failures: int, final_url: str) -> int:
    """Track edge health without rotating a still-live ephemeral hostname.

    Short failures preserve the current link. Persistent failures are handled
    by the provider-specific recycler so the stable locator can move visitors
    away from an endpoint that is connected at SSH level but no longer serves
    HTTP or WebSocket traffic.
    """
    if healthy:
        if failures >= PUBLIC_HEALTH_DEGRADED_AFTER:
            LOGGER.info("公网线路已恢复，继续使用原链接")
            _write_status(
                state="running",
                message="公网只读分享正在运行",
                public_url=final_url,
                tunnel_state="connected",
                last_error="",
            )
        return 0

    failures += 1
    LOGGER.warning(
        "公网健康检查暂时失败（%s）；保留原链接和现有隧道，等待线路自行恢复",
        failures,
    )
    if failures == PUBLIC_HEALTH_DEGRADED_AFTER or (
        failures > PUBLIC_HEALTH_DEGRADED_AFTER and failures % 15 == 0
    ):
        _write_status(
            state="degraded",
            message="公网线路暂时波动，正在保留原链接并等待自动恢复…",
            public_url=final_url,
            tunnel_state="connected",
            last_error=f"连续 {failures} 次公网探测未通过；未更换链接",
        )
    return failures


def _should_recycle_stale_tunnel(provider: str, failures: int) -> bool:
    """Recycle only relays whose hostname survives an in-process reconnect.

    Plain Tunnel keeps its per-machine hostname across reconnects.
    localhost.run may rotate, but copied visitor launchers follow the stable
    locator, so a permanently dead edge must be replaced instead of being
    retained forever.
    """
    return (
        provider in STALE_TUNNEL_RECYCLE_PROVIDERS
        and failures >= PUBLIC_HEALTH_RECYCLE_AFTER
    )


def _on_signal(_signum: int, _frame: Any) -> None:
    STOP_EVENT.set()


def run_share(managed: bool = False) -> None:
    global PORT
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    instance_lock = _acquire_instance_lock()
    if instance_lock is None:
        existing = control.read_status()
        existing_url = str(existing.get("public_url") or "")
        print("Yang-gumi 公网只读分享已经在运行，不会重复启动。", flush=True)
        if existing_url:
            print(f"当前链接：{existing_url}", flush=True)
            if not managed and os.getenv("YANGGUMI_NO_BROWSER", "0") != "1":
                webbrowser.open(existing_url)
        return
    control.STOP_PATH.unlink(missing_ok=True)
    control.PID_PATH.write_text(str(os.getpid()), encoding="ascii")
    started_at = _now()
    token = share_token()
    owned_streamlit: subprocess.Popen[str] | None = None
    proxy: subprocess.Popen[str] | None = None
    tunnel: subprocess.Popen[str] | None = None
    asset_prewarmer: threading.Thread | None = None
    asset_prewarmer_stop = threading.Event()
    job: WindowsJob | None = None
    final_error = ""
    try:
        PORT = find_available_port()
        _write_status(
            state="starting", message="正在连接本地主站和只读代理…", public_url="", local_state="starting",
            tunnel_state="starting", port=PORT, started_at=started_at, reconnect_attempt=0, last_error="",
        )
        LOGGER.info("开始公网只读分享；本地端口 %s", PORT)
        _write_status(message="正在增量优化分享图片；原图和数据库不会修改…")
        asset_started_at = time.monotonic()
        try:
            asset_stats = share_assets.prepare_share_assets()
            LOGGER.info(
                "分享图片增量预热完成；covers=%s seasonal=%s daily_art=%s failed=%s elapsed=%.2fs",
                asset_stats["covers"], asset_stats["seasonal"], asset_stats["daily_art"],
                asset_stats["failed"], time.monotonic() - asset_started_at,
            )
        except Exception as exc:
            LOGGER.warning("分享图片预热失败，继续启动并使用原图回退：%s", type(exc).__name__)
        initial_asset_revision = share_assets.source_revision()
        asset_prewarmer = threading.Thread(
            target=share_assets.run_prewarmer,
            args=(lambda: asset_prewarmer_stop.is_set() or _stop_requested(),),
            kwargs={"initial_revision": initial_asset_revision},
            name="yanggumi-share-assets",
            daemon=True,
        )
        asset_prewarmer.start()
        job = WindowsJob()
        ensure_legacy_frontend_compatibility()

        upstream_port = MAIN_APP_PORT
        if streamlit_server_ready(port=MAIN_APP_PORT):
            LOGGER.info("复用已经健康运行的主站；端口 %s", MAIN_APP_PORT)
            _write_status(message="已复用正在运行的主站，正在启动只读安全代理…")
        else:
            upstream_port = find_available_streamlit_port() if port_is_open(MAIN_APP_PORT) else MAIN_APP_PORT
            if upstream_port == MAIN_APP_PORT:
                _write_status(message="主站尚未运行，正在启动正常 Yang-gumi 主站…")
            else:
                _write_status(
                    message=f"主站端口 {MAIN_APP_PORT} 无响应，正在使用独立只读上游 {upstream_port} 自动恢复…",
                    local_state="recovering",
                )
                LOGGER.warning(
                    "主站端口 %s 被非健康实例占用；分享改用独立上游 %s",
                    MAIN_APP_PORT,
                    upstream_port,
                )
            owned_streamlit = _start_owned_streamlit(upstream_port, job)

        proxy = _start_proxy_process(PORT, upstream_port, job)
        _write_status(local_state="running", message="只读安全代理已启动，正在申请公网链接…")

        excluded_providers: set[str] = set()
        tunnel, base_url, tunnel_provider = start_tunnel(PORT, job, token)
        final_url = public_access_url(base_url, token)
        locator_synced = update_launchers(final_url)
        next_locator_sync = time.monotonic() + (
            LOCATOR_REFRESH_SECONDS if locator_synced else LOCATOR_RETRY_SECONDS
        )
        _write_status(
            state="running", message="公网只读分享正在运行", public_url=final_url,
            local_state="running", tunnel_state="connected", tunnel_provider=tunnel_provider,
            reconnect_attempt=0,
        )
        LOGGER.info("公网隧道已连接；访问令牌已遮盖为 %s…%s", token[:4], token[-4:])
        print("\nYang-gumi 公网只读分享已启动。", flush=True)
        print(f"远程访问链接：{final_url}", flush=True)
        print("关闭本窗口或在网站中点击停止分享后，链接立即失效。\n", flush=True)
        if not managed and os.getenv("YANGGUMI_NO_BROWSER", "0") != "1":
            webbrowser.open(final_url)

        last_public_health_check = time.monotonic()
        public_health_failures = 0
        while not _stop_requested():
            if proxy.poll() is not None:
                raise RuntimeError("本地只读安全代理意外停止，公网链接已经失效。")
            if owned_streamlit is not None and owned_streamlit.poll() is not None:
                raise RuntimeError("由分享程序启动的 Yang-gumi 主站意外停止。")
            if tunnel.poll() is None:
                if time.monotonic() - last_public_health_check >= PUBLIC_HEALTH_INTERVAL_SECONDS:
                    last_public_health_check = time.monotonic()
                    if not streamlit_server_ready(port=upstream_port):
                        LOGGER.warning("分享上游端口 %s 已失效；正在切换到独立上游", upstream_port)
                        _write_status(
                            state="recovering",
                            message="本地主站无响应，正在自动切换只读上游…",
                            local_state="recovering",
                            last_error=f"本地上游端口 {upstream_port} 健康检查失败",
                        )
                        proxy, owned_streamlit, upstream_port = _recover_local_upstream(
                            proxy,
                            owned_streamlit,
                            job,
                        )
                        public_health_failures = 0
                        _write_status(
                            state="running",
                            message=f"本地只读上游已恢复（端口 {upstream_port}）",
                            local_state="running",
                            last_error="",
                        )
                    healthy = (
                        streamlit_server_ready(port=upstream_port)
                        and public_streamlit_server_ready(base_url)
                        and public_authorized_streamlit_ready(base_url, token)
                        and public_streamlit_websocket_ready(base_url, token=token)
                    )
                    public_health_failures = _update_public_health_status(
                        healthy, public_health_failures, final_url
                    )
                    if _should_recycle_stale_tunnel(tunnel_provider, public_health_failures):
                        # Plain Tunnel restarts on the same per-machine host.
                        # Do not exclude it and rotate visitors onto a new URL.
                        if tunnel_provider not in SAME_PROVIDER_RECONNECT_PROVIDERS:
                            excluded_providers.add(tunnel_provider)
                        LOGGER.warning(
                            "Public endpoint failed %s consecutive probes; recycling the live %s process",
                            public_health_failures,
                            tunnel_provider,
                        )
                        _write_status(
                            state="reconnecting",
                            message="公网端点已经失效，正在自动申请新链接…",
                            public_url="",
                            tunnel_state="disconnected",
                            last_error=f"连续 {public_health_failures} 次公网探测失败，已自动回收失效端点",
                        )
                        _terminate(tunnel)
                if time.monotonic() >= next_locator_sync:
                    _locator_url, locator_synced = sync_public_locator(final_url)
                    next_locator_sync = time.monotonic() + (
                        LOCATOR_REFRESH_SECONDS if locator_synced else LOCATOR_RETRY_SECONDS
                    )
                time.sleep(0.5)
                continue
            LOGGER.warning(
                "公网隧道进程已退出；provider=%s code=%s",
                tunnel_provider,
                tunnel.poll(),
            )
            tunnel = None
            _write_status(
                state="reconnecting", message="公网隧道已断开，正在有限次数内重连…",
                public_url="", tunnel_state="disconnected",
            )
            reconnected = False
            for attempt in range(1, MAX_RECONNECTS + 1):
                if _stop_requested():
                    break
                _write_status(reconnect_attempt=attempt, message=f"公网隧道重连中（{attempt}/{MAX_RECONNECTS}）…")
                time.sleep(attempt * 2)
                try:
                    tunnel, base_url, tunnel_provider = start_tunnel(
                        PORT,
                        job,
                        token,
                        excluded_providers=excluded_providers,
                    )
                    final_url = public_access_url(base_url, token)
                    locator_synced = update_launchers(final_url)
                    next_locator_sync = time.monotonic() + (
                        LOCATOR_REFRESH_SECONDS if locator_synced else LOCATOR_RETRY_SECONDS
                    )
                    _write_status(
                        state="running", message="公网隧道已重连；链接已更新，请复制新地址", public_url=final_url,
                        tunnel_state="connected", tunnel_provider=tunnel_provider,
                        reconnect_attempt=attempt, last_error="",
                    )
                    last_public_health_check = time.monotonic()
                    public_health_failures = 0
                    reconnected = True
                    LOGGER.info("公网隧道第 %s 次重连成功", attempt)
                    print(f"\n公网隧道已重连，旧链接失效；新链接：{final_url}\n", flush=True)
                    break
                except Exception as exc:
                    LOGGER.warning("公网隧道第 %s 次重连失败：%s", attempt, type(exc).__name__)
            if not reconnected and not _stop_requested():
                raise RuntimeError("公网隧道连续重连失败；本地只读站已关闭，请检查网络后重新启动分享。")
    except KeyboardInterrupt:
        STOP_EVENT.set()
    except Exception as exc:
        final_error = str(exc) or type(exc).__name__
        LOGGER.exception("公网只读分享失败：%s", type(exc).__name__)
        _write_status(
            state="error", message=final_error, public_url="", local_state="stopping",
            tunnel_state="disconnected", last_error=final_error,
        )
        if not managed:
            print(f"\n启动失败：{final_error}", flush=True)
    finally:
        asset_prewarmer_stop.set()
        sync_public_locator("")
        if asset_prewarmer and asset_prewarmer.is_alive():
            asset_prewarmer.join(timeout=3)
        _write_status(
            state="stopping", message="正在关闭公网链接和只读站…", public_url="",
            tunnel_state="stopping", local_state="stopping",
        )
        _terminate(tunnel)
        _terminate(proxy)
        _terminate(owned_streamlit)
        if job:
            job.close()
        TOKEN_PATH.unlink(missing_ok=True)
        CURRENT_URL_PATH.unlink(missing_ok=True)
        control.STOP_PATH.unlink(missing_ok=True)
        control.PID_PATH.unlink(missing_ok=True)
        if final_error:
            _write_status(
                state="error", message=final_error, public_url="", local_state="stopped",
                tunnel_state="disconnected", port=None, pid=None, last_error=final_error,
            )
        else:
            _write_status(
                state="stopped", message="公网只读分享已停止；原链接已经失效", public_url="",
                local_state="stopped", tunnel_state="stopped", port=None, pid=None,
                reconnect_attempt=0, last_error="",
            )
        LOGGER.info("公网只读分享已停止")
        _release_instance_lock(instance_lock)


def main() -> None:
    parser = argparse.ArgumentParser(description="Yang-gumi 公网实时只读分享")
    parser.add_argument("--managed", action="store_true", help="由 Yang-gumi 数据管理页启动")
    args = parser.parse_args()
    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_signal)
    run_share(managed=args.managed)


if __name__ == "__main__":
    main()
