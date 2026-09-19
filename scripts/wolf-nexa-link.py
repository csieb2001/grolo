#!/usr/bin/env python3
"""Hängt den NEXA-Dashboards eine Wärmepumpen-Zeile und den Link zum Wärmepumpen-Dashboard an.

Getrennt vom Generator der Wärmepumpen-Dashboards, weil die NEXA-Dashboards von Hand gepflegt werden.
Der Lauf ist wiederholbar: vorhandene Zeile und vorhandener Link werden ersetzt, nicht verdoppelt.

Aufruf:  scripts/wolf-nexa-link.py
"""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DS = {"type": "influxdb", "uid": "influx-nexa"}
ROW_TITLE = {"de": "Wärmepumpe", "en": "Heat pump"}


def panels(lang):
    de = lang == "de"
    t = lambda a, b: a if de else b
    return [
        {"type": "row", "title": ROW_TITLE[lang], "collapsed": False, "panels": [],
         "gridPos": {"x": 0, "y": 0, "w": 24, "h": 1}},
        {"type": "timeseries", "title": t("Haus, Solar und Wärmepumpe", "House, solar and heat pump"),
         "description": t("Was das Haus zieht (Shelly), was die Module liefern und wie viel davon die "
                          "Wärmepumpe nimmt. Die Wolf meldet ihre Aufnahme nur in ganzen kW.",
                          "What the house draws (Shelly), what the panels deliver, and how much of it the "
                          "heat pump takes. The Wolf reports its input only in whole kW."),
         "gridPos": {"x": 0, "y": 1, "w": 16, "h": 9}, "datasource": DS,
         "targets": [
             {"refId": "A", "datasource": DS, "query": f'''from(bucket: "nexa")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "nexa" and (r._field == "pv1Voltage" or r._field == "pv1Current" or r._field == "pv2Voltage" or r._field == "pv2Current" or r._field == "pv3Voltage" or r._field == "pv3Current" or r._field == "pv4Voltage" or r._field == "pv4Current"))
  |> aggregateWindow(every: $__interval, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.pv1Voltage)
  |> map(fn: (r) => ({{ _time: r._time, _value: r.pv1Voltage * r.pv1Current + r.pv2Voltage * r.pv2Current + r.pv3Voltage * r.pv3Current + r.pv4Voltage * r.pv4Current }}))
  |> keep(columns: ["_time", "_value"])
  |> set(key: "_field", value: "{t("Solar", "Solar")}")'''},
             {"refId": "B", "datasource": DS, "query": f'''from(bucket: "nexa")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "shelly" and r._field == "household_w")
  |> aggregateWindow(every: $__interval, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
  |> set(key: "_field", value: "{t("Haus", "House")}")'''},
             {"refId": "C", "datasource": DS, "query": f'''from(bucket: "nexa")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "wolf_derived" and r._field == "strom_w")
  |> aggregateWindow(every: $__interval, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
  |> set(key: "_field", value: "{t("Wärmepumpe", "Heat pump")}")'''},
         ],
         "fieldConfig": {"defaults": {"unit": "watt",
                                      "custom": {"drawStyle": "line", "lineWidth": 1.6, "fillOpacity": 10,
                                                 "showPoints": "never", "spanNulls": True,
                                                 "lineInterpolation": "smooth"}},
                         "overrides": [
                             {"matcher": {"id": "byName", "options": t("Solar", "Solar")},
                              "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "yellow"}}]},
                             {"matcher": {"id": "byName", "options": t("Haus", "House")},
                              "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "orange"}},
                                             {"id": "custom.fillOpacity", "value": 0}]},
                             {"matcher": {"id": "byName", "options": t("Wärmepumpe", "Heat pump")},
                              "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}]},
         "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True, "calcs": []},
                     "tooltip": {"mode": "multi", "sort": "none"}}},
        stat(t("Betriebsart", "Operating mode"), x=16, y=1, w=8, h=3, query='''from(bucket: "nexa")
  |> range(start: -2h)
  |> filter(fn: (r) => r._measurement == "wolf" and r.device == "heatpump" and r._field == "betriebsart_heizgeraet")
  |> last()
  |> keep(columns: ["_time", "_value"])''', maps=mappings(lang)),
        stat(t("Wärme heute", "Heat today"), x=16, y=4, w=4, h=6, query='''from(bucket: "nexa")
  |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "wolf" and r.device == "heatpump" and r._field == "erzeugte_waermemenge_aktueller_tag")
  |> last()
  |> keep(columns: ["_time", "_value"])''', unit="kwatth", color="orange", decimals=0),
        stat(t("Strom heute", "Electricity today"), x=20, y=4, w=4, h=6, query='''from(bucket: "nexa")
  |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "wolf" and r.device == "heatpump" and r._field == "verbrauch_aktueller_tag")
  |> last()
  |> keep(columns: ["_time", "_value"])''', unit="kwatth", color="red", decimals=0),
    ]


def mappings(lang):
    with open(os.path.join(ROOT, "wolf", "catalog.json")) as fh:
        catalog = json.load(fh)
    for dev in catalog["devices"]:
        if dev["role"] != "heatpump":
            continue
        for p in dev["params"]:
            if p["key"] == "betriebsart_heizgeraet" and p.get("options"):
                return [{"type": "value",
                         "options": {o["v"]: {"text": o[lang], "index": i} for i, o in enumerate(p["options"])}}]
    return []


def stat(title, x, y, w, h, query, maps=None, unit=None, color="blue", decimals=None):
    defaults = {"color": {"mode": "fixed", "fixedColor": color}}
    if unit:
        defaults["unit"] = unit
    if decimals is not None:
        defaults["decimals"] = decimals
    if maps:
        defaults["mappings"] = maps
    return {"type": "stat", "title": title, "gridPos": {"x": x, "y": y, "w": w, "h": h}, "datasource": DS,
            "targets": [{"refId": "A", "datasource": DS, "query": query}],
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/^Value$/", "values": False},
                        "colorMode": "value", "graphMode": "none", "textMode": "value", "justifyMode": "center"}}


def main():
    for lang, uid in (("en", "nexa2000"), ("de", "nexa2000-de")):
        path = os.path.join(ROOT, "grafana", "dashboards", f"nexa-{lang}.json")
        with open(path) as fh:
            dash = json.load(fh)

        # eine frühere Wärmepumpen-Zeile samt ihrer Panels entfernen
        keep, dropping = [], False
        for p in dash["panels"]:
            if p["type"] == "row":
                dropping = p.get("title") == ROW_TITLE[lang]
            if not dropping:
                keep.append(p)
        dash["panels"] = keep

        bottom = max((p["gridPos"]["y"] + p["gridPos"]["h"] for p in keep), default=0)
        new = panels(lang)
        for p in new:
            p["gridPos"]["y"] += bottom
        ids = {p.get("id", 0) for p in keep}
        next_id = max(ids, default=0) + 1
        for p in new:
            p["id"] = next_id
            next_id += 1
        dash["panels"] = keep + new

        title = "Wärmepumpe" if lang == "de" else "Heat pump"
        dash["links"] = [l for l in dash.get("links", []) if not l["url"].startswith("/d/wolf-")]
        dash["links"].insert(1, {"title": title, "type": "link", "url": f"/d/wolf-{lang}",
                                 "icon": "external link", "tooltip": "", "targetBlank": False,
                                 "asDropdown": False, "keepTime": True})

        with open(path, "w") as fh:
            json.dump(dash, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print(f"{path}: Zeile „{ROW_TITLE[lang]}“ mit {len(new) - 1} Panels angehängt", file=sys.stderr)


if __name__ == "__main__":
    main()
