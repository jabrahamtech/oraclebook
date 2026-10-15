"""Round 3 helpers: settlement stations for the Kalshi daily-high markets and the running maximum
temperature an observer could know from METARs at any moment of the climate day.

A METAR gives the temperature to 0.1 °C in its T-group and, at synoptic hours, the 6-hour maximum
(group 1sTTT). Each observation is treated as known `LAG` after its valid time. The NWS climate
day for the daily high runs midnight to midnight local standard time, which is also when the
Kalshi markets close.
"""

import pathlib
import re

import numpy as np
import pandas as pd

RAW = pathlib.Path(__file__).resolve().parents[1] / "data" / "raw"
LAG = pd.Timedelta(minutes=10)
SPIKE = 3.0

# series -> (ASOS station, UTC offset of local standard time in hours)
STATIONS = {
    "KXHIGHNY": ("NYC", -5),
    "KXHIGHCHI": ("MDW", -6),
    "KXHIGHMIA": ("MIA", -5),
    "KXHIGHLAX": ("LAX", -8),
    "KXHIGHAUS": ("AUS", -6),
    "KXHIGHDEN": ("DEN", -7),
    "KXHIGHPHIL": ("PHL", -5),
    "KXHIGHTSFO": ("SFO", -8),
    "KXHIGHTATL": ("ATL", -5),
    "KXHIGHTHOU": ("HOU", -6),
    "KXHIGHTDC": ("DCA", -5),
    "KXHIGHTSEA": ("SEA", -8),
    "KXHIGHTLV": ("LAS", -8),
    "KXHIGHTBOS": ("BOS", -5),
    "KXHIGHTPHX": ("PHX", -7),
    "KXHIGHTDAL": ("DFW", -6),
    "KXHIGHTMIN": ("MSP", -6),
    "KXHIGHTNOLA": ("MSY", -6),
    "KXHIGHTOKC": ("OKC", -6),
    "KXHIGHTSATX": ("SAT", -6),
}

T_GROUP = re.compile(r" T([01])(\d{3})([01])\d{3}")
MAX6 = re.compile(r" 1([01])(\d{3})(?= )")
MIN6 = re.compile(r" 2([01])(\d{3})(?= )")
BODY = re.compile(r" (M?\d{2})/(M?\d{2})? ")


def c_to_f(c):
    return c * 9 / 5 + 32


def _sign(s, v):
    return -v if s == "1" else v


def parse(metar):
    """(temperature °C, 6-hour max °C, 6-hour min °C; NaN if absent) from one METAR string."""
    rmk = metar.split(" RMK ", 1)
    t = T_GROUP.search(metar)
    if t:
        temp = _sign(t.group(1), int(t.group(2)) / 10)
    else:
        b = BODY.search(rmk[0] + " ")
        temp = float(b.group(1).replace("M", "-")) if b else np.nan
    six = []
    for pat in (MAX6, MIN6):
        m = pat.search(" " + rmk[1] + " ") if len(rmk) == 2 else None
        six.append(_sign(m.group(1), int(m.group(2)) / 10) if m else np.nan)
    return temp, *six


def observations(station, offset, kind="max", six_hour=True):
    """One row per METAR: availability time (UTC), climate day, best known max °F so far. With
    kind="min" every temperature is negated, so the same columns track minus the running min.
    six_hour=False ignores the 6-hour extreme groups (spot readings only: a weaker but safer bound;
    a corrupt 6-hour minimum, 8 °F below every reading of the day, appeared on 2026-09-19 NYC)."""
    d = pd.read_csv(RAW / "iem_asos" / f"{station}.csv", usecols=["valid", "metar"]).dropna()
    d["valid"] = pd.to_datetime(d.valid, utc=True)
    temp, mx6, mn6 = zip(*(parse(" " + m + " ") for m in d.metar), strict=True)
    sign = 1 if kind == "max" else -1
    d["temp_f"] = sign * c_to_f(np.array(temp))
    d["max6_f"] = sign * c_to_f(np.array(mx6 if kind == "max" else mn6))
    d["avail"] = d.valid + LAG
    local = d.valid + pd.Timedelta(hours=offset)
    d["day"] = local.dt.floor("D").dt.tz_localize(None)
    # A 6-hour max issued in the first hours of the day reaches back into the previous day.
    hour = local.dt.hour + local.dt.minute / 60
    d.loc[hour < 6, "max6_f"] = np.nan
    d["obs_f"] = d[["temp_f", "max6_f"]].max(axis=1)
    d = d.sort_values("valid").reset_index(drop=True)
    # A lone sensor spike (seen once in 1,220 city-days) is not in the climate report, so a
    # reading counts once the next one arrives, capped at SPIKE °F above the warmer neighbour;
    # a 6-hour maximum is capped at SPIKE °F above the (capped) readings of those 6 hours.
    nb = np.maximum(d.temp_f.shift(1), d.temp_f.shift(-1))
    spot = np.minimum(d.temp_f, nb + SPIKE)
    six = pd.Series(spot.to_numpy(), index=d.valid).rolling("6h").max().to_numpy()
    d["conf_f"] = np.fmax(spot, np.minimum(d.max6_f, six + SPIKE)) if six_hour else spot
    d["conf_avail"] = d.avail.shift(-1)
    d = d.dropna(subset=["conf_avail"])
    d["run_max_f"] = d.groupby("day").conf_f.cummax()
    return d[["valid", "avail", "conf_avail", "day", "temp_f", "obs_f", "conf_f", "run_max_f"]]


def daily_max(obs, confirmed=True):
    return obs.groupby("day")["conf_f" if confirmed else "obs_f"].max()


def known_max(obs, at):
    """Confirmed running max (°F, unrounded) of the climate day containing each time in `at`, and
    the latest reading, using only observations available by then. `at` is a Series of UTC
    timestamps; returns aligned Series."""
    q = pd.DataFrame({"at": at.to_numpy()}, index=at.index).sort_values("at")
    o = obs.sort_values("conf_avail")[["conf_avail", "day", "run_max_f", "temp_f"]]
    r = pd.merge_asof(q.reset_index(), o, left_on="at", right_on="conf_avail", direction="backward")
    return r.set_index(r.columns[0])[["day", "run_max_f", "temp_f"]].reindex(at.index)


def low_series(high_series):
    """Daily-low series for the same city, e.g. KXHIGHNY -> KXLOWTNYC, KXHIGHTBOS -> KXLOWTBOS."""
    city = high_series.removeprefix("KXHIGHT").removeprefix("KXHIGH")
    return "KXLOWT" + {"NY": "NYC"}.get(city, city)


def event_day(event_ticker):
    """Climate day from an event ticker like KXHIGHNY-26OCT13."""
    return pd.to_datetime(event_ticker.split("-")[-1], format="%y%b%d")
