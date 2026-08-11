# Yang-gumi release: 1.3.0
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import http.client
import json
import sys
from collections.abc import Iterable
from urllib.parse import urlencode

from websockets.asyncio.client import ClientConnection, connect


CONTROL_URL = "wss://runlocal.eu/tunnel/websocket"
CONTROL_TOPIC = "tunnel:connect"
FILTERED_REQUEST_HEADERS = {"host", "accept-encoding"}
FILTERED_WEBSOCKET_HEADERS = {
    "host",
    "upgrade",
    "connection",
    "sec-websocket-key",
    "sec-websocket-version",
    "sec-websocket-extensions",
    "sec-websocket-protocol",
}


def _filtered_headers(
    values: Iterable[Iterable[str]] | None,
    blocked: set[str],
) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for pair in values or ():
        name, value = tuple(pair)
        if name.lower() not in blocked:
            result.append((name, value))
    return result


def _request_local(
    port: int,
    payload: dict[str, object],
) -> dict[str, object]:
    path = str(payload.get("path") or "/")
    query = str(payload.get("query_string") or "")
    target = f"{path}?{query}" if query else path
    body_value = payload.get("body")
    request_body: bytes | None = None
    if body_value:
        text = str(body_value)
        if payload.get("body_encoding") == "base64":
            request_body = base64.b64decode(text)
        else:
            request_body = text.encode("utf-8")
    headers = dict(
        _filtered_headers(
            payload.get("headers") if isinstance(payload.get("headers"), list) else None,
            FILTERED_REQUEST_HEADERS,
        )
    )
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        connection.request(
            str(payload.get("method") or "GET"),
            target,
            body=request_body,
            headers=headers,
        )
        response = connection.getresponse()
        body = response.read()
        return {
            "request_id": payload.get("request_id"),
            "status": response.status,
            "headers": list(response.getheaders()),
            "body": base64.b64encode(body).decode("ascii"),
            "body_encoding": "base64",
        }
    except OSError as exc:
        body = f"Could not connect to localhost:{port}: {exc}".encode("utf-8")
        return {
            "request_id": payload.get("request_id"),
            "status": 502,
            "headers": [["content-type", "text/plain; charset=utf-8"]],
            "body": base64.b64encode(body).decode("ascii"),
            "body_encoding": "base64",
        }
    finally:
        connection.close()


class RunlocalClient:
    def __init__(self, port: int, subdomain: str = "") -> None:
        self.port = port
        self.subdomain = subdomain
        self.ref = 0
        self.join_ref = ""
        self.control: ClientConnection | None = None
        self.send_lock = asyncio.Lock()
        self.local_websockets: dict[str, ClientConnection] = {}
        self.tasks: set[asyncio.Task[object]] = set()

    def next_ref(self) -> str:
        self.ref += 1
        return str(self.ref)

    async def send(self, topic: str, event: str, payload: dict[str, object]) -> None:
        if self.control is None:
            return
        message = json.dumps(
            [self.join_ref or None, self.next_ref(), topic, event, payload],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        async with self.send_lock:
            await self.control.send(message)

    def spawn(self, coroutine: object) -> None:
        task = asyncio.create_task(coroutine)  # type: ignore[arg-type]
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def heartbeat(self) -> None:
        while True:
            await asyncio.sleep(30)
            await self.send("phoenix", "heartbeat", {})

    async def handle_http(self, topic: str, payload: dict[str, object]) -> None:
        response = await asyncio.to_thread(_request_local, self.port, payload)
        await self.send(topic, "http_response", response)

    async def relay_local_websocket(
        self,
        ws_id: str,
        topic: str,
        local: ClientConnection,
    ) -> None:
        try:
            async for message in local:
                if isinstance(message, bytes):
                    data = base64.b64encode(message).decode("ascii")
                    opcode = "binary"
                else:
                    data = message
                    opcode = "text"
                await self.send(
                    topic,
                    "ws_frame",
                    {"ws_id": ws_id, "data": data, "opcode": opcode},
                )
        finally:
            self.local_websockets.pop(ws_id, None)
            await self.send(topic, "ws_close", {"ws_id": ws_id})

    async def handle_ws_upgrade(self, topic: str, payload: dict[str, object]) -> None:
        ws_id = str(payload.get("ws_id") or "")
        path = str(payload.get("path") or "/")
        query = str(payload.get("query_string") or "")
        target = f"{path}?{query}" if query else path
        headers = _filtered_headers(
            payload.get("headers") if isinstance(payload.get("headers"), list) else None,
            FILTERED_WEBSOCKET_HEADERS,
        )
        try:
            local = await connect(
                f"ws://127.0.0.1:{self.port}{target}",
                additional_headers=headers,
                compression=None,
                max_size=None,
                open_timeout=20,
                proxy=None,
            )
        except Exception:
            await self.send(topic, "ws_close", {"ws_id": ws_id})
            return
        self.local_websockets[ws_id] = local
        self.spawn(self.relay_local_websocket(ws_id, topic, local))

    async def handle_ws_client_frame(self, payload: dict[str, object]) -> None:
        ws_id = str(payload.get("ws_id") or "")
        local = self.local_websockets.get(ws_id)
        if local is None:
            return
        data = payload.get("data")
        if payload.get("opcode") == "binary":
            await local.send(base64.b64decode(str(data or "")))
        else:
            await local.send(str(data or ""))

    async def handle_ws_close(self, payload: dict[str, object]) -> None:
        ws_id = str(payload.get("ws_id") or "")
        local = self.local_websockets.pop(ws_id, None)
        if local is not None:
            await local.close()

    async def close_local_websockets(self) -> None:
        sockets = list(self.local_websockets.values())
        self.local_websockets.clear()
        for local in sockets:
            with contextlib.suppress(Exception):
                await local.close()

    async def run(self) -> None:
        query_values = {"vsn": "2.0.0", "caps": "binary-bodies"}
        if self.subdomain:
            query_values["subdomain"] = self.subdomain
        query = urlencode(query_values)
        async with connect(
            f"{CONTROL_URL}?{query}",
            compression=None,
            max_size=None,
            open_timeout=20,
            ping_interval=None,
        ) as control:
            self.control = control
            self.join_ref = self.next_ref()
            await control.send(
                json.dumps(
                    [self.join_ref, self.join_ref, CONTROL_TOPIC, "phx_join", {}],
                    separators=(",", ":"),
                )
            )
            heartbeat = asyncio.create_task(self.heartbeat())
            try:
                async for raw in control:
                    message = json.loads(raw)
                    _join, _ref, topic, event, payload = message
                    payload = payload if isinstance(payload, dict) else {}
                    if event == "phx_reply" and topic == CONTROL_TOPIC:
                        if payload.get("status") != "ok":
                            raise RuntimeError(f"runlocal join failed: {payload!r}")
                    elif event == "tunnel_created":
                        url = str(payload.get("url") or "").rstrip("/")
                        if not url:
                            raise RuntimeError("runlocal did not return a public URL")
                        print(f"RUNLOCAL_URL={url}", flush=True)
                    elif event == "http_request":
                        self.spawn(self.handle_http(topic, payload))
                    elif event == "ws_upgrade":
                        self.spawn(self.handle_ws_upgrade(topic, payload))
                    elif event == "ws_client_frame":
                        self.spawn(self.handle_ws_client_frame(payload))
                    elif event == "ws_close":
                        self.spawn(self.handle_ws_close(payload))
                    elif event == "phx_close":
                        raise RuntimeError("runlocal closed the tunnel")
            finally:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
                await self.close_local_websockets()
                self.control = None


def main() -> int:
    parser = argparse.ArgumentParser(description="Account-free Runlocal HTTPS tunnel")
    parser.add_argument("port", type=int)
    parser.add_argument("--subdomain", default="")
    args = parser.parse_args()
    try:
        asyncio.run(RunlocalClient(args.port, args.subdomain).run())
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"RUNLOCAL_ERROR={type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
