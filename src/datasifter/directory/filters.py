"""LDAP filter validation and AI context minimization."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable


class FilterSyntaxError(ValueError):
    """Raised when an LDAP filter does not follow RFC 4515's basic grammar."""


def validate_filter(expression: str) -> None:
    """Validate filter structure before it can be submitted to a directory."""
    text = expression.strip()
    if not text:
        raise FilterSyntaxError("Filter cannot be empty.")

    index = 0

    def parse_filter(position: int) -> int:
        if position >= len(text) or text[position] != "(":
            raise FilterSyntaxError("Each LDAP filter must start with '('.")
        position += 1
        if position >= len(text):
            raise FilterSyntaxError("Filter is missing an assertion.")

        if text[position] in "&|":
            operator = text[position]
            position += 1
            child_count = 0
            while position < len(text) and text[position] == "(":
                position = parse_filter(position)
                child_count += 1
            if child_count == 0:
                raise FilterSyntaxError(f"Operator '{operator}' requires at least one filter.")
        elif text[position] == "!":
            position += 1
            position = parse_filter(position)
        else:
            position = parse_assertion(position)

        if position >= len(text) or text[position] != ")":
            raise FilterSyntaxError("Filter has unbalanced parentheses.")
        return position + 1

    def parse_assertion(position: int) -> int:
        start = position
        escaped = False
        while position < len(text):
            char = text[position]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "(":
                raise FilterSyntaxError("Unescaped '(' is not valid inside an assertion.")
            elif char == ")":
                break
            position += 1
        if escaped:
            raise FilterSyntaxError("Filter ends with an incomplete escape sequence.")
        assertion = text[start:position]
        match = re.fullmatch(
            r"(?P<attribute>(?:[A-Za-z][A-Za-z0-9-]*|[0-9]+(?:\.[0-9]+)+)"
            r"(?:;[A-Za-z0-9-]+)*)(?P<operator>~=|>=|<=|=)(?P<value>.*)",
            assertion,
        )
        if not match:
            match = re.fullmatch(
                r"(?P<attribute>(?:[A-Za-z][A-Za-z0-9-]*|[0-9]+(?:\.[0-9]+)+)"
                r"(?:;[A-Za-z0-9-]+)*)?"
                r"(?P<dn>:dn)?"
                r"(?P<matching_rule>:[A-Za-z][A-Za-z0-9-]*|:[0-9]+(?:\.[0-9]+)+)?:="
                r"(?P<value>.*)",
                assertion,
            )
            if match and not (match.group("attribute") or match.group("matching_rule")):
                match = None
        if not match:
            raise FilterSyntaxError("Assertion must contain a valid LDAP comparison operator.")
        if re.search(r"\\(?![0-9a-fA-F]{2})", match.group("value")):
            raise FilterSyntaxError("Filter escape sequences must contain two hexadecimal digits.")
        return position

    index = parse_filter(index)
    if index != len(text):
        raise FilterSyntaxError("Unexpected content after the LDAP filter.")


def schema_context(object_classes: Iterable[str], attributes: Iterable[str]) -> str:
    """Return structural schema only; callers must not pass directory entry values."""
    classes = sorted({value.strip() for value in object_classes if value.strip()})
    names = sorted({value.strip() for value in attributes if value.strip()})
    return "Object classes:\n" + "\n".join(classes) + "\nAttributes:\n" + "\n".join(names)


_CORE_ATTRIBUTES = (
    "objectClass", "cn", "sn", "givenName", "displayName", "uid", "mail",
    "telephoneNumber", "mobile", "facsimileTelephoneNumber", "ou", "title",
    "description", "member", "uniqueMember",
)
_CORE_OBJECT_CLASSES = (
    "person", "organizationalPerson", "inetOrgPerson", "groupOfNames",
    "groupOfUniqueNames", "organizationalUnit",
)
USER_ID_ATTRIBUTES = (
    "cn",
    "uid",
    "sAMAccountName",
    "userPrincipalName",
    "displayName",
    "mail",
    "employeeNumber",
)
_SYNONYMS = {
    "phone": ("telephone", "phone", "mobile", "fax"),
    "phones": ("telephone", "phone", "mobile", "fax"),
    "telephone": ("telephone", "phone", "mobile"),
    "fax": ("facsimile",),
    "facsimile": ("facsimile",),
    "cell": ("mobile",),
    "mobile": ("mobile",),
    "email": ("mail",),
    "e-mail": ("mail",),
    "name": ("cn", "sn", "givenname", "displayname"),
    "surname": ("sn",),
    "first": ("givenname",),
    "last": ("sn",),
    "login": ("uid",),
    "username": ("uid",),
    "user": ("person", "uid"),
    "users": ("person", "uid"),
    "people": ("person",),
    "person": ("person",),
    "group": ("group", "member"),
    "groups": ("group", "member"),
    "city": ("l",),
    "state": ("st",),
    "zip": ("postalcode",),
    "address": ("street", "postaladdress"),
    "department": ("department", "ou"),
    "employee": ("employee",),
}


def relevant_schema(
    request: str,
    object_classes: Iterable[str],
    attributes: Iterable[str],
    max_object_classes: int = 30,
    max_attributes: int = 60,
) -> tuple[list[str], list[str]]:
    """Keep core and request-related schema names so prompts fit small model contexts."""
    words = {word for word in re.findall(r"[a-z][a-z0-9-]+", request.casefold()) if len(word) > 1}
    terms = set(words)
    for word in words:
        terms.update(_SYNONYMS.get(word, ()))
        if word in ATTRIBUTE_ALIASES:
            terms.add(ATTRIBUTE_ALIASES[word].casefold())

    def select(names: Iterable[str], core: tuple[str, ...], limit: int) -> list[str]:
        unique = {name.strip().casefold(): name.strip() for name in names if name.strip()}
        chosen = [unique[name.casefold()] for name in core if name.casefold() in unique]
        matches = sorted(
            (
                name for key, name in unique.items()
                if name not in chosen and any(
                    term == key or (len(term) > 2 and term in key) or (len(key) > 2 and key in term)
                    for term in terms
                )
            ),
            key=lambda name: (len(name), name.casefold()),
        )
        return (chosen + matches)[:limit]

    return (
        select(object_classes, _CORE_OBJECT_CLASSES, max_object_classes),
        select(attributes, _CORE_ATTRIBUTES, max_attributes),
    )


ATTRIBUTE_ALIASES = {
    "phone": "telephoneNumber",
    "telephone": "telephoneNumber",
    "telephonenumber": "telephoneNumber",
    "homephone": "homePhone",
    "fax": "facsimileTelephoneNumber",
    "facsimile": "facsimileTelephoneNumber",
    "facsimiletelephonenumber": "facsimileTelephoneNumber",
    "mobile": "mobile",
    "cell": "mobile",
    "pager": "pager",
    "email": "mail",
    "e-mail": "mail",
    "mail": "mail",
    "surname": "sn",
    "lastname": "sn",
    "firstname": "givenName",
    "given": "givenName",
    "username": "uid",
    "login": "uid",
    "userid": "uid",
    "state": "st",
    "province": "st",
    "region": "st",
    "city": "l",
    "town": "l",
    "locality": "l",
    "location": "l",
    "zip": "postalCode",
    "zipcode": "postalCode",
    "postcode": "postalCode",
    "postalcode": "postalCode",
    "street": "street",
    "address": "street",
    "country": "c",
    "company": "o",
    "organization": "o",
    "organisation": "o",
    "title": "title",
    "jobtitle": "title",
    "employeeid": "employeeNumber",
    "employeenumber": "employeeNumber",
    "manager": "manager",
    "initials": "initials",
    "description": "description",
}
ATTRIBUTE_COMMAND_ALIASES = {
    "password": "userPassword",
    "userpassword": "userPassword",
    "phone": "telephoneNumber",
    "phonenumber": "telephoneNumber",
    "telephone": "telephoneNumber",
    "telephonenumber": "telephoneNumber",
    "tel": "telephoneNumber",
    "workphone": "telephoneNumber",
    "officephone": "telephoneNumber",
    "businessphone": "telephoneNumber",
    "mainphone": "telephoneNumber",
    "mobile": "mobile",
    "mobilenumber": "mobile",
    "mobilephone": "mobile",
    "cell": "mobile",
    "cellnumber": "mobile",
    "cellphone": "mobile",
    "cellular": "mobile",
    "cellularphone": "mobile",
    "cellularnumber": "mobile",
    "cellulartelephone": "mobile",
}
_COMPLEX_REQUEST = re.compile(
    r"\b(?:not|without|except|excluding|no|and|but|greater|less|more|fewer|before|after|"
    r"between|equal|equals|exactly|missing|empty)\b",
    re.IGNORECASE,
)
_VALUE_PATTERN = re.compile(
    r"\b(?P<mode>(?:start(?:s|ing)?|begin(?:s|ning)?|end(?:s|ing)?)\s+(?:with|in)|"
    r"including|includes|include|containing|contains|contain|matching|with|having|has)(?:\s+|$)",
    re.IGNORECASE,
)
_SEARCH_VALUE = re.compile(
    r"(?:(?:the\s+)?(?:letters?|words?|digits?|number|text|string|prefix|suffix)\s+)?"
    r"(?:\"(?P<double>[^\"]+)\"|'(?P<single>[^']+)'|(?P<bare>[^\s\"',;()]+?))"
    r"[.!?]?",
    re.IGNORECASE,
)
_STOP_WORDS = frozenset(
    {
        "a", "an", "the", "show", "find", "list", "get", "search", "display", "give", "me",
        "all", "any", "entries", "entry", "users", "user", "people", "person", "records",
        "record", "objects", "number", "numbers", "value", "values", "or", "with", "in", "of",
        "for", "whose", "where", "that", "have", "has", "having", "is", "are",
    }
)
_ALWAYS_VALID = frozenset({"objectclass", "ou", "dc", "o", "cn"})
_LOCATION = re.compile(
    r"\b(?:in|under|within|inside|from)\s+(?:the\s+)?(ou|o|dc|cn|l)\s*[= ]\s*"
    r"([A-Za-z0-9][\w.-]*)",
    re.IGNORECASE,
)
_USER_LOOKUP = re.compile(
    r"^\s*(?:(?:show|find|list|get|search|display)\s+)?(?:the\s+)?user\s+"
    r"(?:(?:named|called)\s+)?(?:\"(?P<double>[^\"]+)\"|'(?P<single>[^']+)'|"
    r"(?P<bare>[A-Za-z0-9._@+-]+))\s*$",
    re.IGNORECASE,
)
_QUOTED_VALUES = re.compile(r"""(?<!\w)(?:"[^"]*(?:"|$)|'[^']*(?:'|$))""")


def request_location(request: str) -> re.Match[str] | None:
    """Find an unquoted DN-component clause without interpreting literal search values."""
    masked = _QUOTED_VALUES.sub(lambda match: " " * len(match.group()), request)
    return _LOCATION.search(masked)


def escape_filter_value(value: str) -> str:
    """Escape an assertion value per RFC 4515."""
    return "".join(f"\\{ord(char):02x}" if char in "*()\\\0" else char for char in value)


def _schema_lookup(attributes: Iterable[str]) -> dict[str, str]:
    return {name.strip().casefold(): name.strip() for name in attributes if name.strip()}


def alias_hints(attributes: Iterable[str]) -> str:
    """Describe common words for the supplied attributes, e.g. 'st = state, province'."""
    available = {name.casefold(): name for name in attributes}
    grouped: dict[str, list[str]] = {}
    for word, target in ATTRIBUTE_ALIASES.items():
        name = available.get(target.casefold())
        if name and word != target.casefold():
            grouped.setdefault(name, []).append(word)
    return "; ".join(f"{name} = {', '.join(words)}" for name, words in grouped.items())


def resolve_attribute(name: str, attributes: Iterable[str]) -> str | None:
    """Return the schema spelling for an attribute name or common alias."""
    lookup = _schema_lookup(attributes)
    folded = name.casefold()
    if not lookup:
        return ATTRIBUTE_ALIASES.get(folded, name)
    if folded in lookup:
        return lookup[folded]
    alias = ATTRIBUTE_ALIASES.get(folded)
    return lookup.get(alias.casefold()) if alias else None


def structured_filter(request: str, attributes: Iterable[str]) -> str | None:
    """Build simple searches, preserving complete literal values and known attribute names.

    Return None for unsupported wording instead of silently dropping request conditions,
    so the caller can fall back to the language model.
    """
    text = request.strip()
    location = request_location(text)
    if location:
        if text[location.end():].strip(" .!?"):
            return None
        text = text[:location.start()].strip()
    if not text:
        return None
    schema = list(attributes)
    lookup = _schema_lookup(schema)

    user_match = _USER_LOOKUP.fullmatch(text)
    if user_match:
        value = next(group for group in user_match.groups() if group is not None)
        targets = [
            lookup.get(name.casefold(), name)
            for name in USER_ID_ATTRIBUTES
            if not lookup or name.casefold() in lookup
        ]
        if not targets:
            return None
        assertions = "".join(
            f"({name}={escape_filter_value(value)})" for name in targets
        )
        return f"(&(objectClass=person)(|{assertions}))"

    def attribute_for(word: str) -> str | None:
        folded = word.casefold()
        if folded in _STOP_WORDS or folded == "objectclass":
            return None
        if folded in ATTRIBUTE_ALIASES:
            alias = ATTRIBUTE_ALIASES[folded]
            return lookup.get(alias.casefold()) if lookup else alias
        return lookup.get(folded) if len(folded) > 1 else None

    allow_equality = True
    for match in _VALUE_PATTERN.finditer(text):
        mode = match.group("mode").casefold()
        prefix = text[:match.start()]
        if mode not in {"with", "having", "has"} or any(
            word.casefold() not in _STOP_WORDS
            for word in re.split(r"[\s,]+", prefix.strip()) if word
        ):
            allow_equality = False
        value_match = _SEARCH_VALUE.fullmatch(text[match.end():].strip())
        if value_match is None:
            continue
        if _COMPLEX_REQUEST.search(prefix):
            continue
        prefix = re.sub(
            r"\b(first|last)\s+name\b",
            lambda name: name.group(1).casefold() + "name",
            prefix,
            flags=re.IGNORECASE,
        )
        targets: list[str] = []
        for word in re.split(r"[\s,]+", prefix.strip()):
            if not word or word.casefold() in _STOP_WORDS:
                continue
            resolved = attribute_for(word)
            if resolved is None:
                break
            if resolved not in targets:
                targets.append(resolved)
        else:
            if not targets:
                continue
            raw_value = next(value for value in value_match.groups() if value is not None)
            value = escape_filter_value(raw_value)
            if mode.startswith(("start", "begin")):
                pattern = f"{value}*"
            elif mode.startswith("end"):
                pattern = f"*{value}"
            else:
                pattern = f"*{value}*"
            assertions = [f"({name}={pattern})" for name in targets]
            return assertions[0] if len(assertions) == 1 else "(|" + "".join(assertions) + ")"
    if not allow_equality or _COMPLEX_REQUEST.search(text):
        return None
    return _equality_filter(text, attribute_for)


_EQUALITY_FILLER = frozenset({"from", "located", "living", "based", "=", ":", "named"})


def _equality_filter(text: str, attribute_for: Callable[[str], str | None]) -> str | None:
    """Handle 'users in state PA' / 'city = New York': one attribute followed by its value."""
    tokens = [
        token.strip(".,;!?\"'")
        for token in re.split(r"\s+|(?<=[A-Za-z])(?==)|(?<==)", text)
        if token.strip(".,;!?\"'")
    ]
    remaining = [
        token for token in tokens
        if token.casefold() not in _STOP_WORDS and token.casefold() not in _EQUALITY_FILLER
    ]
    if len(remaining) < 2:
        return None
    attribute = attribute_for(remaining[0])
    if attribute is None or any(attribute_for(token) for token in remaining[1:]):
        return None
    value = " ".join(remaining[1:])
    if "*" in value:
        return None
    return f"({attribute}={escape_filter_value(value)})"


_FILTER_ITEM = re.compile(
    r"\(\s*(?P<name>[A-Za-z][A-Za-z0-9-]*|[0-9]+(?:\.[0-9]+)+)(?:;[A-Za-z0-9-]+)*"
    r"(?P<rule>:[^=()]*)?(?:~=|>=|<=|=)(?P<value>[^()]*)\)"
)


def filter_attribute_names(expression: str) -> set[str]:
    """Return casefolded attribute names a filter compares against entry values.

    DN-component matches such as ``(ou:dn:=People)`` and the generic ``(objectClass=*)``
    browse term are ignored because they do not describe a matched attribute value.
    """
    names: set[str] = set()
    for match in _FILTER_ITEM.finditer(expression):
        name = match.group("name").casefold()
        rule = (match.group("rule") or "").casefold()
        if "dn" in rule.strip(":").split(":"):
            continue
        if name == "objectclass" and match.group("value").strip() == "*":
            continue
        names.add(name)
    return names


def normalize_filter_attributes(expression: str, attributes: Iterable[str]) -> str:
    """Map aliases to schema names; raise FilterSyntaxError for unknown attributes."""
    schema = list(attributes)
    if not schema:
        return expression
    unknown: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", name) or name.casefold() in _ALWAYS_VALID:
            return match.group(0)
        resolved = resolve_attribute(name, schema)
        if resolved is None:
            unknown.append(name)
            return match.group(0)
        return match.group("open") + resolved

    normalized = re.sub(
        r"(?P<open>\((?:[&|!]\()*)(?P<name>[A-Za-z0-9][A-Za-z0-9.-]*)(?=(?:;[A-Za-z0-9-]+)*(?:~=|>=|<=|=|:))",
        replace,
        expression,
    )
    if unknown:
        raise FilterSyntaxError(
            "The generated filter uses attributes not in the directory schema: "
            + ", ".join(sorted(set(unknown)))
        )
    return normalized
