import importlib.util
import pathlib
import sys
import types
import unittest
from typing import Optional
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ENGINE_PATH = REPO_ROOT / "searx/engines/community_scripts_proxmoxve.py"


class DummyLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def warning(self, message: str, *args: object) -> None:
        if args:
            message = message % args
        self.messages.append(("warning", message))

    def info(self, message: str, *args: object) -> None:
        if args:
            message = message % args
        self.messages.append(("info", message))

    def error(self, message: str, *args: object) -> None:
        if args:
            message = message % args
        self.messages.append(("error", message))

    def debug(self, message: str, *args: object) -> None:
        if args:
            message = message % args
        self.messages.append(("debug", message))


class DummyEngineCache:
    def __init__(self, name: str) -> None:
        self.name = name
        self.values: dict[str, object] = {}

    def set(self, key: str, value: object, expire: Optional[int] = None) -> None:
        _ = expire
        self.values[key] = value

    def get(self, key: str) -> Optional[object]:
        return self.values.get(key)


class DummyEngineResults:
    class Types:
        @staticmethod
        def MainResult(url: str, title: str, content: str) -> dict[str, str]:
            return {"url": url, "title": title, "content": content}

    def __init__(self) -> None:
        self.types = self.Types()
        self.items: list[dict[str, str]] = []

    def add(self, item: dict[str, str]) -> None:
        self.items.append(item)


class FakeHTTPResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> object:
        return self.payload


def load_engine_module() -> tuple[types.ModuleType, DummyLogger]:
    module_name = "community_scripts_proxmoxve_test_module"
    logger = DummyLogger()

    searx_module = types.ModuleType("searx")
    searx_module.logger = types.SimpleNamespace(getChild=lambda _name: logger)

    enginelib_module = types.ModuleType("searx.enginelib")
    enginelib_module.EngineCache = DummyEngineCache

    result_types_module = types.ModuleType("searx.result_types")
    result_types_module.EngineResults = DummyEngineResults

    network_module = types.ModuleType("searx.network")
    network_module.get = lambda url, timeout: FakeHTTPResponse([])

    # Add dummy httpx module
    httpx_module = types.ModuleType("httpx")
    httpx_module.HTTPError = Exception # Dummy class for HTTPError
    httpx_module.TimeoutException = Exception # Dummy class for TimeoutException

    module_overrides = {
        "searx": searx_module,
        "searx.enginelib": enginelib_module,
        "searx.result_types": result_types_module,
        "searx.network": network_module,
        "httpx": httpx_module, # Add httpx here
    }
    original_modules = {key: sys.modules.get(key) for key in module_overrides}
    original_test_module = sys.modules.get(module_name)

    try:
        sys.modules.update(module_overrides)
        sys.modules.pop(module_name, None)

        spec = importlib.util.spec_from_file_location(module_name, ENGINE_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError("Unable to load engine module")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if original_test_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = original_test_module
        for key, original_module in original_modules.items():
            if original_module is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = original_module

    return module, logger


class CommunityScriptsTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.module, self.logger = load_engine_module()

    def _patch_network_get(self, payload: object, status_code: int = 200) -> mock._patch:
        return mock.patch.object(
            self.module,
            "get",
            return_value=FakeHTTPResponse(payload, status_code=status_code),
        )


class CommunityScriptsNetworkTests(CommunityScriptsTestBase):

    def test_fetch_scripts_success(self) -> None:
        payload = [{"scripts": [{"name": "Test Script", "slug": "test-script"}]}]
        with self._patch_network_get(payload):
            scripts = self.module._fetch_scripts()
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0]["name"], "Test Script")

    def test_fetch_scripts_http_error(self) -> None:
        with self._patch_network_get([], status_code=500):
            scripts = self.module._fetch_scripts()
        self.assertEqual(scripts, [])
        self.assertTrue(
            any("Unexpected community scripts API status" in msg for _, msg in self.logger.messages)
        )

    def test_fetch_scripts_exception(self) -> None:
        with mock.patch.object(self.module, "get", side_effect=Exception("Network Error")):
            scripts = self.module._fetch_scripts()
        self.assertEqual(scripts, [])
        self.assertTrue(
            any("Failed to fetch community scripts" in msg for _, msg in self.logger.messages)
        )


class CommunityScriptsSchemaHardeningTests(CommunityScriptsTestBase):

    def test_fetch_scripts_rejects_non_list_payload(self) -> None:
        with self._patch_network_get({"scripts": []}):
            scripts = self.module._fetch_scripts()

        self.assertEqual(scripts, [])
        self.assertTrue(
            any(
                "Unexpected categories payload type" in message
                for _level, message in self.logger.messages
            )
        )

    def test_fetch_scripts_skips_malformed_category_entries(self) -> None:
        payload = [
            None,
            {"scripts": [{"name": "Valid Script", "slug": "valid-script"}]},
        ]
        with self._patch_network_get(payload):
            scripts = self.module._fetch_scripts()

        self.assertEqual(
            scripts,
            [
                {
                    "name": "Valid Script",
                    "slug": "valid-script",
                    "description": "",
                }
            ],
        )
        warning_messages = [message for _level, message in self.logger.messages]
        self.assertTrue(any("Skipping malformed category" in msg for msg in warning_messages))

    def test_fetch_scripts_skips_malformed_script_entries_within_category(self) -> None:
        payload = [
            {"scripts": [1, "broken", {"name": "Valid Script", "slug": "valid-script"}]},
        ]
        with self._patch_network_get(payload):
            scripts = self.module._fetch_scripts()

        self.assertEqual(
            scripts,
            [
                {
                    "name": "Valid Script",
                    "slug": "valid-script",
                    "description": "",
                }
            ],
        )
        warning_messages = [message for _level, message in self.logger.messages]
        self.assertTrue(any("Skipping malformed script" in msg for msg in warning_messages))

    def test_fetch_scripts_skips_malformed_scripts_list_in_category(self) -> None:
        payload = [
            {"scripts": "not-a-list"},
        ]
        with self._patch_network_get(payload):
            scripts = self.module._fetch_scripts()

        self.assertEqual(scripts, [])
        warning_messages = [message for _level, message in self.logger.messages]
        self.assertTrue(any("Skipping malformed scripts list" in msg for msg in warning_messages))

    def test_fetch_scripts_handles_scripts_with_invalid_or_missing_name_slug(self) -> None:
        payload = [
            {
                "scripts": [
                    {"name": None, "slug": "missing-name"},
                    {"name": "Missing Slug"},
                    {"name": 123, "slug": "numeric-name"},
                    {"name": "Numeric Slug", "slug": 456},
                    {"name": "", "slug": "empty-name"},
                    {"name": "Empty Slug", "slug": ""},
                    {"name": "Valid Script", "slug": "valid-script"},
                ]
            }
        ]
        with self._patch_network_get(payload):
            scripts = self.module._fetch_scripts()

        self.assertEqual(
            scripts,
            [
                {
                    "name": "Valid Script",
                    "slug": "valid-script",
                    "description": "",
                }
            ],
        )
        warning_messages = [message for _level, message in self.logger.messages]
        self.assertTrue(
            any(
                "Skipping script with invalid name/slug" in msg
                for msg in warning_messages
            )
        )

    def test_fetch_scripts_strips_whitespace_from_name_and_slug(self) -> None:
        payload = [
            {
                "scripts": [
                    {"name": "  Whitespace Name  ", "slug": "  whitespace-slug  "},
                    {"name": "Dup", "slug": "dup"},
                    {"name": "Dup Duplicate", "slug": "  dup  "},
                ]
            }
        ]
        with self._patch_network_get(payload):
            scripts = self.module._fetch_scripts()

        self.assertEqual(
            scripts,
            [
                {
                    "name": "Whitespace Name",
                    "slug": "whitespace-slug",
                    "description": "",
                },
                {
                    "name": "Dup",
                    "slug": "dup",
                    "description": "",
                },
            ],
        )

    def test_init_and_search_continue_with_partial_bad_data(self) -> None:
        payload = [
            {
                "scripts": [
                    None,
                    {"name": "Docker LXC", "slug": "docker-lxc", "description": "Docker setup"},
                ]
            }
        ]
        with self._patch_network_get(payload):
            self.module.setup({"name": "proxmox ve community scripts"})
            initialized = self.module.init({})

        self.assertTrue(initialized)
        params = types.SimpleNamespace()
        results = self.module.search("docker", params)
        self.assertEqual(len(results.items), 1)
        self.assertEqual(results.items[0]["title"], "Docker LXC")
        self.assertTrue(
            any("Skipping malformed script" in message for _level, message in self.logger.messages)
        )


if __name__ == "__main__":
    unittest.main()
