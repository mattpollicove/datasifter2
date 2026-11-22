import json

import pytest
from datalens_support import ADA

from datasifter.datalens import (
    AttributeChange,
    AuditLogError,
    ConfirmationRequiredError,
    DataLensError,
    DataLensMutationService,
    DeletePlan,
    DirectoryResolver,
    EntryKind,
    LocalAuditLog,
    MovePlan,
    PreparedMutation,
    PreviewChangedError,
    UpdatePlan,
)


def _delete_preview(adapter, dn=ADA):
    plan = DeletePlan(dn=dn)
    return PreparedMutation(plan=plan, preview=adapter.execute(plan).preview)


def _events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_confirmation_and_audit_of_applied_change(directory, tmp_path):
    adapter = directory.adapter()
    audit_path = tmp_path / "audit.jsonl"
    service = DataLensMutationService(adapter, LocalAuditLog(audit_path))
    prepared = _delete_preview(adapter)

    with pytest.raises(ConfirmationRequiredError):
        service.apply(prepared)
    assert directory.read(ADA) is not None
    assert not audit_path.exists()

    result = service.apply(prepared, confirmed=True)
    assert result.applied and directory.read(ADA) is None
    events = _events(audit_path)
    assert [event["outcome"] for event in events] == ["attempted", "applied"]
    assert events[0]["event_id"] == events[1]["event_id"]
    assert ADA not in audit_path.read_text(encoding="utf-8")
    assert "ada@example.com" not in audit_path.read_text(encoding="utf-8")


def test_create_review_handles_server_generated_naming_attribute(directory, tmp_path):
    adapter = directory.adapter()
    resolver = DirectoryResolver(adapter)
    details = (
        {"sam account name": "newlensuser"}
        if directory.backend.value == "active_directory"
        else {"last name": "User"}
    )
    prepared = resolver.prepare_create(
        EntryKind.USER, "New Lens User", container="ou=People,dc=test", attributes=details
    )
    assert prepared.ready
    result = DataLensMutationService(adapter, LocalAuditLog(tmp_path / "audit")).apply(
        prepared, confirmed=True
    )
    assert result.applied
    assert directory.read("cn=New Lens User,ou=People,dc=test") is not None


def test_changed_preview_blocks_execution(directory, tmp_path):
    adapter = directory.adapter()
    prepared = _delete_preview(adapter)
    adapter.update_entry(
        ADA, [AttributeChange(attribute="description", operation="replace", values=("changed",))],
        dry_run=False, confirmed=True,
    )
    audit_path = tmp_path / "audit"
    with pytest.raises(PreviewChangedError):
        DataLensMutationService(adapter, LocalAuditLog(audit_path)).apply(prepared, confirmed=True)
    assert directory.read(ADA) is not None
    assert [event["outcome"] for event in _events(audit_path)] == [
        "attempted", "failed_or_unknown"
    ]


def test_plan_must_match_reviewed_preview(directory, tmp_path):
    adapter = directory.adapter()
    prepared = _delete_preview(adapter)
    other_plan = DeletePlan(dn="cn=Deep,ou=Team,ou=People,dc=test")
    altered = prepared.model_copy(update={"plan": other_plan})
    with pytest.raises(PreviewChangedError):
        DataLensMutationService(adapter, LocalAuditLog(tmp_path / "audit")).apply(
            altered, confirmed=True
        )
    assert directory.read(ADA) is not None
    assert directory.read(other_plan.dn) is not None


def test_read_only_preview_never_applies(directory, tmp_path):
    adapter = directory.adapter(profile_read_only=True)
    prepared = _delete_preview(adapter)
    assert not prepared.ready
    with pytest.raises(DataLensError, match="valid mutation preview"):
        DataLensMutationService(adapter, LocalAuditLog(tmp_path / "audit")).apply(
            prepared, confirmed=True
        )
    assert directory.read(ADA) is not None


def test_unwritable_audit_blocks_mutation(directory, tmp_path):
    adapter = directory.adapter()
    invalid_parent = tmp_path / "not-a-directory"
    invalid_parent.write_text("x", encoding="utf-8")
    service = DataLensMutationService(adapter, LocalAuditLog(invalid_parent / "audit"))
    with pytest.raises(AuditLogError, match="persist"):
        service.apply(_delete_preview(adapter), confirmed=True)
    assert directory.read(ADA) is not None


def test_failed_final_audit_explicitly_reports_applied_change(directory, tmp_path, monkeypatch):
    adapter = directory.adapter()
    audit = LocalAuditLog(tmp_path / "audit")
    original = audit.record

    def fail_final(event_id, operation, backend, outcome):
        if outcome == "applied":
            raise AuditLogError("simulated failure")
        original(event_id, operation, backend, outcome)

    monkeypatch.setattr(audit, "record", fail_final)
    with pytest.raises(AuditLogError, match="change was applied"):
        DataLensMutationService(adapter, audit).apply(_delete_preview(adapter), confirmed=True)
    assert directory.read(ADA) is None


@pytest.mark.parametrize(
    "plan",
    [
        UpdatePlan(
            dn=ADA,
            changes=(AttributeChange(attribute="description", operation="replace", values=("new",)),),
        ),
        MovePlan(dn=ADA, new_rdn="cn=Renamed"),
    ],
)
def test_update_and_move_use_confirmed_preview(directory, tmp_path, plan):
    adapter = directory.adapter()
    prepared = PreparedMutation(plan=plan, preview=adapter.execute(plan).preview)
    assert prepared.ready
    result = DataLensMutationService(adapter, LocalAuditLog(tmp_path / "audit")).apply(
        prepared, confirmed=True
    )
    assert result.applied
    if plan.kind == "move":
        assert directory.read("cn=Renamed,ou=People,dc=test") is not None
    else:
        assert directory.read(ADA)["description"] == [b"new"]


def test_recursive_delete_submits_only_reviewed_dns(directory, tmp_path, monkeypatch):
    adapter = directory.adapter()
    plan = DeletePlan(dn="ou=Team,ou=People,dc=test", recursive=True)
    prepared = PreparedMutation(plan=plan, preview=adapter.execute(plan).preview)
    assert prepared.ready
    calls = []
    original = adapter._service.delete_entry

    def delete(dn, recursive=False, cancelled=None):
        calls.append((dn.casefold(), recursive))
        original(dn, recursive=recursive, cancelled=cancelled)

    monkeypatch.setattr(adapter._service, "delete_entry", delete)
    DataLensMutationService(adapter, LocalAuditLog(tmp_path / "audit")).apply(
        prepared, confirmed=True
    )
    assert calls == [(dn.casefold(), False) for dn in prepared.preview.affected_dns]
