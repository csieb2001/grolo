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
import argparse, json, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "grobro", "sidecar"))
from fit import run_fit  # noqa: E402


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


def compass(az):
    names = ["N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return names[int((az + 11.25) // 22.5) % 16]


def main():
    load_env()
    ap = argparse.ArgumentParser(description="Ausrichtung der PV-Strings aus Messdaten schätzen (gleiche Rechnung wie im Sidecar weather)")
    ap.add_argument("--days", type=int, default=30); ap.add_argument("--min-dni", type=float, default=150.0, help="nur Stunden mit Direktnormalstrahlung ab W/m² (Standard 150)")
    ap.add_argument("--lat", type=float); ap.add_argument("--lon", type=float); ap.add_argument("--apply", action="store_true", help="Ergebnis als Konfiguration auf den Broker schreiben")
    a = ap.parse_args()
    base = os.environ.get("HA_BASE_TOPIC", "homeassistant")
    site = site_from_broker(base) or {}
    lat = a.lat or site.get("lat") or float(os.environ.get("WEATHER_LAT", 0) or 0); lon = a.lon or site.get("lon") or float(os.environ.get("WEATHER_LON", 0) or 0)
    if not lat or not lon:
        sys.exit("Kein Standort: --lat/--lon angeben, WEATHER_LAT/LON in .env setzen oder Ort auf der Einstellungsseite wählen")
    pr = float(os.environ.get("STRING_PR", 0.85) or 0.85)
    print(f"Standort {site.get('name') or ''} ({lat:.4f}, {lon:.4f}), letzte {a.days} Tage, Stunden mit DNI ≥ {a.min_dni:.0f} W/m²\n")
    res = run_fit(os.environ.get("INFLUX_URL", "http://127.0.0.1:8086"), os.environ["INFLUX_TOKEN"], os.environ.get("INFLUX_ORG", "growatt"), os.environ.get("INFLUX_BUCKET", "nexa"), lat, lon, a.days, a.min_dni, pr)
    found = {}
    for s, v in res["strings"].items():
        st = v["status"]
        if st == "unused":
            print(f"String {s}: Spitze {v['peak_w']:.0f} W, kein Modul angeschlossen"); continue
        if st == "insufficient":
            print(f"String {s}: nicht bestimmbar ({v.get('hours', 0)} brauchbare Sonnenstunden, Spitze {v['peak_w']:.0f} W). Mehr sonnige Tage abwarten oder --min-dni senken."); continue
        print(f"String {s}: Azimut {v['azimuth']}° ({compass(v['azimuth'])}), Neigung {v['tilt']}°, wirksam {v['wp_eff']:.0f} W (≈ {v['wp']:.0f} Wp bei PR {pr}), Spitze gemessen {v['peak_w']:.0f} W")
        print(f"          R² = {v['r2']:.2f} ({'gut' if st == 'ok' else 'unsicher'}) aus {v['hours']} Sonnenstunden; ähnlich gute Lösungen: Azimut {v['az_range'][0]}–{v['az_range'][1]}°, Neigung {v['tilt_range'][0]}–{v['tilt_range'][1]}°")
        found[s] = {"tilt": float(v["tilt"]), "azimuth": float(v["azimuth"]), "wp": float(v["wp"])}
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
        print("\nMit --apply auf den Broker schreiben, oder auf der Einstellungsseite „Übernehmen“ klicken.")


if __name__ == "__main__":
    main()
