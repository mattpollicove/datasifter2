import hashlib
from dataclasses import replace
from io import BytesIO
from threading import Event

import pytest

from datasifter.ai import model_catalog
from datasifter.ai.local_model import LocalModelError
from datasifter.ai.model_catalog import MODEL_CATALOG, download_model


def prepare_download(monkeypatch, data=b"GGUFtest-model"):
    model = replace(
        MODEL_CATALOG[0], size=len(data), sha256=hashlib.sha256(data).hexdigest()
    )
    monkeypatch.setattr(model_catalog, "MODEL_CATALOG", (model,))

    class Response(BytesIO):
        def geturl(self):
            return model.url

    class Opener:
        def open(self, url, timeout):
            assert url == model.url
            assert timeout == 10
            return Response(data)

    monkeypatch.setattr(model_catalog, "build_opener", lambda *_args: Opener())
    return model


def test_download_verifies_bytes_and_publishes_without_partial_files(monkeypatch, tmp_path):
    model = prepare_download(monkeypatch)
    path = download_model(model, tmp_path, Event(), lambda *_args: None)
    assert (tmp_path / model.filename).read_bytes() == b"GGUFtest-model"
    assert path == str(tmp_path / model.filename)
    assert not list(tmp_path.glob("*.part"))
    with pytest.raises(LocalModelError, match="already exists"):
        download_model(model, tmp_path, Event(), lambda *_args: None)


def test_bad_hash_cleans_up_download(monkeypatch, tmp_path):
    model = prepare_download(monkeypatch)
    model = replace(model, sha256="0" * 64)
    monkeypatch.setattr(model_catalog, "MODEL_CATALOG", (model,))
    with pytest.raises(LocalModelError, match="verification"):
        download_model(model, tmp_path, Event(), lambda *_args: None)
    assert list(tmp_path.iterdir()) == []


def test_cancellation_removes_partial_download(monkeypatch, tmp_path):
    model = prepare_download(monkeypatch)
    cancelled = Event()
    with pytest.raises(LocalModelError, match="cancelled"):
        download_model(model, tmp_path, cancelled, lambda *_args: cancelled.set())
    assert list(tmp_path.iterdir()) == []


def test_download_rejects_non_gguf_payload(monkeypatch, tmp_path):
    model = prepare_download(monkeypatch, b"HTMLnot-a-model")
    with pytest.raises(LocalModelError, match="not a GGUF"):
        download_model(model, tmp_path, Event(), lambda *_args: None)
    assert list(tmp_path.iterdir()) == []


def test_model_download_rejects_https_downgrade():
    handler = model_catalog.HTTPSRedirectHandler()
    with pytest.raises(LocalModelError, match="non-HTTPS"):
        handler.redirect_request(None, None, 302, "", {}, "http://example.test/model.gguf")
