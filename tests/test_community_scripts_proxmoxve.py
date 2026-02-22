import importlib.util
import json
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
    def __init__(self, payload: object, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

class FakeHTTPSConnection:
    def __init__(self, payload: object, status: int = 200) -> None:
        self._response = FakeHTTPResponse(payload, status=status)
        self.request_calls: list[tuple[str, str]] = []

    def request(self, method: str, path: str) -> None:
        self.request_calls.append((method, path))

    def getresponse(self) -> FakeHTTPResponse:
        return self._response

    def __enter__(self) -> "FakeHTTPSConnection":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        _ = (exc_type, exc, traceback)
        return False


def load_engine_module() -> tuple[types.ModuleType, DummyLogger]:
    module_name = "community_scripts_proxmoxve_test_module"
    logger = DummyLogger()

    searx_module = types.ModuleType("searx")
    searx_module.logger = types.SimpleNamespace(getChild=lambda _name: logger)

    enginelib_module = types.ModuleType("searx.enginelib")
    enginelib_module.EngineCache = DummyEngineCache

    result_types_module = types.ModuleType("searx.result_types")
    result_types_module.EngineResults = DummyEngineResults

    module_overrides = {
        "searx": searx_module,
        "searx.enginelib": enginelib_module,
        "searx.result_types": result_types_module,
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


class CommunityScriptsSchemaHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module, self.logger = load_engine_module()

    def _patch_https_connection(self, payload: object, status: int = 200) -> mock._patch:
        return mock.patch.object(
            self.module.http.client,
            "HTTPSConnection",
            return_value=FakeHTTPSConnection(payload, status=status),
        )

    def test_fetch_scripts_rejects_non_list_payload(self) -> None:
        with self._patch_https_connection({"scripts": []}):
            scripts = self.module._fetch_scripts()

        self.assertEqual(scripts, [])
        self.assertTrue(
            any(
                "Unexpected categories payload type" in message
                for _level, message in self.logger.messages
            )
        )

    def test_fetch_scripts_skips_malformed_categories_and_scripts(self) -> None:
        payload = [
            None,
            {"scripts": [1, "broken", {"name": "Valid Script", "slug": "valid-script"}]},
            {"scripts": "not-a-list"},
        ]
        with self._patch_https_connection(payload):
            scripts = self.module._fetch_scripts()

        self.assertEqual(
            scripts,
            [
                {
                    "name": "Valid Script",
                    "slug": "valid-script",
                    "description": "",
                    "type": "",
                }
            ],
        )
        warning_messages = [message for _level, message in self.logger.messages]
        self.assertTrue(any("Skipping malformed category" in msg for msg in warning_messages))
        self.assertTrue(any("Skipping malformed script" in msg for msg in warning_messages))
        self.assertTrue(any("Skipping malformed scripts list" in msg for msg in warning_messages))

    def test_init_and_search_continue_with_partial_bad_data(self) -> None:
        payload = [
            {
                "scripts": [
                    None,
                    {"name": "Docker LXC", "slug": "docker-lxc", "description": "Docker setup"},
                ]
            }
        ]
        with self._patch_https_connection(payload):
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
