"""
TwinCAT Variant switching via .tsproj XML attribute.

The active variant is stored as the TcProjectVariant attribute on the
root <TcSmProject> element.  Switching variants is a single attribute
write -- no DTE COM automation required.

With EnableImplicitDefines="true" on the PLC project, the active
variant name (e.g. "Release" or "Test") is automatically available
as a compiler define in Structured Text.
"""

import sys
import re
from pathlib import Path
from typing import Optional


def log(phase: str, message: str, error: bool = False):
    level = "ERROR" if error else "INFO"
    print(f"[{level}] [{phase}] {message}", file=sys.stderr)


def _find_tsproj(solution_path: str) -> Optional[Path]:
    """Locate the .tsproj file next to the .sln."""
    sln = Path(solution_path)
    candidates = list(sln.parent.rglob("*.tsproj"))
    # Prefer non-backup, non-Test files
    for c in candidates:
        if "Test" not in c.stem and ".bak" not in c.suffixes:
            return c
    return candidates[0] if candidates else None


def get_active_variant(tsproj_path: str) -> Optional[str]:
    """Read the currently active variant from the .tsproj file."""
    try:
        content = Path(tsproj_path).read_text(encoding="utf-8")
        m = re.search(r'TcProjectVariant="([^"]*)"', content)
        return m.group(1) if m else None
    except Exception as exc:
        log("VARIANT", f"Failed to read variant: {exc}", error=True)
        return None


def activate_variant(
    solution_path: str,
    variant_name: str,
) -> bool:
    """Switch the active variant by updating the TcProjectVariant XML attribute.

    Returns True on success, False on failure.
    """
    tsproj = _find_tsproj(solution_path)
    if tsproj is None:
        log("VARIANT", "No .tsproj file found", error=True)
        return False

    try:
        content = tsproj.read_text(encoding="utf-8")

        # Read current variant from the attribute
        m = re.search(r'TcProjectVariant="([^"]*)"', content)
        current = m.group(1) if m else ""

        if current == variant_name:
            log("VARIANT", f"Already on variant: {variant_name}")
            return True

        # Replace only the attribute value — do not rewrite the rest of the file
        new_content = re.sub(
            r'TcProjectVariant="[^"]*"',
            f'TcProjectVariant="{variant_name}"',
            content,
            count=1,
        )
        tsproj.write_text(new_content, encoding="utf-8")

        log("VARIANT", f"Switched variant: {current} -> {variant_name}")
        return True
    except Exception as exc:
        log("VARIANT", f"Failed to activate '{variant_name}': {exc}", error=True)
        return False
