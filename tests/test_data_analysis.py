import httpx
import json
import pickle
import zlib
import unittest
import pathlib
import sys
import typing as t

class DataAnalysisTest(unittest.TestCase):
    @unittest.skip("This test performs live network calls and is for manual runs only.")
    def test_analyze_script_data(self):
        """
        This test fetches real data from the Proxmox VE community scripts API
        to analyze its size and content.
        """
        api_url = "https://community-scripts.github.io/ProxmoxVE/api/categories"
        timeout = 30

        print("\n--- Fetching real script data from the API ---")
        try:
            resp = httpx.get(api_url, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            self.fail(f"Failed to fetch scripts from the API: {e}")
        except json.JSONDecodeError as e:
            self.fail(f"Failed to decode JSON from API response: {e}")

        # Re-implement data processing logic from _fetch_scripts
        if not isinstance(data, list):
            self.fail("Unexpected categories payload type: not a list")

        seen: set[str] = set()
        scripts: list[dict[str, t.Any]] = []

        for category_index, category in enumerate(data):
            if not isinstance(category, dict):
                continue
            category_scripts = category.get("scripts", [])
            if not isinstance(category_scripts, list):
                continue
            for script_index, script in enumerate(category_scripts):
                if not isinstance(script, dict):
                    continue
                name = script.get("name")
                slug = script.get("slug")
                if not isinstance(name, str) or not isinstance(slug, str):
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
                description = description[:500] if isinstance(description, str) else ""
                seen.add(slug)
                scripts.append({"name": name, "slug": slug, "description": description})

        print(f"Fetched {len(scripts)} valid scripts.")

        serialized_scripts = pickle.dumps(scripts)
        pre_compressed_size = len(serialized_scripts)
        print(f"Pre-compressed (pickled) size: {pre_compressed_size} bytes")

        compressed_scripts = zlib.compress(serialized_scripts, level=zlib.Z_BEST_COMPRESSION)
        compressed_size = len(compressed_scripts)
        print(f"Compressed size: {compressed_size} bytes")
        print(f"Compression ratio: {pre_compressed_size / compressed_size:.2f}x")

        print("\n--- Description Analysis ---")
        description_lengths = [len(s.get("description", "")) for s in scripts]
        description_lengths.sort(reverse=True)

        if not description_lengths:
            print("No descriptions found for analysis.")
            return

        print(f"Longest description: {description_lengths[0]} characters")
        print(f"Shortest description: {description_lengths[-1]} characters")
        avg_len = sum(description_lengths) / len(description_lengths)
        print(f"Average description length: {avg_len:.2f} characters")

        print("\nTop 5 longest descriptions (first 100 chars):")
        printed_slugs = set()
        count = 0
        for length in description_lengths:
            if count >= 5:
                break
            for script in scripts:
                if len(script.get("description", "")) == length and script.get("slug") not in printed_slugs:
                    print(f"  - Length: {length}, Name: {script['name']}")
                    print(f"    Description: {script.get('description', '')[:100]}...")
                    printed_slugs.add(script.get("slug"))
                    count += 1
                    break

if __name__ == "__main__":
    import os
    if os.getenv("RUN_MANUAL_TESTS"):
        suite = unittest.TestSuite()
        suite.addTest(DataAnalysisTest("test_analyze_script_data"))
        runner = unittest.TextTestRunner()
        runner.run(suite)
    else:
        print("Skipping manual data analysis test. Set RUN_MANUAL_TESTS=1 to run it.")
