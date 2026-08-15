"""Locating the zsh toolkit shipped inside the wheel.

The shell runs *before* the application exists, so it cannot live in the app's
virtualenv. It ships as package data and is executed with ``os.execvp``, which
replaces this process — signals, exit codes and terminal control all behave as
if the operator had run the script directly, which matters for a tool whose
whole job is an interactive wait.
"""

from __future__ import annotations

import os
import shutil
from importlib import resources
from pathlib import Path

__all__ = ["exec_toolkit", "lib_path", "script_path", "templates_path"]


def _root() -> Path:
    return Path(str(resources.files("eventkit.azure")))


def script_path() -> Path:
    return _root() / "eventkit-azure"


def lib_path() -> Path:
    return _root() / "lib"


def templates_path() -> Path:
    return _root() / "templates"


def exec_toolkit(argv: list[str]) -> int:
    """Replace this process with the zsh toolkit.

    Returns an exit code only when the handover itself fails; on success it does
    not return at all.
    """
    zsh = shutil.which("zsh")
    if zsh is None:
        print(
            "The Azure toolkit is written in zsh, which is not installed.\n"
            "  macOS: already present\n"
            "  Debian/Ubuntu: sudo apt-get install zsh\n"
            "  Alpine: apk add zsh",
        )
        return 127

    script = script_path()
    if not script.is_file():
        print(f"The toolkit is missing from the installed package ({script}).")
        return 1

    env = dict(os.environ)
    env["EVENTKIT_AZURE_LIB"] = str(lib_path())
    env.setdefault("EK_VERSION", _version())

    os.execve(zsh, [zsh, str(script), *argv], env)
    return 0  # pragma: no cover - execve does not return


def _version() -> str:
    from eventkit import __version__

    return __version__
