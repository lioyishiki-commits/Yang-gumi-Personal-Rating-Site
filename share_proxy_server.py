from __future__ import annotations

import argparse
import asyncio
import gzip
import http.client
import ipaddress
import logging
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect as websocket_connect
from websockets.exceptions import ConnectionClosed

from share_auth import SHARE_COOKIE_NAME, session_cookie_value, valid_session_cookie


PATH_TOKEN_PREFIX = "/_yanggumi_share/"


LOGGER = logging.getLogger("yanggumi.share_proxy")
READ_ONLY_HEADER = "X-Yanggumi-Read-Only"
HEALTH_PATH = "/__yanggumi_share_health"
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
IMMUTABLE_PATH_PREFIXES = (
    "/static/",
    "/app/static/share_assets/",
    "/media/",
)
STREAMLIT_WEBSOCKET_CONSTRUCTORS = (
    (
        b"this.websocket=new WebSocket(e,[`streamlit`,...t])",
        b"this.websocket=/(?:\\.wormhole\\.bar|\\.runlocal\\.eu|\\.plaintunnel\\.com)$/i.test(location.hostname)"
        b"?new WebSocket(e):new WebSocket(e,[`streamlit`,...t])",
    ),
    (
        b'this.websocket=new WebSocket(n,["streamlit",...t])',
        b"this.websocket=/(?:\\.wormhole\\.bar|\\.runlocal\\.eu|\\.plaintunnel\\.com)$/i.test(location.hostname)"
        b'?new WebSocket(n):new WebSocket(n,["streamlit",...t])',
    ),
)


def _load_token(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"无法读取分享令牌：{path}") from exc
    if not token:
        raise RuntimeError("分享令牌为空，拒绝启动代理。")
    return token


def _clean_query(query: bytes) -> str:
    pairs = parse_qsl(query.decode("utf-8", errors="replace"), keep_blank_values=True)
    return urlencode([(key, value) for key, value in pairs if key != "access"], doseq=True)


def _redirect_target(request: Request) -> str:
    query = _clean_query(request.scope.get("query_string", b""))
    return request.url.path + (f"?{query}" if query else "")


def _is_secure_request(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    if forwarded == "https" or request.url.scheme == "https":
        return True
    hostname = (request.url.hostname or "").strip("[]")
    try:
        address = ipaddress.ip_address(hostname)
        return not address.is_loopback
    except ValueError:
        return hostname.lower() not in {"", "localhost"}


def _query_token(request: Request) -> str:
    return str(request.query_params.get("access") or "")


def _path_token(request: Request) -> str:
    path = request.url.path
    if not path.startswith(PATH_TOKEN_PREFIX):
        return ""
    return path[len(PATH_TOKEN_PREFIX) :].strip("/")


def _authorized_request(request: Request, token: str) -> bool:
    return valid_session_cookie(request.cookies.get(SHARE_COOKIE_NAME), token)


def _forwarded_headers(request: Request, upstream_port: int) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, value in request.headers.items():
        lowered = name.lower()
        if lowered in HOP_BY_HOP_HEADERS or lowered in {
            "host",
            "content-length",
            "accept-encoding",
            READ_ONLY_HEADER.lower(),
        }:
            continue
        headers[name] = value
    # Streamlit may gzip already-compressed WebP files when it sees the
    # browser's Accept-Encoding header. Keep images byte-for-byte original;
    # the proxy applies gzip only after it has identified text responses.
    headers["Accept-Encoding"] = "identity"
    headers["Host"] = f"127.0.0.1:{upstream_port}"
    headers[READ_ONLY_HEADER] = "1"
    headers["X-Forwarded-Host"] = request.headers.get("host", "")
    headers["X-Forwarded-Proto"] = (
        request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
        or request.url.scheme
    )
    headers["X-Forwarded-For"] = request.client.host if request.client else "127.0.0.1"
    return headers


def _proxy_http_request(
    method: str,
    target: str,
    headers: dict[str, str],
    body: bytes,
    upstream_port: int,
) -> tuple[int, str, list[tuple[str, str]], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", upstream_port, timeout=30)
    try:
        connection.request(method, target, body=body or None, headers=headers)
        upstream = connection.getresponse()
        payload = upstream.read()
        return upstream.status, upstream.reason, upstream.getheaders(), payload
    finally:
        connection.close()


def _cache_control(path: str, upstream_value: str | None) -> str:
    if path.startswith("/static/js/index."):
        # The read-only edge applies a provider-compatibility patch to this
        # entry bundle. Do not let a visitor keep an unpatched copy forever.
        return "no-store"
    if path.startswith(IMMUTABLE_PATH_PREFIXES):
        return "public, max-age=31536000, immutable"
    if path in {"/", "/index.html", HEALTH_PATH} or path.startswith("/_stcore/"):
        return "no-store"
    return upstream_value or "no-cache"


def _compressible_content_type(value: str) -> bool:
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type.startswith("text/") or media_type in {
        "application/javascript",
        "application/json",
        "application/manifest+json",
        "application/wasm",
        "application/xml",
        "image/svg+xml",
    }


def _patch_streamlit_websocket_frontend(
    path: str,
    content_type: str,
    payload: bytes,
) -> tuple[bytes, bool]:
    if not path.startswith("/static/js/index.") or "javascript" not in content_type.lower():
        return payload, False
    updated = payload
    for source, replacement in STREAMLIT_WEBSOCKET_CONSTRUCTORS:
        if source in updated:
            updated = updated.replace(source, replacement, 1)
            return updated, True
    return payload, False


def create_app(token: str, upstream_port: int) -> Starlette:
    async def health(_request: Request) -> Response:
        return PlainTextResponse("ok", headers={"Cache-Control": "no-store"})

    async def proxy_http(request: Request) -> Response:
        supplied = _query_token(request) or _path_token(request)
        if supplied and hmac_compare(supplied, token):
            target = "/" if _path_token(request) else _redirect_target(request)
            response = RedirectResponse(target, status_code=303)
            response.set_cookie(
                SHARE_COOKIE_NAME,
                session_cookie_value(token),
                secure=_is_secure_request(request),
                httponly=True,
                samesite="lax",
                path="/",
            )
            response.headers["Cache-Control"] = "no-store"
            return response
        if not _authorized_request(request, token):
            return PlainTextResponse(
                "Yang-gumi 只读分享链接无效或已经停止。",
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            return PlainTextResponse("只读分享不允许写入请求。", status_code=405)

        body = await request.body()
        query = request.scope.get("query_string", b"").decode("latin-1")
        target = request.url.path + (f"?{query}" if query else "")
        status, _reason, upstream_headers, payload = await asyncio.to_thread(
            _proxy_http_request,
            request.method,
            target,
            _forwarded_headers(request, upstream_port),
            body,
            upstream_port,
        )
        content_type = next(
            (value for name, value in upstream_headers if name.lower() == "content-type"), ""
        )
        content_encoding = next(
            (value for name, value in upstream_headers if name.lower() == "content-encoding"), ""
        )
        if request.method == "GET" and status == 200 and not content_encoding:
            payload, _frontend_patched = _patch_streamlit_websocket_frontend(
                request.url.path,
                content_type,
                payload,
            )
        use_gzip = (
            request.method == "GET"
            and status == 200
            and len(payload) >= 1024
            and not content_encoding
            and "gzip" in request.headers.get("accept-encoding", "").lower()
            and _compressible_content_type(content_type)
        )
        if use_gzip:
            payload = gzip.compress(payload, compresslevel=6)
        elif request.method == "HEAD":
            payload = b""
        response = Response(payload, status_code=status)
        response.raw_headers = []
        upstream_cache: str | None = None
        for name, value in upstream_headers:
            lowered = name.lower()
            if lowered in HOP_BY_HOP_HEADERS or lowered == "content-length":
                continue
            if use_gzip and lowered in {"accept-ranges", "content-encoding", "vary"}:
                continue
            if lowered == "cache-control":
                upstream_cache = value
                continue
            if use_gzip and lowered == "etag":
                value = value if value.startswith("W/") else f"W/{value}"
            response.headers.append(name, value)
        if use_gzip:
            response.headers["Content-Encoding"] = "gzip"
            response.headers["Vary"] = "Accept-Encoding"
        response.headers["Cache-Control"] = _cache_control(request.url.path, upstream_cache)
        response.headers["Content-Length"] = str(len(payload))
        return response

    async def proxy_websocket(websocket: WebSocket) -> None:
        cookie = websocket.cookies.get(SHARE_COOKIE_NAME)
        if not valid_session_cookie(cookie, token):
            await websocket.close(code=4403, reason="invalid share session")
            return

        incoming_protocols = [
            item.strip()
            for item in websocket.headers.get("sec-websocket-protocol", "").split(",")
            if item.strip()
        ]
        protocols = ["streamlit"] if "streamlit" in incoming_protocols else None
        path = websocket.url.path
        query = websocket.scope.get("query_string", b"").decode("latin-1")
        upstream_url = f"ws://127.0.0.1:{upstream_port}{path}" + (f"?{query}" if query else "")
        forwarded = {
            READ_ONLY_HEADER: "1",
            "X-Forwarded-Host": websocket.headers.get("host", ""),
            "X-Forwarded-Proto": websocket.headers.get("x-forwarded-proto", "http").split(",", 1)[0].strip(),
            "X-Forwarded-For": websocket.client.host if websocket.client else "127.0.0.1",
        }
        if websocket.headers.get("cookie"):
            forwarded["Cookie"] = websocket.headers["cookie"]
        if websocket.headers.get("user-agent"):
            forwarded["User-Agent"] = websocket.headers["user-agent"]

        try:
            async with websocket_connect(
                upstream_url,
                origin=f"http://127.0.0.1:{upstream_port}",
                subprotocols=protocols,
                additional_headers=forwarded,
                compression="deflate",
                proxy=None,
                open_timeout=10,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
                max_size=None,
            ) as upstream:
                accepted_protocol = "streamlit" if upstream.subprotocol == "streamlit" else None
                await websocket.accept(subprotocol=accepted_protocol)

                async def downstream_to_upstream() -> None:
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            await upstream.close()
                            return
                        if message.get("bytes") is not None:
                            await upstream.send(message["bytes"])
                        elif message.get("text") is not None:
                            await upstream.send(message["text"])

                async def upstream_to_downstream() -> None:
                    async for message in upstream:
                        await websocket.send_bytes(message) if isinstance(message, bytes) else await websocket.send_text(message)

                tasks = {
                    asyncio.create_task(downstream_to_upstream()),
                    asyncio.create_task(upstream_to_downstream()),
                }
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    task.result()
        except (WebSocketDisconnect, ConnectionClosed, asyncio.CancelledError):
            return
        except Exception as exc:
            LOGGER.warning("WebSocket proxy failed: %s", type(exc).__name__)
            try:
                await websocket.close(code=1011, reason="upstream unavailable")
            except RuntimeError:
                pass

    return Starlette(
        routes=[
            Route(HEALTH_PATH, health, methods=["GET", "HEAD"]),
            WebSocketRoute("/{path:path}", proxy_websocket),
            Route("/{path:path}", proxy_http, methods=["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]),
        ]
    )


def hmac_compare(supplied: str, expected: str) -> bool:
    import hmac

    return bool(supplied and expected) and hmac.compare_digest(supplied, expected)


def main() -> None:
    parser = argparse.ArgumentParser(description="Yang-gumi authenticated read-only reverse proxy")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--upstream-port", default=8501, type=int)
    parser.add_argument("--token-file", required=True, type=Path)
    args = parser.parse_args()
    token = _load_token(args.token_file)
    uvicorn.run(
        create_app(token, args.upstream_port),
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
        access_log=False,
        ws_per_message_deflate=True,
        timeout_keep_alive=30,
    )


if __name__ == "__main__":
    main()
