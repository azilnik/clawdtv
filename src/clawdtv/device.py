"""Talking to the GeekMagic SmallTV Pro.

The firmware's HTTP server is loose about the spec: it has been observed sending
duplicate Content-Length headers and trailing bytes after announcing
Connection: close. Strict clients reject those responses, so this speaks HTTP
over a plain socket and reads deliberately forgivingly — we only ever need the
status line and a few bytes of body.

Display model, which is not obvious: Picture mode on the Pro is an autoplay
album, not an image viewer. There is no "show this file" call for a JPEG —
/set?img= is the GIF selector and returns FAIL for a still. The way to control
what is on screen is to keep exactly one image in /image/ and let autoplay land
on it.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass

TIMEOUT_S = 25
CHUNK = 8192


@dataclass
class Response:
    status: int
    body: bytes

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class DeviceError(Exception):
    pass


def _request(host: str, raw: bytes, read_body: bool = True) -> Response:
    try:
        with socket.create_connection((host, 80), timeout=TIMEOUT_S) as sock:
            sock.sendall(raw)
            buffer = b""
            while True:
                try:
                    chunk = sock.recv(CHUNK)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buffer += chunk
                if not read_body and b"\r\n\r\n" in buffer:
                    break
    except OSError as exc:
        raise DeviceError(f"device unreachable: {exc}") from None

    head, _, body = buffer.partition(b"\r\n\r\n")
    first = head.split(b"\r\n", 1)[0].split(b" ")
    try:
        status = int(first[1])
    except (IndexError, ValueError):
        raise DeviceError("malformed response from device") from None
    return Response(status=status, body=body)


def _headers(method: str, path: str, host: str, extra: str = "", length: int | None = None) -> bytes:
    lines = [f"{method} {path} HTTP/1.1", f"Host: {host}", "Connection: close"]
    if length is not None:
        lines.append(f"Content-Length: {length}")
    if extra:
        lines.append(extra)
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


class Device:
    def __init__(self, host: str):
        self.host = host

    def get(self, path: str) -> Response:
        return _request(self.host, _headers("GET", path, self.host))

    def model(self) -> str | None:
        """Reads /v.json. The Pro and the Ultra differ in ways that matter, most
        importantly the Picture theme number."""
        try:
            response = self.get("/v.json")
        except DeviceError:
            return None
        if not response.ok:
            return None
        import json

        try:
            return json.loads(response.text).get("m")
        except (json.JSONDecodeError, AttributeError):
            return None

    def free_bytes(self) -> int | None:
        try:
            import json

            return json.loads(self.get("/space.json").text).get("free")
        except (DeviceError, json.JSONDecodeError, AttributeError):
            return None

    def list_images(self) -> list[str]:
        """/filelist returns an HTML table rather than JSON."""
        import re

        try:
            html = self.get("/filelist?dir=/image/").text
        except DeviceError:
            return []
        return sorted({name for name in re.findall(r"/image/+([^'\"<>]+)", html)})

    def upload(self, filename: str, data: bytes) -> None:
        boundary = "----clawdtv7f3a9c2b"
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: image/jpeg\r\n\r\n"
        ).encode()
        tail = f"\r\n--{boundary}--\r\n".encode()
        body = head + data + tail
        request = _headers(
            "POST",
            "/doUpload?dir=/image/",
            self.host,
            extra=f"Content-Type: multipart/form-data; boundary={boundary}",
            length=len(body),
        ) + body
        response = _request(self.host, request)
        if not response.ok:
            raise DeviceError(f"upload rejected (HTTP {response.status})")

    def delete(self, filename: str) -> None:
        self.get(f"/delete?file=/image/{filename}")

    def set_theme(self, theme: int) -> None:
        response = self.get(f"/set?theme={theme}")
        if not response.ok:
            raise DeviceError(f"theme {theme} rejected (HTTP {response.status})")

    def current_theme(self) -> int | None:
        """State JSON lives under /.sys/ on the Pro and at the root on the Ultra.

        Trying both matters: if this returned None on one family, every push
        would look like theme drift and re-assert Picture mode, writing flash
        each tick for nothing.
        """
        import json

        for path in ("/.sys/app.json", "/app.json"):
            try:
                return int(json.loads(self.get(path).text)["theme"])
            except (DeviceError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
        return None

    def brightness(self) -> int | None:
        """Current brightness, 0-100.

        The device reports this inverted: /.sys/brt.json returns 185 when the
        panel is at 70. Writes are not inverted, so only the read is corrected.
        Verified on firmware V3.3.76EN by setting 30 and 70 and reading back
        225 and 185.
        """
        import json

        try:
            raw = int(json.loads(self.get("/.sys/brt.json").text)["brt"])
        except (DeviceError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None
        return 255 - raw if raw > 100 else raw

    def set_brightness(self, value: int) -> None:
        self.get(f"/set?brt={max(0, min(100, value))}")

    def set_album(self, interval_s: int = 60, autoplay: int = 1) -> None:
        self.get(f"/set?i_i={interval_s}&gif_loop=1&autoplay={autoplay}")

    def prune_album(self, keep: str) -> list[str]:
        """Autoplay cycles through whatever is in /image/, so anything besides our
        frame would periodically replace it on screen."""
        removed = []
        for name in self.list_images():
            if name != keep:
                self.delete(name)
                removed.append(name)
        return removed
