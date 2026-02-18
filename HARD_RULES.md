# Amazon Launchpad - Hard Architectural Rules

## Port Allocation (CRITICAL)

### Reserved Ports (NEVER TOUCH)
- **Port 8501**: RESERVED for Amazon BI Dashboard
  - Status: PRODUCTION
  - Owner: Amazon BI Team
  - NEVER serve Launchpad on this port
  
- **Port 8502**: RESERVED for amazon-mi (Market Intelligence)
  - Status: PRODUCTION  
  - Owner: Market Intelligence Team
  - NEVER serve Launchpad on this port

### Launchpad Port
- **Port 8503**: DEDICATED to Amazon Launchpad
  - Status: DEDICATED
  - Owner: Launchpad Application
  - ONLY port for Launchpad Streamlit server

## Enforcement

These rules are enforced by:
1. `.streamlit/config.toml` - Explicit port 8503
2. This document - Hard rule declaration
3. Cloudflare tunnel configuration - Routes to localhost:8503
4. Systemd service files - Bind to port 8503 only

## Violation Consequences

Using ports 8501 or 8502 for Launchpad will:
- Conflict with existing production services
- Cause port binding errors
- Potentially disrupt BI and MI dashboards
- Violate architectural isolation principles

## Verification Command

Before starting Launchpad, verify port availability:
```bash
# Check if 8503 is free
lsof -i :8503

# Check that 8501 and 8502 are occupied (by other services)
lsof -i :8501
lsof -i :8502
```

## Hard Rule Summary

> **PORT 8501 and 8502 are FORBIDDEN for Launchpad.**
> 
> **ONLY use Port 8503.**

This rule is non-negotiable and has no exceptions.
