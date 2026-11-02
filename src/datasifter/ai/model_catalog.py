"""Pinned GGUF downloads from publisher or well-known quantizer repositories."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, build_opener

from datasifter.ai.local_model import LocalModelError


@dataclass(frozen=True)
class CatalogModel:
    name: str
    publisher: str
    license: str
    repository: str
    revision: str
    filename: str
    size: int
    sha256: str
    description: str = ""
    upstream: str = ""
    quantized_by: str = ""

    @property
    def source(self) -> str:
        return f"https://huggingface.co/{self.repository}/tree/{self.revision}"

    @property
    def model_card(self) -> str:
        return f"https://huggingface.co/{self.upstream or self.repository}"

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repository}/resolve/{self.revision}/{self.filename}"


MODEL_CATALOG = (
    CatalogModel(
        "SmolLM2 1.7B Instruct Q4_K_M", "Hugging Face (US/France)", "Apache-2.0",
        "HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF",
        "2d4a76a30b4af41ecd395c35725ac11688d4cfe4",
        "smollm2-1.7b-instruct-q4_k_m.gguf", 1055609536,
        "decd2598bc2c8ed08c19adc3c8fdd461ee19ed5708679d1c54ef54a5a30d4f33",
        "Compact 1.7B instruction model with an 8K context. Fast on modest CPUs; "
        "a good default for drafting simple LDAP filters.",
        "HuggingFaceTB/SmolLM2-1.7B-Instruct", "Hugging Face",
    ),
    CatalogModel(
        "Google Gemma 3 1B Instruct Q4_K_M", "Google (US)", "Gemma Terms of Use",
        "ggml-org/gemma-3-1b-it-GGUF",
        "f9c28bcd85737ffc5aef028638d3341d49869c27",
        "gemma-3-1b-it-Q4_K_M.gguf", 806058240,
        "8ccc5cd1f1b3602548715ae25a66ed73fd5dc68a210412eea643eb20eb75a135",
        "Smallest catalog option: 1B text model with a 32K context. Lowest memory use "
        "and fastest responses; best for short, simple filters.",
        "google/gemma-3-1b-it", "ggml-org (llama.cpp maintainers)",
    ),
    CatalogModel(
        "Google Gemma 3 4B Instruct Q4_K_M", "Google (US)", "Gemma Terms of Use",
        "ggml-org/gemma-3-4b-it-GGUF",
        "d0976223747697cb51e056d85c532013931fe52e",
        "gemma-3-4b-it-Q4_K_M.gguf", 2489757856,
        "882e8d2db44dc554fb0ea5077cb7e4bc49e7342a1f0da57901c0802ea21a0863",
        "4B model with a 128K context and strong multilingual instruction following. "
        "Good balance of quality and speed on laptops (text use only here).",
        "google/gemma-3-4b-it", "ggml-org (llama.cpp maintainers)",
    ),
    CatalogModel(
        "Meta Llama 3.2 1B Instruct Q4_K_M", "Meta (US)", "Llama 3.2 Community License",
        "hugging-quants/Llama-3.2-1B-Instruct-Q4_K_M-GGUF",
        "7d1f70022fcab2038000074bd0342e03e1d8b755",
        "llama-3.2-1b-instruct-q4_k_m.gguf", 807690656,
        "1d0e9419ec4e12aef73ccf4ffd122703e94c48344a96bc7c5f0f2772c2152ce3",
        "Lightweight 1B multilingual model with a 128K context. Very fast on CPU; "
        "suited to simple prompts. License requires \"Built with Llama\" attribution.",
        "meta-llama/Llama-3.2-1B-Instruct", "Hugging Face (hugging-quants)",
    ),
    CatalogModel(
        "Meta Llama 3.2 3B Instruct Q4_K_M", "Meta (US)", "Llama 3.2 Community License",
        "hugging-quants/Llama-3.2-3B-Instruct-Q4_K_M-GGUF",
        "eb72f2a08dd2b9edd07ffacfe5aa56938b7939b0",
        "llama-3.2-3b-instruct-q4_k_m.gguf", 2019373920,
        "c55a83bfb6396799337853ca69918a0b9bbb2917621078c34570bc17d20fd7a1",
        "3B multilingual model with a 128K context. Noticeably better reasoning than the "
        "1B models while staying responsive on most laptops.",
        "meta-llama/Llama-3.2-3B-Instruct", "Hugging Face (hugging-quants)",
    ),
    CatalogModel(
        "Meta Llama 3.1 8B Instruct Q4_0", "Meta (US)", "Llama 3.1 Community License",
        "ggml-org/Meta-Llama-3.1-8B-Instruct-Q4_0-GGUF",
        "0aba27dd2f1c7f4941a94a5c59d80e0a256f9ff8",
        "meta-llama-3.1-8b-instruct-q4_0.gguf", 6036116480,
        "b4778681efbd6898fe1965e74f810b3b7dfdf6afab01df9c88c1b0e6e9ca122d",
        "8B model with a 128K context. Higher-quality answers for complex filters; "
        "needs about 8 GB of free RAM and is slower on CPU-only systems.",
        "meta-llama/Llama-3.1-8B-Instruct", "ggml-org (llama.cpp maintainers)",
    ),
    CatalogModel(
        "Phi-3 Mini 4K Instruct Q4", "Microsoft (US)", "MIT",
        "microsoft/Phi-3-mini-4k-instruct-gguf",
        "a64113399c2f6b8ad3e11c394733a2ddadaa7f33",
        "Phi-3-mini-4k-instruct-q4.gguf", 2393231072,
        "8a83c7fb9049a9b2e92266fa7ad04933bb53aa1e85136b7b30f1b8000ff2edef",
        "3.8B model focused on reasoning and code with a 4K context. Published as GGUF "
        "directly by Microsoft.",
        "microsoft/Phi-3-mini-4k-instruct", "Microsoft",
    ),
    CatalogModel(
        "Microsoft Phi-4 14B Q4_K_S", "Microsoft (US)", "MIT",
        "microsoft/phi-4-gguf",
        "6edc2ef6664b739a8e11e62f2672ff6afe0c15ac",
        "phi-4-Q4_K_S.gguf", 8440762560,
        "8a8189132ef70cd737e136d38b706e8cb832f9cdf3c744547694769e9313550e",
        "Largest catalog option: 14B model with a 16K context and strong reasoning. "
        "Published as GGUF directly by Microsoft; needs about 10 GB of free RAM.",
        "microsoft/phi-4", "Microsoft",
    ),
)


class HTTPSRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme != "https":
            raise LocalModelError("Model download refused a non-HTTPS redirect.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_model(
    model: CatalogModel,
    directory: Path,
    cancelled: Event,
    progress: Callable[[int, str], None],
) -> str:
    if model not in MODEL_CATALOG:
        raise LocalModelError("Select a model from the approved download catalog.")
    if cancelled.is_set():
        raise LocalModelError("Model download cancelled.")
    temporary: Path | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / model.filename
        if target.exists():
            raise LocalModelError("This model file already exists. Select it or choose another folder.")
        digest = hashlib.sha256()
        received = 0
        opener = build_opener(HTTPSRedirectHandler())
        with opener.open(model.url, timeout=10) as response:
            if urlparse(response.geturl()).scheme != "https":
                raise LocalModelError("Model download requires HTTPS.")
            with tempfile.NamedTemporaryFile(dir=directory, suffix=".part", delete=False) as output:
                temporary = Path(output.name)
                while True:
                    if cancelled.is_set():
                        raise LocalModelError("Model download cancelled.")
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    if received == 0 and chunk[:4] != b"GGUF":
                        raise LocalModelError("Downloaded file is not a GGUF model.")
                    received += len(chunk)
                    if received > model.size:
                        raise LocalModelError("Model download exceeded the expected file size.")
                    output.write(chunk)
                    digest.update(chunk)
                    progress(int(received * 100 / model.size), f"Downloaded {received:,} / {model.size:,} bytes")
                output.flush()
                os.fsync(output.fileno())
        if cancelled.is_set():
            raise LocalModelError("Model download cancelled.")
        if received != model.size or digest.hexdigest() != model.sha256:
            raise LocalModelError("Model download failed size or SHA-256 verification.")
        # Exclusive creation avoids replacing another download or an existing model.
        os.link(temporary, target)
        return str(target)
    except OSError as error:
        raise LocalModelError(f"Model download failed: {error}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
