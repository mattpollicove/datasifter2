"""Optional llama.cpp integration for local LDAP filter generation."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from threading import Event

from datasifter.directory.filters import (
    FilterSyntaxError,
    alias_hints,
    normalize_filter_attributes,
    relevant_schema,
    request_location,
    schema_context,
    structured_filter,
    validate_filter,
)


class LocalModelError(RuntimeError):
    """Raised when local inference is unavailable or returns an invalid command."""


def _escape_value(value: str) -> str:
    return "".join(f"\\{ord(char):02x}" if char in "*()\\\0" else char for char in value)


def _apply_location(request: str, generated: str) -> str:
    """Small models often drop 'in ou X'; enforce it as an RFC 4515 dnAttributes match."""
    match = request_location(request)
    if match is None or ":dn:" in generated.casefold() or not generated.startswith("("):
        return generated
    location = f"({match.group(1).lower()}:dn:={_escape_value(match.group(2))})"
    if generated.startswith("(&") and generated.endswith(")"):
        return generated[:-1] + location + ")"
    return f"(&{generated}{location})"


def model_sha256(
    model_path: str | Path,
    cancelled: Event | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> str:
    path = Path(model_path).expanduser()
    if path.suffix.casefold() != ".gguf":
        raise LocalModelError("Only local .gguf model files are supported.")
    try:
        size = path.stat().st_size
        digest = hashlib.sha256()
        processed = 0
        with path.open("rb") as model_file:
            if model_file.read(4) != b"GGUF":
                raise LocalModelError("The selected file is not a GGUF model (invalid GGUF header).")
            model_file.seek(0)
            while chunk := model_file.read(1024 * 1024):
                if cancelled and cancelled.is_set():
                    raise LocalModelError("Model verification cancelled.")
                digest.update(chunk)
                processed += len(chunk)
                if progress:
                    percent = int(processed * 100 / size) if size else 100
                    progress(percent, "Verifying local model")
        return digest.hexdigest()
    except OSError as error:
        raise LocalModelError(f"Unable to read local model file: {error}") from error


class LlamaCppFilterGenerator:
    def __init__(
        self,
        model_path: str,
        expected_sha256: str,
        context_size: int = 4096,
    ) -> None:
        if not model_path:
            raise LocalModelError("Configure a local GGUF model file before using AI assistance.")
        path = Path(model_path).expanduser()
        if not path.is_file():
            raise LocalModelError(f"Configured model file does not exist: {path}")
        if not expected_sha256:
            raise LocalModelError(
                "Verify the model's provenance in Settings before using AI assistance."
            )
        actual_sha256 = model_sha256(path)
        if actual_sha256.casefold() != expected_sha256.casefold():
            raise LocalModelError(
                "The local model file changed after it was approved. Verify it again in Settings."
            )
        try:
            from llama_cpp import Llama, StoppingCriteriaList
        except ImportError as error:
            raise LocalModelError(
                "Local AI support is not installed. Install the optional 'local-ai' extra."
            ) from error
        self._stopping_criteria_list = StoppingCriteriaList
        self._context_size = context_size
        self._model = Llama(model_path=str(path), n_ctx=context_size, verbose=False)

    def generate_filter(
        self,
        request: str,
        object_classes: list[str],
        attribute_names: list[str],
        on_token: Callable[[str], None] | None = None,
        cancelled: Event | None = None,
        on_prompt: Callable[[str], None] | None = None,
    ) -> str:
        """Generate a filter; on_prompt receives the exact text sent to the model, if any."""
        if not request.strip():
            raise LocalModelError("Describe the directory entries you want to find.")
        structured = structured_filter(request, attribute_names)
        if structured is not None:
            generated = _apply_location(request, structured)
            validate_filter(generated)
            if on_token:
                on_token(generated)
            return generated
        classes, attributes = relevant_schema(request, object_classes, attribute_names)
        prompt = self._prompt(request, classes, attributes)
        # Shrink schema context until prompt plus answer fits the model context window.
        while self._token_count(prompt) + 256 > self._context_size and (classes or attributes):
            classes = classes[: len(classes) // 2]
            attributes = attributes[: len(attributes) // 2]
            prompt = self._prompt(request, classes, attributes)
        if on_prompt:
            on_prompt(prompt)
        try:
            result_stream = self._model(
                prompt,
                max_tokens=256,
                temperature=0.1,
                stop=["\n"],
                stream=True,
                stopping_criteria=self._stopping_criteria_list(
                    [lambda *_args: bool(cancelled and cancelled.is_set())]
                ),
            )
            generated_chunks: list[str] = []
            for result in result_stream:
                if cancelled and cancelled.is_set():
                    raise LocalModelError("Local model generation cancelled.")
                generated_text = result["choices"][0]["text"]
                if not isinstance(generated_text, str):
                    raise LocalModelError("Local model returned a non-text response.")
                generated_chunks.append(generated_text)
                if on_token:
                    on_token(generated_text)
            generated_text = "".join(generated_chunks)
        except LocalModelError:
            raise
        except (KeyError, IndexError, TypeError, ValueError, RuntimeError) as error:
            raise LocalModelError(f"Local model inference failed: {error}") from error
        generated = _apply_location(request, generated_text.strip())
        try:
            validate_filter(generated)
            return normalize_filter_attributes(generated, attribute_names)
        except FilterSyntaxError as error:
            raise LocalModelError(
                f"The local model produced an unusable filter ({generated or 'empty'}): {error} "
                "Try rephrasing the request more simply."
            ) from error

    @staticmethod
    def _prompt(request: str, object_classes: list[str], attributes: list[str]) -> str:
        hints = alias_hints(attributes)
        return (
            "Translate the request into one RFC 4515 LDAP search filter. "
            "Use only attribute names from the supplied schema, never everyday words. "
            "Return only the filter, with no explanation.\n"
            "Rules: 'contains X' or 'including X' or 'with X' is a substring match (attr=*X*). "
            "'starts with X' is (attr=X*). 'in state X' or 'state is X' is an equality match "
            "(attr=X). When several attributes are listed with 'or', "
            "match any of them with (|...). A location such as 'in ou X' or "
            "'under ou X' is a DN component match (ou:dn:=X). Combine conditions with (&...).\n"
            + (f"Attribute names for common words: {hints}.\n" if hints else "")
            + "Examples:\n"
            "Request: users whose mail contains example\nFilter: (mail=*example*)\n"
            "Request: entries with uid or mail including smith\n"
            "Filter: (|(uid=*smith*)(mail=*smith*))\n"
            "Request: people in state PA who are not in city Erie\n"
            "Filter: (&(st=PA)(!(l=Erie)))\n"
            "Request: entries with title including manager in ou Sales\n"
            "Filter: (&(title=*manager*)(ou:dn:=Sales))\n"
            f"{schema_context(object_classes, attributes)}\n"
            f"Request: {request.strip()}\nFilter:"
        )

    def _token_count(self, prompt: str) -> int:
        tokenize = getattr(self._model, "tokenize", None)
        if tokenize is None:
            return len(prompt) // 3
        return len(tokenize(prompt.encode("utf-8")))
