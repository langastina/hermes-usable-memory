from __future__ import annotations

import json
from pprint import pprint

from . import DEFAULT_MCP_URL, UsableMcpClient, _load_config


def _client_from_config() -> UsableMcpClient:
    config = _load_config()
    token = config.get("bearer_token", "")
    if not token:
        raise SystemExit("Usable token is not configured. Set USABLE_MCP_BEARER_TOKEN or run hermes memory setup.")
    return UsableMcpClient(
        mcp_url=config.get("mcp_url", DEFAULT_MCP_URL),
        bearer_token=token,
        timeout_secs=float(config.get("timeout_secs", 20.0)),
    )


def usable_command(args) -> None:
    config = _load_config()

    if args.usable_command == "status":
        print(f"mcp_url: {config.get('mcp_url', DEFAULT_MCP_URL)}")
        print(f"workspace_id: {config.get('workspace_id') or '(unset)'}")
        print(f"repository: {config.get('repository') or '(unset)'}")
        print(f"token_configured: {'yes' if config.get('bearer_token') else 'no'}")
        client = _client_from_config()
        workspaces = client.call_tool("list-workspaces", {"outputFormat": "json"})
        print(f"visible_workspaces: {workspaces.get('count', 0)}")
        return

    if args.usable_command == "list-workspaces":
        result = _client_from_config().call_tool("list-workspaces", {"outputFormat": "json"})
        pprint(result)
        return

    if args.usable_command == "search":
        workspace_id = args.workspace_id or config.get("workspace_id")
        if not workspace_id:
            raise SystemExit("workspace_id is required. Set USABLE_WORKSPACE_ID or pass --workspace-id.")
        result = _client_from_config().call_tool(
            "search-memory-fragments",
            {
                "workspaceId": workspace_id,
                "query": args.query,
                "limit": args.limit,
                "outputFormat": "json",
            },
        )
        print(json.dumps(result, indent=2))
        return

    raise SystemExit("Unknown usable subcommand")


def register_cli(subparser) -> None:
    subs = subparser.add_subparsers(dest="usable_command")

    subs.add_parser("status", help="Show Usable plugin configuration and connectivity")
    subs.add_parser("list-workspaces", help="List accessible Usable workspaces")

    search = subs.add_parser("search", help="Search fragments in the configured Usable workspace")
    search.add_argument("query", help="Natural-language search query")
    search.add_argument("--workspace-id", help="Override the configured workspace ID")
    search.add_argument("--limit", type=int, default=5, help="Maximum results to return")

    subparser.set_defaults(func=usable_command)
