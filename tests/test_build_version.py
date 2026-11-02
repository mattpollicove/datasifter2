import pytest

from datasifter.build_version import reserve_build_version


def test_build_version_increments_exactly_and_persists(tmp_path):
    path = tmp_path / "__init__.py"
    path.write_text('"""Package."""\n\n__version__ = "0.000"\n', encoding="utf-8")
    assert reserve_build_version(path) == "0.001"
    assert reserve_build_version(path) == "0.002"
    assert path.read_text() == '"""Package."""\n\n__version__ = "0.002"\n'
    assert not path.with_suffix(".build-version.tmp").exists()


def test_build_version_rollover(tmp_path):
    path = tmp_path / "__init__.py"
    path.write_text('__version__ = "0.999"\n', encoding="utf-8")
    assert reserve_build_version(path) == "1.000"


def test_explicit_build_version_override(tmp_path):
    path = tmp_path / "__init__.py"
    path.write_text('__version__ = "0.010"\n', encoding="utf-8")
    assert reserve_build_version(path, "0.010") == "0.010"
    assert reserve_build_version(path, "1.123") == "1.123"
    assert reserve_build_version(path) == "1.124"


@pytest.mark.parametrize("override", ["", "0.01", "0.0001", "bad", "01.001"])
def test_invalid_override_does_not_change_source(tmp_path, override):
    path = tmp_path / "__init__.py"
    source = '__version__ = "0.001"\n'
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ValueError, match="Explicit build version"):
        reserve_build_version(path, override)
    assert path.read_text() == source
