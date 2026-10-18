"""0.12.1: on Windows the local zone is mapped to IANA, so recurring events keep
their local wall clock across DST without --tz (0.12.0 fell back to UTC)."""
from zoneinfo import ZoneInfo

import pytest

from mesh import _when, _winzones


@pytest.mark.parametrize("win,iana", sorted(_winzones.WINDOWS_TO_IANA.items()))
def test_every_mapped_name_is_a_loadable_iana_zone(win, iana):
    ZoneInfo(iana)  # raises if the table names a zone that does not exist


def test_sydney_maps_from_the_windows_registry_name():
    assert _winzones.WINDOWS_TO_IANA["AUS Eastern Standard Time"] == "Australia/Sydney"


def test_iana_name_falls_back_to_the_windows_zone(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(_winzones, "windows_zone_key", lambda: "AUS Eastern Standard Time")
    monkeypatch.setattr(_when.Path, "is_symlink", lambda self: False)
    assert _when.iana_name(None) == "Australia/Sydney"


def test_unmapped_windows_zone_keeps_the_utc_fallback(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(_winzones, "windows_zone_key", lambda: "Some Unknown Standard Time")
    monkeypatch.setattr(_when.Path, "is_symlink", lambda self: False)
    assert _when.iana_name(None) is None


def test_explicit_tz_still_wins(monkeypatch):
    monkeypatch.setattr(_winzones, "windows_zone_key", lambda: "AUS Eastern Standard Time")
    assert _when.iana_name("Europe/Warsaw") == "Europe/Warsaw"
