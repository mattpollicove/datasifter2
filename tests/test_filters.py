import pytest

from datasifter.directory.filters import (
    FilterSyntaxError,
    schema_context,
    structured_filter,
    validate_filter,
)


@pytest.mark.parametrize(
    "expression",
    [
        "(objectClass=person)",
        "(&(objectClass=person)(uid=jdoe))",
        "(!(uid=*))",
        "(uid>=100)",
        "(cn:caseExactMatch:=Jane)",
        "(:1.2.3.4:=value)",
        r"(cn=Jane\20Doe)",
        "(cn=)",
    ],
)
def test_accepts_valid_filter_structures(expression):
    validate_filter(expression)


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "objectClass=person",
        "(&(objectClass=person))tail",
        "(&(objectClass=person)",
        "(&)",
        "(:dn:=value)",
        "(!(uid=*)(cn=Jane))",
        "(invalid attr=value)",
        r"(cn=bad\z0)",
    ],
)
def test_rejects_invalid_filter_structures(expression):
    with pytest.raises(FilterSyntaxError):
        validate_filter(expression)


def test_schema_context_contains_only_structural_metadata():
    context = schema_context(["person", "top"], ["cn", "uid"])

    assert "person" in context
    assert "uid" in context
    assert "Jane Doe" not in context


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ("show all entries with lastname starting with the letter b", "(sn=b*)"),
        ("last name starts with B", "(sn=B*)"),
        ("surname beginning with letter b", "(sn=b*)"),
        ("sn begins with the letters Mc", "(sn=Mc*)"),
        ("first name that starts with the letter J", "(givenName=J*)"),
        ("sn ending with the letters son", "(sn=*son)"),
        ("sn ends in the letter b.", "(sn=*b)"),
        ("sn start with b", "(sn=b*)"),
        ("sn begin with b", "(sn=b*)"),
        ("cn contains the word Smith", "(cn=*Smith*)"),
        ("telephone number containing the digits 732", "(telephoneNumber=*732*)"),
        ('cn contains "Jane Doe"', "(cn=*Jane Doe*)"),
        ("cn contains 'Jane Doe'", "(cn=*Jane Doe*)"),
        ('cn contains "Research and Development"', "(cn=*Research and Development*)"),
        ('cn contains "phone"', "(cn=*phone*)"),
        ('cn contains "a*(b)\\\\c"', r"(cn=*a\2a\28b\29\5c\5cc*)"),
        ('cn contains "staff in ou Sales"', "(cn=*staff in ou Sales*)"),
        ("sn or givenName starts with the letter b", "(|(sn=b*)(givenName=b*))"),
        ("sn starts with b in ou People", "(sn=b*)"),
        ("Show entries whose LAST NAME is starting with the letter B!", "(sn=B*)"),
        ("sn starts with the letter \u00c9", "(sn=\u00c9*)"),
        ('cn contains "staff in ou Sales" in ou People', "(cn=*staff in ou Sales*)"),
        ("cn contains phone", "(cn=*phone*)"),
        ("sn with cn", "(sn=*cn*)"),
    ],
)
def test_search_phrasing_preserves_the_complete_value(request_text, expected):
    attributes = ["sn", "givenName", "cn", "telephoneNumber"]

    result = structured_filter(request_text, attributes)

    assert result == expected
    validate_filter(result)


@pytest.mark.parametrize(
    "request_text",
    [
        "sn starts with b extra condition",
        "sn starts with b or c",
        "sn starts with b and mail contains example",
        "sn does not start with b",
        "sn starts with the letter",
        "sn starts with",
        'cn contains "Jane Doe',
        'cn contains "Jane Doe" extra condition',
        "unknownAttribute starts with b",
        "sn or unknownAttribute starts with b",
        "sn starts with b in ou People with mail example",
        "sn with b extra condition",
        "sn or unknownAttribute with b",
        'cn contains ""',
    ],
)
def test_ambiguous_search_phrasing_falls_back_to_the_model(request_text):
    assert structured_filter(request_text, ["sn", "cn", "mail"]) is None


def test_relevant_schema_keeps_core_and_request_matches():
    from datasifter.directory.filters import relevant_schema

    classes, attributes = relevant_schema(
        "show entries with phone number including 732 in ou people",
        ["inetOrgPerson", "ds-cfg-backend", "person"],
        ["cn", "telephoneNumber", "homePhone", "mobile", "ds-cfg-unrelated", "uid"],
    )
    assert classes == ["person", "inetOrgPerson"]
    assert attributes[:3] == ["cn", "uid", "telephoneNumber"]
    assert {"homePhone", "mobile"} <= set(attributes)
    assert "ds-cfg-unrelated" not in attributes
