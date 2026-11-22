import pytest

from datasifter.directory.ldif import (
    LDIFModification,
    LDIFRecord,
    LDIFValidationError,
    parse_ldif,
    read_ldif,
    serialize_ldif,
    write_ldif,
)
from datasifter.directory.service import LDAPImportError, LDAPOperationError, LDAPService


def test_parse_ldif_content_and_all_change_record_types():
    records = parse_ldif(
        """version: 1

# content entry
dn: cn=Alice,ou=People,dc=example,dc=com
objectClass: top
objectClass: person
cn: Alice
sn: Example
description: This is a folded
 line of text

dn: cn=Alice,ou=People,dc=example,dc=com
changetype: modify
add: description
description: Added detail
-
delete: obsoleteAttribute
-
replace: cn
cn: Alice Example
-

dn: cn=Old,ou=People,dc=example,dc=com
changetype: delete

dn: cn=Old,ou=People,dc=example,dc=com
changetype: modrdn
newrdn: cn=New
deleteoldrdn: 1
newsuperior: ou=Staff,dc=example,dc=com
"""
    )

    assert len(records) == 4
    assert records[0].change_type == "add"
    assert records[0].attributes["objectClass"] == ["top", "person"]
    assert records[0].attributes["description"] == ["This is a foldedline of text"]
    assert records[1].modifications == (
        LDIFModification("add", "description", ("Added detail",)),
        LDIFModification("delete", "obsoleteAttribute", ()),
        LDIFModification("replace", "cn", ("Alice Example",)),
    )
    assert records[2].change_type == "delete"
    assert records[3].change_type == "modrdn"
    assert records[3].new_rdn == "cn=New"
    assert records[3].delete_old_rdn is True
    assert records[3].new_superior == "ou=Staff,dc=example,dc=com"


@pytest.mark.parametrize(
    "content",
    [
        "cn: Alice\n",
        "dn: not-a-distinguished-name\ncn: Alice\n",
        "dn: cn=Alice,dc=example\ncn: Alice\ndn: cn=Bob,dc=example\n",
        "dn: cn=Alice,dc=example\ncn: Alice\n",
        "dn: cn=Alice,dc=example\ncn:: not-base64!\n",
        "dn: cn=Alice,dc=example\ncn:< file:///etc/passwd\n",
        "dn: cn=Alice,dc=example\ncn: :leading-colon\n",
        "dn: cn=Alice,dc=example\ncn: <leading-angle\n",
        "dn: cn=Alice,dc=example\ncn: non-ASCII Jöhn\n",
        "dn: cn=Alice,dc=example\nobjectClass:\ncn: Alice\n",
        "dn: cn=Alice,dc=example\nchangetype: delete\ncn: Alice\n",
        "dn: cn=Alice,dc=example\nchangetype: modify\nadd: cn\n-\n",
        "dn: cn=Alice,dc=example\nchangetype: modify\nreplace: cn\ncn: Alice\n",
        "dn: cn=Alice,dc=example\nchangetype: modrdn\nnewrdn: ou=One,dc=example\ndeleteoldrdn: 1\n",
        "dn: cn=Alice,dc=example\ncontrol: 1.2.3 true\nchangetype: delete\n",
        "dn: cn=Alice,dc=example\nchangetype: unsupported\n",
    ],
)
def test_parse_ldif_rejects_malformed_or_unsupported_records(content):
    with pytest.raises(LDIFValidationError):
        parse_ldif(content)


def test_ldif_round_trip_preserves_binary_special_and_long_values():
    record = LDIFRecord(
        dn="cn=Jöhn,ou=People,dc=example,dc=com",
        change_type="add",
        attributes={
            "objectClass": ["top", "person"],
            "cn": [" leading-space", "colon:value", "<angle", "trailing "],
            "jpegPhoto": [b"\x00\xff\x10"],
            "description": ["x" * 180],
        },
    )

    serialized = serialize_ldif([record])
    parsed = parse_ldif(serialized)

    assert parsed[0].attributes["cn"] == [
        b" leading-space",
        "colon:value",
        b"<angle",
        b"trailing ",
    ]
    assert parsed[0].attributes["jpegPhoto"] == [b"\x00\xff\x10"]
    assert parsed[0].attributes["description"] == ["x" * 180]
    assert any(line.startswith(" ") for line in serialized.splitlines())
    assert "jpegPhoto:: AP8Q" in serialized
    assert "cn::" in serialized


def test_read_ldif_accepts_utf8_bom_and_write_is_atomic_and_validated(tmp_path):
    destination = tmp_path / "entry.ldif"
    destination.write_text("existing", encoding="utf-8")
    record = LDIFRecord(
        dn="cn=Alice,dc=example",
        change_type="add",
        attributes={"objectClass": ["top"], "cn": ["Alice"]},
    )

    write_ldif(destination, [record])

    assert read_ldif(destination) == [record]
    destination.write_text("\ufeff" + destination.read_text(encoding="utf-8"), encoding="utf-8")
    assert read_ldif(destination) == [record]

    invalid = LDIFRecord(
        dn="not-a-dn",
        change_type="add",
        attributes={"objectClass": ["top"]},
    )
    with pytest.raises(LDIFValidationError):
        write_ldif(destination, [invalid])
    assert read_ldif(destination) == [record]
    assert not list(tmp_path.glob("*.tmp"))


def test_apply_ldif_records_runs_in_file_order_and_reports_partial_failure(monkeypatch):
    service = LDAPService.__new__(LDAPService)
    service.profile = type("Profile", (), {"read_only": False})()
    records = [
        LDIFRecord(
            dn="cn=Alice,dc=example",
            change_type="add",
            attributes={"objectClass": ["top"]},
        ),
        LDIFRecord(dn="cn=Alice,dc=example", change_type="delete"),
        LDIFRecord(dn="cn=Bob,dc=example", change_type="delete"),
    ]
    calls = []

    monkeypatch.setattr(
        service,
        "add_entry",
        lambda dn, attributes, _cancelled: calls.append(("add", dn, attributes)),
    )

    def fail_delete(dn, cancelled=None):
        calls.append(("delete", dn))
        if dn == "cn=Bob,dc=example":
            raise LDAPOperationError("directory rejected delete")

    monkeypatch.setattr(service, "delete_entry", fail_delete)

    with pytest.raises(LDAPImportError, match="2 complete record\\(s\\) were already applied"):
        service.apply_ldif_records(records)

    assert calls == [
        ("add", "cn=Alice,dc=example", {"objectClass": ["top"]}),
        ("delete", "cn=Alice,dc=example"),
        ("delete", "cn=Bob,dc=example"),
    ]


def test_apply_ldif_records_supports_add_modify_delete_and_rename(monkeypatch):
    service = LDAPService.__new__(LDAPService)
    service.profile = type("Profile", (), {"read_only": False})()
    calls = []

    monkeypatch.setattr(
        service,
        "add_entry",
        lambda dn, attributes, _cancelled: calls.append(("add", dn, attributes)),
    )
    monkeypatch.setattr(
        service,
        "modify_entry_ordered",
        lambda dn, modifications, _cancelled: calls.append(
            ("modify", dn, modifications)
        ),
    )
    monkeypatch.setattr(
        service,
        "delete_entry",
        lambda dn, cancelled=None: calls.append(("delete", dn)),
    )
    monkeypatch.setattr(
        service,
        "move_entry",
        lambda dn, relative_dn, **kwargs: calls.append(
            ("modrdn", dn, relative_dn, kwargs)
        ),
    )
    records = [
        LDIFRecord(
            dn="cn=Alice,dc=example",
            change_type="add",
            attributes={"objectClass": ["top"]},
        ),
        LDIFRecord(
            dn="cn=Alice,dc=example",
            change_type="modify",
            modifications=(LDIFModification("replace", "cn", ("Alice A",)),),
        ),
        LDIFRecord(dn="cn=Old,dc=example", change_type="delete"),
        LDIFRecord(
            dn="cn=Old,dc=example",
            change_type="modrdn",
            new_rdn="cn=New",
            delete_old_rdn=False,
            new_superior="ou=People,dc=example",
        ),
    ]

    assert service.apply_ldif_records(records) == 4
    assert [call[0] for call in calls] == ["add", "modify", "delete", "modrdn"]
    assert calls[-1] == (
        "modrdn",
        "cn=Old,dc=example",
        "cn=New",
        {"new_superior": "ou=People,dc=example", "delete_old_rdn": False, "cancelled": None},
    )


def test_apply_ldif_records_respects_read_only_profile():
    from datasifter.config import DirectoryProfile

    service = LDAPService(
        DirectoryProfile(name="read-only", host="ldap.test", read_only=True),
        None,
    )
    record = LDIFRecord(
        dn="cn=Alice,dc=example",
        change_type="add",
        attributes={"objectClass": ["top"]},
    )

    with pytest.raises(LDAPOperationError, match="read-only"):
        service.apply_ldif_records([record])


def test_modify_entry_ordered_preserves_operations(monkeypatch):
    from contextlib import contextmanager

    from datasifter.directory.service import MODIFY_ADD, MODIFY_REPLACE

    service = LDAPService.__new__(LDAPService)
    service.profile = type("Profile", (), {"read_only": False})()

    class FakeConnection:
        def __init__(self):
            self.changes = []

        def modify(self, dn, changes):
            self.changes.append((dn, changes))
            return True

    connection = FakeConnection()

    @contextmanager
    def fake_connection(*_args, **_kwargs):
        yield connection

    monkeypatch.setattr(service, "_connection", fake_connection)
    service.modify_entry_ordered(
        "cn=Alice,dc=example",
        [
            LDIFModification("add", "description", ("first",)),
            LDIFModification("replace", "cn", ("Alice Example",)),
        ],
    )

    assert connection.changes == [
        ("cn=Alice,dc=example", {"description": [(MODIFY_ADD, ["first"])]}),
        ("cn=Alice,dc=example", {"cn": [(MODIFY_REPLACE, ["Alice Example"])]}),
    ]
