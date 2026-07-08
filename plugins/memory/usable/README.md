# Usable Memory Provider

Usable-backed memory provider for Hermes Agent using the Usable MCP HTTP endpoint at `https://usable.dev/api/mcp`.

## What it does

- exposes Hermes tools for workspace listing, fragment search, fragment listing, fragment fetch, and fragment storage
- supports background prefetch recall via semantic search
- can mirror curated built-in Hermes memory writes into Usable
- can optionally capture a bounded session transcript at session end

## Files

Copy this directory into your Hermes checkout at:

```bash
<hermes-repo>/plugins/memory/usable
```

Or use the helper script from this repository:

```bash
./scripts/install_into_hermes.sh /path/to/hermes-agent
# or
./scripts/install_into_hermes.sh ~/.hermes
```

Then activate it:

```bash
hermes config set memory.provider usable
hermes memory setup
```

Or configure it manually:

```bash
hermes config set memory.provider usable
echo 'USABLE_MCP_BEARER_TOKEN=your-token' >> "$HERMES_HOME/.env"
cat > "$HERMES_HOME/usable.json" <<'JSON'
{
  "workspace_id": "your-workspace-uuid",
  "repository": "optional-repo-name"
}
JSON
```

## Required configuration

- `workspace_id`: target workspace UUID in `$HERMES_HOME/usable.json` or `USABLE_WORKSPACE_ID`
- authentication, either:
  - native Hermes MCP OAuth cache for the `usable` MCP server (preferred), or
  - `USABLE_MCP_BEARER_TOKEN` / `MCP_BEARER_TOKEN` for a manually supplied bearer token

When Hermes already has `mcp_servers.usable` configured with OAuth, this provider reuses `$HERMES_HOME/mcp-tokens/usable.json` and refreshes the access token from the cached refresh token when needed. You do not need to copy the short-lived access token into `.env`.

The provider also accepts `MCP_BEARER_TOKEN` as a fallback if you already use that env var elsewhere.

## Optional configuration

Configuration file: `$HERMES_HOME/usable.json`

```json
{
  "workspace_id": "2e5db7e8-5bf7-44de-8312-4472d531f6bc",
  "repository": "my-repo",
  "default_tags": ["team:core"],
  "search_tags": ["repo:my-repo"],
  "fragment_type_id": "",
  "auto_recall": true,
  "auto_capture": false,
  "capture_memory_writes": true,
  "prefetch_limit": 5,
  "max_captured_turns": 10,
  "timeout_secs": 20.0,
  "title_prefix": "Hermes"
}
```

## Hermes tools

- `usable_workspaces`: list accessible Usable workspaces
- `usable_search`: semantic search in the configured workspace
- `usable_list`: metadata listing with SQL-like filters
- `usable_get`: fetch full fragment content by fragment ID
- `usable_store`: create a new durable fragment

## CLI

When `usable` is the active memory provider, Hermes will expose:

```bash
hermes usable status
hermes usable list-workspaces
hermes usable search "what do we know about auth?"
```

## Notes

- The provider talks to the MCP endpoint directly over JSON-RPC, so it does not require a separate Usable SDK.
- `auto_capture` is disabled by default because storing every turn as memory is usually too noisy.
- Mirroring built-in Hermes memory writes is enabled by default because those entries are already curated.
