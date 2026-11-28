"""``os_optics.client``: the commercial client against a local stand-in of the Commercial API (``commerce_stub``).

Covers every client method, the error mapping (``code`` / ``family`` / ``recovery`` / ``http_status`` / ``request_id``,
also when the service states no recovery), credential resolution order, token-file permissions, deterministic
idempotency keys, the Assembly Detail refused before payment confirmation, that nothing decrypted is written, and that the
token and the decryption grants never reach ``str`` / ``repr`` / exceptions / logs.
"""

from __future__ import annotations

import copy
import inspect
import json
import logging
import os
import pickle
import subprocess
import sys
import traceback
from pathlib import Path

import pytest

from commerce_stub import AGENT_SCOPES, MANAGE_SCOPE, REVIEW_SCOPES, StubServer, StubState, credential_for
from delivery_helpers import PREVIEW_ZIP, sealed_reference_deliveries
from os_optics.assembly import load_assembly_preview
from os_optics.client import (
    AccountToken,
    ArtifactIntegrityError,
    AssemblyDetailNotReleasedError,
    LocalInputError,
    OpenSciApiError,
    OpenSciClient,
    OpenSciConfigurationError,
    OpenSciConnectionError,
    ProtectedContentError,
    Recovery,
    canonical_json_bytes,
    idempotency_key_for,
    read_token_file,
    resolve_token,
    save_token_file,
    submission_document,
)
from os_optics.client.errors import derive_recovery, error_from_response
from os_optics.client.transport import normalize_api_url
from os_optics import __version__
from os_optics.local import example_project, run_optical_project
from os_optics.protected_delivery import ProtectedDelivery

RESOLUTION_ID = load_assembly_preview(PREVIEW_ZIP).manifest.binding.commercial_resolution_id
AGENT = credential_for("agent")
MANAGER = credential_for("manager")
REVIEW = credential_for("review")
DRAWING_FILES = {"assembly_drawing.pdf", "assembly_drawing.svg", "drawing_manifest.json", "drawing_result.json"}


def new_state(**kw) -> StubState:
    state = StubState(resolution_id=RESOLUTION_ID, **kw)
    state.add_token("agent", AGENT_SCOPES)
    state.add_token("manager", [MANAGE_SCOPE])
    state.add_token("review", REVIEW_SCOPES)
    return state


@pytest.fixture(scope="module")
def deliveries():
    return sealed_reference_deliveries()


@pytest.fixture()
def stub(deliveries):
    state = new_state(deliveries=deliveries)
    with StubServer(state) as server:
        yield server


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    return run_optical_project(example_project(), output_dir=tmp_path_factory.mktemp("run") / "run")


def client_for(stub: StubServer, credential: str = AGENT, **kw) -> OpenSciClient:
    return OpenSciClient(stub.url, credential, retries=kw.pop("retries", 0), environ={}, **kw)


def secrets_of(deliveries) -> list[str]:
    return [d["grant"]["key"] for d in deliveries]


def engine_frame_locals(error: BaseException) -> str:
    """``repr`` of the local variables of every os_optics frame the error passed through (what a traceback
    renderer that shows locals prints)."""

    texts, tb = [], error.__traceback__
    while tb is not None:
        if "os_optics" in tb.tb_frame.f_code.co_filename:
            texts += [f"{name}={value!r}" for name, value in tb.tb_frame.f_locals.items()]
        tb = tb.tb_next
    return "\n".join(texts)


def assert_no_secret(text: str, *secrets: str) -> None:
    for secret in secrets:
        assert secret not in text


# ------------------------------------------------------------------------------------------ credentials


def test_credential_resolution_order(tmp_path):
    home = tmp_path / "home"
    (home / ".opensci").mkdir(parents=True)
    (home / ".opensci" / "token").write_text(credential_for("default") + "\n", encoding="ascii")
    env_file = tmp_path / "env-token"
    env_file.write_text("  " + credential_for("env-file") + "\r\n", encoding="ascii")
    arg_file = tmp_path / "arg-token"
    arg_file.write_text(credential_for("arg-file"), encoding="ascii")
    env = {"OPENSCI_TOKEN": credential_for("env"), "OPENSCI_TOKEN_FILE": str(env_file)}

    def resolved(explicit, environ):
        token = resolve_token(explicit, environ=environ, home=home)
        return token.reveal(), token.source

    assert resolved(credential_for("arg"), env) == (credential_for("arg"), "argument")
    assert resolved(arg_file, env) == (credential_for("arg-file"), "argument file")
    assert resolved(None, env) == (credential_for("env"), "OPENSCI_TOKEN")
    assert resolved(None, {"OPENSCI_TOKEN_FILE": str(env_file)}) == (credential_for("env-file"), "OPENSCI_TOKEN_FILE")
    assert resolved(None, {"OPENSCI_TOKEN": "  "}) == (credential_for("default"), "default token file")
    explicit = AccountToken(credential_for("object"))
    assert resolve_token(explicit, environ=env, home=home) is explicit
    with pytest.raises(OpenSciConfigurationError) as missing:
        resolve_token(None, environ={}, home=tmp_path / "nobody")
    assert missing.value.code == "TOKEN_MISSING" and missing.value.recovery is Recovery.PROVIDE_TOKEN
    with pytest.raises(TypeError):
        resolve_token(42, environ={}, home=home)


@pytest.mark.parametrize("bad", ['"{t}"', "{t}x", "{t}\n{t}", "Bearer {t}", "{t_upper}", ""])
def test_a_malformed_token_is_refused_without_echoing_it(bad, tmp_path):
    token = credential_for("secret")
    text = bad.format(t=token, t_upper=token.upper())
    with pytest.raises(OpenSciConfigurationError) as refused:
        AccountToken(text, source="OPENSCI_TOKEN")
    error = refused.value
    assert error.code == "TOKEN_MALFORMED" and error.recovery is Recovery.PROVIDE_TOKEN
    rendered = "".join(traceback.format_exception(error)) + repr(error) + json.dumps(error.as_dict())
    assert_no_secret(rendered, token, token[37:], token.upper())
    file = tmp_path / "token"
    file.write_text(text, encoding="ascii")
    with pytest.raises(OpenSciConfigurationError) as from_file:
        read_token_file(file)
    assert from_file.value.details["path"] == str(file)
    assert_no_secret(str(from_file.value), token, token[37:])


def test_the_token_object_never_shows_the_secret():
    secret = credential_for("hidden")
    token = AccountToken(secret, source="OPENSCI_TOKEN")
    assert token.token_id == f"{secret[4:12]}-{secret[12:16]}-{secret[16:20]}-{secret[20:24]}-{secret[24:36]}"
    for text in (repr(token), str(token), f"{token}", "%s" % token, repr([token]), repr({"t": token})):
        assert_no_secret(text, secret, secret[37:])
    assert token.reveal() == secret and token.authorization() == "Bearer " + secret
    for dump in (pickle.dumps, copy.copy, copy.deepcopy):
        with pytest.raises(TypeError):
            dump(token)


def test_the_token_file_is_owner_only_and_replaced_atomically(tmp_path):
    token = AccountToken(credential_for("saved"))
    target = save_token_file(token, tmp_path / "dir" / "token")
    assert target.read_text(encoding="ascii") == token.reveal() + "\n"
    assert read_token_file(target).reveal() == token.reveal()
    assert sorted(p.name for p in target.parent.iterdir()) == ["token"]
    with pytest.raises(FileExistsError):
        save_token_file(AccountToken(credential_for("other")), target)
    save_token_file(AccountToken(credential_for("other")), target, overwrite=True)
    assert read_token_file(target).reveal() == credential_for("other")
    assert sorted(p.name for p in target.parent.iterdir()) == ["token"]
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600
    else:
        listing = subprocess.run(["icacls", str(target)], capture_output=True, text=True, check=True).stdout
        entries = [line.strip() for line in listing.splitlines()[:-1] if line.strip()]
        entries[0] = entries[0][len(str(target)):].strip()
        user = os.environ["USERNAME"].lower()
        assert len(entries) == 1 and user in entries[0].lower() and "(F)" in entries[0], listing
        assert "(I)" not in listing


def test_the_default_token_file_goes_to_the_home_directory(tmp_path):
    target = save_token_file(AccountToken(credential_for("home")), home=tmp_path)
    assert target == tmp_path / ".opensci" / "token"
    assert resolve_token(None, environ={}, home=tmp_path).source == "default token file"


# ------------------------------------------------------------------------------------------ transport


@pytest.mark.parametrize("url, expected", [
    ("http://127.0.0.1:8750", "http://127.0.0.1:8750"),
    ("http://localhost:8750/api/v1/", "http://localhost:8750"),
    ("http://[::1]:8750", "http://[::1]:8750"),
    ("https://opensci.example:443/", "https://opensci.example:443"),
])
def test_service_urls(url, expected):
    assert normalize_api_url(url) == expected


@pytest.mark.parametrize("url", ["http://opensci.example", "ftp://127.0.0.1", "http://u:p@127.0.0.1", "http://127.0.0.1/x",
                                 "http://127.0.0.1/?a=1", "127.0.0.1:8750"])
def test_unusable_service_urls_are_refused(url):
    with pytest.raises(OpenSciConfigurationError) as refused:
        normalize_api_url(url)
    assert refused.value.code == "API_URL_INVALID"
    assert normalize_api_url("http://opensci.example", allow_insecure_http=True) == "http://opensci.example"


def test_from_environment_needs_the_service_url(tmp_path):
    with pytest.raises(OpenSciConfigurationError) as missing:
        OpenSciClient.from_environment(environ={"OPENSCI_TOKEN": AGENT})
    assert missing.value.code == "API_URL_MISSING"
    client = OpenSciClient.from_environment(environ={"OPENSCI_TOKEN": AGENT, "OPENSCI_API_URL": "http://127.0.0.1:1"})
    assert client.api_url == "http://127.0.0.1:1" and client.token.source == "OPENSCI_TOKEN"
    assert_no_secret(repr(client), AGENT, AGENT[37:])


def test_the_token_travels_only_in_the_authorization_header(stub):
    client = client_for(stub)
    client.token_status()
    request = stub.state.requests[-1]
    assert request["headers"]["Authorization"] == "Bearer " + AGENT
    assert AGENT not in request["path"] + request["query"] and "X-opensci-authorize" not in request["headers"]
    assert request["headers"]["User-Agent"] == f"os-optics/{__version__}"


def test_redirects_are_not_followed(stub, deliveries):
    other = new_state(deliveries=deliveries)
    with StubServer(other) as target:
        stub.state.redirect_to = target.url + "/api/v1/account/token"
        with pytest.raises(OpenSciApiError) as refused:
            client_for(stub).token_status()
        assert refused.value.http_status == 307 and refused.value.code == "UNEXPECTED_RESPONSE"
        assert target.state.requests == []


def test_transient_failures_are_retried_with_the_same_idempotency_key(stub, run):
    stub.state.fail_next.extend([503, 0])
    client = client_for(stub, retries=2)
    submitted = client.submit(run)
    assert submitted["resolution_id"] == RESOLUTION_ID and submitted["replayed"] is False
    posts = [r for r in stub.state.requests if r["path"] == "/api/v1/commercial-submissions"]
    assert len(posts) == 3 and len({r["headers"]["Idempotency-Key"] for r in posts}) == 1
    stub.state.fail_next.append(503)
    with pytest.raises(OpenSciApiError) as unavailable:
        client_for(stub).token_status()
    assert (unavailable.value.http_status, unavailable.value.recovery) == (503, Recovery.RETRY)


def test_an_unreachable_service_is_a_connection_error_without_the_token():
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    client = OpenSciClient(f"http://127.0.0.1:{port}", AGENT, retries=0, timeout=5, environ={})
    with pytest.raises(OpenSciConnectionError) as failed:
        client.token_status()
    error = failed.value
    assert error.code == "SERVICE_UNREACHABLE" and error.recovery is Recovery.RETRY and error.http_status is None
    assert error.__context__ is None and error.__cause__ is None
    rendered = engine_frame_locals(error) + "".join(traceback.format_exception(error))
    assert_no_secret(rendered + repr(error) + json.dumps(error.as_dict()), AGENT, AGENT[37:])


# ------------------------------------------------------------------------------------------ errors


def test_service_errors_are_typed(stub):
    with pytest.raises(OpenSciApiError) as invalid:
        client_for(stub, credential_for("unknown")).token_status()
    assert (invalid.value.code, invalid.value.family, invalid.value.http_status, invalid.value.recovery) == (
        "AUTHORIZATION_INVALID", "UNAUTHENTICATED", 401, Recovery.REQUEST_NEW_TOKEN)
    assert invalid.value.request_id and invalid.value.request_id.startswith("req-")
    with pytest.raises(OpenSciApiError) as scope:
        client_for(stub, REVIEW).create_order(RESOLUTION_ID)
    assert scope.value.recovery is Recovery.REQUEST_SCOPE and scope.value.details == {"required_scope": "commercial:order:place"}
    with pytest.raises(OpenSciApiError) as unknown:
        client_for(stub).resolution("00000000-0000-4000-8000-000000000999")
    assert (unknown.value.code, unknown.value.http_status) == ("RESOLUTION_NOT_FOUND", 404)
    document = unknown.value.as_dict()
    assert set(document) == {"code", "family", "message", "details", "recovery", "hint", "http_status", "request_id"}


def test_the_recovery_is_derived_when_the_service_states_none(stub):
    stub.state.include_recovery = False
    with pytest.raises(OpenSciApiError) as scope:
        client_for(stub, REVIEW).create_order(RESOLUTION_ID)
    assert scope.value.recovery is Recovery.REQUEST_SCOPE
    with pytest.raises(AssemblyDetailNotReleasedError) as detail:
        client_for(stub).assembly_detail(RESOLUTION_ID)
    assert detail.value.recovery is Recovery.CREATE_ORDER_FIRST
    assert derive_recovery(403, "ASSEMBLY_DETAIL_NOT_RELEASED", {"reason": "PAYMENT_NOT_CONFIRMED"}) is \
        Recovery.WAIT_FOR_PAYMENT_CONFIRMATION
    assert derive_recovery(403, "AUTHORIZATION_CONSUMED", {}) is Recovery.REQUEST_NEW_TOKEN
    assert derive_recovery(409, "IDEMPOTENCY_KEY_CONFLICT", {}) is Recovery.USE_NEW_IDEMPOTENCY_KEY
    assert derive_recovery(422, "SUBMISSION_SCHEMA_INVALID", {}) is Recovery.FIX_REQUEST
    assert derive_recovery(500, "INTERNAL_ERROR", {}) is Recovery.CONTACT_OPENSCI
    garbled = error_from_response(502, b"<html>bad gateway</html>", "req-x")
    assert (garbled.code, garbled.recovery, garbled.request_id) == ("UNEXPECTED_RESPONSE", Recovery.RETRY, "req-x")
    unknown = error_from_response(400, json.dumps({"error": {"code": "X", "message": "m", "recovery": "MAKE_TEA"}}).encode(), None)
    assert unknown.recovery is Recovery.FIX_REQUEST


# ------------------------------------------------------------------------------------------ shipping addresses


def test_addresses_are_listed_added_and_chosen_for_an_order(stub):
    client = client_for(stub)
    listed = client.addresses()
    assert listed["default_address_id"] == listed["addresses"][0]["address_id"]
    assert stub.state.requests[-1]["path"] == "/api/v1/account/addresses"
    assert client_for(stub, REVIEW).addresses()["addresses"] == listed["addresses"]  # order:read may list
    fields = {"recipient_name": "李四", "mobile_phone": "13900000000", "province": "北京市", "city": "北京市",
              "district": "海淀区", "street_address": "中关村大街 27 号"}
    added = client.add_address(**fields, label="Home")
    assert added["replayed"] is False and added["address"]["is_default"] is False
    body = json.loads(stub.state.requests[-1]["body"])
    assert body == {**fields, "postal_code": None, "label": "Home", "make_default": False}
    key = stub.state.requests[-1]["headers"]["Idempotency-Key"]
    assert key == idempotency_key_for("shipping-address", canonical_json_bytes(body))
    assert client.add_address(**fields, label="Home")["replayed"] is True  # the same address again: no duplicate
    with pytest.raises(OpenSciApiError) as review:
        client_for(stub, REVIEW).add_address(**fields)
    assert review.value.recovery is Recovery.REQUEST_SCOPE
    with pytest.raises(OpenSciApiError) as invalid:
        client.add_address(**{**fields, "mobile_phone": "not a number"})
    assert invalid.value.code == "SHIPPING_ADDRESS_INVALID" and invalid.value.details == {"invalid_fields": ["mobile_phone"]}

    chosen = added["address"]["address_id"]
    placed = client.create_order(RESOLUTION_ID, shipping_address_id=chosen)
    assert placed["order"]["shipping_address"]["recipient_name"] == "李四"
    assert json.loads(stub.state.requests[-1]["body"]) == {"resolution_id": RESOLUTION_ID, "shipping_address_id": chosen}
    # the key is the resolution's and the address's: the same call again returns the first Order with its address
    key = stub.state.requests[-1]["headers"]["Idempotency-Key"]
    assert key == idempotency_key_for("order", RESOLUTION_ID, "shipping_address_id", chosen) != idempotency_key_for("order", RESOLUTION_ID)
    again = client.create_order(RESOLUTION_ID, shipping_address_id=chosen)
    assert again["replayed"] is True and again["order"]["shipping_address"]["recipient_name"] == "李四"


def test_an_order_without_any_address_names_the_add_address_recovery(deliveries):
    state = new_state(deliveries=deliveries, addresses=[])
    with StubServer(state) as server:
        client = client_for(server)
        assert client.addresses() == {"schema": "opensci.commercial.shipping_address_list", "schema_version": "1",
                                      "default_address_id": None, "max_addresses": 20, "addresses": []}
        with pytest.raises(OpenSciApiError) as refused:
            client.create_order(RESOLUTION_ID)
        assert (refused.value.code, refused.value.http_status, refused.value.recovery) == (
            "SHIPPING_ADDRESS_REQUIRED", 409, Recovery.ADD_SHIPPING_ADDRESS)
        assert "add_address" in refused.value.hint
        server.state.include_recovery = False
        with pytest.raises(OpenSciApiError) as derived:
            client.create_order(RESOLUTION_ID)
        assert derived.value.recovery is Recovery.ADD_SHIPPING_ADDRESS
        client.add_address(recipient_name="张三", mobile_phone="13800138000", province="广东省", city="深圳市",
                           district="南山区", street_address="科技园路 1 号")
        assert client.create_order(RESOLUTION_ID)["order"]["shipping_address"]["city"] == "深圳市"


# ------------------------------------------------------------------------------------------ Account tokens


def test_token_status_issue_list_and_revoke(stub):
    manager = client_for(stub, MANAGER)
    assert manager.token_status()["capabilities"]["manage_tokens"] is True
    issued = manager.issue_token(profile="agent", lifetime_hours=24, label="lab agent")
    assert issued.credential is not None and issued.status["scopes"] == AGENT_SCOPES and issued.status["label"] == "lab agent"
    assert issued.token_id == issued.credential.token_id
    assert_no_secret(repr(issued) + str(issued), issued.credential.reveal())
    request = stub.state.requests[-1]
    assert json.loads(request["body"]) == {"scopes": None, "profile": "agent", "lifetime_hours": 24,
                                            "label": "lab agent", "lifecycle": "TIME_LIMITED"}
    assert request["headers"]["Idempotency-Key"].startswith("opensci-token-issue-")
    replay = manager.issue_token(profile="agent", lifetime_hours=24, label="lab agent",
                                 idempotency_key=request["headers"]["Idempotency-Key"])
    assert replay.credential is None and replay.token_id == issued.token_id
    single_use = manager.issue_token(profile="agent", lifecycle="SINGLE_USE")
    assert json.loads(stub.state.requests[-1]["body"])["lifecycle"] == "SINGLE_USE"
    permanent = manager.issue_token(profile="agent", lifecycle="PERMANENT")
    assert json.loads(stub.state.requests[-1]["body"])["lifecycle"] == "PERMANENT"
    assert single_use.credential is not None and permanent.credential is not None
    second = manager.issue_token(scopes=["commercial:resolve"])
    assert second.credential is not None and second.token_id != issued.token_id
    assert {t["token_id"] for t in manager.tokens()["tokens"]} >= {issued.token_id, second.token_id}
    new_client = client_for(stub, issued.credential.reveal())
    assert new_client.token_status()["status"] == "ACTIVE"
    assert manager.revoke_token(second.token_id)["status"] == "REVOKED"
    assert new_client.revoke_current_token()["status"] == "REVOKED"
    with pytest.raises(OpenSciApiError) as revoked:
        new_client.token_status()
    assert revoked.value.code == "AUTHORIZATION_REVOKED" and revoked.value.recovery is Recovery.REQUEST_NEW_TOKEN
    with pytest.raises(ValueError):
        manager.issue_token(profile="agent", scopes=["commercial:resolve"])
    with pytest.raises(ValueError):
        manager.issue_token(lifetime_hours=721)
    with pytest.raises(ValueError):
        manager.issue_token(lifecycle="FOREVER")
    with pytest.raises(OpenSciApiError) as not_manager:
        client_for(stub).issue_token()
    assert not_manager.value.recovery is Recovery.REQUEST_SCOPE


# ------------------------------------------------------------------------------------------ submission


def test_submissions_are_idempotent_by_content(stub, run):
    client = client_for(stub)
    from_run = submission_document(run)
    from_dir = submission_document(run.artifacts.directory)
    assert canonical_json_bytes(from_run) == canonical_json_bytes(from_dir)
    assert "idempotency_key" not in from_run and from_run["project_hash"] == run.project.content_hash()
    first = client.submit(run)
    again = client.submit(str(run.artifacts.directory))
    assert (first["replayed"], again["replayed"]) == (False, True) and stub.state.submissions == 1
    keys = [r["headers"]["Idempotency-Key"] for r in stub.state.requests if r["path"].endswith("/commercial-submissions")]
    assert keys == [idempotency_key_for("submit", canonical_json_bytes(from_run))] * 2
    constrained = client.submit(run, customer_constraints={"include_breadboard": False})
    assert constrained["replayed"] is False and stub.state.submissions == 2
    assert json.loads(stub.state.requests[-1]["body"])["customer_constraints"] == {"include_breadboard": False}


def test_an_inconsistent_run_directory_is_refused_before_any_request(stub, run, tmp_path):
    import shutil

    copy_dir = tmp_path / "run"
    shutil.copytree(run.artifacts.directory, copy_dir)
    record = copy_dir / "local_validation.json"
    record.write_bytes(record.read_bytes().replace(b'"PASS"', b'"FAIL"', 1))
    with pytest.raises(LocalInputError) as refused:
        client_for(stub).submit(copy_dir)
    assert refused.value.code == "RUN_DIRECTORY_INCONSISTENT" and refused.value.details["problems"]
    assert stub.state.requests == []
    with pytest.raises(TypeError):
        client_for(stub).submit(42)


def test_resolution_reads(stub):
    client = client_for(stub)
    assert client.resolution(RESOLUTION_ID)["resolution_id"] == RESOLUTION_ID
    assert client.resolution_presentation(RESOLUTION_ID, locale="en")["locale"] == "en"
    assert stub.state.requests[-1]["query"] == "locale=en"
    with pytest.raises(ValueError):
        client.resolution_presentation(RESOLUTION_ID, locale="de")
    with pytest.raises(ValueError):
        client.resolution("")


# ------------------------------------------------------------------------------------------ the commercial flow


def test_the_whole_flow_keeps_secrets_out_and_writes_nothing_decrypted(stub, run, deliveries, tmp_path, monkeypatch, caplog, capsys):
    work = tmp_path / "work"
    temp = tmp_path / "temp"
    work.mkdir()
    temp.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("TMP", str(temp))
    monkeypatch.setenv("TEMP", str(temp))
    monkeypatch.setenv("TMPDIR", str(temp))
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    caplog.set_level(logging.DEBUG)
    client = client_for(stub)
    seen: list[str] = [repr(client), str(client)]

    status = client.token_status()
    assert status["capabilities"]["place_order"] is True
    resolution = client.submit(run)
    rid = resolution["resolution_id"]
    preview = client.assembly_preview(rid)
    assert preview.content_hash == load_assembly_preview(PREVIEW_ZIP).content_hash
    validation = client.validate_assembly(preview)
    assert validation.package.package_content_hash == preview.content_hash

    with pytest.raises(AssemblyDetailNotReleasedError) as before_order:
        client.assembly_detail(rid, preview=preview)
    assert before_order.value.recovery is Recovery.CREATE_ORDER_FIRST and before_order.value.http_status == 403
    seen += [str(before_order.value), repr(before_order.value)]

    assert list(inspect.signature(OpenSciClient.create_order).parameters) == [
        "self", "resolution_id", "shipping_address_id", "additional_order", "idempotency_key"]
    placed = client.create_order(rid)
    order = placed["order"]
    assert placed["replayed"] is False and order["payment"]["state"] == "AWAITING_TRANSFER"
    assert order["shipping_address"]["recipient_name"] == "张三"  # the default address
    assert json.loads(stub.state.requests[-1]["body"]) == {"resolution_id": rid}
    assert stub.state.requests[-1]["headers"]["Idempotency-Key"] == idempotency_key_for("order", rid)
    assert client.create_order(rid)["replayed"] is True and len(stub.state.orders) == 1
    assert client.order(order["order_id"])["order_number"] == order["order_number"]
    assert client.orders()["orders"][0]["order_id"] == order["order_id"]
    assert client.orders(locale="zh-CN")["locale"] == "zh-CN"
    for document in ("order", "quotation", "contract"):
        client.order_presentation(order["order_id"], locale="en", document=document)
    assert stub.state.requests[-1]["path"].endswith("/contract/presentation")
    with pytest.raises(ValueError):
        client.order_presentation(order["order_id"], document="invoice")

    with pytest.raises(AssemblyDetailNotReleasedError) as unpaid:
        client.assembly_detail(rid, preview=preview)
    assert unpaid.value.recovery is Recovery.WAIT_FOR_PAYMENT_CONFIRMATION
    reported = client.report_bank_transfer(order["order_id"])
    assert reported["replayed"] is False and reported["order"]["payment"]["state"] == "PENDING_CONFIRMATION"
    assert stub.state.requests[-1]["headers"]["Idempotency-Key"] == idempotency_key_for("bank-transfer-report", order["order_id"])
    assert client.report_bank_transfer(order["order_id"])["replayed"] is True
    with pytest.raises(AssemblyDetailNotReleasedError) as reported_only:
        client.assembly_detail(rid, preview=preview)
    assert reported_only.value.recovery is Recovery.WAIT_FOR_PAYMENT_CONFIRMATION

    stub.state.confirm_payment()
    detail = client.assembly_detail(rid, preview=preview)
    assert detail.content_hash
    drawing = client.assembly_drawing(rid, preview=preview)
    assert set(drawing.files) == DRAWING_FILES
    assert sorted(p.name for p in work.rglob("*")) == [] and sorted(temp.rglob("*")) == []

    # drawings are never uploaded (#557): the client has no upload call and posted no artifact set
    assert not hasattr(client, "upload_assembly_results")
    assert not any(r["path"] == "/api/v1/artifact-sets" and r["method"] == "POST" for r in stub.state.requests)
    # an earlier service still serves artifact sets stored before; they are read back and checked as before
    uploaded = {"drawing": _stored_drawing_set(stub, rid, drawing)}
    listed = client.artifact_sets(rid)["artifact_sets"]
    assert [s["artifact_kind"] for s in listed] == ["ASSEMBLY_DRAWING"]
    drawing_set = client.artifact_set(uploaded["drawing"]["artifact_set_id"])
    for entry in drawing_set["files"]:
        assert client.artifact_file(drawing_set["artifact_set_id"], entry["role"]) == drawing.files[entry["filename"]]
    # the drawing manifest's own role names download its files, and so do the drawing schema 3.0 names
    for entry in drawing.manifest.files:
        assert client.artifact_file(drawing_set["artifact_set_id"], entry.role.value) == drawing.files[entry.path]
    for legacy, path in (("ASSEMBLY_DRAWING_PDF", "assembly_drawing.pdf"), ("ASSEMBLY_DRAWING_SVG", "assembly_drawing.svg")):
        assert client.artifact_file(drawing_set["artifact_set_id"], legacy) == drawing.files[path]
    with pytest.raises(LocalInputError):
        client.artifact_file(drawing_set["artifact_set_id"], "NOT_A_ROLE")
    role = drawing_set["files"][0]["role"]
    stub.state.artifact_files[(drawing_set["artifact_set_id"], role)] += b"tampered"
    with pytest.raises(ArtifactIntegrityError):
        client.artifact_file(drawing_set["artifact_set_id"], role)

    out = tmp_path / "drawing"
    written = client.assembly_drawing(rid, out, preview=preview)
    assert sorted(p.name for p in out.iterdir()) == sorted(DRAWING_FILES) and dict(written.files) == dict(drawing.files)
    assert sorted(p.name for p in work.rglob("*")) == [] and sorted(temp.rglob("*")) == []

    seen += [repr(preview), repr(detail), repr(drawing), json.dumps(uploaded), caplog.text]
    captured = capsys.readouterr()
    seen += [captured.out, captured.err]
    assert "GET /api/v1/account/token -> 200" in caplog.text
    assert_no_secret("\n".join(seen), AGENT, AGENT[37:], *secrets_of(deliveries))


def _stored_drawing_set(stub, resolution_id: str, drawing) -> dict:
    """An artifact set an earlier service stored for ``drawing`` (seeded into the stub: the client no longer uploads)."""

    import hashlib

    set_id = "00000000-0000-4000-8000-000000000001"
    files = [{"role": entry.role.value, "filename": entry.path, "size_bytes": len(drawing.files[entry.path]),
              "sha256": hashlib.sha256(drawing.files[entry.path]).hexdigest()} for entry in drawing.manifest.files]
    view = {"schema": "opensci.client_assembly_artifact_set", "schema_version": "1.0", "artifact_set_id": set_id,
            "artifact_kind": "ASSEMBLY_DRAWING", "commercial_resolution_id": resolution_id,
            "artifact_set_digest": "0" * 64, "files": files, "replayed": False}
    stub.state.artifact_sets[set_id] = view
    for entry in drawing.manifest.files:
        stub.state.artifact_files[(set_id, entry.role.value)] = drawing.files[entry.path]
    return view


def test_the_client_has_no_upload_path(stub):
    """Uploads of local assembly validations and drawings are retired (#557): no client call posts an artifact set."""

    import inspect

    from os_optics.client import api

    client = client_for(stub)
    assert not [name for name in dir(client) if "upload" in name]
    assert '"POST", "/artifact-sets"' not in inspect.getsource(api)
    assert "encode_multipart" not in inspect.getsource(api)


def test_a_delivery_that_does_not_open_is_a_protected_content_error(stub, deliveries):
    from datetime import UTC, datetime, timedelta

    stub.state.deliveries = sealed_reference_deliveries(now=datetime.now(UTC) - timedelta(hours=2))
    with pytest.raises(ProtectedContentError) as expired:
        client_for(stub).assembly_preview(RESOLUTION_ID)
    error = expired.value
    assert (error.code, error.family, error.recovery) == ("GRANT_EXPIRED", "PROTECTED_CONTENT", Recovery.REQUEST_NEW_DELIVERY)
    assert error.__context__ is None and error.__cause__ is None
    assert "os_optics" in "".join(traceback.format_exception(error))
    assert_no_secret(engine_frame_locals(error) + repr(error) + "".join(traceback.format_exception(error)),
                     *secrets_of(stub.state.deliveries), AGENT, AGENT[37:])
    stub.state.deliveries = (deliveries[1], deliveries[0])  # the other kind: refused, never opened as a preview
    with pytest.raises(ProtectedContentError) as wrong_kind:
        client_for(stub).assembly_preview(RESOLUTION_ID)
    assert wrong_kind.value.code == "ENVELOPE_MALFORMED"


def test_the_client_module_needs_no_extra_dependency():
    code = ("import sys; import os_optics.client as c; "
            "assert 'cryptography' not in sys.modules and 'os_optics.assembly_drawing' not in sys.modules; "
            "print(sorted(m.split('.')[0] for m in sys.modules if m.split('.')[0] in "
            "{'requests','httpx','urllib3','aiohttp'}))")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "[]"


def test_protected_delivery_documents_never_show_the_grant(deliveries):
    preview_doc, detail_doc = deliveries
    key = preview_doc["grant"]["key"]
    held = ProtectedDelivery(json.dumps(preview_doc).encode("utf-8"))
    assert (held.delivery, held.commercial_resolution_id) == ("ASSEMBLY_PREVIEW", RESOLUTION_ID)
    assert_no_secret(repr(held) + str(held) + json.dumps(held.redacted()), key, preview_doc["envelope"]["ciphertext"])
    assert held.redacted()["grant"]["key"] == "<redacted>" and held.redacted()["grant"]["grant_id"] == preview_doc["grant"]["grant_id"]
    for dump in (pickle.dumps, copy.copy, copy.deepcopy):
        with pytest.raises(TypeError):
            dump(held)
    held.discard()
    assert held.discarded and "discarded" in repr(held)


def test_an_order_cancelled_by_opensci_is_ordered_again_as_a_new_order(stub):
    client = client_for(stub)
    first = client.create_order(RESOLUTION_ID)["order"]
    assert client.create_order(RESOLUTION_ID)["order"]["order_id"] == first["order_id"]
    stub.state.orders[first["order_id"]]["payment"]["state"] = "CANCELLED"
    again = client.create_order(RESOLUTION_ID)
    assert again["replayed"] is False and again["order"]["order_id"] != first["order_id"]
    assert stub.state.requests[-1]["headers"]["Idempotency-Key"] == idempotency_key_for(
        "order", RESOLUTION_ID, first["order_id"])
    repeated = client.create_order(RESOLUTION_ID)
    assert repeated["replayed"] is True and repeated["order"]["order_id"] == again["order"]["order_id"]
    assert len(stub.state.orders) == 2


def test_an_unpaid_order_is_withdrawn_and_is_gone_afterwards(stub):
    client = client_for(stub)
    order = client.create_order(RESOLUTION_ID)["order"]
    with pytest.raises(OpenSciApiError) as scope:
        client_for(stub, REVIEW).withdraw_order(order["order_id"])
    assert scope.value.recovery is Recovery.REQUEST_SCOPE
    withdrawn = client.withdraw_order(order["order_id"])
    assert (withdrawn["order_status"], withdrawn["order_number"]) == ("WITHDRAWN", order["order_number"])
    request = stub.state.requests[-1]
    assert (request["method"], request["path"]) == ("POST", f"/api/v1/orders/{order['order_id']}/withdrawal")
    assert "Idempotency-Key" not in request["headers"]
    # the Order no longer exists: repeating the call is ORDER_NOT_FOUND (404)
    with pytest.raises(OpenSciApiError) as gone:
        client.withdraw_order(order["order_id"])
    assert (gone.value.code, gone.value.http_status, gone.value.recovery) == ("ORDER_NOT_FOUND", 404, Recovery.FIX_REQUEST)
    with pytest.raises(ValueError):
        client.withdraw_order("")


def test_an_order_with_a_reported_transfer_is_not_withdrawable(stub):
    client = client_for(stub)
    order = client.create_order(RESOLUTION_ID)["order"]
    client.report_bank_transfer(order["order_id"])
    with pytest.raises(OpenSciApiError) as refused:
        client.withdraw_order(order["order_id"])
    assert (refused.value.code, refused.value.http_status, refused.value.recovery) == (
        "ORDER_NOT_WITHDRAWABLE", 409, Recovery.CONTACT_OPENSCI)
    assert refused.value.hint


# ------------------------------------------------------------------------------------------ feedback


FEEDBACK = {"category": "PROBLEM", "summary": "order create refused after resolve",
            "details": "The user wanted to order the folded design. I ran 'opensci-engine order create RID'; it was refused "
                       "with SHIPPING_ADDRESS_REQUIRED although the address book was not empty."}


def test_feedback_is_sent_with_any_token_and_a_repeat_replays(stub):
    from os_optics import __version__
    from os_optics.client import runtime_label

    client = client_for(stub, REVIEW)  # any Account token: the Review profile carries no feedback scope
    receipt = client.submit_feedback(**{**FEEDBACK, "summary": "  " + FEEDBACK["summary"] + "  \n"},
                                     context={"error_code": "SHIPPING_ADDRESS_REQUIRED", "request_ids": ["req-0007"],
                                              "resolution_id": RESOLUTION_ID, "locale": "en"})
    assert set(receipt) == {"feedback_id", "received_at", "status", "replayed"}
    assert receipt["status"] == "RECEIVED" and receipt["replayed"] is False
    request = stub.state.requests[-1]
    assert (request["method"], request["path"]) == ("POST", "/api/v1/feedback")
    body = json.loads(request["body"])
    assert body == {**FEEDBACK, "context": {"engine_version": __version__, "runtime": runtime_label(),
                                            "error_code": "SHIPPING_ADDRESS_REQUIRED", "request_ids": ["req-0007"],
                                            "resolution_id": RESOLUTION_ID, "locale": "en"}}
    assert runtime_label().startswith("python 3.")
    assert request["headers"]["Idempotency-Key"] == idempotency_key_for("feedback", canonical_json_bytes(body))
    again = client.submit_feedback(**FEEDBACK, context={"error_code": "SHIPPING_ADDRESS_REQUIRED", "locale": "en",
                                                        "request_ids": ["req-0007"], "resolution_id": RESOLUTION_ID})
    assert again == {**receipt, "replayed": True} and len(stub.state.feedback) == 1
    assert stub.state.feedback[0]["token_id"] == client.token.token_id
    other = client_for(stub).submit_feedback(category="FEATURE_REQUEST", summary="a zh-CN drawing legend",
                                             details="The user reads Chinese only.")
    assert other["replayed"] is False and other["feedback_id"] != receipt["feedback_id"]


def test_feedback_names_the_agent_from_the_environment(stub):
    client = OpenSciClient(stub.url, AGENT, retries=0, environ={"OPENSCI_AGENT_NAME": " Lab Copilot "})
    client.submit_feedback(**FEEDBACK)
    assert json.loads(stub.state.requests[-1]["body"])["context"]["agent"] == "Lab Copilot"
    client.submit_feedback(**FEEDBACK, context={"agent": "Other Agent"})  # a given agent wins
    assert json.loads(stub.state.requests[-1]["body"])["context"]["agent"] == "Other Agent"
    with pytest.raises(ValueError, match="OPENSCI_AGENT_NAME"):
        OpenSciClient(stub.url, AGENT, environ={"OPENSCI_AGENT_NAME": "x" * 81}).submit_feedback(**FEEDBACK)


@pytest.mark.parametrize("change, message", [
    ({"category": "BUG"}, "category"),
    ({"summary": "abc"}, "summary must be 5 to 200"),
    ({"summary": "x" * 201}, "summary must be 5 to 200"),
    ({"summary": "first line" + chr(10) + "second line"}, "summary must be one line"),
    ({"details": "bell " + chr(7) + " here"}, "details must not contain control characters"),
    ({"details": "  \n"}, "details must be 1 to 8000"),
    ({"details": "x" * 8001}, "details must be 1 to 8000"),
    ({"context": {"colour": "blue"}}, "unknown feedback context fields: colour"),
    ({"context": {"resolution_id": "not-a-uuid"}}, "context.resolution_id must be a UUID"),
    ({"context": {"command": "x" * 501}}, "context.command"),
    ({"context": {"locale": "fr"}}, "context.locale"),
    ({"context": {"request_ids": [f"req-{i}" for i in range(11)]}}, "context.request_ids"),
    ({"context": {"request_ids": "req-1"}}, "context.request_ids"),
    ({"details": f"my token is {AGENT}, it fails"}, "contains an OpenSci Account token (details)"),
    ({"context": {"command": f"OPENSCI_TOKEN={AGENT.upper().replace('OAT_', 'oat_')} os-optics resolve run"}},
     "contains an OpenSci Account token (context.command)"),
])
def test_feedback_is_checked_before_anything_is_sent(stub, change, message):
    with pytest.raises(ValueError) as refused:
        client_for(stub).submit_feedback(**{**FEEDBACK, **change})
    assert message in str(refused.value) and AGENT[37:] not in str(refused.value).lower()
    assert stub.state.requests == []


def test_feedback_refusals_are_typed_and_a_rate_limit_is_not_repeated(stub):
    client = client_for(stub, retries=3)
    with pytest.raises(OpenSciApiError) as invalid:  # credential-shaped text the client does not know: the service refuses
        client.submit_feedback(**{**FEEDBACK, "details": "the staff page showed oss_abcdefghijklmnopqrstuvwx"})
    assert (invalid.value.code, invalid.value.http_status, invalid.value.recovery) == (
        "FEEDBACK_INVALID", 422, Recovery.FIX_REQUEST)
    assert invalid.value.details == {"reason": "CONTAINS_CREDENTIAL", "invalid_fields": ["details"]}
    assert "retry_after_seconds" not in invalid.value.as_dict() and stub.state.feedback == []

    first = client.submit_feedback(**FEEDBACK)
    stub.state.feedback_rate_limited = True
    sent = len(stub.state.requests)
    with pytest.raises(OpenSciApiError) as limited:
        client.submit_feedback(**{**FEEDBACK, "summary": "another problem"})
    error = limited.value
    assert (error.code, error.family, error.http_status, error.recovery) == (
        "FEEDBACK_RATE_LIMITED", "RATE_LIMITED", 429, Recovery.RETRY_LATER)
    assert error.retry_after_seconds == 1800 and error.as_dict()["retry_after_seconds"] == 1800
    assert "rate limit" in error.hint and len(stub.state.requests) == sent + 1  # not repeated by the client
    assert client.submit_feedback(**FEEDBACK) == {**first, "replayed": True}  # a replay is not rate limited

    stub.state.include_recovery = False
    with pytest.raises(OpenSciApiError) as derived:
        client.submit_feedback(**{**FEEDBACK, "summary": "a third problem"})
    assert derived.value.recovery is Recovery.RETRY_LATER
    assert derive_recovery(429, "SOMETHING_NEW", {}) is Recovery.RETRY_LATER
    assert derive_recovery(422, "FEEDBACK_INVALID", {"reason": "CONTAINS_CREDENTIAL"}) is Recovery.FIX_REQUEST


def test_an_additional_order_is_explicit_and_keyed_to_the_orders_seen(stub):
    """``additional_order`` sends ``additional_order: true`` and derives its key from the resolution, the
    marker and the Orders of the design known now (the resolution's ``design_orders``), like the Designer's checkout:
    a retry replays, while the key differs from the first Order's."""

    from os_optics.local.submission import idempotency_key_for

    client = client_for(stub)
    first = client.create_order(RESOLUTION_ID)
    def key_of(request):
        return next(v for k, v in request["headers"].items() if k.lower() == "idempotency-key")

    first_key = key_of(stub.state.requests[-1])
    again = client.create_order(RESOLUTION_ID, additional_order=True)
    request = stub.state.requests[-1]
    assert json.loads(request["body"]) == {"resolution_id": RESOLUTION_ID, "additional_order": True}
    seen = sorted(str(o["order_id"]) for o in client.resolution(RESOLUTION_ID).get("design_orders", ()))
    assert key_of(request) == idempotency_key_for("order", RESOLUTION_ID, "additional_order", ",".join(seen))
    assert key_of(request) != first_key
    assert first["order"]["order_id"]


def test_an_additional_order_retried_after_a_lost_answer_replays(stub, monkeypatch):
    """The Orders an additional Order is added to leave out an unpaid Order of the same resolution -- the one a lost
    answer created -- so the retry's key is the first call's; once that Order is paid, a further Order gets a new key."""

    client = client_for(stub)
    other = {"order_id": "o-other", "same_resolution": False, "payment_state": "CONFIRMED"}
    created = {"order_id": "o-new", "same_resolution": True, "payment_state": "AWAITING_TRANSFER"}
    states = [[other], [other, created], [other, {**created, "payment_state": "CONFIRMED"}]]
    seen = []
    for design_orders in states:
        monkeypatch.setattr(client, "resolution", lambda _rid, orders=design_orders: {"design_orders": orders})
        seen.append(client._orders_before(RESOLUTION_ID))
    assert seen == [["o-other"], ["o-other"], ["o-new", "o-other"]]
