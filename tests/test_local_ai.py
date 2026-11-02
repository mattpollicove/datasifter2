import sys
from threading import Event
from types import SimpleNamespace

import pytest

from datasifter.ai.local_model import (
    LlamaCppFilterGenerator,
    LocalModelError,
    model_sha256,
)


class FakeStoppingCriteriaList(list):
    def __call__(self, input_ids, logits):
        return any(criterion(input_ids, logits) for criterion in self)


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("model.bin", b"GGUFplaceholder", r"Only local .gguf"),
        ("model.safetensors", b"GGUFplaceholder", r"Only local .gguf"),
        ("model.gguf", b"not a GGUF file", "invalid GGUF header"),
        ("empty.gguf", b"", "invalid GGUF header"),
        ("short.gguf", b"GG", "invalid GGUF header"),
    ],
)
def test_non_gguf_models_are_rejected_before_hashing_or_loading(
    tmp_path, monkeypatch, filename, content, message
):
    model_path = tmp_path / filename
    model_path.write_bytes(content)

    def fail_load(**_options):
        raise AssertionError("Invalid models must not reach llama.cpp")

    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_cpp(fail_load))
    with pytest.raises(LocalModelError, match=message):
        model_sha256(model_path)
    with pytest.raises(LocalModelError, match=message):
        LlamaCppFilterGenerator(str(model_path), "a" * 64)


def test_gguf_hash_covers_header_and_accepts_uppercase_extension(tmp_path):
    import hashlib

    model_path = tmp_path / "model.GGUF"
    content = b"GGUFplaceholder"
    model_path.write_bytes(content)

    assert model_sha256(model_path) == hashlib.sha256(content).hexdigest()


def fake_llama_cpp(llama):
    return SimpleNamespace(Llama=llama, StoppingCriteriaList=FakeStoppingCriteriaList)


def test_local_model_prompt_contains_schema_but_not_row_values(tmp_path, monkeypatch):
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUFtest model placeholder")
    prompts = []

    class FakeLlama:
        def __init__(self, **_options):
            pass

        def __call__(self, prompt, **options):
            prompts.append(prompt)
            assert options["stopping_criteria"]([], []) is False
            return iter(
                [
                    {"choices": [{"text": "(objectClass="}]},
                    {"choices": [{"text": "person)"}]},
                ]
            )

    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_cpp(FakeLlama))
    generator = LlamaCppFilterGenerator(str(model_path), model_sha256(model_path))
    streamed = []

    result = generator.generate_filter(
        "Find people",
        ["person"],
        ["cn", "uid"],
        on_token=streamed.append,
    )

    assert result == "(objectClass=person)"
    assert "".join(streamed) == result
    assert "person" in prompts[0]
    assert "uid" in prompts[0]
    assert "secret employee value" not in prompts[0]
    assert "(ou:dn:=" in prompts[0]
    assert "=*X*" in prompts[0]


def test_model_hash_mismatch_is_rejected_before_loading_model(tmp_path):
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUFapproved model")

    with pytest.raises(LocalModelError, match="changed after it was approved"):
        LlamaCppFilterGenerator(str(model_path), "0" * 64)


def test_local_generation_stops_when_cancelled(tmp_path, monkeypatch):
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUFtest model")
    cancelled = Event()

    class FakeLlama:
        def __init__(self, **_options):
            pass

        def __call__(self, _prompt, **options):
            assert options["stream"] is True
            return iter(
                [
                    {"choices": [{"text": "("}]},
                    {"choices": [{"text": "objectClass=person)"}]},
                ]
            )

    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_cpp(FakeLlama))
    generator = LlamaCppFilterGenerator(str(model_path), model_sha256(model_path))

    with pytest.raises(LocalModelError, match="cancelled"):
        generator.generate_filter(
            "Find people",
            ["person"],
            ["objectClass"],
            on_token=lambda _token: cancelled.set(),
            cancelled=cancelled,
        )


def test_stopping_criteria_is_callable_for_installed_llama_cpp(tmp_path, monkeypatch):
    llama_cpp = pytest.importorskip("llama_cpp")
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUFtest model")
    cancelled = Event()
    checked = []

    class FakeLlama:
        def __init__(self, **_options):
            pass

        def __call__(self, _prompt, **options):
            criteria = options["stopping_criteria"]
            assert isinstance(criteria, llama_cpp.StoppingCriteriaList)
            checked.append(criteria([], []))
            cancelled.set()
            checked.append(criteria([], []))
            cancelled.clear()
            return iter([{"choices": [{"text": "(telephoneNumber=*732*)"}]}])

    monkeypatch.setattr(llama_cpp, "Llama", FakeLlama)
    generator = LlamaCppFilterGenerator(str(model_path), model_sha256(model_path))

    assert generator.generate_filter(
        "phone not including 555", ["person"], ["telephoneNumber"], cancelled=cancelled
    ) == "(telephoneNumber=*732*)"
    assert checked == [False, True]


def test_large_schema_is_trimmed_to_relevant_names_within_context(tmp_path, monkeypatch):
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUFtest model")
    prompts = []

    class FakeLlama:
        def __init__(self, **_options):
            pass

        def tokenize(self, data):
            return data.split()

        def __call__(self, prompt, **_options):
            prompts.append(prompt)
            return iter([{"choices": [{"text": "(telephoneNumber=*732*)"}]}])

    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_cpp(FakeLlama))
    generator = LlamaCppFilterGenerator(str(model_path), model_sha256(model_path), context_size=420)
    attributes = [f"ds-cfg-unrelated-{index}" for index in range(20000)] + ["telephoneNumber"]

    generator.generate_filter(
        "show entries whose phone number is not 732 in ou people",
        ["inetOrgPerson"],
        attributes,
    )

    assert "telephoneNumber" in prompts[0]
    assert "ds-cfg-unrelated-1\n" not in prompts[0]
    assert len(prompts[0].split()) + 256 <= 420


@pytest.mark.parametrize(
    ("request_text", "generated", "expected"),
    [
        (
            "show entries with phone number including 732 in ou people",
            "(&(telephoneNumber=*732*))",
            "(&(telephoneNumber=*732*)(ou:dn:=people))",
        ),
        ("phones under ou=Sales", "(telephoneNumber=*)", "(&(telephoneNumber=*)(ou:dn:=Sales))"),
        ("in ou People", "(&(cn=a)(ou:dn:=People))", "(&(cn=a)(ou:dn:=People))"),
        ("people named a", "(cn=a)", "(cn=a)"),
    ],
)
def test_requested_location_is_enforced_as_dn_component_match(request_text, generated, expected):
    from datasifter.ai.local_model import _apply_location
    from datasifter.directory.filters import validate_filter

    result = _apply_location(request_text, generated)
    assert result == expected
    validate_filter(result)


LAB_ATTRIBUTES = ["cn", "sn", "mail", "uid", "telephoneNumber", "mobile", "facsimileTelephoneNumber"]


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        (
            "show fax, phone or mobile numbers with 732",
            "(|(facsimileTelephoneNumber=*732*)(telephoneNumber=*732*)(mobile=*732*))",
        ),
        (
            "show entries with phone number including 732 in ou people",
            "(&(telephoneNumber=*732*)(ou:dn:=people))",
        ),
        ("users whose mail ends with example.com", "(mail=*example.com)"),
        ("uid starting with user*1", "(uid=user\\2a1*)"),
        ("show all entries with lastname starting with the letter b", "(sn=b*)"),
        ("last name begins with the letters Mc", "(sn=Mc*)"),
        ('cn contains "Jane Doe"', "(cn=*Jane Doe*)"),
        ('cn contains "staff in ou Sales"', "(cn=*staff in ou Sales*)"),
        (
            'cn contains "staff in ou Sales" in ou People',
            "(&(cn=*staff in ou Sales*)(ou:dn:=People))",
        ),
        (
            "lastname starts with the letter b in ou People",
            "(&(sn=b*)(ou:dn:=People))",
        ),
    ],
)
def test_simple_requests_use_schema_names_without_the_model(tmp_path, monkeypatch, request_text, expected):
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUFtest model")

    class FakeLlama:
        def __init__(self, **_options):
            pass

        def __call__(self, *_args, **_options):
            raise AssertionError("model should not be called for simple requests")

    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_cpp(FakeLlama))
    generator = LlamaCppFilterGenerator(str(model_path), model_sha256(model_path))

    assert generator.generate_filter(request_text, ["inetOrgPerson"], LAB_ATTRIBUTES) == expected


@pytest.mark.parametrize(
    "request_text",
    [
        "sn starts with b or c",
        "sn with b extra condition",
        'cn contains "Jane Doe" extra condition',
        "sn or unknownAttribute starts with b",
    ],
)
def test_unsupported_phrasing_reaches_model_without_losing_conditions(
    tmp_path, monkeypatch, request_text
):
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUFtest model")
    prompts = []

    class FakeLlama:
        def __init__(self, **_options):
            pass

        def __call__(self, prompt, **_options):
            prompts.append(prompt)
            return iter([{"choices": [{"text": "(sn=b*)"}]}])

    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_cpp(FakeLlama))
    generator = LlamaCppFilterGenerator(str(model_path), model_sha256(model_path))

    assert generator.generate_filter(request_text, ["inetOrgPerson"], LAB_ATTRIBUTES) == "(sn=b*)"
    assert len(prompts) == 1
    assert f"Request: {request_text}\nFilter:" in prompts[0]


@pytest.mark.parametrize(
    ("model_output", "expected"),
    [
        ("(|(fax=*1*)(phone=*1*))", "(|(facsimileTelephoneNumber=*1*)(telephoneNumber=*1*))"),
        ("(!(MAIL=*))", "(!(mail=*))"),
    ],
)
def test_model_aliases_are_mapped_to_schema_names(tmp_path, monkeypatch, model_output, expected):
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUFtest model")

    class FakeLlama:
        def __init__(self, **_options):
            pass

        def __call__(self, *_args, **_options):
            return iter([{"choices": [{"text": model_output}]}])

    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_cpp(FakeLlama))
    generator = LlamaCppFilterGenerator(str(model_path), model_sha256(model_path))

    assert generator.generate_filter("entries not matching", ["person"], LAB_ATTRIBUTES) == expected


@pytest.mark.parametrize("model_output", ["(fax=316)(phone=316)", "(color=blue)"])
def test_unusable_model_output_is_reported(tmp_path, monkeypatch, model_output):
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUFtest model")

    class FakeLlama:
        def __init__(self, **_options):
            pass

        def __call__(self, *_args, **_options):
            return iter([{"choices": [{"text": model_output}]}])

    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_cpp(FakeLlama))
    generator = LlamaCppFilterGenerator(str(model_path), model_sha256(model_path))

    with pytest.raises(LocalModelError, match="unusable filter"):
        generator.generate_filter("entries not matching", ["person"], LAB_ATTRIBUTES)


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ("show users in state pa", "(st=pa)"),
        ("people with state = PA", "(st=PA)"),
        ("users from city New York", "(l=New York)"),
        ("zip 15213", "(postalCode=15213)"),
        ("state PA in ou People", "(&(st=PA)(ou:dn:=People))"),
    ],
)
def test_attribute_value_requests_map_common_words(tmp_path, monkeypatch, request_text, expected):
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUFtest model")

    class FakeLlama:
        def __init__(self, **_options):
            pass

        def __call__(self, *_args, **_options):
            raise AssertionError("model should not be called for simple requests")

    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_cpp(FakeLlama))
    generator = LlamaCppFilterGenerator(str(model_path), model_sha256(model_path))
    attributes = LAB_ATTRIBUTES + ["st", "l", "postalCode"]

    assert generator.generate_filter(request_text, ["inetOrgPerson"], attributes) == expected


def test_model_state_word_is_mapped_and_prompt_lists_aliases(tmp_path, monkeypatch):
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUFtest model")
    prompts = []

    class FakeLlama:
        def __init__(self, **_options):
            pass

        def __call__(self, prompt, **_options):
            return iter([{"choices": [{"text": "(&(state=pa)(!(city=Erie)))"}]}])

    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_cpp(FakeLlama))
    generator = LlamaCppFilterGenerator(str(model_path), model_sha256(model_path))
    attributes = [f"ds-cfg-unrelated-{index}" for index in range(500)] + ["st", "l", "cn"]

    result = generator.generate_filter(
        "people in state pa but not in city erie",
        ["inetOrgPerson"],
        attributes,
        on_prompt=prompts.append,
    )

    assert result == "(&(st=pa)(!(l=Erie)))"
    assert len(prompts) == 1
    assert "st = state, province, region" in prompts[0]
    assert "l = city, town, locality, location" in prompts[0]
    assert "\nst\n" in prompts[0]


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("(objectClass=*)", set()),
        ("(&(objectClass=person)(st=PA))", {"objectclass", "st"}),
        ("(|(telephoneNumber=*732*)(mobile=*732*))", {"telephonenumber", "mobile"}),
        ("(&(cn=a*)(ou:dn:=People))", {"cn"}),
        ("(&(!(l=Erie))(createTimestamp>=2026))", {"l", "createtimestamp"}),
        ("(cn;lang-en:caseExactMatch:=Fred)", {"cn"}),
    ],
)
def test_filter_attribute_names(expression, expected):
    from datasifter.directory.filters import filter_attribute_names

    assert filter_attribute_names(expression) == expected
