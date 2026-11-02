import sys
from datetime import UTC, datetime
from pathlib import Path

from datasifter import build_info


def test_write_build_stamp_records_utc_iso_time(tmp_path):
    moment = datetime(2026, 11, 1, 18, 6, 55, tzinfo=UTC)

    stamp = build_info.write_build_stamp(tmp_path, moment)

    assert stamp == "2026-11-01T18:06:55+00:00"
    assert 'BUILD_TIMESTAMP = "2026-11-01T18:06:55+00:00"' in (
        tmp_path / build_info.STAMP_MODULE
    ).read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))


def test_frozen_build_label_uses_stamped_local_time(monkeypatch):
    moment = datetime(2026, 11, 1, 18, 6, 55, tzinfo=UTC)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(build_info, "_stamped_time", lambda: moment)

    expected = moment.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    assert build_info.build_label() == f"built {expected}"


def test_frozen_build_without_stamp_is_reported(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(build_info, "_stamped_time", lambda: None)

    assert build_info.build_label() == "build time unknown"


def test_source_run_uses_newest_source_file_time(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    moment, frozen = build_info.build_time()

    assert not frozen
    assert moment is not None
    assert moment.timestamp() >= Path(build_info.__file__).stat().st_mtime - 1
