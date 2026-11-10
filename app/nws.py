"""National Weather Service data: hourly forecast and today's observed high.
Free, no key. Kalshi settles on the NWS Daily Climate Report for each city's station,
which uses LOCAL STANDARD TIME (midnight to midnight, ignoring daylight saving)."""
from datetime import datetime, timedelta, timezone

import requests

from . import config

# code: (Kalshi series, settlement station, lat, lon, standard UTC offset hours)
CITIES = {
    "NYC":  ("KXHIGHNY",   "KNYC", 40.7790, -73.9692, -5),   # Central Park
    "CHI":  ("KXHIGHCHI",  "KMDW", 41.7868, -87.7522, -6),   # Midway, not O'Hare
    "MIA":  ("KXHIGHMIA",  "KMIA", 25.7932, -80.2906, -5),
    "AUS":  ("KXHIGHAUS",  "KAUS", 30.1945, -97.6699, -6),   # Bergstrom
    "LAX":  ("KXHIGHLAX",  "KLAX", 33.9382, -118.3866, -8),  # airport, not downtown
    "DEN":  ("KXHIGHDEN",  "KDEN", 39.8466, -104.6562, -7),
    "PHIL": ("KXHIGHPHIL", "KPHL", 39.8733, -75.2268, -5),
    "PHX":  ("KXHIGHTPHX", "KPHX", 33.4342, -112.0116, -7),
    "HOU":  ("KXHIGHTHOU", "KHOU", 29.6454, -95.2789, -6),   # Hobby, not Bush/IAH
    "MIN":  ("KXHIGHTMIN", "KMSP", 44.8848, -93.2223, -6),
    "OKC":  ("KXHIGHTOKC", "KOKC", 35.3931, -97.6007, -6),
    "SFO":  ("KXHIGHTSFO", "KSFO", 37.6190, -122.3749, -8),
    "DC":   ("KXHIGHTDC",  "KDCA", 38.8483, -77.0341, -5),   # Reagan National
    "BOS":  ("KXHIGHTBOS", "KBOS", 42.3606, -71.0097, -5),
    "DAL":  ("KXHIGHTDAL", "KDFW", 32.8998, -97.0403, -6),   # DFW, not Love Field
    "SEA":  ("KXHIGHTSEA", "KSEA", 47.4447, -122.3136, -8),
    "LV":   ("KXHIGHTLV",  "KLAS", 36.0719, -115.1634, -8),
    "ATL":  ("KXHIGHTATL", "KATL", 33.6407, -84.4277, -5),
    "NOLA": ("KXHIGHTNOLA", "KMSY", 29.9934, -90.2580, -6),
    "SAN":  ("KXHIGHTSAN", "KSAN", 32.7336, -117.1831, -8),
    "SDF":  ("KXHIGHTSDF", "KSDF", 38.1740, -85.7365, -5),
    "EWR":  ("KXHIGHTEWR", "KEWR", 40.6925, -74.1687, -5),
}

_points_cache = {}
_s = requests.Session()
_s.headers.update({"User-Agent": config.USER_AGENT, "Accept": "application/geo+json"})


def lst_now(std_offset):
    return datetime.now(timezone.utc) + timedelta(hours=std_offset)


def climate_day_bounds_utc(day, std_offset):
    """UTC start/end of a local-standard-time calendar day."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - timedelta(hours=std_offset)
    return start, start + timedelta(days=1)


def _get(url, params=None):
    """NWS returns occasional 500s; retry twice before giving up on this city."""
    import time
    for attempt in range(3):
        try:
            r = _s.get(url, params=params, timeout=20)
            if r.status_code >= 500 and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def _points(code):
    _, _, lat, lon, _ = CITIES[code]
    if code not in _points_cache:
        _points_cache[code] = _get(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")["properties"]
    return _points_cache[code]


def _temp_f(p):
    temp = p.get("temperature")
    if isinstance(temp, dict):                  # newer format {value, unitCode}
        v = temp.get("value")
        return None if v is None else (v * 9 / 5 + 32 if "degC" in (temp.get("unitCode") or "") else v)
    if temp is not None and p.get("temperatureUnit") == "C":
        return temp * 9 / 5 + 32
    return temp


def daily_high(code, day):
    """The official NWS forecast high for `day` (what meteorologists publish, e.g. 'High 61').
    It already accounts for the afternoon peak that hourly readings undershoot."""
    periods = _get(_points(code)["forecast"])["properties"]["periods"]
    for p in periods:
        if not p.get("isDaytime"):
            continue
        start = datetime.fromisoformat(p["startTime"])          # local time with offset
        if start.date() == day:
            t = _temp_f(p)
            return float(t) if t is not None else None
    return None


def hourly_forecast(code):
    """List of (utc_datetime, temp_F)."""
    periods = _get(_points(code)["forecastHourly"])["properties"]["periods"]
    out = []
    for p in periods:
        t = datetime.fromisoformat(p["startTime"]).astimezone(timezone.utc)
        temp = _temp_f(p)
        if temp is not None:
            out.append((t, float(temp)))
    return out


def observed_max_f(code, day):
    """Highest observed temp (F) so far in the climate day, or None."""
    _, station, _, _, off = CITIES[code]
    start, end = climate_day_bounds_utc(day, off)
    data = _get(f"https://api.weather.gov/stations/{station}/observations",
                {"start": start.isoformat().replace("+00:00", "Z"),
                 "end": min(end, datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")})
    temps = []
    for f in data.get("features", []):
        v = (f["properties"].get("temperature") or {}).get("value")
        if v is not None:
            temps.append(v * 9 / 5 + 32)
    return round(max(temps)) if temps else None
