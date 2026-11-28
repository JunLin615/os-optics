"""The Python API of local assembly plans (DESIGN 2.4): derive into a plan directory, reopen it, edit, check, preview,
draw, list its BOMs and bind an optics run. No token, no network: the mechanical documents come from ``--mechanical``
files, ``OS_OPTICS_MECHANICAL_CATALOG``, the plan's own copies or the local catalog store (``local.catalog``).

Every write goes through ``directory.PlanDirectory`` (lock, revision, journal); every revision written carries the check
made when it was written (``check.check_content``). An edit call is atomic and journaled: the same ``op_id`` with the same
ops is answered from the journal (``replayed``) and writes nothing, the same ``op_id`` with other ops is refused
(``OP_ID_REUSED``), an ``expected_base`` other than the head's plan digest is refused (``BASE_CHANGED``), and with
``require_check`` an edit that adds an ERROR or UNKNOWN finding is refused (``PLAN_CHECK_REFUSED``); a refusal writes
nothing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .._vendor.shared_contracts.common.hashes import osc1_digest
from .._vendor.shared_contracts.optical_design import LocalValidationRecordV1, ProjectDocumentV2
from .check import CheckOutcome, check_content
from .context import PlanContext, plan_context
from .contract import (AssemblyPlanError, AssemblyPlanErrorCode, AssemblyPlanRevisionV1, DeriveOptionsV1,
                       EditRequestV1, HistoryLineV1, OpResultV1, PlanCallResultV1, PlanCallSummaryV1, PlanOriginV1, content_payload)
from .directory import PlanDirectory
from .findings import blocking_new_findings, carry_derive_findings, findings_diff
from .mechanical import MechanicalCatalogError, MechanicalSources
from .mechanical.model import MechanicalPlanBinding

#: ``op_id`` of a call that names none: ``auto-`` + this many hex digits (unique in the journal).
AUTO_PREFIX = "auto-"
AUTO_ID_DIGITS = 32
#: The record file of a run directory (``project run --out-dir``).
RUN_RECORD_FILE = "local_validation.json"
LOCAL_VALIDATION_SUBJECT = "local_validation_record"
PROJECT_SUBJECT = "project"
#: What one derive budget defaults to (the public policy's bounds).
DEFAULT_TIME_LIMIT_S = None


@dataclass(frozen=True)
class PlanResult:
    """One derive / edit call: the revision it wrote (or the head, when replayed), its Project, the call result."""

    revision: AssemblyPlanRevisionV1
    project: ProjectDocumentV2
    result: PlanCallResultV1
    plan_dir: Path | None = None
    check: CheckOutcome | None = None

    @property
    def exit_state(self) -> str:
        return self.revision.status.state.value


# ------------------------------------------------------------------------------------------------ inputs


def _load_project(project: Any) -> ProjectDocumentV2:
    from ..local.catalog import load_project
    from ..local.errors import LocalRunError

    if isinstance(project, ProjectDocumentV2):
        return project
    try:
        return load_project(project)
    except LocalRunError as exc:
        raise AssemblyPlanError(AssemblyPlanErrorCode.PROJECT_REJECTED, "the Project cannot be read",
                                {"problems": [p.as_dict() for p in exc.problems]}) from None


def _load_catalog(catalog: Any, project: ProjectDocumentV2, version: str | None = None) -> Any:
    from ..local.catalog import load_public_catalog, pinned_catalog_version
    from ..local.errors import LocalRunError

    try:
        return load_public_catalog(catalog, catalog_version=version or pinned_catalog_version(project))
    except LocalRunError as exc:
        raise AssemblyPlanError(AssemblyPlanErrorCode.PROJECT_REJECTED, "the public optical catalog of the Project is not available",
                                {"problems": [p.as_dict() for p in exc.problems]}) from None


def resolve_mechanical(mechanical: Any = None, plan_binding: MechanicalPlanBinding | None = None) -> MechanicalSources:
    """The mechanical documents of a call: ``MechanicalSources`` as given; else files / bytes a caller names (``--mechanical``)
    resolved with the environment, the plan's binding and the store (``local.catalog.mechanical_sources``)."""

    from ..local.catalog import mechanical_sources

    if isinstance(mechanical, MechanicalSources):
        return mechanical
    given = tuple(mechanical or ())
    try:
        if given and all(isinstance(m, (bytes, bytearray)) for m in given):
            return MechanicalSources.of(*(bytes(m) for m in given))
        return mechanical_sources(tuple(str(m) for m in given), plan_binding)
    except MechanicalCatalogError as exc:
        code = exc.code.value if exc.code.value in AssemblyPlanErrorCode.__members__ else AssemblyPlanErrorCode.MECHANICAL_CATALOG_INVALID.value
        raise AssemblyPlanError(AssemblyPlanErrorCode(code), exc.message, {**dict(exc.details), "mechanical_code": exc.code.value}) from None


def _require(sources: MechanicalSources) -> MechanicalSources:
    if sources.empty:
        raise AssemblyPlanError(AssemblyPlanErrorCode.MECHANICAL_CATALOG_NOT_AVAILABLE,
                                "no mechanical catalog is available on this device: run 'os-optics catalog sync' or pass "
                                "--mechanical FILE", {"recovery": ["os-optics catalog sync", "--mechanical FILE"]})
    return sources


def read_run_record(run: Any) -> LocalValidationRecordV1 | None:
    """The Local Validation record of a run: a record model or document, a run directory or a record file."""

    if run is None or isinstance(run, LocalValidationRecordV1):
        return run
    try:
        if isinstance(run, Mapping):
            return LocalValidationRecordV1.model_validate(dict(run))
        path = Path(run)
        path = path / RUN_RECORD_FILE if path.is_dir() else path
        return LocalValidationRecordV1.model_validate(json.loads(path.read_bytes()))
    except (OSError, ValueError, ValidationError) as exc:
        raise AssemblyPlanError(AssemblyPlanErrorCode.PROJECT_REJECTED, f"not a readable run or Local Validation record: {run}",
                                {"run": str(run), "reason": type(exc).__name__}) from None


def run_binding(record: LocalValidationRecordV1) -> dict[str, Any]:
    return {"local_validation_hash": {"profile": "osc1", "digest": record.record_hash(), "subject": LOCAL_VALIDATION_SUBJECT},
            "project_hash": {"profile": "osc1", "digest": record.project.project_hash.digest, "subject": PROJECT_SUBJECT}}


def default_options(sources: MechanicalSources, **changes: Any) -> DeriveOptionsV1:
    policy = sources.policy
    budget = {"max_chain_placements": policy.max_chain_placements, "max_geometry_checks": policy.max_geometry_checks}
    time_limit = changes.pop("time_limit_s", DEFAULT_TIME_LIMIT_S)
    if time_limit is not None:
        budget["time_limit_s"] = float(time_limit)
    return DeriveOptionsV1.model_validate({"budget": budget, **{k: v for k, v in changes.items() if v is not None}})


def user_document_bytes(sources: MechanicalSources) -> dict[str, bytes]:
    """``{sha256: bytes}`` of the USER documents in use, for the plan's copies (a document whose bytes are not at hand is
    left out: the plan then needs ``--mechanical`` to reopen)."""

    from .mechanical.model import canonical_mechanical_bytes

    out: dict[str, bytes] = {}
    for doc in sources.users:
        candidates = []
        if doc.location:
            try:
                candidates.append(Path(doc.location).read_bytes())
            except OSError:
                pass
        candidates.append(canonical_mechanical_bytes(doc.document))
        for data in candidates:
            if hashlib.sha256(data).hexdigest() == doc.sha256:
                out[doc.sha256] = data
                break
    return out


def _build_id() -> tuple[str, str]:
    from .._version import ENGINE_VERSION
    from ..manifest.build import engine_build_id

    return ENGINE_VERSION, engine_build_id()


def _generated_op_id(lines: list[HistoryLineV1], request_digest: str | None, base: str | None) -> str:
    previous = lines[-1].op_id if lines else None
    taken = {line.op_id for line in lines}
    salt = 0
    while True:
        op_id = AUTO_PREFIX + osc1_digest({"previous": previous, "count": len(lines), "request_digest": request_digest,
                                           "base": base, "salt": salt})[:AUTO_ID_DIGITS]
        if op_id not in taken:
            return op_id
        salt += 1


def _call_result(plan_dir: Path, revision: AssemblyPlanRevisionV1, *, op_id: str, base: AssemblyPlanRevisionV1 | None,
                 ops: list[OpResultV1], project_changed: bool, run_freshness: dict[str, Any] | None,
                 replayed: bool = False) -> PlanCallResultV1:
    added, resolved = findings_diff(() if base is None else base.findings, revision.findings)
    return PlanCallResultV1(ok=True, plan_dir=str(plan_dir), plan_id=revision.plan_id, revision=revision.revision,
                            content_digest=revision.content_digest, base_digest=None if base is None else base.content_digest,
                            replayed=replayed, op_id=op_id, ops=tuple(ops), project_hash=revision.bindings.project.project_hash.digest,
                            project_changed=project_changed, run_freshness=run_freshness, findings_added=added,
                            findings_resolved=resolved, status=revision.status, findings=revision.findings)


def _line(lines: list[HistoryLineV1], kind: PlanOriginV1, op_id: str, request_digest: str | None, base: AssemblyPlanRevisionV1 | None,
          revision: AssemblyPlanRevisionV1, result: PlanCallResultV1) -> HistoryLineV1:
    version, build = _build_id()
    summary = PlanCallSummaryV1.model_validate(result.model_dump(mode="json", exclude={"findings"}))
    return HistoryLineV1(seq=len(lines) + 1, op_id=op_id, kind=kind, request_digest=request_digest,
                         base_revision=None if base is None else base.revision, base_digest=None if base is None else base.content_digest,
                         result_revision=revision.revision, result_digest=revision.content_digest,
                         project_hash=revision.bindings.project.project_hash.digest, result=summary, engine_version=version,
                         engine_build_id=build)


# ------------------------------------------------------------------------------------------------ derive


def derive_plan(project: Any, *, out_dir: str | Path | None = None, run: Any = None, from_plan: str | Path | None = None,
                mechanical: Any = None, catalog: Any = None, options: DeriveOptionsV1 | Mapping[str, Any] | None = None,
                plan_id: str | None = None, op_id: str | None = None) -> PlanResult:
    """Derive a plan of ``project`` (DESIGN 2.3) and write it: revision 1 of a new plan directory ``out_dir`` (absent or
    empty, else ``PLAN_DIR_NOT_EMPTY``), or with ``from_plan`` a REDERIVE revision of that plan (ids, supply, purchase and
    pins kept; into ``from_plan`` itself when ``out_dir`` is None or the same directory). Without a directory the plan is
    derived and checked in memory only (``plan_dir`` None)."""

    from .derive import derive_plan_content

    document = _load_project(project)
    base_dir = None if from_plan is None else open_plan(from_plan)
    base = None if base_dir is None else base_dir.head
    target = Path(out_dir) if out_dir is not None else (None if base_dir is None else base_dir.path)
    rederive = base_dir is not None and target is not None and target.resolve() == base_dir.path.resolve()
    if target is not None and not rederive and not PlanDirectory.at(target).is_empty():
        raise AssemblyPlanError(AssemblyPlanErrorCode.PLAN_DIR_NOT_EMPTY, f"{target} is not empty: give a new directory "
                                "(or --from PLAN_DIR with --out the same directory to derive it again)", {"plan_dir": str(target)})
    binding = None if base_dir is None else base_dir.directory.mechanical_binding(base)  # type: ignore[arg-type]
    sources = _require(resolve_mechanical(mechanical, binding if mechanical is None else None))
    optical = _load_catalog(catalog, document)
    if options is None:
        derive_options = default_options(sources)
    elif isinstance(options, DeriveOptionsV1):
        derive_options = options
    else:
        changes = dict(options)
        derive_options = (DeriveOptionsV1.model_validate(changes) if "budget" in changes else default_options(sources, **changes))
    record = read_run_record(run)
    revision_number = 1 if not rederive else base.revision + 1  # type: ignore[union-attr]
    parent = None if not rederive else base.content_digest  # type: ignore[union-attr]
    try:
        outcome = derive_plan_content(document, optical, sources, derive_options, from_revision=base,
                                      run=None if record is None else run_binding(record), plan_id=plan_id,
                                      revision=revision_number, parent_digest=parent)
    except (KeyError, ValueError) as exc:
        raise AssemblyPlanError(AssemblyPlanErrorCode.PROJECT_REJECTED, f"the Project cannot be planned: {exc}"[:MAX_TEXT]) from None
    content = outcome.content
    context = plan_context(document, optical, sources, local_validation=record)
    check = check_content(content, context, derive_findings=outcome.derive_findings)
    revision = check.revision(content)
    if target is None:
        result = _call_result(Path("."), revision, op_id=op_id or "in-memory", base=None, ops=[], project_changed=False,
                              run_freshness=check.freshness)
        return PlanResult(revision=revision, project=document, result=result, plan_dir=None, check=check)
    directory = PlanDirectory.at(target)
    with directory.locked():
        lines = directory.history() if directory.is_plan() else []
        request = osc1_digest({"project_hash": document.content_hash(), "options": derive_options.model_dump(mode="json"),
                               "mechanical": [r.model_dump(mode="json") for r in sources.binding()]})
        call_id = op_id or _generated_op_id(lines, request, None if not rederive else parent)
        kind = revision.origin
        result = _call_result(target, revision, op_id=call_id, base=base if rederive else None, ops=[], project_changed=False,
                              run_freshness=check.freshness)
        line = _line(lines, kind, call_id, request, base if rederive else None, revision, result)
        directory.commit(revision, document, line, user_documents=user_document_bytes(sources), run_record=record)
    return PlanResult(revision=revision, project=document, result=result, plan_dir=target, check=check)


# ------------------------------------------------------------------------------------------------ an open plan


@dataclass(frozen=True)
class OpenPlan:
    """A verified plan directory (``open_plan``)."""

    directory: PlanDirectory
    catalog: Any = None
    mechanical: Any = None

    @property
    def path(self) -> Path:
        return self.directory.path

    @property
    def head(self) -> AssemblyPlanRevisionV1:
        return self.directory.revision()

    def revision(self, number: int | None = None) -> AssemblyPlanRevisionV1:
        return self.directory.revision(number)

    def history(self, op_id: str | None = None, limit: int | None = None) -> list[HistoryLineV1]:
        return self.directory.history(op_id, limit)

    def verify(self) -> list[str]:
        return self.directory.verify()

    def project(self, revision: AssemblyPlanRevisionV1 | None = None) -> ProjectDocumentV2:
        revision = revision or self.head
        return self.directory.project(revision.bindings.project.project_hash.digest)

    def sources(self, revision: AssemblyPlanRevisionV1, mechanical: Any = None) -> MechanicalSources:
        binding = self.directory.mechanical_binding(revision)
        return _require(resolve_mechanical(mechanical or self.mechanical or None, binding))

    def context(self, revision: AssemblyPlanRevisionV1 | None = None, *, mechanical: Any = None) -> PlanContext:
        """The check / output context of ``revision`` (default the head): its Project, optical catalog, mechanical
        documents, bound run record and design BOM."""

        revision = revision or self.head
        project = self.project(revision)
        optical = _load_catalog(self.catalog, project, revision.bindings.optical_catalog.catalog_version)
        bound = revision.bindings.optics_run
        record = None if bound is None else self.directory.run_record(bound.local_validation_hash.digest)
        return plan_context(project, optical, self.sources(revision, mechanical), local_validation=record, revision=revision)

    def check(self, revision: int | None = None, *, mechanical: Any = None, run: Any = None, tolerances: Any = None) -> CheckOutcome:
        """The check of a revision with this Engine and the documents supplied now (nothing written)."""

        target = self.revision(revision)
        context = self.context(target, mechanical=mechanical)
        carried = [f for f in target.findings if f.source.value == "DERIVE"]
        return check_content(target, context, derive_findings=carried, run=run, tolerances=tolerances)

    def edit(self, ops: Iterable[Any], *, op_id: str | None = None, expected_base: str | None = None,
             require_check: bool = False, mechanical: Any = None) -> PlanResult:
        """Apply ``ops`` as one new revision (module docstring)."""

        try:
            request = EditRequestV1.model_validate({"ops": [o if isinstance(o, dict) else o.model_dump(mode="json") for o in ops],
                                                    "op_id": op_id, "expected_base": expected_base, "require_check": require_check})
        except ValidationError as exc:
            raise op_validation_error(exc) from None
        return self._edit(request, mechanical)

    def _edit(self, request: EditRequestV1, mechanical: Any) -> PlanResult:
        from .ops import apply_ops

        digest = request.request_digest()
        with self.directory.locked():
            lines = self.directory.history()
            head = self.directory.revision()
            if request.op_id is not None:
                recorded = [line for line in lines if line.op_id == request.op_id]
                if recorded:
                    line = recorded[-1]
                    if line.request_digest == digest:
                        answered = self.directory.revision(line.result_revision)
                        result = PlanCallResultV1.model_validate({**line.result.model_dump(mode="json"), "replayed": True,
                                                                  "findings": [f.model_dump(mode="json") for f in answered.findings]})
                        return PlanResult(revision=answered, project=self.project(answered), result=result, plan_dir=self.path)
                    raise AssemblyPlanError(AssemblyPlanErrorCode.OP_ID_REUSED, f"op_id {request.op_id!r} was already used for "
                                            "other ops; use a new op_id", {"op_id": request.op_id})
            if request.expected_base is not None and request.expected_base != head.content_digest:
                raise AssemblyPlanError(AssemblyPlanErrorCode.BASE_CHANGED, "the plan changed since the edit was written; read it "
                                        "again", {"head": head.content_digest, "expected_base": request.expected_base,
                                                  "revision": head.revision})
            context = self.context(head, mechanical=mechanical)
            project = context.project

            def revisions(number: int) -> tuple[AssemblyPlanRevisionV1, ProjectDocumentV2]:
                target = self.directory.revision(number)
                return target, self.project(target)

            outcome = apply_ops(head, project, request.ops, context, revisions=revisions)
            content = dict(outcome.content)
            content.update(revision=head.revision + 1, parent_digest=head.content_digest, origin=outcome.origin.value)
            edited = context.with_project(outcome.project) if outcome.project_changed or outcome.project.content_hash() != project.content_hash() else context
            carried = list(carry_derive_findings(head.findings, outcome.touched, outcome.retired)) + list(outcome.derive_findings)
            try:
                check = check_content(content_payload(content), edited, derive_findings=carried)
            except ValueError as exc:
                raise AssemblyPlanError(AssemblyPlanErrorCode.PLAN_OP_INVALID, f"the ops leave an invalid plan: {exc}"[:MAX_TEXT],
                                        {"op_index": len(request.ops) - 1, "op_code": "OP_MALFORMED"}) from None
            if request.require_check:
                blocking = blocking_new_findings(head.findings, check.findings)
                if blocking:
                    raise AssemblyPlanError(AssemblyPlanErrorCode.PLAN_CHECK_REFUSED,
                                            "the edit adds ERROR or UNKNOWN findings (require_check); nothing was written",
                                            {"findings": [f.model_dump(mode="json") for f in blocking]})
            revision = check.revision(content)
            call_id = request.op_id or _generated_op_id(lines, digest, head.content_digest)
            result = _call_result(self.path, revision, op_id=call_id, base=head, ops=outcome.results,
                                  project_changed=outcome.project_changed, run_freshness=check.freshness if outcome.project_changed else None)
            line = _line(lines, outcome.origin, call_id, digest, head, revision, result)
            self.directory.commit(revision, outcome.project, line, user_documents=user_document_bytes(edited.mechanical))
        return PlanResult(revision=revision, project=outcome.project, result=result, plan_dir=self.path, check=check)

    def bind_run(self, run: Any, *, op_id: str | None = None, mechanical: Any = None) -> PlanResult:
        """A REBIND revision binding the optics run ``run`` (a run directory, record file or record) to the head."""

        record = read_run_record(run)
        assert record is not None
        with self.directory.locked():
            lines = self.directory.history()
            head = self.directory.revision()
            context = self.context(head, mechanical=mechanical)
            payload = head.content()
            payload["bindings"] = {**payload["bindings"], "optics_run": run_binding(record)}
            payload.update(revision=head.revision + 1, parent_digest=head.content_digest, origin=PlanOriginV1.REBIND.value)
            from dataclasses import replace

            context = replace(context, local_validation=record)
            carried = [f for f in head.findings if f.source.value == "DERIVE"]
            check = check_content(payload, context, derive_findings=carried)
            revision = check.revision(payload)
            digest = osc1_digest({"bind_run": record.record_hash()})
            call_id = op_id or _generated_op_id(lines, digest, head.content_digest)
            result = _call_result(self.path, revision, op_id=call_id, base=head, ops=[], project_changed=False,
                                  run_freshness=check.freshness)
            line = _line(lines, PlanOriginV1.REBIND, call_id, digest, head, revision, result)
            self.directory.commit(revision, context.project, line, run_record=record)
        return PlanResult(revision=revision, project=context.project, result=result, plan_dir=self.path, check=check)


def open_plan(plan_dir: str | Path, *, catalog: Any = None, mechanical: Any = None, verify: bool = True) -> OpenPlan:
    """A plan directory, verified (``PLAN_DIR_INCONSISTENT`` with the problems when it is not consistent). ``catalog`` /
    ``mechanical``: the optical catalog and mechanical documents its calls use when they name none (default: the
    releases the plan binds, from the local store and the plan's own copies)."""

    directory = PlanDirectory.at(plan_dir)
    if not directory.is_plan():
        raise AssemblyPlanError(AssemblyPlanErrorCode.PLAN_DIR_INCONSISTENT, f"{plan_dir} is not a plan directory (no history.jsonl)",
                                {"plan_dir": str(plan_dir)}, ["history.jsonl: missing"])
    if verify:
        directory.require_consistent()
    return OpenPlan(directory=directory, catalog=catalog, mechanical=mechanical)


def op_validation_error(exc: ValidationError) -> AssemblyPlanError:
    errors = exc.errors()
    first = errors[0] if errors else {}
    loc = list(first.get("loc", ()))
    index = loc[1] if len(loc) > 1 and loc[0] == "ops" and isinstance(loc[1], int) else -1
    return AssemblyPlanError(AssemblyPlanErrorCode.PLAN_OP_INVALID, f"the edit is not valid: {first.get('msg', exc)}"[:MAX_TEXT],
                             {"op_index": index, "op_code": "OP_MALFORMED",
                              "location": ".".join(str(p) for p in loc)})


def apply_plan_ops(revision: AssemblyPlanRevisionV1, project: ProjectDocumentV2, ops: Iterable[Any], *, context: PlanContext,
                   require_check: bool = False) -> tuple[AssemblyPlanRevisionV1, ProjectDocumentV2, list[OpResultV1], CheckOutcome]:
    """The pure form of an edit (the JSON API's): the next revision of ``revision``, its Project, the op results and the
    check. Nothing is written; ``revert_to`` is not available without the plan directory."""

    from .ops import apply_ops

    try:
        request = EditRequestV1.model_validate({"ops": [o if isinstance(o, dict) else o.model_dump(mode="json") for o in ops],
                                                "require_check": require_check})
    except ValidationError as exc:
        raise op_validation_error(exc) from None
    outcome = apply_ops(revision, project, request.ops, context)
    content = dict(outcome.content)
    content.update(revision=revision.revision + 1, parent_digest=revision.content_digest, origin=outcome.origin.value)
    edited = context.with_project(outcome.project) if outcome.project_changed else context
    carried = list(carry_derive_findings(revision.findings, outcome.touched, outcome.retired)) + list(outcome.derive_findings)
    check = check_content(content_payload(content), edited, derive_findings=carried)
    if require_check:
        blocking = blocking_new_findings(revision.findings, check.findings)
        if blocking:
            raise AssemblyPlanError(AssemblyPlanErrorCode.PLAN_CHECK_REFUSED, "the edit adds ERROR or UNKNOWN findings (require_check)",
                                    {"findings": [f.model_dump(mode="json") for f in blocking]})
    return check.revision(content), outcome.project, outcome.results, check


def open_plan_view(plan_dir: str | Path, *, revision: int | None = None,
                   mechanical: Any = ()) -> tuple[AssemblyPlanRevisionV1, PlanContext, tuple[Any, ...]]:
    """``(revision, context, findings)`` of a plan directory for the viewer (``os-optics view PLAN_DIR``): the revision
    (default the head), its projection context and its findings recomputed with this Engine."""

    plan = open_plan(plan_dir)
    target = plan.revision(revision)
    context = plan.context(target, mechanical=mechanical or None)
    carried = [f for f in target.findings if f.source.value == "DERIVE"]
    check = check_content(target, context, derive_findings=carried)
    return check.revision(target), context.for_revision(target), check.findings


def _target(plan_or_dir: Any, revision: int | None) -> tuple[OpenPlan, AssemblyPlanRevisionV1]:
    plan = plan_or_dir if isinstance(plan_or_dir, OpenPlan) else open_plan(plan_or_dir)
    return plan, plan.revision(revision)


def check_plan(plan_or_dir: Any, *, revision: int | None = None, mechanical: Any = None, run: Any = None,
               tolerances: Any = None) -> CheckOutcome:
    """The check of a plan revision (default the head) with this Engine; nothing is written."""

    plan, target = _target(plan_or_dir, revision)
    return plan.check(target.revision, mechanical=mechanical, run=run, tolerances=tolerances)


def plan_preview(plan_or_dir: Any, *, revision: int | None = None, out: str | Path | None = None, as_directory: bool = False,
                 mechanical: Any = None) -> Any:
    """The LOCAL_PLAN Assembly Preview Package of a revision (``PLAN_USER_HARDWARE_NOT_PROJECTABLE`` for a plan holding the
    user's own hardware); with ``out`` also written there (the archive, or the package files with ``as_directory``)."""

    from ..output_files import replace_files
    from .projection import project_plan_package

    plan, target = _target(plan_or_dir, revision)
    package = project_plan_package(target, plan.context(target, mechanical=mechanical))
    if out is not None:
        if as_directory:
            replace_files(out, dict(package.files))
        else:
            replace_files(Path(out).parent, {Path(out).name: package.archive_bytes()})
    return package


def plan_drawing(plan_or_dir: Any, out_dir: str | Path | None = None, *, locale: str = "en", diagnostic: bool = False,
                 revision: int | None = None, mechanical: Any = None) -> Any:
    """The assembly drawing of a revision (``drawing.plan_drawing``), judged by the findings this Engine computes now."""

    from .drawing import plan_drawing as draw

    plan, target = _target(plan_or_dir, revision)
    context = plan.context(target, mechanical=mechanical)
    carried = [f for f in target.findings if f.source.value == "DERIVE"]
    check = check_content(target, context, derive_findings=carried)
    return draw(target, out_dir, context=context, locale=locale, diagnostic=diagnostic, findings=check.findings,
                validation=check.validation)


def design_bom(plan_or_dir: Any, *, revision: int | None = None) -> dict[str, Any]:
    """The design BOM of a revision (``bom.design_bom``)."""

    from .bom import design_bom as build

    if isinstance(plan_or_dir, AssemblyPlanRevisionV1):
        return build(plan_or_dir)
    return build(_target(plan_or_dir, revision)[1])


def purchase_bom(plan_or_dir: Any, *, revision: int | None = None, schema_version: str = "2") -> tuple[dict[str, Any], dict[str, Any]]:
    """``(body, report)``: the purchase BOM of a revision (``bom.purchase_bom``)."""

    from .bom import purchase_bom as build

    if isinstance(plan_or_dir, AssemblyPlanRevisionV1):
        return build(plan_or_dir, schema_version=schema_version)
    return build(_target(plan_or_dir, revision)[1], schema_version=schema_version)


#: Characters of a refusal text kept.
MAX_TEXT = 400

__all__ = ["OpenPlan", "PlanResult", "apply_plan_ops", "check_plan", "default_options", "derive_plan", "design_bom", "open_plan",
           "open_plan_view", "plan_drawing", "plan_preview", "purchase_bom", "read_run_record", "resolve_mechanical"]
