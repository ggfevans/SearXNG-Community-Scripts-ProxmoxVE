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

import pickle

import typing as t

import zlib



from httpx import HTTPError, TimeoutException



from searx import logger

from searx.enginelib import EngineCache

from searx.network import get

from searx.result_types import EngineResults

if t.TYPE_CHECKING:
    from searx.search.processors import RequestParams

engine_type = "offline"  # query never leaves the instance; we fetch bulk data and search locally
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

CACHE: EngineCache
"""Persistent (SQLite) key/value cache that stores the fetched script catalogue."""


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

    for category_index, category in enumerate(data):
        if not isinstance(category, dict):
            _logger.warning("Skipping malformed category at index %d", category_index)
            continue

        category_scripts = category.get("scripts", [])
        if not isinstance(category_scripts, list):
            _logger.warning("Skipping malformed scripts list in category index %d", category_index)
            continue

        for script_index, script in enumerate(category_scripts):
            if not isinstance(script, dict):
                _logger.warning(
                    "Skipping malformed script at category %d index %d",
                    category_index,
                    script_index,
                )
                continue

            name = script.get("name")
            slug = script.get("slug")
            if not isinstance(name, str) or not isinstance(slug, str):
                _logger.warning(
                    "Skipping script with invalid name/slug at category %d index %d: name=%r slug=%r",
                    category_index,
                    script_index,
                    name,
                    slug,
                )
                continue

            name = name.strip()
            slug = slug.strip()
            if not name or not slug:
                continue
            if script.get("disable") is True:
                continue
            if slug in seen:
                continue

            description = script.get("description")
            # Truncate description to 500 characters
            description = description[:500] if isinstance(description, str) else ""

            seen.add(slug)
            scripts.append(
                {
                    "name": name,
                    "slug": slug,
                    "description": description,
                }
            )

    return scripts


def setup(engine_settings: dict[str, t.Any]) -> bool:
    """Set up the engine: create the persistent cache.

    For more details see :py:obj:`searx.enginelib.Engine.setup`.
    """
    global CACHE  # pylint: disable=global-statement
    CACHE = EngineCache(engine_settings["name"])
    return True


def init(engine_settings: dict[str, t.Any]) -> bool:  # pylint: disable=unused-argument
    """Pre-warm the cache by fetching the full script catalogue.

    For more details see :py:obj:`searx.enginelib.Engine.init`.
    """
    scripts = _fetch_scripts()
    if not scripts:
        _logger.warning("No scripts fetched during init")
        return True

    try:
        slugs = []
        for script in scripts:
            slug = script.get("slug")
            if not slug:
                _logger.warning("Skipping script with no slug in init: %s", script.get('name', 'unknown'))
                continue
            
            slugs.append(slug)
            serialized_script = pickle.dumps(script)
            compressed_script = zlib.compress(serialized_script, level=zlib.Z_BEST_COMPRESSION)

            if len(compressed_script) > _MAX_CACHE_VALUE_LEN:
                _logger.warning("Individual script is too large for cache, even when compressed: %s (size: %d bytes)", slug, len(compressed_script))
                continue

            CACHE.set(f"script_{slug}", compressed_script, expire=_CACHE_TTL)

        # Store the list of all slugs
        CACHE.set("script_slugs_list", slugs, expire=_CACHE_TTL)
        _logger.debug("Cached %d scripts individually.", len(slugs))

    except (pickle.PickleError, zlib.error) as e:
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
    # Retrieve the list of all script slugs
    slugs_list = CACHE.get("script_slugs_list")
    
    if isinstance(slugs_list, list) and slugs_list:
        _logger.debug("Attempting to retrieve %d scripts from individual cache entries.", len(slugs_list))
        temp_scripts = []
        retrieval_successful = True
        for slug in slugs_list:
            cached_script = CACHE.get(f"script_{slug}")
            if cached_script:
                try:
                    decompressed_script = zlib.decompress(cached_script)
                    script = pickle.loads(decompressed_script)
                    temp_scripts.append(script)
                except (zlib.error, pickle.UnpicklingError) as e:
                    _logger.warning("Failed to decompress or unpickle script with slug %s: %s", slug, e)
                    retrieval_successful = False
                    break
            else:
                _logger.warning("Missing script with slug %s from cache.", slug)
                retrieval_successful = False
                break
        
        if retrieval_successful:
            scripts = temp_scripts
            _logger.debug("Successfully retrieved %d scripts from individual cache entries.", len(scripts))
        else:
            _logger.warning("Failed to retrieve all scripts. Re-fetching fresh data.")
            scripts = [] # Clear any partial retrieval

    if not scripts: # If scripts still empty after all cache attempts
        scripts = _fetch_scripts()
        if scripts:
            # Re-attempt individual script caching from search
            try:
                new_slugs = []
                for script in scripts:
                    slug = script.get("slug")
                    if not slug:
                        _logger.warning("Skipping script with no slug in search cache: %s", script.get('name', 'unknown'))
                        continue
                    
                    new_slugs.append(slug)
                    serialized_script = pickle.dumps(script)
                    compressed_script = zlib.compress(serialized_script, level=zlib.Z_BEST_COMPRESSION)

                    if len(compressed_script) > _MAX_CACHE_VALUE_LEN:
                        _logger.warning("Individual script is too large for cache from search, even when compressed: %s (size: %d bytes)", slug, len(compressed_script))
                        continue

                    CACHE.set(f"script_{slug}", compressed_script, expire=_CACHE_TTL)
                
                CACHE.set("script_slugs_list", new_slugs, expire=_CACHE_TTL)
                _logger.debug("Cached %d scripts individually from search.", len(new_slugs))

            except (pickle.PickleError, zlib.error) as e:
                _logger.warning("Failed to serialize, compress and cache scripts from search: %s", e)
    
    if not scripts: # Final check if scripts is empty
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
