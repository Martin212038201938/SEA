"""Bilder (Screenshots) im Chat: prüfen, speichern, für das Sprachmodell aufbereiten.

Das Panel verkleinert Bilder schon im Browser (längste Kante 1600 px) und schickt sie als Base64 mit der Frage.
Hier wird nur noch geprüft (Typ an den Dateibytes, Größe, Anzahl) und unter data/chat-uploads/<thread>/ abgelegt.
Ans Modell gehen die Bilder nur mit der Frage, zu der sie gehören; ältere Bilder im Verlauf erscheinen dort als
Hinweis „[Screenshot]“, damit nicht jede Folgefrage alle Bilder erneut mitschickt.
"""
from __future__ import annotations

import base64
import re
import uuid
from pathlib import Path
from typing import Any

from ..config import BASE_DIR

UPLOAD_DIR = BASE_DIR / "data" / "chat-uploads"
MAX_IMAGES = 4
MAX_BYTES = 4_500_000  # Anthropic erlaubt 5 MB je Bild (Base64 wird größer, daher Puffer)
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
_NAME = re.compile(r"^[0-9a-f]{32}\.(png|jpg|webp|gif)$")


class AttachmentError(ValueError):
    pass


def _sniff(raw: bytes) -> str | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def _safe_thread(thread: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", thread)[:64] or "x"


def decode(items: Any) -> list[dict[str, Any]]:
    """Body-Feld images = [{"data": "<base64 oder data:-URL>"}, …] prüfen. Liefert [{"media_type", "raw"}]."""
    if not items:
        return []
    if not isinstance(items, list):
        raise AttachmentError("Bilder als Liste erwartet")
    if len(items) > MAX_IMAGES:
        raise AttachmentError(f"Höchstens {MAX_IMAGES} Bilder je Nachricht")
    out = []
    for it in items:
        data = it.get("data") if isinstance(it, dict) else None
        if not isinstance(data, str) or not data:
            raise AttachmentError("Bild ohne Inhalt")
        if data.startswith("data:"):
            data = data.split(",", 1)[-1]
        try:
            raw = base64.b64decode(data, validate=True)
        except Exception:
            raise AttachmentError("Bild ist nicht lesbar") from None
        if len(raw) > MAX_BYTES:
            raise AttachmentError("Bild zu groß (max. 4,5 MB)")
        media_type = _sniff(raw)
        if media_type is None:
            raise AttachmentError("Nur PNG, JPEG, WebP oder GIF")
        out.append({"media_type": media_type, "raw": raw})
    return out


def store(thread: str, images: list[dict[str, Any]]) -> list[str]:
    folder = UPLOAD_DIR / _safe_thread(thread)
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    for img in images:
        name = f"{uuid.uuid4().hex}.{_EXT[img['media_type']]}"
        (folder / name).write_bytes(img["raw"])
        names.append(name)
    return names


def path_for(thread: str, name: str) -> Path | None:
    if not _NAME.match(name or ""):
        return None
    p = UPLOAD_DIR / _safe_thread(thread) / name
    return p if p.is_file() else None


def for_model(images: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Neutrales Format für client.run_with_tools: [{"media_type", "data"(base64)}]."""
    return [{"media_type": i["media_type"], "data": base64.b64encode(i["raw"]).decode()} for i in images]
