#!/usr/bin/env python3
"""forecast: Jahresprognose für Stromverbrauch, Stromrechnung und Abschlag.

Rechnet ein volles Jahr Stunde für Stunde durch, mit dem echten Wetter des Standorts aus dem Open-Meteo-Archiv
(ERA5, ein Jahr Stundenwerte für Einstrahlung und Außentemperatur). Je Stunde:

  PV          Einstrahlung auf jede Modulfläche (solar.py) × Wp × Performance-Ratio
  Wärmepumpe  Wärmebedarf ∝ Heizgradstunden (Heizgrenze 15 °C) plus Warmwasser als Grundlast,
              geteilt durch den COP der Stunde (Carnot mit der Vorlauftemperatur aus der Heizkurve der Wolf)
  Haushalt    gemessener Tagesverbrauch (Shelly ohne Wärmepumpe) auf ein gemessenes Stundenprofil verteilt
  NEXA        Direktverbrauch und Batterie, begrenzt auf die Abgabegrenze (800 W) und die Kapazität der Packs

Daraus Netzbezug je Stunde, aufsummiert zu Monaten und zum Jahr, und mit dem Tarif zu Geld:

  Jahresrechnung = Netzbezug × Arbeitspreis + Grundpreis × 12 − Einspeisung × Einspeisevergütung
  Abschlag       = Jahresrechnung / 12, auf die nächsten 5 € aufgerundet

Was gemessen werden kann, wird gemessen; was nicht, kommt aus der Konfiguration, und was auch dort fehlt, aus
einem benannten Standardwert. Jede Prognose trägt ihre Annahmen als Liste mit, damit sichtbar bleibt, worauf
die Zahl beruht – eine Jahresrechnung aus zwei Wochen Messdaten ist eine Rechnung mit Annahmen, keine Messung.

Veröffentlicht (retained):
  <BASE>/grolo/forecast        das volle Ergebnis für die Website und die Einstellungsseite
  <BASE>/grolo/forecast/state  flache Zahlenfelder für Telegraf -> Measurement "forecast"
zusätzlich je Monat eine Zeile "forecast_month" (Tag month) direkt nach InfluxDB.

Eingaben von der Einstellungsseite:
  <BASE>/grolo/config/tariff   price_ct_kwh, feedin_ct_kwh, base_eur_month, abschlag_eur_month
  <BASE>/grolo/config/house    gas_kwh_year, boiler_eff, dhw_share, area_m2, battery_kwh, power_kwh_year
  <BASE>/grolo/config/site     Standort und Module je String

Umgebung: MQTT_HOST/PORT, HA_BASE_TOPIC, INFLUX_URL/TOKEN/ORG/BUCKET, FORECAST_INTERVAL (s, Standard 21600), TZ
"""
import calendar, datetime, json, logging, math, os, sys, threading, time, urllib.parse, urllib.request
import paho.mqtt.client as mqtt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from solar import poa_irradiance, sun_position  # noqa: E402

BASE = os.getenv("HA_BASE_TOPIC", "homeassistant")
HOST = os.getenv("MQTT_HOST", "mosquitto"); PORT = int(os.getenv("MQTT_PORT", "1883"))
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", ""); INFLUX_ORG = os.getenv("INFLUX_ORG", "growatt")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "nexa")
INTERVAL = int(os.getenv("FORECAST_INTERVAL", "21600"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("forecast")

# ---------------------------------------------------------------------------- Standardwerte
# Alle benannt und begründet: wer sie ändert, soll wissen, was er ändert.
D = {
    "heat_limit_c": 15.0,      # Heizgrenze: darüber heizt das Haus nicht mehr (übliche Annahme für Bestandsbauten)
    "room_c": 20.0,            # Raumtemperatur, Bezugspunkt der Heizkurve
    "boiler_eff": 0.85,        # Nutzungsgrad der alten Gasheizung: aus kWh Gas werden 85 % Wärme
    "dhw_share": 0.15,         # Anteil Warmwasser am Wärmebedarf, wenn nichts Besseres bekannt ist
    "dhw_flow_c": 50.0,        # Vorlauf bei Warmwasserbereitung
    "eta_carnot": 0.42,        # Gütegrad gegenüber Carnot; typisch für eine moderne Luft-Wasser-Wärmepumpe
    "defrost_penalty": 0.9,    # Abschlag auf den COP unter 5 °C für Abtauverluste
    "pack_kwh": 2.048,         # Typenschild eines NEXA-Akkumoduls: 2048 Wh
    "soc_min": 0.10,           # Entladeschluss, wie ihn die Shelly-Regelung fährt
    "eta_charge": 0.96, "eta_discharge": 0.96,   # Wirkungsgrad je Richtung, zusammen rund 92 % Umlauf
    "max_out_w": 800.0,        # Abgabegrenze des NEXA ins Haus
    "max_charge_w": 1600.0,    # Ladeleistung der Packs
    "pr": 0.85,                # Performance-Ratio der Module
    "power_class_kw": 10.0,    # Leistungsklasse der Wärmepumpe; darüber springt der Heizstab ein
    "base_eur_month": 12.0,    # Grundpreis, falls nicht eingetragen
    "price_ct_kwh": 30.0,
    "household_kwh_day": 4.5,  # Haushalt ohne Wärmepumpe, falls noch nichts gemessen wurde
    "area_kwh_m2": 110.0,      # spezifischer Wärmebedarf je m² und Jahr, wenn nur die Wohnfläche bekannt ist
    "flow_norm_c": 45.0, "flow_base_c": 25.0, "t_norm_c": -10.0,   # Heizkurve, falls die Wolf keine liefert
    "winter_extra": 0.12,      # Haushalt im Winter über, im Sommer unter dem Mittel (Licht, Länge der Abende)
}
MIN_HOUSE_DAYS = 14        # so viele volle Messtage, bevor der gemessene Haushalt den Standardwert ablöst
PV_CAL_DAYS = 30           # Zeitraum, über den die PV gegen die Messung geeicht wird
MIN_HEAT_DAYS = 20         # Heiztage, bevor die gemessene Gebäudekennlinie den Gasverbrauch ablöst

# Stundenprofil des Haushalts, falls noch keines gemessen ist: Nachtgrundlast, Morgen- und Abendspitze.
PROFILE = [0.025, 0.021, 0.019, 0.018, 0.018, 0.022, 0.033, 0.044, 0.046, 0.043, 0.041, 0.043,
           0.047, 0.044, 0.040, 0.040, 0.046, 0.058, 0.068, 0.068, 0.061, 0.052, 0.042, 0.031]

_ARCHIVE = {}    # (lat, lon, ende) -> Jahresstunden


# ---------------------------------------------------------------------------- Wetterjahr
def archive_year(lat, lon):
    """Ein volles Jahr Stundenwerte bis zum letzten Monatsende: Einstrahlung und Außentemperatur.

    Alle Stunden, auch die Nachtstunden – die Wärmepumpe heizt nachts weiter, und genau dann fehlt die Sonne.
    """
    end = datetime.date.today().replace(day=1) - datetime.timedelta(days=1)
    start = datetime.date(end.year - 1 if end.month != 12 else end.year, end.month % 12 + 1, 1)
    key = (round(lat, 2), round(lon, 2), end.isoformat())
    if key in _ARCHIVE:
        return _ARCHIVE[key], start, end
    url = (f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
           f"&start_date={start}&end_date={end}"
           "&hourly=shortwave_radiation,direct_normal_irradiance,diffuse_radiation,temperature_2m"
           "&timezone=UTC&timeformat=unixtime")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "GroLo forecast"}), timeout=90) as r:
        d = json.load(r)["hourly"]
    hours = []
    for i, ts in enumerate(d["time"]):
        t_air = d["temperature_2m"][i]
        if t_air is None:
            continue
        ghi = d["shortwave_radiation"][i] or 0.0
        dni = d["direct_normal_irradiance"][i] or 0.0
        dhi = d["diffuse_radiation"][i] or 0.0
        az, el = sun_position(int(ts) - 1800, lat, lon) if ghi >= 5 else (0.0, -90.0)
        hours.append((int(ts), ghi, dni, dhi, t_air, az, el))
    _ARCHIVE.clear(); _ARCHIVE[key] = hours
    LOG.info("Wetterjahr %s bis %s geladen: %d Stunden", start, end, len(hours))
    return hours, start, end


# ---------------------------------------------------------------------------- Bausteine des Modells
def flow_temp(t_out, curve):
    """Vorlauftemperatur bei dieser Außentemperatur, aus der Heizkurve: Gerade zwischen dem Fußpunkt bei
    Raumtemperatur und dem Auslegungspunkt bei Normaußentemperatur."""
    t_norm, f_norm, f_base = curve["t_norm_c"], curve["flow_norm_c"], curve["flow_base_c"]
    room = curve.get("room_c") or D["room_c"]
    if t_out >= room:
        return f_base
    k = (f_norm - f_base) / max(room - t_norm, 1.0)
    return min(f_norm, f_base + k * (room - t_out))


def cop(t_out, t_flow, eta):
    """COP nach Carnot mit Gütegrad, mit Abschlag fürs Abtauen im Nassbereich um den Gefrierpunkt."""
    dt = max(t_flow - t_out, 8.0)
    value = eta * (t_flow + 273.15) / dt
    if -7.0 <= t_out <= 5.0:
        value *= D["defrost_penalty"]
    return max(1.0, min(value, 6.5))


def fit_eta(points, curve):
    """Gütegrad aus gemessenen Takten schätzen: welcher Wert bringt den Carnot-COP im Mittel auf die Messung."""
    ratios = []
    for t_out, measured in points:
        t_flow = flow_temp(t_out, curve)
        base = cop(t_out, t_flow, 1.0)
        if base > 0.5 and 1.0 < measured < 8.0:
            ratios.append(measured / base)
    if len(ratios) < 20:
        return None
    ratios.sort()
    return round(ratios[len(ratios) // 2], 3)


def season(month):
    """Saisonfaktor des Haushalts: Sinus mit Maximum im Januar, über das Jahr gemittelt genau 1.

    Licht, lange Abende, Wäschetrockner statt Leine – im Winter liegt der Haushalt über dem Jahresmittel,
    im Sommer darunter. Der Faktor wird in beide Richtungen gebraucht: um eine Messung aus einem einzelnen
    Monat auf das Jahresmittel zurückzurechnen, und um dieses Mittel wieder auf die Monate zu verteilen.
    """
    return 1.0 + D["winter_extra"] * math.cos((month - 1) / 12.0 * 2 * math.pi)


def pv_hour(ghi, dni, dhi, az, el, strings, pr):
    """Erzeugung aller Strings in dieser Stunde, in kWh."""
    if el <= 0 or ghi < 5:
        return 0.0
    w = 0.0
    for c in strings.values():
        if not c.get("wp"):
            continue
        w += poa_irradiance(ghi, dni, dhi, az, el, c["tilt"], c["azimuth"]) / 1000.0 * c["wp"] * c.get("pr", pr)
    return w / 1000.0


def measured_pv_hours(days):
    """Stundenmittel der gemessenen PV-Leistung (Summe aller Strings) aus InfluxDB: {unix_stundenende: W}."""
    fields = " or ".join(f'r._field == "pv{i}{s}"' for i in range(1, 5) for s in ("Voltage", "Current"))
    terms = " + ".join(f'(if exists r.pv{i}Voltage and exists r.pv{i}Current then r.pv{i}Voltage * r.pv{i}Current else 0.0)'
                       for i in range(1, 5))
    rows = influx_query(f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{int(days)}d)
  |> filter(fn: (r) => r._measurement == "nexa" and ({fields}))
  |> aggregateWindow(every: 5m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> map(fn: (r) => ({{ _time: r._time, _value: {terms} }}))
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])''')
    out = {}
    for r in rows:
        if not r.get("_time") or not r.get("_value"):
            continue
        ts = int(calendar.timegm(time.strptime(r["_time"][:19], "%Y-%m-%dT%H:%M:%S")))
        out[ts] = float(r["_value"])
    return out


def weather_hours(lat, lon, days):
    """{unix_stundenende: (GHI, DNI, DHI)} der letzten Tage, echtes Wetter statt Klimamittel."""
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           "&hourly=shortwave_radiation,direct_normal_irradiance,diffuse_radiation"
           f"&past_days={min(int(days), 92)}&forecast_days=1&timezone=UTC&timeformat=unixtime")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "GroLo forecast"}), timeout=30) as r:
        d = json.load(r)["hourly"]
    return {int(t): (d["shortwave_radiation"][i], d["direct_normal_irradiance"][i], d["diffuse_radiation"][i])
            for i, t in enumerate(d["time"])
            if d["shortwave_radiation"][i] is not None and d["direct_normal_irradiance"][i] is not None}


def calibrate_pv(lat, lon, strings, pr, days=PV_CAL_DAYS):
    """Wie viel die Module wirklich liefern, verglichen mit dem, was das Modell für dieselben Stunden sagt.

    Das Modell kennt Neigung, Azimut und Wp - aber nicht den Baum vor dem Balkon, den Dachüberstand, den
    Schmutz auf dem Glas und auch nicht, ob die geschätzte Ausrichtung überhaupt stimmt. Der Vergleich mit
    dem echten Wetter derselben Stunden fängt das alles in einer Zahl: Messung geteilt durch Modell.
    """
    if not strings:
        return None
    power = measured_pv_hours(days)
    if len(power) < 48:
        return None
    wx = weather_hours(lat, lon, days)
    meas = model = 0.0
    n = 0
    for ts, w in power.items():
        s = wx.get(ts)
        if not s or s[0] < 30:
            continue
        az, el = sun_position(ts - 1800, lat, lon)
        if el < 5:
            continue
        expected = pv_hour(s[0], s[1], s[2], az, el, strings, pr) * 1000.0     # W
        if expected < 20:
            continue
        meas += w; model += expected; n += 1
    if n < 24 or model <= 0:
        return None
    return {"factor": round(min(max(meas / model, 0.3), 1.6), 3), "hours": n,
            "measured_kwh": round(meas / 1000.0, 1), "modelled_kwh": round(model / 1000.0, 1)}


def heat_line(days=120):
    """Gebäudekennlinie aus Messwerten: erzeugte Wärme je Tag gegen die mittlere Außentemperatur.

    Ein Haus verhält sich in guter Näherung linear: je Kelvin, das es draußen kälter ist als die
    Heizgrenze, braucht es eine feste Menge Wärme mehr. Die Steigung dieser Geraden ist die Heizlast des
    Gebäudes in kWh je Kelvin und Tag, ihr Achsenabschnitt das Warmwasser, das vom Wetter unabhängig ist.
    Beides zusammen ist eine Messung des Hauses – besser als jede Hochrechnung aus dem Gasverbrauch, sobald
    genug kalte Tage im Kasten sind.
    """
    rows = influx_query(f'''import "timezone"
option location = timezone.location(name: "Europe/Berlin")
warm = from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{int(days)}d)
  |> filter(fn: (r) => r._measurement == "wolf" and r.device == "heatpump" and r._field == "erzeugte_waermemenge_aktueller_tag")
  |> aggregateWindow(every: 1d, fn: max, createEmpty: false, timeSrc: "_start")
  |> keep(columns: ["_time", "_value"])
  |> set(key: "k", value: "kwh")
temp = from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{int(days)}d)
  |> filter(fn: (r) => r._measurement == "wolf" and r.device == "heatpump" and r._field == "aussentemperatur")
  |> aggregateWindow(every: 1d, fn: mean, createEmpty: false, timeSrc: "_start")
  |> keep(columns: ["_time", "_value"])
  |> set(key: "k", value: "t")
union(tables: [warm, temp])
  |> pivot(rowKey: ["_time"], columnKey: ["k"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.kwh and exists r.t)''')
    points = []
    for r in rows:
        try:
            kwh, t = float(r["kwh"]), float(r["t"])
        except (TypeError, ValueError, KeyError):
            continue
        if kwh > 0:
            points.append((t, kwh))
    heating = [p for p in points if p[0] < D["heat_limit_c"]]
    if len(heating) < MIN_HEAT_DAYS or max(p[0] for p in heating) - min(p[0] for p in heating) < 6.0:
        return {"days": len(heating), "enough": False}
    n = len(heating)
    mx = sum(p[0] for p in heating) / n
    my = sum(p[1] for p in heating) / n
    sxx = sum((p[0] - mx) ** 2 for p in heating)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in heating)
    if sxx <= 0:
        return {"days": n, "enough": False}
    slope = sxy / sxx                       # kWh je Kelvin und Tag, negativ
    if slope >= -0.05:
        return {"days": n, "enough": False}
    intercept = my - slope * mx
    limit = -intercept / slope              # Außentemperatur, bei der der Heizbedarf null waere
    sst = sum((p[1] - my) ** 2 for p in heating) or 1.0
    ssr = sum((p[1] - (slope * p[0] + intercept)) ** 2 for p in heating)
    return {"days": n, "enough": True, "per_kelvin_day": round(-slope, 3), "limit_c": round(limit, 1),
            "r2": round(max(0.0, 1.0 - ssr / sst), 3)}


def battery_wh_per_pct(days=90):
    """Wie viel Energie ein Prozentpunkt Ladezustand kostet, gemessen über alle Ladephasen.

    Hineingeflossene Wattstunden geteilt durch den Hub in Prozentpunkten. Die Ladeverluste stecken darin,
    und genau das braucht eine Restzeit: nicht was in der Zelle ankommt, sondern was oben hineingesteckt
    werden muss. Gezählt wird über den ganzen Ladezeitraum, nicht nur in den Messpunkten, in denen der
    Ladezustand gerade um ein Prozent springt - er wird nur in ganzen Prozent gemeldet, und zwischen zwei
    Sprüngen fließt der Großteil der Energie. Ab 99 % zählt nichts mehr, dort fließt Energie, ohne
    dass der Ladezustand noch steigt.
    """
    rows = influx_query(f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{int(days)}d)
  |> filter(fn: (r) => r._measurement == "nexa" and (r._field == "totalBatteryPackSoc" or r._field =~ /^pv[1-4](Voltage|Current)$/ or r._field == "onGridPower"))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.totalBatteryPackSoc and exists r.onGridPower)
  |> map(fn: (r) => ({{ _time: r._time, soc: r.totalBatteryPackSoc,
        bat: (if exists r.pv1Voltage then r.pv1Voltage * r.pv1Current else 0.0)
           + (if exists r.pv2Voltage then r.pv2Voltage * r.pv2Current else 0.0)
           + (if exists r.pv3Voltage then r.pv3Voltage * r.pv3Current else 0.0)
           + (if exists r.pv4Voltage then r.pv4Voltage * r.pv4Current else 0.0)
           - (r.onGridPower - 30000.0) / 10.0 }}))
  |> keep(columns: ["_time", "soc", "bat"])''')
    points = []
    for r in rows:
        try:
            points.append((r["_time"], float(r["soc"]), float(r["bat"])))
        except (TypeError, ValueError, KeyError):
            continue
    points.sort(key=lambda p: p[0])
    wh = pct = 0.0
    prev = None
    for t, soc, bat in points:
        if prev is not None and bat > 5 and soc < 99:
            wh += bat / 60.0                      # ein Minutenmittel
            if soc > prev:
                pct += soc - prev
        prev = soc
    if pct < 40 or wh <= 0:
        return {"pct_observed": round(pct, 1), "enough": False}
    return {"wh_per_pct": round(wh / pct, 1), "kwh": round(wh / pct / 10.0, 2),
            "pct_observed": round(pct), "enough": True}


# ---------------------------------------------------------------------------- Das Modell
def build_forecast(lat, lon, strings, cfg, measured):
    """Ein Jahr Stunde für Stunde. Rückgabe siehe Modulkopf."""
    hours, start, end = archive_year(lat, lon)
    notes = []

    def note(key, de, en, source, value=None):
        notes.append({"key": key, "de": de, "en": en, "source": source, "value": value})

    # ---------------------------------------------------------------- Wärmebedarf
    curve = {k: cfg.get(k) if cfg.get(k) is not None else D[k] for k in ("t_norm_c", "flow_norm_c", "flow_base_c")}
    curve["room_c"] = cfg.get("room_c") or D["room_c"]
    gas = cfg.get("gas_kwh_year")
    eff = cfg.get("boiler_eff") or D["boiler_eff"]
    area = cfg.get("area_m2")
    line = measured.get("heat_line") or {}
    # Die Heizgrenze von 15 °C gilt für ein Haus, das auf 20 °C gefahren wird: darüber tragen innere Lasten
    # und Sonne den Rest. Wer wärmer wohnt, heizt entsprechend länger ins Jahr hinein – die Grenze wandert
    # um genau den Unterschied mit.
    room_c = cfg.get("room_c")
    limit = D["heat_limit_c"] + ((room_c - D["room_c"]) if room_c else 0.0)
    heat_year = None
    if line.get("enough"):
        # Die gemessene Gerade des Hauses schlägt jeden Anker: sie enthält Dämmung, Lüftung, Wunschtemperatur
        # und Nutzerverhalten schon, ohne sie einzeln kennen zu müssen – auch die eigene Heizgrenze.
        limit = line["limit_c"]
        heating_measured = sum(max(0.0, limit - t) for _, _, _, _, t, _, _ in hours) / 24.0 * line["per_kelvin_day"]
        note("heat", f"Wärmebedarf {heating_measured:.0f} kWh Heizung im Jahr, aus der gemessenen Kennlinie des "
                     f"Hauses: {line['per_kelvin_day']:.2f} kWh je Kelvin und Tag, Heizgrenze {limit:.1f} °C, "
                     f"aus {line['days']} Heiztagen (Bestimmtheitsmaß {line['r2']:.2f})",
             f"Heat demand {heating_measured:.0f} kWh of space heating a year, from the measured line of the "
             f"building: {line['per_kelvin_day']:.2f} kWh per kelvin and day, heating limit {limit:.1f} °C, "
             f"from {line['days']} heating days (R² {line['r2']:.2f})", "measured", round(heating_measured))
    elif gas:
        heat_year = gas * eff
        seen = line.get("days", 0)
        extra_de = f" Gemessen sind bisher {seen} Heiztage, ab {MIN_HEAT_DAYS} misst das Modell das Haus selbst." if seen else ""
        extra_en = f" {seen} heating days measured so far, from {MIN_HEAT_DAYS} on the model measures the building itself." if seen else ""
        note("heat", f"Wärmebedarf {heat_year:.0f} kWh im Jahr, aus {gas:.0f} kWh Gas × {eff:.0%} Nutzungsgrad.{extra_de}",
             f"Heat demand {heat_year:.0f} kWh a year, from {gas:.0f} kWh of gas × {eff:.0%} efficiency.{extra_en}",
             "config", round(heat_year))
    elif area:
        heat_year = area * D["area_kwh_m2"]
        note("heat", f"Wärmebedarf {heat_year:.0f} kWh im Jahr, aus {area:.0f} m² × {D['area_kwh_m2']:.0f} kWh/m². "
                     "Der Gasverbrauch der Vorjahre wäre der genauere Anker.",
             f"Heat demand {heat_year:.0f} kWh a year, from {area:.0f} m² × {D['area_kwh_m2']:.0f} kWh/m². "
             "Previous years' gas consumption would be the better anchor.", "default", round(heat_year))
    else:
        note("heat", "Kein Anker für den Wärmebedarf eingetragen – ohne Gasverbrauch oder Wohnfläche bleibt die "
                     "Wärmepumpe in der Prognose leer.",
             "No anchor for the heat demand – without gas consumption or floor area the heat pump stays out of "
             "the forecast.", "missing")
    if room_c:
        note("room", f"Räume werden im Mittel auf {room_c:.1f} °C gefahren ({cfg.get('room_rooms')} Räume, "
                     f"{cfg.get('room_min_c'):.1f} bis {cfg.get('room_max_c'):.1f} °C), gemessen über tado. "
                     f"Die Heizgrenze verschiebt sich damit von {D['heat_limit_c']:.0f} auf {limit:.1f} °C und "
                     "die Heizkurve rechnet ab diesem Fußpunkt.",
             f"Rooms are kept at {room_c:.1f} °C on average ({cfg.get('room_rooms')} rooms, "
             f"{cfg.get('room_min_c'):.1f} to {cfg.get('room_max_c'):.1f} °C), measured through tado. The heating "
             f"limit moves from {D['heat_limit_c']:.0f} to {limit:.1f} °C and the heating curve starts from "
             "that base.", "measured", room_c)
    else:
        note("room", f"Raumtemperatur mit {D['room_c']:.0f} °C angenommen – ohne gemessene Räume ist das der "
                     "übliche Bezugspunkt für Heizgrenze und Heizkurve.",
             f"Room temperature assumed at {D['room_c']:.0f} °C – without measured rooms that is the usual "
             "reference for heating limit and heating curve.", "default", D["room_c"])
    dhw_share = cfg.get("dhw_share") if cfg.get("dhw_share") is not None else D["dhw_share"]
    eta = measured.get("eta_carnot") or cfg.get("eta_carnot") or D["eta_carnot"]
    if measured.get("eta_carnot"):
        note("cop", f"COP-Kennlinie aus {measured.get('cop_points', 0)} gemessenen Takten (Gütegrad {eta:.2f})",
             f"COP curve from {measured.get('cop_points', 0)} measured cycles (quality factor {eta:.2f})", "measured", eta)
    else:
        note("cop", f"COP nach Carnot mit Gütegrad {eta:.2f}, bis genug eigene Takte gemessen sind",
             f"COP from Carnot with quality factor {eta:.2f} until enough of your own cycles are measured", "default", eta)

    # ---------------------------------------------------------------- Haushalt
    # Rangfolge: die eigene Jahresabrechnung schlägt jede Hochrechnung aus wenigen Tagen; erst ab zwei Wochen
    # Messung ist der Median belastbarer als gar nichts, und darunter bleibt der Standardwert stehen.
    power_year = cfg.get("power_kwh_year")
    m_day, m_days = measured.get("household_kwh_day"), measured.get("household_days", 0)
    if power_year:
        house_day = power_year / 365.0
        note("house", f"Haushalt {power_year:.0f} kWh im Jahr laut Abrechnung, das sind {house_day:.1f} kWh am Tag",
             f"Household {power_year:.0f} kWh a year from your bill, that is {house_day:.1f} kWh a day",
             "config", round(power_year))
    elif m_day and m_days >= MIN_HOUSE_DAYS:
        house_day = m_day
        note("house", f"Haushalt {house_day:.1f} kWh am Tag im Jahresmittel, Median aus {m_days} vollen "
                      "Messtagen (Netzbezug plus NEXA-Abgabe, abzüglich Wärmepumpe), um die Jahreszeit der "
                      "Messung bereinigt und im Modell wieder auf die Monate verteilt",
             f"Household {house_day:.1f} kWh a day as an annual mean, median of {m_days} full days measured "
             "(grid import plus NEXA output, heat pump subtracted), corrected for the season it was measured "
             "in and spread back over the months in the model", "measured", round(house_day, 2))
    else:
        house_day = D["household_kwh_day"]
        extra_de = f" Gemessen sind bisher {m_days} Tage, ab {MIN_HOUSE_DAYS} rechnet die Prognose damit." if m_days else ""
        extra_en = f" {m_days} days measured so far, from {MIN_HOUSE_DAYS} on the forecast uses them." if m_days else ""
        note("house", f"Haushalt {house_day:.1f} kWh am Tag angenommen – trag den Jahresverbrauch deiner letzten "
                      f"Stromabrechnung ein, das ist der belastbarste Wert.{extra_de}",
             f"Household assumed at {house_day:.1f} kWh a day – enter the annual consumption from your last "
             f"electricity bill, that is the most solid figure.{extra_en}", "default", house_day)
    profile = measured.get("profile") or PROFILE

    # ---------------------------------------------------------------- Module
    pr = cfg.get("pr") or D["pr"]
    wp_total = sum(c.get("wp") or 0 for c in strings.values())
    guessed = [i for i, c in strings.items() if c.get("assumed")]
    if wp_total and not guessed:
        note("pv", f"{wp_total:.0f} Wp an {len(strings)} Strings, Neigung und Ausrichtung wie eingetragen",
             f"{wp_total:.0f} Wp across {len(strings)} strings, tilt and azimuth as configured", "config", wp_total)
    elif wp_total:
        src = {c.get("source") for i, c in strings.items() if c.get("assumed")}
        how_de = ("aus der Kurvenanpassung an die Messdaten" if "fit" in src
                  else "als Standortoptimum, die Leistung aus der gemessenen Spitze zurückgerechnet")
        how_en = ("from fitting the curve to the measured data" if "fit" in src
                  else "as the site optimum, with the power derived from the measured peak")
        note("pv", f"{wp_total:.0f} Wp an {len(strings)} Strings, davon {len(guessed)} mit geschätzter Ausrichtung "
                   f"({how_de}). Trag Neigung, Azimut und Wp auf der Einstellungsseite ein, dann steht die PV "
                   "auf eigenen Zahlen statt auf einer Schätzung.",
             f"{wp_total:.0f} Wp across {len(strings)} strings, {len(guessed)} of them with an estimated "
             f"orientation ({how_en}). Enter tilt, azimuth and Wp on the settings page and the PV rests on your "
             "own figures instead of an estimate.", "default", wp_total)
    else:
        note("pv", "Keine Modulleistung bekannt – die PV bleibt in der Prognose leer. Die Felder dafür stehen "
                   "auf der Einstellungsseite unter „Standort und Module“.",
             "No module power known – PV stays out of the forecast. The fields are on the settings page "
             "under “Location and modules”.", "missing")

    # ---------------------------------------------------------------- Batterie
    packs = cfg.get("packs") or 0
    meas = measured.get("battery") or {}
    nameplate = packs * D["pack_kwh"] if packs else 0.0
    cap = cfg.get("battery_kwh") or (meas.get("kwh") if meas.get("enough") else 0.0) or nameplate
    if meas.get("enough") and not cfg.get("battery_kwh"):
        eff = (nameplate / meas["kwh"] * 100) if nameplate and meas["kwh"] else None
        note("battery", f"Batterie {meas['wh_per_pct']:.1f} Wh je Prozentpunkt, gemessen über "
                        f"{meas['pct_observed']:.0f} Prozentpunkte Ladehub – das sind {cap:.2f} kWh, die "
                        f"hineingehen müssen." + (f" Gegenüber dem Typenschild von {nameplate:.2f} kWh "
                        f"entspricht das {eff:.0f} % Ladewirkungsgrad." if eff else ""),
             f"Battery {meas['wh_per_pct']:.1f} Wh per percentage point, measured over "
             f"{meas['pct_observed']:.0f} points of charging – that is {cap:.2f} kWh that has to go in."
             + (f" Against a nameplate of {nameplate:.2f} kWh that is {eff:.0f} % charging efficiency." if eff else ""),
             "measured", cap)
    elif cfg.get("battery_kwh"):
        note("battery", f"Batterie {cap:.1f} kWh nutzbar, eingetragen",
             f"Battery {cap:.1f} kWh usable, as configured", "config", cap)
    elif packs:
        note("battery", f"Batterie {cap:.1f} kWh nutzbar, aus {packs:.0f} Packs à {D['pack_kwh']} kWh",
             f"Battery {cap:.1f} kWh usable, from {packs:.0f} packs of {D['pack_kwh']} kWh", "default", cap)
    cal = measured.get("pv_cal")
    pv_factor = cal["factor"] if cal else 1.0
    if cal:
        note("pv_cal", f"PV mit dem Faktor {pv_factor:.2f} an die Messung angeglichen: in {cal['hours']} Sonnenstunden "
                       f"kamen {cal['measured_kwh']:.1f} kWh an, wo das Modell {cal['modelled_kwh']:.1f} kWh erwartet "
                       "hätte. Darin steckt alles, was das Modell nicht kennt – Verschattung, Schmutz, eine schiefe "
                       "Annahme zur Ausrichtung. Ein Faktor hebt oder senkt aber nur die Höhe, nicht den Verlauf über "
                       "das Jahr: liegt es an der Ausrichtung, stimmt die Sommer-Winter-Verteilung erst, wenn Neigung "
                       "und Azimut eingetragen sind.",
             f"PV scaled to the measurement by a factor of {pv_factor:.2f}: over {cal['hours']} sunny hours "
             f"{cal['measured_kwh']:.1f} kWh arrived where the model expected {cal['modelled_kwh']:.1f} kWh. That "
             "covers everything the model does not know – shading, dirt, a wrong guess at the orientation. A factor "
             "only moves the level, not the shape over the year: if the orientation is the cause, the summer-winter "
             "split is only right once tilt and azimuth are entered.",
             "measured", pv_factor)

    # Leistungsgrenze der Wärmepumpe: was sie nicht schafft, macht der Heizstab – mit COP 1.
    hp_kw = cfg.get("power_class_kw") or D["power_class_kw"]
    out_max = (cfg.get("max_out_w") or D["max_out_w"]) / 1000.0        # kWh je Stunde
    charge_max = (cfg.get("max_charge_w") or D["max_charge_w"]) / 1000.0
    soc_floor = cap * D["soc_min"]

    # ---------------------------------------------------------------- Heizgradstunden für die Verteilung
    degree_hours = sum(max(0.0, limit - t) for _, _, _, _, t, _, _ in hours)
    if line.get("enough"):
        # Die Gerade liefert die Heizwärme direkt; das Warmwasser kommt weiter aus dem Anteil am Gasanker,
        # sonst aus dem Standardanteil einer typischen Jahreswärme.
        heating_year = degree_hours / 24.0 * line["per_kelvin_day"]
        dhw_year = (gas * eff * dhw_share) if gas else heating_year * dhw_share / max(1.0 - dhw_share, 0.1)
    else:
        heating_year = (heat_year or 0.0) * (1.0 - dhw_share)
        dhw_year = (heat_year or 0.0) * dhw_share
    per_degree_hour = heating_year / degree_hours if degree_hours else 0.0
    dhw_hour = dhw_year / max(len(hours), 1)

    # ---------------------------------------------------------------- Stunde für Stunde
    months = {m: {"m": m, "household_kwh": 0.0, "heatpump_kwh": 0.0, "heat_kwh": 0.0, "eheat_kwh": 0.0,
                  "pv_kwh": 0.0, "self_kwh": 0.0, "grid_kwh": 0.0, "feedin_kwh": 0.0, "lost_kwh": 0.0}
              for m in range(1, 13)}
    soc = cap * 0.5
    for ts, ghi, dni, dhi, t_air, az, el in hours:
        dt = datetime.datetime.utcfromtimestamp(ts)
        m = months[dt.month]

        heat = per_degree_hour * max(0.0, limit - t_air) + dhw_hour
        if heat > 0:
            share_dhw = dhw_hour / heat
            c_heat = cop(t_air, flow_temp(t_air, curve), eta)
            c_dhw = cop(t_air, D["dhw_flow_c"], eta)
            # Die Wärmepumpe verliert mit sinkender Außentemperatur an Leistung; was darüber hinaus gebraucht
            # wird, macht der Heizstab elektrisch, also mit COP 1. Das ist der Grund, warum die kältesten
            # Wochen überproportional teuer sind.
            capacity = hp_kw * max(0.45, min(1.0, 0.7 + 0.03 * t_air))
            by_hp = min(heat, capacity)
            by_rod = heat - by_hp
            hp = by_hp * ((1.0 - share_dhw) / c_heat + share_dhw / c_dhw) + by_rod
            m_rod = by_rod
        else:
            hp = 0.0
            m_rod = 0.0
        house = house_day * profile[dt.hour] * season(dt.month)
        load = house + hp
        pv = pv_hour(ghi, dni, dhi, az, el, strings, pr) * pv_factor

        direct = min(pv, load, out_max)
        surplus = pv - direct
        charge = min(surplus, max(0.0, cap - soc) / D["eta_charge"], charge_max)
        soc += charge * D["eta_charge"]
        lost = surplus - charge
        need = load - direct
        avail = max(0.0, out_max - direct)
        dis = min(need, avail, max(0.0, soc - soc_floor) * D["eta_discharge"])
        soc -= dis / D["eta_discharge"]
        grid = load - direct - dis

        m["household_kwh"] += house; m["heatpump_kwh"] += hp; m["heat_kwh"] += heat; m["eheat_kwh"] += m_rod
        m["pv_kwh"] += pv; m["self_kwh"] += direct + dis; m["grid_kwh"] += grid; m["lost_kwh"] += lost

    # ---------------------------------------------------------------- Geld
    price = (cfg.get("price_ct_kwh") or D["price_ct_kwh"]) / 100.0
    feedin = (cfg.get("feedin_ct_kwh") or 0.0) / 100.0
    base_month = cfg.get("base_eur_month") if cfg.get("base_eur_month") is not None else D["base_eur_month"]
    for m in months.values():
        m["feedin_kwh"] = m["lost_kwh"] if feedin > 0 else 0.0
        m["cost_eur"] = m["grid_kwh"] * price + base_month - m["feedin_kwh"] * feedin
        for k, v in m.items():
            if k != "m":
                m[k] = round(v, 2)

    total = {k: round(sum(m[k] for m in months.values()), 1)
             for k in ("household_kwh", "heatpump_kwh", "heat_kwh", "eheat_kwh", "pv_kwh", "self_kwh",
                       "grid_kwh", "feedin_kwh", "lost_kwh")}
    total["load_kwh"] = round(total["household_kwh"] + total["heatpump_kwh"], 1)
    total["spf"] = round(total["heat_kwh"] / total["heatpump_kwh"], 2) if total["heatpump_kwh"] else None
    total["self_share_pct"] = round(100.0 * total["self_kwh"] / total["pv_kwh"], 1) if total["pv_kwh"] else None
    total["autarky_pct"] = round(100.0 * total["self_kwh"] / total["load_kwh"], 1) if total["load_kwh"] else None
    total["cost_energy_eur"] = round(total["grid_kwh"] * price, 2)
    total["cost_base_eur"] = round(base_month * 12.0, 2)
    total["revenue_eur"] = round(total["feedin_kwh"] * feedin, 2)
    total["cost_total_eur"] = round(total["cost_energy_eur"] + total["cost_base_eur"] - total["revenue_eur"], 2)
    # Was ohne Anlage zu zahlen wäre: der ganze Bedarf aus dem Netz
    total["cost_without_pv_eur"] = round(total["load_kwh"] * price + base_month * 12.0, 2)
    total["saving_eur"] = round(total["cost_without_pv_eur"] - total["cost_total_eur"], 2)
    total["price_ct_kwh"] = round(price * 100, 2)
    total["base_eur_month"] = round(base_month, 2)

    # ---------------------------------------------------------------- Unsicherheit
    # Jede Annahme trägt eine eigene Streuung bei. Sie werden quadratisch addiert, weil sie unabhängig
    # voneinander danebenliegen können – nicht alle in dieselbe Richtung. Das Ergebnis ist ein ehrliches
    # Band um die Zahl, und es ist der Grund, beim Abschlag eher nach oben zu runden als nach unten.
    spread = {"heat": 0.0, "cop": 0.0, "house": 0.0, "pv": 0.0, "weather": 0.06}
    if line.get("enough"):
        spread["heat"] = 0.07                      # gemessene Kennlinie, aber nur ein Teiljahr
    elif gas:
        spread["heat"] = 0.12                      # Gasverbrauch schwankt mit dem Winter und dem Ablesetag
    elif area:
        spread["heat"] = 0.30                      # Wohnfläche mal Kennwert ist grob
    else:
        spread["heat"] = 0.0                       # keine Wärmepumpe im Modell, also auch keine Unsicherheit
    spread["cop"] = 0.05 if measured.get("eta_carnot") else 0.12
    spread["house"] = 0.04 if power_year else (0.10 if m_days >= MIN_HOUSE_DAYS else 0.25)
    spread["pv"] = 0.0 if not wp_total else (0.08 if cal else (0.15 if not guessed else 0.35))
    # Auf die Rechnung wirkt jede Unsicherheit nur mit ihrem Anteil am Netzbezug
    grid = max(total["grid_kwh"], 1.0)
    weights = {"heat": total["heatpump_kwh"] / grid, "cop": total["heatpump_kwh"] / grid,
               "house": total["household_kwh"] / grid, "pv": total["pv_kwh"] / grid, "weather": 1.0}
    rel = math.sqrt(sum((spread[k] * weights[k]) ** 2 for k in spread))
    band = round(total["cost_total_eur"] * rel, 0)
    total["uncertainty_pct"] = round(rel * 100, 1)
    total["cost_low_eur"] = round(total["cost_total_eur"] - band, 0)
    total["cost_high_eur"] = round(total["cost_total_eur"] + band, 0)

    # ---------------------------------------------------------------- Abschlag
    # Empfohlen wird der Erwartungswert, aufgerundet auf die nächsten fünf Euro – nicht die Oberkante des
    # Bandes. Ein zu hoher Abschlag ist ein zinsloses Darlehen an den Versorger; wer die Nachzahlung mehr
    # fürchtet als das gebundene Geld, findet die Oberkante als eigene Zahl daneben.
    recommended = math.ceil(total["cost_total_eur"] / 12.0 / 5.0) * 5.0
    current = cfg.get("abschlag_eur_month")
    ab = {"recommended_eur": recommended, "current_eur": current,
          "monthly_average_eur": round(total["cost_total_eur"] / 12.0, 2),
          "low_eur": math.ceil(total["cost_low_eur"] / 12.0 / 5.0) * 5.0,
          "high_eur": math.ceil(total["cost_high_eur"] / 12.0 / 5.0) * 5.0}
    if current:
        delta = round((current - recommended) * 12.0, 0)
        ab["delta_eur"] = round(current - recommended, 2)
        ab["year_delta_eur"] = delta
        if abs(current - recommended) < 5:
            ab["de"] = (f"Der Abschlag von {current:.0f} € passt: rechnerisch nötig sind {recommended:.0f} € im Monat. "
                        "Nichts ändern.")
            ab["en"] = (f"The {current:.0f} € payment is right: {recommended:.0f} € a month is what the model needs. "
                        "Leave it alone.")
            ab["action"] = "keep"
        elif current > recommended:
            ab["de"] = (f"Der Abschlag ist zu hoch: {current:.0f} € statt der nötigen {recommended:.0f} €. Über das Jahr "
                        f"leihst du dem Versorger {abs(delta):.0f} € zinslos. Senken spart nichts, holt aber das Geld "
                        "in den eigenen Monat.")
            ab["en"] = (f"The payment is too high: {current:.0f} € instead of the {recommended:.0f} € needed. Over the year "
                        f"you lend the supplier {abs(delta):.0f} € interest-free. Lowering it saves nothing but keeps the "
                        "money in your own month.")
            ab["action"] = "lower"
        else:
            ab["de"] = (f"Der Abschlag ist zu niedrig: {current:.0f} € statt der nötigen {recommended:.0f} €. Bei der "
                        f"Jahresrechnung stünden rund {abs(delta):.0f} € Nachzahlung an. Rechtzeitig erhöhen.")
            ab["en"] = (f"The payment is too low: {current:.0f} € instead of the {recommended:.0f} € needed. The annual bill "
                        f"would come with about {abs(delta):.0f} € to pay on top. Raise it in good time.")
            ab["action"] = "raise"
    else:
        ab["de"] = (f"Rechnerisch nötig sind {recommended:.0f} € im Monat, aus einer Jahresrechnung von "
                    f"{total['cost_total_eur']:.0f} € ± {band:.0f} €. Wer keine Nachzahlung riskieren will, nimmt "
                    f"{ab['high_eur']:.0f} € und bekommt die Differenz am Jahresende zurück. Trage deinen laufenden "
                    "Abschlag auf der Einstellungsseite ein, dann sagt die Empfehlung, ob er passt.")
        ab["en"] = (f"The model needs {recommended:.0f} € a month, from an annual bill of "
                    f"{total['cost_total_eur']:.0f} € ± {band:.0f} €. If you would rather not risk a bill at the end, "
                    f"take {ab['high_eur']:.0f} € and get the difference back. Enter your current payment on the "
                    "settings page and the recommendation will say whether it fits.")
        ab["action"] = "unknown"

    anchored = bool(power_year) or m_days >= MIN_HOUSE_DAYS
    heat_known = line.get("enough") or bool(gas)
    quality = "measured" if (line.get("enough") and anchored and wp_total and cal and measured.get("eta_carnot")) else \
              "partial" if (heat_known or anchored) else "assumed"
    return {"updated": int(time.time()), "period": [start.isoformat(), end.isoformat()],
            "year": total, "months": [months[m] for m in range(1, 13)], "abschlag": ab,
            "assumptions": notes, "quality": quality}


# ---------------------------------------------------------------------------- Messwerte aus InfluxDB
def influx_query(flux):
    if not INFLUX_TOKEN:
        return []
    req = urllib.request.Request(f"{INFLUX_URL}/api/v2/query?org={urllib.parse.quote(INFLUX_ORG)}",
                                 data=flux.encode(), method="POST",
                                 headers={"Authorization": f"Token {INFLUX_TOKEN}",
                                          "Content-Type": "application/vnd.flux", "Accept": "application/csv"})
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode()
    rows = []
    header = None
    for line in text.splitlines():
        if not line or line.startswith("#"):
            header = None
            continue
        cells = line.split(",")
        if header is None:
            header = cells
            continue
        rows.append(dict(zip(header, cells)))
    return rows


def influx_write(lines):
    if not INFLUX_TOKEN or not lines:
        return
    q = urllib.parse.urlencode({"org": INFLUX_ORG, "bucket": INFLUX_BUCKET, "precision": "s"})
    req = urllib.request.Request(f"{INFLUX_URL}/api/v2/write?{q}", data="\n".join(lines).encode(), method="POST",
                                 headers={"Authorization": f"Token {INFLUX_TOKEN}", "Content-Type": "text/plain; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def measure(curve):
    """Was sich messen lässt, aus InfluxDB holen: Haushalt je Tag und Stundenprofil, COP je Außentemperatur."""
    out = {}
    # Haushalt ohne Wärmepumpe: der Shelly misst den ganzen Hausverbrauch, die Wolf ihren eigenen Anteil.
    try:
        rows = influx_query(f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -60d)
  |> filter(fn: (r) => r._measurement == "shelly" and r._field == "household_w")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])''')
        hp_rows = influx_query(f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -60d)
  |> filter(fn: (r) => r._measurement == "wolf_derived" and r._field == "strom_w")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])''')
        hp_by_hour = {r["_time"]: float(r["_value"]) for r in hp_rows if r.get("_value")}
        by_hour = {}
        for r in rows:
            if not r.get("_value"):
                continue
            t = r["_time"]
            w = max(0.0, float(r["_value"]) - hp_by_hour.get(t, 0.0))
            by_hour[t] = w / 1000.0            # kWh in dieser Stunde
        # Der Median über die Tage statt des Mittels: ein einzelner Tag mit Handwerkern, Inbetriebnahme oder
        # Waschmaschinenmarathon soll die Jahresprognose nicht anheben. Nur volle Tage zählen.
        # Jeder Tag wird durch den Saisonfaktor seines Monats geteilt, bevor der Median gebildet wird: sonst
        # wäre eine Messung aus lauter Januartagen als Jahresmittel zu hoch und eine aus Julitagen zu niedrig.
        by_day = {}
        for t, kwh in by_hour.items():
            by_day.setdefault(t[:10], [0.0, 0])[0] += kwh / season(int(t[5:7]))
            by_day[t[:10]][1] += 1
        full = sorted(v[0] for v in by_day.values() if v[1] >= 22)
        if full:
            out["household_kwh_day"] = round(full[len(full) // 2] if len(full) % 2
                                             else (full[len(full) // 2 - 1] + full[len(full) // 2]) / 2.0, 2)
            out["household_days"] = len(full)
        if len(by_hour) >= 48:
            shape = [0.0] * 24
            for t, kwh in by_hour.items():
                shape[int(t[11:13])] += kwh
            total = sum(shape)
            if total > 0:
                out["profile"] = [round(v / total, 5) for v in shape]
    except Exception as exc:
        LOG.warning("Haushaltsmessung nicht lesbar: %s", exc)
    # COP je Außentemperatur aus den einzelnen Takten
    try:
        rows = influx_query(f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -365d)
  |> filter(fn: (r) => r._measurement == "wolf_cycle" and (r._field == "cop" or r._field == "t_out"))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["_time", "cop", "t_out"])''')
        points = [(float(r["t_out"]), float(r["cop"])) for r in rows if r.get("cop") and r.get("t_out")]
        out["cop_points"] = len(points)
        fitted = fit_eta(points, curve)
        if fitted:
            out["eta_carnot"] = fitted
    except Exception as exc:
        LOG.warning("COP-Messwerte nicht lesbar: %s", exc)
    try:
        out["battery"] = battery_wh_per_pct()
    except Exception as exc:
        LOG.warning("Batteriemessung nicht lesbar: %s", exc)
    try:
        out["heat_line"] = heat_line()
    except Exception as exc:
        LOG.warning("Gebäudekennlinie nicht lesbar: %s", exc)
    return out


# ---------------------------------------------------------------------------- Dienst
CFG = {"site": None, "tariff": None, "house": None, "wolf_circuit": None, "wolf_heatpump": None,
       "nexa_packs": None, "pv_model": None, "tado_rooms": None}
wake = threading.Event()


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload)
    except Exception:
        return
    topic = msg.topic
    if topic.endswith("/config/site"):
        CFG["site"] = payload
    elif topic.endswith("/config/tariff"):
        CFG["tariff"] = payload
    elif topic.endswith("/config/house"):
        CFG["house"] = payload
    elif topic.endswith("/wolf/circuit/state"):
        CFG["wolf_circuit"] = payload
    elif topic.endswith("/wolf/heatpump/state"):
        CFG["wolf_heatpump"] = payload
    elif topic.endswith("/grolo/pv_model"):
        CFG["pv_model"] = payload
    elif topic.endswith("/grolo/tado/rooms"):
        CFG["tado_rooms"] = payload
    elif topic.endswith("/state") and "/grobro/" in topic:
        q = payload.get("batteryPackageQuantity")
        if q:
            CFG["nexa_packs"] = float(q)
        return
    else:
        return
    wake.set()


def gather_cfg():
    """Konfiguration aus den drei retained Topics und dem, was die Anlage selbst über sich weiß."""
    tariff = CFG["tariff"] or {}
    house = CFG["house"] or {}
    circuit = CFG["wolf_circuit"] or {}
    cfg = {
        "price_ct_kwh": tariff.get("price_ct_kwh"), "feedin_ct_kwh": tariff.get("feedin_ct_kwh"),
        "base_eur_month": tariff.get("base_eur_month"), "abschlag_eur_month": tariff.get("abschlag_eur_month"),
        "gas_kwh_year": house.get("gas_kwh_year"), "boiler_eff": house.get("boiler_eff"),
        "power_kwh_year": house.get("power_kwh_year"),
        "dhw_share": house.get("dhw_share"), "area_m2": house.get("area_m2"),
        "battery_kwh": house.get("battery_kwh"), "packs": CFG["nexa_packs"],
    }
    # Wie warm das Haus tatsächlich gefahren wird. Das ist keine Kleinigkeit: die Heizgrenze und die
    # Heizkurve beziehen sich beide auf eine Raumtemperatur, und 20 °C anzunehmen, während überall 22 °C
    # stehen, verschiebt den ganzen Wärmebedarf.
    rooms = CFG["tado_rooms"] or {}
    temps = [r["temp_c"] for r in rooms.values() if isinstance(r, dict) and r.get("temp_c") is not None]
    sets = [r["setpoint_c"] for r in rooms.values() if isinstance(r, dict) and r.get("setpoint_c") is not None]
    if temps:
        cfg["room_c"] = round(sum(temps) / len(temps), 2)
        cfg["room_rooms"] = len(temps)
        cfg["room_min_c"] = round(min(temps), 2)
        cfg["room_max_c"] = round(max(temps), 2)
    if sets:
        cfg["room_setpoint_c"] = round(sum(sets) / len(sets), 2)

    hp = CFG["wolf_heatpump"] or {}
    if hp.get("leistungsklasse"):
        cfg["power_class_kw"] = hp["leistungsklasse"]
    # Heizkurve der Wolf, wenn sie auf dem Bus steht – sonst bleibt der Standard stehen
    if circuit.get("vorlauftemperatur_heizkurve"):
        cfg["flow_norm_c"] = circuit["vorlauftemperatur_heizkurve"]
    if circuit.get("sockeltemperatur_heizkurve"):
        cfg["flow_base_c"] = circuit["sockeltemperatur_heizkurve"]
    if circuit.get("normaussentemperatur_heizkurve"):
        cfg["t_norm_c"] = circuit["normaussentemperatur_heizkurve"]
    return cfg


def strings_of(site, model):
    """Module je String: was eingetragen ist, sonst was der Wetterdienst geschätzt hat.

    weather.py führt für jeden String mit messbarer Leistung eine Ausrichtung mit: entweder aus der
    Kurvenanpassung (fit) oder, solange die nicht greift, das Standortoptimum mit einer Leistung, die aus der
    gemessenen Spitze zurückgerechnet ist. Das ist eine Annahme – aber eine aus Messdaten, und damit besser
    als die PV ganz wegzulassen. Sie wird als solche gekennzeichnet und weicht der Eintragung, sobald es eine gibt.
    """
    out = {}
    for k, v in ((site or {}).get("strings") or {}).items():
        try:
            if v.get("tilt") is None or v.get("azimuth") is None or not v.get("wp"):
                continue
            out[int(k)] = {"tilt": float(v["tilt"]), "azimuth": float(v["azimuth"]), "wp": float(v["wp"]),
                           "pr": float(v.get("pr") or D["pr"]), "assumed": False}
        except (TypeError, ValueError):
            continue
    for k, v in ((model or {}).get("strings") or {}).items():
        try:
            i = int(k)
            if i in out or not v.get("wp"):
                continue
            out[i] = {"tilt": float(v["tilt"]), "azimuth": float(v["azimuth"]), "wp": float(v["wp"]),
                      "pr": float(v.get("pr") or D["pr"]), "assumed": True, "source": v.get("source")}
        except (TypeError, ValueError, KeyError):
            continue
    return out


def run_once(client):
    site = CFG["site"] or {}
    lat, lon = site.get("lat"), site.get("lon")
    if lat is None or lon is None:
        LOG.info("Noch kein Standort auf %s/grolo/config/site – warte", BASE)
        return
    cfg = gather_cfg()
    curve = {k: cfg.get(k) if cfg.get(k) is not None else D[k] for k in ("t_norm_c", "flow_norm_c", "flow_base_c")}
    curve["room_c"] = cfg.get("room_c") or D["room_c"]
    strings = strings_of(site, CFG["pv_model"])
    measured = measure(curve)
    try:
        measured["pv_cal"] = calibrate_pv(float(lat), float(lon), strings, cfg.get("pr") or D["pr"])
    except Exception as exc:
        LOG.warning("PV-Eichung fehlgeschlagen: %s", exc)
    result = build_forecast(float(lat), float(lon), strings, cfg, measured)
    client.publish(f"{BASE}/grolo/forecast", json.dumps(result, ensure_ascii=False), retain=True)
    flat = {k: v for k, v in result["year"].items() if isinstance(v, (int, float))}
    cal = next((a for a in result["assumptions"] if a["key"] == "pv_cal"), None)
    room = next((a for a in result["assumptions"] if a["key"] == "room"), None)
    flat.update({"room_c": (room or {}).get("value") or 0.0,
                 "pv_factor": (cal or {}).get("value") or 1.0,
                 "abschlag_eur": result["abschlag"]["recommended_eur"],
                 "abschlag_ist_eur": result["abschlag"].get("current_eur") or 0.0,
                 "quality": result["quality"]})
    client.publish(f"{BASE}/grolo/forecast/state", json.dumps(flat, ensure_ascii=False), retain=True)
    # Die Batteriekennzahl steht eigenständig, damit die Bedienseite Restzeiten rechnen kann, ohne die
    # ganze Prognose zu laden – und damit Website, Einstellungsseite und Grafana dieselbe Zahl nennen.
    bat = measured.get("battery") or {}
    if bat.get("enough"):
        client.publish(f"{BASE}/grolo/battery", json.dumps({
            "wh_per_pct": bat["wh_per_pct"], "kwh": bat["kwh"], "pct_observed": bat["pct_observed"],
            "packs": cfg.get("packs"), "nameplate_kwh": round((cfg.get("packs") or 0) * D["pack_kwh"], 3),
            "updated": int(time.time())}, ensure_ascii=False), retain=True)
    lines = []
    for m in result["months"]:
        vals = ",".join(f"{k}={float(v)}" for k, v in m.items() if k != "m")
        lines.append(f"forecast_month,month={m['m']:02d} {vals} {result['updated']}")
    influx_write(lines)
    y = result["year"]
    LOG.info("Prognose (%s): %s kWh Verbrauch, davon %s kWh Wärmepumpe; %s kWh PV, %s kWh Netz; "
             "%s € im Jahr, Abschlag %s €", result["quality"], y["load_kwh"], y["heatpump_kwh"], y["pv_kwh"],
             y["grid_kwh"], y["cost_total_eur"], result["abschlag"]["recommended_eur"])


def main():
    client = mqtt.Client(client_id=f"grolo-forecast-{os.getpid()}", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = lambda c, u, f, rc, p=None: (
        LOG.info("MQTT verbunden %s:%s", HOST, PORT),
        c.subscribe([(f"{BASE}/grolo/config/site", 0), (f"{BASE}/grolo/config/tariff", 0),
                     (f"{BASE}/grolo/config/house", 0), (f"{BASE}/grolo/wolf/circuit/state", 0),
                     (f"{BASE}/grolo/wolf/heatpump/state", 0), (f"{BASE}/grolo/pv_model", 0),
                     (f"{BASE}/grolo/tado/rooms", 0), (f"{BASE}/grobro/+/state", 0)]))
    client.on_message = on_message
    while True:
        try:
            client.connect(HOST, PORT, 60); break
        except Exception as exc:
            LOG.warning("MQTT nicht erreichbar (%s), neuer Versuch in 10 s", exc); time.sleep(10)
    client.loop_start()
    wake.wait(60)          # den retained Konfigurationen Zeit geben, einzutreffen
    while True:
        wake.clear()
        try:
            run_once(client)
        except Exception:
            LOG.exception("Prognose fehlgeschlagen")
        wake.wait(INTERVAL)


if __name__ == "__main__":
    main()
