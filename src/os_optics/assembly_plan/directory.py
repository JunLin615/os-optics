"""The plan directory (DESIGN 1.3.1, 1.3.9): immutable revisions, the Projects and documents they bind, the journal.

Layout (``contract`` names every entry)::

    plan.json                     copy of the head revision (convenience, never authoritative)
    revisions/r0001.json ...      revision documents, canonical osc1 bytes, 1-based and contiguous
    projects/<project_hash>.json  every Project a revision binds (canonical bytes; the name is its osc1 hash)
    mechanical/<sha256>.json      the USER mechanical documents a revision binds (their bytes as given)
    runs/<record_hash>.json       the Local Validation record of every optics run a revision binds
    history.jsonl                 the journal: one ``HistoryLineV1`` per line; its last line names the head
    plan.lock                     held for every write

Write protocol (``PlanDirectory.locked`` + ``commit``): the lock is an exclusive-create file (``PLAN_DIR_LOCKED`` while
another write holds it); under it the head is read from the journal, the caller's base checked, the revision computed,
then the bound files are written, the revision file (temporary name, then ``Path.replace``), the journal (rewritten the
same way with the new line) and ``plan.json`` last. A revision file above the journal's head (an interrupted write) is
ignored and overwritten by the next write.

Reader rule (``verify``): every revision up to the head validates (its plan digest included), each names its
predecessor's digest, every bound Project and USER document is present with its hash, the journal lines are contiguous
and name existing revisions, and ``plan.json`` holds the head revision's bytes; anything else is
``PLAN_DIR_INCONSISTENT`` with the problems listed. pathlib only: no ``os``, no clock.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .._vendor.shared_contracts.common.hashes import canonicalize_osc1_bytes
from .._vendor.shared_contracts.optical_design import LocalValidationRecordV1, ProjectDocumentV2
from .contract import (HISTORY_FILE, LOCK_FILE, MECHANICAL_DIR, PLAN_FILE, PROJECTS_DIR, REVISIONS_DIR, RUNS_DIR,
                       AssemblyPlanError, AssemblyPlanErrorCode, AssemblyPlanRevisionV1, HistoryLineV1, parse_revision_file_name,
                       revision_file_name)
from .mechanical.model import MechanicalPlanBinding

#: The recovery of a held lock.
LOCK_RECOVERY = "wait for the other os-optics command to finish; delete PLAN_DIR/plan.lock if no os-optics process uses this plan"
#: Problems listed in a PLAN_DIR_INCONSISTENT refusal at most.
MAX_PROBLEMS = 64
#: Characters of a validation error a problem keeps.
ERROR_TEXT_MAX = 300


def _inconsistent(message: str, problems: list[str], plan_dir: Path) -> AssemblyPlanError:
    return AssemblyPlanError(AssemblyPlanErrorCode.PLAN_DIR_INCONSISTENT, message, {"plan_dir": str(plan_dir)}, problems[:MAX_PROBLEMS])


def _replace(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` through a temporary name beside it (``Path.replace``)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial")
    temporary.write_bytes(data)
    temporary.replace(path)


def revision_bytes(revision: AssemblyPlanRevisionV1) -> bytes:
    """The stored bytes of a revision (canonical osc1 JSON of its payload)."""

    return canonicalize_osc1_bytes(revision.payload())


def project_bytes(project: ProjectDocumentV2) -> bytes:
    return canonicalize_osc1_bytes(project.payload())


def history_line_bytes(line: HistoryLineV1) -> bytes:
    return json.dumps(line.model_dump(mode="json", by_alias=True, exclude_none=True), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


@dataclass(frozen=True)
class PlanDirectory:
    """One plan directory on this device (module docstring)."""

    path: Path

    @classmethod
    def at(cls, plan_dir: str | Path) -> PlanDirectory:
        return cls(Path(plan_dir))

    # ------------------------------------------------------------------------------------------- state

    def is_plan(self) -> bool:
        return (self.path / HISTORY_FILE).is_file()

    def is_empty(self, *, held: bool = False) -> bool:
        """No entry in the directory (or no directory); ``held``: under ``locked``, the lock file itself not counted."""

        if not self.path.exists():
            return True
        return self.path.is_dir() and not any(not (held and p.name == LOCK_FILE) for p in self.path.iterdir())

    def history(self, op_id: str | None = None, limit: int | None = None) -> list[HistoryLineV1]:
        """The journal lines (oldest first), optionally of one ``op_id`` and the last ``limit`` of them."""

        lines = self._history()
        if op_id is not None:
            lines = [line for line in lines if line.op_id == op_id]
        return lines if limit is None else lines[-limit:] if limit > 0 else []

    def _history(self) -> list[HistoryLineV1]:
        try:
            raw = (self.path / HISTORY_FILE).read_bytes()
        except OSError as exc:
            raise _inconsistent("the plan directory has no readable history.jsonl", [f"{HISTORY_FILE}: {exc.strerror or exc}"],
                                self.path) from None
        lines: list[HistoryLineV1] = []
        problems: list[str] = []
        for number, text in enumerate(raw.split(b"\n"), start=1):
            if not text.strip():
                continue
            try:
                lines.append(HistoryLineV1.model_validate(json.loads(text)))
            except (ValueError, ValidationError) as exc:
                problems.append(f"{HISTORY_FILE} line {number}: not a history line ({type(exc).__name__})")
        if problems:
            raise _inconsistent("the plan's history cannot be read", problems, self.path)
        if not lines:
            raise _inconsistent("the plan's history is empty", [f"{HISTORY_FILE}: no line"], self.path)
        return lines

    def head_number(self) -> int:
        return self._history()[-1].result_revision

    def revision_file(self, number: int) -> Path:
        return self.path / REVISIONS_DIR / revision_file_name(number)

    def revision(self, number: int | None = None) -> AssemblyPlanRevisionV1:
        """Revision ``number`` (default: the head); ``PLAN_DIR_INCONSISTENT`` when it is not a valid revision of the plan."""

        head = self.head_number()
        number = head if number is None else number
        if not 1 <= number <= head:
            raise AssemblyPlanError(AssemblyPlanErrorCode.PLAN_OP_INVALID, f"the plan has revisions 1 to {head}, not {number}",
                                    {"op_code": "REVISION_UNKNOWN", "op_index": -1, "head": head, "revision": number})
        return self._load_revision(number)

    def _load_revision(self, number: int) -> AssemblyPlanRevisionV1:
        path = self.revision_file(number)
        try:
            revision = AssemblyPlanRevisionV1.model_validate(json.loads(path.read_bytes()))
        except OSError as exc:
            raise _inconsistent(f"revision {number} cannot be read", [f"{REVISIONS_DIR}/{path.name}: {exc.strerror or exc}"],
                                self.path) from None
        except (ValueError, ValidationError) as exc:
            raise _inconsistent(f"revision {number} is not a valid revision",
                                [f"{REVISIONS_DIR}/{path.name}: {_first_error(exc)}"], self.path) from None
        if revision.revision != number:
            raise _inconsistent(f"revision file {path.name} holds revision {revision.revision}",
                                [f"{REVISIONS_DIR}/{path.name}: revision {revision.revision}"], self.path)
        return revision

    def project(self, project_hash: str) -> ProjectDocumentV2:
        path = self.path / PROJECTS_DIR / f"{project_hash}.json"
        try:
            project = ProjectDocumentV2.model_validate(json.loads(path.read_bytes()))
        except OSError as exc:
            raise _inconsistent(f"the Project {project_hash} the plan binds is missing",
                                [f"{PROJECTS_DIR}/{path.name}: {exc.strerror or exc}"], self.path) from None
        except (ValueError, ValidationError) as exc:
            raise _inconsistent(f"the Project {project_hash} the plan binds is not a Project",
                                [f"{PROJECTS_DIR}/{path.name}: {_first_error(exc)}"], self.path) from None
        if project.content_hash() != project_hash:
            raise _inconsistent(f"the Project file {path.name} does not hold that Project",
                                [f"{PROJECTS_DIR}/{path.name}: hash {project.content_hash()}"], self.path)
        return project

    def run_record(self, record_hash: str) -> LocalValidationRecordV1 | None:
        """The bound run's Local Validation record kept in the plan (None when the plan does not hold it)."""

        path = self.path / RUNS_DIR / f"{record_hash}.json"
        try:
            record = LocalValidationRecordV1.model_validate(json.loads(path.read_bytes()))
        except (OSError, ValueError, ValidationError):
            return None
        return record if record.record_hash() == record_hash else None

    def mechanical_binding(self, revision: AssemblyPlanRevisionV1) -> MechanicalPlanBinding:
        """What source resolution needs to reopen ``revision``'s mechanical documents (its refs and the plan's copies)."""

        from .mechanical.model import MechanicalDocumentRefV1

        refs = tuple(MechanicalDocumentRefV1(publisher=b.publisher.value, mechanical_version=b.mechanical_version, sha256=b.sha256)
                     for b in revision.bindings.mechanical)
        return MechanicalPlanBinding(refs=refs, user_copies_dir=self.path / MECHANICAL_DIR)

    # ------------------------------------------------------------------------------------------- verification

    def verify(self) -> list[str]:
        """Every inconsistency of the directory (module docstring); empty when it is consistent."""

        problems: list[str] = []
        try:
            lines = self._history()
        except AssemblyPlanError as exc:
            return exc.problems
        head = lines[-1].result_revision
        for seq, line in enumerate(lines, start=1):
            if line.seq != seq:
                problems.append(f"{HISTORY_FILE}: line {seq} has seq {line.seq}")
            if line.result_revision > head:
                problems.append(f"{HISTORY_FILE}: line {seq} names revision {line.result_revision} above the head {head}")
        parent: str | None = None
        revisions: dict[int, AssemblyPlanRevisionV1] = {}
        for number in range(1, head + 1):
            try:
                revision = self._load_revision(number)
            except AssemblyPlanError as exc:
                problems += exc.problems
                parent = None
                continue
            revisions[number] = revision
            if number > 1 and parent is not None and revision.parent_digest != parent:
                problems.append(f"revision {number}: parent_digest is not revision {number - 1}'s digest")
            parent = revision.content_digest
            try:
                self.project(revision.bindings.project.project_hash.digest)
            except AssemblyPlanError as exc:
                problems += [f"revision {number}: {p}" for p in exc.problems]
            for binding in revision.bindings.mechanical:
                if binding.publisher.value == "USER":
                    copy = self.path / MECHANICAL_DIR / f"{binding.sha256}.json"
                    if not copy.is_file() or hashlib.sha256(copy.read_bytes()).hexdigest() != binding.sha256:
                        problems.append(f"revision {number}: the USER mechanical document {binding.sha256} is not in {MECHANICAL_DIR}/")
        for line in lines:
            revision = revisions.get(line.result_revision)
            if revision is not None and revision.content_digest != line.result_digest:
                problems.append(f"{HISTORY_FILE}: line {line.seq} names a digest revision {line.result_revision} does not have")
        head_revision = revisions.get(head)
        if head_revision is not None:
            try:
                current = (self.path / PLAN_FILE).read_bytes()
            except OSError:
                current = b""
            if current != revision_bytes(head_revision):
                problems.append(f"{PLAN_FILE} is not the head revision {head}")
        return problems

    def require_consistent(self) -> None:
        problems = self.verify()
        if problems:
            raise _inconsistent("the plan directory is inconsistent", problems, self.path)

    # ------------------------------------------------------------------------------------------- writing

    def locked(self) -> PlanLock:
        """Hold the plan's lock in a ``with`` block (``PLAN_DIR_LOCKED`` when another write holds it)."""

        return PlanLock(self)

    def commit(self, revision: AssemblyPlanRevisionV1, project: ProjectDocumentV2, line: HistoryLineV1, *,
               user_documents: dict[str, bytes] | None = None, run_record: LocalValidationRecordV1 | None = None) -> None:
        """Write one revision (under ``locked``): bound files, the revision, the journal line, ``plan.json``."""

        project_path = self.path / PROJECTS_DIR / f"{project.content_hash()}.json"
        if not project_path.is_file():
            _replace(project_path, project_bytes(project))
        for sha256, data in (user_documents or {}).items():
            copy = self.path / MECHANICAL_DIR / f"{sha256}.json"
            if not copy.is_file():
                _replace(copy, data)
        if run_record is not None:
            record_path = self.path / RUNS_DIR / f"{run_record.record_hash()}.json"
            if not record_path.is_file():
                _replace(record_path, canonicalize_osc1_bytes(run_record.payload()))
        data = revision_bytes(revision)
        _replace(self.revision_file(revision.revision), data)
        history = self.path / HISTORY_FILE
        earlier = history.read_bytes() if history.is_file() else b""
        _replace(history, earlier + history_line_bytes(line))
        _replace(self.path / PLAN_FILE, data)

    def append_line(self, line: HistoryLineV1) -> None:
        """Add a journal line that writes no revision (under ``locked``)."""

        history = self.path / HISTORY_FILE
        _replace(history, history.read_bytes() + history_line_bytes(line))

    def stray_revisions(self) -> list[int]:
        """Revision files above the head (left by an interrupted write)."""

        head = self.head_number()
        folder = self.path / REVISIONS_DIR
        numbers = [parse_revision_file_name(p.name) for p in folder.iterdir()] if folder.is_dir() else []
        return sorted(n for n in numbers if n is not None and n > head)


class PlanLock:
    """The exclusive-create lock file of a plan directory, held for one write."""

    def __init__(self, directory: PlanDirectory) -> None:
        self.directory = directory
        self.path = directory.path / LOCK_FILE
        self.held = False

    def __enter__(self) -> PlanDirectory:
        self.directory.path.mkdir(parents=True, exist_ok=True)
        try:
            handle = self.path.open("xb")
        except FileExistsError:
            raise AssemblyPlanError(AssemblyPlanErrorCode.PLAN_DIR_LOCKED, "another os-optics command is writing this plan",
                                    {"plan_dir": str(self.directory.path), "lock": str(self.path), "recovery": LOCK_RECOVERY}) from None
        handle.close()
        self.held = True
        return self.directory

    def __exit__(self, *exc: object) -> None:
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False


def _first_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        if errors:
            first = errors[0]
            return f"{'.'.join(str(p) for p in first.get('loc', ()))}: {first.get('msg', '')}"[:ERROR_TEXT_MAX]
    return str(exc)[:ERROR_TEXT_MAX]


__all__ = ["PlanDirectory", "PlanLock", "history_line_bytes", "project_bytes", "revision_bytes"]
