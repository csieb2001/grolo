#!/usr/bin/env python3
"""weather: solar-relevante Wetterdaten von Open-Meteo (kostenlos, ohne Schlüssel) für den Anlagenstandort.

Alle 10 Minuten:
  - aktueller Zustand (Temperatur, Bewölkung, WMO-Wettercode, Global-/Direkt-/Diffusstrahlung, Wind, Tag/Nacht)
  - Tageswerte (Sonnenauf-/-untergang, Sonnenscheindauer, Strahlungssumme)
  - stündliche Vorhersage 48 h (Globalstrahlung, Bewölkung, Temperatur, Wettercode)

Veröffentlicht per MQTT (retained):
  <BASE>/grolo/weather/current   JSON mit Klartext DE/EN
  <BASE>/grolo/weather/forecast  JSON-Liste der Vorhersagestunden
  <BASE>/grolo/weather/state     flache Zahlenfelder für Telegraf -> Measurement "weather"
Schreibt die Vorhersage zusätzlich direkt nach InfluxDB (Measurement "weather_forecast", Zeitstempel = Vorhersagestunde,
neuere Vorhersagen überschreiben ältere).

Sonnenstand und Erwartungsmodell (solar.py):
  <BASE>/grolo/sun        Azimut/Höhe der Sonne, jede Minute, zusätzlich Measurement "sun" in InfluxDB
  <BASE>/grolo/pv_model   erwartete Leistung je konfiguriertem String (STRING<n>_TILT/_AZIMUTH/_WP) für die letzten 24 h
                          und die nächsten 48 h, stündlich aus DNI/DHI/GHI; zusätzlich Measurement "pv_model" (Tag string)

Ausrichtungsschätzung (fit.py): alle FIT_INTERVAL Sekunden (Standard 24 h) und auf Kommando (<BASE>/grolo/fit/run) werden
Neigung/Azimut/Wp je String aus den letzten FIT_DAYS Tagen geschätzt; Ergebnis retained unter <BASE>/grolo/fit und als
Measurement "pv_fit" (Tag string) in InfluxDB.

Standort und Strings kommen aus der Umgebung (WEATHER_LAT/LON, STRING<n>_*) und werden von der retained Nachricht
<BASE>/grolo/config/site (Einstellungsseite: Ortssuche, Neigung/Ausrichtung/Wp je String) überschrieben.

Umgebung: WEATHER_LAT, WEATHER_LON, WEATHER_INTERVAL (s, Standard 600), MQTT_HOST, MQTT_PORT, HA_BASE_TOPIC,
          INFLUX_URL (Standard http://influxdb:8086), INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET,
          STRING1_TILT … STRING4_AZIMUTH/_WP, STRING_PR (Performance-Ratio, Standard 0,85)
"""
import json, logging, os, sys, threading, time, urllib.parse, urllib.request
import paho.mqtt.client as mqtt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from solar import sun_position, poa_irradiance, expected_w, string_config
from fit import run_fit

BASE = os.getenv("HA_BASE_TOPIC", "homeassistant")
HOST = os.getenv("MQTT_HOST", "mosquitto"); PORT = int(os.getenv("MQTT_PORT", "1883"))
LAT = os.getenv("WEATHER_LAT", ""); LON = os.getenv("WEATHER_LON", "")
INTERVAL = int(os.getenv("WEATHER_INTERVAL", "600"))
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086"); INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", "growatt"); INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "nexa")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("weather")

# WMO-Wettercodes (Open-Meteo)
WMO = {
    0: ("Klar", "Clear sky"), 1: ("Überwiegend klar", "Mainly clear"), 2: ("Teilweise bewölkt", "Partly cloudy"), 3: ("Bedeckt", "Overcast"),
    45: ("Nebel", "Fog"), 48: ("Reifnebel", "Rime fog"), 51: ("Leichter Sprühregen", "Light drizzle"), 53: ("Sprühregen", "Drizzle"),
    55: ("Starker Sprühregen", "Dense drizzle"), 56: ("Gefrierender Sprühregen", "Freezing drizzle"), 57: ("Starker gefrierender Sprühregen", "Dense freezing drizzle"),
    61: ("Leichter Regen", "Slight rain"), 63: ("Regen", "Rain"), 65: ("Starker Regen", "Heavy rain"), 66: ("Gefrierender Regen", "Freezing rain"),
    67: ("Starker gefrierender Regen", "Heavy freezing rain"), 71: ("Leichter Schneefall", "Slight snow"), 73: ("Schneefall", "Snow"), 75: ("Starker Schneefall", "Heavy snow"),
    77: ("Schneegriesel", "Snow grains"), 80: ("Leichte Regenschauer", "Slight showers"), 81: ("Regenschauer", "Showers"), 82: ("Heftige Regenschauer", "Violent showers"),
    85: ("Leichte Schneeschauer", "Slight snow showers"), 86: ("Starke Schneeschauer", "Heavy snow showers"), 95: ("Gewitter", "Thunderstorm"),
    96: ("Gewitter mit Hagel", "Thunderstorm with hail"), 99: ("Gewitter mit starkem Hagel", "Thunderstorm with heavy hail"),
}

URL = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
       "&current=temperature_2m,cloud_cover,weather_code,shortwave_radiation,direct_radiation,diffuse_radiation,wind_speed_10m,is_day"
       "&hourly=shortwave_radiation,direct_normal_irradiance,diffuse_radiation,cloud_cover,temperature_2m,weather_code"
       "&daily=sunrise,sunset,sunshine_duration,shortwave_radiation_sum"
       "&past_days=1&forecast_days=3&timezone=auto&timeformat=unixtime")
STRINGS = string_config()
SITE = {"name": os.getenv("WEATHER_NAME", ""), "lat": float(LAT) if LAT else None, "lon": float(LON) if LON else None}
refresh = threading.Event()   # wird gesetzt, wenn die Konfiguration sich ändert -> sofort neu rechnen
fit_request = threading.Event()
FIT_INTERVAL = int(os.getenv("FIT_INTERVAL", "86400")); FIT_DAYS = int(os.getenv("FIT_DAYS", "30"))


def apply_site(cfg):
    """Konfiguration von der Einstellungsseite übernehmen (retained <BASE>/grolo/config/site)."""
    global LAT, LON, STRINGS
    changed = False
    try:
        lat = float(cfg.get("lat")); lon = float(cfg.get("lon"))
        if (LAT, LON) != (str(lat), str(lon)):
            LAT, LON = str(lat), str(lon); changed = True
        SITE.update({"name": cfg.get("name") or "", "lat": lat, "lon": lon})
    except (TypeError, ValueError):
        pass
    strings = {}
    pr = float(cfg.get("pr") or os.getenv("STRING_PR", 0.85) or 0.85)
    for k, v in (cfg.get("strings") or {}).items():
        try:
            if v.get("tilt") is None or v.get("azimuth") is None:
                continue
            strings[int(k)] = {"tilt": float(v["tilt"]), "azimuth": float(v["azimuth"]), "wp": float(v.get("wp") or 0.0), "pr": pr}
        except (TypeError, ValueError):
            continue
    if strings != STRINGS:
        STRINGS = strings; changed = True
    if changed:
        LOG.info("Konfiguration übernommen: %s (%s, %s), Strings %s", SITE["name"] or "-", LAT, LON,
                 ", ".join(f"{i}: {c['tilt']:.0f}°/{c['azimuth']:.0f}°/{c['wp']:.0f} Wp" for i, c in STRINGS.items()) or "keine")
        refresh.set()


def fetch():
    req = urllib.request.Request(URL.format(lat=LAT, lon=LON), headers={"User-Agent": "GroLo weather sidecar"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def hhmm(epoch, offset):
    t = time.gmtime(epoch + offset)
    return "%02d:%02d" % (t.tm_hour, t.tm_min)


def build(d):
    off = int(d.get("utc_offset_seconds", 0))
    cur = d["current"]; daily = d["daily"]; hourly = d["hourly"]
    code = int(cur.get("weather_code", 0)); de, en = WMO.get(code, (f"Code {code}", f"Code {code}"))
    now = int(time.time())
    current = {
        "time": cur["time"], "temperature": cur["temperature_2m"], "cloud_cover": cur["cloud_cover"], "weather_code": code,
        "condition_de": de, "condition_en": en, "shortwave_radiation": cur["shortwave_radiation"], "direct_radiation": cur["direct_radiation"],
        "diffuse_radiation": cur["diffuse_radiation"], "wind_speed": cur["wind_speed_10m"], "is_day": cur["is_day"],
        "sunrise": hhmm(daily["sunrise"][0], off), "sunset": hhmm(daily["sunset"][0], off),
        "sunshine_hours_today": round(daily["sunshine_duration"][0] / 3600.0, 2), "radiation_sum_today": daily["shortwave_radiation_sum"][0],
        "sunrise_tomorrow": hhmm(daily["sunrise"][1], off), "sunset_tomorrow": hhmm(daily["sunset"][1], off),
        "radiation_sum_tomorrow": daily["shortwave_radiation_sum"][1], "sunshine_hours_tomorrow": round(daily["sunshine_duration"][1] / 3600.0, 2),
        "latitude": d.get("latitude"), "longitude": d.get("longitude"), "timezone": d.get("timezone"), "fetched": now,
        "strings": {str(i): c for i, c in STRINGS.items()}, "site": dict(SITE),
    }
    current["sun_azimuth"], current["sun_elevation"] = (round(v, 2) for v in sun_position(now, float(LAT), float(LON)))
    forecast = []
    for i, ts in enumerate(hourly["time"]):
        if ts < now - 3600 or ts > now + 48 * 3600:
            continue
        forecast.append({"time": ts, "shortwave_radiation": hourly["shortwave_radiation"][i], "cloud_cover": hourly["cloud_cover"][i],
                         "temperature": hourly["temperature_2m"][i], "weather_code": hourly["weather_code"][i]})
    state = {k: current[k] for k in ("temperature", "cloud_cover", "weather_code", "shortwave_radiation", "direct_radiation", "diffuse_radiation",
                                     "wind_speed", "is_day", "sunshine_hours_today", "radiation_sum_today", "radiation_sum_tomorrow", "sunrise", "sunset")}
    return current, forecast, state, model_rows(hourly, now)


def model_rows(hourly, now):
    """Erwartete Leistung je konfiguriertem String, stündlich von −24 h bis +48 h.

    Open-Meteo-Stundenwerte der Strahlung sind Mittel der vorangehenden Stunde, deshalb Sonnenstand zur Stundenmitte
    und Zeitstempel der Zeile ebenfalls Stundenmitte."""
    rows = []
    if not STRINGS:
        return rows
    lat, lon = float(LAT), float(LON)
    for i, ts in enumerate(hourly["time"]):
        if ts < now - 24 * 3600 or ts > now + 48 * 3600:
            continue
        ghi = hourly["shortwave_radiation"][i]; dni = hourly["direct_normal_irradiance"][i]; dhi = hourly["diffuse_radiation"][i]
        if ghi is None or dni is None or dhi is None:
            continue
        mid = ts - 1800
        az, el = sun_position(mid, lat, lon)
        for n, c in STRINGS.items():
            gti = poa_irradiance(ghi, dni, dhi, az, el, c["tilt"], c["azimuth"])
            rows.append({"time": mid, "string": n, "gti": round(gti, 1), "expected_w": round(expected_w(gti, c["wp"], c["pr"]), 1) if c["wp"] else None})
    return rows


def influx_write(lines):
    if not INFLUX_TOKEN or not lines:
        return
    q = urllib.parse.urlencode({"org": INFLUX_ORG, "bucket": INFLUX_BUCKET, "precision": "s"})
    req = urllib.request.Request(f"{INFLUX_URL}/api/v2/write?{q}", data="\n".join(lines).encode(), method="POST",
                                 headers={"Authorization": f"Token {INFLUX_TOKEN}", "Content-Type": "text/plain; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def write_model(rows):
    lines = []
    for r in rows:
        vals = f"gti={float(r['gti'])}" + (f",expected_w={float(r['expected_w'])}" if r["expected_w"] is not None else "")
        lines.append(f"pv_model,string={int(r['string'])} {vals} {int(r['time'])}")
    influx_write(lines)
    if lines:
        LOG.info("Modell: %d Stundenwerte für %d Strings nach InfluxDB geschrieben", len(lines), len(STRINGS))


def fit_loop(client):
    """Ausrichtung schätzen: kurz nach dem Start, dann alle FIT_INTERVAL s oder auf Kommando."""
    fit_request.wait(120)
    while True:
        fit_request.clear()
        if LAT and LON and INFLUX_TOKEN:
            try:
                pr = float(os.getenv("STRING_PR", 0.85) or 0.85)
                res = run_fit(INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET, float(LAT), float(LON), FIT_DAYS, 150.0, pr)
                client.publish(f"{BASE}/grolo/fit", json.dumps(res), retain=True)
                lines = []
                for k, v in res["strings"].items():
                    if v.get("status") in ("ok", "uncertain"):
                        lines.append(f'pv_fit,string={k} azimuth={float(v["azimuth"])},tilt={float(v["tilt"])},wp={float(v["wp"])},r2={float(v["r2"])},hours={int(v["hours"])}i,quality="{v["status"]}" {res["updated"]}')
                    else:
                        lines.append(f'pv_fit,string={k} hours={int(v.get("hours", 0))}i,quality="{v["status"]}" {res["updated"]}')
                influx_write(lines)
                LOG.info("Ausrichtung geschätzt: %s", "; ".join(f"{k}: {v['status']}" + (f" {v['azimuth']:.0f}°/{v['tilt']:.0f}°/{v['wp']:.0f} Wp R²={v['r2']:.2f}" if v.get("status") in ("ok", "uncertain") else "") for k, v in res["strings"].items()))
            except Exception as e:
                LOG.warning("Ausrichtungsschätzung fehlgeschlagen: %s", e)
                client.publish(f"{BASE}/grolo/fit/status", json.dumps({"error": str(e), "time": int(time.time())}), retain=False)
        fit_request.wait(FIT_INTERVAL)


def sun_loop(client):
    """Jede Minute: Sonnenstand per MQTT (retained) und nach InfluxDB."""
    while True:
        now = int(time.time())
        try:
            az, el = sun_position(now, float(LAT), float(LON))
            client.publish(f"{BASE}/grolo/sun", json.dumps({"time": now, "azimuth": round(az, 2), "elevation": round(el, 2)}), retain=True)
            influx_write([f"sun azimuth={az:.3f},elevation={el:.3f} {now}"])
        except Exception as e:
            LOG.debug("Sonnenstand: %s", e)
        time.sleep(60 - (time.time() % 60))


def write_forecast(forecast):
    if not INFLUX_TOKEN:
        return
    lines = []
    for f in forecast:
        vals = ",".join(f"{k}={float(f[k])}" for k in ("shortwave_radiation", "cloud_cover", "temperature") if f.get(k) is not None)
        lines.append(f"weather_forecast {vals},weather_code={int(f['weather_code'])}i {int(f['time'])}")
    influx_write(lines)
    LOG.info("Vorhersage: %d Stunden nach InfluxDB geschrieben", len(lines))


def main():
    client = mqtt.Client(client_id="grolo-weather", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = lambda c, u, f, rc, p=None: c.subscribe([(f"{BASE}/grolo/config/site", 0), (f"{BASE}/grolo/fit/run", 0)])
    def on_message(c, u, msg):
        try:
            if msg.topic.endswith("/fit/run"):
                LOG.info("Ausrichtungsschätzung angefordert"); fit_request.set(); return
            apply_site(json.loads(msg.payload))
        except Exception as e:
            LOG.warning("Konfiguration unlesbar: %s", e)
    client.on_message = on_message
    while True:
        try:
            client.connect(HOST, PORT, 60); break
        except Exception as e:
            LOG.warning("MQTT nicht erreichbar (%s)", e); time.sleep(5)
    client.loop_start()
    LOG.info("Standort %s, %s; Intervall %ss; Strings mit Ausrichtung: %s", LAT, LON, INTERVAL,
             ", ".join(f"{i}: {c['tilt']:.0f}°/{c['azimuth']:.0f}°/{c['wp']:.0f} Wp" for i, c in STRINGS.items()) or "keine (STRING<n>_TILT/_AZIMUTH setzen)")
    threading.Thread(target=sun_loop, args=(client,), daemon=True).start()
    threading.Thread(target=fit_loop, args=(client,), daemon=True).start()
    while True:
        if not LAT or not LON:
            LOG.warning("Kein Standort: WEATHER_LAT/WEATHER_LON setzen oder Ort auf der Einstellungsseite wählen"); refresh.wait(60); refresh.clear(); continue
        try:
            current, forecast, state, model = build(fetch())
            client.publish(f"{BASE}/grolo/weather/current", json.dumps(current, ensure_ascii=False), retain=True)
            client.publish(f"{BASE}/grolo/weather/forecast", json.dumps(forecast), retain=True)
            client.publish(f"{BASE}/grolo/weather/state", json.dumps(state), retain=True)
            client.publish(f"{BASE}/grolo/pv_model", json.dumps({"strings": current["strings"], "hours": model}), retain=True)
            LOG.info("%s, %.1f °C, Bewölkung %s %%, Strahlung %s W/m², Sonne %s-%s", current["condition_de"], current["temperature"],
                     current["cloud_cover"], current["shortwave_radiation"], current["sunrise"], current["sunset"])
            try:
                write_forecast(forecast); write_model(model)
            except Exception as e:
                LOG.warning("InfluxDB-Schreiben fehlgeschlagen: %s", e)
        except Exception as e:
            LOG.warning("Abruf fehlgeschlagen: %s", e)
        refresh.wait(INTERVAL); refresh.clear()


main()
