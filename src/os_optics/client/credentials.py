"""The OpenSci Account token on this device.

The client finds the token in this order (the first that is set wins):

1. an explicit argument: the token text (``str``), a token file (``pathlib.Path`` / ``os.PathLike``) or an
   ``AccountToken``;
2. the ``OPENSCI_TOKEN`` environment variable (the token text);
3. the ``OPENSCI_TOKEN_FILE`` environment variable (the path of a token file);
4. the token file ``~/.opensci/token`` (``os-optics account token create --save`` writes it).

A token file holds one token; surrounding whitespace is ignored. The token is ``oat_<32 hex>_<64 hex>``; the first
part is its non-secret token id. It is held in an ``AccountToken``, whose ``repr`` / ``str`` show only the token id
and where it came from; ``reveal()`` is the one way to read the secret. ``save_token_file`` writes a token file that
only the current user can read.

Without a token, ``resolve_token`` raises ``TOKEN_MISSING`` whose ``details.handoff`` says how to get one
(``account_handoff``): the OpenSci Account registration and sign-in pages, which return to the token page
(``<account>/register?next=%2Ftokens``, ``<account>/login?next=%2Ftokens``; ``<account>`` is ``OPENSCI_ACCOUNT_URL``, a base
URL that may carry a path, else the packaged default), the token file to save the new token to, and the command to run
again (filled in by the command that needed the token with ``resume_with``: ``resume_argv``, the argument list,
``resume_shell``, the shell it is quoted for -- ``posix`` or ``powershell`` (``shell_words``) -- and ``resume_command``,
the list quoted for that shell). ``handoff_text`` is the same as text.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..deployment import default_account_origin
from .errors import TOKEN_FILE_UNREADABLE, TOKEN_MALFORMED, TOKEN_MISSING, OpenSciConfigurationError, Recovery
from . import shell_words

ENV_TOKEN = "OPENSCI_TOKEN"
ENV_TOKEN_FILE = "OPENSCI_TOKEN_FILE"
ENV_ACCOUNT_URL = "OPENSCI_ACCOUNT_URL"
#: The Account page a registration or sign-in returns to (``next``, URL-encoded): where Agent tokens are created.
TOKENS_RETURN_PATH = "%2Ftokens"
DEFAULT_TOKEN_DIR = ".opensci"
DEFAULT_TOKEN_FILE_NAME = "token"
TOKEN_PATTERN = re.compile(r"oat_([0-9a-f]{32})_([0-9a-f]{64})")
#: A token file is one line; anything much longer is not a token file.
MAX_TOKEN_FILE_BYTES = 4096
#: POSIX permissions of the token file and its directory: owner only.
TOKEN_FILE_MODE = 0o600
TOKEN_DIR_MODE = 0o700
UUID_GROUPS = (8, 4, 4, 4, 12)

SOURCE_ARGUMENT = "argument"
SOURCE_ARGUMENT_FILE = "argument file"
SOURCE_ENV_TOKEN = ENV_TOKEN
SOURCE_ENV_TOKEN_FILE = ENV_TOKEN_FILE
SOURCE_DEFAULT_FILE = "default token file"
#: How a message names each token source (``source`` itself stays the stable value above).
SOURCE_WORDS = {SOURCE_ARGUMENT: "the token argument", SOURCE_ARGUMENT_FILE: "the token file you named",
                SOURCE_ENV_TOKEN: SOURCE_ENV_TOKEN, SOURCE_ENV_TOKEN_FILE: f"the file {SOURCE_ENV_TOKEN_FILE} names",
                SOURCE_DEFAULT_FILE: "the default token file"}


def _uuid_text(hex32: str) -> str:
    parts, start = [], 0
    for size in UUID_GROUPS:
        parts.append(hex32[start:start + size])
        start += size
    return "-".join(parts)


class AccountToken:
    """An OpenSci Account token held in memory. ``repr`` / ``str`` never show the secret; it cannot be pickled."""

    __slots__ = ("_secret", "path", "source", "token_id")

    def __init__(self, value: str, *, source: str = SOURCE_ARGUMENT, path: Path | None = None) -> None:
        text = value.strip() if isinstance(value, str) else ""
        match = TOKEN_PATTERN.fullmatch(text)
        if match is None:
            raise OpenSciConfigurationError(
                TOKEN_MALFORMED,
                f"the Account token from {SOURCE_WORDS.get(source, source)} is not an OpenSci Account token (expected "
                "oat_<32 hex>_<64 hex>, one "
                "line, no quotes)", details={"source": source, **({"path": str(path)} if path is not None else {})},
                recovery=Recovery.PROVIDE_TOKEN)
        self._secret = text
        self.token_id = _uuid_text(match.group(1))
        self.source = source
        self.path = path

    def reveal(self) -> str:
        """The token text. Use it only to send it to OpenSci or to write the user's token file."""

        return self._secret

    def authorization(self) -> str:
        """The value of the HTTP ``Authorization`` header."""

        return "Bearer " + self._secret

    def __repr__(self) -> str:
        return f"AccountToken(token_id={self.token_id!r}, source={self.source!r})"

    __str__ = __repr__

    def __reduce_ex__(self, protocol: Any) -> Any:
        raise TypeError("an AccountToken cannot be pickled or copied")


def account_base_url(environ: Mapping[str, str] | None = None) -> str:
    """The OpenSci Account website: ``OPENSCI_ACCOUNT_URL`` (a base URL, possibly with a path), else the packaged
    default (``os_optics/deployment.toml`` ``[account] origin``); without a trailing slash."""

    env = os.environ if environ is None else environ
    value = str(env.get(ENV_ACCOUNT_URL, "") or "").strip()
    return (value or default_account_origin()).rstrip("/")


def account_handoff(environ: Mapping[str, str] | None = None, *, home: Path | None = None,
                    resume_command: str | None = None) -> dict[str, Any]:
    """How a person without a token gets one and continues: ``{register_url, login_url, token_file, resume_command,
    resume_argv, resume_shell}`` (see the module docstring; the last three are None until ``resume_with``)."""

    base = account_base_url(environ)
    return {"register_url": f"{base}/register?next={TOKENS_RETURN_PATH}", "login_url": f"{base}/login?next={TOKENS_RETURN_PATH}",
            "token_file": str(default_token_file(home)), "resume_command": resume_command, "resume_argv": None,
            "resume_shell": None}


def resume_with(handoff: dict[str, Any], argv: list[str], shell: str | None = None) -> dict[str, Any]:
    """``handoff`` with the command to resume with: ``resume_argv`` (``argv``), ``resume_shell`` (``shell``, default the
    shell of this device) and ``resume_command`` (``argv`` quoted for it)."""

    shell = shell or shell_words.device_shell()
    handoff.update(resume_argv=[str(argument) for argument in argv], resume_shell=shell,
                   resume_command=shell_words.command_line(argv, shell))
    return handoff


def handoff_text(handoff: Mapping[str, Any], *, kept: str | None = None, repeat_note: str | None = None) -> str:
    """The handoff as the lines a person reads. ``kept``: what stays on this device meanwhile (e.g. the BOM file);
    ``repeat_note``: why running the command again is safe."""

    kept_text = f"; your {kept}" if kept else ""
    resume = handoff.get("resume_command") or "the command you ran"
    lines = [f"No OpenSci Account token on this device. Nothing was sent{kept_text}.",
             f"1. Register:  {handoff['register_url']}   (or sign in: {handoff['login_url']})",
             f"2. Create an Agent token there and save it to {handoff['token_file']} (or set {ENV_TOKEN_FILE}).",
             f"3. Resume with the same command:  {resume}"]
    if repeat_note:
        lines.append(repeat_note)
    return "\n".join(lines) + "\n"


def default_token_file(home: Path | None = None) -> Path:
    """``~/.opensci/token``."""

    return (home if home is not None else Path.home()) / DEFAULT_TOKEN_DIR / DEFAULT_TOKEN_FILE_NAME


def read_token_file(path: str | os.PathLike[str], *, source: str = SOURCE_ARGUMENT_FILE) -> AccountToken:
    """The token of a token file."""

    if TOKEN_PATTERN.search(os.fspath(path)):
        # A token given where a token file belongs: refuse without echoing it (not even as a path).
        raise OpenSciConfigurationError(TOKEN_FILE_UNREADABLE, "a token was given where the path of a token file belongs; "
                                        "write the token to a file and give that file", details={"source": source},
                                        recovery=Recovery.PROVIDE_TOKEN)
    file = Path(path).expanduser()
    try:
        with file.open("rb") as handle:
            data = handle.read(MAX_TOKEN_FILE_BYTES + 1)
    except OSError as exc:
        raise OpenSciConfigurationError(TOKEN_FILE_UNREADABLE, f"the token file cannot be read ({type(exc).__name__})",
                                        details={"source": source, "path": str(file)}, recovery=Recovery.PROVIDE_TOKEN) from None
    if len(data) > MAX_TOKEN_FILE_BYTES:
        raise OpenSciConfigurationError(TOKEN_MALFORMED, "the token file is not a token file (too large)",
                                        details={"source": source, "path": str(file)}, recovery=Recovery.PROVIDE_TOKEN)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = ""
    return AccountToken(text, source=source, path=file)


def resolve_token(explicit: Any = None, *, environ: Mapping[str, str] | None = None, home: Path | None = None) -> AccountToken:
    """The Account token, found in the documented order (see the module docstring)."""

    env = os.environ if environ is None else environ
    if isinstance(explicit, AccountToken):
        return explicit
    if isinstance(explicit, os.PathLike):
        return read_token_file(explicit, source=SOURCE_ARGUMENT_FILE)
    if isinstance(explicit, str):
        return AccountToken(explicit, source=SOURCE_ARGUMENT)
    if explicit is not None:
        raise TypeError("token must be the token text (str), a token file path (os.PathLike) or an AccountToken")
    if env.get(ENV_TOKEN, "").strip():
        return AccountToken(env[ENV_TOKEN], source=SOURCE_ENV_TOKEN)
    if env.get(ENV_TOKEN_FILE, "").strip():
        return read_token_file(env[ENV_TOKEN_FILE].strip(), source=SOURCE_ENV_TOKEN_FILE)
    default = default_token_file(home)
    if default.is_file():
        return read_token_file(default, source=SOURCE_DEFAULT_FILE)
    raise OpenSciConfigurationError(
        TOKEN_MISSING, f"no OpenSci Account token: set {ENV_TOKEN} or {ENV_TOKEN_FILE}, or save one to {default}",
        details={"searched": [ENV_TOKEN, ENV_TOKEN_FILE, str(default)], "handoff": account_handoff(env, home=home)},
        recovery=Recovery.PROVIDE_TOKEN)


def _restrict_to_owner(path: Path) -> None:
    """Owner-only access to ``path``.

    POSIX: mode 0600 (0700 for a directory). Windows ignores POSIX modes; the best restriction available without an extra
    dependency is the file's ACL: ``icacls`` (always present in %SystemRoot%\\System32) first grants the current user full
    control, and only if that worked removes the inherited entries, so the file keeps exactly one entry: its owner. The
    token file lives in the user profile, which Windows already limits to the user, SYSTEM and administrators; if
    ``icacls`` is unavailable the file keeps that inherited protection."""

    if os.name != "nt":
        os.chmod(path, TOKEN_DIR_MODE if path.is_dir() else TOKEN_FILE_MODE)
        return
    user = os.environ.get("USERNAME")
    if not user:
        return
    domain = os.environ.get("USERDOMAIN")
    account = f"{domain}\\{user}" if domain else user
    icacls = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "icacls.exe"
    if not icacls.is_file():
        return
    run = {"capture_output": True, "check": False}
    granted = subprocess.run([str(icacls), str(path), "/grant:r", f"{account}:F"], **run)  # noqa: S603 -- fixed argv
    if granted.returncode == 0:
        subprocess.run([str(icacls), str(path), "/inheritance:r"], **run)  # noqa: S603 -- fixed argv


def save_token_file(token: AccountToken, path: str | os.PathLike[str] | None = None, *, overwrite: bool = False,
                    home: Path | None = None) -> Path:
    """Write ``token`` to ``path`` (default ``~/.opensci/token``) readable only by the current user and return the path.

    The file is created next to its destination, restricted, written and then moved into place, so a reader never
    sees a partial token. An existing file is replaced only with ``overwrite=True`` (``FileExistsError`` otherwise)."""

    target = Path(path).expanduser() if path is not None else default_token_file(home)
    if target.exists() and not overwrite:
        raise FileExistsError(f"{target} already exists (pass overwrite=True to replace it)")
    target.parent.mkdir(parents=True, exist_ok=True)
    if path is None:
        _restrict_to_owner(target.parent)
    staging = target.with_name(target.name + ".new")
    staging.unlink(missing_ok=True)
    descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), TOKEN_FILE_MODE)
    try:
        os.close(descriptor)
        _restrict_to_owner(staging)
        with staging.open("r+b") as handle:
            handle.write((token.reveal() + "\n").encode("ascii"))
        os.replace(staging, target)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    return target


def remove_token_file(path: str | os.PathLike[str]) -> bool:
    """Delete a token file; ``False`` if there was none."""

    file = Path(path).expanduser()
    try:
        file.unlink()
    except FileNotFoundError:
        return False
    return True


__all__ = [
    "ENV_ACCOUNT_URL",
    "ENV_TOKEN",
    "ENV_TOKEN_FILE",
    "AccountToken",
    "account_base_url",
    "account_handoff",
    "default_token_file",
    "handoff_text",
    "resume_with",
    "read_token_file",
    "remove_token_file",
    "resolve_token",
    "save_token_file",
]
