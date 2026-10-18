#!/usr/bin/env python3
"""house_check: der Hauscheck – einmal am Tag die kritischen Punkte des Hauses, als Kacheln.

Der Erkenntnis-Dienst (insights.py) beantwortet Fragen an die Anlage: wo verschattet es, wie hoch ist der
Vorlauf, was kostet Warmwasser mittags. Der Hauscheck stellt eine andere Frage: *geht hier gerade etwas
kaputt, oder fängt es an, kaputtzugehen?* Gemeint sind die Dinge, die an einem Haus teuer werden, wenn man
sie zu spät bemerkt – Wasser im Heizkreis, Feuchte an der Wand, ein Speicher, der nie mehr voll wird, ein
Messfühler, der seit Tagen denselben Wert liefert.

Jede Kachel gehört zu einem von zwei Zeithorizonten:

  short   letzte 24 Stunden bis 7 Tage. Ein Zustand, auf den man diese Woche reagieren sollte.
  long    Wochen bis Jahre. Ein Trend, der erst über Zeit sichtbar wird – Druckverlust im Monat,
          Grundlast über Monate, Wärmebedarf je Gradtag, PV-Ertrag gegen das Modell, Alterung des Akkus.

Für die langen gilt die Regel aus insights.py verschärft: die Historie beginnt am 06.09.2026, und eine
Jahresaussage nach vier Wochen wäre erfunden. Deshalb trägt jede lange Kachel ein "ready" – das Datum, ab
dem die Aussage trägt – und bis dahin basis "thin". Gezeigt wird sie trotzdem: wer sieht, dass ein Trend
noch dünn ist, weiß wenigstens, dass er beobachtet wird.

Was hier *nicht* steht, weil es woanders schon steht und zwei Rechnungen zwei Wahrheiten wären:
Verschattung, Vorlauf/Hub, Arbeitszahl, Zellspreizung, Standby, Umlauf (alles insights.py). Die Taktung
kommt als einzige doppelt vor – aber als das Urteil der wolf-bridge, nicht neu gerechnet.

Dazu eine Liste "gaps": was das Haus heute nicht misst, obwohl es zu den kritischen Punkten gehört. Sie ist
an die vorhandenen Messreihen gebunden – was gemessen wird, fällt aus der Liste.

Veröffentlicht (retained):
  <BASE>/grolo/house_check   {"updated": unix, "day": "YYYY-MM-DD", "tiles": [...], "gaps": [...], "metrics": {...}}
Auslösen von Hand:
  <BASE>/grolo/house_check/run   (beliebige Nachricht)

Umgebung: wie insights.py, dazu HOUSE_CHECK_HOUR (lokale Stunde des Tageslaufs, Standard 6),
          HOUSE_CHECK_ONCE (einmal rechnen, ausgeben, nichts veröffentlichen), TZ
"""
import calendar, json, logging, math, os, sys, threading, time
import paho.mqtt.client as mqtt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Postgres-Protokoll, InfluxDB-Abfrage und die kleinen Helfer gibt es schon – einmal reicht.
from insights import Pg, influx_query, num, de, day_de, median, INFLUX_BUCKET  # noqa: E402
import mqtt_watchdog  # noqa: E402

BASE = os.getenv("HA_BASE_TOPIC", "homeassistant")
HOST = os.getenv("MQTT_HOST", "mosquitto"); PORT = int(os.getenv("MQTT_PORT", "1883"))
CHECK_HOUR = int(os.getenv("HOUSE_CHECK_HOUR", "6"))
TOPIC = f"{BASE}/grolo/house_check"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("house_check")

# ---------------------------------------------------------------------------- Schwellen, alle benannt
H = {
    # Heizkreis. Ein geschlossener Heizkreis im Einfamilienhaus läuft kalt mit 1,0–2,0 bar; das
    # Sicherheitsventil öffnet bei 3 bar. Unter 1 bar schalten viele Wärmepumpen ab oder ziehen Luft.
    "p_low": 1.0, "p_watch": 1.2, "p_high": 2.7,
    "p_loss_warn": 0.15,      # bar je 30 Tage – darüber ist es kein Temperatureffekt mehr
    "p_loss_watch": 0.06,
    "trend_min_days": 21,     # so viele Tage braucht eine Gerade, bevor man ihr eine Steigung glaubt
    # Datenfrische
    "gap_warn_h": 2.0,        # längere Lücke in 24 h ist ein Ausfall
    "frozen_h": 3.0,          # so lange dieselben Werte bei Wolf -> der Fühler liefert nichts Neues mehr
    "frozen_room_h": 8.0,     # Raumfühler: Temperatur *und* Feuchte auf die Hundertstel gleich
    # Feuchte, DIN 4108-2: Schimmel wächst ab 80 % relativer Feuchte an der Oberfläche. Die Oberfläche
    # wird über den Temperaturfaktor f_Rsi = 0,7 geschätzt – das ist der Mindestwert, den die Norm an
    # einer Wärmebrücke verlangt, also die ungünstigste Ecke, die ein ordentliches Haus haben darf.
    "f_rsi": 0.70, "surface_rh": 80.0, "ground_c": 10.0,
    "mould_warn_share": 0.20, "mould_watch_share": 0.05,
    "rh_watch": 65.0,
    # Räume
    "under_k": 1.0, "under_share": 0.60, "cold_c": 16.0, "heating_mean_c": 15.0,
    # Warmwasser kommt aus einer Frischwasserstation an einem 600-l-Puffer: das Trinkwasser wird erst beim
    # Zapfen über den Wärmetauscher erwärmt und steht nirgends – Legionellen sind hier kein Thema, 50 °C im
    # Puffer genügen. Die Station braucht oben im Puffer rund 5 K über der gewünschten Zapftemperatur.
    "buf_tap_c": 45.0, "buf_margin_k": 5.0,
    # Akku
    "bat_hot": 45.0, "bat_cold": 0.0, "full_soc": 99.0, "full_days": 14,
    "rated_cycles": 6000,     # Datenblatt der NOAH/NEXA-Akkus (LFP)
    # Grundlast
    "base_rise_watch": 0.20, "base_rise_warn": 0.50, "base_min_w": 20.0,
    # PV gegen Modell
    "pv_drop_watch": 0.15, "pv_drop_warn": 0.30, "string_dead_share": 0.05,
    # Wärme je Gradtag
    "hdd_base": 15.0, "hdd_min": 30.0,
    # Funk
    "wifi_weak": -75.0, "wifi_poor": -82.0,
    "price_ct_kwh": 30.0,
    "start": "2026-09-06",    # ab hier misst das Haus; die langen Kacheln rechnen ihr "ready" davon aus
}


def en(v, n=1):
    return f"{v:.{n}f}"


def both(fn, *a):
    """Ein Satz in zwei Sprachen aus derselben Funktion – damit die Zahlen nie auseinanderlaufen."""
    return {"de": fn("de", *a), "en": fn("en", *a)}


def fmt(lang, v, n=1):
    return de(v, n) if lang == "de" else en(v, n)


def day_of(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def plus_days(iso, n):
    t = time.mktime(time.strptime(iso, "%Y-%m-%d")) + n * 86400 + 3600
    return day_of(t)


def slope_per_day(pts):
    """Steigung einer Ausgleichsgeraden durch (Tag, Wert). Kleinste Quadrate, sonst nichts."""
    if len(pts) < 2:
        return None
    n = len(pts)
    mx = sum(p[0] for p in pts) / n; my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    if sxx <= 0:
        return None
    return sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx


def psat(t):
    """Sättigungsdampfdruck in hPa (Magnus, über Wasser)."""
    return 6.112 * math.exp(17.62 * t / (243.12 + t))


def upper1(s):
    return s[:1].upper() + s[1:]


def pct(share):
    return round(share * 100)


def influx(flux):
    try:
        return influx_query(flux)
    except Exception as exc:
        LOG.warning("InfluxDB: %s", exc)
        return []


def series(measurement, field, start, every, fn):
    rows = influx(f'''from(bucket: "{INFLUX_BUCKET}") |> range(start: {start})
  |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "{field}")
  |> aggregateWindow(every: {every}, fn: {fn}, createEmpty: false, timeSrc: "_start")
  |> keep(columns: ["_time", "_value"])''')
    out = []
    for r in rows:
        v = num(r.get("_value"))
        t = r.get("_time")
        if v is None or not t:
            continue
        # Influx antwortet in UTC; der Zeitstempel ist der Beginn des Fensters (timeSrc oben).
        out.append((calendar.timegm(time.strptime(t[:19], "%Y-%m-%dT%H:%M:%S")), v))
    return out


def last_value(measurement, field, start="-30d"):
    rows = influx(f'''from(bucket: "{INFLUX_BUCKET}") |> range(start: {start})
  |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "{field}") |> last()''')
    return num(rows[-1].get("_value")) if rows else None


# ---------------------------------------------------------------------------- Daten einsammeln
def collect():
    now = time.time()
    d = {"now": now}
    with Pg() as pg:
        q = pg.query
        d["nexa"] = [[num(x) for x in r] for r in q("""
            SELECT extract(epoch from ts)::bigint, soc, temp_bat1, temp_bat2, temp_sys, pv_w
            FROM samples WHERE ts > now() - interval '8 days' ORDER BY ts""")]
        d["first_ts"] = num((q("SELECT extract(epoch from min(ts))::bigint FROM samples") or [[None]])[0][0])
        d["last_full"] = num((q(f"""SELECT extract(epoch from max(ts))::bigint FROM samples
                                    WHERE soc >= {H['full_soc']}""") or [[None]])[0][0])
        d["heat"] = [[num(x) for x in r] for r in q("""
            SELECT extract(epoch from ts)::bigint, outside_c, flow_c, return_c, dhw_c, compressor, mode
            FROM heat_samples WHERE ts > now() - interval '8 days' ORDER BY ts""")]
        d["rooms_h"] = q("""
            SELECT room, extract(epoch from date_trunc('hour', ts))::bigint, avg(temp_c), avg(setpoint_c),
                   avg(humidity_pct), min(temp_c), max(temp_c), min(humidity_pct), max(humidity_pct)
            FROM room_samples WHERE ts > now() - interval '7 days' GROUP BY 1, 2 ORDER BY 2""")
        d["rooms_last"] = q("""SELECT room, extract(epoch from max(ts))::bigint FROM room_samples
                               WHERE ts > now() - interval '3 days' GROUP BY 1""")
        d["room_state"] = (q("SELECT data::text FROM rooms WHERE id = 1") or [[None]])[0][0]
        d["weather_h"] = {int(float(r[0])): num(r[1]) for r in q("""
            SELECT extract(epoch from date_trunc('hour', ts))::bigint, avg(temperature)
            FROM weather_history WHERE ts > now() - interval '400 days' GROUP BY 1""")}
        d["weather_last"] = num((q("SELECT extract(epoch from max(ts))::bigint FROM weather_history")
                                 or [[None]])[0][0])
        d["heat_days"] = q("SELECT to_char(day, 'YYYY-MM-DD'), heat_kwh, el_kwh FROM heat_days ORDER BY day")
        # PV stundenweise, Ist gegen Modell. Stunden mit vollem Speicher fliegen raus: dort regeln die
        # Laderegler ab, und das sähe aus wie ein Ertragsverlust, wäre aber nur ein voller Akku.
        d["pv_hours"] = q("""
            WITH a AS (SELECT date_trunc('hour', ts) h, avg(pv_w) w, max(soc) soc, count(*) n,
                              avg(pv_v[1]*pv_a[1]) s1, avg(pv_v[2]*pv_a[2]) s2,
                              avg(pv_v[3]*pv_a[3]) s3, avg(pv_v[4]*pv_a[4]) s4
                       FROM samples WHERE ts > now() - interval '400 days' GROUP BY 1),
                 m AS (SELECT date_trunc('hour', t) h, sum(expected_w) e,
                              sum(expected_w) FILTER (WHERE string = 1) e1, sum(expected_w) FILTER (WHERE string = 2) e2,
                              sum(expected_w) FILTER (WHERE string = 3) e3, sum(expected_w) FILTER (WHERE string = 4) e4
                       FROM pv_model WHERE t < now() GROUP BY 1)
            SELECT extract(epoch from a.h)::bigint, a.w, m.e, a.soc, a.n, a.s1, a.s2, a.s3, a.s4, m.e1, m.e2, m.e3, m.e4
            FROM a JOIN m ON a.h = m.h WHERE m.e > 0 ORDER BY 1""")
        d["tariff"] = num((q("SELECT price_ct_kwh FROM tariff WHERE id = 1") or [[None]])[0][0])
        d["strings"] = (q("SELECT strings::text FROM site WHERE id = 1") or [[None]])[0][0]

    d["pressure_days"] = series("wolf", "anlagendruck", "-400d", "1d", "min")
    d["pressure_week"] = series("wolf", "anlagendruck", "-7d", "1h", "mean")
    # Tage, an denen die Außentemperatur der Wolf sich gar nicht bewegt hat, sind Tage, an denen ism7mqtt
    # nichts Neues geliefert hat (so geschehen vom 28.09. bis 04.10.2026). Was an diesen Tagen über den
    # Heizkreis gemeldet wurde, ist der eingefrorene letzte Stand und gehört aus jedem Trend heraus.
    d["wolf_stale_days"] = {day_of(t) for t, v in series("wolf", "aussentemperatur", "-400d", "1d", "spread")
                            if v == 0}
    d["dhw_days"] = series("wolf", "warmwassertemperatur", "-8d", "1d", "max")
    d["dhw_set"] = last_value("wolf", "warmwassersolltemperatur")
    d["shelly_10m"] = series("shelly", "household_w", "-120d", "10m", "mean")
    d["shelly_24h"] = series("shelly", "household_w", "-24h", "10m", "count")
    d["soh"] = series("nexa", "batterySoh", "-400d", "1d", "last")
    d["cycles"] = series("nexa", "batteryCycles", "-400d", "1d", "last")
    d["soc_limit"] = last_value("nexa", "dischargeSocLimit")
    d["wifi"] = series("dongle", "wifi_signal", "-7d", "1h", "mean")
    rows = influx('import "influxdata/influxdb/schema"\n'
                  f'schema.measurements(bucket: "{INFLUX_BUCKET}")')
    d["measurements"] = {r.get("_value") for r in rows if r.get("_value")}
    rows = influx('import "influxdata/influxdb/schema"\n'
                  f'schema.measurementFieldKeys(bucket: "{INFLUX_BUCKET}", measurement: "weather", start: -30d)')
    d["weather_fields"] = {r.get("_value") for r in rows if r.get("_value")}
    return d


# ---------------------------------------------------------------------------- die einzelnen Prüfungen
# Jede Prüfung gibt eine Kachel zurück oder None. None heißt: zu dieser Frage gibt es heute keine Daten,
# und eine leere Kachel wäre schlechter als keine.

def tile(key, area, horizon, level, basis, title, value, sub, text, advice=None, ready=None, detail=None):
    return {"key": key, "area": area, "horizon": horizon, "level": level, "basis": basis,
            "title": title, "value": value, "sub": sub, "text": text, "advice": advice,
            "ready": ready, "detail": detail or {}}


def longest_run(points, same, gap_s=900):
    """Längste Strecke (Sekunden) aufeinanderfolgender Punkte, die same() für gleich hält.
    Liefert (Dauer, Ende), Ende = Zeitpunkt des letzten Punkts dieser Strecke."""
    best, best_end, start, prev = 0.0, None, None, None
    for p in points:
        if prev is not None and same(prev, p) and p[0] - prev[0] <= gap_s:
            if start is None:
                start = prev[0]
            if p[0] - start > best:
                best, best_end = p[0] - start, p[0]
        else:
            start = None
        prev = p
    return best, best_end


def max_gap(ts_list, since, now):
    pts = [since] + sorted(t for t in ts_list if t >= since) + [now]
    return max(b - a for a, b in zip(pts, pts[1:])) if len(pts) > 1 else now - since


def check_data(d):
    """Datenfrische: kommen die Werte an, und bewegen sie sich noch?

    Zweierlei kann schiefgehen, und das Zweite ist das Gefährlichere: eine Quelle schweigt (Lücke), oder
    sie liefert weiter, aber immer denselben Stand (eingefroren). Das Zweite fällt in keinem Diagramm auf,
    weil die Linie ja weiterläuft – und jede Kachel auf dieser Seite rechnet dann mit einem Zustand von
    vorgestern.
    """
    now = d["now"]; since = now - 86400
    problems = []   # (de, en, advice_de, advice_en, ernst)

    # Der NEXA schläft, wenn der Akku an der Entladegrenze steht und keine Sonne scheint – dann meldet
    # auch der Dongle nichts. Eine solche Lücke ist Betrieb, kein Ausfall; gezählt wird nur, was danach übrig bleibt.
    floor = (d["soc_limit"] or 5) + 2
    nexa = [r for r in d["nexa"] if r[0]]
    worst, slept = 0.0, 0.0
    pts = [r for r in nexa if r[0] >= since]
    edges = [(since, None)] + [(r[0], r) for r in pts] + [(now, None)]
    for (a, ra), (b, _) in zip(edges, edges[1:]):
        if ra is not None and (ra[1] or 100) <= floor:     # die Abendsonne reicht ihm dann nicht mehr
            slept = max(slept, b - a)
        else:
            worst = max(worst, b - a)
    g = worst / 3600
    if g >= H["gap_warn_h"]:
        problems.append((f"NEXA: {de(g)} h ohne Messwert", f"NEXA: {en(g)} h without a sample",
                         "Dongle-Verbindung prüfen (scripts/verify.sh, Schritt 8).",
                         "Check the dongle connection (scripts/verify.sh, step 8).", True))

    heat = [r for r in d["heat"] if r[0] and r[0] >= since]
    g = max_gap([r[0] for r in heat], since, now) / 3600
    if g >= H["gap_warn_h"]:
        problems.append((f"Wärmepumpe: {de(g)} h ohne Messwert", f"Heat pump: {en(g)} h without a sample",
                         "Container wolf und wolf-bridge prüfen.", "Check the wolf and wolf-bridge containers.", True))
    frozen, end = longest_run(heat, lambda a, b: a[1:5] == b[1:5] and a[1] is not None)
    if frozen / 3600 >= H["frozen_h"]:
        still = end is not None and end == heat[-1][0] and now - end < 1800
        h = frozen / 3600
        problems.append((
            f"Wärmepumpe: {de(h, 0)} h lang dieselben Werte" + (" – bis jetzt" if still else ""),
            f"Heat pump: the same values for {en(h, 0)} h" + (" – still now" if still else ""),
            "ism7mqtt hängt, ohne abzustürzen (zuletzt 28.09.–04.10.2026): Container wolf neu starten."
            if still else "War eingefroren und läuft wieder; wenn das wiederkommt, braucht ism7mqtt einen Wachhund.",
            "ism7mqtt hangs without crashing (last seen 28 Sep – 4 Oct 2026): restart the wolf container."
            if still else "Was frozen and is running again; if it recurs, ism7mqtt needs a watchdog.", still))

    # Räume: jeder Fühler einzeln, sonst versteckt sich ein toter Thermostat hinter neun lebenden
    last = {r[0]: num(r[1]) for r in d["rooms_last"]}
    for room, t in sorted(last.items()):
        if t is not None and now - t > H["gap_warn_h"] * 3600:
            problems.append((f"Raum {room}: seit {de((now - t) / 3600, 0)} h still",
                             f"Room {room}: silent for {en((now - t) / 3600, 0)} h",
                             "Thermostat erreichbar? Batterie, Thread-Netz, tado-bridge.",
                             "Is the thermostat reachable? Battery, Thread mesh, tado-bridge.", True))
    by_room = {}
    for r in d["rooms_h"]:
        by_room.setdefault(r[0], []).append((int(float(r[1])), num(r[5]), num(r[6]), num(r[7]), num(r[8])))
    for room, hrs in by_room.items():
        # stündlich Minimum = Maximum bei Temperatur *und* Feuchte über viele Stunden: der Fühler steht
        run, _ = longest_run(hrs[-24:], lambda a, b: a[1] == a[2] == b[1] == b[2] and a[3] == a[4] == b[3] == b[4]
                             and a[1] is not None, gap_s=3700)
        if run / 3600 >= H["frozen_room_h"]:
            problems.append((f"Raum {room}: Temperatur und Feuchte stehen seit {de(run / 3600, 0)} h",
                             f"Room {room}: temperature and humidity unchanged for {en(run / 3600, 0)} h",
                             "Thermostat neu anlernen oder Batterie tauschen.", "Re-pair the thermostat or replace its battery.", False))

    cnt = sum(v for _, v in d["shelly_24h"])
    if "shelly" in d["measurements"] and len(d["shelly_24h"]) < 144 * 0.9:
        problems.append((f"Stromzähler: nur {pct(len(d['shelly_24h']) / 144)} % der Zeit gemeldet",
                         f"Electricity meter: reported only {pct(len(d['shelly_24h']) / 144)} % of the time",
                         "Shelly erreichbar? shelly-control-Log prüfen.", "Is the Shelly reachable? Check the shelly-control log.", True))
    if d["weather_last"] and now - d["weather_last"] > 3 * 3600:
        problems.append((f"Wetter: seit {de((now - d['weather_last']) / 3600, 0)} h kein Abruf",
                         f"Weather: no fetch for {en((now - d['weather_last']) / 3600, 0)} h",
                         "Container weather prüfen (Open-Meteo erreichbar?).", "Check the weather container (Open-Meteo reachable?).", False))

    sources = 5
    bad = len({p[0].split(":")[0].split()[0] for p in problems})   # NEXA, Wärmepumpe, Raum, Stromzähler, Wetter
    serious = any(p[4] for p in problems)
    level = "warn" if serious else "info" if problems else "good"
    tde = "; ".join(p[0] for p in problems) if problems else \
        "Alle Quellen haben in den letzten 24 Stunden geliefert, und ihre Werte bewegen sich."
    ten = "; ".join(p[1] for p in problems) if problems else \
        "Every source delivered in the last 24 hours, and its values are moving."
    adv = ({"de": " ".join(dict.fromkeys(p[2] for p in problems)), "en": " ".join(dict.fromkeys(p[3] for p in problems))}
           if problems else None)
    return tile("data_fresh", "data", "short", level, "measured",
                {"de": "Datenfrische", "en": "Data freshness"},
                {"de": f"{max(0, sources - bad)} / {sources}", "en": f"{max(0, sources - bad)} / {sources}"},
                {"de": "Quellen ohne Befund (24 h)", "en": "sources without findings (24 h)"},
                {"de": tde + ".", "en": ten + "."}, adv,
                detail={"problems": len(problems), "shelly_points": int(cnt), "nexa_sleep_h": round(slept / 3600, 1)})


def check_sensors(d):
    """Batterien der Thermostate und das WLAN des NEXA-Dongles – die leisen Ausfälle von morgen."""
    try:
        rooms = json.loads(d["room_state"] or "{}")
    except Exception:
        rooms = {}
    weak, offline = [], []
    for key, r in rooms.items():
        name = r.get("name") or key
        for dev in r.get("devices") or []:
            if dev.get("battery_replace") or (dev.get("battery_text") not in (None, "ok")):
                weak.append(dev.get("label") or name)
            if dev.get("available") is False:
                offline.append(dev.get("label") or name)
    wifi = median([v for _, v in d["wifi"]]) if d["wifi"] else None
    if not rooms and wifi is None:
        return None
    n_dev = sum(len(r.get("devices") or []) for r in rooms.values())
    parts_de, parts_en, adv_de, adv_en = [], [], [], []
    level = "good"
    if offline:
        level = "warn"
        parts_de.append(f"nicht erreichbar: {', '.join(offline)}"); parts_en.append(f"unreachable: {', '.join(offline)}")
        adv_de.append("Thermostat neu verbinden."); adv_en.append("Reconnect the thermostat.")
    if weak:
        level = "warn" if level == "warn" else "info"
        parts_de.append(f"Batterie schwach: {', '.join(weak)}"); parts_en.append(f"battery low: {', '.join(weak)}")
        adv_de.append("Batterien tauschen, bevor das Ventil in der Heizperiode stehen bleibt.")
        adv_en.append("Replace the batteries before the valve stalls in the heating season.")
    if wifi is not None and wifi <= H["wifi_weak"]:
        level = "warn" if (wifi <= H["wifi_poor"] or level == "warn") else "info"
        parts_de.append(f"Dongle-WLAN im Mittel {de(wifi, 0)} dBm"); parts_en.append(f"dongle Wi-Fi median {en(wifi, 0)} dBm")
        adv_de.append("Access Point näher an den NEXA oder einen Repeater – Abbrüche kosten Messwerte und die Regelung.")
        adv_en.append("Move an access point closer to the NEXA or add a repeater – dropouts cost samples and control.")
    if not parts_de:
        parts_de.append(f"alle {n_dev} Thermostate erreichbar, Batterien in Ordnung"
                        + (f", Dongle-WLAN {de(wifi, 0)} dBm" if wifi is not None else ""))
        parts_en.append(f"all {n_dev} thermostats reachable, batteries fine"
                        + (f", dongle Wi-Fi {en(wifi, 0)} dBm" if wifi is not None else ""))
    # Die große Zahl ist die, an der das Urteil hängt: steht nur das WLAN schlecht, ist es der dBm-Wert –
    # ein rotes „12 / 12" läse sich, als fehle ein Thermostat.
    radio = not offline and not weak and wifi is not None and wifi <= H["wifi_weak"]
    return tile("sensors", "data", "short", level, "measured", {"de": "Fühler und Funk", "en": "Sensors and radio"},
                {"de": f"{de(wifi, 0)} dBm", "en": f"{en(wifi, 0)} dBm"} if radio else
                {"de": f"{n_dev - len(offline)} / {n_dev}", "en": f"{n_dev - len(offline)} / {n_dev}"},
                {"de": f"Dongle-WLAN · {n_dev - len(offline)}/{n_dev} Thermostate erreichbar",
                 "en": f"dongle Wi-Fi · {n_dev - len(offline)}/{n_dev} thermostats reachable"} if radio else
                {"de": "Thermostate erreichbar", "en": "thermostats reachable"},
                {"de": upper1("; ".join(parts_de)) + ".", "en": upper1("; ".join(parts_en)) + "."},
                {"de": " ".join(adv_de), "en": " ".join(adv_en)} if adv_de else None,
                detail={"wifi_dbm": round(wifi) if wifi is not None else None})


def check_pressure(d):
    """Anlagendruck jetzt und über die Woche."""
    stale = d["wolf_stale_days"]
    week = [(t, v) for t, v in d["pressure_week"] if day_of(t) not in stale]
    if not week:
        return None
    cur = week[-1][1]
    lo, hi = min(v for _, v in week), max(v for _, v in week)
    if cur < H["p_low"] or lo < H["p_low"] or hi > H["p_high"]:
        level = "warn"
    elif lo < H["p_watch"]:
        level = "info"
    else:
        level = "good"

    def txt(lang):
        f = lambda v: fmt(lang, v, 2)
        if lang == "de":
            s = f"Der Heizkreis steht bei {f(cur)} bar, in den letzten sieben Tagen zwischen {f(lo)} und {f(hi)} bar."
            if level == "good":
                return s + " Das ist der übliche Bereich eines geschlossenen Heizkreises (1,2–2,5 bar)."
            return s + (" Über 2,7 bar ist das Sicherheitsventil nah." if hi > H["p_high"] else
                        " Unter 1 bar schalten Wärmepumpen ab oder ziehen Luft." if lo < H["p_low"] else
                        " Das ist knapp über der Grenze, unter der Luft in den Kreis kommt.")
        s = f"The heating circuit is at {f(cur)} bar, between {f(lo)} and {f(hi)} bar over the last seven days."
        if level == "good":
            return s + " That is the usual range of a sealed heating circuit (1.2–2.5 bar)."
        return s + (" Above 2.7 bar the safety valve is close." if hi > H["p_high"] else
                    " Below 1 bar heat pumps shut down or draw in air." if lo < H["p_low"] else
                    " That is just above the point where air gets into the circuit.")

    adv = None
    if hi > H["p_high"]:
        adv = {"de": "Ausdehnungsgefäß prüfen lassen (Vordruck) – ein zu hoher Druck beim Aufheizen heißt meist, dass es kein Polster mehr hat.",
               "en": "Have the expansion vessel checked (pre-charge) – high pressure when heating up usually means it lost its cushion."}
    elif level != "good":
        adv = {"de": "Heizungswasser nachfüllen (kalt auf 1,5 bar) und beobachten, wie schnell es wieder fällt – das sagt die Langzeitkachel.",
               "en": "Top up the heating water (1.5 bar cold) and watch how fast it drops again – the long-term tile tells."}
    return tile("pressure", "heat", "short", level, "measured", {"de": "Anlagendruck", "en": "System pressure"},
                {"de": f"{de(cur, 2)} bar", "en": f"{en(cur, 2)} bar"},
                {"de": f"7 Tage: {de(lo, 2)}–{de(hi, 2)} bar", "en": f"7 days: {en(lo, 2)}–{en(hi, 2)} bar"},
                both(txt), adv, detail={"min_7d": lo, "max_7d": hi})


def check_pressure_trend(d):
    """Druckverlust über Wochen: das langsame Leck, das man erst bemerkt, wenn die Anlage auf Störung geht.

    Verglichen wird das Tagesminimum. Der Druck steigt mit der Wassertemperatur, und das Minimum liegt
    meist in der kältesten Stunde – damit ist der Temperatureffekt nicht weg, aber deutlich kleiner als
    beim Mittelwert. Eingefrorene Tage fallen heraus. Ein Sprung nach oben ist Nachfüllen; ab dort beginnt
    die Gerade neu, sonst rechnet sie das Nachfüllen gegen den Verlust.
    """
    stale = d["wolf_stale_days"]
    pts = [(t, v) for t, v in d["pressure_days"] if day_of(t) not in stale and day_of(t) != day_of(d["now"])]
    if not pts:
        return None
    start = 0
    for i in range(1, len(pts)):
        if pts[i][1] - pts[i - 1][1] >= 0.25:      # nachgefüllt
            start = i
    seg = pts[start:]
    span = (seg[-1][0] - seg[0][0]) / 86400 if len(seg) > 1 else 0
    s = slope_per_day([((t - seg[0][0]) / 86400, v) for t, v in seg])
    first = day_of(seg[0][0])
    ready = plus_days(first, H["trend_min_days"])
    if s is None or span < H["trend_min_days"]:
        return tile("pressure_trend", "heat", "long", "info", "thin", {"de": "Druckverlust", "en": "Pressure loss"},
                    # Solange die Gerade dünn ist, steht die gemessene Spanne in der Kachel, nicht ihre
                    # Hochrechnung auf einen Monat – aus neun Herbsttagen wird sonst ein Leck.
                    {"de": f"{de(seg[0][1], 2)} → {de(seg[-1][1], 2)} bar", "en": f"{en(seg[0][1], 2)} → {en(seg[-1][1], 2)} bar"},
                    {"de": f"{de(span, 0)} von {H['trend_min_days']} Tagen", "en": f"{en(span, 0)} of {H['trend_min_days']} days"},
                    {"de": f"Die Gerade durch die Tagesminima reicht erst über {de(span, 0)} Tage. Im Herbst steigt die "
                           f"Wassertemperatur, und das verschiebt den Druck stärker als ein kleines Leck – belastbar ab {day_de(ready)}.",
                     "en": f"The line through the daily minima only spans {en(span, 0)} days. In autumn the water temperature rises, "
                           f"which moves the pressure more than a small leak would – reliable from {ready}."},
                    ready=ready, detail={"days": round(span)})
    per30 = s * 30
    loss = -per30
    level = "warn" if loss >= H["p_loss_warn"] else "info" if loss >= H["p_loss_watch"] else "good"
    cur = seg[-1][1]
    weeks = (cur - H["p_low"]) / (loss / 30) / 7 if loss > 0.01 and cur > H["p_low"] else None

    def txt(lang):
        f = lambda v, n=2: fmt(lang, v, n)
        if lang == "de":
            s1 = f"Über {f(span, 0)} Tage verliert der Heizkreis {f(loss)} bar im Monat." if loss > 0 else \
                 f"Über {f(span, 0)} Tage hält der Heizkreis seinen Druck ({f(per30)} bar im Monat)."
            if weeks is not None and level != "good":
                s1 += f" In diesem Tempo ist er in etwa {f(weeks, 0)} Wochen unter 1 bar."
            return s1
        s1 = f"Over {f(span, 0)} days the heating circuit loses {f(loss)} bar a month." if loss > 0 else \
             f"Over {f(span, 0)} days the heating circuit holds its pressure ({f(per30)} bar a month)."
        if weeks is not None and level != "good":
            s1 += f" At this rate it drops below 1 bar in about {f(weeks, 0)} weeks."
        return s1

    adv = None if level == "good" else {
        "de": "Sichtprüfung an Entlüftern, Ventilen und Heizkörperanschlüssen; wenn nichts tropft, den Vorordruck des "
              "Ausdehnungsgefäßes prüfen lassen. Jedes Nachfüllen bringt Sauerstoff in den Kreis.",
        "en": "Inspect air vents, valves and radiator joints; if nothing drips, have the expansion vessel pre-charge "
              "checked. Every top-up brings oxygen into the circuit."}
    return tile("pressure_trend", "heat", "long", level, "measured", {"de": "Druckverlust", "en": "Pressure loss"},
                {"de": f"{de(per30, 2)} bar/Monat", "en": f"{en(per30, 2)} bar/month"},
                {"de": f"seit {day_de(first)}, {de(span, 0)} Tage", "en": f"since {first}, {en(span, 0)} days"},
                both(txt), adv, detail={"days": round(span)})


def room_hours(d):
    out = {}
    for r in d["rooms_h"]:
        t, sp, rh = num(r[2]), num(r[3]), num(r[4])
        if t is None:
            continue
        out.setdefault(r[0], []).append((int(float(r[1])), t, sp, rh))
    return out


def check_mould(d):
    """Schimmelrisiko je Raum: Feuchte an der kältesten zulässigen Stelle der Außenwand, nicht im Raum.

    Die Raumluft mit 60 % ist harmlos, solange keine Wand kalt genug ist, dass dort 80 % daraus werden.
    Wie kalt die Wand ist, misst hier niemand – geschätzt wird sie über f_Rsi = 0,7 (DIN 4108-2) aus Raum-
    und Außentemperatur. Kellerräume grenzen ans Erdreich, nicht an die Außenluft; dort gilt 10 °C.
    Die Thermostate sitzen am Heizkörper: sie messen etwas zu warm und damit etwas zu trocken. Die Schätzung
    ist also eher zu milde als zu streng – ein Grund mehr, einen Befund ernst zu nehmen.
    """
    rooms = room_hours(d)
    wx = d["weather_h"]
    res = []
    for room, hrs in rooms.items():
        hits = n = 0
        rhs = []
        for ts, t, sp, rh in hrs:
            if rh is None:
                continue
            rhs.append(rh)
            te = H["ground_c"] if "keller" in room.lower() else wx.get(ts)
            if te is None:
                continue
            tsi = te + H["f_rsi"] * (t - te)
            rh_s = rh * psat(t) / psat(tsi)
            n += 1
            hits += rh_s >= H["surface_rh"]
        if n >= 24:
            res.append((room, hits / n, median(rhs) if rhs else None, n))
    if not res:
        return None
    res.sort(key=lambda x: (-x[1], -(x[2] or 0)))
    worst = res[0]
    at_risk = [r for r in res if r[1] >= H["mould_watch_share"]]
    humid = [r for r in res if (r[2] or 0) >= H["rh_watch"]]
    level = ("warn" if worst[1] >= H["mould_warn_share"] else
             "info" if at_risk or humid else "good")

    def txt(lang):
        if lang == "de":
            if at_risk:
                lst = ", ".join(f"{r[0]} {pct(r[1])} %" for r in at_risk[:4])
                s = (f"An der kältesten zulässigen Stelle der Außenwand lag die Feuchte in diesen Räumen über 80 % – "
                     f"Anteil der Stunden der letzten Woche: {lst}.")
            else:
                s = "In keinem Raum wäre die Außenwand in der letzten Woche feucht genug für Schimmel gewesen."
            if humid:
                s += " Dauerhaft feucht (Median ≥ 65 %): " + ", ".join(f"{r[0]} {de(r[2], 0)} %" for r in humid[:4]) + "."
            return s + " Je kälter es draußen wird, desto schneller kippt das."
        if at_risk:
            lst = ", ".join(f"{r[0]} {pct(r[1])} %" for r in at_risk[:4])
            s = f"At the coldest permissible spot of the outer wall, humidity exceeded 80 % in these rooms – share of last week's hours: {lst}."
        else:
            s = "In no room would the outer wall have been damp enough for mould last week."
        if humid:
            s += " Persistently humid (median ≥ 65 %): " + ", ".join(f"{r[0]} {en(r[2], 0)} %" for r in humid[:4]) + "."
        return s + " The colder it gets outside, the faster this tips."

    adv = None
    if level != "good":
        cellar = any("keller" in r[0].lower() for r in at_risk + humid)
        adv = {"de": "Stoßlüften statt Kipp (3× täglich 5–10 min), Möbel 5 cm von Außenwänden abrücken."
                     + (" Im Keller im Sommer nicht warm lüften – das bringt Wasser hinein; ein Entfeuchter mit Zähler zeigt, wie viel." if cellar else ""),
               "en": "Purge-ventilate instead of tilting windows (3× a day for 5–10 min), keep furniture 5 cm off outer walls."
                     + (" Do not ventilate the cellar with warm summer air – that brings water in; a dehumidifier with a meter shows how much." if cellar else "")}
    return tile("mould", "rooms", "short", level, "modelled", {"de": "Schimmelrisiko", "en": "Mould risk"},
                {"de": f"{pct(worst[1])} %", "en": f"{pct(worst[1])} %"},
                {"de": f"der Stunden, {worst[0]}", "en": f"of hours, {worst[0]}"},
                both(txt), adv, detail={"rooms": len(res), "f_rsi": H["f_rsi"]})


def heating_season(d):
    wx = d["weather_h"]
    week = [v for t, v in wx.items() if t > d["now"] - 7 * 86400 and v is not None]
    return (sum(week) / len(week)) if week else None


def check_rooms(d):
    """Räume, die ihren Sollwert nicht erreichen, und Räume, die zu kalt werden."""
    rooms = room_hours(d)
    if not rooms:
        return None
    t_mean = heating_season(d)
    season = t_mean is not None and t_mean < H["heating_mean_c"]
    under, cold = [], []
    for room, hrs in rooms.items():
        req = [(t, sp) for _, t, sp, _ in hrs if sp is not None and sp >= 16]
        if len(req) >= 24:
            share = sum(1 for t, sp in req if t < sp - H["under_k"]) / len(req)
            if share >= H["under_share"]:
                under.append((room, share, median([sp for _, sp in req]), median([t for t, _ in req])))
        tmin = min(t for _, t, _, _ in hrs)
        if tmin < H["cold_c"]:
            cold.append((room, tmin))
    level = "warn" if cold else ("info" if under else "good")
    basis = "measured" if season else "thin"

    def txt(lang):
        f = lambda v, n=1: fmt(lang, v, n)
        parts = []
        if lang == "de":
            for r, share, sp, t in under:
                parts.append(f"{r} bleibt {pct(share)} % der Zeit mehr als 1 K unter Soll ({f(t)} statt {f(sp)} °C)")
            for r, tmin in cold:
                parts.append(f"{r} fiel auf {f(tmin)} °C")
            s = ("; ".join(parts) + ".") if parts else "Alle Räume erreichen ihren Sollwert."
            if not season:
                s += f" Draußen waren es im Wochenmittel {f(t_mean)} °C – die Heizperiode hat kaum begonnen, das Urteil zählt erst bei Kälte."
            return s
        for r, share, sp, t in under:
            parts.append(f"{r} stays more than 1 K below its setpoint {pct(share)} % of the time ({f(t)} instead of {f(sp)} °C)")
        for r, tmin in cold:
            parts.append(f"{r} dropped to {f(tmin)} °C")
        s = ("; ".join(parts) + ".") if parts else "Every room reaches its setpoint."
        if not season and t_mean is not None:
            s += f" Outside the weekly mean was {f(t_mean)} °C – the heating season has barely begun; the verdict counts once it is cold."
        return s

    adv = None
    if under or cold:
        adv = {"de": "Ein Soll, das nie erreicht wird, hält das Ventil dauerhaft offen und zieht den Vorlauf für das ganze Haus hoch. "
                     "Soll auf das Erreichbare senken, oder Heizkörper/Ventil prüfen (klemmender Stift, Luft im Heizkörper)."
                     + (" Unter 16 °C droht an Außenwänden Tauwasser." if cold else ""),
               "en": "A setpoint that is never reached keeps the valve open and pulls up the flow temperature for the whole house. "
                     "Lower it to what is achievable, or check radiator/valve (stuck pin, air in the radiator)."
                     + (" Below 16 °C condensation on outer walls becomes likely." if cold else "")}
    n_ok = len(rooms) - len({u[0] for u in under} | {c[0] for c in cold})
    return tile("rooms", "rooms", "short", level, basis, {"de": "Räume am Soll", "en": "Rooms at setpoint"},
                {"de": f"{n_ok} / {len(rooms)}", "en": f"{n_ok} / {len(rooms)}"},
                {"de": "Räume ohne Befund (7 Tage)", "en": "rooms without findings (7 days)"},
                both(txt), adv, detail={"t_out_7d": round(t_mean, 1) if t_mean is not None else None})


def check_dhw(d):
    """Puffer für die Frischwasserstation: reicht die Temperatur oben, damit warmes Wasser aus dem Hahn kommt?

    Keine Hygienefrage – das Trinkwasser steht nicht, es wird im Durchlauf erwärmt. Gefragt ist nur der
    Komfort: fällt der Puffer oben unter Zapftemperatur plus Abstand, wird die Dusche lauwarm. Gezählt wird
    das Tagesmaximum, also ob die Wärmepumpe ihn jeden Tag wieder hochgeladen hat.
    """
    stale = d["wolf_stale_days"]
    days = [(t, v) for t, v in d["dhw_days"] if day_of(t) not in stale]
    if not days:
        return None
    mx = [v for _, v in days]
    top, low = max(mx), min(mx)
    need = H["buf_tap_c"] + H["buf_margin_k"]
    short = sum(1 for v in mx if v < need)
    level = "warn" if low < H["buf_tap_c"] else "info" if short else "good"

    def txt(lang):
        f = lambda v: fmt(lang, v, 1)
        if lang == "de":
            s = (f"Der Puffer erreichte oben an jedem der letzten {len(days)} Tage mindestens {f(low)} °C, höchstens {f(top)} °C. "
                 f"Die Frischwasserstation braucht etwa {f(need)} °C für {f(H['buf_tap_c'])} °C am Hahn.")
            if level == "warn":
                s += " An mindestens einem Tag reichte es nicht einmal für die Zapftemperatur."
            elif short:
                s += f" An {short} Tag(en) blieb er darunter – dann kommt das Wasser etwas kühler."
            return s + " Legionellen sind bei einer Frischwasserstation kein Thema."
        s = (f"The buffer top reached at least {f(low)} °C on each of the last {len(days)} days, at most {f(top)} °C. "
             f"The fresh-water station needs about {f(need)} °C for {f(H['buf_tap_c'])} °C at the tap.")
        if level == "warn":
            s += " On at least one day it did not even reach the tap temperature."
        elif short:
            s += f" On {short} day(s) it stayed below – the water then comes out a little cooler."
        return s + " Legionella are not an issue with a fresh-water station."

    adv = None if level == "good" else {
        "de": "Warmwasser-Soll der Wolf um 2–3 K anheben oder die Ladezeit in die Mittagsstunden legen, wenn dort PV da ist.",
        "en": "Raise the Wolf's hot water setpoint by 2–3 K or move the charging window to midday when PV is available."}
    return tile("dhw", "heat", "short", level, "measured", {"de": "Puffer für Warmwasser", "en": "Hot water buffer"},
                {"de": f"{de(low)} °C", "en": f"{en(low)} °C"},
                {"de": f"niedrigstes Tagesmaximum, {len(days)} Tage", "en": f"lowest daily maximum, {len(days)} days"},
                both(txt), adv, detail={"set_c": d["dhw_set"]})


def check_cycling(cyc):
    """Taktung: das Urteil der wolf-bridge, nicht neu gerechnet."""
    if not isinstance(cyc, dict) or not isinstance(cyc.get("verdict"), dict):
        return None
    v = cyc["verdict"]
    sev = v.get("severity")
    level = "good" if sev == "ok" else "warn" if sev in ("bad", "warn") else "info"
    per_day, med = cyc.get("per_day"), cyc.get("median_min")
    spy = cyc.get("starts_per_year")
    return tile("cycling", "heat", "short", level, "measured", {"de": "Taktung", "en": "Cycling"},
                {"de": f"{de(per_day)} /Tag" if per_day is not None else "–", "en": f"{en(per_day)} /day" if per_day is not None else "–"},
                {"de": f"Median {de(med)} min je Lauf" + (f", ≈ {int(spy)} Starts/Jahr" if spy else ""),
                 "en": f"median {en(med)} min per run" + (f", ≈ {int(spy)} starts/year" if spy else "")} if med is not None else None,
                {"de": v.get("de") or "", "en": v.get("en") or ""},
                None if level == "good" else {"de": "Hysterese oder Heizkurve prüfen – kurze Läufe verschleißen den Verdichter.",
                                              "en": "Check hysteresis or heating curve – short runs wear the compressor."},
                detail={"window_days": cyc.get("window_days")})


def check_battery(d):
    """Akku kurzfristig: Temperatur und wann er zuletzt voll war.

    LFP-Zellen haben eine flache Spannungskurve; das BMS kann den Ladestand nur am oberen Ende sicher
    nachstellen. Ein Speicher, der wochenlang nie voll wird, zeigt einen Ladestand, der immer weiter
    vom echten abweicht – und die Zellen werden nicht ausgeglichen.
    """
    rows = [r for r in d["nexa"] if r[0] and r[0] > d["now"] - 7 * 86400]
    if not rows:
        return None
    temps = [v for r in rows for v in (r[2], r[3]) if v is not None and v > -40]
    tmin, tmax = (min(temps), max(temps)) if temps else (None, None)
    lf = d["last_full"]
    days_full = (d["now"] - lf) / 86400 if lf else ((d["now"] - d["first_ts"]) / 86400 if d["first_ts"] else None)
    lim = d["soc_limit"]
    at_floor = None
    if lim is not None:
        socs = [r[1] for r in rows if r[1] is not None]
        at_floor = sum(1 for s in socs if s <= lim + 1) / len(socs) if socs else None
    hot = tmax is not None and tmax >= H["bat_hot"]
    cold = tmin is not None and tmin <= H["bat_cold"]
    stale_full = days_full is not None and days_full >= H["full_days"]
    level = "warn" if (hot or cold) else "info" if stale_full else "good"

    def txt(lang):
        f = lambda v, n=0: fmt(lang, v, n)
        if lang == "de":
            s = f"Zelltemperatur in der Woche {f(tmin)}–{f(tmax)} °C." if tmin is not None else ""
            if days_full is not None:
                s += (f" Zuletzt voll vor {f(days_full)} Tagen." if lf else f" Seit Messbeginn ({f(days_full)} Tage) nie voll.")
            if at_floor is not None:
                s += f" {pct(at_floor)} % der Zeit an der Entladegrenze ({f(lim)} %)."
            if stale_full:
                s += " Ohne gelegentliche Vollladung driftet die Ladestandsanzeige, und die Zellen werden nicht ausgeglichen."
            if hot:
                s += " Über 45 °C altert LFP spürbar schneller."
            if cold:
                s += " Unter 0 °C darf LFP nicht geladen werden."
            return s.strip()
        s = f"Cell temperature this week {f(tmin)}–{f(tmax)} °C." if tmin is not None else ""
        if days_full is not None:
            s += (f" Last full {f(days_full)} days ago." if lf else f" Never full since measurements began ({f(days_full)} days).")
        if at_floor is not None:
            s += f" {pct(at_floor)} % of the time at the discharge limit ({f(lim)} %)."
        if stale_full:
            s += " Without an occasional full charge the state-of-charge reading drifts and the cells are not balanced."
        if hot:
            s += " Above 45 °C LFP ages noticeably faster."
        if cold:
            s += " LFP must not be charged below 0 °C."
        return s.strip()

    adv = None
    if stale_full:
        adv = {"de": "An einem sonnigen Tag die Abgabe drosseln (Regler aus, Ausgang niedrig), bis der Speicher 100 % erreicht – einmal im Monat genügt.",
               "en": "On a sunny day throttle the output (controller off, low output) until the battery reaches 100 % – once a month is enough."}
    elif hot:
        adv = {"de": "Aufstellort prüfen: keine direkte Sonne, Luft zum Abführen der Wärme.",
               "en": "Check the location: no direct sun, room for the heat to escape."}
    elif cold:
        adv = {"de": "Frostfreien Aufstellort wählen oder im Winter die Ladung über Nacht verhindern.",
               "en": "Choose a frost-free location or avoid charging on frosty nights."}
    return tile("battery", "battery", "short", level, "measured", {"de": "Akku", "en": "Battery"},
                {"de": f"{de(days_full, 0)} Tage" if days_full is not None else "–",
                 "en": f"{en(days_full, 0)} days" if days_full is not None else "–"},
                {"de": "seit der letzten Vollladung", "en": "since the last full charge"},
                both(txt), adv, detail={"t_min": tmin, "t_max": tmax})


def check_battery_aging(d):
    """Akku langfristig: Zyklen im Jahr gegen die Zyklen, für die er gebaut ist, und das SoH des BMS."""
    cyc = d["cycles"]
    if len(cyc) < 2:
        return None
    first_t, first_c = cyc[0]
    last_t, last_c = cyc[-1]
    span = (last_t - first_t) / 86400
    per_year = (last_c - first_c) / span * 365 if span > 0 else None
    soh = d["soh"][-1][1] if d["soh"] else None
    soh0 = d["soh"][0][1] if d["soh"] else None
    start = day_of(first_t)
    ready = plus_days(start, 365)
    thin = span < 365
    years = H["rated_cycles"] / per_year if per_year else None

    def txt(lang):
        f = lambda v, n=0: fmt(lang, v, n)
        if lang == "de":
            s = f"{f(last_c)} Zyklen in {f(span)} Tagen, hochgerechnet etwa {f(per_year)} im Jahr." if per_year else f"{f(last_c)} Zyklen."
            if years:
                s += (f" Bis zu den {H['rated_cycles']} Zyklen des Datenblatts wären es {f(years)} Jahre – "
                      "die Zeit altert diesen Akku, nicht die Benutzung.")
            if soh is not None:
                s += f" Das BMS meldet {f(soh)} % Gesundheit" + (f" (zu Beginn {f(soh0)} %)." if soh0 != soh else ".")
            if thin:
                s += f" Die Hochrechnung kennt nur Spätsommer und Herbst – ein Jahr gibt es ab {day_de(ready)}."
            return s
        s = f"{f(last_c)} cycles in {f(span)} days, about {f(per_year)} a year when extrapolated." if per_year else f"{f(last_c)} cycles."
        if years:
            s += (f" Reaching the data sheet's {H['rated_cycles']} cycles would take {f(years)} years – "
                  "time ages this battery, not use.")
        if soh is not None:
            s += f" The BMS reports {f(soh)} % health" + (f" (initially {f(soh0)} %)." if soh0 != soh else ".")
        if thin:
            s += f" The extrapolation only knows late summer and autumn – a full year exists from {ready}."
        return s

    level = "warn" if soh is not None and soh < 80 else "info" if soh is not None and soh0 and soh0 - soh >= 5 else "good"
    return tile("battery_aging", "battery", "long", level, "thin" if thin else "measured",
                {"de": "Akku-Alterung", "en": "Battery ageing"},
                {"de": f"{de(per_year, 0)} Zyklen/Jahr" if per_year else "–", "en": f"{en(per_year, 0)} cycles/yr" if per_year else "–"},
                {"de": f"SoH {de(soh, 0)} %" if soh is not None else "", "en": f"SoH {en(soh, 0)} %" if soh is not None else ""},
                both(txt), None, ready=ready if thin else None, detail={"cycles": last_c})


def nightly_base(points):
    """Je Nacht das 10-%-Quantil der 10-Minuten-Mittel zwischen 1 und 5 Uhr.

    Das Quantil statt des Mittels: die Wärmepumpe läuft nachts gelegentlich an, und das ist kein
    Standby. Die ruhigsten zehn Minuten einer Nacht sind, was das Haus braucht, wenn niemand etwas will.
    """
    nights = {}
    for t, v in points:
        lt = time.localtime(t)
        if 1 <= lt.tm_hour < 5 and v is not None and v >= 0:
            nights.setdefault(time.strftime("%Y-%m-%d", lt), []).append(v)
    out = {}
    for day, vs in nights.items():
        if len(vs) >= 12:
            s = sorted(vs)
            out[day] = s[int(len(s) * 0.1)]
    return out


def check_base_load(d, price_ct):
    nights = nightly_base(d["shelly_10m"])
    if len(nights) < 3:
        return None
    days = sorted(nights)
    recent = [nights[k] for k in days[-7:]]
    earlier = [nights[k] for k in days[-37:-7]]
    cur = median(recent)
    ref = median(earlier) if len(earlier) >= 7 else None
    rise = (cur - ref) / ref if ref else None
    eur = cur * 8.76 * price_ct / 100
    level = "good"
    if rise is not None and cur - ref >= H["base_min_w"]:
        level = "warn" if rise >= H["base_rise_warn"] else "info" if rise >= H["base_rise_watch"] else "good"

    def txt(lang):
        f = lambda v, n=0: fmt(lang, v, n)
        if lang == "de":
            s = f"Nachts zwischen 1 und 5 Uhr braucht das Haus mindestens {f(cur)} W – das sind {f(cur * 8.76)} kWh oder {f(eur)} € im Jahr, ohne dass jemand etwas tut."
            if ref is not None:
                s += f" Die 30 Tage davor waren es {f(ref)} W" + (f" ({'+' if rise >= 0 else ''}{pct(rise)} %)." if rise is not None else ".")
            return s
        s = f"At night between 1 and 5 am the house needs at least {f(cur)} W – {f(cur * 8.76)} kWh or €{f(eur)} a year with nobody doing anything."
        if ref is not None:
            s += f" The 30 days before it was {f(ref)} W" + (f" ({'+' if rise >= 0 else ''}{pct(rise)} %)." if rise is not None else ".")
        return s

    adv = None
    if level != "good":
        adv = {"de": "Was ist neu dazugekommen? Eine Nacht lang Sicherungen einzeln aus und die Grundlast beobachten – jedes Watt sind 2,6 € im Jahr.",
               "en": "What is new? One night, switch breakers off one at a time and watch the base load – every watt costs about €2.60 a year."}
    elif cur > 150:
        adv = {"de": f"{de(cur, 0)} W Grundlast ist viel für ein Einfamilienhaus (typisch 50–150 W). Standby-Verbraucher mit einer Messsteckdose suchen.",
               "en": f"{cur:.0f} W base load is a lot for a single-family home (typically 50–150 W). Hunt standby loads with a metering plug."}
    return tile("base_load", "power", "short", level, "measured", {"de": "Grundlast", "en": "Base load"},
                {"de": f"{de(cur, 0)} W", "en": f"{en(cur, 0)} W"},
                {"de": f"≈ {de(eur, 0)} €/Jahr", "en": f"≈ €{en(eur, 0)}/year"},
                both(txt), adv, detail={"nights": len(recent)})


def check_base_trend(d):
    """Grundlast über Monate. Ein Monat ist hier die kleinste Einheit, die etwas sagt."""
    nights = nightly_base(d["shelly_10m"])
    months = {}
    for day, v in nights.items():
        months.setdefault(day[:7], []).append(v)
    per = [(m, median(v)) for m, v in sorted(months.items()) if len(v) >= 10]
    if not per:
        return None
    first = min(nights)
    ready = plus_days(first, 90)
    thin = len(per) < 3

    def txt(lang):
        f = lambda v: fmt(lang, v, 0)
        lst = ", ".join(f"{m[5:]}/{m[2:4]}: {f(v)} W" for m, v in per[-6:])
        if lang == "de":
            s = f"Monatliche Grundlast: {lst}."
            return s + (f" Ein Trend braucht drei Monate – ab {day_de(ready)}. Im Winter steigt sie oft durch Umwälzpumpen und Licht." if thin
                        else " Ein stetiger Anstieg über Monate ist fast immer ein neues Gerät oder ein alterndes (Kühlschrank, Pumpe).")
        s = f"Monthly base load: {lst}."
        return s + (f" A trend needs three months – from {ready}. It often rises in winter from circulation pumps and lighting." if thin
                    else " A steady rise over months is almost always a new appliance or an ageing one (fridge, pump).")

    level = "good"
    if not thin and per[-1][1] - per[0][1] >= H["base_min_w"] and per[-1][1] / per[0][1] - 1 >= H["base_rise_watch"]:
        level = "info"
    return tile("base_trend", "power", "long", level, "thin" if thin else "measured",
                {"de": "Grundlast über Monate", "en": "Base load over months"},
                {"de": f"{de(per[-1][1], 0)} W", "en": f"{en(per[-1][1], 0)} W"},
                {"de": f"{len(per)} Monat(e)", "en": f"{len(per)} month(s)"}, both(txt), None,
                ready=ready if thin else None)


def installed_wp(d):
    try:
        strings = json.loads(d["strings"] or "{}")
    except Exception:
        return {}, None
    wp = sum(num(v.get("wp")) or 0 for v in strings.values() if isinstance(v, dict))
    return strings, (wp or None)


def pv_days(d):
    """Tage mit Ist- und Modellenergie, nur aus Stunden, in denen nichts abgeregelt wird.

    Zwei Dinge drücken den Ertrag, ohne dass an den Modulen etwas fehlt: ein voller Speicher, und eine
    Nulleinspeisung, die mehr Sonne anbietet, als Haus und Akku abnehmen (am 30.09.2026 erwartete das
    Modell 8 kWh, es kamen 3 – bei 79 % Ladestand). Beides passiert bei viel Licht. Gezählt werden
    deshalb nur die Stunden, in denen das Modell unter 35 % der installierten Leistung erwartet –
    dort nimmt der Akku alles, und was fehlt, fehlt wirklich (Schmutz, Laub, ein loser Stecker).
    """
    _, wp = installed_wp(d)
    cap = 0.35 * wp if wp else None
    days = {}
    for r in d["pv_hours"]:
        t, w, e, soc, n = int(float(r[0])), num(r[1]), num(r[2]), num(r[3]), num(r[4])
        if w is None or e is None or (soc is not None and soc >= 97) or (n or 0) < 30:
            continue
        if cap is not None and e > cap:
            continue
        x = days.setdefault(day_of(t), [0.0, 0.0])
        x[0] += w; x[1] += e
    return {k: v for k, v in days.items() if v[1] >= 150}   # Tage mit unter 0,15 kWh Modell sagen nichts


def check_pv(d):
    """PV gegen das eigene Modell: plötzlicher Einbruch (Schmutz, Laub, Defekt) und tote Strings."""
    days = pv_days(d)
    keys = sorted(k for k in days if k != day_of(d["now"]))
    if len(keys) < 3:
        return None
    ratio = lambda ks: (sum(days[k][0] for k in ks) / sum(days[k][1] for k in ks)) if ks else None
    rec = keys[-7:]; ref_k = keys[-37:-7]
    r_now = ratio(rec); r_ref = ratio(ref_k) if len(ref_k) >= 7 else None
    drop = 1 - r_now / r_ref if r_ref else None
    # tote Strings: Stunden mit nennenswertem Modell, in denen ein String unter 5 % liefert, die anderen nicht
    strings, _ = installed_wp(d)
    dead = []
    since = d["now"] - 7 * 86400
    for i in range(1, 5):
        if str(i) not in strings:
            continue
        hits = n = 0
        for r in d["pv_hours"]:
            if int(float(r[0])) < since:
                continue
            act, exp = num(r[4 + i]), num(r[8 + i])
            if act is None or not exp or exp < 80:
                continue
            n += 1
            hits += act < exp * H["string_dead_share"]
        if n >= 10 and hits / n >= 0.5:
            dead.append(i)
    level = "warn" if dead or (drop is not None and drop >= H["pv_drop_warn"]) else \
        "info" if drop is not None and drop >= H["pv_drop_watch"] else "good"

    def txt(lang):
        f = lambda v, n=0: fmt(lang, v, n)
        if lang == "de":
            s = (f"In den Schwachlicht-Stunden der letzten {len(rec)} Tage kam {pct(r_now)} % dessen, was das Modell für Wetter "
                 "und Sonnenstand erwartet (Stunden mit viel Licht zählen nicht – dort regelt die Nulleinspeisung ab).")
            if r_ref:
                s += f" Die Wochen davor waren es {pct(r_ref)} %."
            if dead:
                s += " String " + ", ".join(map(str, dead)) + " liefert in der Hälfte der Sonnenstunden fast nichts."
            if level == "good":
                s += " Kein Einbruch."
            return s
        s = (f"In the low-light hours of the last {len(rec)} days the array delivered {pct(r_now)} % of what the model expects "
             "for weather and sun position (bright hours do not count – zero feed-in curtails there).")
        if r_ref:
            s += f" The weeks before it was {pct(r_ref)} %."
        if dead:
            s += " String " + ", ".join(map(str, dead)) + " delivers almost nothing in half of the sunny hours."
        if level == "good":
            s += " No drop."
        return s

    adv = None
    if dead:
        adv = {"de": "Stecker und Kabel des Strings prüfen (MC4), dann die String-Spannung am NEXA.",
               "en": "Check the string's connectors and cable (MC4), then its voltage at the NEXA."}
    elif level != "good":
        adv = {"de": "Laub, Vogelkot oder Pollen auf den Modulen? Im Herbst kommt dazu der tiefere Sonnenstand hinter der Eiche – die Verschattungskarte zeigt, ob es das ist.",
               "en": "Leaves, bird droppings or pollen on the modules? In autumn the lower sun behind the oak adds to it – the shading map shows whether that is it."}
    return tile("pv", "pv", "short", level, "measured", {"de": "PV-Ertrag gegen Modell", "en": "PV yield vs model"},
                {"de": f"{pct(r_now)} %", "en": f"{pct(r_now)} %"},
                {"de": f"vorher {pct(r_ref)} %" if r_ref else f"{len(rec)} Tage", "en": f"before {pct(r_ref)} %" if r_ref else f"{len(rec)} days"},
                both(txt), adv, detail={"dead_strings": len(dead)})


def check_pv_long(d):
    """PV über Monate: ob die Anlage gegenüber dem eigenen Modell nachlässt. Eine Degradationsrate gibt
    es erst im Vergleich gleicher Monate – also nach einem Jahr."""
    days = pv_days(d)
    months = {}
    for k, (a, e) in days.items():
        x = months.setdefault(k[:7], [0.0, 0.0, 0]); x[0] += a; x[1] += e; x[2] += 1
    per = [(m, x[0] / x[1]) for m, x in sorted(months.items()) if x[2] >= 7]
    if not per:
        return None
    first = min(days)
    ready = plus_days(first, 395)

    def txt(lang):
        lst = ", ".join(f"{m[5:]}/{m[2:4]}: {pct(r)} %" for m, r in per[-6:])
        if lang == "de":
            return (f"Ist gegen Modell je Monat: {lst}. Das Verhältnis schwankt mit Laub, Schnee und Sonnenstand; "
                    f"die Alterung der Module (typisch 0,3–0,5 % im Jahr) zeigt erst der Vergleich gleicher Monate – ab {day_de(ready)}.")
        return (f"Actual against model per month: {lst}. The ratio moves with leaves, snow and sun height; "
                f"module ageing (typically 0.3–0.5 % a year) only shows when comparing the same months – from {ready}.")

    return tile("pv_long", "pv", "long", "info", "thin", {"de": "PV-Alterung", "en": "PV ageing"},
                {"de": f"{pct(per[-1][1])} %", "en": f"{pct(per[-1][1])} %"},
                {"de": "Ist/Modell im letzten Monat", "en": "actual/model, last month"}, both(txt), None, ready=ready)


def check_heat_demand(d):
    """Wärme je Gradtag: wie viel das Haus für dasselbe Wetter braucht.

    Heizgradtage über 15 °C Heizgrenze aus dem Wetterverlauf, Wärme aus den Tageszählern der Wolf.
    Zwei Fehlerquellen fliegen raus: Tage, an denen ism7mqtt eingefroren war, und Tage, die exakt die
    Zahlen des Vortags tragen – dann hat der Tageszähler nicht zurückgesetzt, und es ist dieselbe Wärme
    zweimal. Warmwasser steckt mit drin; im Sommer ist es fast alles, darum zählen nur Tage mit Gradtagen.
    """
    hdd = {}
    for t, v in d["weather_h"].items():
        if v is None:
            continue
        x = hdd.setdefault(day_of(t), [0.0, 0]); x[0] += v; x[1] += 1
    hdd = {k: max(0.0, H["hdd_base"] - x[0] / x[1]) for k, x in hdd.items() if x[1] >= 20}
    stale = d["wolf_stale_days"]
    rows, prev, dropped = [], None, 0
    today = day_of(d["now"])
    for day, hk, ek in d["heat_days"]:
        hk = num(hk)
        if hk is None or day == today:
            continue
        cur = (hk, num(ek))
        if day in stale or cur == prev:
            dropped += 1
        elif day in hdd and hdd[day] > 0.5:
            rows.append((day, hk, hdd[day]))
        prev = cur
    if not rows:
        return None
    rec = rows[-14:]
    kd = sum(r[2] for r in rec)
    if kd <= 0:
        return None
    per = sum(r[1] for r in rec) / kd
    first = rows[0][0]
    ready = plus_days(first, 365)
    thin = kd < H["hdd_min"]

    def txt(lang):
        f = lambda v, n=1: fmt(lang, v, n)
        if lang == "de":
            s = f"Über {len(rec)} Heiztage mit zusammen {f(kd, 0)} Kelvin-Tagen brauchte das Haus {f(per)} kWh Wärme je Kelvin-Tag."
            if dropped:
                s += f" {dropped} Tage ausgelassen, weil der Tageszähler eingefroren war oder den Vortag wiederholte."
            if thin:
                s += f" Unter {H['hdd_min']:.0f} Kelvin-Tagen ist das Warmwasser noch ein zu großer Teil davon."
            return s + f" Der Vergleich mit dem Vorjahr – ob das Haus bei gleichem Wetter mehr braucht – gibt es ab {day_de(ready)}."
        s = f"Over {len(rec)} heating days with {f(kd, 0)} kelvin-days in total the house needed {f(per)} kWh of heat per kelvin-day."
        if dropped:
            s += f" {dropped} days left out because the daily counter was frozen or repeated the previous day."
        if thin:
            s += f" Below {H['hdd_min']:.0f} kelvin-days hot water is still too large a share of it."
        return s + f" The comparison with last year – whether the house needs more for the same weather – exists from {ready}."

    return tile("heat_demand", "heat", "long", "info", "thin", {"de": "Wärmebedarf je Gradtag", "en": "Heat per degree-day"},
                {"de": f"{de(per)} kWh/Kd", "en": f"{en(per)} kWh/Kd"},
                {"de": f"{len(rec)} Tage, {de(kd, 0)} Kd", "en": f"{len(rec)} days, {en(kd, 0)} Kd"},
                both(txt), None, ready=ready, detail={"dropped_days": dropped})


# ---------------------------------------------------------------------------- was wir (noch) nicht messen
def gaps(d):
    """Kritische Punkte eines Hauses, für die es heute keinen Messwert gibt. Gebunden an die vorhandenen
    Messreihen: was dazukommt, fällt hier heraus."""
    m = d["measurements"]
    out = []

    def add(key, prio, title, text, cost):
        out.append({"key": key, "prio": prio, "title": title, "text": text, "cost": cost})

    if not m & {"water", "leak", "water_leak"}:
        add("leak", "high", {"de": "Wassermelder", "en": "Leak sensors"},
            {"de": "Unter Wärmepumpe, Speicher, Waschmaschine und im Keller. Leitungswasser ist der häufigste Gebäudeschaden – "
                   "und der einzige hier, der in Stunden statt Monaten teuer wird.",
             "en": "Under the heat pump, tank, washing machine and in the cellar. Escaping water is the most common building "
                   "damage – and the only one here that gets expensive in hours rather than months."},
            {"de": "je 20–40 € (Zigbee/Thread)", "en": "€20–40 each (Zigbee/Thread)"})
    if not m & {"water_meter", "water"}:
        add("water_meter", "mid", {"de": "Hauswasserzähler", "en": "Water meter"},
            {"de": "Ein Durchfluss, der nachts nie auf null geht, ist eine laufende Spülung oder ein Leck in der Wand.",
             "en": "A flow that never drops to zero at night is a running cistern or a leak in the wall."},
            {"de": "Impulsleser 30–60 €", "en": "pulse reader €30–60"})
    if not any("humidity" in f for f in d.get("weather_fields", ())):
        add("outdoor_humidity", "high", {"de": "Außenfeuchte", "en": "Outdoor humidity"},
            {"de": "Mit Taupunkt außen kann der Hauscheck sagen, ob Lüften gerade trocknet oder – im Sommerkeller – Wasser hereinholt. "
                   "Open-Meteo liefert sie in derselben Abfrage, die der Wetterdienst schon macht.",
             "en": "With the outdoor dew point the house check can say whether ventilating dries the air right now or – in a summer "
                   "cellar – brings water in. Open-Meteo delivers it in the same request the weather service already makes."},
            {"de": "kostenlos, eine Zeile in weather.py", "en": "free, one line in weather.py"})
    add("surface_temp", "mid", {"de": "Wandtemperatur an der kältesten Ecke", "en": "Wall temperature at the coldest corner"},
        {"de": "Macht aus dem modellierten Schimmelrisiko ein gemessenes – ein Fühler hinter dem Schrank an der Außenwand.",
         "en": "Turns the modelled mould risk into a measured one – one sensor behind the wardrobe on the outer wall."},
        {"de": "15–30 €", "en": "€15–30"})
    add("hp_meter", "mid", {"de": "Eigener Stromzähler für die Wärmepumpe", "en": "Dedicated heat pump meter"},
        {"de": "Die Arbeitszahl beruht auf dem Zähler der Wolf (H1/H2, ohne Pumpen und Heizstab, 100-W-Stufen). "
               "Ein MID-Zähler im Verteiler macht sie mit heatpumpmonitor.org vergleichbar.",
         "en": "The performance factor rests on the Wolf's own counter (H1/H2, without pumps and heater rod, 100 W steps). "
               "A MID meter in the panel makes it comparable to heatpumpmonitor.org."},
        {"de": "Hutschienenzähler 60–120 € + Elektriker", "en": "DIN-rail meter €60–120 + electrician"})
    if "co2" not in m:
        add("co2", "low", {"de": "CO₂ im Schlaf- und Kinderzimmer", "en": "CO₂ in bedrooms"},
            {"de": "Feuchte sagt, wann gelüftet werden muss, damit die Wand trocken bleibt; CO₂ sagt es für die Menschen. "
                   "Über 1 400 ppm nachts ist zu wenig Luft.",
             "en": "Humidity says when to ventilate for the walls; CO₂ says it for the people. Above 1,400 ppm at night is too little air."},
            {"de": "NDIR-Sensor 40–80 €", "en": "NDIR sensor €40–80"})
    add("smoke", "low", {"de": "Rauchmelder-Status", "en": "Smoke alarm status"},
        {"de": "Vernetzte Melder melden Batterie und Verschmutzung, bevor der jährliche Test fällig ist.",
         "en": "Connected alarms report battery and dirt before the annual test is due."},
        {"de": "nur bei Austausch sinnvoll", "en": "only worth it when replacing"})
    return out


# ---------------------------------------------------------------------------- Zusammenbau
def build(d, cfg):
    price = (cfg.get("tariff") or {}).get("price_ct_kwh") or d.get("tariff") or H["price_ct_kwh"]
    checks = [
        lambda: check_data(d), lambda: check_sensors(d), lambda: check_pressure(d), lambda: check_mould(d),
        lambda: check_rooms(d), lambda: check_dhw(d), lambda: check_cycling(cfg.get("cycling")),
        lambda: check_battery(d), lambda: check_base_load(d, price), lambda: check_pv(d),
        lambda: check_pressure_trend(d), lambda: check_heat_demand(d), lambda: check_base_trend(d),
        lambda: check_pv_long(d), lambda: check_battery_aging(d),
    ]
    tiles = []
    for c in checks:
        # Eine kaputte Prüfung darf die anderen nicht mitnehmen – sie fehlt dann eben, und das Log sagt warum.
        try:
            t = c()
            if t:
                tiles.append(t)
        except Exception:
            LOG.exception("Prüfung fehlgeschlagen")
    metrics = {t["key"]: t["detail"] for t in tiles}
    return {"updated": int(d["now"]), "day": day_of(d["now"]), "tiles": tiles, "gaps": gaps(d), "metrics": metrics}


# ---------------------------------------------------------------------------- Dienst
CFG = {"tariff": None, "cycling": None, "last": None}
wake = threading.Event()


def on_message(client, userdata, msg):
    t = msg.topic
    if t.endswith("/house_check/run"):
        wake.set(); return
    try:
        payload = json.loads(msg.payload)
    except Exception:
        return
    if t.endswith("/config/tariff"):
        CFG["tariff"] = payload
    elif t.endswith("/wolf/cycling"):
        CFG["cycling"] = payload
    elif t == TOPIC:
        CFG["last"] = payload.get("updated")


def next_run(now):
    lt = time.localtime(now)
    target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, CHECK_HOUR, 0, 0, 0, 0, -1))
    return target if target > now else target + 86400


def run_once(client):
    result = build(collect(), CFG)
    client.publish(TOPIC, json.dumps(result, ensure_ascii=False), retain=True)
    CFG["last"] = result["updated"]
    LOG.info("Hauscheck: %s", ", ".join(f"{t['key']}={t['level']}/{t['basis']}" for t in result["tiles"]))


def main():
    client = mqtt.Client(client_id=f"grolo-house-check-{os.getpid()}", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = lambda c, u, f, rc, p=None: (
        LOG.info("MQTT verbunden %s:%s", HOST, PORT),
        c.subscribe([(f"{BASE}/grolo/config/tariff", 0), (f"{BASE}/grolo/wolf/cycling", 0),
                     (TOPIC, 0), (f"{TOPIC}/run", 0)]))
    client.on_message = on_message
    mqtt_watchdog.install(client, "house-check")
    while True:
        try:
            client.connect(HOST, PORT, 60); break
        except Exception as exc:
            LOG.warning("MQTT nicht erreichbar (%s), neuer Versuch in 10 s", exc); time.sleep(10)
    client.loop_start()
    time.sleep(20)          # retained Tarif, Taktung und den eigenen letzten Stand abwarten
    if os.getenv("HOUSE_CHECK_ONCE"):
        print(json.dumps(build(collect(), CFG), ensure_ascii=False, indent=1))
        return
    # Nach einem Neustart nicht bis morgen früh warten, wenn der letzte Lauf schon über einen Tag her ist.
    if not CFG["last"] or time.time() - CFG["last"] > 23 * 3600:
        try:
            run_once(client)
        except Exception:
            LOG.exception("Hauscheck fehlgeschlagen")
    while True:
        wake.clear()
        wake.wait(max(1.0, next_run(time.time()) - time.time()))
        try:
            run_once(client)
        except Exception:
            LOG.exception("Hauscheck fehlgeschlagen")
        time.sleep(30)      # ein zweites "run" direkt hinterher ist kein Grund, alles erneut zu rechnen


if __name__ == "__main__":
    main()
