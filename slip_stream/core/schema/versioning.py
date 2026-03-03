"""Semantic versioning utilities for schema version management.

Provides parsing, comparison, and sorting for semver strings (``major.minor.patch``).
No external dependencies — uses only stdlib.
"""

from __future__ import annotations

import re

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_semver(version: str) -> tuple[int, int, int]:
    """Parse a semver string into a ``(major, minor, patch)`` tuple.

    Args:
        version: A version string like ``"1.2.3"``.

    Returns:
        Tuple of ``(major, minor, patch)`` integers.

    Raises:
        ValueError: If the string is not a valid semver.
    """
    match = _SEMVER_RE.match(version.strip())
    if not match:
        raise ValueError(
            f"Invalid semver: {version!r} — expected 'major.minor.patch' (e.g. '1.0.0')"
        )
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def is_valid_semver(version: str) -> bool:
    """Check whether a string is a valid semver (``major.minor.patch``).

    >>> is_valid_semver("1.0.0")
    True
    >>> is_valid_semver("abc")
    False
    """
    return _SEMVER_RE.match(version.strip()) is not None


def compare_versions(a: str, b: str) -> int:
    """Compare two semver strings.

    Returns:
        ``-1`` if *a < b*, ``0`` if *a == b*, ``1`` if *a > b*.

    Raises:
        ValueError: If either string is not a valid semver.
    """
    pa, pb = parse_semver(a), parse_semver(b)
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0


def sort_versions(versions: list[str]) -> list[str]:
    """Sort version strings by semver in ascending order.

    Invalid semver strings are placed at the end, sorted lexicographically.

    >>> sort_versions(["2.0.0", "1.0.0", "1.1.0"])
    ['1.0.0', '1.1.0', '2.0.0']
    """
    valid: list[tuple[tuple[int, int, int], str]] = []
    invalid: list[str] = []

    for v in versions:
        try:
            valid.append((parse_semver(v), v))
        except ValueError:
            invalid.append(v)

    valid.sort(key=lambda x: x[0])
    return [v for _, v in valid] + sorted(invalid)


def latest_version(versions: list[str]) -> str:
    """Return the highest version by semver comparison.

    >>> latest_version(["1.0.0", "1.10.0", "1.9.0"])
    '1.10.0'

    Raises:
        ValueError: If the list is empty.
    """
    if not versions:
        raise ValueError("Cannot determine latest version from empty list")
    sorted_v = sort_versions(versions)
    return sorted_v[-1]
