"""Ordered assembly steps, adjustment rows and customer-supplied rows, from the message catalog only.

Every step is generated from the preview scene's own facts: ``assembly_order``, the support relation of each part
(``STANDS_ON`` / ``INSERTED_INTO`` / ``MOUNTED_ON`` / ``SEATED_IN`` / ``FASTENED_TO``) and the interface designation of
its mate, the part's settings (an insertion depth as MEASURED from the poses by its detail dimension, an angle or a
designation as set; each with its basis), its fixation (mode, board holes, fastener), manual operations, the delivered
DOFs, and the dimensions of the Assembly Detail Package measured from the poses. Parts are named by their Customer
Purchase BOM line reference and OpenSci identity only; the wording is a catalog template per relation, in the drawing's
locale (``i18n``). Lengths are printed to 0.1 mm and angles to 0.1 deg (``text.mm`` / ``text.deg``); an angle names
its reference (a rotation about the vertical axis, measured from board +X). The step of an optic whose two faces
differ (a plano-convex lens, an achromat: ``PreviewPartV1.faces``) says which face meets the incoming beam, from the
optic's pose and the direction of the beam axis that names it.

Board holes are numbered for people: (column, row) from (1, 1), the hole at the grid origin, columns along +X and rows
along +Y (the sheet's notes state it). The scene's fixations name zero-based grid indices; ``hole_label`` converts.

With several optic stacks (support chains) the steps are grouped stack by stack (``Step.chain``) and worded compactly:
the dimension table and the views carry every dimension, so a step repeats only its optic's placement and every nominal
value. One stack keeps the single-stack order and wording.

Customer-supplied design components (the customer's light source, or an optical component OpenSci does not supply) are
placed last, one ``PLACE_CUSTOMER_SUPPLIED`` step each (``customer_steps``): where it goes (the assembly-world point,
located from the board corner and the board top) and which way it faces; its support is the customer's own. A
customer-supplied catalog component is "your own <role> (modelled as <product> <SKU>)": the product is only the model
the design used. ``customer_rows`` are the rows of the sheet's table and of the drawing result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .._vendor.shared_contracts.assembly_detail import DimensionBasisV1, DimensionKindV1, MeasureAxisV1
from .._vendor.shared_contracts.assembly_preview.scene import (
    DeliveredControlV1,
    DofDeliveryStatusV1,
    FixationModeV1,
    SettingKindV1,
    SupportRelationV1,
)
from .i18n import CATALOG
from .model import RELATION_ACTION, DrawingFacts
from .text import deg, mm, value_text

MAX_STEP_TEXT = 600
#: What a step text may not hold (the contract's StepText: no control character and no markup bracket).
_UNSAFE = re.compile(r"[\x00-\x1f\x7f<>]")
ROTATION_AXES = frozenset({"Rx", "Ry", "Rz"})
AXIS_LETTER = {MeasureAxisV1.WORLD_X: "X", MeasureAxisV1.WORLD_Y: "Y", MeasureAxisV1.WORLD_Z: "Z"}
#: A customer-supplied component is "outside the board" when its design position is more than this beyond an edge.
OFF_BOARD_MM = 0.5
#: A direction component this close to 0 or +-1 is printed as that value (rounding noise of the poses).
AXIS_SNAP = 1e-9


def safe(text: str) -> str:
    """A package identifier made fit for the step alphabet (control characters and markup brackets become spaces)."""

    return _UNSAFE.sub(" ", text)


def world_axis(direction: tuple[float, float, float]) -> str:
    for index, name in enumerate("XYZ"):
        if direction[index] in (1.0, -1.0) and all(direction[k] == 0.0 for k in range(3) if k != index):
            return ("+" if direction[index] > 0 else "-") + name
    return "(" + ", ".join(value_text(c) for c in direction) + ")"


def hole_label(i: int, j: int) -> str:
    """A board hole as people count it: (column, row) from (1, 1)."""

    return f"({i + 1}, {j + 1})"


@dataclass(frozen=True)
class Step:
    action: str
    part_ref: str | None
    line_ref: str | None
    text: str
    chain: int = 0  # the optic stack (support chain, 1..n) the step belongs to; 0 = the breadboard / no stack
    design_instance_id: str | None = None  # a PLACE_CUSTOMER_SUPPLIED step: the customer-supplied design component


class Words:
    """The catalog in one locale; on the customer's own optical table (``table``) a key's ``<key>_table`` wording is used
    where the catalog has one (no "breadboard" / "board" for a drawing without one); for the drawing of a local assembly
    plan (``local``) a key's ``<key>_local`` wording comes first ("your design", design BOM lines)."""

    def __init__(self, locale: str, *, table: bool = False, local: bool = False) -> None:
        self.locale = locale
        self.table = table
        self.local = local

    def _key(self, key: str) -> str:
        if self.local and CATALOG.has(f"{key}_local"):
            return f"{key}_local"
        return f"{key}_table" if self.table and CATALOG.has(f"{key}_table") else key

    def __call__(self, key: str, **values: object) -> str:
        return CATALOG.text(self.locale, self._key(key), **values)

    def enum(self, prefix: str, value: str) -> str:
        key = self._key(f"{prefix}.{value}")
        return CATALOG.text(self.locale, key) if CATALOG.has(key) else CATALOG.enum(self.locale, prefix, value)


def words_for(facts, locale: str) -> Words:  # noqa: ANN001 - model.DrawingFacts
    """The words of one drawing: on the customer's own table, and for a local assembly plan's package, their variants."""

    return Words(locale, table=facts.board.customer_table, local=facts.preview.manifest.binding.local)


@dataclass(frozen=True)
class CustomerRow:
    """One customer-supplied design component of the sheet (the customer-supplied components table)."""

    instance_id: str
    role: str
    reason: str
    sku: str | None
    model_name: str | None  # the catalog product's name (in the drawing's locale) the design used as its model
    model_name_en: str | None
    position: tuple[float, float, float]
    design_position: tuple[float, float, float]
    axis: tuple[float, float, float]
    reserved: bool
    envelope: tuple[float, float, float] | None  # (diameter, z_min, z_max) of the reserved envelope

    def result(self) -> dict:
        return {"design_instance_id": self.instance_id, "optical_role": self.role, "reason": self.reason,
                **({"opensci_sku": self.sku} if self.sku else {}),
                **({"model_display_name": self.model_name_en} if self.model_name_en else {}),
                "position_mm": [float(value_text(v)) for v in self.position],
                "design_position_mm": [float(value_text(v)) for v in self.design_position],
                "axis_world": [float(value_text(v)) for v in self.axis], "reserved_envelope": self.reserved}


def _qrotate(q: tuple[float, ...], v: tuple[float, float, float]) -> tuple[float, float, float]:
    w, x, y, z = q
    tx, ty, tz = 2 * (y * v[2] - z * v[1]), 2 * (z * v[0] - x * v[2]), 2 * (x * v[1] - y * v[0])
    return (v[0] + w * tx + (y * tz - z * ty), v[1] + w * ty + (z * tx - x * tz), v[2] + w * tz + (x * ty - y * tx))


#: The anchor reason of an optic OpenSci supplies without mounting hardware: the customer mounts it.
UNMOUNTED_REASON = "OPENSCI_SUPPLIED_UNMOUNTED"


def customer_rows(facts: DrawingFacts, locale: str = "en") -> list[CustomerRow]:
    """Every customer-supplied design anchor of the scene, by instance id."""

    reference = facts.scene.beam_reference
    frame = reference.design_frame_in_world
    inverse = (frame.quaternion_wxyz[0], -frame.quaternion_wxyz[1], -frame.quaternion_wxyz[2], -frame.quaternion_wxyz[3])
    models = {m.instance_id: m for m in facts.detail.detail.customer_supplied_models}
    rows = []
    for anchor in sorted(reference.design_anchors, key=lambda a: a.instance_id):
        supply = anchor.customer_supply
        if not anchor.customer_supplied or supply is None:
            continue
        position = tuple(anchor.position_mm)
        local = _qrotate(inverse, tuple(p - o for p, o in zip(position, frame.position_mm, strict=True)))  # type: ignore[arg-type]
        axis = _qrotate(tuple(supply.orientation_wxyz), (0.0, 0.0, 1.0))
        model = models.get(anchor.instance_id)
        name = None
        if model is not None and model.opensci_sku == supply.opensci_sku:
            name = model.display_name_zh_cn if locale == "zh-CN" and model.display_name_zh_cn else model.display_name
        envelope = supply.reserved_envelope
        rows.append(CustomerRow(anchor.instance_id, supply.optical_role, supply.reason.value, supply.opensci_sku, name,
                                model.display_name if model is not None and name is not None else None,
                                position, local, axis, envelope is not None,  # type: ignore[arg-type]
                                (envelope.diameter_mm, envelope.z_min_mm, envelope.z_max_mm) if envelope is not None else None))
    return rows


def customer_what(w: Words, row: CustomerRow, *, with_id: bool = False) -> str:
    """"your own <role>" -- with "(modelled as <product> <SKU>)" for a customer-supplied catalog component; "the <role>
    supplied unmounted (<product> <SKU>)" for an optic OpenSci supplies without mounting hardware; the
    design instance id after the role when ``with_id``."""

    role = w.enum("role", row.role)
    suffix = "_id" if with_id else ""
    ident = {"id": safe(row.instance_id)} if with_id else {}
    if row.reason == UNMOUNTED_REASON:
        if row.model_name:
            return w("customer.unmounted" + suffix, role=role, name=row.model_name, sku=row.sku, **ident)
        return w("customer.unmounted_sku" + suffix, role=role, sku=row.sku, **ident)
    if row.sku and row.model_name:
        return w("customer.modelled" + suffix, role=role, name=row.model_name, sku=row.sku, **ident)
    if row.sku:
        return w("customer.modelled_sku" + suffix, role=role, sku=row.sku, **ident)
    return w("customer.what" + suffix, role=role, **ident)


def located(facts: DrawingFacts, p: tuple[float, float, float]) -> tuple[str, str, str]:
    """(from the -X edge, from the -Y edge, above the top) of a world point, printed with their unit."""

    board = facts.board
    return (f"{mm(p[0] - board.x0)} mm", f"{mm(p[1] - board.y0)} mm", f"{mm(p[2] - board.top_z)} mm")


def outside_board(facts: DrawingFacts, p: tuple[float, float, float]) -> tuple[str, float] | None:
    """(edge, distance beyond it) when ``p`` lies outside the board outline in plan (the farthest edge), else None."""

    board = facts.board
    beyond = [("-X", board.x0 - p[0]), ("+X", p[0] - board.x1), ("-Y", board.y0 - p[1]), ("+Y", p[1] - board.y1)]
    edge, distance = max(beyond, key=lambda e: e[1])
    return (edge, distance) if distance > OFF_BOARD_MM else None


def _point(p: tuple[float, float, float]) -> dict[str, str]:
    return {"x": mm(p[0]), "y": mm(p[1]), "z": mm(p[2])}


def customer_steps(facts: DrawingFacts, w: Words) -> list[Step]:
    """One PLACE_CUSTOMER_SUPPLIED step per customer-supplied component (after every OpenSci part)."""

    out = []
    table = facts.board.customer_table
    for row in customer_rows(facts, w.locale):
        dx, dy, h = located(facts, row.position)
        off = outside_board(facts, row.position)
        if off is not None:  # outside the board: only how far beyond which edge, and the height
            where = w("step.place_customer_outside", d=mm(off[1]), edge=off[0], h=h)
        else:
            where = w("step.place_customer_located_table" if table else "step.place_customer_located", dx=dx, dy=dy, h=h)
        text = w("step.place_customer", what=customer_what(w, row, with_id=True), **_point(row.position), located=where,
                 facing=w("step.facing_source" if row.role == "source" else "step.facing_axis"), axis=world_axis(row.axis),
                 reserved=w("step.reserved") if row.reserved else "")
        out.append(Step("PLACE_CUSTOMER_SUPPLIED", None, None, text, 0, row.instance_id))
    return out


def fastener_text(w: Words, note) -> str:  # noqa: ANN001 - the vendored FastenerNoteV1
    """The fastener of a fixing: standard screws supplied with the order, or a parts-legend line."""

    if note.on_commercial_bom:
        return w("fastener.on_bom", thread=note.thread_designation) if note.thread_designation else w("fastener.on_bom_plain")
    return w("fastener.supplied", thread=note.thread_designation) if note.thread_designation else w("fastener.supplied_plain")


def _setting_text(setting, facts: DrawingFacts, w: Words, *, compact: bool = False) -> str:  # noqa: ANN001 - SettingV1
    """A setting clause. A length (INSERTION_DEPTH) prints the value MEASURED from the preview poses by its Assembly
    Detail dimension (the contract requires exactly one per setting and that it equals the setting), never the stored
    setting value; an angle or a designation prints the setting value with its basis (``compact``: a resolved basis
    is left unsaid, a nominal value is always said)."""

    kind = setting.kind.value
    if setting.kind is SettingKindV1.INSERTION_DEPTH:
        shown = [d for d in facts.detail.detail.dimensions if d.kind is DimensionKindV1.INSERTION_DEPTH
                 and d.setting is not None and d.setting.part_ref == setting.part_ref]
        if len(shown) != 1:
            raise ValueError(f"the INSERTION_DEPTH setting of {setting.part_ref!r} is not shown by exactly one dimension")
        dim = shown[0]
        value = mm(abs(facts.detail.measured[dim.dim_ref].value_mm))
        return w(f"step.setting.{kind}", v=value, basis=w.enum("basis_words", dim.basis.value))
    if setting.unit == "mm":
        raise ValueError(f"a length setting of {setting.part_ref!r} has no dimension measured from the poses")
    value = safe(setting.value) if setting.unit == "NONE" else deg(float(setting.value))
    nominal = setting.basis.value == "NOMINAL"
    if compact and not nominal:
        return w(f"step.setting_compact.{kind}", v=value)
    return w(f"step.setting.{kind}", v=value, basis=w.enum("basis_words", "NOMINAL" if nominal else "RESOLVED"))


def _fixation_clauses(facts: DrawingFacts, fixation, w: Words) -> tuple[list[str], bool]:  # noqa: ANN001
    """The fixing clauses of a part standing on the board, and whether a fastener clause was given."""

    if fixation is None:
        return [w("step.no_fixing")], False
    if fixation.mode is FixationModeV1.MANUAL_BASE_FIXATION:
        clauses = [w("step.fix_manual", op=fixation.manual_operation_ref)]
    else:
        holes = ", ".join(hole_label(i, j) for i, j in fixation.board_holes)
        where = ""
        if fixation.board_holes and facts.board.grid_origin is not None:
            hx, hy = facts.board.hole_xy(*fixation.board_holes[0])
            where = w("step.fix_where", x=mm(hx), y=mm(hy))
        clauses = [w("step.fix_hole", mode=w.enum("fix_mode", fixation.mode.value), holes=holes, where=where)]
    if fixation.fastener is None:
        return clauses, False
    note = fixation.fastener
    short = w("fastener.step") if not note.on_commercial_bom else fastener_text(w, note)
    clauses.append(w("step.fastener", fastener=short))
    return clauses, True


#: Dimensions a compact (multi-stack) step repeats: the placement facts of its optic; the rest is in the table.
COMPACT_STEP_KINDS = (DimensionKindV1.OPTICAL_AXIS_HEIGHT, DimensionKindV1.OPTIC_BOARD_POSITION)


def _part_step(facts: DrawingFacts, part_facts, w: Words, *, compact: bool) -> Step:  # noqa: ANN001 - PartFacts
    scene = facts.scene
    measured = facts.detail.measured
    part = part_facts.part
    chain = facts.chain_of(part.part_ref)
    number = chain.number if chain is not None else 0
    me = w("step.me", line=part_facts.line_ref, sku=part_facts.sku)
    if part.support is None and facts.board.customer_table and part.part_ref == facts.board.part_ref:
        board = facts.board
        grid = (w("step.place_table_grid", thread=board.thread, pitch=mm(board.grid_pitch[0]))
                if board.thread is not None and board.grid_pitch is not None else "")
        text = w("step.place_table", w=mm(board.x1 - board.x0), h=mm(board.y1 - board.y0), grid=grid, z=mm(board.top_z))
        return Step("PLACE", part.part_ref, None, text, number)
    if part.support is None:
        text = w("step.place_board", line=part_facts.line_ref, sku=part_facts.sku, z=mm(facts.board.top_z))
        return Step("PLACE", part.part_ref, part_facts.line_ref, text, number)
    settings = [s for s in scene.settings if s.part_ref == part.part_ref]
    fixation = next((f for f in scene.fixations if f.part_ref == part.part_ref), None)
    mine = [d for d in facts.detail.detail.dimensions if d.subject_part_ref == part.part_ref]
    parent = facts.part(part.support.parent_ref)
    mate = next((m for m in scene.mates if m.mate_ref == part.support.mate_ref), None)
    parent_name = w("step.your_table") if parent.part_ref == facts.board.part_ref and facts.board.customer_table else parent.line_ref
    head = w(f"step.{part.support.relation.value}", me=me, parent=parent_name)
    if mate is not None and mate.interface_designation:
        head += w("step.interface", interface=mate.interface_designation)
    clauses = [head]
    if mate is not None and mate.fastener is not None and mate.part_a_ref == part.part_ref:
        # a joint that is not the post's stud in the part's only tapped hole: say how it is fastened
        clauses.append(w(f"step.mate.{mate.fastener.kind.value}", thread=mate.fastener.thread_designation, parent=parent_name))
    if (face := face_clause(facts, part, w)) is not None:
        clauses.append(face)
    fastener_shown = False
    if part.support.relation is SupportRelationV1.STANDS_ON:
        more, fastener_shown = _fixation_clauses(facts, fixation, w)
        clauses += more
    for setting in settings:
        if setting.kind is SettingKindV1.FIXATION_THREAD and fastener_shown:
            continue
        clauses.append(_setting_text(setting, facts, w, compact=compact))
    grouped: dict[tuple[DimensionKindV1, DimensionBasisV1], list[str]] = {}
    for dim in mine:
        if dim.kind is DimensionKindV1.INSERTION_DEPTH:
            continue  # the setting clause shows it
        if compact and dim.kind not in COMPACT_STEP_KINDS:
            continue  # the dimension table and the views show it
        value = mm(abs(measured[dim.dim_ref].value_mm))
        shown = value if compact and dim.kind is DimensionKindV1.OPTICAL_AXIS_HEIGHT else w("step.dim_value", axis=AXIS_LETTER[dim.axis], v=value)
        grouped.setdefault((dim.kind, dim.basis), []).append(shown)
    dim_clauses = []
    for (kind, basis), values in grouped.items():
        words = w.enum("kind_words", kind.value)
        if compact:
            key = "step.dims_compact" if basis is DimensionBasisV1.RESOLVED else "step.dims_compact_basis"
            dim_clauses.append(w(key, kind=words, values=", ".join(values), basis=w.enum("basis_words", basis.value)))
        else:
            dim_clauses.append(w("step.dims", kind=words, values=", ".join(values), basis=w.enum("basis_words", basis.value)))
    join = w("step.join")
    text = join.join(clauses + dim_clauses) + w("step.end")
    while len(text) > MAX_STEP_TEXT and dim_clauses:
        dim_clauses.pop()
        text = join.join(clauses + dim_clauses) + w("step.more_dims")
    return Step(RELATION_ACTION[part.support.relation], part.part_ref, part_facts.line_ref, text, number)


def _snap(v: tuple[float, float, float]) -> tuple[float, float, float]:
    """A unit direction with rounding noise removed, so an axis-aligned one prints as +X / -Y / ..."""

    return tuple(0.0 if abs(c) < AXIS_SNAP else (1.0 if c > 1.0 - AXIS_SNAP else (-1.0 if c < -1.0 + AXIS_SNAP else c))
                 for c in v)  # type: ignore[return-value]


def incoming_direction(facts: DrawingFacts, instance_id: str) -> tuple[float, float, float] | None:
    """The world direction of the beam arriving at a design element: the segment entering it on the first beam axis
    that names it (``through``) at a point other than the axis's start; None when no axis names it so."""

    for axis in facts.scene.beam_reference.beam_axes:
        through = axis.through or ()
        if instance_id not in through:
            continue
        i = through.index(instance_id)
        if i == 0:
            continue
        d = tuple(b - a for a, b in zip(axis.points_mm[i - 1], axis.points_mm[i], strict=True))
        n = sum(c * c for c in d) ** 0.5
        if n > 0.0:
            return tuple(c / n for c in d)  # type: ignore[return-value]
    return None


def _face_words(w: Words, face, faces) -> str:  # noqa: ANN001 - the vendored OpticFaceV1 / OpticFacesV1
    """"convex face", or with its radius when both faces share that shape ("convex face of radius 60 mm")."""

    words = w.enum("face", face.shape.value)
    if faces.front.shape is faces.back.shape and face.radius_mm is not None:
        return w("face.radius", face=words, r=mm(face.radius_mm))
    return words


def face_clause(facts: DrawingFacts, part, w: Words) -> str | None:  # noqa: ANN001 - the vendored PreviewPartV1
    """Which face of an optic with dissimilar faces meets the incoming beam, from its pose: light along
    the optic's local +Z meets its front face first. Without a beam axis naming the optic: which way its front faces."""

    faces = part.faces
    if faces is None or part.design_binding is None:
        return None
    axis = _qrotate(tuple(part.pose_in_world.quaternion_wxyz), (0.0, 0.0, 1.0))
    incoming = incoming_direction(facts, part.design_binding.instance_id)
    if incoming is None:
        return w("step.face", face=_face_words(w, faces.front, faces), axis=world_axis(_snap(tuple(-c for c in axis))))  # type: ignore[arg-type]
    face = faces.front if sum(a * b for a, b in zip(axis, incoming, strict=True)) > 0.0 else faces.back
    return w("step.face_beam", face=_face_words(w, face, faces), axis=world_axis(_snap(tuple(-c for c in incoming))))  # type: ignore[arg-type]


def _operation_step(facts: DrawingFacts, operation, w: Words) -> Step:  # noqa: ANN001 - ManualOperationV1
    owner = facts.part(operation.part_ref)
    chain = facts.chain_of(operation.part_ref)
    text = w("step.operation", line=owner.line_ref, sku=owner.sku, kind=w.enum("operation", operation.kind.value))
    return Step("MANUAL_OPERATION", operation.part_ref, owner.line_ref, text, chain.number if chain is not None else 0)


def travel_text(w: Words, dof, key: str = "step.adjust_travel") -> str:  # noqa: ANN001 - DofImplementationV1
    if dof.travel_min is None or dof.travel_max is None:
        return ""
    unit = w("unit.deg") if dof.axis.value in ROTATION_AXES else w("unit.mm")
    printed = deg if dof.axis.value in ROTATION_AXES else mm
    nominal = dof.travel_basis is not None and dof.travel_basis.value == "NOMINAL"
    return w(key + ("_nominal" if nominal else ""), lo=printed(dof.travel_min), hi=printed(dof.travel_max), unit=unit)


def optic_name(facts: DrawingFacts, dof) -> str:  # noqa: ANN001 - DofImplementationV1
    """The optic a DOF belongs to: its design id with its BOM line, e.g. ``L2 (B09)``."""

    if dof.optic_part_ref is None:
        return safe(dof.design_instance_id)
    return f"{safe(dof.design_instance_id)} ({facts.part(dof.optic_part_ref).line_ref})"


def _adjust_step(facts: DrawingFacts, dof, w: Words, *, compact: bool) -> Step:  # noqa: ANN001 - DofImplementationV1
    impl = facts.part(dof.implemented_by_part_ref)
    moved = facts.part(dof.moved_part_ref)
    chain = facts.chain_of(impl.part_ref)
    values = {"control": w.enum("control_words", dof.delivered_control.value), "axis": dof.axis.value,
              "optic": optic_name(facts, dof), "direction": world_axis(dof.world_direction),
              "mechanism": w.enum("mechanism", dof.mechanism.value), "line": impl.line_ref, "travel": travel_text(w, dof),
              "fallback": w("step.adjust_fallback") if dof.status is DofDeliveryStatusV1.DELIVERED_AS_MANUAL else ""}
    if compact or moved.part_ref == impl.part_ref:
        text = w("step.adjust", **values)
    else:
        text = w("step.adjust_moves", moved=moved.line_ref, **values)
    return Step("ADJUST", impl.part_ref, impl.line_ref, text, chain.number if chain is not None else 0)


def delivered(dof) -> bool:  # noqa: ANN001
    return not (dof.delivered_control is DeliveredControlV1.NONE or dof.implemented_by_part_ref is None
                or dof.moved_part_ref is None)


def assembly_steps(facts: DrawingFacts, locale: str = "en") -> list[Step]:
    """The ordered steps. One optic stack: every part in assembly order, then the manual operations, then the
    delivered adjustments. Several stacks: the breadboard, then stack by stack (its parts in assembly order, its manual
    operations, its delivered adjustments) in compact wording -- each dimension stays in the dimension table and on the
    views; a step repeats only its optic's placement and every nominal value."""

    w = words_for(facts, locale)
    scene = facts.scene
    compact = len(facts.chains) > 1
    parts = [_part_step(facts, p, w, compact=compact) for p in facts.parts]
    operations = [_operation_step(facts, o, w) for o in scene.manual_operations]
    adjustments = [_adjust_step(facts, d, w, compact=compact) for d in scene.dof_implementations if delivered(d)]
    if not compact:
        steps = parts + operations + adjustments
    else:
        steps = [s for s in parts if s.chain == 0]
        for chain in facts.chains:
            steps += [s for group in (parts, operations, adjustments) for s in group if s.chain == chain.number]
        steps += [s for group in (operations, adjustments) for s in group if s.chain == 0]
    steps += customer_steps(facts, w)
    for step in steps:
        if len(step.text) > MAX_STEP_TEXT or _UNSAFE.search(step.text):
            raise ValueError(f"step text outside the step alphabet or length: {step.part_ref or step.design_instance_id}")
    return steps


def dof_rows(facts: DrawingFacts, locale: str = "en") -> list[tuple[str, str, str, bool]]:
    """(axis label, delivery, detail, highlighted) of every DOF implementation, for the sheet's adjustments table
    (``highlighted``: a fixed axis or a nominal travel)."""

    w = words_for(facts, locale)
    rows = []
    for dof in facts.scene.dof_implementations:
        label = w("dof.label", axis=dof.axis.value, instance=safe(dof.design_instance_id))
        requested = w.enum("control_words", dof.requested_control.value)
        if dof.status is DofDeliveryStatusV1.NOT_DELIVERED:
            if dof.optic_part_ref is None:
                rows.append((label, w("dof.your_hardware"), w("dof.your_hardware_detail", requested=requested), False))
            else:
                rows.append((label, w("dof.fixed"), w("dof.fixed_detail", requested=requested), True))
            continue
        impl = facts.part(dof.implemented_by_part_ref).line_ref if dof.implemented_by_part_ref else "-"
        control = w.enum("control", dof.delivered_control.value)
        delivery = (w("dof.delivered_as_manual", control=control, requested=requested)
                    if dof.status is DofDeliveryStatusV1.DELIVERED_AS_MANUAL else w("dof.delivered", control=control))
        nominal = dof.travel_basis is not None and dof.travel_basis.value == "NOMINAL"
        detail = w("dof.detail", mechanism=w.enum("mechanism", dof.mechanism.value), line=impl,
                   direction=world_axis(dof.world_direction), travel=travel_text(w, dof, "dof.travel"))
        rows.append((label, delivery, detail, nominal and dof.travel_min is not None))
    return rows
