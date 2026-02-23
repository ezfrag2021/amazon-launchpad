# OpenCode Project Configuration

This project uses local OpenCode configuration and MCP servers from the repo root.

## Configuration Isolation

Isolation is controlled by `OPENCODE_DISABLE_GLOBAL_CONFIG=true` in `.env`.

When set, OpenCode loads project-local config and skips global user config under `/root/.config/opencode/`.

## Active Configuration Files

### 1. `opencode.json`
Location: `/mnt/amazon-launch/opencode.json`

Contains:
- `$schema` validation
- `provider` model definitions
- `agent` role/model mapping
- `permission` defaults
- `mcp` server configuration (including code search)

### 2. `.env`
Location: `/mnt/amazon-launch/.env`

Contains runtime environment variables, including `OPENCODE_DISABLE_GLOBAL_CONFIG=true`.

## Available Memory/Context Sources

- `megamemory`: persistent project knowledge graph
- `cocoindex-code`: semantic code search across the repository

## Starting OpenCode

### Method 1 (recommended)
```bash
cd /mnt/amazon-launch
source .env
opencode
```

### Method 2 (explicit env var)
```bash
cd /mnt/amazon-launch
OPENCODE_DISABLE_GLOBAL_CONFIG=true opencode
```

## Verification

Validate the active config file:

```bash
python3 -m json.tool /mnt/amazon-launch/opencode.json > /dev/null && echo "Valid"
```

## Security Notes

- Keep secrets in `.env`, not committed files
- Ensure `.env` remains gitignored
- Do not commit API keys

## References

- OpenCode docs: https://opencode.ai
- Config schema: https://opencode.ai/config.json
