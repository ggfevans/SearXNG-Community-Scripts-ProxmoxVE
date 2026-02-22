# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Unit tests mocking network errors and status codes.
- Comprehensive unit tests covering malformed payloads, trimming/deduplication, and partial-cache resilience.
- Manual data analysis script in `tools/analyze_api.py` for live API inspection.
- Support for `hmac_secret_key` in engine configuration and environment variables.

### Changed

- Aligned `disabled` default guidance across engine docstring, `README.md`, and `settings-snippet.yml` with clear distinction between upstream (opt-in) and personal deployment use cases.
- Switched networking from `urllib.request` to shared `searx.network.get` (httpx-style).
- Hardened payload validation (top-level list requirement, strict type checking for categories and scripts).
- Improved slug normalization using a new `_slugify` helper (NFKD normalization, lowercase, special character removal).
- Implemented slug collision handling using numeric suffixes (e.g., `slug-1`).
- Migrated from a single-blob cache to individual per-script cache entries.
- Switched cache serialization from `pickle` to compressed JSON (zlib level 6).
- Truncated script descriptions to 500 characters to optimize cache utilization within SearXNG's 10 KB cache limit.
- Enhanced logging with informative warnings for malformed upstream data and cache failures.
- Refactored `setup()` to use a secure "load-or-generate" flow for per-instance HMAC keys.

### Fixed

- Logic error where disabled scripts reserved slugs and caused unnecessary renaming of enabled scripts.

### Security

- Replaced `pickle` with `json` for cache serialization to eliminate potential deserialization vulnerabilities.
- Added HMAC-based integrity protection for all cached script data.
- Configured `.gitignore` to prevent the instance-local `.hmac_secret` from being committed.

## [1.0.1] - 2026-02-19

### Removed

- Unused `DEPLOY.md` file.
- `.DS_Store` files from the repository.

### Changed

- Updated `README.md` with improved documentation.

## [1.0.0] - 2026-02-19

### Added

- Initial release of the Proxmox VE Community Scripts engine for SearXNG.
- Offline search support against the community-scripts catalogue.
- Configuration snippet (`settings-snippet.yml`) for easy integration.
- Project documentation and screenshots.
- AGPL-3.0 License.
