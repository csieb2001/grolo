#!/usr/bin/env python3
"""advice: Veränderungsempfehlungen je String aus Standort, Ausrichtung und Messdaten.

1. Jahresmodell: ein Jahr Stundenwerte GHI/DNI/DHI aus dem Open-Meteo-Archiv (ERA5, kostenlos) für den Standort, daraus
   die Einstrahlung auf jede Ausrichtung (Azimut 10°, Neigung 10°, um das Optimum auf 5° verfeinert) in kWh/kWp und Jahr.
   Verglichen wird die aktuelle Ausrichtung (konfiguriert, sonst geschätzt) mit dem Optimum und praktischen Alternativen:
   gleicher Azimut mit bester Neigung, senkrecht (Balkon) mit bestem Azimut, flach. Dazu die Winter-/Sommerverteilung.
2. Verschattung: gemessene Stundenleistung der letzten Tage gegen das Modell mit echtem Wetter; Sonnenazimut-Bereiche
   (10°-Bins), in denen die Messung deutlich unter dem Modell bleibt, während es sonst passt, werden als Hindernis gemeldet,
   mit Uhrzeit (heute) und Verlustanteil.

Ergebnis (retained <BASE>/grolo/advice, Website site.advice):
  {"updated", "period": ["2025-09-01", "2026-08-31"], "site_best": {"tilt", "azimuth", "kwh_kwp"}, "free_inputs": 3,
   "strings": {"1": {"basis": "config" | "fit" | null, "tilt", "azimuth", "kwh_kwp", "pct_of_best",
                     "best": {"tilt", "azimuth", "kwh_kwp", "gain_pct"}, "same_azimuth": {"tilt", "gain_pct"},
                     "vertical": {"azimuth", "kwh_kwp", "gain_pct"}, "flat": {"kwh_kwp", "gain_pct"},
                     "winter_share_pct", "best_winter_share_pct",
                     "shading": [{"az_from", "az_to", "ratio", "hours", "time_from", "time_to"}], "shading_loss_pct", "ratio_median"}}}
"""
import datetime, json, math, os, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from solar import sun_position, poa_irradiance  # noqa: E402

_ARCHIVE = {}   # (lat, lon, monat) -> Stundenliste [(ts, ghi, dni, dhi, az, el)]


def annual_hours(lat, lon):
    """Ein volles Jahr bis zum letzten Monatsende, nur Stunden mit Sonne; Cache je Monat."""
    today = datetime.date.today()
    end = today.replace(day=1) - datetime.timedelta(days=1)
    start = datetime.date(end.year - 1 if end.month != 12 else end.year, end.month % 12 + 1, 1)   # genau 12 Monate
    key = (round(lat, 2), round(lon, 2), end.isoformat())
    if key in _ARCHIVE:
        return _ARCHIVE[key], start, end
    url = (f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}&start_date={start}&end_date={end}"
           "&hourly=shortwave_radiation,direct_normal_irradiance,diffuse_radiation&timezone=UTC&timeformat=unixtime")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "GroLo advice"}), timeout=60) as r:
        d = json.load(r)["hourly"]
    hours = []
    for i, ts in enumerate(d["time"]):
        ghi, dni, dhi = d["shortwave_radiation"][i], d["direct_normal_irradiance"][i], d["diffuse_radiation"][i]
        if not ghi or ghi < 5 or dni is None or dhi is None:
            continue
        az, el = sun_position(int(ts) - 1800, lat, lon)
        if el <= 0:
            continue
        hours.append((int(ts), ghi, dni, dhi, az, el))
    _ARCHIVE.clear(); _ARCHIVE[key] = hours
    return hours, start, end


def yearly_kwh_kwp(hours, tilt, azimuth, pr=0.85):
    return sum(poa_irradiance(g, dn, df, az, el, tilt, azimuth) for _, g, dn, df, az, el in hours) / 1000.0 * pr


def winter_share(hours, tilt, azimuth):
    """Anteil des Jahresertrags im Winterhalbjahr (Oktober bis März)."""
    w = t = 0.0
    for ts, g, dn, df, az, el in hours:
        v = poa_irradiance(g, dn, df, az, el, tilt, azimuth); t += v
        if datetime.datetime.utcfromtimestamp(ts).month in (10, 11, 12, 1, 2, 3):
            w += v
    return 100.0 * w / t if t else 0.0


def site_optimum(hours, pr):
    best = None
    for azimuth in range(0, 360, 10):
        for tilt in range(0, 91, 10):
            v = yearly_kwh_kwp(hours, tilt, azimuth, pr)
            if best is None or v > best[0]:
                best = (v, tilt, azimuth)
    v0, t0, a0 = best
    for azimuth in range(a0 - 10, a0 + 11, 5):
        for tilt in range(max(0, t0 - 10), min(90, t0 + 10) + 1, 5):
            v = yearly_kwh_kwp(hours, tilt, azimuth % 360, pr)
            if v > best[0]:
                best = (v, tilt, azimuth % 360)
    return {"kwh_kwp": round(best[0]), "tilt": best[1], "azimuth": best[2]}


def hhmm_today(lat, lon, target_az, tz_offset):
    """Lokale Uhrzeit (heute), zu der die Sonne den Azimut erreicht; None, wenn nicht über dem Horizont."""
    day = int(time.time()) // 86400 * 86400 - tz_offset
    prev = None
    for m in range(0, 1440, 5):
        ts = day + m * 60
        az, el = sun_position(ts, lat, lon)
        if prev is not None and el > 0 and prev <= target_az < az:
            lt = time.localtime(ts)
            return "%02d:%02d" % (lt.tm_hour, lt.tm_min)
        prev = az
    return None


def shading(power, wx, lat, lon, tilt, azimuth, wp_eff):
    """Messung/Modell je 10°-Sonnenazimut. Liefert (Liste auffälliger Bereiche, Verlust in %, Median-Verhältnis)."""
    bins = {}
    tot_exp = tot_meas = 0.0
    for ts, p in power.items():
        w = wx.get(ts)
        if not w or w[1] < 150:
            continue
        az, el = sun_position(ts - 1800, lat, lon)
        if el < 5:
            continue
        exp = poa_irradiance(w[0], w[1], w[2], az, el, tilt, azimuth) / 1000.0 * wp_eff
        if exp < 15:
            continue
        b = int(az // 10) * 10
        e, m, n = bins.get(b, (0.0, 0.0, 0)); bins[b] = (e + exp, m + p, n + 1)
        tot_exp += exp; tot_meas += p
    if len(bins) < 3:
        return [], 0.0, None
    ratios = sorted(m / e for e, m, n in bins.values() if e > 0)
    median = ratios[len(ratios) // 2]
    tz_offset = -time.timezone if not time.localtime().tm_isdst else -time.altzone
    flagged = []; loss = 0.0
    for b in sorted(bins):
        e, m, n = bins[b]; r = m / e if e else 0.0
        if median > 0.3 and r < 0.6 * median and n >= 2:
            flagged.append({"az_from": b, "az_to": b + 10, "ratio": round(r / median, 2), "hours": n, "time_from": hhmm_today(lat, lon, b, tz_offset), "time_to": hhmm_today(lat, lon, b + 10, tz_offset)})
            loss += (median * e - m)
    merged = []
    for f in flagged:
        if merged and merged[-1]["az_to"] == f["az_from"]:
            last = merged[-1]; last["az_to"] = f["az_to"]; last["time_to"] = f["time_to"]; last["hours"] += f["hours"]; last["ratio"] = round(min(last["ratio"], f["ratio"]), 2)
        else:
            merged.append(dict(f))
    return merged, (100.0 * loss / (median * tot_exp) if tot_exp and median else 0.0), round(median, 2)


def build_advice(lat, lon, strings_cfg, fit, power, wx, pr=0.85):
    hours, start, end = annual_hours(lat, lon)
    best_site = site_optimum(hours, pr)
    out = {"updated": int(time.time()), "period": [start.isoformat(), end.isoformat()], "site_best": best_site, "strings": {}, "free_inputs": 0}
    for s in ("1", "2", "3", "4"):
        p = power.get(s, {}); pk = max(p.values()) if p else 0.0
        f = (fit or {}).get("strings", {}).get(s, {}) if fit else {}
        cfg = (strings_cfg or {}).get(int(s)) or (strings_cfg or {}).get(s)
        if pk < 20 and not cfg:
            out["free_inputs"] += 1; out["strings"][s] = {"basis": None, "unused": True}; continue
        if cfg and cfg.get("tilt") is not None:
            basis, tilt, azimuth = "config", float(cfg["tilt"]), float(cfg["azimuth"])
            wp_eff = float(cfg.get("wp") or 0) * pr or float(f.get("wp_eff") or 0)
        elif f.get("status") in ("ok", "uncertain"):
            basis, tilt, azimuth, wp_eff = "fit", float(f["tilt"]), float(f["azimuth"]), float(f["wp_eff"])
        else:
            out["strings"][s] = {"basis": None, "unused": False, "peak_w": round(pk, 1)}; continue
        cur = yearly_kwh_kwp(hours, tilt, azimuth, pr)
        same_az = max(((yearly_kwh_kwp(hours, t, azimuth, pr), t) for t in range(0, 91, 5)))
        vert = max(((yearly_kwh_kwp(hours, 90, a, pr), a) for a in range(0, 360, 10)))
        flat = yearly_kwh_kwp(hours, 0, 180, pr)
        gain = lambda v: round(100.0 * (v - cur) / cur, 1) if cur else None
        entry = {"basis": basis, "tilt": tilt, "azimuth": azimuth, "kwh_kwp": round(cur), "pct_of_best": round(100.0 * cur / best_site["kwh_kwp"]) if best_site["kwh_kwp"] else None,
                 "best": {**best_site, "gain_pct": gain(best_site["kwh_kwp"])},
                 "same_azimuth": {"tilt": same_az[1], "kwh_kwp": round(same_az[0]), "gain_pct": gain(same_az[0])},
                 "vertical": {"azimuth": vert[1], "kwh_kwp": round(vert[0]), "gain_pct": gain(vert[0])},
                 "flat": {"kwh_kwp": round(flat), "gain_pct": gain(flat)},
                 "winter_share_pct": round(winter_share(hours, tilt, azimuth)), "best_winter_share_pct": round(winter_share(hours, best_site["tilt"], best_site["azimuth"])),
                 "peak_w": round(pk, 1), "wp_eff": round(wp_eff)}
        if wp_eff > 0 and p:
            sh, loss, med = shading(p, wx, lat, lon, tilt, azimuth, wp_eff)
            entry.update({"shading": sh, "shading_loss_pct": round(loss, 1), "ratio_median": med})
        out["strings"][s] = entry
    return out
