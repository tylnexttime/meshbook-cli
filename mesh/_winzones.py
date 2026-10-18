"""Windows time-zone id -> IANA name, for the zones people actually run.

Windows reports its zone as a registry key name ("AUS Eastern Standard Time"),
which zoneinfo cannot load. Without an IANA name the server expands recurring
events on the UTC wall clock, so a weekly 10:00 Sydney meeting drifts by an
hour across DST. Mapping from CLDR windowsZones.xml, territory "001" (the
canonical zone for each Windows id). An unknown id returns None and the CLI
keeps its old behaviour (it says recurrences repeat on UTC and suggests --tz).
"""

from __future__ import annotations

WINDOWS_TO_IANA: dict[str, str] = {
    "AUS Eastern Standard Time": "Australia/Sydney",
    "AUS Central Standard Time": "Australia/Darwin",
    "Cen. Australia Standard Time": "Australia/Adelaide",
    "E. Australia Standard Time": "Australia/Brisbane",
    "Tasmania Standard Time": "Australia/Hobart",
    "W. Australia Standard Time": "Australia/Perth",
    "Aus Central W. Standard Time": "Australia/Eucla",
    "Lord Howe Standard Time": "Australia/Lord_Howe",
    "New Zealand Standard Time": "Pacific/Auckland",
    "Chatham Islands Standard Time": "Pacific/Chatham",
    "UTC": "Etc/UTC",
    "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw",
    "Romance Standard Time": "Europe/Paris",
    "E. Europe Standard Time": "Europe/Chisinau",
    "FLE Standard Time": "Europe/Kiev",
    "GTB Standard Time": "Europe/Bucharest",
    "Russian Standard Time": "Europe/Moscow",
    "Turkey Standard Time": "Europe/Istanbul",
    "Israel Standard Time": "Asia/Jerusalem",
    "South Africa Standard Time": "Africa/Johannesburg",
    "Egypt Standard Time": "Africa/Cairo",
    "Arabian Standard Time": "Asia/Dubai",
    "Arab Standard Time": "Asia/Riyadh",
    "Iran Standard Time": "Asia/Tehran",
    "Pakistan Standard Time": "Asia/Karachi",
    "India Standard Time": "Asia/Calcutta",
    "Nepal Standard Time": "Asia/Katmandu",
    "Bangladesh Standard Time": "Asia/Dhaka",
    "SE Asia Standard Time": "Asia/Bangkok",
    "Singapore Standard Time": "Asia/Singapore",
    "China Standard Time": "Asia/Shanghai",
    "Taipei Standard Time": "Asia/Taipei",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Korea Standard Time": "Asia/Seoul",
    "West Pacific Standard Time": "Pacific/Port_Moresby",
    "Fiji Standard Time": "Pacific/Fiji",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "Alaskan Standard Time": "America/Anchorage",
    "Pacific Standard Time": "America/Los_Angeles",
    "US Mountain Standard Time": "America/Phoenix",
    "Mountain Standard Time": "America/Denver",
    "Central Standard Time": "America/Chicago",
    "Eastern Standard Time": "America/New_York",
    "Atlantic Standard Time": "America/Halifax",
    "Newfoundland Standard Time": "America/St_Johns",
    "SA Pacific Standard Time": "America/Bogota",
    "E. South America Standard Time": "America/Sao_Paulo",
    "Argentina Standard Time": "America/Buenos_Aires",
    "Pacific SA Standard Time": "America/Santiago",
    "Central Standard Time (Mexico)": "America/Mexico_City",
}


def windows_zone_key() -> str | None:
    """The machine's Windows zone id from the registry, or None (not Windows,
    or unreadable). stdlib winreg only; never raises."""
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "TimeZoneKeyName")
    except OSError:
        return None
    value = str(value or "").strip().rstrip("\x00")
    return value or None


def windows_iana() -> str | None:
    key = windows_zone_key()
    return WINDOWS_TO_IANA.get(key) if key else None
