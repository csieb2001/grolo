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

Umgebung: WEATHER_LAT, WEATHER_LON, WEATHER_INTERVAL (s, Standard 600), MQTT_HOST, MQTT_PORT, HA_BASE_TOPIC,
          INFLUX_URL (Standard http://influxdb:8086), INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET
"""
import json, logging, os, time, urllib.parse, urllib.request
import paho.mqtt.client as mqtt

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
       "&hourly=shortwave_radiation,cloud_cover,temperature_2m,weather_code"
       "&daily=sunrise,sunset,sunshine_duration,shortwave_radiation_sum"
       "&forecast_days=3&timezone=auto&timeformat=unixtime")


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
    }
    forecast = []
    for i, ts in enumerate(hourly["time"]):
        if ts < now - 3600 or ts > now + 48 * 3600:
            continue
        forecast.append({"time": ts, "shortwave_radiation": hourly["shortwave_radiation"][i], "cloud_cover": hourly["cloud_cover"][i],
                         "temperature": hourly["temperature_2m"][i], "weather_code": hourly["weather_code"][i]})
    state = {k: current[k] for k in ("temperature", "cloud_cover", "weather_code", "shortwave_radiation", "direct_radiation", "diffuse_radiation",
                                     "wind_speed", "is_day", "sunshine_hours_today", "radiation_sum_today", "radiation_sum_tomorrow", "sunrise", "sunset")}
    return current, forecast, state


def write_forecast(forecast):
    if not INFLUX_TOKEN:
        return
    lines = []
    for f in forecast:
        vals = ",".join(f"{k}={float(f[k])}" for k in ("shortwave_radiation", "cloud_cover", "temperature") if f.get(k) is not None)
        lines.append(f"weather_forecast {vals},weather_code={int(f['weather_code'])}i {int(f['time'])}")
    q = urllib.parse.urlencode({"org": INFLUX_ORG, "bucket": INFLUX_BUCKET, "precision": "s"})
    req = urllib.request.Request(f"{INFLUX_URL}/api/v2/write?{q}", data="\n".join(lines).encode(), method="POST",
                                 headers={"Authorization": f"Token {INFLUX_TOKEN}", "Content-Type": "text/plain; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()
    LOG.info("Vorhersage: %d Stunden nach InfluxDB geschrieben", len(lines))


def main():
    if not LAT or not LON:
        LOG.error("WEATHER_LAT/WEATHER_LON fehlen"); time.sleep(3600); return
    client = mqtt.Client(client_id="grolo-weather", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    while True:
        try:
            client.connect(HOST, PORT, 60); break
        except Exception as e:
            LOG.warning("MQTT nicht erreichbar (%s)", e); time.sleep(5)
    client.loop_start()
    LOG.info("Standort %s, %s; Intervall %ss", LAT, LON, INTERVAL)
    while True:
        try:
            current, forecast, state = build(fetch())
            client.publish(f"{BASE}/grolo/weather/current", json.dumps(current, ensure_ascii=False), retain=True)
            client.publish(f"{BASE}/grolo/weather/forecast", json.dumps(forecast), retain=True)
            client.publish(f"{BASE}/grolo/weather/state", json.dumps(state), retain=True)
            LOG.info("%s, %.1f °C, Bewölkung %s %%, Strahlung %s W/m², Sonne %s-%s", current["condition_de"], current["temperature"],
                     current["cloud_cover"], current["shortwave_radiation"], current["sunrise"], current["sunset"])
            try:
                write_forecast(forecast)
            except Exception as e:
                LOG.warning("InfluxDB-Schreiben fehlgeschlagen: %s", e)
        except Exception as e:
            LOG.warning("Abruf fehlgeschlagen: %s", e)
        time.sleep(INTERVAL)


main()
