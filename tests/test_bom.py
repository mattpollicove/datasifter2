import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def generator():
    return runpy.run_path(str(Path(__file__).resolve().parents[1] / "tools" / "generate_bom.py"))


def test_resolver_tracks_extras_transitives_and_excludes_unrelated(generator, monkeypatch):
    distributions = {
        "base": SimpleNamespace(
            metadata={"Name": "base", "License-Expression": "MIT"},
            version="1.0", requires=['child>=2; extra == "feature"', "common>=1"],
        ),
        "child": SimpleNamespace(metadata={"Name": "child"}, version="2.0", requires=[]),
        "common": SimpleNamespace(metadata={"Name": "common"}, version="1.0", requires=[]),
    }
    queried = []

    def distribution(name):
        queried.append(name)
        return distributions[name]

    monkeypatch.setattr(generator["metadata"], "distribution", distribution)
    manifest = {
        "project": {"dependencies": ["base[feature]>=1"], "optional-dependencies": {
            "dev": ["common>=1", 'foreign; sys_platform == "not-a-real-platform"'],
        }},
        "build-system": {"requires": []},
    }
    components, graph, direct = generator["resolve_libraries"](manifest)
    assert {c["name"] for c in components} == {"base", "child", "common"}
    assert direct == {"pkg:pypi/base@1.0", "pkg:pypi/common@1.0"}
    assert next(d for d in graph if d["ref"] == "pkg:pypi/base@1.0")["dependsOn"] == [
        "pkg:pypi/child@2.0", "pkg:pypi/common@1.0",
    ]
    common = next(c for c in components if c["name"] == "common")
    assert {p["value"] for p in common["properties"]} == {"runtime", "dev"}
    assert "foreign" not in queried


def test_missing_distribution_is_not_silently_omitted(generator, monkeypatch):
    def missing(name):
        raise generator["metadata"].PackageNotFoundError(name)

    monkeypatch.setattr(generator["metadata"], "distribution", missing)
    with pytest.raises(generator["metadata"].PackageNotFoundError):
        generator["resolve_libraries"]({
            "project": {"dependencies": ["missing"]}, "build-system": {"requires": []},
        })


def test_generator_validates_schema_hashes_and_readable_inventory(generator, tmp_path):
    source = tmp_path / "src" / "datasifter"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text('__version__ = "0.001"\n', encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="datasifter"\ndependencies=[]\n[build-system]\nrequires=[]\n',
        encoding="utf-8",
    )
    (tmp_path / "datasifter.spec").write_text("# test build\n", encoding="utf-8")
    native = tmp_path / "dist" / "DataSifter 2.0" / "example.dll"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"native test content")
    generator["generate"](tmp_path)
    bom = json.loads((tmp_path / "bom.json").read_text())
    assert bom["specVersion"] == "1.6"
    assert bom["metadata"]["component"]["version"] == "0.001"
    files = {c["name"]: c for c in bom["components"] if c["type"] == "file"}
    assert "src/datasifter/__init__.py" in files
    assert files["dist/DataSifter 2.0/example.dll"]["hashes"][0]["content"] == (
        generator["hashlib"].sha256(native.read_bytes()).hexdigest()
    )
    document = (tmp_path / "BOM.md").read_text()
    assert "example.dll" in document
    assert "not proof that those binaries incorporate current source changes" in document


def test_packaging_refreshes_bom_after_collecting_files(generator, monkeypatch, tmp_path):
    import PyInstaller.utils.hooks

    calls = []
    monkeypatch.setattr(PyInstaller.utils.hooks, "collect_submodules", lambda name: [])
    execute_spec = runpy.run_path

    def run_path(path):
        if str(path).endswith("build_version.py"):
            return {"reserve_build_version": lambda *args: "0.002"}
        if str(path).endswith("build_info.py"):
            return {"write_build_stamp": lambda *args: "2026-11-01T18:06:55+00:00"}
        return {"generate": lambda root: calls.append(("bom", root))}

    monkeypatch.setattr(runpy, "run_path", run_path)
    analysis = SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])
    namespace = {
        "SPECPATH": str(tmp_path),
        "Analysis": lambda *args, **kwargs: analysis,
        "PYZ": lambda *args: None,
        "EXE": lambda *args, **kwargs: None,
        "COLLECT": lambda *args, **kwargs: calls.append(("collect", tmp_path)),
    }
    spec = Path(__file__).resolve().parents[1] / "datasifter.spec"
    execute_spec(str(spec), init_globals=namespace)
    assert calls == [("collect", tmp_path), ("bom", tmp_path)]
