# SearXNG Engine: Proxmox VE Community Scripts
  
A [SearXNG](https://github.com/searxng/searxng) engine that searches the [Proxmox VE Community Scripts](https://community-scripts.github.io/ProxmoxVE/) catalogue of ~480 install scripts for LXC containers, VMs, and add-ons.

![SearXNG search results for "media" using the !pve bang shortcut, showing matches across script names and descriptions](static/screenshot.png)

## How it works

The engine fetches the full script catalogue from the site's static JSON API, caches it locally for 12 hours, and searches against it offline. Your query never leaves your SearXNG instance.

- **Engine type:** `offline` — no per-query network requests
- **Data source:** `community-scripts.github.io/ProxmoxVE/api/categories` (static JSON, ~480 scripts)
- **Scoring:** +10 for name match, +5 for description match per query word; AND logic
- **Cache:** SQLite-backed via SearXNG's `EngineCache`, 12h TTL
- **Dependencies:** None beyond SearXNG itself

## Installation

### 1. Copy the engine file

Find your SearXNG engines directory:

```bash
# If installed via community scripts LXC:
ls /usr/local/searxng/searxng-src/searx/engines/

# Or find it dynamically:
python3 -c "import searx; print(searx.__path__[0] + '/engines')"
```

Copy the engine file:

```bash
cp searx/engines/community_scripts_proxmoxve.py /path/to/searx/engines/
```

### 2. Add to settings.yml

Add this block under `engines:` in your SearXNG settings file (typically `/etc/searxng/settings.yml`):

```yaml
  - name: proxmox ve community scripts
    engine: community_scripts_proxmoxve
    shortcut: pve
    categories: [it]
    disabled: false
```

### 3. Restart SearXNG

```bash
systemctl restart searxng
```

## Usage

- Search normally under the **IT** tab — results from the community scripts catalogue will appear alongside other engines
- Use the `!pve` bang shortcut to search the catalogue exclusively (e.g., `!pve docker`)

## Verification

After installation, try these searches:

| Query | Expected results |
|-------|-----------------|
| `!pve docker` | Docker LXC, Dockge, etc. |
| `!pve reverse proxy` | Nginx Proxy Manager, Traefik, Caddy |
| `!pve adguard` | AdGuard Home LXC |
| `!pve xyznonexistent` | Empty results |

## Licence

[![AGPL-3.0-or-later](static/agplv3-155x51.png)](LICENSE)

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE), the same licence as SearXNG.
