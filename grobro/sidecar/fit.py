#!/usr/bin/env python3
"""fit: schätzt Neigung und Ausrichtung jedes PV-Strings aus den Messdaten (Kern für weather.py und scripts/fit-orientation.py).

Stundenmittel der String-Leistung aus InfluxDB, Stundenwerte von Global-, Direktnormal- und Diffusstrahlung von Open-Meteo
für denselben Zeitraum. Für jede Kombination aus Azimut (5°) und Neigung (5°) wird die Einstrahlung auf die Modulfläche
berechnet (solar.py) und per kleinster Quadrate an die gemessene Kurve angepasst. Nur Stunden mit nennenswerter
Direktstrahlung zählen, denn nur dann unterscheidet sich eine Süd- von einer Westfläche.

run_fit(...) liefert ein Ergebnis-Dict, das so per MQTT (retained <BASE>/grolo/fit) und an die Website geht:
  {"updated": 1757250000, "days": 30, "lat": 53.6, "lon": 9.8, "min_dni": 150,
   "strings": {"1": {"status": "ok" | "uncertain" | "insufficient" | "unused", "azimuth": 175, "tilt": 30, "wp": 420,
                     "wp_eff": 357.0, "r2": 0.91, "hours": 64, "az_range": [165, 185], "tilt_range": [25, 40], "peak_w": 298.0}}}
"""
import csv, io, json, os, sys, time, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from solar import sun_position, poa_irradiance  # noqa: E402


def influx_hourly(url, token, org, bucket, days):
    """Stundenmittel der Leistung je String: {"1": {unix_hour_end: W}, ...}"""
    fields = " or ".join(f'r._field == "pv{i}{s}"' for i in range(1, 5) for s in ("Voltage", "Current"))
    maps = ", ".join(f'b |> map(fn: (r) => ({{_time: r._time, string: "{i}", _value: r.pv{i}Voltage * r.pv{i}Current}}))' for i in range(1, 5))
    flux = f'''b = from(bucket: "{bucket}")
  |> range(start: -{int(days)}d)
  |> filter(fn: (r) => r._measurement == "nexa" and ({fields}))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.pv1Voltage and exists r.pv1Current)
union(tables: [{maps}])
  |> group(columns: ["string"])
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value", "string"])'''
    q = urllib.parse.urlencode({"org": org})
    req = urllib.request.Request(f"{url}/api/v2/query?{q}", data=json.dumps({"query": flux, "type": "flux"}).encode(), method="POST",
                                 headers={"Authorization": f"Token {token}", "Content-Type": "application/json", "Accept": "application/csv"})
    with urllib.request.urlopen(req, timeout=120) as r:
        text = r.read().decode()
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        if not row.get("_time") or row.get("_value") in (None, ""):
            continue
        ts = int(time.mktime(time.strptime(row["_time"][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone)
        out.setdefault(row["string"], {})[ts] = float(row["_value"])
    return out


def open_meteo_history(lat, lon, days):
    """{unix_hour_end: (GHI, DNI, DHI)} als Mittel der vorangehenden Stunde."""
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=shortwave_radiation,direct_normal_irradiance,diffuse_radiation"
           f"&past_days={min(int(days), 92)}&forecast_days=1&timezone=UTC&timeformat=unixtime")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "GroLo fit"}), timeout=30) as r:
        d = json.load(r)["hourly"]
    return {int(t): (d["shortwave_radiation"][i], d["direct_normal_irradiance"][i], d["diffuse_radiation"][i]) for i, t in enumerate(d["time"])
            if d["shortwave_radiation"][i] is not None and d["direct_normal_irradiance"][i] is not None}


def fit_string(power, wx, lat, lon, min_dni=150.0):
    """power: {hour_end: W}. Liefert (best (rss, az, tilt, k), close [(rss, az, tilt, k)], hours, r2)."""
    hours = []
    for ts, p in power.items():
        w = wx.get(ts)
        if not w or w[0] < 30 or w[1] < min_dni:
            continue
        az, el = sun_position(ts - 1800, lat, lon)
        if el < 5:
            continue
        hours.append((p, w, az, el))
    if len(hours) < 12:
        return None, [], len(hours), 0.0
    pm = sum(h[0] for h in hours) / len(hours); sst = sum((h[0] - pm) ** 2 for h in hours) or 1.0
    results = []
    for azimuth in range(0, 360, 5):
        for tilt in range(0, 91, 5):
            m = [poa_irradiance(w[0], w[1], w[2], az, el, tilt, azimuth) for _, w, az, el in hours]
            smm = sum(x * x for x in m)
            if smm <= 0:
                continue
            k = sum(x * h[0] for x, h in zip(m, hours)) / smm
            rss = sum((h[0] - k * x) ** 2 for x, h in zip(m, hours))
            results.append((rss, azimuth, tilt, k))
    results.sort()
    best = results[0]
    return best, [r for r in results if r[0] <= best[0] * 1.03], len(hours), 1.0 - best[0] / sst


def run_fit(influx_url, token, org, bucket, lat, lon, days=30, min_dni=150.0, pr=0.85):
    power = influx_hourly(influx_url, token, org, bucket, days)
    wx = open_meteo_history(lat, lon, days)
    res = {"updated": int(time.time()), "days": int(days), "lat": lat, "lon": lon, "min_dni": min_dni, "pr": pr, "strings": {}}
    for s in ("1", "2", "3", "4"):
        p = power.get(s, {})
        pk = max(p.values()) if p else 0.0
        if pk < 20:
            res["strings"][s] = {"status": "unused", "peak_w": round(pk, 1)}; continue
        best, close, n, r2 = fit_string(p, wx, lat, lon, min_dni)
        if not best:
            res["strings"][s] = {"status": "insufficient", "hours": n, "peak_w": round(pk, 1)}; continue
        rss, az, tilt, k = best
        azs = sorted(r[1] for r in close); tilts = sorted(r[2] for r in close)
        wp_eff = k * 1000.0
        status = "ok" if (r2 >= 0.75 and azs[-1] - azs[0] < 60) else "uncertain" if (r2 >= 0.5 and azs[-1] - azs[0] < 120) else "insufficient"
        res["strings"][s] = {"status": status, "azimuth": az, "tilt": tilt, "wp": round(wp_eff / pr / 5.0) * 5.0, "wp_eff": round(wp_eff, 1), "r2": round(r2, 3),
                             "hours": n, "az_range": [azs[0], azs[-1]], "tilt_range": [tilts[0], tilts[-1]], "peak_w": round(pk, 1)}
    return res
