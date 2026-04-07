import importlib.util
import pathlib
import sys
import types
import unittest
from typing import Optional
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ENGINE_PATH = REPO_ROOT / "searx/engines/community_scripts_proxmoxve.py"


def _pocketbase_response(
    items: list,
    page: int = 1,
    total_pages: int = 1,
    total_items: Optional[int] = None,
) -> dict:
    """Wrap items in a PocketBase-style paginated envelope."""
    return {
        "page": page,
        "perPage": 500,
        "totalItems": total_items if total_items is not None else len(items),
        "totalPages": total_pages,
        "items": items,
    }


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
    network_module.get = lambda url, timeout: FakeHTTPResponse(_pocketbase_response([]))

    # Add dummy httpx module
    class DummyHTTPError(Exception):
        pass
    class DummyTimeoutException(DummyHTTPError):
        pass

    httpx_module = types.ModuleType("httpx")
    httpx_module.HTTPError = DummyHTTPError
    httpx_module.TimeoutException = DummyTimeoutException

    module_overrides = {
        "searx": searx_module,
        "searx.enginelib": enginelib_module,
        "searx.result_types": result_types_module,
        "searx.network": network_module,
        "httpx": httpx_module,
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

    def _patch_network_get_pages(self, pages: list[object]) -> mock._patch:
        """Return different responses for successive get() calls (pagination)."""
        responses = [FakeHTTPResponse(p) for p in pages]
        return mock.patch.object(self.module, "get", side_effect=responses)


class CommunityScriptsNetworkTests(CommunityScriptsTestBase):

    def test_fetch_scripts_success(self) -> None:
        payload = _pocketbase_response([{"name": "Test Script", "slug": "test-script"}])
        with self._patch_network_get(payload):
            scripts = self.module._fetch_scripts()
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0]["name"], "Test Script")

    def test_fetch_scripts_http_error(self) -> None:
        with self._patch_network_get(_pocketbase_response([]), status_code=500):
            scripts = self.module._fetch_scripts()
        self.assertEqual(scripts, [])
        self.assertTrue(
            any("Unexpected PocketBase API status" in msg for _, msg in self.logger.messages)
        )

    def test_fetch_scripts_exception(self) -> None:
        with mock.patch.object(self.module, "get", side_effect=self.module.HTTPError("Network Error")):
            scripts = self.module._fetch_scripts()
        self.assertEqual(scripts, [])
        self.assertTrue(
            any("Failed to fetch community scripts" in msg for _, msg in self.logger.messages)
        )

    def test_fetch_scripts_pagination(self) -> None:
        page1 = _pocketbase_response(
            [{"name": "Script A", "slug": "script-a"}],
            page=1, total_pages=2, total_items=2,
        )
        page2 = _pocketbase_response(
            [{"name": "Script B", "slug": "script-b"}],
            page=2, total_pages=2, total_items=2,
        )
        with self._patch_network_get_pages([page1, page2]):
            scripts = self.module._fetch_scripts()
        self.assertEqual(len(scripts), 2)
        self.assertEqual(scripts[0]["name"], "Script A")
        self.assertEqual(scripts[1]["name"], "Script B")

    def test_fetch_scripts_pagination_failure_on_page_2(self) -> None:
        page1 = _pocketbase_response(
            [{"name": "Script A", "slug": "script-a"}],
            page=1, total_pages=2, total_items=2,
        )
        with mock.patch.object(
            self.module, "get",
            side_effect=[
                FakeHTTPResponse(page1),
                self.module.HTTPError("Page 2 failed"),
            ],
        ):
            scripts = self.module._fetch_scripts()
        self.assertEqual(scripts, [])

    def test_fetch_scripts_empty_items(self) -> None:
        payload = _pocketbase_response([])
        with self._patch_network_get(payload):
            scripts = self.module._fetch_scripts()
        self.assertEqual(scripts, [])


class CommunityScriptsSchemaHardeningTests(CommunityScriptsTestBase):

    def test_fetch_scripts_rejects_non_dict_payload(self) -> None:
        with self._patch_network_get([]):
            scripts = self.module._fetch_scripts()

        self.assertEqual(scripts, [])
        self.assertTrue(
            any(
                "Unexpected payload type" in message
                for _level, message in self.logger.messages
            )
        )

    def test_fetch_scripts_skips_malformed_items(self) -> None:
        payload = _pocketbase_response([
            None,
            {"name": "Valid Script", "slug": "valid-script"},
        ])
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
        self.assertTrue(any("Skipping malformed item" in msg for msg in warning_messages))

    def test_fetch_scripts_skips_malformed_items_in_response(self) -> None:
        payload = _pocketbase_response([
            1, "broken", {"name": "Valid Script", "slug": "valid-script"},
        ])
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
        self.assertTrue(any("Skipping malformed item" in msg for msg in warning_messages))

    def test_fetch_scripts_rejects_non_list_items(self) -> None:
        payload = {"page": 1, "perPage": 500, "totalItems": 0, "totalPages": 1, "items": "not-a-list"}
        with self._patch_network_get(payload):
            scripts = self.module._fetch_scripts()

        self.assertEqual(scripts, [])
        warning_messages = [message for _level, message in self.logger.messages]
        self.assertTrue(any("Unexpected items type" in msg for msg in warning_messages))

    def test_fetch_scripts_handles_items_with_invalid_or_missing_name_slug(self) -> None:
        payload = _pocketbase_response([
            {"name": None, "slug": "missing-name"},
            {"name": "Missing Slug"},
            {"name": 123, "slug": "numeric-name"},
            {"name": "Numeric Slug", "slug": 456},
            {"name": "", "slug": "empty-name"},
            {"name": "Empty Slug", "slug": ""},
            {"name": "Valid Script", "slug": "valid-script"},
        ])
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
                "Skipping item with invalid name/slug" in msg
                for msg in warning_messages
            )
        )

    def test_fetch_scripts_strips_whitespace_from_name_and_slug(self) -> None:
        payload = _pocketbase_response([
            {"name": "  Whitespace Name  ", "slug": "  whitespace-slug  "},
            {"name": "Dup", "slug": "dup"},
            {"name": "Dup Duplicate", "slug": "  dup  "},
        ])
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
                {
                    "name": "Dup Duplicate",
                    "slug": "dup-1",
                    "description": "",
                },
            ],
        )

    def test_init_and_search_continue_with_partial_bad_data(self) -> None:
        payload = _pocketbase_response([
            None,
            {"name": "Docker LXC", "slug": "docker-lxc", "description": "Docker setup"},
        ])
        with self._patch_network_get(payload):
            self.module.setup({"name": "proxmox ve community scripts"})
            initialized = self.module.init({})

        self.assertTrue(initialized)
        params = types.SimpleNamespace()
        results = self.module.search("docker", params)
        self.assertEqual(len(results.items), 1)
        self.assertEqual(results.items[0]["title"], "Docker LXC")
        self.assertTrue(
            any("Skipping malformed item" in message for _level, message in self.logger.messages)
        )


if __name__ == "__main__":
    unittest.main()
