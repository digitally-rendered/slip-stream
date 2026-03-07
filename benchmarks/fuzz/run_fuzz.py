"""Schemathesis fuzz runner for slip-stream and stellar-drive.

Language-agnostic: works against any running HTTP server that implements
the hex CRUD contract. Generates an OpenAPI spec from schema files and
runs schemathesis against the target URL.

Usage:
    python benchmarks/fuzz/run_fuzz.py --url http://localhost:8100/api/v1 --mode all
    python benchmarks/fuzz/run_fuzz.py --url http://localhost:8200/api/v1 --mode lifecycle
    python benchmarks/fuzz/run_fuzz.py --url http://localhost:8100/api/v1 --mode positive --max-examples 100

Modes:
    lifecycle  — CRUD lifecycle per entity (create→get→list→patch→delete→verify 404)
    positive   — schemathesis parametrized fuzzing with valid-ish data
    negative   — corrupted payloads (type swaps, unknown fields)
    all        — runs all three
"""

import argparse
import json
import random
import sys
import uuid
from pathlib import Path

import requests

# Support running as both `python -m benchmarks.fuzz.run_fuzz` and `python benchmarks/fuzz/run_fuzz.py`
try:
    from benchmarks.fuzz.checks import register_hex_checks
    from benchmarks.fuzz.gen_openapi import generate_openapi
except ImportError:
    import importlib.util
    from pathlib import Path as _Path

    _fuzz_dir = _Path(__file__).parent
    for _mod_name, _file_name in [
        ("checks", "checks.py"),
        ("gen_openapi", "gen_openapi.py"),
    ]:
        _spec = importlib.util.spec_from_file_location(
            _mod_name, _fuzz_dir / _file_name
        )
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        if _mod_name == "checks":
            register_hex_checks = _mod.register_hex_checks
        else:
            generate_openapi = _mod.generate_openapi


def _load_or_generate_spec(
    schema_dir: Path, api_prefix: str, spec_path: Path | None
) -> dict:
    """Load existing spec or generate from schemas."""
    if spec_path and spec_path.exists():
        with open(spec_path) as f:
            return json.load(f)
    return generate_openapi(schema_dir, api_prefix)


def _extract_entity_names(spec: dict) -> list[str]:
    """Get unique entity names from the spec's component schemas."""
    names = set()
    for key in spec.get("components", {}).get("schemas", {}):
        if key.endswith("_create"):
            names.add(key.removesuffix("_create"))
    return sorted(names)


def _generate_create_payload(spec: dict, entity_name: str) -> dict:
    """Generate a valid create payload from the spec's schema."""
    schema = spec["components"]["schemas"].get(f"{entity_name}_create", {})
    props = schema.get("properties", {})
    required = schema.get("required", [])

    payload = {}
    for field, field_schema in props.items():
        ftype = field_schema.get("type", "string")
        fmt = field_schema.get("format", "")
        enum = field_schema.get("enum")

        if enum:
            payload[field] = random.choice(enum)
        elif fmt == "uuid":
            payload[field] = str(uuid.uuid4())
        elif fmt == "date-time":
            payload[field] = "2026-01-01T00:00:00Z"
        elif fmt == "email":
            payload[field] = f"test-{uuid.uuid4().hex[:6]}@example.com"
        elif fmt == "uri" or fmt == "url":
            payload[field] = f"https://example.com/{uuid.uuid4().hex[:6]}"
        elif ftype == "string":
            payload[field] = f"test-{field}-{uuid.uuid4().hex[:6]}"
        elif ftype == "integer":
            payload[field] = random.randint(1, 100)
        elif ftype == "number":
            payload[field] = round(random.uniform(1.0, 100.0), 2)
        elif ftype == "boolean":
            payload[field] = random.choice([True, False])
        elif ftype == "array":
            item_type = field_schema.get("items", {}).get("type", "string")
            if item_type == "string":
                payload[field] = [f"item-{uuid.uuid4().hex[:4]}"]
            else:
                payload[field] = [1]
        elif field in required:
            payload[field] = f"test-{field}"

    return payload


def _corrupt_payload(payload: dict) -> dict:
    """Corrupt a payload for negative testing."""
    if not payload:
        return {"__unknown_field__": "should-be-rejected"}

    corrupted = dict(payload)
    field = random.choice(list(corrupted.keys()))
    val = corrupted[field]

    if isinstance(val, str):
        corrupted[field] = random.randint(-999, 999)
    elif isinstance(val, int):
        corrupted[field] = "not-a-number"
    elif isinstance(val, list):
        corrupted[field] = "not-an-array"
    elif isinstance(val, bool):
        corrupted[field] = "not-a-bool"

    corrupted["__unknown_field__"] = "injected"
    return corrupted


def _extract_data(response_json: dict) -> dict:
    """Extract entity data from response (handles envelope and flat formats)."""
    if isinstance(response_json, dict):
        return response_json.get("data", response_json)
    return response_json


class FuzzResult:
    def __init__(
        self, mode: str, entity: str, step: str, passed: bool, detail: str = ""
    ):
        self.mode = mode
        self.entity = entity
        self.step = step
        self.passed = passed
        self.detail = detail

    def __repr__(self):
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.mode}/{self.entity}/{self.step}: {self.detail}"


def run_lifecycle(base_url: str, spec: dict) -> list[FuzzResult]:
    """Run CRUD lifecycle tests against each entity."""
    results = []
    entities = _extract_entity_names(spec)

    for name in entities:
        kebab = name.replace("_", "-")
        url = f"{base_url}/{kebab}"
        payload = _generate_create_payload(spec, name)

        # CREATE
        resp = requests.post(f"{url}/", json=payload, timeout=10)
        if resp.status_code != 201:
            results.append(
                FuzzResult(
                    "lifecycle",
                    name,
                    "create",
                    False,
                    f"status={resp.status_code} body={resp.text[:200]}",
                )
            )
            continue
        results.append(FuzzResult("lifecycle", name, "create", True, "201"))

        data = _extract_data(resp.json())
        entity_id = data.get("entity_id")
        if not entity_id:
            results.append(
                FuzzResult(
                    "lifecycle", name, "create_entity_id", False, "missing entity_id"
                )
            )
            continue

        # GET
        resp = requests.get(f"{url}/{entity_id}", timeout=10)
        passed = resp.status_code == 200
        results.append(
            FuzzResult("lifecycle", name, "get", passed, f"status={resp.status_code}")
        )

        # LIST
        resp = requests.get(f"{url}/", timeout=10)
        passed = resp.status_code == 200
        results.append(
            FuzzResult("lifecycle", name, "list", passed, f"status={resp.status_code}")
        )

        # PATCH
        update_field = next(
            (f for f in payload if f not in ("entity_id", "id")),
            None,
        )
        if update_field:
            update_val = payload[update_field]
            if isinstance(update_val, str):
                update_payload = {update_field: f"updated-{uuid.uuid4().hex[:6]}"}
            elif isinstance(update_val, int):
                update_payload = {update_field: update_val + 1}
            else:
                update_payload = {update_field: update_val}
        else:
            update_payload = {}

        resp = requests.patch(f"{url}/{entity_id}", json=update_payload, timeout=10)
        passed = resp.status_code == 200
        if passed:
            patch_data = _extract_data(resp.json())
            rv = patch_data.get("record_version")
            if rv != 2:
                results.append(
                    FuzzResult(
                        "lifecycle",
                        name,
                        "patch_version",
                        False,
                        f"record_version={rv}, expected 2",
                    )
                )
            else:
                results.append(
                    FuzzResult("lifecycle", name, "patch", True, "200, rv=2")
                )
        else:
            results.append(
                FuzzResult(
                    "lifecycle", name, "patch", False, f"status={resp.status_code}"
                )
            )

        # DELETE
        resp = requests.delete(f"{url}/{entity_id}", timeout=10)
        passed = resp.status_code in (200, 204)
        results.append(
            FuzzResult(
                "lifecycle", name, "delete", passed, f"status={resp.status_code}"
            )
        )

        # GET after DELETE → 404
        resp = requests.get(f"{url}/{entity_id}", timeout=10)
        passed = resp.status_code == 404
        results.append(
            FuzzResult(
                "lifecycle",
                name,
                "get_after_delete",
                passed,
                f"status={resp.status_code}",
            )
        )

    return results


def _parse_host_from_url(base_url: str) -> str:
    """Extract scheme+host from a base URL (strip path prefix)."""
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def run_positive_fuzz(
    base_url: str, spec: dict, max_examples: int = 20
) -> list[FuzzResult]:
    """Run schemathesis positive fuzzing via CLI subprocess.

    Writes the spec to a temp file and runs `schemathesis run` against it.
    This uses schemathesis's own test case generation (Hypothesis-powered),
    which handles path parameters, request bodies, and all edge cases.
    """
    import subprocess
    import tempfile

    results = []
    host = _parse_host_from_url(base_url)

    # Write spec to temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(spec, f)
        spec_path = f.name

    try:
        import shutil

        st_cmd = shutil.which("schemathesis") or "schemathesis"
        cmd = [
            st_cmd,
            "run",
            spec_path,
            f"--url={host}",
            f"--max-examples={max_examples}",
            "--checks=not_a_server_error",
        ]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        passed = proc.returncode == 0
        output = proc.stdout + proc.stderr
        # Extract summary line
        lines = output.strip().split("\n")
        summary = next(
            (
                line
                for line in reversed(lines)
                if "passed" in line.lower()
                or "failed" in line.lower()
                or "error" in line.lower()
            ),
            lines[-1] if lines else "no output",
        )
        detail = f"exit={proc.returncode}: {summary.strip()}"

        results.append(FuzzResult("positive", "*", "schemathesis", passed, detail))

    except subprocess.TimeoutExpired:
        results.append(
            FuzzResult("positive", "*", "schemathesis", False, "timeout after 120s")
        )
    finally:
        Path(spec_path).unlink(missing_ok=True)

    return results


def run_negative_fuzz(
    base_url: str, spec: dict, max_examples: int = 20
) -> list[FuzzResult]:
    """Run negative fuzzing — send corrupted payloads, verify no 500s."""
    results = []
    entities = _extract_entity_names(spec)
    failures = []
    total = 0

    for name in entities:
        kebab = name.replace("_", "-")
        url = f"{base_url}/{kebab}/"

        for _ in range(max_examples // max(len(entities), 1)):
            total += 1
            payload = _generate_create_payload(spec, name)
            corrupted = _corrupt_payload(payload)

            try:
                resp = requests.post(url, json=corrupted, timeout=10)
                if resp.status_code >= 500:
                    failures.append(
                        f"POST {url}: {resp.status_code} with {json.dumps(corrupted)[:100]}"
                    )
            except Exception as e:
                failures.append(f"POST {url}: {e}")

    passed = len(failures) == 0
    detail = f"{total} corrupted requests, {len(failures)} server errors"
    if failures:
        detail += f": {failures[:3]}"
    results.append(FuzzResult("negative", "*", "fuzz", passed, detail))
    return results


def print_results(results: list[FuzzResult]) -> bool:
    """Print results and return True if all passed."""
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)

    for r in results:
        print(r)

    print(f"\n{'='*60}")
    print(f"Total: {len(results)} checks — {passed} passed, {failed} failed")

    if failed:
        print("\nFAILED checks:")
        for r in results:
            if not r.passed:
                print(f"  {r}")
        return False

    print("\nAll checks passed.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Schemathesis fuzz runner")
    parser.add_argument(
        "--url",
        required=True,
        help="Base URL of the API (e.g., http://localhost:8100/api/v1)",
    )
    parser.add_argument(
        "--schema-dir",
        type=Path,
        default=Path("benchmarks/schemas"),
        help="Directory containing JSON schema files",
    )
    parser.add_argument(
        "--spec",
        type=Path,
        default=None,
        help="Path to pre-generated OpenAPI spec (skips generation)",
    )
    parser.add_argument(
        "--mode",
        choices=["lifecycle", "positive", "negative", "all"],
        default="all",
        help="Fuzz mode to run",
    )
    parser.add_argument(
        "--max-examples",
        type=int,
        default=20,
        help="Max examples for positive/negative fuzzing",
    )
    parser.add_argument(
        "--api-prefix",
        default="/api/v1",
        help="API prefix used when generating the spec",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write results as JSON to this file",
    )
    args = parser.parse_args()

    spec = _load_or_generate_spec(args.schema_dir, args.api_prefix, args.spec)

    all_results: list[FuzzResult] = []
    modes = ["lifecycle", "positive", "negative"] if args.mode == "all" else [args.mode]

    for mode in modes:
        print(f"\n{'='*60}")
        print(f"Running {mode} fuzz tests against {args.url}")
        print(f"{'='*60}\n")

        if mode == "lifecycle":
            all_results.extend(run_lifecycle(args.url, spec))
        elif mode == "positive":
            all_results.extend(run_positive_fuzz(args.url, spec, args.max_examples))
        elif mode == "negative":
            all_results.extend(run_negative_fuzz(args.url, spec, args.max_examples))

    success = print_results(all_results)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(
                {
                    "url": args.url,
                    "mode": args.mode,
                    "total": len(all_results),
                    "passed": sum(1 for r in all_results if r.passed),
                    "failed": sum(1 for r in all_results if not r.passed),
                    "results": [
                        {
                            "mode": r.mode,
                            "entity": r.entity,
                            "step": r.step,
                            "passed": r.passed,
                            "detail": r.detail,
                        }
                        for r in all_results
                    ],
                },
                f,
                indent=2,
            )
        print(f"\nResults written to {args.output}")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
