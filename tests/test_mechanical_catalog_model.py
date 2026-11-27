"""The public mechanical catalog contract (``opensci.public_mechanical_catalog`` 1.0): validators, canonical form,
content digest, profiles, parameter sets, policy bounds, publisher rules, schema-version refusal, the manifest model
and ``MechanicalSources``."""

from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

from mechanical_fixture import OPENSCI_FIXTURE, USER_FIXTURE, fixture_document, fixture_path
from os_optics._vendor.shared_contracts.common.osc1 import canonicalize_osc1_bytes
from os_optics.assembly_plan.mechanical import (
    PARAM_KEYS,
    SUPPORTED_MECHANICAL_SCHEMA_VERSIONS,
    BreadboardIdentityV1,
    IdentityProfileV1,
    MechanicalCatalogError,
    MechanicalCatalogManifestV1,
    MechanicalErrorCode,
    MechanicalKindV1,
    MechanicalSources,
    MountHoldsV1,
    MountIdentityV1,
    PublicBasisV1,
    PublicMechanicalCatalogV1,
    PublicResolverPolicyV1,
    VisualPrimitiveV1,
    canonical_mechanical_bytes,
    canonical_visual,
    load_mechanical_catalog,
    load_mechanical_document,
    mechanical_catalog_json_schema,
    mechanical_content_digest,
    seal_mechanical_document,
    weakest_basis,
)

E = MechanicalErrorCode


def _bytes(document: dict) -> bytes:
    return canonicalize_osc1_bytes(seal_mechanical_document(document))


def _refused(data, code: MechanicalErrorCode, **kwargs) -> MechanicalCatalogError:
    with pytest.raises(MechanicalCatalogError) as caught:
        load_mechanical_catalog(data, **kwargs)
    assert caught.value.code is code, caught.value.as_dict()
    return caught.value


def _identity(document: dict, sku: str) -> dict:
    return next(i for i in document["identities"] if i["opensci_sku"] == sku)


@pytest.fixture
def doc() -> dict:
    return fixture_document(OPENSCI_FIXTURE)


# ---------------------------------------------------------------------------------------------------- reading


def test_the_committed_documents_load_and_round_trip():
    for name, publisher in ((OPENSCI_FIXTURE, "OPENSCI"), (USER_FIXTURE, "USER")):
        data = fixture_path(name).read_bytes()
        model = load_mechanical_catalog(data, publisher=publisher)
        assert model.publisher == publisher
        assert canonical_mechanical_bytes(model) == data  # the model dump is the generator form, byte for byte
        assert model.content_digest == mechanical_content_digest(json.loads(data))


def test_an_opensci_document_must_be_canonical_bytes_and_a_user_document_need_not_be(doc):
    pretty = json.dumps(doc, indent=2).encode("utf-8")
    _refused(pretty, E.MECHANICAL_CATALOG_NOT_CANONICAL)
    user = fixture_document(USER_FIXTURE)
    pretty_user = json.dumps(user, indent=2, ensure_ascii=False).encode("utf-8")
    loaded = load_mechanical_document(pretty_user)
    assert loaded.document.publisher == "USER" and loaded.size_bytes == len(pretty_user)
    import hashlib

    assert loaded.sha256 == hashlib.sha256(pretty_user).hexdigest()  # the bytes as given, not a canonical form


def test_a_user_document_may_leave_defaults_out_and_its_digest_is_over_its_own_json():
    user = fixture_document(USER_FIXTURE)
    for primitive in user["identities"][0]["visual"]:
        primitive.pop("inner_radius_mm")  # a default
    user["identities"][0].pop("basis")  # absent = nothing recorded
    data = json.dumps(seal_mechanical_document(user)).encode("utf-8")
    model = load_mechanical_catalog(data)
    assert model.identities[0].basis_of("diameter_mm") is None
    assert model.content_digest == mechanical_content_digest(json.loads(data))


def test_the_content_digest_is_checked(doc):
    doc["identities"][0]["display_name"] = "Changed name"
    _refused(canonicalize_osc1_bytes(doc), E.MECHANICAL_CATALOG_DIGEST_MISMATCH)


@pytest.mark.parametrize("version", ["1.1", "2.0", None])
def test_an_unsupported_schema_version_is_refused_with_the_supported_ones(doc, version):
    doc["schema_version"] = version
    error = _refused(_bytes(doc), E.MECHANICAL_SCHEMA_UNSUPPORTED)
    assert error.details["supported"] == list(SUPPORTED_MECHANICAL_SCHEMA_VERSIONS) == ["1.0"]
    assert error.details["newest_supported"] == "1.0"


@pytest.mark.parametrize("data", [b"\xff\xfe", b"[1, 2]", b"{\"schema\": \"opensci.public_optical_catalog\"}", b"not json"])
def test_what_is_not_a_mechanical_document_is_refused(data):
    _refused(data, E.MECHANICAL_CATALOG_INVALID)


def test_the_size_limit_is_enforced():
    from os_optics.assembly_plan.mechanical.model import MAX_DOCUMENT_BYTES

    error = _refused(b" " * (MAX_DOCUMENT_BYTES + 1), E.MECHANICAL_CATALOG_INVALID)
    assert error.details["max_bytes"] == MAX_DOCUMENT_BYTES


def test_the_expected_publisher_is_checked(doc):
    _refused(fixture_path(OPENSCI_FIXTURE).read_bytes(), E.MECHANICAL_CATALOG_INVALID, publisher="USER")


@pytest.mark.parametrize("field", ["unit_price", "supplier", "part_key", "currency", "supplier_sku"])
def test_private_fields_are_refused_anywhere(doc, field):
    _identity(doc, "OS-MECH-TEST-POST-D6-L50")[field] = "1"
    error = _refused(_bytes(doc), E.MECHANICAL_CATALOG_INVALID)
    assert any(p["type"] == "extra_forbidden" for p in error.problems)
    doc = fixture_document(OPENSCI_FIXTURE)
    doc["policy"]["price_sheet_key" if field == "part_key" else field] = "1"
    _refused(_bytes(doc), E.MECHANICAL_CATALOG_INVALID)


def test_the_json_schema_is_produced():
    schema = mechanical_catalog_json_schema()
    assert schema["properties"]["schema"]["const"] == "opensci.public_mechanical_catalog"
    assert set(schema["required"]) >= {"schema", "schema_version", "publisher", "mechanical_version", "frames", "identities",
                                       "content_digest"}


# ---------------------------------------------------------------------------------------------------- document rules


def _invalid(document: dict, *needles: str) -> None:
    with pytest.raises(ValidationError) as caught:
        PublicMechanicalCatalogV1.model_validate(document)
    text = str(caught.value)
    assert all(n in text for n in needles), text


def test_identities_are_sorted_and_unique(doc):
    doc["identities"].reverse()
    _invalid(doc, "sorted")
    doc = fixture_document(OPENSCI_FIXTURE)
    doc["identities"].insert(0, copy.deepcopy(doc["identities"][0]))
    _invalid(doc, "unique")


def test_opensci_rules(doc):
    bad = copy.deepcopy(doc)
    bad["mechanical_version"] = "m1.0123"
    _invalid(bad, "mechanical_version")
    bad = copy.deepcopy(doc)
    bad.pop("policy")
    _invalid(bad, "policy")
    bad = copy.deepcopy(doc)
    bad["identities"] = [i for i in bad["identities"] if i["kind"] != "BREADBOARD"]
    _invalid(bad, "BREADBOARD")
    bad = copy.deepcopy(doc)
    user_post = copy.deepcopy(fixture_document(USER_FIXTURE)["identities"][0])
    bad["identities"] = sorted(bad["identities"] + [user_post], key=lambda i: i["opensci_sku"])
    _invalid(bad, "OS-MECH")


def test_user_rules():
    user = fixture_document(USER_FIXTURE)
    bad = copy.deepcopy(user)
    bad["policy"] = PublicResolverPolicyV1().model_dump(mode="json")
    _invalid(bad, "no policy")
    bad = copy.deepcopy(user)
    bad["mechanical_version"] = "m1.0123456789ab"
    PublicMechanicalCatalogV1.model_validate(bad)  # a lower-case label is a valid USER version too
    bad["mechanical_version"] = "Customer Posts"
    _invalid(bad, "mechanical_version")
    bad = copy.deepcopy(user)
    bad["identities"][0]["opensci_sku"] = "OS-MECH-TEST-POST-D12P7-L60"
    _invalid(bad, "USER-MECH")


# ---------------------------------------------------------------------------------------------------- identities


def test_basis_and_profile_keys_belong_to_the_kind(doc):
    post = _identity(doc, "OS-MECH-TEST-POST-D6-L50")
    post["basis"]["envelope"] = "CAD_DERIVED"  # a post has no envelope parameter
    _invalid(doc, "envelope")
    doc = fixture_document(OPENSCI_FIXTURE)
    _identity(doc, "OS-MECH-TEST-POST-D6-L50")["basis"]["diameter_mm"] = "SUPPLIER_STRUCTURED"  # the private name
    _invalid(doc, "basis values")
    doc = fixture_document(OPENSCI_FIXTURE)
    _identity(doc, "OS-MECH-TEST-POST-D6-L50")["profile"] = {"kind": "CONSERVATIVE", "conservative_parameters": ["thread"]}
    _invalid(doc, "conservative_parameters")


def test_param_key_sets_are_the_resolver_parameters():
    assert PARAM_KEYS[MechanicalKindV1.POST] == {"diameter_mm", "length_mm", "top_thread"}
    assert "envelope" in PARAM_KEYS[MechanicalKindV1.POST_HOLDER] and "base.slot" in PARAM_KEYS[MechanicalKindV1.POST_HOLDER]
    assert {f"mechanism.{k}.travel" for k in ("TIP_TILT", "TRANSVERSE_TRANSLATION", "AXIAL_ROTATION")} <= PARAM_KEYS[MechanicalKindV1.MOUNT]
    assert not any("price" in k or "supplier" in k for keys in PARAM_KEYS.values() for k in keys)


@pytest.mark.parametrize("profile", [
    {"kind": "SINGLE", "conservative_parameters": ["envelope"]},
    {"kind": "CONSERVATIVE", "conservative_parameters": []},
    {"kind": "CONSERVATIVE", "conservative_parameters": ["holds.size_mm", "envelope"]},
    {"kind": "CONSERVATIVE", "conservative_parameters": ["envelope", "envelope"]},
])
def test_profile_rules(profile):
    with pytest.raises(ValidationError):
        IdentityProfileV1.model_validate(profile)


def test_the_conservative_profile_is_read(doc):
    model = load_mechanical_catalog(fixture_path().read_bytes())
    mirror = model.identity("OS-MECH-TEST-MIRROR-MOUNT-KIN-D25")
    assert mirror.profile.kind == "CONSERVATIVE"
    assert mirror.profile.conservative_parameters == ("envelope", "holds.thickness_max_mm")
    plate = model.identity("OS-MECH-TEST-PLATE-MOUNT-KIN-D25-T")
    assert plate.holds.clear_aperture_mm is None
    assert plate.basis_of("holds.clear_aperture_mm") is PublicBasisV1.PREVIEW_ASSUMPTION and not plate.is_fact("holds.clear_aperture_mm")
    assert plate.basis_of("seat_stop_x_mm") is None  # not recorded


def test_visual_primitives_are_in_canonical_order_and_shaped_by_kind(doc):
    holder = _identity(doc, "OS-MECH-TEST-HOLDER-D12P7-H50")
    holder["visual"].reverse()
    _invalid(doc, "canonical order")
    box = {"kind": "BOX", "finish": "BODY", "min_mm": [0, 0, 0], "max_mm": [1, 1, 1]}
    for bad in ({**box, "radius_mm": 1.0}, {**box, "max_mm": [1, 0, 1]}, {**box, "inner_radius_mm": 0.5},
                {"kind": "CYLINDER", "finish": "GLASS", "base_mm": [0, 0, 0], "axis": "z", "length_mm": 1.0},
                {"kind": "CYLINDER", "finish": "GLASS", "base_mm": [0, 0, 0], "axis": "z", "length_mm": 1.0, "radius_mm": 1.0,
                 "inner_radius_mm": 1.0}):
        with pytest.raises(ValidationError):
            VisualPrimitiveV1.model_validate(bad)
    a, b = VisualPrimitiveV1.model_validate(box), VisualPrimitiveV1.model_validate({**box, "finish": "DARK"})
    assert canonical_visual([b, a, b]) == canonical_visual([a, b]) and len(canonical_visual([b, a, b])) == 2


def test_numbers_are_strict(doc):
    _identity(doc, "OS-MECH-TEST-POST-D6-L50")["length_mm"] = "50"
    _invalid(doc, "length_mm")
    doc = fixture_document(OPENSCI_FIXTURE)
    _identity(doc, "OS-MECH-TEST-POST-D6-L50")["length_mm"] = True
    _invalid(doc, "length_mm")
    doc = fixture_document(OPENSCI_FIXTURE)
    _identity(doc, "OS-MECH-TEST-LENS-MOUNT-FIXED-D25")["holds"]["transmissive"] = 1
    _invalid(doc, "transmissive")


def test_mount_rules(doc):
    xy = _identity(doc, "OS-MECH-TEST-LENS-MOUNT-XY-D25")
    xy["mechanisms"] = []
    _invalid(doc, "XY_TRANSLATION")
    doc = fixture_document(OPENSCI_FIXTURE)
    fixed = _identity(doc, "OS-MECH-TEST-LENS-MOUNT-FIXED-D25")
    fixed["mechanisms"] = [{"kind": "TIP_TILT", "travel": 1.0, "control": "MANUAL", "adjustment": "FINE_ADJUSTER"}]
    _invalid(doc, "FIXED")
    doc = fixture_document(OPENSCI_FIXTURE)
    _identity(doc, "OS-MECH-TEST-LENS-MOUNT-FIXED-D25")["post_fastening"] = []
    _invalid(doc, "post_fastening")
    doc = fixture_document(OPENSCI_FIXTURE)
    _identity(doc, "OS-MECH-TEST-LENS-MOUNT-FIXED-D25")["post_fastening"] = ["STUD_INTO_TAPPED_FACE", "PLAIN_STUD"]
    _invalid(doc, "sorted")
    with pytest.raises(ValidationError, match="thickness_min_mm"):
        MountHoldsV1.model_validate({"shape": "ROUND", "size_mm": 25.4, "thickness_min_mm": 5.0, "thickness_max_mm": 4.0,
                                     "transmissive": True})
    model = load_mechanical_catalog(fixture_path().read_bytes())
    rotation = model.identity("OS-MECH-TEST-ROTATION-MOUNT-D25")
    assert isinstance(rotation, MountIdentityV1) and rotation.mechanism("AXIAL_ROTATION").travel == 180.0
    assert rotation.mechanism("TIP_TILT") is None


def test_holder_and_fork_rules(doc):
    holder = _identity(doc, "OS-MECH-TEST-HOLDER-D12P7-H25")
    holder["insertion_max_mm"] = 30.0  # deeper than the holder is high
    _invalid(doc, "insertion")
    doc = fixture_document(OPENSCI_FIXTURE)
    base = _identity(doc, "OS-MECH-TEST-HOLDER-D12P7-H25")["base"]
    base["slot_end_mm"] = base["slot_start_mm"]
    _invalid(doc, "slot")
    doc = fixture_document(OPENSCI_FIXTURE)
    _identity(doc, "OS-MECH-TEST-HOLDER-D12P7-H25")["base"]["body"]["max_mm"][2] = 0
    _invalid(doc, "min_mm < max_mm")


def test_breadboard_hole_count():
    model = load_mechanical_catalog(fixture_path().read_bytes())
    board = model.identity("OS-MECH-TEST-BREADBOARD-450X150-M6G25")
    assert isinstance(board, BreadboardIdentityV1) and board.hole_count == (18, 6)
    assert [i.opensci_sku for i in model.of_kind("BREADBOARD")] == [
        "OS-MECH-TEST-BREADBOARD-150X150-M6G25", "OS-MECH-TEST-BREADBOARD-300X150-M6G25",
        "OS-MECH-TEST-BREADBOARD-300X300-M6G25", "OS-MECH-TEST-BREADBOARD-450X150-M6G25"]


def test_display_names_follow_the_scene_text_grammar(doc):
    _identity(doc, "OS-MECH-TEST-POST-D6-L50")["display_name"] = "Post <6 mm>"
    _invalid(doc, "display_name")


def test_identities_are_hashable_and_frozen():
    model = load_mechanical_catalog(fixture_path().read_bytes())
    assert len({hash(i) for i in model.identities}) == len(model.identities)
    with pytest.raises(ValidationError):
        model.identities[0].display_name = "x"  # type: ignore[misc]


# ---------------------------------------------------------------------------------------------------- policy and basis


def test_policy_defaults_and_bounds():
    policy = PublicResolverPolicyV1()
    assert (policy.preferred_beam_height_mm, policy.beam_height_step_mm, policy.max_chain_placements) == (125.0, 0.5, 12000)
    assert set(PublicResolverPolicyV1.model_fields).isdisjoint({"price_sheet_key", "currency", "price_notice"})
    for bad in ({"fallback_clear_aperture_ratio": 1.5}, {"max_plate_tilt_deg": 31.0}, {"max_beam_points": 1},
                {"oblique_glass_index": 0.9}, {"min_beam_height_mm": 200.0}, {"max_chain_placements": 0},
                {"beam_clearance_mm": -1.0}):
        with pytest.raises(ValidationError):
            PublicResolverPolicyV1.model_validate(bad)


def test_weakest_basis():
    assert weakest_basis(["DATASHEET", "CAD_DERIVED"]) is PublicBasisV1.CAD_DERIVED
    assert weakest_basis(["REVIEWED_REFERENCE", "PREVIEW_ASSUMPTION", "FAMILY_RULE"]) is PublicBasisV1.PREVIEW_ASSUMPTION
    assert weakest_basis([]) is None


# ---------------------------------------------------------------------------------------------------- manifest


def _entry(version: str, status: str, at: str) -> dict:
    return {"mechanical_version": version, "schema_version": "1.0", "sha256": "a" * 64, "size_bytes": 10, "published_at": at,
            "status": status}


def test_manifest_rules():
    current = _entry("m1.0123456789ab", "CURRENT", "2026-11-28T00:00:00Z")
    older = _entry("m1.00000000000a", "RETAINED", "2026-11-01T00:00:00Z")
    manifest = {"schema": "opensci.public_mechanical_catalog.manifest", "manifest_version": "1.0", "current": current,
                "retained": [current, older], "release_path_template": "/api/v1/catalog/mechanical/{mechanical_version}.json"}
    model = MechanicalCatalogManifestV1.model_validate(manifest)
    assert model.release_path("m1.00000000000a") == "/api/v1/catalog/mechanical/m1.00000000000a.json"
    for change in ({"retained": [older, current]}, {"retained": [older]},
                   {"retained": [current, {**older, "status": "CURRENT"}]},
                   {"release_path_template": "/api/v1/catalog/mechanical/latest.json"},
                   {"release_path_template": "https://other.example/{mechanical_version}"},
                   {"current": {**current, "status": "RETAINED"}}):
        with pytest.raises(ValidationError):
            MechanicalCatalogManifestV1.model_validate({**manifest, **change})
    with pytest.raises(ValidationError):
        MechanicalCatalogManifestV1.model_validate({**manifest, "current": {**current, "mechanical_version": "user-1"}})


# ---------------------------------------------------------------------------------------------------- sources


def _user_doc(sku: str, version: str) -> bytes:
    user = fixture_document(USER_FIXTURE)
    user["mechanical_version"] = version
    user["identities"][0]["opensci_sku"] = sku
    return _bytes(user)


def test_sources_order_policy_binding_and_lookup():
    opensci = fixture_path(OPENSCI_FIXTURE).read_bytes()
    user = fixture_path(USER_FIXTURE).read_bytes()
    sources = MechanicalSources.of(user, opensci, user)  # identical bytes twice count once
    assert [d.publisher for d in sources.documents] == ["OPENSCI", "USER"]
    assert sources.policy == load_mechanical_catalog(opensci).policy
    refs = sources.binding()
    assert [(r.publisher, r.mechanical_version) for r in refs] == [("OPENSCI", "m1.0123456789ab"), ("USER", "test-user-hardware.1")]
    assert refs[0].sha256 == load_mechanical_document(opensci).sha256
    assert sources.identity("USER-MECH-TEST-POST-D12P7-L60").length_mm == 60.0
    assert sources.publisher_of("OS-MECH-TEST-POST-D6-L50") == "OPENSCI" and sources.identity("OS-MECH-NONE") is None
    skus = [i.opensci_sku for i in sources.identities()]
    assert skus == sorted(skus) and len(skus) == len(load_mechanical_catalog(opensci).identities) + 1


def test_sources_without_an_opensci_document_use_the_default_policy_and_require_reports_the_recovery():
    sources = MechanicalSources.of(fixture_path(USER_FIXTURE).read_bytes())
    assert sources.opensci is None and sources.policy == PublicResolverPolicyV1()
    with pytest.raises(MechanicalCatalogError) as caught:
        MechanicalSources().require()
    assert caught.value.code is E.MECHANICAL_CATALOG_NOT_AVAILABLE and "catalog sync" in caught.value.message


def test_sources_refuse_duplicates_and_too_many_documents():
    opensci = fixture_path(OPENSCI_FIXTURE).read_bytes()
    with pytest.raises(MechanicalCatalogError) as caught:
        MechanicalSources.of(_user_doc("USER-MECH-TEST-A", "a"), _user_doc("USER-MECH-TEST-A", "b"))
    assert caught.value.code is E.MECHANICAL_IDENTITY_DUPLICATE and caught.value.details["opensci_sku"] == "USER-MECH-TEST-A"
    other = fixture_document(OPENSCI_FIXTURE)
    other["mechanical_version"] = "m2.0123456789ab"
    with pytest.raises(MechanicalCatalogError) as caught:
        MechanicalSources.of(opensci, _bytes(other))
    assert caught.value.code is E.MECHANICAL_CATALOG_INVALID
    users = [_user_doc(f"USER-MECH-TEST-P{n}", f"u{n}") for n in range(9)]
    MechanicalSources.of(*users[:8])
    with pytest.raises(MechanicalCatalogError) as caught:
        MechanicalSources.of(*users)
    assert caught.value.code is E.MECHANICAL_CATALOG_INVALID
