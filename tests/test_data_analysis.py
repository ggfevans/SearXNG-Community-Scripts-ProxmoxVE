import httpx
import json
import pickle
import zlib
import unittest
import pathlib
import sys
import typing as t # Needed for type hints in the re-implemented _fetch_scripts logic

# Add the project root to the python path to allow importing the engine
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.append(str(REPO_ROOT))

class DataAnalysisTest(unittest.TestCase):
    def test_analyze_script_data(self):
        """
        This test fetches real data from the Proxmox VE community scripts API
        to analyze its size and content. It is intended for manual runs and
        is not part of the regular test suite.
        """
        api_url = "https://community-scripts.github.io/ProxmoxVE/api/categories"
        timeout = 30

        print("\n--- Fetching real script data from the API ---")
        try:
            resp = httpx.get(api_url, timeout=timeout)
            resp.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
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
                print(f"Skipping malformed category at index {category_index}")
                continue

            category_scripts = category.get("scripts", [])
            if not isinstance(category_scripts, list):
                print(f"Skipping malformed scripts list in category index {category_index}")
                continue

            for script_index, script in enumerate(category_scripts):
                if not isinstance(script, dict):
                    print(f"Skipping malformed script at category {category_index} index {script_index}")
                    continue

                name = script.get("name")
                slug = script.get("slug")
                if not isinstance(name, str) or not isinstance(slug, str):
                    print(f"Skipping script with invalid name/slug at category {category_index} index {script_index}: name={name!r} slug={slug!r}")
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
                # Truncate description to 500 characters for analysis
                description = description[:500] if isinstance(description, str) else ""

                seen.add(slug)
                scripts.append(
                    {
                        "name": name,
                        "slug": slug,
                        "description": description,
                    }
                )
        # End re-implemented data processing logic

        print(f"Fetched {len(scripts)} valid scripts.")

        # Calculate pre-compressed size
        serialized_scripts = pickle.dumps(scripts)
        pre_compressed_size = len(serialized_scripts)
        print(f"Pre-compressed (pickled) size: {pre_compressed_size} bytes")

        # Calculate compressed size
        compressed_scripts = zlib.compress(serialized_scripts, level=zlib.Z_BEST_COMPRESSION)
        compressed_size = len(compressed_scripts)
        print(f"Compressed size: {compressed_size} bytes")
        print(f"Compression ratio: {pre_compressed_size / compressed_size:.2f}x")

        # Analyze descriptions
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
        for i in range(min(5, len(description_lengths))):
            # Find the script corresponding to the current length
            # This is not efficient for many scripts with same length, but sufficient for analysis
            script = next(s for s in scripts if len(s.get("description", "")) == description_lengths[i])
            print(f"  - Length: {description_lengths[i]}, Name: {script['name']}")
            print(f"    Description: {script.get('description', '')[:100]}...")

if __name__ == "__main__":
    unittest.main()
