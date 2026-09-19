#!/usr/bin/env python3
"""wolf-dashboard: erzeugt die Grafana-Dashboards der Wärmepumpe in Deutsch und Englisch.

Beide Sprachen kommen aus einer Beschreibung, damit sie nicht auseinanderlaufen. Die Klartexte der
Auswahlparameter (Betriebsart, Verdichterstatus, …) werden als Value-Mappings aus wolf/catalog.json
übernommen – deutsch im deutschen Dashboard, englisch im englischen, jeweils Wolfs eigene Wortwahl.

Aufruf:  scripts/wolf-dashboard.py
Schreibt grafana/dashboards/wolf-de.json und wolf-en.json.
"""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DS = {"type": "influxdb", "uid": "influx-nexa"}
BUCKET = "nexa"
TZ = "Europe/Berlin"


def L(de, en):
    return {"de": de, "en": en}


class Builder:
    def __init__(self, lang, catalog):
        self.lang = lang
        self.catalog = catalog
        self.panels = []
        self.id = 0
        self.y = 0
        self.x = 0

    # -------------------------------------------------------------- Hilfen
    def t(self, label):
        return label[self.lang] if isinstance(label, dict) else label

    def mappings(self, device, key):
        """Value-Mappings aus dem Katalog: Zahl -> Wolfs Klartext in der jeweiligen Sprache."""
        for dev in self.catalog["devices"]:
            if dev["role"] != device:
                continue
            for p in dev["params"]:
                if p["key"] == key and p.get("options"):
                    return [{"type": "value",
                             "options": {o["v"]: {"text": o[self.lang], "index": i}
                                         for i, o in enumerate(p["options"])}}]
        return []

    def row(self, title):
        self.id += 1
        if self.x:
            self.y += 1
            self.x = 0
        self.panels.append({"id": self.id, "type": "row", "title": self.t(title),
                            "collapsed": False, "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1},
                            "panels": []})
        self.y += 1
        self.x = 0

    def add(self, panel, w, h):
        if self.x + w > 24:
            self.x = 0
            self.y += h
        self.id += 1
        panel["id"] = self.id
        panel["gridPos"] = {"x": self.x, "y": self.y, "w": w, "h": h}
        panel.setdefault("datasource", DS)
        for target in panel.get("targets", []):
            target.setdefault("datasource", DS)
        self.panels.append(panel)
        self.x += w
        if self.x >= 24:
            self.x = 0
            self.y += h

    # -------------------------------------------------------------- Bausteine
    def stat(self, title, query, w=3, h=4, unit=None, decimals=None, color=None, mappings=None,
             desc=None, graph="none", thresholds=None, text_mode="value"):
        defaults = {}
        if unit:
            defaults["unit"] = unit
        if decimals is not None:
            defaults["decimals"] = decimals
        if color:
            defaults["color"] = {"mode": "fixed", "fixedColor": color}
        if mappings:
            defaults["mappings"] = mappings
        if thresholds:
            defaults["thresholds"] = {"mode": "absolute", "steps": thresholds}
            defaults["color"] = {"mode": "thresholds"}
        panel = {
            "type": "stat", "title": self.t(title),
            "targets": [{"query": query, "refId": "A"}],
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/^Value$/", "values": False},
                        "colorMode": "value", "graphMode": graph, "textMode": text_mode,
                        "justifyMode": "center"},
        }
        if desc:
            panel["description"] = self.t(desc)
        self.add(panel, w, h)

    def gauge(self, title, query, w=4, h=8, unit="celsius", mn=None, mx=None, steps=None, desc=None):
        defaults = {"unit": unit}
        if mn is not None:
            defaults["min"] = mn
        if mx is not None:
            defaults["max"] = mx
        if steps:
            defaults["thresholds"] = {"mode": "absolute", "steps": steps}
        panel = {"type": "gauge", "title": self.t(title),
                 "targets": [{"query": query, "refId": "A"}],
                 "fieldConfig": {"defaults": defaults, "overrides": []},
                 "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/^Value$/", "values": False},
                             "showThresholdLabels": False, "showThresholdMarkers": True}}
        if desc:
            panel["description"] = self.t(desc)
        self.add(panel, w, h)

    def series(self, title, targets, w=12, h=9, unit=None, desc=None, overrides=None,
               fill=8, stack=False, style="line", min_=None, max_=None, decimals=None):
        defaults = {
            "custom": {"drawStyle": style, "lineWidth": 1.6, "fillOpacity": fill,
                       "showPoints": "never", "spanNulls": True,
                       "stacking": {"mode": "normal" if stack else "none"},
                       "lineInterpolation": "smooth" if style == "line" else "linear"},
        }
        if unit:
            defaults["unit"] = unit
        if min_ is not None:
            defaults["min"] = min_
        if max_ is not None:
            defaults["max"] = max_
        if decimals is not None:
            defaults["decimals"] = decimals
        panel = {"type": "timeseries", "title": self.t(title),
                 "targets": [{"query": q, "refId": chr(65 + i)} for i, q in enumerate(targets)],
                 "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
                 "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True,
                                        "calcs": []},
                             "tooltip": {"mode": "multi", "sort": "none"}}}
        if desc:
            panel["description"] = self.t(desc)
        self.add(panel, w, h)

    def timeline(self, title, query, mappings, w=24, h=6, desc=None):
        panel = {"type": "state-timeline", "title": self.t(title),
                 "targets": [{"query": query, "refId": "A"}],
                 "fieldConfig": {"defaults": {"mappings": mappings, "color": {"mode": "palette-classic"},
                                              "custom": {"fillOpacity": 80, "lineWidth": 0}},
                                 "overrides": []},
                 "options": {"showValue": "auto", "mergeValues": True, "alignValue": "center",
                             "rowHeight": 0.9, "legend": {"displayMode": "list", "placement": "bottom",
                                                          "showLegend": True}}}
        if desc:
            panel["description"] = self.t(desc)
        self.add(panel, w, h)

    def bars(self, title, query, w=12, h=8, unit=None, desc=None, stack=False, overrides=None):
        defaults = {"custom": {"lineWidth": 1, "fillOpacity": 80,
                               "stacking": {"mode": "normal" if stack else "none"}}}
        if unit:
            defaults["unit"] = unit
        panel = {"type": "barchart", "title": self.t(title),
                 "targets": [{"query": query, "refId": "A"}],
                 "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
                 "options": {"xTickLabelRotation": 0, "showValue": "auto", "stacking": "normal" if stack else "none",
                             "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                             "tooltip": {"mode": "multi"}}}
        if desc:
            panel["description"] = self.t(desc)
        self.add(panel, w, h)

    def table(self, title, query, w=12, h=9, desc=None, overrides=None):
        panel = {"type": "table", "title": self.t(title),
                 "targets": [{"query": query, "refId": "A", "format": "table"}],
                 "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": overrides or []},
                 "options": {"showHeader": True, "cellHeight": "sm",
                             "footer": {"show": False, "reducer": ["sum"], "countRows": False, "fields": ""}},
                 "transformations": [{"id": "organize", "options": {"excludeByName": {"_start": True, "_stop": True,
                                                                                      "result": True, "table": True}}}]}
        if desc:
            panel["description"] = self.t(desc)
        self.add(panel, w, h)

    def text(self, title, body, w=24, h=4):
        self.add({"type": "text", "title": self.t(title),
                  "options": {"mode": "markdown", "content": self.t(body)}}, w, h)


# ------------------------------------------------------------------ Flux-Bausteine
def last(field, device="heatpump", measurement="wolf", scale=None, window="-2h"):
    """Letzter Wert eines Feldes."""
    dev = f' and r.device == "{device}"' if measurement == "wolf" else ""
    q = (f'from(bucket: "{BUCKET}")\n  |> range(start: {window})\n'
         f'  |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "{field}"{dev})\n'
         f'  |> last()')
    if scale:
        q += f'\n  |> map(fn: (r) => ({{ r with _value: r._value * {scale} }}))'
    return q + '\n  |> keep(columns: ["_time", "_value"])'


def series(field, name, device="heatpump", measurement="wolf", every="$__interval", fn="mean", scale=None):
    """Zeitreihe eines Feldes mit sprechendem Namen."""
    dev = f' and r.device == "{device}"' if measurement == "wolf" else ""
    q = (f'from(bucket: "{BUCKET}")\n  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
         f'  |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "{field}"{dev})\n'
         f'  |> aggregateWindow(every: {every}, fn: {fn}, createEmpty: false)')
    if scale:
        q += f'\n  |> map(fn: (r) => ({{ r with _value: r._value * {scale} }}))'
    return q + f'\n  |> keep(columns: ["_time", "_value"])\n  |> set(key: "_field", value: "{name}")'


def cop_now():
    """Momentaner COP: Sekundärleistung geteilt durch Leistungsaufnahme, nur bei laufendem Verdichter."""
    return f'''from(bucket: "{BUCKET}")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "wolf" and r.device == "heatpump" and (r._field == "aktuelle_sekundaerleistung" or r._field == "leistungsaufnahme_wp_ehz" or r._field == "verdichter"))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.aktuelle_sekundaerleistung and exists r.leistungsaufnahme_wp_ehz and exists r.verdichter)
  |> filter(fn: (r) => r.verdichter == 1.0 and r.leistungsaufnahme_wp_ehz > 0.2)
  |> last(column: "aktuelle_sekundaerleistung")
  |> map(fn: (r) => ({{ _time: r._time, _value: r.aktuelle_sekundaerleistung / r.leistungsaufnahme_wp_ehz }}))
  |> keep(columns: ["_time", "_value"])'''


def cop_series(name):
    return f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "wolf" and r.device == "heatpump" and (r._field == "aktuelle_sekundaerleistung" or r._field == "leistungsaufnahme_wp_ehz" or r._field == "verdichter"))
  |> aggregateWindow(every: $__interval, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.aktuelle_sekundaerleistung and exists r.leistungsaufnahme_wp_ehz and exists r.verdichter)
  |> filter(fn: (r) => r.verdichter > 0.5 and r.leistungsaufnahme_wp_ehz > 0.2)
  |> map(fn: (r) => ({{ _time: r._time, _value: r.aktuelle_sekundaerleistung / r.leistungsaufnahme_wp_ehz }}))
  |> keep(columns: ["_time", "_value"])
  |> set(key: "_field", value: "{name}")'''


def daily(field, name, fn="max"):
    """Tageswert aus einem Zähler, der um Mitternacht auf null springt: das Tagesmaximum."""
    return f'''import "timezone"
option location = timezone.location(name: "{TZ}")
from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "wolf" and r.device == "heatpump" and r._field == "{field}")
  |> aggregateWindow(every: 1d, fn: {fn}, createEmpty: false, timeSrc: "_start")
  |> keep(columns: ["_time", "_value"])
  |> set(key: "_field", value: "{name}")'''


def daily_delta(field, name):
    """Tageswert aus einem stetig wachsenden Zähler: Differenz der Tagesmaxima."""
    return f'''import "timezone"
option location = timezone.location(name: "{TZ}")
from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "wolf" and r.device == "heatpump" and r._field == "{field}")
  |> aggregateWindow(every: 1d, fn: max, createEmpty: false, timeSrc: "_start")
  |> difference(nonNegative: true)
  |> keep(columns: ["_time", "_value"])
  |> set(key: "_field", value: "{name}")'''


def build(lang, catalog):
    b = Builder(lang, catalog)
    de = lang == "de"

    mode_map = b.mappings("heatpump", "betriebsart_heizgeraet")
    comp_map = b.mappings("heatpump", "verdichterstatus")
    onoff = b.mappings("heatpump", "verdichter")
    valve = b.mappings("heatpump", "3_wege_umschaltventil_hz_ww")
    pv_map = b.mappings("heatpump", "status_pv")

    # ---------------------------------------------------------------- Jetzt
    b.row(L("Jetzt", "Now"))
    b.stat(L("Betriebsart", "Operating mode"), last("betriebsart_heizgeraet"), w=4, h=5,
           mappings=mode_map, color="blue", text_mode="value",
           desc=L("Was die Wärmepumpe gerade tut: Heizen, Warmwasser, Abtauen, Kühlen oder Standby.",
                  "What the heat pump is doing right now: heating, hot water, defrost, cooling or standby."))
    b.stat(L("Verdichter", "Compressor"), last("verdichterstatus"), w=4, h=5, mappings=comp_map, color="purple",
           desc=L("Zustand des Verdichters. „Sperrzeit“ und „EVU Sperre“ sind normale Wartezustände, keine Störung.",
                  "Compressor state. “Blocking time” and “Power-OFF” are normal waiting states, not faults."))
    b.stat(L("Verdichterfrequenz", "Compressor frequency"), last("verdichterfrequenz"), w=3, h=5,
           unit="rothz", decimals=0, color="purple",
           desc=L("Drehzahl des Inverters. Niedrige Frequenz über lange Zeit ist der effizienteste Betrieb.",
                  "Inverter speed. A low frequency over a long time is the most efficient way to run."))
    b.stat(L("Wärmeleistung", "Heat output"), last("aktuelle_sekundaerleistung"), w=3, h=5,
           unit="kwatt", decimals=1, color="orange")
    b.stat(L("Leistungsaufnahme", "Power input"), last("leistungsaufnahme_wp_ehz"), w=3, h=5,
           unit="kwatt", decimals=1, color="red",
           desc=L("Strom für Verdichter und Elektroheizstab zusammen, wie die Wolf ihn meldet (ganze kW).",
                  "Electricity for compressor and immersion heater together, as the Wolf reports it (whole kW)."))
    b.stat(L("COP jetzt", "COP now"), cop_now(), w=3, h=5, decimals=2,
           thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 2.5},
                       {"color": "yellow", "value": 3.5}, {"color": "green", "value": 4.5}],
           desc=L("Wärmeleistung geteilt durch Leistungsaufnahme, nur bei laufendem Verdichter. "
                  "Die Wolf meldet die Aufnahme in ganzen kW, der Wert ist daher grob gestuft.",
                  "Heat output divided by power input, only while the compressor runs. The Wolf reports "
                  "the input in whole kW, so the value is coarse."))
    b.stat(L("Außentemperatur", "Outside temperature"), last("aussentemperatur"), w=4, h=5,
           unit="celsius", decimals=1, color="blue")

    b.gauge(L("Warmwasser", "Hot water"), last("warmwassertemperatur"), w=4, h=9, unit="celsius",
            mn=20, mx=65, steps=[{"color": "blue", "value": None}, {"color": "orange", "value": 40},
                                 {"color": "green", "value": 48}],
            desc=L("Speichertemperatur. Der Sollwert steht daneben.", "Cylinder temperature. The setpoint is next to it."))
    b.stat(L("Warmwasser-Soll", "Hot water setpoint"), last("warmwassersolltemperatur"), w=3, h=4,
           unit="celsius", decimals=0, color="text")
    b.stat(L("Vorlauf", "Flow"), last("kesseltemperatur"), w=3, h=4, unit="celsius", decimals=1, color="orange")
    b.stat(L("Rücklauf", "Return"), last("ruecklauftemperatur"), w=3, h=4, unit="celsius", decimals=1, color="yellow")
    b.stat(L("Sammler", "Header"), last("sammlertemperatur"), w=3, h=4, unit="celsius", decimals=1, color="text")
    b.stat(L("Spreizung", "Spread"), last("spreizung", measurement="wolf_derived"), w=4, h=4,
           unit="celsius", decimals=1, color="semi-dark-orange",
           desc=L("Vorlauf minus Rücklauf. Die Regelung hält sie auf der eingestellten Soll-Spreizung; "
                  "eine dauerhaft zu kleine Spreizung heißt zu viel Pumpenleistung.",
                  "Flow minus return. The control holds it at the configured target spread; a permanently "
                  "small spread means the pump runs harder than needed."))
    b.stat(L("Durchfluss", "Flow rate"), last("heizkreisdurchfluss"), w=3, h=4, unit="lpm", decimals=1, color="blue")
    b.stat(L("Anlagendruck", "System pressure"), last("anlagendruck"), w=3, h=4, unit="pressurebar", decimals=1,
           thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 1.0},
                       {"color": "green", "value": 1.3}, {"color": "orange", "value": 2.5}],
           desc=L("Unter etwa 1 bar sollte nachgefüllt werden.", "Below roughly 1 bar the system should be topped up."))
    b.stat(L("3-Wege-Ventil", "3-way valve"), last("3_wege_umschaltventil_hz_ww"), w=3, h=4, mappings=valve,
           color="text")
    b.stat(L("Elektroheizstab", "Immersion heater"), last("e_heizung"), w=3, h=4, mappings=onoff,
           thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}],
           desc=L("Direktstrom statt Wärmepumpe. Jede Stunde hier kostet dreimal so viel wie Verdichterbetrieb.",
                  "Direct electric heat instead of the heat pump. Every hour here costs about three times "
                  "as much as running the compressor."))
    b.stat(L("PV-Status", "PV status"), last("status_pv"), w=3, h=4, mappings=pv_map, color="yellow",
           desc=L("Zustand der SG/PV-Schnittstelle der Wärmepumpe.", "State of the heat pump's SG/PV interface."))

    # ---------------------------------------------------------------- Verlauf
    b.row(L("Verlauf", "History"))
    b.series(L("Temperaturen", "Temperatures"), [
        series("kesseltemperatur", "Vorlauf" if de else "Flow"),
        series("ruecklauftemperatur", "Rücklauf" if de else "Return"),
        series("sammlertemperatur", "Sammler" if de else "Header"),
        series("warmwassertemperatur", "Warmwasser" if de else "Hot water"),
        series("aussentemperatur", "Außen" if de else "Outside"),
    ], w=12, h=9, unit="celsius", fill=0,
        desc=L("Vorlauf und Rücklauf laufen im Betrieb parallel, ihr Abstand ist die Spreizung.",
               "Flow and return run in parallel while operating; the gap between them is the spread."))

    b.series(L("Leistung und COP", "Power and COP"), [
        series("aktuelle_sekundaerleistung", "Wärme kW" if de else "Heat kW"),
        series("leistungsaufnahme_wp_ehz", "Strom kW" if de else "Electricity kW"),
        cop_series("COP"),
    ], w=12, h=9, fill=10, overrides=[
        {"matcher": {"id": "byName", "options": "COP"},
         "properties": [{"id": "custom.axisPlacement", "value": "right"},
                        {"id": "custom.fillOpacity", "value": 0},
                        {"id": "custom.lineWidth", "value": 2},
                        {"id": "color", "value": {"mode": "fixed", "fixedColor": "green"}},
                        {"id": "unit", "value": "none"}]},
        {"matcher": {"id": "byName", "options": "Wärme kW" if de else "Heat kW"},
         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "orange"}},
                        {"id": "unit", "value": "kwatt"}]},
        {"matcher": {"id": "byName", "options": "Strom kW" if de else "Electricity kW"},
         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}},
                        {"id": "unit", "value": "kwatt"}]},
    ], desc=L("Der COP ist nur eingezeichnet, solange der Verdichter läuft.",
              "The COP is only drawn while the compressor is running."))

    b.series(L("Verdichter und Pumpen", "Compressor and pumps"), [
        series("verdichterfrequenz", "Frequenz Hz" if de else "Frequency Hz"),
        series("aktuelle_leistungsvorgabe_verdichter", "Leistungsvorgabe %" if de else "Power demand %"),
        series("drehzahl_zhp", "Heizungspumpe %" if de else "Heating pump %"),
        series("drehzahl_ventilator", "Ventilator rpm" if de else "Fan rpm"),
    ], w=12, h=8, fill=0, overrides=[
        {"matcher": {"id": "byName", "options": "Ventilator rpm" if de else "Fan rpm"},
         "properties": [{"id": "custom.axisPlacement", "value": "right"}]}])

    b.series(L("Durchfluss und Spreizung", "Flow rate and spread"), [
        series("heizkreisdurchfluss", "Durchfluss l/min" if de else "Flow rate l/min"),
        series("spreizung", "Spreizung K" if de else "Spread K", measurement="wolf_derived"),
        series("soll_spreizung", "Soll-Spreizung K" if de else "Target spread K"),
    ], w=12, h=8, fill=0, overrides=[
        {"matcher": {"id": "byName", "options": "Soll-Spreizung K" if de else "Target spread K"},
         "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [8, 6]}},
                        {"id": "color", "value": {"mode": "fixed", "fixedColor": "text"}}]}])

    b.timeline(L("Betriebsart", "Operating mode"),
               f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "wolf" and r.device == "heatpump" and r._field == "betriebsart_heizgeraet")
  |> aggregateWindow(every: $__interval, fn: last, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
  |> set(key: "_field", value: "{"Betriebsart" if de else "Mode"}")''',
               mode_map, w=24, h=6,
               desc=L("Ein Balken je Betriebsart. Kurze Abtaubalken im Winter sind normal.",
                      "One bar per operating mode. Short defrost bars in winter are normal."))

    # ---------------------------------------------------------------- Effizienz
    b.row(L("Effizienz", "Efficiency"))
    b.stat(L("JAZ laufendes Jahr", "SPF this year"), last("jaz_aktuelles_jahr"), w=4, h=5, decimals=2,
           thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 2.5},
                       {"color": "yellow", "value": 3.2}, {"color": "green", "value": 4.0}],
           desc=L("Jahresarbeitszahl aus den Zählern der Wolf: erzeugte Wärme geteilt durch Stromverbrauch.",
                  "Seasonal performance factor from the Wolf's own counters: heat produced divided by electricity used."))
    b.stat(L("Arbeitszahl heute", "Performance factor today"),
           last("az_tag", measurement="wolf_derived"), w=4, h=5, decimals=2,
           thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 2.5},
                       {"color": "yellow", "value": 3.2}, {"color": "green", "value": 4.0}])
    b.stat(L("Arbeitszahl Monat", "Performance factor month"),
           last("az_monat", measurement="wolf_derived"), w=4, h=5, decimals=2)
    b.stat(L("Arbeitszahl Vortag", "Performance factor yesterday"), last("taz_vortag"), w=4, h=5, decimals=2,
           desc=L("Tagesarbeitszahl des Vortages, direkt aus der Wolf.",
                  "Daily performance factor of the previous day, straight from the Wolf."))
    b.stat(L("Wärme heute", "Heat today"), last("erzeugte_waermemenge_aktueller_tag"), w=4, h=5,
           unit="kwatth", decimals=0, color="orange")
    b.stat(L("Strom heute", "Electricity today"), last("verbrauch_aktueller_tag"), w=4, h=5,
           unit="kwatth", decimals=0, color="red")

    b.bars(L("Wärme und Strom je Tag", "Heat and electricity per day"),
           f'''import "timezone"
option location = timezone.location(name: "{TZ}")
from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "wolf" and r.device == "heatpump" and (r._field == "erzeugte_waermemenge_aktueller_tag" or r._field == "verbrauch_aktueller_tag"))
  |> aggregateWindow(every: 1d, fn: max, createEmpty: false, timeSrc: "_start")
  |> map(fn: (r) => ({{ r with _field: if r._field == "erzeugte_waermemenge_aktueller_tag" then "{"Wärme" if de else "Heat"}" else "{"Strom" if de else "Electricity"}" }}))
  |> keep(columns: ["_time", "_value", "_field"])
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")''',
           w=12, h=9, unit="kwatth", overrides=[
               {"matcher": {"id": "byName", "options": "Wärme" if de else "Heat"},
                "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "orange"}}]},
               {"matcher": {"id": "byName", "options": "Strom" if de else "Electricity"},
                "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}],
           desc=L("Die Wolf zählt beides je Tag mit und setzt um Mitternacht zurück; hier steht der Tagesendstand.",
                  "The Wolf counts both per day and resets at midnight; shown here is each day's final reading."))

    b.series(L("Arbeitszahl je Tag", "Performance factor per day"), [
        f'''import "timezone"
option location = timezone.location(name: "{TZ}")
from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "wolf" and r.device == "heatpump" and (r._field == "erzeugte_waermemenge_aktueller_tag" or r._field == "verbrauch_aktueller_tag"))
  |> aggregateWindow(every: 1d, fn: max, createEmpty: false, timeSrc: "_start")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.verbrauch_aktueller_tag and r.verbrauch_aktueller_tag > 0.0)
  |> map(fn: (r) => ({{ _time: r._time, _value: r.erzeugte_waermemenge_aktueller_tag / r.verbrauch_aktueller_tag }}))
  |> keep(columns: ["_time", "_value"])
  |> set(key: "_field", value: "{"Arbeitszahl" if de else "Performance factor"}")'''],
        w=12, h=9, style="bars", fill=70, decimals=2,
        desc=L("Erzeugte Wärme geteilt durch Strom, Tag für Tag. Im Sommer drückt reiner Warmwasserbetrieb den Wert.",
               "Heat produced divided by electricity, day by day. In summer, hot-water-only operation pulls it down."))

    # ---------------------------------------------------------------- Taktung
    b.row(L("Taktung und Laufzeit", "Cycling and runtime"))
    b.stat(L("Takte heute", "Cycles today"), last("takte_heute", measurement="wolf_derived"), w=4, h=5,
           decimals=0, thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 12},
                                   {"color": "orange", "value": 20}, {"color": "red", "value": 30}],
           desc=L("Wie oft der Verdichter heute angelaufen ist. Wenige lange Takte schonen ihn, "
                  "viele kurze sind der häufigste Auslegungsfehler.",
                  "How often the compressor started today. Few long cycles are gentle on it; many short "
                  "ones are the most common sizing mistake."))
    b.stat(L("Laufzeit je Takt", "Runtime per cycle"), last("laufzeit_je_takt_min", measurement="wolf_derived"),
           w=4, h=5, unit="m", decimals=0,
           thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 10},
                       {"color": "yellow", "value": 20}, {"color": "green", "value": 30}],
           desc=L("Mittlere Laufzeit eines Takts heute. Unter zehn Minuten taktet die Anlage zu kurz.",
                  "Average runtime of a cycle today. Below ten minutes the system is short-cycling."))
    b.stat(L("Laufzeit heute", "Runtime today"), last("laufzeit_min_heute", measurement="wolf_derived"),
           w=4, h=5, unit="m", decimals=0, color="purple")
    b.stat(L("Abtauungen heute", "Defrosts today"), last("abtauungen_heute", measurement="wolf_derived"),
           w=4, h=5, decimals=0, color="blue",
           desc=L("Abtauvorgänge kosten Wärme aus dem Speicher. Bei Außentemperaturen um 0 bis 5 °C "
                  "mit Feuchte sind sie normal.",
                  "Defrost cycles take heat back out of the buffer. Around 0 to 5 °C with humidity they are normal."))
    b.stat(L("Verdichterstarts gesamt", "Compressor starts total"), last("verdichterstarts"), w=4, h=5,
           decimals=0, color="text")
    b.stat(L("Betriebsstunden Verdichter", "Compressor hours"), last("betriebsstunden_verdichter"), w=4, h=5,
           unit="h", decimals=0, color="text")

    b.bars(L("Takte je Tag", "Cycles per day"), daily_delta("verdichterstarts", "Takte" if de else "Cycles"),
           w=12, h=8, desc=L("Aus dem Gesamtzähler der Wolf: wie viele Starts auf jeden Tag entfallen.",
                             "From the Wolf's total counter: how many starts fall on each day."))

    b.bars(L("Laufzeitanteile heute", "Runtime split today"),
           f'''from(bucket: "{BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "wolf_derived" and (r._field == "hz_min_heute" or r._field == "ww_min_heute" or r._field == "abtau_min_heute" or r._field == "kuehl_min_heute" or r._field == "sperr_min_heute" or r._field == "eheiz_min_heute"))
  |> last()
  |> map(fn: (r) => ({{ r with _field:
        if r._field == "hz_min_heute" then "{"Heizen" if de else "Heating"}"
        else if r._field == "ww_min_heute" then "{"Warmwasser" if de else "Hot water"}"
        else if r._field == "abtau_min_heute" then "{"Abtauen" if de else "Defrost"}"
        else if r._field == "kuehl_min_heute" then "{"Kühlen" if de else "Cooling"}"
        else if r._field == "eheiz_min_heute" then "{"Heizstab" if de else "Immersion heater"}"
        else "{"Sperrzeit" if de else "Blocking time"}" }}))
  |> keep(columns: ["_time", "_value", "_field"])
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")''',
           w=12, h=8, unit="m",
           desc=L("Minuten je Betriebsart seit Mitternacht, vom Sidecar mitgezählt.",
                  "Minutes per operating mode since midnight, counted by the sidecar."))

    # ---------------------------------------------------------------- Kältekreis
    b.row(L("Kältekreis (Fachmann)", "Refrigerant circuit (expert)"))
    b.series(L("Temperaturen Kältekreis", "Refrigerant temperatures"), [
        series("heissgastemperatur", "Heißgas" if de else "Hot gas"),
        series("sauggastemperatur", "Sauggas" if de else "Suction gas"),
        series("zulufttemperatur", "Zuluft" if de else "Supply air"),
        series("ablufttemperatur", "Abluft" if de else "Exhaust air"),
    ], w=12, h=8, unit="celsius", fill=0,
        desc=L("Heißgas weit über 100 °C oder ein sehr kleiner Abstand zwischen Sauggas und Zuluft "
               "deutet auf Kältemittelmangel hin. Abluft −273 °C heißt: Fühler nicht vorhanden.",
               "Hot gas well above 100 °C, or a very small gap between suction gas and supply air, points to "
               "low refrigerant. Exhaust air at −273 °C means the sensor is not fitted."))
    b.series(L("Drücke und Expansionsventil", "Pressures and expansion valve"), [
        series("p_heissgas", "Hochdruck bar" if de else "High pressure bar"),
        series("p_sauggas", "Niederdruck bar" if de else "Low pressure bar"),
        series("eev_hz", "EEV Heizen" if de else "EEV heating"),
        series("eev_k", "EEV Kühlen" if de else "EEV cooling"),
    ], w=12, h=8, fill=0, overrides=[
        {"matcher": {"id": "byRegexp", "options": "EEV.*"},
         "properties": [{"id": "custom.axisPlacement", "value": "right"}]}])

    # ---------------------------------------------------------------- Heizkreis und Warmwasser
    b.row(L("Heizkreis und Warmwasser", "Heating circuit and hot water"))
    b.series(L("Heizkreis", "Heating circuit"), [
        series("vorlauftemperatur", "Vorlauf Mischerkreis" if de else "Mixer circuit flow", device="circuit"),
        series("mischersolltemperatur", "Mischer-Soll" if de else "Mixer setpoint", device="mixer"),
        series("analogeingang_vorlauffuehler_vf", "Vorlauffühler VF" if de else "Flow sensor VF", device="mixer"),
        series("gemittelte_aussentemperatur", "Außen gemittelt" if de else "Outside averaged", device="circuit"),
    ], w=12, h=8, unit="celsius", fill=0,
        desc=L("Der Mischerkreis fährt die Heizkurve; die gemittelte Außentemperatur ist ihre Eingangsgröße.",
               "The mixer circuit follows the heating curve; the averaged outside temperature is its input."))

    b.series(L("Warmwasser", "Hot water"), [
        series("warmwassertemperatur", "Speicher" if de else "Cylinder"),
        series("warmwassersolltemperatur", "Soll" if de else "Setpoint"),
    ], w=12, h=8, unit="celsius", fill=0, overrides=[
        {"matcher": {"id": "byName", "options": "Soll" if de else "Setpoint"},
         "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [8, 6]}},
                        {"id": "color", "value": {"mode": "fixed", "fixedColor": "text"}}]}],
        desc=L("Die Sägezahnform ist normal: aufheizen bis zum Sollwert, dann Abkühlen bis zur Hysterese.",
               "The sawtooth shape is normal: heat up to the setpoint, then cool down to the hysteresis."))

    # ---------------------------------------------------------------- Zähler der Wolf
    b.row(L("Zähler der Wolf", "The Wolf's own counters"))
    b.table(L("Verbrauch und Wärme", "Consumption and heat"),
            f'''from(bucket: "{BUCKET}")
  |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "wolf" and r.device == "heatpump" and (r._field == "verbrauch_aktueller_tag" or r._field == "erzeugte_waermemenge_aktueller_tag" or r._field == "verbrauch_aktueller_monat" or r._field == "erzeugte_waermemenge_aktueller_monat" or r._field == "verbrauch_aktuelles_jahr" or r._field == "erzeugte_waermemenge_aktuelles_jahr" or r._field == "verbrauch_vorjahr" or r._field == "erzeugte_waermemenge_vorjahr" or r._field == "verbrauch_vortag" or r._field == "erzeugte_waermemenge_vortag"))
  |> last()
  |> map(fn: (r) => ({{ r with
        _field: if r._field =~ /^verbrauch/ then "{"Strom kWh" if de else "Electricity kWh"}" else "{"Wärme kWh" if de else "Heat kWh"}",
        span: if r._field =~ /aktueller_tag$/ then "1 {"heute" if de else "today"}"
              else if r._field =~ /vortag$/ then "2 {"Vortag" if de else "yesterday"}"
              else if r._field =~ /aktueller_monat$/ then "3 {"Monat" if de else "month"}"
              else if r._field =~ /aktuelles_jahr$/ then "4 {"Jahr" if de else "year"}"
              else "5 {"Vorjahr" if de else "last year"}" }}))
  |> keep(columns: ["span", "_field", "_value"])
  |> pivot(rowKey: ["span"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["span"])''',
            w=12, h=9,
            desc=L("Direkt aus den Statistikregistern der Wärmepumpe, nicht von uns gerechnet.",
                   "Straight from the heat pump's statistics registers, not computed by us."))

    b.table(L("Gerät", "Device"),
            f'''from(bucket: "{BUCKET}")
  |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "wolf" and r.device == "heatpump" and (r._field == "seriennummer" or r._field == "leistungsklasse" or r._field == "hcm_4_firmware" or r._field == "hpm_2_firmware" or r._field == "herstellwoche" or r._field == "herstelljahr" or r._field == "netzbetriebsstunden" or r._field == "betriebsstunden_verdichter" or r._field == "betriebsstunden_e_heizung" or r._field == "anzahl_netz_ein"))
  |> last()
  |> keep(columns: ["_field", "_value"])
  |> sort(columns: ["_field"])''',
            w=12, h=9)

    # ---------------------------------------------------------------- Haus und PV
    b.row(L("Haus und PV", "House and PV"))
    b.series(L("Wärmepumpe, Haus und Solar", "Heat pump, house and solar"), [
        series("strom_w", "Wärmepumpe W" if de else "Heat pump W", measurement="wolf_derived"),
        f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "shelly" and r._field == "household_w")
  |> aggregateWindow(every: $__interval, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
  |> set(key: "_field", value: "{"Haus W" if de else "House W"}")''',
        f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "nexa" and (r._field == "pv1Voltage" or r._field == "pv1Current" or r._field == "pv2Voltage" or r._field == "pv2Current" or r._field == "pv3Voltage" or r._field == "pv3Current" or r._field == "pv4Voltage" or r._field == "pv4Current"))
  |> aggregateWindow(every: $__interval, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.pv1Voltage)
  |> map(fn: (r) => ({{ _time: r._time, _value: r.pv1Voltage * r.pv1Current + r.pv2Voltage * r.pv2Current + r.pv3Voltage * r.pv3Current + r.pv4Voltage * r.pv4Current }}))
  |> keep(columns: ["_time", "_value"])
  |> set(key: "_field", value: "{"Solar W" if de else "Solar W"}")''',
    ], w=24, h=9, unit="watt", fill=10, overrides=[
        {"matcher": {"id": "byName", "options": "Wärmepumpe W" if de else "Heat pump W"},
         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]},
        {"matcher": {"id": "byName", "options": "Haus W" if de else "House W"},
         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "orange"}},
                        {"id": "custom.fillOpacity", "value": 0}]},
        {"matcher": {"id": "byName", "options": "Solar W"},
         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "yellow"}}]}],
        desc=L("Die Wärmepumpe meldet ihre Aufnahme nur in ganzen kW, die Stufen sind daher grob. "
               "Der Shelly misst das ganze Haus inklusive Wärmepumpe.",
               "The heat pump reports its input only in whole kW, so the steps are coarse. The Shelly "
               "measures the whole house including the heat pump."))

    b.stat(L("Wärmekosten heute", "Heat cost today"),
           f'''import "array"
strom = from(bucket: "{BUCKET}")
  |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "wolf" and r.device == "heatpump" and r._field == "verbrauch_aktueller_tag")
  |> last()
  |> findColumn(fn: (key) => true, column: "_value")
preis = from(bucket: "{BUCKET}")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "tariff" and r._field == "price_ct_kwh")
  |> last()
  |> findColumn(fn: (key) => true, column: "_value")
array.from(rows: [{{ _time: now(), _value: (if length(arr: strom) > 0 then strom[0] else 0.0) * (if length(arr: preis) > 0 then preis[0] else 30.0) / 100.0 }}])''',
           w=6, h=5, unit="currencyEUR", decimals=2, color="red",
           desc=L("Stromverbrauch der Wärmepumpe heute mal dem hinterlegten Arbeitspreis "
                  "(Einstellungsseite, Abschnitt Kosten).",
                  "The heat pump's electricity use today times the configured price (settings page, cost section)."))
    b.stat(L("Wärmepreis je kWh", "Heat price per kWh"),
           f'''import "array"
az = from(bucket: "{BUCKET}")
  |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "wolf_derived" and r._field == "az_jahr")
  |> last()
  |> findColumn(fn: (key) => true, column: "_value")
preis = from(bucket: "{BUCKET}")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "tariff" and r._field == "price_ct_kwh")
  |> last()
  |> findColumn(fn: (key) => true, column: "_value")
array.from(rows: [{{ _time: now(), _value: (if length(arr: preis) > 0 then preis[0] else 30.0) / (if length(arr: az) > 0 and az[0] > 0.0 then az[0] else 1.0) }}])''',
           w=6, h=5, decimals=1, color="orange",
           desc=L("Arbeitspreis geteilt durch die Arbeitszahl des Jahres: was eine Kilowattstunde Wärme "
                  "wirklich kostet, in Cent.",
                  "Price per kWh divided by this year's performance factor: what a kilowatt hour of heat "
                  "really costs, in cents."))
    b.stat(L("Strom Wärmepumpe Jahr", "Heat pump electricity this year"), last("verbrauch_aktuelles_jahr"),
           w=6, h=5, unit="kwatth", decimals=0, color="red")
    b.stat(L("Wärme Jahr", "Heat this year"), last("erzeugte_waermemenge_aktuelles_jahr"),
           w=6, h=5, unit="kwatth", decimals=0, color="orange")

    other = "wolf-en" if de else "wolf-de"
    return {
        "uid": f"wolf-{lang}",
        "title": "GroLo · Wärmepumpe CHA" if de else "GroLo · Heat pump CHA",
        "tags": ["wolf", "waermepumpe" if de else "heatpump", "grolo", lang],
        "timezone": "browser",
        "editable": True,
        "graphTooltip": 1,
        "refresh": "30s",
        "time": {"from": "now-24h", "to": "now"},
        "timepicker": {"refresh_intervals": ["10s", "30s", "1m", "5m", "15m"]},
        "links": [
            {"title": "Wärmepumpe bedienen" if de else "Heat pump controls", "type": "link",
             "url": "/public/grolo/wolf.html", "icon": "external link", "tooltip": "", "targetBlank": True,
             "asDropdown": False},
            {"title": "NEXA", "type": "link", "url": f"/d/nexa2000{'-de' if de else ''}", "icon": "external link",
             "tooltip": "", "targetBlank": False, "asDropdown": False, "keepTime": True},
            {"title": "English" if de else "Deutsch", "type": "link", "url": f"/d/{other}",
             "icon": "external link", "tooltip": "", "targetBlank": False, "asDropdown": False, "keepTime": True},
        ],
        "schemaVersion": 39,
        "version": 1,
        "panels": b.panels,
    }


def main():
    with open(os.path.join(ROOT, "wolf", "catalog.json")) as fh:
        catalog = json.load(fh)
    for lang in ("de", "en"):
        dash = build(lang, catalog)
        path = os.path.join(ROOT, "grafana", "dashboards", f"wolf-{lang}.json")
        with open(path, "w") as fh:
            json.dump(dash, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        print(f"{path}: {len(dash['panels'])} Panels", file=sys.stderr)


if __name__ == "__main__":
    main()
