"""Shared inputs of the plan directory / ops / check / BOM / CLI tests: derived plans on the synthetic fixtures."""

from __future__ import annotations

from pathlib import Path

import derive_helpers as h
from mechanical_fixture import USER_FIXTURE, fixture_path

from os_optics import assembly_plan as ap
from os_optics.assembly_plan.interfaces import chain_items

MECHANICAL = [str(fixture_path())]
USER_MECHANICAL = [str(fixture_path()), str(fixture_path(USER_FIXTURE))]
ROTATION_MOUNT = "OS-MECH-TEST-ROTATION-MOUNT-D25"
MIRROR_MOUNT = "OS-MECH-TEST-MIRROR-MOUNT-KIN-D25"
THIN_POST = "OS-MECH-TEST-POST-D6-L50"
WIDE_MOUNT = "OS-MECH-TEST-LENS-MOUNT-FIXED-D50"
BOARD_300 = "OS-MECH-TEST-BREADBOARD-300X150-M6G25"
BOARD_450 = "OS-MECH-TEST-BREADBOARD-450X150-M6G25"
LENS = "OS-OPT-TEST-LENS-PCX-F100"


def catalog():
    return h.optical_catalog()


def derive(tmp: Path, kind: str = "focus", *, project=None, name: str = "plan", mechanical=None, **options) -> ap.OpenPlan:
    """A plan of the example ``kind`` (or ``project``) derived into ``tmp/name`` and reopened."""

    cat = catalog()
    prj = project if project is not None else h.project(kind, cat)
    ap.derive_plan(prj, out_dir=tmp / name, mechanical=mechanical or MECHANICAL, catalog=cat, options=options or None)
    return ap.open_plan(tmp / name, catalog=cat, mechanical=mechanical or MECHANICAL)


def hwp_plan(tmp: Path, name: str = "plan") -> ap.OpenPlan:
    cat = catalog()
    return derive(tmp, project=h.with_hwp(h.project("focus", cat)), name=name)


def item_of(revision, sku: str | None = None, *, instance: str | None = None):
    for item in revision.items:
        if (sku is not None and item.identity.opensci_sku == sku) or (instance is not None and item.design_instance_id == instance):
            return item
    raise KeyError(sku or instance)


def hardware(revision, optic_id: str) -> dict[str, object]:
    """``{"MOUNT": item, "POST": item, "POST_HOLDER": item}`` of an optic's chain."""

    return {role.value: item for role, item in chain_items(revision, optic_id).items()}


def codes(revision, *severities: str) -> set[str]:
    return {f.code.value for f in revision.findings if not severities or f.severity.value in severities}


def finding(revision, code: str):
    return [f for f in revision.findings if f.code.value == code]
