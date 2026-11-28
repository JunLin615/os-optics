"""Command lines a person or an Agent can paste: an argument list quoted for the shells of this device.

``order quote`` names the command to run next (``next_command``) and the command to resume with after sign-in
(``details.handoff.resume_command``). Both come with the argument list itself (``next_argv`` / ``resume_argv``, for an
Agent that runs it without a shell), the shells the one text is quoted for (``next_shells`` / ``resume_shells``) and
the text for each shell of the device (``next_commands`` / ``resume_commands``, ``{shell: text}``).

The shells of a device (``device_shells``): ``posix`` (sh, bash, zsh) everywhere; on Windows also ``powershell``
(Windows PowerShell 5.1 and PowerShell 7, the default shells there), and ``posix`` there is Git Bash or MSYS, which many
Agents use. ``cmd.exe`` is not supported. Paths in a printed command are absolute and, on Windows, written with ``/``
(``command_path``; Windows and Python read ``C:/dir/file.json`` as ``C:\\dir\\file.json``), so no argument carries a
backslash that the two shells read differently.

``printed_command`` gives one text for every shell of the device when one exists (``quote_portable``): an argument
made only of ``[A-Za-z0-9_+=:./-]`` stays bare; one without a single quote (``'`` or one of the typographic ``‘ ’ ‚ ‛``,
which PowerShell also reads as a single quote) is written in single quotes; one without any of ``$ ` " \\ !`` and the
typographic double quotes ``“ ” „`` is written in double quotes, which keeps an apostrophe in a path. An argument that
is none of these (a ``'`` together with one of those characters) has no text both shells read the same: the command
is then the text for the device's first shell (``powershell`` on Windows), and ``*_commands`` hold one text per shell:

* ``posix``: an argument made only of ``[A-Za-z0-9_@%+=:,./-]`` stays bare; any other one, the empty one included, is
  written in single quotes, each ``'`` as ``'"'"'``.
* ``powershell``: an argument made only of ``[A-Za-z0-9_+=:./\\-]`` stays bare; any other one is written in single
  quotes, with each of the quote characters PowerShell reads as a single quote doubled.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path

POSIX = "posix"
POWERSHELL = "powershell"
SHELLS = (POSIX, POWERSHELL)
#: How the text names each shell.
SHELL_NAMES = {POSIX: "sh", POWERSHELL: "PowerShell"}
_POSIX_BARE = re.compile(r"[A-Za-z0-9_@%+=:,./-]+")
_POWERSHELL_BARE = re.compile(r"[A-Za-z0-9_+=:./\\-]+")
_PORTABLE_BARE = re.compile(r"[A-Za-z0-9_+=:./-]+")
#: The characters PowerShell reads as a single quote inside a single-quoted string (each is escaped by doubling it).
_POWERSHELL_QUOTES = re.compile("['‘’‚‛]")
#: The characters either shell reads specially inside a double-quoted string (``!``: history expansion of an
#: interactive bash), and the ones PowerShell reads as a double quote.
_DOUBLE_QUOTE_SPECIAL = re.compile('[$`"\\\\!“”„]')


def device_shells() -> tuple[str, ...]:
    """The shells a command printed on this device is for, the default first: ``(powershell, posix)`` on Windows, else
    ``(posix,)``."""

    return (POWERSHELL, POSIX) if os.name == "nt" else (POSIX,)


def device_shell() -> str:
    """The default shell of this device: ``powershell`` on Windows, else ``posix``."""

    return device_shells()[0]


def command_path(path: str | Path) -> str:
    """``path`` as a printed command names it: absolute, and with ``/`` on Windows."""

    absolute = Path(path).absolute()
    return absolute.as_posix() if os.name == "nt" else str(absolute)


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


def quote_portable(argument: str) -> str | None:
    """One argument as both POSIX ``sh`` and PowerShell read it back unchanged, or None when no text does (module
    docstring)."""

    if _PORTABLE_BARE.fullmatch(argument):
        return argument
    if argument and not _POWERSHELL_QUOTES.search(argument):
        return "'" + argument + "'"
    if argument and not _DOUBLE_QUOTE_SPECIAL.search(argument):
        return '"' + argument + '"'
    return None


def command_line(argv: Sequence[str], shell: str | None = None) -> str:
    """``argv`` as one command line of ``shell`` (default: ``device_shell()``)."""

    shell = shell or device_shell()
    if shell not in SHELLS:
        raise ValueError(f"shell is one of {', '.join(SHELLS)}")
    quote = quote_powershell if shell == POWERSHELL else quote_posix
    return " ".join(quote(str(argument)) for argument in argv)


def printed_command(argv: Sequence[str], shells: Sequence[str] | None = None) -> tuple[str, list[str], dict[str, str]]:
    """``argv`` as the command to print for ``shells`` (default: ``device_shells()``, the default first): ``(text, the
    shells the text is for, {shell: text})`` -- one text for all of them when one exists (``quote_portable``), else the
    text of the first shell, and each shell's own text."""

    shells = list(shells or device_shells())
    if not shells or any(shell not in SHELLS for shell in shells):
        raise ValueError(f"shells are some of {', '.join(SHELLS)}")
    arguments = [str(argument) for argument in argv]
    if len(shells) > 1:
        quoted = [quote_portable(argument) for argument in arguments]
        if all(word is not None for word in quoted):
            text = " ".join(word for word in quoted if word is not None)
            return text, shells, {shell: text for shell in shells}
    texts = {shell: command_line(arguments, shell) for shell in shells}
    return texts[shells[0]], shells[:1], texts


def shells_text(shells: Sequence[str]) -> str:
    """The shells a printed command is for, as a person reads them (e.g. ``PowerShell and sh``)."""

    return " and ".join(SHELL_NAMES[shell] for shell in shells)


__all__ = ["POSIX", "POWERSHELL", "SHELLS", "SHELL_NAMES", "command_line", "command_path", "device_shell",
           "device_shells", "printed_command", "quote_portable", "quote_posix", "quote_powershell", "shells_text"]
