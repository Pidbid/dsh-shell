from __future__ import annotations

import os
import subprocess
import sys

import pytest

CASES = [
    # The session asks for modules the bundled Qt cannot load: use IBus.
    ("wayland;fcitx", "fcitx", "ibus", "ibus"),
    ("wayland", "", "ibus", "ibus"),
    ("", "", "ibus", "ibus"),
    # Explicitly requested modules we ship are preserved.
    ("ibus", "ibus", "ibus", "ibus"),
    ("compose", "", "compose", ""),
]

PROBE = (
    "import dsh_shell, os; "
    "print(os.environ.get('QT_IM_MODULES', '') + '|' + os.environ.get('QT_IM_MODULE', ''))"
)


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux-only Qt input-context selection"
)
@pytest.mark.parametrize(("modules", "module", "expected_modules", "expected_module"), CASES)
def test_qt_ime_module_selection(
    modules: str, module: str, expected_modules: str, expected_module: str
) -> None:
    env = os.environ.copy()
    env.pop("QT_IM_MODULES", None)
    env.pop("QT_IM_MODULE", None)
    if modules:
        env["QT_IM_MODULES"] = modules
    if module:
        env["QT_IM_MODULE"] = module

    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == f"{expected_modules}|{expected_module}"
