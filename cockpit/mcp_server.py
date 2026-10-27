"""MCP-Endpunkt (Streamable HTTP) für den ChatGPT-Team-Workspace und andere MCP-Clients.

Stellt dieselben Werkzeuge wie der Cockpit-Chat bereit. Zugriffsschutz: geheimer Pfad
/mcp/<MCP_TOKEN>/mcp (ChatGPT-Connectoren können keine eigenen Header setzen) oder
Authorization: Bearer <MCP_TOKEN>. OAuth kann später ergänzt werden (MCPServer unterstützt token_verifier).
"""
from __future__ import annotations

import inspect
import json
import logging
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import settings
from .db import SessionLocal
from .llm.tools import REGISTRY, ToolSpec

log = logging.getLogger(__name__)
MCP_USER = "chatgpt"

_TYPE_MAP = {"integer": int, "number": float, "string": str, "boolean": bool}


def _wrap(spec: ToolSpec):
    """Aus dem JSON-Schema eine Funktion mit expliziter Signatur bauen (für die Schema-Ableitung des SDK)."""
    props = spec.parameters.get("properties", {})
    required = set(spec.parameters.get("required", []))
    params = []
    annotations: dict[str, Any] = {}
    for name, schema in props.items():
        if schema.get("type") == "array":
            typ: Any = list[str]
        else:
            typ = _TYPE_MAP.get(schema.get("type", "string"), str)
        desc = schema.get("description", "")
        if schema.get("enum"):
            desc = (desc + " " if desc else "") + "Erlaubt: " + ", ".join(map(str, schema["enum"]))
        if name in required:
            ann = Annotated[typ, Field(description=desc)] if desc else typ
            params.append(inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, annotation=ann))
        else:
            ann = Annotated[typ | None, Field(description=desc)] if desc else (typ | None)
            params.append(inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=ann))
        annotations[name] = ann

    def impl(**kwargs):
        args = {k: v for k, v in kwargs.items() if v is not None}
        session = SessionLocal()
        try:
            result = spec.fn(session, MCP_USER, **args)
            session.commit()
        except Exception as exc:
            session.rollback()
            log.exception("MCP-Werkzeug %s fehlgeschlagen", spec.name)
            result = {"error": str(exc)[:500]}
        finally:
            session.close()
        return json.dumps(result, ensure_ascii=False, default=str)

    impl.__name__ = spec.name
    impl.__doc__ = spec.description
    impl.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    impl.__annotations__ = annotations
    return impl


def build_server() -> MCPServer:
    server = MCPServer(
        name="SEA-Cockpit Copilotenschule",
        instructions=(
            "Werkzeuge über die SEA-Datenbank der Copilotenschule (Google Ads, Microsoft Advertising, Leads, Experimente, "
            "Vorschläge). Alle Zahlen in EUR, Zeiträume in ISO-Daten. Keines der Werkzeuge verändert etwas in den Werbekonten. "
            "Schreibende Werkzeuge betreffen nur Experimente, Vorschlagsstatus und manuelle Leads im Cockpit."
        ),
    )
    for spec in REGISTRY.values():
        server.add_tool(_wrap(spec), name=spec.name, description=spec.description)
    return server


class TokenGate(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        token = settings.mcp_token
        if not token:
            return JSONResponse({"error": "MCP_TOKEN nicht konfiguriert"}, status_code=503)
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer ") and auth[7:].strip() == token:
            return await call_next(request)
        # Pfad-Geheimnis: Mount-Pfad enthält das Token, die App sieht nur den Rest; wir prüfen das Original
        path = request.scope.get("root_path", "") + request.url.path
        if f"/mcp/{token}" in path:
            return await call_next(request)
        return JSONResponse({"error": "nicht autorisiert"}, status_code=401)


mcp_server = build_server()


def mcp_asgi_app():
    app = mcp_server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    app.add_middleware(TokenGate)
    return app


def mount_path() -> str:
    return f"/mcp/{settings.mcp_token or 'kein-token'}"
