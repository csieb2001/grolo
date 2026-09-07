#!/usr/bin/env python3
"""fit-orientation: schätzt Neigung und Ausrichtung jedes PV-Strings aus den Messdaten.

    python3 scripts/fit-orientation.py [--days 30] [--apply]

Holt die Stundenmittel der String-Leistung aus InfluxDB und die Stundenwerte von Global-, Direktnormal- und
Diffusstrahlung von Open-Meteo für denselben Zeitraum (Standort aus der Einstellungsseite, sonst .env). Für jede
Kombination aus Azimut (5°-Schritte) und Neigung (5°-Schritte) wird die Einstrahlung auf die Modulfläche berechnet
(solar.py, isotropes Modell) und per kleinster Quadrate an die gemessene Kurve angepasst. Die Kombination mit dem
kleinsten Restfehler ist die Schätzung; der Skalierungsfaktor ergibt die wirksame Modulleistung (Wp × Performance-Ratio).

Nur Stunden mit nennenswerter Direktstrahlung zählen, denn nur dann unterscheidet sich eine Süd- von einer Westfläche.
Braucht einige sonnige Tage; bei wenig Direktstrahlung ist die Schätzung unscharf, das Skript sagt das dann.

--apply schreibt die gefundenen Werte als retained Nachricht <BASE>/grolo/config/site auf den Broker (über den
Mosquitto-Container), so als hätte man sie auf der Einstellungsseite eingetragen. Standort bleibt erhalten.

Umgebung (.env im Projektordner wird gelesen): INFLUX_URL (Standard http://127.0.0.1:8086), INFLUX_TOKEN, INFLUX_ORG,
INFLUX_BUCKET, WEATHER_LAT, WEATHER_LON, HA_BASE_TOPIC, STRING_PR.
"""
import argparse, csv, io, json, math, os, subprocess, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "grobro", "sidecar"))
from solar import sun_position, poa_irradiance  # noqa: E402


def load_env():
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"'))


def site_from_broker(base):
    """Retained Konfiguration der Einstellungsseite, falls der Mosquitto-Container erreichbar ist."""
    try:
        out = subprocess.run(["docker", "exec", "grolo-mosquitto", "mosquitto_sub", "-t", f"{base}/grolo/config/site", "-C", "1", "-W", "3"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return json.loads(out) if out else None
    except Exception:
        return None


def influx_hourly(url, token, org, bucket, days):
    """Stundenmittel der Leistung je String: {string: {unix_hour_end: W}}"""
    fields = " or ".join(f'r._field == "pv{i}{s}"' for i in range(1, 5) for s in ("Voltage", "Current"))
    maps = ", ".join(f'b |> map(fn: (r) => ({{_time: r._time, string: "{i}", _value: r.pv{i}Voltage * r.pv{i}Current}}))' for i in range(1, 5))
    flux = f'''b = from(bucket: "{bucket}")
  |> range(start: -{days}d)
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


def open_meteo(lat, lon, days):
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=shortwave_radiation,direct_normal_irradiance,diffuse_radiation"
           f"&past_days={min(days, 92)}&forecast_days=1&timezone=UTC&timeformat=unixtime")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "GroLo fit-orientation"}), timeout=30) as r:
        d = json.load(r)["hourly"]
    return {int(t): (d["shortwave_radiation"][i], d["direct_normal_irradiance"][i], d["diffuse_radiation"][i]) for i, t in enumerate(d["time"])
            if d["shortwave_radiation"][i] is not None}


def fit_string(power, wx, lat, lon, min_dni):
    """power: {hour_end: W}. Liefert (best, alternatives, hours, r2)"""
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
    r2 = 1.0 - best[0] / sst
    close = [r for r in results if r[0] <= best[0] * 1.03]
    return best, close, len(hours), r2


def compass(az):
    names = ["N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return names[int((az + 11.25) // 22.5) % 16]


def main():
    load_env()
    ap = argparse.ArgumentParser(description="Ausrichtung der PV-Strings aus Messdaten schätzen")
    ap.add_argument("--days", type=int, default=30); ap.add_argument("--min-dni", type=float, default=150.0, help="nur Stunden mit Direktnormalstrahlung ab W/m² (Standard 150)")
    ap.add_argument("--lat", type=float); ap.add_argument("--lon", type=float); ap.add_argument("--apply", action="store_true", help="Ergebnis als Konfiguration auf den Broker schreiben")
    a = ap.parse_args()
    base = os.environ.get("HA_BASE_TOPIC", "homeassistant")
    site = site_from_broker(base) or {}
    lat = a.lat or site.get("lat") or float(os.environ.get("WEATHER_LAT", 0) or 0); lon = a.lon or site.get("lon") or float(os.environ.get("WEATHER_LON", 0) or 0)
    if not lat or not lon:
        sys.exit("Kein Standort: --lat/--lon angeben, WEATHER_LAT/LON in .env setzen oder Ort auf der Einstellungsseite wählen")
    pr = float(os.environ.get("STRING_PR", 0.85) or 0.85)
    print(f"Standort {site.get('name') or ''} ({lat:.4f}, {lon:.4f}), letzte {a.days} Tage, Stunden mit DNI ≥ {a.min_dni:.0f} W/m²")
    power = influx_hourly(os.environ.get("INFLUX_URL", "http://127.0.0.1:8086"), os.environ["INFLUX_TOKEN"], os.environ.get("INFLUX_ORG", "growatt"), os.environ.get("INFLUX_BUCKET", "nexa"), a.days)
    wx = open_meteo(lat, lon, a.days)
    print(f"{sum(len(v) for v in power.values())} Stundenwerte aus InfluxDB, {len(wx)} Wetterstunden von Open-Meteo\n")
    found = {}
    for s in sorted(power):
        pk = max(power[s].values()) if power[s] else 0.0
        if pk < 20:
            print(f"String {s}: Spitze {pk:.0f} W, kein Modul angeschlossen, übersprungen"); continue
        best, close, n, r2 = fit_string(power[s], wx, lat, lon, a.min_dni)
        if not best:
            print(f"String {s}: nur {n} brauchbare Sonnenstunden, zu wenig für eine Schätzung (mehr Tage abwarten oder --min-dni senken)"); continue
        rss, az, tilt, k = best
        azs = sorted(r[1] for r in close); tilts = sorted(r[2] for r in close)
        wp_eff = k * 1000.0
        if azs[-1] - azs[0] >= 120 or r2 < 0.5:
            print(f"String {s}: Ausrichtung nicht bestimmbar (R² = {r2:.2f}, {n} Sonnenstunden, ähnlich gute Lösungen von Azimut {azs[0]}° bis {azs[-1]}°).")
            print(f"          Zu wenig Direktstrahlung, starke Verschattung oder noch zu wenige sonnige Tage. Später erneut versuchen; Spitze bisher {pk:.0f} W.")
            continue
        quality = "gut" if r2 > 0.85 else "brauchbar" if r2 > 0.6 else "unsicher"
        print(f"String {s}: Azimut {az}° ({compass(az)}), Neigung {tilt}°, wirksam {wp_eff:.0f} W (≈ {wp_eff / pr:.0f} Wp bei PR {pr}), Spitze gemessen {pk:.0f} W")
        print(f"          Anpassung R² = {r2:.2f} ({quality}) aus {n} Sonnenstunden; ähnlich gute Lösungen: Azimut {azs[0]}–{azs[-1]}°, Neigung {tilts[0]}–{tilts[-1]}°")
        found[s] = {"tilt": float(tilt), "azimuth": float(az), "wp": round(wp_eff / pr / 5.0) * 5.0}
    if not found:
        return
    print("\nFür .env:")
    for s, c in found.items():
        print(f"STRING{s}_TILT={c['tilt']:.0f}\nSTRING{s}_AZIMUTH={c['azimuth']:.0f}\nSTRING{s}_WP={c['wp']:.0f}")
    if a.apply:
        cfg = {"name": site.get("name", ""), "lat": lat, "lon": lon, "strings": {**{str(k): v for k, v in (site.get("strings") or {}).items()}, **found}, "updated": int(time.time()), "source": "fit-orientation"}
        subprocess.run(["docker", "exec", "grolo-mosquitto", "mosquitto_pub", "-r", "-t", f"{base}/grolo/config/site", "-m", json.dumps(cfg)], check=True)
        print(f"\nKonfiguration auf den Broker geschrieben ({base}/grolo/config/site), der Wetterdienst rechnet neu.")
    else:
        print("\nMit --apply auf den Broker schreiben (wie auf der Einstellungsseite gespeichert) oder dort von Hand eintragen.")


if __name__ == "__main__":
    main()
