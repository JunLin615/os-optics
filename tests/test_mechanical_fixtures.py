"""The committed synthetic mechanical fixtures equal their builder and stay synthetic."""

from __future__ import annotations

import json
import re

from mechanical_fixture import FIXTURE_DIR, OPENSCI_FIXTURE, USER_FIXTURE, fixture_bytes
from os_optics.assembly_plan.mechanical import MechanicalKindV1, MechanicalSources, load_mechanical_catalog

# Words a public mechanical document never carries as a key (supplier, commerce and evidence vocabulary).
PRIVATE_KEY_WORDS = ("supplier", "price", "cost", "currency", "part_key", "family", "evidence", "physical", "asset", "mass")


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def test_committed_fixtures_equal_the_builder():
    built = fixture_bytes()
    assert sorted(p.name for p in FIXTURE_DIR.glob("*.json")) == sorted(built)
    for name, data in built.items():
        assert (FIXTURE_DIR / name).read_bytes() == data, f"{name} is stale: run python tests/generate_fixtures.py mechanical"


def test_fixtures_are_synthetic_and_carry_no_private_vocabulary():
    for name in (OPENSCI_FIXTURE, USER_FIXTURE):
        document = json.loads((FIXTURE_DIR / name).read_bytes())
        assert all(re.fullmatch(r"(OS|USER)-MECH-TEST(-[A-Z0-9]+)+", i["opensci_sku"]) for i in document["identities"])
        keys = _keys(document)
        assert not [k for k in keys if any(word in k.lower() for word in PRIVATE_KEY_WORDS)]
        assert not re.search(r"\b[0-9a-f]{64}\b", json.dumps({k: v for k, v in document.items() if k != "content_digest"}))


def test_the_opensci_fixture_covers_the_planned_identities():
    model = load_mechanical_catalog((FIXTURE_DIR / OPENSCI_FIXTURE).read_bytes(), publisher="OPENSCI")
    counts = {kind: len(model.of_kind(kind)) for kind in MechanicalKindV1}
    assert counts == {MechanicalKindV1.POST: 6, MechanicalKindV1.POST_HOLDER: 3, MechanicalKindV1.MOUNT: 6,
                      MechanicalKindV1.BREADBOARD: 4}
    assert model.mechanical_version == "m1.0123456789ab"
    assert {m.mount_kind.value for m in model.of_kind("MOUNT")} == {"FIXED", "XY_TRANSLATION", "ROTATION", "KINEMATIC"}
    assert [i.opensci_sku for i in model.identities if i.profile.kind == "CONSERVATIVE"] == ["OS-MECH-TEST-MIRROR-MOUNT-KIN-D25"]
    user = load_mechanical_catalog((FIXTURE_DIR / USER_FIXTURE).read_bytes(), publisher="USER")
    sources = MechanicalSources.of((FIXTURE_DIR / OPENSCI_FIXTURE).read_bytes(), (FIXTURE_DIR / USER_FIXTURE).read_bytes())
    assert [i.opensci_sku for i in user.identities] == ["USER-MECH-TEST-POST-D12P7-L60"]
    assert len(sources.identities()) == len(model.identities) + 1
