"""Command lines a person or an Agent can paste: an argument list quoted for the shell of this device.

``order quote`` names the command to run next (``next_command``) and the command to resume with after sign-in
(``details.handoff.resume_command``). Both come with the argument list itself (``next_argv`` / ``resume_argv``, for an
Agent that runs it without a shell) and the name of the shell the text is quoted for:

* ``posix`` (Linux, macOS, Git Bash): an argument made only of ``[A-Za-z0-9_@%+=:,./-]`` stays bare; any other one,
  the empty one included, is written in single quotes, each ``'`` as ``'"'"'``.
* ``powershell`` (Windows PowerShell 5.1 and PowerShell 7, the default shells of Windows): an argument made only of
  ``[A-Za-z0-9_+=:./\\-]`` stays bare; any other one is written in single quotes, with each of the quote characters
  PowerShell reads as a single quote (``'`` and the typographic ``‘ ’ ‚ ‛``) doubled. ``cmd.exe`` is not supported.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

POSIX = "posix"
POWERSHELL = "powershell"
SHELLS = (POSIX, POWERSHELL)
_POSIX_BARE = re.compile(r"[A-Za-z0-9_@%+=:,./-]+")
_POWERSHELL_BARE = re.compile(r"[A-Za-z0-9_+=:./\\-]+")
#: The characters PowerShell reads as a single quote inside a single-quoted string (each is escaped by doubling it).
_POWERSHELL_QUOTES = re.compile("['‘’‚‛]")


def device_shell() -> str:
    """The shell a command printed on this device is quoted for: ``powershell`` on Windows, else ``posix``."""

    return POWERSHELL if os.name == "nt" else POSIX


def quote_posix(argument: str) -> str:
    """One argument as POSIX ``sh`` reads it back unchanged."""

    if _POSIX_BARE.fullmatch(argument):
        return argument
    return "'" + argument.replace("'", "'\"'\"'") + "'"


def quote_powershell(argument: str) -> str:
    """One argument as PowerShell reads it back unchanged."""

    if _POWERSHELL_BARE.fullmatch(argument):
        return argument
    return "'" + _POWERSHELL_QUOTES.sub(lambda match: match.group(0) * 2, argument) + "'"


def command_line(argv: Sequence[str], shell: str | None = None) -> str:
    """``argv`` as one command line of ``shell`` (default: ``device_shell()``)."""

    shell = shell or device_shell()
    if shell not in SHELLS:
        raise ValueError(f"shell is one of {', '.join(SHELLS)}")
    quote = quote_powershell if shell == POWERSHELL else quote_posix
    return " ".join(quote(str(argument)) for argument in argv)


__all__ = ["POSIX", "POWERSHELL", "SHELLS", "command_line", "device_shell", "quote_posix", "quote_powershell"]
