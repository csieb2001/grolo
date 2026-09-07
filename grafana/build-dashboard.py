#!/usr/bin/env python3
"""GroLo: erzeugt die Grafana-Dashboards für den Growatt NEXA 2000 in Englisch und Deutsch.

    python3 grafana/build-dashboard.py

Ausgabe: grafana/dashboards/nexa-en.json (uid nexa2000, Startseite) und grafana/dashboards/nexa-de.json (uid nexa2000-de).
Grafana lädt die Dateien per Provisioning automatisch nach (alle 30 s). Die Dashboards verlinken sich gegenseitig.
"""
import json
import os

DS = {"type": "influxdb", "uid": "influx-nexa"}
BUCKET = os.environ.get("INFLUX_BUCKET", "nexa")
TZ = os.environ.get("TZ", "Europe/Berlin")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboards")

# ----------------------------------------------------------------- Übersetzung: Deutsch ist der Schlüssel
EN = {
    "Jetzt": "Now", "PV-Leistung": "PV power", "Aktuelle Leistung aller PV-Strings": "Current power of all PV strings",
    "Ausgang ins Haus": "Output to house", "AC-Ausgangsleistung des NEXA": "AC output power of the NEXA",
    "Batterie-Leistung": "Battery power", "Lade-/Entladeleistung der Batterie": "Battery charge/discharge power",
    "Ladezustand": "State of charge", "Hausverbrauch": "Household load", "Nur mit Smart Meter / GroPlug befüllt, sonst 0": "Only filled with a smart meter / GroPlug, otherwise 0",
    "Batterie-Status": "Battery status", "Statusregister 10 des Geräts. Auf aktueller Firmware oft „Ruhe“, obwohl die Bilanz Laden oder Entladen zeigt.": "Device status register 10. On current firmware often \"Idle\" although the balance shows charging or discharging.", "Betriebsmodus": "Operating mode", "Systemtemperatur": "System temperature", "Batterietemperatur": "Battery temperature",
    "Lädt": "Charging", "Entlädt": "Discharging", "Ruhe": "Idle", "Last zuerst": "Load first", "Batterie zuerst": "Battery first", "Smart": "Smart",
    "Leistung und Ladezustand": "Power and state of charge", "Leistungsverlauf": "Power history", "PV": "PV", "Ins Haus": "To house",
    "Batterie (+ laden / − entladen)": "Battery (+ charge / − discharge)", "Batterie: positiv = laden, negativ = entladen": "Battery: positive = charging, negative = discharging",
    "Energie": "Energy", "PV-Ertrag pro Tag (30 Tage)": "PV yield per day (30 days)", "PV-Ertrag": "PV yield",
    "Aus den gemessenen Leistungswerten integriert, Tagesgrenzen lokale Zeit, Balken auf Tagesmitte. Zeitraum auf 30 Tage stellen, um alle Tage zu sehen.": "Integrated from measured power, day boundaries in local time, bars at midday. Set the time range to 30 days to see all days.",
    "Abgabe ins Haus pro Tag (30 Tage)": "Output to house per day (30 days)",
    "Wohin ging der PV-Strom heute?": "Where did today's PV energy go?", "Direkt ins Haus": "Directly to house", "In die Batterie": "Into the battery",
    "Woher kam der Hausstrom heute?": "Where did today's house energy come from?", "Direkt aus PV": "Directly from PV", "Aus der Batterie": "From the battery",
    "PV heute": "PV today", "Aus Messungen berechnet. Die Energiezähler-Register des Geräts (eacToday usw.) bleiben auf aktueller Firmware bei 0.": "Computed from measurements. The device energy counter registers (eacToday etc.) stay at 0 on current firmware.", "Aus Messungen berechnet, seit Monatsbeginn": "Computed from measurements, since start of month", "Aus Messungen berechnet, seit Jahresbeginn": "Computed from measurements, since start of year", "Aus Messungen berechnet, seit Aufzeichnungsbeginn": "Computed from measurements, since recording began", "PV Monat": "PV month", "PV Jahr": "PV year", "PV gesamt": "PV total",
    "Heute aus Messwerten": "Today from measurements", "Batterie geladen": "Battery charged", "Batterie entladen": "Battery discharged",
    "PV-Strings": "PV strings", "Leistung je String": "Power per string", "Leistung = Spannung × Strom je Eingang": "Power = voltage × current per input",
    "Spannung je String": "Voltage per string", "Strom je String": "Current per string", "String": "String",
    "Batterie und Technik": "Battery and technical", "Temperaturen": "Temperatures", "System": "System", "Batterie": "Battery",
    "Zellspannung (min / max)": "Cell voltage (min / max)", "Ein großer Abstand deutet auf unausgeglichene Zellen hin": "A large spread indicates unbalanced cells",
    "Zyklen": "Cycles", "Gesundheit (SoH)": "Health (SoH)", "Batteriepacks": "Battery packs",
    "Entlade-Grenze": "Discharge limit", "Unter diesen SoC entlädt die Batterie nicht": "The battery does not discharge below this SoC", "Lade-Grenze": "Charge limit",
    "Netzspannung": "Grid voltage", "Register 115. Nahe 0 V, wenn der NEXA vom Netz getrennt ist.": "Register 115. Near 0 V when the NEXA is disconnected from the grid.",
    "Netzleistung": "Grid power", "Register 116, Offset 30000 = 0, Schritt 0,1 W (38000 = 800 W)": "Register 116, offset 30000 = 0, step 0.1 W (38000 = 800 W)",
    "Zellspannung Differenz": "Cell voltage spread", "Letzte Nachricht": "Last message", "Zeitpunkt des letzten Datensatzes vom Dongle": "Time of the last data packet from the dongle",
    "Firmware NEXA (Reg. 119/120)": "NEXA firmware (reg. 119/120)", "Rohteile aus den Registern, Zusammensetzung laut Growatt unbekannt": "Raw parts from the registers, Growatt's composition unknown",
    "Dongle Firmware / Modell": "Dongle firmware / model", "WLAN-Signal Dongle": "Dongle Wi-Fi signal", "Wird nur beim Verbindungsaufbau des Dongles gemeldet": "Only reported when the dongle connects",
    "Batteriepacks erkannt": "Battery packs detected", "Zellspannung min": "Cell voltage min",
    "SoC je Batteriepack": "SoC per battery pack", "Pack": "Pack", "Temperatur je Pack": "Temperature per pack",
    "Seriennummern Erweiterungspacks": "Serial numbers of expansion packs", "Pack 1 ist das Hauptgerät mit der Geräteseriennummer. Leer = kein Erweiterungspack.": "Pack 1 is the main unit with the device serial. Empty = no expansion pack.",
    "Unbekannte Register (Forschung)": "Unknown registers (research)", "Unbekannte Eingangsregister, Verlauf (nur ≠ 0)": "Unknown input registers, history (≠ 0 only)",
    "Rohwerte der Register, die die Bridge nicht kennt. 30000 = Offset für 0 bei vorzeichenbehafteten Werten.": "Raw values of registers the bridge does not know. 30000 = offset for 0 in signed values.",
    "Halteregister-Dump (unbekannt, ≠ 0)": "Holding register dump (unknown, ≠ 0)", "Kommt stündlich vom Gerät (Nachricht 0x0103).": "Sent hourly by the device (message 0x0103).",
    "Register": "Register", "Wert": "Value",
    "Wetter": "Weather", "Außentemperatur": "Outdoor temperature", "Open-Meteo für den Anlagenstandort, alle 10 Minuten": "Open-Meteo for the plant location, every 10 minutes",
    "Wetterzustand": "Conditions", "WMO-Wettercode von Open-Meteo": "WMO weather code from Open-Meteo", "Bewölkung": "Cloud cover", "Globalstrahlung": "Global irradiance",
    "Kurzwellige Einstrahlung auf die Horizontale in W/m², Referenz für die PV-Leistung": "Shortwave irradiance on the horizontal in W/m², reference for PV power",
    "Sonnenschein heute": "Sunshine today", "Prognostizierte Sonnenscheindauer des Tages": "Forecast sunshine duration for the day",
    "Strahlungssumme heute / morgen": "Irradiation sum today / tomorrow", "Tagessumme der Globalstrahlung in MJ/m² laut Vorhersage (1 MJ/m² ≈ 0,28 kWh/m²)": "Daily sum of global irradiance in MJ/m² per forecast (1 MJ/m² ≈ 0.28 kWh/m²)",
    "Globalstrahlung und PV-Leistung": "Global irradiance and PV power", "Globalstrahlung W/m²": "Irradiance W/m²", "PV-Leistung W": "PV power W",
    "Verhältnis von PV-Leistung zu Einstrahlung zeigt Verschattung, Ausrichtung und Verschmutzung. Strahlung links (W/m²), PV rechts (W).": "The ratio of PV power to irradiance reveals shading, orientation and soiling. Irradiance left (W/m²), PV right (W).",
    "Bewölkung und Temperatur": "Cloud cover and temperature", "Bewölkung %": "Cloud cover %", "Temperatur °C": "Temperature °C",
    "Vorhersage 48 h: Einstrahlung und Bewölkung": "Forecast 48 h: irradiance and cloud cover",
    "Stündliche Open-Meteo-Vorhersage ab jetzt. Zeitraum des Dashboards ist hier ohne Wirkung, das Panel zeigt immer die nächsten 48 Stunden.": "Hourly Open-Meteo forecast from now. The dashboard time range has no effect here, the panel always shows the next 48 hours.",
    "Sonnenaufgang / -untergang": "Sunrise / sunset", "Stunde": "Hour", "So": "Sun", "Mo": "Mon", "Di": "Tue", "Mi": "Wed", "Do": "Thu", "Fr": "Fri", "Sa": "Sat", "Heute, lokale Zeit": "Today, local time", "heute": "today", "morgen": "tomorrow",
    "Klar": "Clear", "Überwiegend klar": "Mainly clear", "Teilweise bewölkt": "Partly cloudy", "Bedeckt": "Overcast", "Nebel": "Fog", "Reifnebel": "Rime fog", "Sprühregen": "Drizzle",
    "Leichter Regen": "Light rain", "Regen": "Rain", "Starker Regen": "Heavy rain", "Schneefall": "Snow", "Regenschauer": "Showers", "Gewitter": "Thunderstorm", "PV aus Strings": "PV from strings", "PV (Register 7)": "PV (register 7)", "Geräteregister zum Vergleich": "Device registers for comparison",
    "AC-Ausgang aus Register 116 (0,1-W-Schritte, Offset 30000). Das Register pac (5) meldet auf aktueller Firmware dauerhaft 0.": "AC output from register 116 (0.1 W steps, offset 30000). Register pac (5) reports a constant 0 on current firmware.",
    "Bilanz PV minus Ausgang: positiv = laden, negativ = entladen. Register 11 meldet auf aktueller Firmware dauerhaft 0.": "Balance PV minus output: positive = charging, negative = discharging. Register 11 reports a constant 0 on current firmware.",
    "PV aus Spannung × Strom der Strings, Ausgang aus Register 116, Batterie als Bilanz PV minus Ausgang (positiv = laden). Gestrichelt das gerundete PV-Register des Geräts.": "PV from voltage × current of the strings, output from register 116, battery as balance PV minus output (positive = charging). Dashed: the device's rounded PV register.",
    "Rohwerte der Geräteregister. pac und Batterieleistung bleiben auf aktueller Firmware bei 0, deshalb rechnet das Dashboard mit Register 116 und den Strings.": "Raw device registers. pac and battery power stay at 0 on current firmware, which is why the dashboard uses register 116 and the strings.",
    "Summe Spannung × Strom aller Strings. Feiner und aktueller als das Geräteregister, das auf ganze Watt rundet.": "Sum of voltage × current of all strings. Finer and more current than the device register, which rounds to whole watts.", "PV-Eingänge belegt": "PV inputs in use", "belegt": "in use",
    "Eingänge mit mehr als 15 V in den letzten 24 h, von 4 MPPT-Eingängen. Freie Eingänge zeigen etwa 7 V.": "Inputs with more than 15 V in the last 24 h, out of 4 MPPT inputs. Free inputs show about 7 V.",
    "PV-Eingang 1 bis 4": "PV input 1 to 4", "Maximale Spannung je Eingang in 24 h. Grün = Panel angeschlossen (> 15 V), rot = frei.": "Maximum voltage per input in 24 h. Green = panel connected (> 15 V), red = free.", "Einstellungen": "Settings", "Einstellungsseite (GroLo)": "Settings page (GroLo)",
}


def build(lang):
    _ = (lambda s: s) if lang == "de" else (lambda s: EN.get(s, s))
    HEAD = f'import "timezone"\nimport "math"\nimport "date"\noption location = timezone.location(name: "{TZ}")\n'

    # ------------------------------------------------------------- Flux-Bausteine
    def q_series(field, label, fn="mean"):
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "nexa" and r._field == "{field}")
  |> aggregateWindow(every: v.windowPeriod, fn: {fn}, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    def q_last(field, measurement="nexa", rng="-1h"):
        return f'''from(bucket: "{BUCKET}")
  |> range(start: {rng})
  |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "{field}")
  |> last()
  |> keep(columns: ["_time", "_value"])'''

    def q_last_named(field, label, measurement="nexa", rng="-1h"):
        """Nur der Wert, benannt: für Stat-Panels mit mehreren Abfragen (kein Zeitfeld, das den Namen stiehlt)."""
        return f'''from(bucket: "{BUCKET}")
  |> range(start: {rng})
  |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "{field}")
  |> last()
  |> keep(columns: ["_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    def q_pv_detected(label):
        """Anzahl PV-Eingänge mit Spannung > 5 V in den letzten 24 h."""
        return f'''from(bucket: "{BUCKET}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "nexa" and (r._field == "pv1Voltage" or r._field == "pv2Voltage" or r._field == "pv3Voltage" or r._field == "pv4Voltage"))
  |> max()
  |> map(fn: (r) => ({{ r with _value: if r._value > 15.0 then 1 else 0 }}))
  |> group()
  |> sum()
  |> keep(columns: ["_value"])
  |> rename(columns: {{_value: "Value"}})'''

    def q_pivot_map(fields, expr_map):
        filt = " or ".join(f'r._field == "{f}"' for f in fields)
        maps = ", ".join(f'"{k}": {v}' for k, v in expr_map.items())
        keep = ", ".join(f'"{k}"' for k in expr_map)
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "nexa" and ({filt}))
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> map(fn: (r) => ({{ _time: r._time, {maps} }}))
  |> keep(columns: ["_time", {keep}])'''

    def q_daily_energy(field, label, days=30):
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r._measurement == "nexa" and r._field == "{field}")
  |> aggregateWindow(every: 1d, fn: (tables=<-, column) => tables |> integral(unit: 1h, column: column), timeSrc: "_start", createEmpty: false)
  |> map(fn: (r) => ({{ r with _value: r._value / 1000.0 }}))
  |> keep(columns: ["_time", "_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    SIGNED_BAT = ('if r.totalBatteryPackChargingStatus == "Discharging" then -1.0 * math.abs(x: r.totalBatteryPackChargingPower) '
                  'else math.abs(x: r.totalBatteryPackChargingPower)')

    def q_signed_battery(label):
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "nexa" and (r._field == "totalBatteryPackChargingPower" or r._field == "totalBatteryPackChargingStatus"))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.totalBatteryPackChargingPower and exists r.totalBatteryPackChargingStatus)
  |> map(fn: (r) => ({{ r with _value: {SIGNED_BAT} }}))
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    PV_FIELDS = [f"pv{i}{s}" for i in range(1, 5) for s in ("Voltage", "Current")]
    PV_EXPR = " + ".join(f"r.pv{i}Voltage * r.pv{i}Current" for i in range(1, 5))
    OUT_EXPR = "(r.onGridPower - 30000.0) / 10.0"  # Register 116 in 0,1-W-Schritten
    FLOW_FIELDS = PV_FIELDS + ["onGridPower"]
    FLOW_FILT = " or ".join(f'r._field == "{f}"' for f in FLOW_FIELDS)

    def q_flow_series(expr_map):
        """Zeitreihen aus PV (Strings) und Ausgang (Reg. 116): expr_map Label -> Flux-Ausdruck mit pv/out."""
        maps = ", ".join(f'"{k}": {v}' for k, v in expr_map.items())
        keep = ", ".join(f'"{k}"' for k in expr_map)
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "nexa" and ({FLOW_FILT}))
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.onGridPower and exists r.pv1Voltage)
  |> map(fn: (r) => {{
      pv = {PV_EXPR}
      out = {OUT_EXPR}
      return {{ _time: r._time, {maps} }}
    }})
  |> keep(columns: ["_time", {keep}])'''

    def q_flow_last(expr):
        """Letzter Wert eines Ausdrucks aus pv/out."""
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "nexa" and ({FLOW_FILT}))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.onGridPower and exists r.pv1Voltage)
  |> last(column: "onGridPower")
  |> map(fn: (r) => {{
      pv = {PV_EXPR}
      out = {OUT_EXPR}
      return {{ _time: r._time, _value: {expr} }}
    }})
  |> keep(columns: ["_time", "_value"])'''

    def q_flow_daily(expr, label, days=30):
        """Tagesenergie (kWh) eines Ausdrucks aus pv/out."""
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r._measurement == "nexa" and ({FLOW_FILT}))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.onGridPower and exists r.pv1Voltage)
  |> map(fn: (r) => {{
      pv = {PV_EXPR}
      out = {OUT_EXPR}
      return {{ r with _value: {expr} }}
    }})
  |> aggregateWindow(every: 1d, fn: (tables=<-, column) => tables |> integral(unit: 1h, column: column), timeSrc: "_start", createEmpty: false)
  |> timeShift(duration: 12h)
  |> map(fn: (r) => ({{ r with _value: r._value / 1000.0 }}))
  |> keep(columns: ["_time", "_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    def q_flow_today(expr, label):
        """Energie heute (kWh) eines Ausdrucks aus pv/out; chg/dis = Batterie laden/entladen aus der Bilanz."""
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: today())
  |> filter(fn: (r) => r._measurement == "nexa" and ({FLOW_FILT}))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.onGridPower and exists r.pv1Voltage)
  |> map(fn: (r) => {{
      pv = {PV_EXPR}
      out = {OUT_EXPR}
      chg = if pv - out > 0.0 then pv - out else 0.0
      dis = if out - pv > 0.0 then out - pv else 0.0
      return {{ r with _value: {expr} }}
    }})
  |> integral(unit: 1h)
  |> map(fn: (r) => ({{ r with _value: r._value / 1000.0 }}))
  |> keep(columns: ["_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    def q_flow_range(expr, label, start):
        """Energie (kWh) eines Ausdrucks aus pv/out über einen Zeitraum (Minutenmittel, dann Integral)."""
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: {start})
  |> filter(fn: (r) => r._measurement == "nexa" and ({FLOW_FILT}))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.onGridPower and exists r.pv1Voltage)
  |> map(fn: (r) => {{
      pv = {PV_EXPR}
      out = {OUT_EXPR}
      return {{ r with _value: {expr} }}
    }})
  |> integral(unit: 1h)
  |> map(fn: (r) => ({{ r with _value: r._value / 1000.0 }}))
  |> keep(columns: ["_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    PIE_FIELDS = ["ppv", "pac", "totalBatteryPackChargingPower", "totalBatteryPackChargingStatus"]

    def q_integral_today(expr, label):
        filt = " or ".join(f'r._field == "{f}"' for f in PIE_FIELDS)
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: today())
  |> filter(fn: (r) => r._measurement == "nexa" and ({filt}))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.ppv and exists r.pac and exists r.totalBatteryPackChargingPower)
  |> map(fn: (r) => {{
      p = math.abs(x: r.totalBatteryPackChargingPower)
      chg = if r.totalBatteryPackChargingStatus == "Charging" then p else 0.0
      dis = if r.totalBatteryPackChargingStatus == "Discharging" then p else 0.0
      return {{ r with _value: {expr} }}
    }})
  |> integral(unit: 1h)
  |> map(fn: (r) => ({{ r with _value: r._value / 1000.0 }}))
  |> keep(columns: ["_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    # ------------------------------------------------------------- Panel-Helfer
    ids = [0]

    def nid():
        ids[0] += 1
        return ids[0]

    def target(query, ref="A"):
        return {"datasource": DS, "query": query, "refId": ref}

    def panel(ptype, title, x, y, w, h, targets, unit=None, opts=None, defaults=None, overrides=None, desc=None):
        fc = {"defaults": {}, "overrides": overrides or []}
        if unit:
            fc["defaults"]["unit"] = unit
        if defaults:
            fc["defaults"].update(defaults)
        p = {"id": nid(), "type": ptype, "title": title, "gridPos": {"x": x, "y": y, "w": w, "h": h},
             "datasource": DS, "targets": targets, "fieldConfig": fc, "options": opts or {}}
        if desc:
            p["description"] = desc
        return p

    def row(title, y):
        return {"id": nid(), "type": "row", "title": title, "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}

    def thresholds(*steps):
        return {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in steps]}

    def stat(title, x, y, w, h, query, unit=None, color="blue", decimals=None, mapping=None, desc=None, thr=None, text_mode="value", extra_targets=None):
        d = {"color": {"mode": "fixed", "fixedColor": color}}
        if thr:
            d["color"] = {"mode": "thresholds"}; d["thresholds"] = thr
        if decimals is not None:
            d["decimals"] = decimals
        if mapping:
            d["mappings"] = mapping
        opts = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/^Value$/", "values": False},
                "colorMode": "value", "graphMode": "none", "textMode": text_mode, "justifyMode": "center"}
        return panel("stat", title, x, y, w, h, [target(query)] + (extra_targets or []), unit, opts=opts, defaults=d, desc=desc)

    def stat_field(title, x, y, w, h, field, unit=None, color="blue", decimals=None, mapping=None, desc=None, thr=None):
        return stat(title, x, y, w, h, q_last(field), unit, color, decimals, mapping, desc, thr)

    def ts(title, x, y, w, h, targets, unit, fill=10, stack=False, overrides=None, desc=None, mn=None, mx=None, bars=False, defaults_extra=None):
        custom = {"drawStyle": "bars" if bars else "line", "lineWidth": 2, "fillOpacity": fill, "gradientMode": "opacity", "showPoints": "never", "spanNulls": True,
                  "stacking": {"mode": "normal" if stack else "none", "group": "A"}}
        if bars:
            custom.update({"fillOpacity": 80, "lineWidth": 1, "barWidthFactor": 0.8})
        d = {"custom": custom}
        if mn is not None:
            d["min"] = mn
        if mx is not None:
            d["max"] = mx
        if defaults_extra:
            d.update(defaults_extra)
        return panel("timeseries", title, x, y, w, h, targets, unit,
                     opts={"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True, "calcs": ["mean", "max", "lastNotNull"]},
                           "tooltip": {"mode": "multi", "sort": "desc"}}, defaults=d, overrides=overrides, desc=desc)

    def color_override(name, color):
        return {"matcher": {"id": "byName", "options": name}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}]}

    def name_by_ref(ref, name):
        return {"matcher": {"id": "byFrameRefID", "options": ref}, "properties": [{"id": "displayName", "value": name}]}

    C_PV, C_HOUSE, C_BAT, C_SOC = "yellow", "orange", "green", "green"
    status_map = [{"type": "value", "options": {"Charging": {"text": _("Lädt"), "color": "green"}, "Discharging": {"text": _("Entlädt"), "color": "orange"}, "Idle": {"text": _("Ruhe"), "color": "blue"}}}]
    mode_map = [{"type": "value", "options": {"Load First": {"text": _("Last zuerst"), "color": "orange"}, "Battery First": {"text": _("Batterie zuerst"), "color": "green"}, "Smart Mode": {"text": _("Smart"), "color": "blue"}}}]
    temp_thr = thresholds((None, "blue"), (10, "green"), (35, "orange"), (45, "red"))

    panels = []
    y = 0

    # ============================================================ Jetzt
    panels.append(row(_("Jetzt"), y)); y += 1
    panels += [
        stat(_("PV-Leistung"), 0, y, 4, 5, q_flow_last("pv"), "watt", C_PV, desc=_("Summe Spannung × Strom aller Strings. Feiner und aktueller als das Geräteregister, das auf ganze Watt rundet.")),
        stat(_("Ausgang ins Haus"), 4, y, 4, 5, q_flow_last("out"), "watt", C_HOUSE, desc=_("AC-Ausgang aus Register 116 (0,1-W-Schritte, Offset 30000). Das Register pac (5) meldet auf aktueller Firmware dauerhaft 0.")),
        stat(_("Batterie-Leistung"), 8, y, 4, 5, q_flow_last("pv - out"), "watt", C_BAT, desc=_("Bilanz PV minus Ausgang: positiv = laden, negativ = entladen. Register 11 meldet auf aktueller Firmware dauerhaft 0.")),
        panel("gauge", _("Ladezustand"), 12, y, 6, 10, [target(q_last("totalBatteryPackSoc"))], "percent",
              opts={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/^Value$/", "values": False}, "showThresholdLabels": False, "showThresholdMarkers": True},
              defaults={"min": 0, "max": 100, "decimals": 0, "thresholds": thresholds((None, "red"), (20, "orange"), (50, "yellow"), (80, "green"))}),
        stat_field(_("Hausverbrauch"), 18, y, 6, 5, "totalHouseholdLoad", "watt", "red", desc=_("Nur mit Smart Meter / GroPlug befüllt, sonst 0")),
        stat_field(_("Batterie-Status"), 0, y + 5, 4, 5, "totalBatteryPackChargingStatus", None, "blue", mapping=status_map, desc=_("Statusregister 10 des Geräts. Auf aktueller Firmware oft „Ruhe“, obwohl die Bilanz Laden oder Entladen zeigt.")),
        stat_field(_("Betriebsmodus"), 4, y + 5, 4, 5, "workMode", None, "orange", mapping=mode_map),
        stat_field(_("Systemtemperatur"), 8, y + 5, 4, 5, "systemTemp", "celsius", None, 1, thr=thresholds((None, "blue"), (35, "green"), (50, "orange"), (60, "red"))),
        stat_field(_("Batterietemperatur"), 18, y + 5, 6, 5, "battery1Temp", "celsius", None, 1, thr=temp_thr),
    ]
    y += 10

    # ============================================================ Leistung
    panels.append(row(_("Leistung und Ladezustand"), y)); y += 1
    panels += [
        ts(_("Leistungsverlauf"), 0, y, 16, 10, [
            target(q_flow_series({_("PV"): "pv", _("Ins Haus"): "out", _("Batterie (+ laden / − entladen)"): "pv - out"}), "A"),
            target(q_series("ppv", _("PV (Register 7)")), "B"),
        ], "watt", overrides=[color_override(_("PV"), C_PV), color_override(_("PV (Register 7)"), "dark-yellow"), color_override(_("Ins Haus"), C_HOUSE), color_override(_("Batterie (+ laden / − entladen)"), C_BAT),
                              {"matcher": {"id": "byName", "options": _("PV (Register 7)")}, "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [6, 4]}}, {"id": "custom.lineWidth", "value": 1}]}],
           desc=_("PV aus Spannung × Strom der Strings, Ausgang aus Register 116, Batterie als Bilanz PV minus Ausgang (positiv = laden). Gestrichelt das gerundete PV-Register des Geräts.")),
        ts(_("Ladezustand"), 16, y, 8, 10, [target(q_series("totalBatteryPackSoc", "SoC"))], "percent", fill=25, overrides=[color_override("SoC", C_SOC)], mn=0, mx=100),
    ]
    y += 10

    # ============================================================ Energie
    panels.append(row(_("Energie"), y)); y += 1
    panels += [
        ts(_("PV-Ertrag pro Tag (30 Tage)"), 0, y, 12, 9, [target(q_flow_daily("pv", _("PV-Ertrag")))], "kwatth", bars=True,
           overrides=[color_override(_("PV-Ertrag"), C_PV)], desc=_("Aus den gemessenen Leistungswerten integriert, Tagesgrenzen lokale Zeit, Balken auf Tagesmitte. Zeitraum auf 30 Tage stellen, um alle Tage zu sehen.")),
        ts(_("Abgabe ins Haus pro Tag (30 Tage)"), 12, y, 12, 9, [target(q_flow_daily("out", _("Ins Haus")))], "kwatth", bars=True, overrides=[color_override(_("Ins Haus"), C_HOUSE)]),
    ]
    y += 9
    pie_opts = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "pieType": "donut", "displayLabels": ["percent"],
                "legend": {"displayMode": "table", "placement": "bottom", "showLegend": True, "values": ["value", "percent"]}}
    panels += [
        panel("piechart", _("Wohin ging der PV-Strom heute?"), 0, y, 6, 9, [
            target(q_flow_today("if pv - chg > 0.0 then pv - chg else 0.0", _("Direkt ins Haus")), "A"),
            target(q_flow_today("chg", _("In die Batterie")), "B")], "kwatth", opts=pie_opts, defaults={"decimals": 2},
              overrides=[color_override(_("Direkt ins Haus"), C_HOUSE), color_override(_("In die Batterie"), C_BAT)]),
        panel("piechart", _("Woher kam der Hausstrom heute?"), 6, y, 6, 9, [
            target(q_flow_today("if out - dis > 0.0 then out - dis else 0.0", _("Direkt aus PV")), "A"),
            target(q_flow_today("dis", _("Aus der Batterie")), "B")], "kwatth", opts=pie_opts, defaults={"decimals": 2},
              overrides=[color_override(_("Direkt aus PV"), C_PV), color_override(_("Aus der Batterie"), C_BAT)]),
        stat(_("PV heute"), 12, y, 3, 4, q_flow_range("pv", "Value", "today()"), "kwatth", C_PV, desc=_("Aus Messungen berechnet. Die Energiezähler-Register des Geräts (eacToday usw.) bleiben auf aktueller Firmware bei 0.")),
        stat(_("PV Monat"), 15, y, 3, 4, q_flow_range("pv", "Value", "date.truncate(t: now(), unit: 1mo)"), "kwatth", C_PV, desc=_("Aus Messungen berechnet, seit Monatsbeginn")),
        stat(_("PV Jahr"), 18, y, 3, 4, q_flow_range("pv", "Value", "date.truncate(t: now(), unit: 1y)"), "kwatth", C_PV, desc=_("Aus Messungen berechnet, seit Jahresbeginn")),
        stat(_("PV gesamt"), 21, y, 3, 4, q_flow_range("pv", "Value", "0"), "kwatth", C_PV, desc=_("Aus Messungen berechnet, seit Aufzeichnungsbeginn")),
        panel("stat", _("Heute aus Messwerten"), 12, y + 4, 12, 5, [
            target(q_flow_today("pv", _("PV-Ertrag")), "A"), target(q_flow_today("out", _("Ins Haus")), "B"),
            target(q_flow_today("chg", _("Batterie geladen")), "C"), target(q_flow_today("dis", _("Batterie entladen")), "D")], "kwatth",
              opts={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "value", "graphMode": "none", "textMode": "value_and_name", "justifyMode": "center"},
              defaults={"decimals": 2, "color": {"mode": "fixed", "fixedColor": "text"}},
              overrides=[color_override(_("PV-Ertrag"), C_PV), color_override(_("Ins Haus"), C_HOUSE), color_override(_("Batterie geladen"), C_BAT), color_override(_("Batterie entladen"), "orange")]),
    ]
    y += 9

    # ============================================================ Wetter
    panels.append(row(_("Wetter"), y)); y += 1
    def q_weather_last(field, rng="-2h"):
        return f'''from(bucket: "{BUCKET}")
  |> range(start: {rng})
  |> filter(fn: (r) => r._measurement == "weather" and r._field == "{field}")
  |> last()
  |> keep(columns: ["_time", "_value"])'''
    def q_weather_series(field, label, measurement="weather"):
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "{field}")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
  |> rename(columns: {{_value: "{label}"}})'''
    cond_map = [{"type": "value", "options": {str(k): {"text": _(v)} for k, v in {0: "Klar", 1: "Überwiegend klar", 2: "Teilweise bewölkt", 3: "Bedeckt", 45: "Nebel", 48: "Reifnebel",
                 51: "Sprühregen", 53: "Sprühregen", 55: "Sprühregen", 61: "Leichter Regen", 63: "Regen", 65: "Starker Regen", 71: "Schneefall", 73: "Schneefall", 75: "Schneefall",
                 80: "Regenschauer", 81: "Regenschauer", 82: "Regenschauer", 95: "Gewitter", 96: "Gewitter", 99: "Gewitter"}.items()}}]
    panels += [
        stat(_("Außentemperatur"), 0, y, 4, 4, q_weather_last("temperature"), "celsius", None, 1, thr=thresholds((None, "blue"), (5, "light-blue"), (15, "green"), (25, "orange"), (30, "red")),
             desc=_("Open-Meteo für den Anlagenstandort, alle 10 Minuten")),
        stat(_("Wetterzustand"), 4, y, 4, 4, q_weather_last("weather_code"), None, "text", mapping=cond_map, desc=_("WMO-Wettercode von Open-Meteo")),
        stat(_("Bewölkung"), 8, y, 4, 4, q_weather_last("cloud_cover"), "percent", None, 0, thr=thresholds((None, "yellow"), (40, "light-yellow"), (70, "blue"), (90, "dark-blue"))),
        stat(_("Globalstrahlung"), 12, y, 4, 4, q_weather_last("shortwave_radiation"), "suffix: W/m²", C_PV, 0, desc=_("Kurzwellige Einstrahlung auf die Horizontale in W/m², Referenz für die PV-Leistung")),
        panel("stat", _("Sonnenaufgang / -untergang"), 16, y, 4, 4, [target(q_last_named("sunrise", "↑", "weather", "-2h"), "A"), target(q_last_named("sunset", "↓", "weather", "-2h"), "B")], None,
              opts={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/.*/", "values": False}, "colorMode": "none", "graphMode": "none", "textMode": "value_and_name", "justifyMode": "center"},
              defaults={"color": {"mode": "fixed", "fixedColor": "text"}}, desc=_("Heute, lokale Zeit")),
        stat(_("Sonnenschein heute"), 20, y, 4, 4, q_weather_last("sunshine_hours_today"), "suffix: h", C_PV, 1, desc=_("Prognostizierte Sonnenscheindauer des Tages")),
    ]
    y += 4
    panels += [
        panel("stat", _("Strahlungssumme heute / morgen"), 0, y, 4, 9, [target(q_last_named("radiation_sum_today", _("heute"), "weather", "-2h"), "A"), target(q_last_named("radiation_sum_tomorrow", _("morgen"), "weather", "-2h"), "B")], None,
              opts={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "value", "graphMode": "none", "textMode": "value_and_name", "justifyMode": "center", "orientation": "vertical"},
              defaults={"decimals": 1, "unit": "suffix: MJ/m²", "color": {"mode": "fixed", "fixedColor": C_PV}}, desc=_("Tagessumme der Globalstrahlung in MJ/m² laut Vorhersage (1 MJ/m² ≈ 0,28 kWh/m²)")),
        panel("timeseries", _("Globalstrahlung und PV-Leistung"), 4, y, 10, 9, [
            target(q_weather_series("shortwave_radiation", _("Globalstrahlung W/m²")), "A"),
            target(q_flow_series({_("PV-Leistung W"): "pv"}), "B")], None,
              opts={"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True, "calcs": ["mean", "max"]}, "tooltip": {"mode": "multi", "sort": "none"}},
              defaults={"custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 8, "gradientMode": "opacity", "showPoints": "never", "spanNulls": True}},
              overrides=[color_override(_("Globalstrahlung W/m²"), "orange"),
                         {"matcher": {"id": "byName", "options": _("PV-Leistung W")}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": C_PV}}, {"id": "custom.axisPlacement", "value": "right"}, {"id": "unit", "value": "watt"}]}],
              desc=_("Verhältnis von PV-Leistung zu Einstrahlung zeigt Verschattung, Ausrichtung und Verschmutzung. Strahlung links (W/m²), PV rechts (W).")),
        ts(_("Bewölkung und Temperatur"), 14, y, 10, 9, [target(q_weather_series("cloud_cover", _("Bewölkung %")), "A"), target(q_weather_series("temperature", _("Temperatur °C")), "B")], None, fill=5,
           overrides=[color_override(_("Bewölkung %"), "blue"), {"matcher": {"id": "byName", "options": _("Temperatur °C")}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}, {"id": "custom.axisPlacement", "value": "right"}, {"id": "unit", "value": "celsius"}]}]),
    ]
    y += 9
    panels += [
        panel("barchart", _("Vorhersage 48 h: Einstrahlung und Bewölkung"), 0, y, 24, 8, [target(HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: -1h, stop: 48h)
  |> filter(fn: (r) => r._measurement == "weather_forecast" and (r._field == "shortwave_radiation" or r._field == "cloud_cover"))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> map(fn: (r) => {{
      wd = date.weekDay(t: r._time, location: location)
      names = ["{_("So")}", "{_("Mo")}", "{_("Di")}", "{_("Mi")}", "{_("Do")}", "{_("Fr")}", "{_("Sa")}"]
      h = date.hour(t: r._time, location: location)
      return {{ _time: r._time, "{_("Stunde")}": names[wd] + " " + (if h < 10 then "0" else "") + string(v: h) + ":00", "{_("Globalstrahlung W/m²")}": r.shortwave_radiation, "{_("Bewölkung %")}": r.cloud_cover }}
    }})
  |> keep(columns: ["{_("Stunde")}", "{_("Globalstrahlung W/m²")}", "{_("Bewölkung %")}"])''')], None,
              opts={"xField": _("Stunde"), "orientation": "auto", "barWidth": 0.8, "groupWidth": 0.7, "showValue": "never", "stacking": "none",
                    "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}, "tooltip": {"mode": "multi", "sort": "none"}, "xTickLabelRotation": -45, "xTickLabelSpacing": 100},
              defaults={"color": {"mode": "palette-classic"}, "custom": {"fillOpacity": 80, "lineWidth": 1}},
              overrides=[color_override(_("Globalstrahlung W/m²"), "orange"), color_override(_("Bewölkung %"), "blue")],
              desc=_("Stündliche Open-Meteo-Vorhersage ab jetzt. Zeitraum des Dashboards ist hier ohne Wirkung, das Panel zeigt immer die nächsten 48 Stunden.")),
    ]
    y += 8

    # ============================================================ PV-Strings
    panels.append(row(_("PV-Strings"), y)); y += 1
    sf = [f"pv{i}{s}" for i in range(1, 5) for s in ("Voltage", "Current")]
    S = _("String")
    panels += [
        ts(_("Leistung je String"), 0, y, 12, 9, [target(q_pivot_map(sf, {f"{S} {i}": f"r.pv{i}Voltage * r.pv{i}Current" for i in range(1, 5)}))], "watt", fill=5, stack=True,
           desc=_("Leistung = Spannung × Strom je Eingang")),
        ts(_("Spannung je String"), 12, y, 6, 9, [target(q_pivot_map(sf, {f"{S} {i}": f"r.pv{i}Voltage" for i in range(1, 5)}))], "volt", fill=0),
        ts(_("Strom je String"), 18, y, 6, 9, [target(q_pivot_map(sf, {f"{S} {i}": f"r.pv{i}Current" for i in range(1, 5)}))], "amp", fill=0),
    ]
    y += 9
    panels += [
        stat(_("PV-Eingänge belegt"), 0, y, 4, 4, q_pv_detected(_("belegt")), None, "yellow", 0, desc=_("Eingänge mit mehr als 15 V in den letzten 24 h, von 4 MPPT-Eingängen. Freie Eingänge zeigen etwa 7 V.")),
        panel("stat", _("PV-Eingang 1 bis 4"), 4, y, 20, 4, [target(f'''from(bucket: "{BUCKET}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "nexa" and r._field == "pv{i}Voltage")
  |> max()
  |> keep(columns: ["_value"])
  |> rename(columns: {{_value: "{S} {i}"}})''', r) for i, r in zip(range(1, 5), "ABCD")], "volt",
              opts={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "background", "graphMode": "none", "textMode": "value_and_name", "justifyMode": "center"},
              defaults={"decimals": 1, "color": {"mode": "thresholds"}, "thresholds": thresholds((None, "dark-red"), (15, "green"))},
              desc=_("Maximale Spannung je Eingang in 24 h. Grün = Panel angeschlossen (> 15 V), rot = frei.")),
    ]
    y += 4

    # ============================================================ Batterie und Technik
    panels.append(row(_("Batterie und Technik"), y)); y += 1
    B = _("Batterie")
    panels += [
        ts(_("Temperaturen"), 0, y, 12, 9, [
            target(q_series("systemTemp", _("System")), "A"),
            target(q_series("battery1Temp", f"{B} 1"), "B"), target(q_series("battery2Temp", f"{B} 2"), "G"),
            target(q_series("battery3Temp", f"{B} 3"), "H"), target(q_series("battery4Temp", f"{B} 4"), "I"),
            target(q_series("pv1Temp", "PV1"), "C"), target(q_series("pv2Temp", "PV2"), "D"), target(q_series("pv3Temp", "PV3"), "E"), target(q_series("pv4Temp", "PV4"), "F"),
        ], "celsius", fill=0),
        ts(_("Zellspannung (min / max)"), 12, y, 8, 9, [target(q_series("maxCellVoltage", "max"), "A"), target(q_series("minCellVoltage", "min"), "B")], "volt", fill=0,
           desc=_("Ein großer Abstand deutet auf unausgeglichene Zellen hin")),
        stat_field(_("Zyklen"), 20, y, 4, 3, "batteryCycles", None, "blue", 0),
        stat_field(_("Gesundheit (SoH)"), 20, y + 3, 4, 3, "batterySoh", "percent", None, 0, thr=thresholds((None, "red"), (80, "orange"), (90, "green"))),
        stat_field(_("Batteriepacks"), 20, y + 6, 4, 3, "batteryPackageQuantity", None, "blue", 0),
    ]
    y += 9
    panels += [
        stat_field(_("Entlade-Grenze"), 0, y, 4, 4, "dischargeSocLimit", "percent", "orange", 0, desc=_("Unter diesen SoC entlädt die Batterie nicht")),
        stat_field(_("Lade-Grenze"), 4, y, 4, 4, "chargeSocLimit", "percent", "green", 0),
        stat_field(_("Netzspannung"), 8, y, 4, 4, "onGridVoltage", "volt", "purple", 1, desc=_("Register 115. Nahe 0 V, wenn der NEXA vom Netz getrennt ist.")),
        panel("stat", _("Geräteregister zum Vergleich"), 12, y, 4, 4, [target(q_last_named("pac", "pac (5)"), "A"), target(q_last_named("totalBatteryPackChargingPower", _("Batterie") + " (11)"), "B"), target(q_last_named("ppv", "ppv (7)"), "C")], "watt",
              opts={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "none", "graphMode": "none", "textMode": "value_and_name", "justifyMode": "center"},
              defaults={"color": {"mode": "fixed", "fixedColor": "purple"}}, desc=_("Rohwerte der Geräteregister. pac und Batterieleistung bleiben auf aktueller Firmware bei 0, deshalb rechnet das Dashboard mit Register 116 und den Strings.")),
        stat(_("Zellspannung Differenz"), 16, y, 4, 4, HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "nexa" and (r._field == "maxCellVoltage" or r._field == "minCellVoltage"))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.maxCellVoltage and exists r.minCellVoltage)
  |> last(column: "maxCellVoltage")
  |> map(fn: (r) => ({{ _time: r._time, _value: r.maxCellVoltage - r.minCellVoltage }}))
  |> keep(columns: ["_time", "_value"])''', "volt", None, 3, thr=thresholds((None, "green"), (0.05, "orange"), (0.1, "red"))),
        stat(_("Letzte Nachricht"), 20, y, 4, 4, f'''from(bucket: "{BUCKET}")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "nexa" and r._field == "ppv")
  |> last()
  |> map(fn: (r) => ({{ _time: r._time, _value: float(v: uint(v: r._time)) / 1000000.0 }}))
  |> keep(columns: ["_time", "_value"])''', "dateTimeFromNow", "text", desc=_("Zeitpunkt des letzten Datensatzes vom Dongle")),
    ]
    y += 4
    panels += [
        stat(_("Firmware NEXA (Reg. 119/120)"), 0, y, 6, 4, HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "nexa" and (r._field == "fw_version_part_1" or r._field == "fw_version_part_2" or r._field == "fw_version_part_3" or r._field == "fw_version_part_4"))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.fw_version_part_1 and exists r.fw_version_part_2 and exists r.fw_version_part_3 and exists r.fw_version_part_4)
  |> last(column: "fw_version_part_1")
  |> map(fn: (r) => ({{ _time: r._time, _value: string(v: int(v: r.fw_version_part_1)) + "." + string(v: int(v: r.fw_version_part_2)) + "." + string(v: int(v: r.fw_version_part_3)) + "." + string(v: int(v: r.fw_version_part_4)) }}))
  |> keep(columns: ["_time", "_value"])''', None, "text", desc=_("Rohteile aus den Registern, Zusammensetzung laut Growatt unbekannt")),
        stat(_("Dongle Firmware / Modell"), 6, y, 6, 4, f'''from(bucket: "{BUCKET}")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "dongle" and r._field == "wifi_signal")
  |> last()
  |> map(fn: (r) => ({{ _time: r._time, _value: r.model_id + "  SW " + r.sw_version + "  HW " + r.hw_version }}))
  |> keep(columns: ["_time", "_value"])''', None, "text"),
        stat(_("WLAN-Signal Dongle"), 12, y, 4, 4, q_last("wifi_signal", "dongle", "-30d"), "dBm", None, 0, thr=thresholds((None, "red"), (-80, "orange"), (-67, "green")),
             desc=_("Wird nur beim Verbindungsaufbau des Dongles gemeldet")),
        stat_field(_("Batteriepacks erkannt"), 16, y, 4, 4, "batteryPackageQuantity", None, "blue", 0),
        stat_field(_("Zellspannung min"), 20, y, 4, 4, "minCellVoltage", "volt", "green", 3),
    ]
    y += 4
    P = _("Pack")
    panels += [
        ts(_("SoC je Batteriepack"), 0, y, 12, 8, [target(q_series(f"battery{i}Soc", f"{P} {i}"), r) for i, r in zip(range(1, 5), "ABCD")], "percent", fill=5, mn=0, mx=100),
        panel("stat", _("Temperatur je Pack"), 12, y, 12, 4, [target(q_last_named(f"battery{i}Temp", f"{P} {i}"), r) for i, r in zip(range(1, 5), "ABCD")], "celsius",
              opts={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "value", "graphMode": "none", "textMode": "value_and_name", "justifyMode": "center"},
              defaults={"decimals": 1, "color": {"mode": "thresholds"}, "thresholds": temp_thr}),
        panel("stat", _("Seriennummern Erweiterungspacks"), 12, y + 4, 12, 4, [target(q_last_named(f"bat{i}_serial", f"{P} {i}", rng="-7d"), r) for i, r in zip(range(2, 5), "ABC")], None,
              opts={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/.*/", "values": False}, "colorMode": "none", "graphMode": "none", "textMode": "value_and_name", "justifyMode": "center"},
              defaults={"color": {"mode": "fixed", "fixedColor": "text"}},
              desc=_("Pack 1 ist das Hauptgerät mit der Geräteseriennummer. Leer = kein Erweiterungspack.")),
    ]
    y += 8

    # ============================================================ Forschung
    panels.append(row(_("Unbekannte Register (Forschung)"), y)); y += 1
    panels += [
        ts(_("Unbekannte Eingangsregister, Verlauf (nur ≠ 0)"), 0, y, 16, 10, [target(HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "raw" and r.kind == "raw_input")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> filter(fn: (r) => r._value != 0.0)
  |> keep(columns: ["_time", "_value", "_field"])''')], None, fill=0, desc=_("Rohwerte der Register, die die Bridge nicht kennt. 30000 = Offset für 0 bei vorzeichenbehafteten Werten."),
           defaults_extra={"displayName": "${__field.labels._field}"}),
        panel("table", _("Halteregister-Dump (unbekannt, ≠ 0)"), 16, y, 8, 10, [target(f'''from(bucket: "{BUCKET}")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "raw" and r.kind == "raw_holding")
  |> last()
  |> filter(fn: (r) => r._value != 0)
  |> keep(columns: ["_field", "_value"])
  |> rename(columns: {{_field: "{_("Register")}", _value: "{_("Wert")}"}})''')], None,
              opts={"showHeader": True, "sortBy": [{"displayName": _("Register"), "desc": False}]}, desc=_("Kommt stündlich vom Gerät (Nachricht 0x0103).")),
    ]

    return {
        "uid": "nexa2000" if lang == "en" else "nexa2000-de",
        "title": "GroLo · Growatt NEXA 2000" + (" (DE)" if lang == "de" else ""),
        "tags": ["solar", "growatt", "nexa", "grolo", lang],
        "timezone": "browser", "editable": True, "graphTooltip": 1, "refresh": "30s",
        "time": {"from": "now-24h", "to": "now"}, "timepicker": {"refresh_intervals": ["10s", "30s", "1m", "5m", "15m"]},
        "links": [
            {"title": _("Einstellungen"), "type": "link", "url": "http://${__url.params:hostname}:8080/", "icon": "external link", "tooltip": _("Einstellungsseite (GroLo)"), "targetBlank": True, "asDropdown": False},
            {"title": "Deutsch" if lang == "en" else "English", "type": "link", "url": "/d/nexa2000-de" if lang == "en" else "/d/nexa2000", "icon": "external link", "tooltip": "", "targetBlank": False, "asDropdown": False, "keepTime": True},
        ],
        "schemaVersion": 39, "version": 1, "panels": panels, "templating": {"list": []}, "annotations": {"list": []},
    }


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    old = os.path.join(OUT_DIR, "nexa.json")
    if os.path.exists(old):
        os.remove(old)
    for lang in ("en", "de"):
        d = build(lang)
        out = os.path.join(OUT_DIR, f"nexa-{lang}.json")
        with open(out, "w") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
        print(f"{out}: {len(d['panels'])} Panels")
