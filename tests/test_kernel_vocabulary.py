"""The assembly kernel's sources state engineering rules only: no price, cost or source-company vocabulary."""

from __future__ import annotations

import re
from pathlib import Path

import os_optics.assembly_plan.kernel as kernel

KERNEL = Path(kernel.__file__).resolve().parent
#: Words of commercial or sourcing data the kernel never reads (whole words or field names, case-insensitive).
FORBIDDEN = ("part_key", "supplier", "unit_price", "price", "cost")


def test_kernel_has_no_private_vocabulary():
    files = sorted(KERNEL.glob("*.py"))
    assert {p.name for p in files} >= {"resolve.py", "catalog.py", "models.py", "geometry.py", "beam.py", "beam_lines.py", "assemble.py"}
    hits = [(p.name, word) for p in files for word in FORBIDDEN
            if re.search(rf"(?<![a-z]){re.escape(word)}", p.read_text(encoding="utf-8"), re.IGNORECASE)]
    assert hits == []


def test_the_ranking_breaks_ties_by_identity():
    """The mount ranking key ends with the identity, and no other key element is a string (no price)."""

    from kernel_fixture import FIXED, catalog, reference_request
    from os_optics.assembly_plan.kernel import rank_mounts
    from os_optics.assembly_plan.kernel.resolve import _optic_with_yaw
    from os_optics.assembly_plan.kernel.geometry import IDENTITY

    request = reference_request()
    optic = _optic_with_yaw(request.optics[0], "c1", IDENTITY)
    ranked, _ = rank_mounts(optic, catalog())
    keys = [key for key, _, _ in ranked]
    assert all(isinstance(key[-1], str) and key[-1] == mount.identity for key, mount, _ in ranked)
    assert all(len(key) == 4 for key in keys)
    assert ranked[0][1].identity == FIXED.identity
