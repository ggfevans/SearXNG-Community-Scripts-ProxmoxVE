# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proxmox VE Community Scripts
===============================

This engine searches the community-maintained catalogue of installation scripts
for Proxmox VE containers, virtual machines, and add-ons hosted at
`community-scripts.github.io/ProxmoxVE <https://community-scripts.github.io/ProxmoxVE/>`_.

The catalogue (~480 scripts) is fetched once from the static JSON API and cached
locally for 12 hours.  Searches run entirely offline against the cached data —
the user's query never leaves the SearXNG instance.

Configuration
=============

.. code:: yaml

   - name: proxmox ve community scripts
     engine: community_scripts_proxmoxve
     shortcut: pve
     categories: [it]
     disabled: false

Implementations
===============

"""

import hashlib
import hmac
import json
import os
import pathlib
import re
import secrets
import typing as t
import unicodedata
import zlib

from httpx import HTTPError, TimeoutException

from searx import logger
from searx.enginelib import EngineCache
from searx.network import get
from searx.result_types import EngineResults

if t.TYPE_CHECKING:
    from searx.search.processors import RequestParams

engine_type = "offline"
categories = ["it"]
disabled = True
paging = False
time_range_support = False

about = {
    "website": "https://community-scripts.github.io/ProxmoxVE/",
    "wikidata_id": None,
    "official_api_documentation": None,
    "use_official_api": False,
    "require_api_key": False,
    "results": "JSON",
}

_SCRIPT_URL = "https://community-scripts.github.io/ProxmoxVE/scripts?id={slug}"
_CACHE_TTL = 43200  # 12 hours in seconds
_MAX_RESULTS = 20
_MAX_CACHE_VALUE_LEN = 10240  # 10 KB

_logger = logger.getChild("community_scripts_proxmoxve")

_HMAC_SECRET_KEY: t.Optional[bytes] = None
CACHE: EngineCache
"""Persistent (SQLite) key/value cache that stores the fetched script catalogue."""


def _slugify(value: str, max_len: int = 64) -> str:
    """Normalizes a string to a slug."""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value[:max_len]


def _fetch_scripts() -> list[dict[str, t.Any]]:
    """Fetch all scripts from the community-scripts API and return a flat, deduplicated list."""
    try:
        resp = get("https://community-scripts.github.io/ProxmoxVE/api/categories", timeout=30)
        if resp.status_code != 200:
            _logger.warning("Unexpected community scripts API status: %s", resp.status_code)
            return []
        data = resp.json()
    except (ValueError, HTTPError, TimeoutException) as e:
        _logger.warning("Failed to fetch community scripts: %s", e)
        return []

    if not isinstance(data, list):
        _logger.warning("Unexpected categories payload type: %s", type(data).__name__)
        return []

    seen: set[str] = set()
    scripts: list[dict[str, t.Any]] = []
    for category in data:
        if not isinstance(category, dict):
            _logger.warning("Skipping malformed category")
            continue
        category_scripts = category.get("scripts", [])
        if not isinstance(category_scripts, list):
            _logger.warning("Skipping malformed scripts list in category")
            continue
        for script in category_scripts:
            if not isinstance(script, dict):
                _logger.warning("Skipping malformed script")
                continue
            name = script.get("name")
            slug = script.get("slug")
            if not isinstance(name, str) or not isinstance(slug, str):
                _logger.warning(
                    "Skipping script with invalid name/slug: name=%r slug=%r",
                    name,
                    slug,
                )
                continue

            slug = _slugify(slug)
            if not name or not slug:
                continue

            if script.get("disable") is True:
                continue

            # Handle collisions only for enabled scripts
            original_slug = slug
            counter = 1
            while slug in seen:
                slug = f"{original_slug}-{counter}"
                counter += 1

            seen.add(slug)

            description = script.get("description")
            # Truncate description to 500 characters
            description = description[:500] if isinstance(description, str) else ""

            scripts.append(
                {"name": name.strip(), "slug": slug, "description": description}
            )
    return scripts


def setup(engine_settings: dict[str, t.Any]) -> bool:
    """Set up the engine: create the persistent cache and load HMAC key.

    For more details see :py:obj:`searx.enginelib.Engine.setup`.
    """
    global CACHE, _HMAC_SECRET_KEY
    CACHE = EngineCache(engine_settings["name"])

    # 1. From engine_settings
    key = engine_settings.get("hmac_secret_key")
    if key:
        _HMAC_SECRET_KEY = key if isinstance(key, bytes) else key.encode("utf-8")
        return True

    # 2. From environment variable
    key_from_env = os.getenv("PROXMOXVE_CACHE_HMAC_KEY")
    if key_from_env:
        _HMAC_SECRET_KEY = key_from_env.encode('utf-8')
        return True

    # 3. From a local file (persistent across restarts)
    # The .hmac_secret file is ignored by git to ensure it stays instance-local.
    key_file = pathlib.Path(__file__).parent / ".hmac_secret"
    if key_file.exists():
        _HMAC_SECRET_KEY = key_file.read_bytes()
        return True

    # 4. Generate and store a new key
    _logger.info("Generating new HMAC secret for Proxmox VE engine cache.")
    new_key = secrets.token_bytes(32)
    try:
        key_file.write_bytes(new_key)
    except IOError as e:
        _logger.error("Failed to write HMAC secret file: %s", e)
        # Fallback to a temporary key for this run, but it won't be persistent
        _HMAC_SECRET_KEY = new_key
        return True

    _HMAC_SECRET_KEY = new_key
    return True


def _serialize_script(script: dict[str, t.Any]) -> bytes:
    """Serializes, compresses and signs a script dictionary using JSON."""
    payload = json.dumps(script, ensure_ascii=False).encode("utf-8")
    compressed = zlib.compress(payload, level=6)  # Use balanced compression

    if _HMAC_SECRET_KEY:
        mac = hmac.new(_HMAC_SECRET_KEY, compressed, hashlib.sha256).digest()
        return mac + compressed
    return compressed


def _deserialize_script(data: bytes) -> dict[str, t.Any]:
    """Verifies, decompresses and deserializes a script using JSON."""
    if _HMAC_SECRET_KEY:
        mac_size = hashlib.sha256().digest_size
        mac, compressed = data[:mac_size], data[mac_size:]

        expected_mac = hmac.new(_HMAC_SECRET_KEY, compressed, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected_mac):
            raise ValueError("HMAC verification failed")
    else:
        compressed = data

    payload = zlib.decompress(compressed)
    return json.loads(payload.decode("utf-8"))


def _cache_scripts(scripts: list[dict[str, t.Any]]) -> None:
    """Serializes, compresses and caches each script individually."""
    slugs = []
    for script in scripts:
        slug = script.get("slug")
        if not slug:
            _logger.warning("Skipping script with no slug: %s", script.get('name', 'unknown'))
            continue

        signed_script = _serialize_script(script)

        if len(signed_script) > _MAX_CACHE_VALUE_LEN:
            _logger.warning("Individual script is too large for cache, even when signed and compressed: %s (size: %d bytes)", slug, len(signed_script))
            continue

        CACHE.set(f"script_{slug}", signed_script, expire=_CACHE_TTL)
        slugs.append(slug)

    CACHE.set("script_slugs_list", slugs, expire=_CACHE_TTL)
    _logger.debug("Cached %d scripts individually.", len(slugs))


def init(engine_settings: dict[str, t.Any]) -> bool:  # pylint: disable=unused-argument
    """Pre-warm the cache by fetching the full script catalogue.

    For more details see :py:obj:`searx.enginelib.Engine.init`.
    """
    scripts = _fetch_scripts()
    if not scripts:
        _logger.warning("No scripts fetched during init")
        return True

    try:
        _cache_scripts(scripts)
    except (json.JSONDecodeError, zlib.error) as e:
        _logger.warning("Failed to serialize, compress and cache scripts: %s", e)
        return False
    return True


def _score_script(script: dict[str, t.Any], words: list[str]) -> int:
    """Score a script against query words.  Returns 0 if any word is missing (AND logic)."""
    score = 0
    name_lower = script["name"].lower()
    desc_lower = script["description"].lower()

    for word in words:
        found = False
        if word in name_lower:
            score += 10
            found = True
        if word in desc_lower:
            score += 5
            found = True
        if not found:
            return 0
    return score


def search(query: str, params: "RequestParams") -> EngineResults:  # pylint: disable=unused-argument
    """Search the cached script catalogue and return scored results.

    Each query word is matched against script names (+10) and descriptions (+5).
    All words must match (AND logic).  Results are sorted by score and capped
    at :py:obj:`_MAX_RESULTS`.
    """
    res = EngineResults()

    if not query or not query.strip():
        return res

    scripts = []
    slugs_list = CACHE.get("script_slugs_list")

    if isinstance(slugs_list, list) and slugs_list:
        _logger.debug("Attempting to retrieve %d scripts from individual cache entries.", len(slugs_list))
        temp_scripts = []
        missed_count = 0
        for slug in slugs_list:
            cached_script = CACHE.get(f"script_{slug}")
            if cached_script:
                try:
                    script = _deserialize_script(cached_script)
                    temp_scripts.append(script)
                except (ValueError, zlib.error, json.JSONDecodeError) as e:
                    _logger.warning("Failed to deserialize script with slug %s: %s", slug, e)
                    missed_count += 1
            else:
                _logger.warning("Missing script with slug %s from cache.", slug)
                missed_count += 1

        if temp_scripts:
            scripts = temp_scripts
            _logger.debug("Successfully retrieved %d of %d scripts from cache.", len(scripts), len(slugs_list))
            if missed_count > 0:
                _logger.warning("Missed %d scripts from cache.", missed_count)
        else:
            _logger.warning("Failed to retrieve any scripts from cache. Re-fetching fresh data.")
            scripts = []

    if not scripts:
        scripts = _fetch_scripts()
        if scripts:
            try:
                _cache_scripts(scripts)
            except (json.JSONDecodeError, zlib.error) as e:
                _logger.warning("Failed to serialize, compress and cache scripts from search: %s", e)

    if not scripts:
        return res

    words = query.lower().split()
    scored = [(s, script) for script in scripts if (s := _score_script(script, words)) > 0]
    scored.sort(key=lambda x: x[0], reverse=True)

    for _score, script in scored[:_MAX_RESULTS]:
        content = script["description"]
        if len(content) > 300:
            content = content[:300].rsplit(" ", 1)[0] + "..."
        res.add(
            res.types.MainResult(
                url=_SCRIPT_URL.format(slug=script["slug"]),
                title=script["name"],
                content=content,
            )
        )
    return res
