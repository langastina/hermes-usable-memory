from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

DEFAULT_MCP_URL = "https://usable.dev/api/mcp"
DEFAULT_TIMEOUT_SECS = 20.0
DEFAULT_PREFETCH_LIMIT = 5
DEFAULT_MAX_CAPTURED_TURNS = 10


def _tool_error(message: str) -> str:
    return json.dumps({"error": message})


def _boolish(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _intish(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _listish(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        logger.debug("Failed to read %s: %s", path, exc)
    return None


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def _oauth_token_from_mcp_cache(server_name: str = "usable") -> str:
    """Return a fresh bearer token from Hermes' native MCP OAuth cache.

    The Usable MCP server is already configured in Hermes with OAuth PKCE.
    This memory provider can reuse that cache so users do not need to copy a
    short-lived access token into .env. If the cached access token is expired
    and a refresh token is present, refresh it synchronously and persist the
    updated token file in the same layout Hermes' MCP client uses.
    """
    from hermes_constants import get_hermes_home

    token_dir = get_hermes_home() / "mcp-tokens"
    token_path = token_dir / f"{server_name}.json"
    client_path = token_dir / f"{server_name}.client.json"
    meta_path = token_dir / f"{server_name}.meta.json"

    token_data = _read_json(token_path) or {}
    access_token = str(token_data.get("access_token") or "")
    expires_at = float(token_data.get("expires_at") or 0)
    if access_token and expires_at > time.time() + 60:
        return access_token

    refresh_token = str(token_data.get("refresh_token") or "")
    if not refresh_token:
        return access_token

    client_data = _read_json(client_path) or {}
    meta_data = _read_json(meta_path) or {}
    token_endpoint = str(meta_data.get("token_endpoint") or "")
    client_id = str(client_data.get("client_id") or "")
    if not token_endpoint or not client_id:
        return access_token

    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    client_secret = client_data.get("client_secret")
    if client_secret:
        form["client_secret"] = str(client_secret)

    request = urllib.request.Request(
        token_endpoint,
        data=urllib.parse.urlencode(form).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20.0) as response:
            refreshed = json.loads(response.read().decode("utf-8") or "{}")
    except Exception as exc:
        logger.warning("Failed to refresh Usable MCP OAuth token: %s", exc)
        return access_token

    new_access_token = str(refreshed.get("access_token") or "")
    if not new_access_token:
        return access_token

    expires_in = int(refreshed.get("expires_in") or token_data.get("expires_in") or 300)
    refreshed.setdefault("refresh_token", refresh_token)
    refreshed.setdefault("token_type", token_data.get("token_type", "Bearer"))
    refreshed.setdefault("scope", token_data.get("scope", ""))
    refreshed["expires_in"] = expires_in
    refreshed["expires_at"] = time.time() + expires_in
    _write_json(token_path, refreshed)
    return new_access_token


def _load_config() -> Dict[str, Any]:
    from hermes_constants import get_hermes_home

    config = {
        "mcp_url": os.environ.get("USABLE_MCP_URL", DEFAULT_MCP_URL),
        "bearer_token": (
            os.environ.get("USABLE_MCP_BEARER_TOKEN")
            or os.environ.get("MCP_BEARER_TOKEN", "")
        ),
        "workspace_id": os.environ.get("USABLE_WORKSPACE_ID", ""),
        "repository": os.environ.get("USABLE_REPOSITORY", ""),
        "default_tags": _listish(os.environ.get("USABLE_DEFAULT_TAGS")),
        "search_tags": _listish(os.environ.get("USABLE_SEARCH_TAGS")),
        "fragment_type_id": os.environ.get("USABLE_FRAGMENT_TYPE_ID", ""),
        "auto_recall": _boolish(os.environ.get("USABLE_AUTO_RECALL"), True),
        "auto_capture": _boolish(os.environ.get("USABLE_AUTO_CAPTURE"), False),
        "capture_memory_writes": _boolish(os.environ.get("USABLE_CAPTURE_MEMORY_WRITES"), True),
        "prefetch_limit": _intish(os.environ.get("USABLE_PREFETCH_LIMIT"), DEFAULT_PREFETCH_LIMIT),
        "max_captured_turns": _intish(
            os.environ.get("USABLE_MAX_CAPTURED_TURNS"), DEFAULT_MAX_CAPTURED_TURNS
        ),
        "timeout_secs": float(os.environ.get("USABLE_TIMEOUT_SECS", DEFAULT_TIMEOUT_SECS)),
        "title_prefix": os.environ.get("USABLE_TITLE_PREFIX", "Hermes"),
    }

    config_path = get_hermes_home() / "usable.json"
    if config_path.exists():
        try:
            file_config = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(file_config, dict):
                config.update({key: value for key, value in file_config.items() if value not in (None, "")})
        except Exception as exc:
            logger.warning("Failed to parse usable.json: %s", exc)

    if not config.get("bearer_token"):
        config["bearer_token"] = _oauth_token_from_mcp_cache("usable")

    config["default_tags"] = _listish(config.get("default_tags"))
    config["search_tags"] = _listish(config.get("search_tags"))
    config["auto_recall"] = _boolish(config.get("auto_recall"), True)
    config["auto_capture"] = _boolish(config.get("auto_capture"), False)
    config["capture_memory_writes"] = _boolish(config.get("capture_memory_writes"), True)
    config["prefetch_limit"] = max(1, min(_intish(config.get("prefetch_limit"), DEFAULT_PREFETCH_LIMIT), 20))
    config["max_captured_turns"] = max(
        1, min(_intish(config.get("max_captured_turns"), DEFAULT_MAX_CAPTURED_TURNS), 50)
    )
    config["timeout_secs"] = max(1.0, float(config.get("timeout_secs", DEFAULT_TIMEOUT_SECS)))
    return config


class UsableMcpClient:
    def __init__(self, mcp_url: str, bearer_token: str, timeout_secs: float = DEFAULT_TIMEOUT_SECS):
        self._mcp_url = mcp_url
        self._bearer_token = bearer_token
        self._timeout_secs = timeout_secs
        self._session_id: Optional[str] = None
        self._rpc_id = 0
        self._lock = threading.RLock()
        self._tool_cache: Optional[List[Dict[str, Any]]] = None

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _request(self, payload: Dict[str, Any], *, include_session: bool = True) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._bearer_token}",
            "Content-Type": "application/json",
        }
        if include_session and self._session_id:
            headers["mcp-session-id"] = self._session_id

        request = urllib.request.Request(
            self._mcp_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_secs) as response:
                raw = response.read().decode("utf-8") or "{}"
                session_id = response.headers.get("mcp-session-id")
                if session_id:
                    self._session_id = session_id
                if response.status == 204:
                    return {}
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Usable MCP HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Usable MCP connection failed: {exc.reason}") from exc

    def _initialize(self) -> None:
        if self._session_id:
            return
        init_payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {
                    "name": "hermes-usable-plugin",
                    "version": "0.1.0",
                },
            },
        }
        result = self._request(init_payload, include_session=False)
        if "error" in result:
            raise RuntimeError(result["error"].get("message", "MCP initialize failed"))
        self._request(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            include_session=True,
        )

    def list_tools(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._initialize()
            if self._tool_cache is not None:
                return self._tool_cache
            result = self._request(
                {
                    "jsonrpc": "2.0",
                    "id": self._next_id(),
                    "method": "tools/list",
                    "params": {},
                }
            )
            if "error" in result:
                raise RuntimeError(result["error"].get("message", "tools/list failed"))
            tools = result.get("result", {}).get("tools", [])
            self._tool_cache = tools
            return tools

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._initialize()
            result = self._request(
                {
                    "jsonrpc": "2.0",
                    "id": self._next_id(),
                    "method": "tools/call",
                    "params": {
                        "name": name,
                        "arguments": arguments,
                    },
                }
            )
            if "error" in result:
                raise RuntimeError(result["error"].get("message", f"{name} failed"))
            payload = result.get("result", {})
            structured = payload.get("structuredContent")
            if structured is not None:
                return structured

            content = payload.get("content", [])
            if content:
                text = content[0].get("text", "")
                if text:
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"text": text}
            return {}


WORKSPACES_SCHEMA = {
    "name": "usable_workspaces",
    "description": "List the Usable workspaces visible to this Hermes installation.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

SEARCH_SCHEMA = {
    "name": "usable_search",
    "description": (
        "Search the configured Usable workspace semantically for relevant fragments."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural-language search query."},
            "limit": {"type": "integer", "description": "Maximum results to return. Default 5."},
            "workspace_id": {"type": "string", "description": "Optional workspace override."},
        },
        "required": ["query"],
    },
}

LIST_SCHEMA = {
    "name": "usable_list",
    "description": "List fragments in the configured Usable workspace using SQL-like filters.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Optional SQL-like filter query."},
            "limit": {"type": "integer", "description": "Maximum results to return. Default 10."},
            "order_by": {"type": "string", "description": "Optional ORDER BY clause."},
            "workspace_id": {"type": "string", "description": "Optional workspace override."},
        },
        "required": [],
    },
}

GET_SCHEMA = {
    "name": "usable_get",
    "description": "Fetch the full content for a specific Usable fragment.",
    "parameters": {
        "type": "object",
        "properties": {
            "fragment_id": {"type": "string", "description": "The fragment UUID."},
        },
        "required": ["fragment_id"],
    },
}

STORE_SCHEMA = {
    "name": "usable_store",
    "description": (
        "Store a durable fragment in Usable. Use for verified project knowledge, not raw chat logs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Fragment title."},
            "content": {"type": "string", "description": "Markdown content for the fragment."},
            "summary": {"type": "string", "description": "Optional short summary."},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags to attach to the fragment.",
            },
            "workspace_id": {"type": "string", "description": "Optional workspace override."},
        },
        "required": ["title", "content"],
    },
}


class UsableMemoryProvider(MemoryProvider):
    def __init__(self):
        self._config: Dict[str, Any] = {}
        self._client: Optional[UsableMcpClient] = None
        self._client_lock = threading.Lock()
        self._prefetch_result = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: Optional[threading.Thread] = None
        self._session_id = ""
        self._agent_identity = "default"
        self._captured_turns: List[Dict[str, str]] = []
        self._captured_turns_lock = threading.Lock()

    @property
    def name(self) -> str:
        return "usable"

    def is_available(self) -> bool:
        config = _load_config()
        return bool(config.get("bearer_token") and config.get("workspace_id"))

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "bearer_token",
                "description": "Usable MCP bearer token",
                "secret": True,
                "required": True,
                "env_var": "USABLE_MCP_BEARER_TOKEN",
                "url": "https://usable.dev",
            },
            {
                "key": "workspace_id",
                "description": "Usable workspace UUID",
                "required": True,
            },
            {
                "key": "repository",
                "description": "Optional repository name for repo:<name> tagging",
                "default": "",
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        config_path = Path(hermes_home) / "usable.json"
        existing: Dict[str, Any] = {}
        if config_path.exists():
            try:
                existing = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        existing.update(values)
        config_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

    def initialize(self, session_id: str, **kwargs) -> None:
        self._config = _load_config()
        self._session_id = session_id
        self._agent_identity = kwargs.get("agent_identity") or "default"
        self._client = None
        self._prefetch_result = ""
        self._captured_turns = []

    def _get_client(self) -> UsableMcpClient:
        with self._client_lock:
            if self._client is None:
                self._client = UsableMcpClient(
                    mcp_url=self._config.get("mcp_url", DEFAULT_MCP_URL),
                    bearer_token=self._config.get("bearer_token", ""),
                    timeout_secs=float(self._config.get("timeout_secs", DEFAULT_TIMEOUT_SECS)),
                )
            return self._client

    def _workspace_id(self, override: str = "") -> str:
        workspace_id = override or self._config.get("workspace_id", "")
        if not workspace_id:
            raise RuntimeError("Usable workspace_id is not configured")
        return workspace_id

    def _base_tags(self) -> List[str]:
        tags = list(self._config.get("default_tags", []))
        repository = self._config.get("repository", "")
        if repository:
            repo_tag = f"repo:{repository}"
            if repo_tag not in tags:
                tags.append(repo_tag)
        return tags

    def _format_prefetch(self, fragments: List[Dict[str, Any]]) -> str:
        lines = []
        for fragment in fragments[: self._config.get("prefetch_limit", DEFAULT_PREFETCH_LIMIT)]:
            title = fragment.get("title", "Untitled")
            summary = fragment.get("summary") or ""
            preview = ""
            previews = fragment.get("chunkPreviews") or []
            if previews:
                preview = previews[0].get("preview", "").strip().replace("\n", " ")
            parts = [title]
            if summary:
                parts.append(summary)
            if preview:
                parts.append(preview[:220])
            lines.append("- " + " | ".join(part for part in parts if part))
        return "\n".join(lines)

    def system_prompt_block(self) -> str:
        workspace_id = self._config.get("workspace_id", "")
        return (
            "# Usable Memory\n"
            f"Active. Workspace: {workspace_id}.\n"
            "Use usable_search before making assumptions about prior project context. "
            "Use usable_store only for durable, verified knowledge worth keeping."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._config.get("auto_recall", True):
            return ""
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=4.0)
        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_result = ""
        if not result:
            return ""
        return f"## Usable Recall\n{result}"

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if not self._config.get("auto_recall", True):
            return

        def _run() -> None:
            try:
                arguments = {
                    "workspaceId": self._workspace_id(),
                    "query": query,
                    "limit": self._config.get("prefetch_limit", DEFAULT_PREFETCH_LIMIT),
                    "tags": self._config.get("search_tags", []) or None,
                    "outputFormat": "json",
                }
                cleaned_args = {key: value for key, value in arguments.items() if value not in (None, "", [])}
                result = self._get_client().call_tool(
                    "search-memory-fragments",
                    cleaned_args,
                )
                fragments = result.get("fragments", [])
                formatted = self._format_prefetch(fragments)
                with self._prefetch_lock:
                    self._prefetch_result = formatted
            except Exception as exc:
                logger.debug("Usable prefetch failed: %s", exc)

        self._prefetch_thread = threading.Thread(target=_run, daemon=True, name="usable-prefetch")
        self._prefetch_thread.start()

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not self._config.get("auto_capture", False):
            return
        with self._captured_turns_lock:
            self._captured_turns.append(
                {"user": user_content.strip(), "assistant": assistant_content.strip()}
            )
            max_turns = self._config.get("max_captured_turns", DEFAULT_MAX_CAPTURED_TURNS)
            if len(self._captured_turns) > max_turns:
                self._captured_turns = self._captured_turns[-max_turns:]

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        self._flush_captured_turns()

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        if not self._config.get("capture_memory_writes", True):
            return
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        title = f"{self._config.get('title_prefix', 'Hermes')} memory write: {target}"
        body = (
            f"# Built-in memory sync\n\n"
            f"- Action: `{action}`\n"
            f"- Target: `{target}`\n"
            f"- Session: `{self._session_id}`\n"
            f"- Time: `{timestamp}`\n\n"
            f"## Content\n\n{content.strip()}\n"
        )
        try:
            self._store_fragment(
                title=title,
                content=body,
                summary=f"Mirrored Hermes memory {action} for {target}.",
                tags=["hermes", "memory-write", f"memory-target:{target}", f"memory-action:{action}"],
            )
        except Exception as exc:
            logger.warning("Failed to mirror built-in memory write to Usable: %s", exc)

    def _flush_captured_turns(self) -> None:
        if not self._config.get("auto_capture", False):
            return
        with self._captured_turns_lock:
            turns = list(self._captured_turns)
            self._captured_turns.clear()
        if not turns:
            return

        sections = []
        for idx, turn in enumerate(turns, start=1):
            sections.append(
                f"## Turn {idx}\n\n"
                f"### User\n{turn['user']}\n\n"
                f"### Assistant\n{turn['assistant']}\n"
            )
        title = f"{self._config.get('title_prefix', 'Hermes')} session {self._session_id}"
        summary = f"Hermes session transcript for {len(turns)} captured turn(s)."
        try:
            self._store_fragment(
                title=title,
                content="\n\n".join(sections),
                summary=summary,
                tags=["hermes", "session-transcript"],
            )
        except Exception as exc:
            logger.warning("Failed to persist captured Hermes session to Usable: %s", exc)

    def _store_fragment(
        self,
        *,
        title: str,
        content: str,
        summary: str = "",
        tags: Optional[List[str]] = None,
        workspace_id: str = "",
    ) -> Dict[str, Any]:
        merged_tags = self._base_tags()
        for tag in tags or []:
            if tag not in merged_tags:
                merged_tags.append(tag)
        arguments = {
            "workspaceId": self._workspace_id(workspace_id),
            "title": title,
            "content": content,
            "summary": summary or None,
            "tags": merged_tags or None,
            "repository": self._config.get("repository") or None,
            "fragmentTypeId": self._config.get("fragment_type_id") or None,
            "outputFormat": "json",
        }
        cleaned_args = {key: value for key, value in arguments.items() if value not in (None, "", [])}
        return self._get_client().call_tool("create-memory-fragment", cleaned_args)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [WORKSPACES_SCHEMA, SEARCH_SCHEMA, LIST_SCHEMA, GET_SCHEMA, STORE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        try:
            if tool_name == "usable_workspaces":
                result = self._get_client().call_tool("list-workspaces", {"outputFormat": "json"})
                return json.dumps(result)

            if tool_name == "usable_search":
                query = (args.get("query") or "").strip()
                if not query:
                    return _tool_error("Missing required parameter: query")
                arguments = {
                    "workspaceId": self._workspace_id(args.get("workspace_id", "")),
                    "query": query,
                    "limit": max(1, min(_intish(args.get("limit"), DEFAULT_PREFETCH_LIMIT), 20)),
                    "tags": self._config.get("search_tags", []) or None,
                    "outputFormat": "json",
                }
                cleaned_args = {key: value for key, value in arguments.items() if value not in (None, "", [])}
                result = self._get_client().call_tool(
                    "search-memory-fragments",
                    cleaned_args,
                )
                return json.dumps(result)

            if tool_name == "usable_list":
                arguments = {
                    "workspaceId": self._workspace_id(args.get("workspace_id", "")),
                    "query": args.get("query") or None,
                    "limit": max(1, min(_intish(args.get("limit"), 10), 50)),
                    "orderBy": args.get("order_by") or "updated_at DESC",
                    "outputFormat": "json",
                }
                cleaned_args = {key: value for key, value in arguments.items() if value not in (None, "", [])}
                result = self._get_client().call_tool(
                    "list-memory-fragments",
                    cleaned_args,
                )
                return json.dumps(result)

            if tool_name == "usable_get":
                fragment_id = (args.get("fragment_id") or "").strip()
                if not fragment_id:
                    return _tool_error("Missing required parameter: fragment_id")
                result = self._get_client().call_tool(
                    "get-memory-fragment-content",
                    {
                        "fragmentId": fragment_id,
                        "outputFormat": "json",
                    },
                )
                return json.dumps(result)

            if tool_name == "usable_store":
                title = (args.get("title") or "").strip()
                content = (args.get("content") or "").strip()
                if not title:
                    return _tool_error("Missing required parameter: title")
                if not content:
                    return _tool_error("Missing required parameter: content")
                result = self._store_fragment(
                    title=title,
                    content=content,
                    summary=(args.get("summary") or "").strip(),
                    tags=_listish(args.get("tags")),
                    workspace_id=args.get("workspace_id", ""),
                )
                return json.dumps(result)

            return _tool_error(f"Unknown tool: {tool_name}")
        except Exception as exc:
            logger.exception("Usable tool call failed: %s", exc)
            return _tool_error(str(exc))

    def shutdown(self) -> None:
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=5.0)
        self._flush_captured_turns()


def register(ctx) -> None:
    ctx.register_memory_provider(UsableMemoryProvider())
