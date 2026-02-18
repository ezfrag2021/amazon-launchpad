# OpenCode Project Configuration

This document explains how this project is configured for complete isolation from global OpenCode settings.

## ⚠️ CRITICAL: Configuration Isolation

This project uses **strict configuration isolation** to ensure it only reads local project configs and ignores the global `/root/.config/opencode/` settings.

### How Isolation Works

The isolation is enforced through the environment variable in `.env`:

```bash
OPENCODE_DISABLE_GLOBAL_CONFIG=true
```

When this variable is set, OpenCode will:
- ✅ Load ONLY `/mnt/amazon-launch/opencode.json`
- ✅ Load ONLY `/mnt/amazon-launch/oh-my-opencode-slim.json`
- ❌ Ignore `/root/.config/opencode/opencode.json`
- ❌ Ignore `/root/.config/opencode/oh-my-opencode-slim.json`
- ❌ Ignore all other global config files

## Configuration Files

### 1. `opencode.json` (Main Config)
Location: `/mnt/amazon-launch/opencode.json`

Contains:
- **$schema**: JSON schema validation
- **plugin**: oh-my-opencode plugin for orchestration
- **permission**: Tool permissions (bash, edit, read, list, glob)
- **provider**: Model provider configurations
- **mcp**: MegaMemory MCP server configuration

### 2. `oh-my-opencode-slim.json` (Plugin Config)
Location: `/mnt/amazon-launch/oh-my-opencode-slim.json`

Contains:
- **preset**: "hybrid-lab" - orchestration preset
- **tmux**: Terminal multiplexer settings
- **agents**: Specialized agent configurations
- **presets**: Model assignments for each agent

### 3. `.env` (Environment Variables)
Location: `/mnt/amazon-launch/.env`

Contains:
- Database connection strings (DSNs)
- API keys (Jungle Scout, Google)
- Launchpad-specific settings
- **CRITICAL**: `OPENCODE_DISABLE_GLOBAL_CONFIG=true`

## Agent/Model Mapping

This project uses the following agent/model combinations:

| Agent/Role | Model | Provider |
|------------|-------|----------|
| **Orchestrator** (Atlas) | `opencode/kimi-k2.5-free` | opencode (local) |
| **oracle** | `anthropic/claude-sonnet-4-5` | anthropic (API) |
| **librarian** | `github-copilot/gemini-3-flash-preview` | github-copilot (API) |
| **hephaestus** | `openai/gpt-5.2-codex` | openai (API) |

### Category-Based Agents (Sisyphus-Junior)

These use preset-based configurations:
- `visual-engineering`
- `ultrabrain`
- `deep`
- `artistry`
- `quick`
- `unspecified-low`
- `unspecified-high`
- `writing`

## Starting OpenCode with Isolation

### Method 1: From Project Directory (Recommended)
```bash
cd /mnt/amazon-launch
source .env  # Load environment variables including OPENCODE_DISABLE_GLOBAL_CONFIG
opencode
```

### Method 2: With Environment Variable Explicitly
```bash
cd /mnt/amazon-launch
OPENCODE_DISABLE_GLOBAL_CONFIG=true opencode
```

### Method 3: Using direnv (if installed)
If you have `direnv` installed, create a `.envrc` file:
```bash
export OPENCODE_DISABLE_GLOBAL_CONFIG=true
```
Then run:
```bash
direnv allow
opencode
```

## Verifying Isolation

To confirm isolation is working, you can check which config files are being loaded:

```bash
# In an OpenCode session, the orchestrator should report:
# - Current config source: /mnt/amazon-launch/opencode.json
# - Plugin config source: /mnt/amazon-launch/oh-my-opencode-slim.json
# - Global config: NOT loaded
```

## Troubleshooting

### Problem: Global config is still being loaded
**Solution**: Ensure `OPENCODE_DISABLE_GLOBAL_CONFIG=true` is set before starting OpenCode:
```bash
echo $OPENCODE_DISABLE_GLOBAL_CONFIG  # Should print: true
```

### Problem: Config file not found errors
**Solution**: Verify files exist in project root:
```bash
ls -la /mnt/amazon-launch/*.json
# Should show: opencode.json, oh-my-opencode-slim.json
```

### Problem: JSON syntax errors
**Solution**: Validate JSON syntax:
```bash
python3 -m json.tool /mnt/amazon-launch/opencode.json > /dev/null && echo "Valid"
python3 -m json.tool /mnt/amazon-launch/oh-my-opencode-slim.json > /dev/null && echo "Valid"
```

## Modifying Configuration

### To Change Models
Edit `/mnt/amazon-launch/oh-my-opencode-slim.json`:
```json
{
  "presets": {
    "hybrid-lab": {
      "orchestrator": {
        "model": "opencode/YOUR-MODEL-HERE",
        "skills": ["*"]
      }
    }
  }
}
```

### To Add New Providers
Edit `/mnt/amazon-launch/opencode.json`:
```json
{
  "provider": {
    "new-provider": {
      "type": "api",
      "models": ["model-name"]
    }
  }
}
```

## Security Notes

- ✅ Project configs are isolated from global settings
- ✅ API keys are stored in `.env` (not committed to git)
- ✅ `.env` is in `.gitignore` (ensure this is set)
- ⚠️ Never commit API keys to version control

## References

- OpenCode Documentation: https://opencode.ai
- Configuration Schema: https://opencode.ai/config.json
- GitHub Issue (Config Isolation): https://github.com/anomalyco/opencode/issues/10021
