"""Regression test for Windows launcher/package-name collision resilience."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    package = importlib.import_module("hearpitch_core")
    package_file = Path(package.__file__).resolve()
    assert package_file == (PROJECT_ROOT / "hearpitch_core" / "__init__.py").resolve(), package_file
    legacy_file = PROJECT_ROOT / "hearpitch.py"
    assert not legacy_file.exists(), "The source release must not include a legacy launcher."
    legacy_file.write_text("raise RuntimeError('legacy file must not be imported')\n", encoding="utf-8")
    try:
        result = subprocess.run(
            [sys.executable, "hearpitch_cli.py", "--help"],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        legacy_file.unlink(missing_ok=True)
    assert result.returncode == 0, result.stderr
    assert "HearPitch Local V260924A" in result.stdout, result.stdout
    assert "hearpitch_cli.py" in result.stdout, result.stdout
    print(f"Hotfix import test passed with stale hearpitch.py present: {package_file}")


if __name__ == "__main__":
    main()
