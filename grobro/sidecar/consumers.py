#!/usr/bin/env python3
"""consumers: die einzelnen Verbraucher im Haus, für das Flussbild „Haus → Geräte" und die Tageswerte.

Der Hauszähler (Shelly Pro 3EM) sagt, wie viel das Haus zieht, aber nicht, wer. Dieser Dienst sammelt die
Geräte, die ihren Verbrauch selbst kennen, und veröffentlicht sie als eine Liste. Was übrig bleibt
(Hausverbrauch minus Summe der Geräte), rechnet die Anzeige als „Rest" – hier wird nichts geschätzt.

Quellen:
  shelly  Messsteckdosen und Unterzähler im LAN (Gen1, Gen2, Gen3: Plug S, 1PM, PM Mini …). Sie werden von
          selbst gefunden: alle CONSUMERS_SCAN_S Sekunden ein Rundruf über CONSUMERS_SUBNET auf /shelly.
          Zähler (EM, 3EM) werden übergangen – das ist der Hausanschluss, kein Verbraucher.
  wolf    die Wärmepumpe aus der wolf-bridge (<BASE>/grolo/wolf/derived strom_w, Tageszähler der Wolf).
          Die Wolf misst ihre Aufnahme nicht, sie rechnet sie aus – und zu hoch: über eine Woche lag der Anstieg
          am Hauszähler bei etwa 0,70 × Wolf-Wert + 65 W (600 → 485 W, 1600 → 1200 W, 2300 → 1670 W). Ungeeicht
          schluckte die Wärmepumpe im Fächer den ganzen Hausverbrauch. Deshalb wird sie alle WOLF_CAL_S Sekunden
          gegen den Hauszähler geeicht (calibrate_wolf) und mit der Geraden aus der Messung angezeigt.
  ext     alles andere, was eine Brücke auf <BASE>/grolo/consumers/ext/<id> legt (retained JSON:
          name, w, kwh_today, icon, state, ok). So kommen Geräte dazu, die über eine eigene Brücke laufen
          (SmartThings, Home Connect), ohne dass dieser Dienst ihr Protokoll kennt.

  virtual Geräte ohne Messung mit festem Mittelwert aus dem Datenblatt (Konfiguration „virtual"), etwa ein
          Einbau-Kühlschrank ohne erreichbare Steckdose: 201 kWh/a → 23 W. Bei Kühlgeräten liefert Home Connect keinen
          Kompressorzustand, nur Tür und Sondermodi – genauer als der Jahresmittelwert wird es ohne Messung nicht.

Konfiguration retained <BASE>/grolo/config/consumers (Einstellungsseite):
  {"devices": {"<id>": {"name": "Trockner", "icon": "dryer", "role": "auto|consumer|source|hidden"}},
   "virtual": {"<id>": {"name": "Kühlschrank - Küche", "icon": "fridge", "kwh_year": 201}}}   (oder "w": 23)
  role source = die Steckdose misst einen Erzeuger (z. B. den Wechselrichter am Balkon) – sie gehört nicht in
  die Verbraucherliste. „auto" entscheidet nach der Richtung: wer über den Tag mehr zurückspeist als bezieht,
  ist ein Erzeuger.

Veröffentlicht retained <BASE>/grolo/consumers/state:
  {"ts": …, "items": [{"id", "name", "icon", "kind", "host", "w", "w_2m", "w_15m", "kwh_today", "role", "ok", "state"}]}
w_2m/w_15m sind die Mittel der letzten 2 bzw. 15 Minuten (für die Umschalter „Ø 2 min / Ø 15 min" im Flussbild):
die Website hält keine Zeitreihe je Gerät, deshalb wird hier gemittelt, wo jede Messung vorbeikommt.
und je Gerät die Leistung nach InfluxDB (Measurement "consumer", Tags id/name).

Umgebung: MQTT_HOST, MQTT_PORT, HA_BASE_TOPIC, INFLUX_*, CONSUMERS_SUBNET (Standard 192.168.1),
          CONSUMERS_INTERVAL (s, Standard 10), CONSUMERS_SCAN_S (s, Standard 1800), CONSUMERS_HOSTS
          (zusätzliche Shelly-Adressen, kommagetrennt, falls der Rundruf sie nicht erreicht), STATE_DIR.
"""
import concurrent.futures as cf
from collections import deque
import json, logging, os, re, threading, time, urllib.parse, urllib.request
import paho.mqtt.client as mqtt
import mqtt_watchdog

BASE = os.getenv("HA_BASE_TOPIC", "homeassistant")
HOST = os.getenv("MQTT_HOST", "mosquitto"); PORT = int(os.getenv("MQTT_PORT", "1883"))
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086"); INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", ""); INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "")
SUBNET = os.getenv("CONSUMERS_SUBNET", "192.168.1").rstrip(".")
INTERVAL = float(os.getenv("CONSUMERS_INTERVAL", "10"))
SCAN_S = float(os.getenv("CONSUMERS_SCAN_S", "1800"))
EXTRA_HOSTS = [h.strip() for h in os.getenv("CONSUMERS_HOSTS", "").split(",") if h.strip()]
STATE_DIR = os.getenv("STATE_DIR", "/state")
EXT_STALE_S = 900.0   # eine Brücke, die eine Viertelstunde schweigt, zählt nicht mehr mit
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("consumers")

lock = threading.Lock()
cfg = {"devices": {}}
shellies = {}        # id -> {"host", "gen", "model", "name", "channels": [(kind, n)]}
wolf = {"w": None, "kwh_today": None, "ts": 0.0, "seen": None}   # seen: Zeit des letzten ECHTEN Werts der Wärmepumpe
# Die wolf-bridge veröffentlicht derived auch dann weiter, wenn der WOLF Link weg ist – mit dem letzten bekannten
# Stand. Am 18.10.2026 stand so eine halbe Stunde „600 W, Betrieb" im Fächer, während der Link nicht einmal auf
# Ping antwortete und die Wärmepumpe längst aus war; der Rest fiel auf 0. Maßgeblich ist deshalb das Alter aus
# <BASE>/grolo/wolf/status, nicht der Takt von derived.
WOLF_STALE_S = 300
# Eichung Wolf-Wert → Mehrverbrauch am Hauszähler. Startwerte aus der Woche bis 18.10.2026, bis die erste eigene
# Rechnung vorliegt.
wolf_cal = {"slope": 0.70, "offset": 65.0, "n": 0, "basis": "prior", "at": 0}
WOLF_CAL_S = float(os.getenv("WOLF_CAL_S", "21600"))
ext = {}             # id -> payload + "_ts"
# Tageszähler: Energiestand der Shelly zu Beginn des Tages, damit „heute" einen Neustart übersteht
days = {"day": None, "start": {}, "ret_start": {}}
hist = {}            # id -> deque[(ts, w)] der letzten 15 Minuten


def averages(items, now):
    """Leistung je Gerät über 2 und 15 Minuten mitteln. Fehlende Messungen zählen nicht als 0 –
    sonst zöge ein Gerät, das kurz nicht antwortet, seinen eigenen Mittelwert nach unten."""
    for it in items:
        q = hist.setdefault(it["id"], deque())
        if it.get("w") is not None:
            q.append((now, it["w"]))
        while q and q[0][0] < now - 900:
            q.popleft()
        for key, span in (("w_2m", 120), ("w_15m", 900)):
            vals = [w for t, w in q if t >= now - span]
            it[key] = round(sum(vals) / len(vals), 1) if vals else None
    for cid in [k for k in hist if not any(i["id"] == k for i in items)]:
        del hist[cid]

# Symbol nach Namen, damit eine neue Steckdose nicht als „Stecker" im Bild steht, nur weil niemand ein Symbol
# gewählt hat. Die Einstellungsseite kann es überschreiben.
ICONS = [(r"wasch", "washer"), (r"trock", "dryer"), (r"spül|spuel|geschirr|dish", "dishwasher"),
         (r"ofen|herd|oven|backofen", "oven"), (r"kühl|kuehl|gefrier|fridge|freez", "fridge"),
         (r"wärmepumpe|waermepumpe|heat ?pump", "heatpump"), (r"wallbox|auto|ev\b", "car"),
         (r"tv|fernseh", "tv"), (r"pc|computer|rechner|server|nas|proxmox", "pc"), (r"kaffee|coffee", "coffee"),
         (r"klima|a/?c\b|air", "ac"), (r"licht|lampe|light", "light")]


def guess_icon(name):
    n = (name or "").lower()
    for pat, icon in ICONS:
        if re.search(pat, n):
            return icon
    return "plug"


def num(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def get_json(url, timeout=2.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


# ---------------- Shelly ----------------

def identify(host):
    """Was steht unter dieser Adresse? None, wenn kein Shelly oder einer ohne Verbrauchsmessung."""
    try:
        d = get_json(f"http://{host}/shelly", 1.5)
    except Exception:
        return None
    gen = int(d.get("gen") or 1)
    model = d.get("model") or d.get("type") or ""
    if gen >= 2:
        app = (d.get("app") or "").lower()
        if "em" in app and "pm" not in app:     # Pro3EM, ProEM, EM Gen3: Zähler, kein Verbraucher
            return None
        try:
            st = get_json(f"http://{host}/rpc/Shelly.GetStatus")
            sc = get_json(f"http://{host}/rpc/Sys.GetConfig")
        except Exception:
            return None
        chans = [tuple(k.split(":")) for k, v in st.items()
                 if ":" in k and k.split(":")[0] in ("switch", "pm1") and isinstance(v, dict) and "apower" in v]
        if not chans:
            return None
        name = (sc.get("device") or {}).get("name")
        if not name and len(chans) == 1:            # manche Apps benennen nur den Schaltkanal, nicht das Gerät
            try:
                name = get_json(f"http://{host}/rpc/{chans[0][0].capitalize()}.GetConfig?id={chans[0][1]}").get("name")
            except Exception:
                pass
        did = d.get("id") or host
        return {"host": host, "gen": gen, "model": model, "id": did, "name": name, "channels": [(k, int(n)) for k, n in chans]}
    # Gen1: Leistung unter meters[], Name in /settings
    if "EM" in model.upper() and "PLUG" not in model.upper():
        return None
    try:
        st = get_json(f"http://{host}/status"); se = get_json(f"http://{host}/settings")
    except Exception:
        return None
    meters = st.get("meters") or []
    if not meters:
        return None
    did = (se.get("device") or {}).get("hostname") or host
    return {"host": host, "gen": 1, "model": model, "id": did, "name": se.get("name"), "channels": [("meter", i) for i in range(len(meters))]}


def scan():
    hosts = [f"{SUBNET}.{i}" for i in range(1, 255)] + EXTRA_HOSTS
    found = {}
    with cf.ThreadPoolExecutor(48) as ex:
        for dev in ex.map(identify, hosts):
            if dev:
                found[dev["id"]] = dev
    with lock:
        new = [k for k in found if k not in shellies]
        # Ein Gerät, das beim Rundruf gerade nicht antwortet, bleibt bekannt – sonst verschwände eine
        # Waschmaschine aus dem Bild, nur weil ihre Steckdose einen Moment im WLAN gehangen hat.
        shellies.update(found)
    LOG.info("Rundruf %s.0/24: %d Shelly mit Leistungsmessung%s", SUBNET, len(found),
             f", neu: {', '.join(new)}" if new else "")


def read_shelly(dev):
    """Liste je Kanal: (Kanal-ID, Leistung W, Energiestand Wh, zurückgespeiste Wh)."""
    out = []
    if dev["gen"] >= 2:
        st = get_json(f"http://{dev['host']}/rpc/Shelly.GetStatus")
        for kind, n in dev["channels"]:
            c = st.get(f"{kind}:{n}") or {}
            out.append((n, num(c.get("apower")), num((c.get("aenergy") or {}).get("total")),
                        num((c.get("ret_aenergy") or {}).get("total"))))
    else:
        st = get_json(f"http://{dev['host']}/status")
        for i, m in enumerate(st.get("meters") or []):
            # Gen1 zählt in Wattminuten
            tot = num(m.get("total")); out.append((i, num(m.get("power")), None if tot is None else tot / 60.0, None))
    return out


# ---------------- Tageswerte ----------------

def day_load():
    try:
        with open(os.path.join(STATE_DIR, "consumers_days.json")) as f:
            days.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        LOG.warning("Tageswerte laden: %s", e)


def day_save():
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        p = os.path.join(STATE_DIR, "consumers_days.json")
        with open(p + ".tmp", "w") as f:
            json.dump(days, f)
        os.replace(p + ".tmp", p)
    except Exception as e:
        LOG.debug("Tageswerte sichern: %s", e)


def integrate_kwh(key, w, now):
    """Bezug seit Mitternacht aus der Leistung über die Zeit (Rechteck je Abfrage) – für Geräte, deren eigener
    Tageszähler nicht taugt. Der SwitchBot-Wert usedElectricity sprang am 18.10.2026 ohne Last von 0,36 auf
    1,44 kWh und stieg bei ausgeschaltetem Stecker; ein Jahresverbrauch darauf wäre Unsinn. Lücken über
    5 Minuten zählen nicht mit, damit ein Ausfall der Brücke keine erfundene Energie einträgt."""
    today_kwh(key, None, None)                  # setzt den Tag um Mitternacht zurück
    days.setdefault("int", {}); days.setdefault("int_ts", {})
    last = days["int_ts"].get(key)
    if w is not None and last is not None and 0 < now - last < 300:
        days["int"][key] = days["int"].get(key, 0.0) + max(0.0, w) * (now - last) / 3600.0
    days["int_ts"][key] = now
    return round(days["int"].get(key, 0.0) / 1000.0, 4)


def today_kwh(key, total_wh, ret_wh):
    """Bezug seit Mitternacht in kWh aus dem Zählerstand. Der erste Stand des Tages ist die Basis."""
    today = time.strftime("%Y-%m-%d")
    if days["day"] != today:
        days.update({"day": today, "start": {}, "ret_start": {}, "int": {}, "int_ts": {}})
    if total_wh is None:
        return None, None
    start = days["start"].setdefault(key, total_wh)
    if total_wh < start:                        # Zähler zurückgesetzt (Werksreset, Firmware)
        days["start"][key] = start = total_wh
    ret = None
    if ret_wh is not None:
        rs = days["ret_start"].setdefault(key, ret_wh)
        if ret_wh < rs:
            days["ret_start"][key] = rs = ret_wh
        ret = (ret_wh - rs) / 1000.0
    return (total_wh - start) / 1000.0, ret


# ---------------- Eichung der Wärmepumpe ----------------

def flux_csv(query):
    q = urllib.parse.urlencode({"org": INFLUX_ORG})
    req = urllib.request.Request(f"{INFLUX_URL}/api/v2/query?{q}", method="POST",
                                 data=json.dumps({"query": query, "type": "flux"}).encode(),
                                 headers={"Authorization": f"Token {INFLUX_TOKEN}", "Content-Type": "application/json", "Accept": "application/csv"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode().splitlines()


def calibrate_wolf():
    """Gerade Wolf-Wert → Mehrverbrauch am Hauszähler aus den letzten 7 Tagen, minutengenau.

    Je gemeldetem Wolf-Wert (die Wolf meldet in 100-W-Stufen) der Median des Hausverbrauchs, davon ab der Median
    bei stehender Wärmepumpe – das ist der Anteil der Wärmepumpe. Durch diese Punkte eine nach Minutenzahl gewichtete
    Gerade. Mediane statt Mittel, damit ein Wasserkocher zur falschen Zeit nicht die Eichung verbiegt.
    Die ersten Minuten nach dem Start bleiben draußen: da meldet die Wolf schon die Zielleistung, der Verdichter
    fährt aber erst hoch."""
    if not (INFLUX_TOKEN and INFLUX_ORG and INFLUX_BUCKET):
        return
    b = INFLUX_BUCKET
    lines = flux_csv(f'''
h = from(bucket: "{b}") |> range(start: -7d) |> filter(fn: (r) => r._measurement == "shelly" and r._field == "household_w")
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false) |> keep(columns: ["_time", "_value"]) |> set(key: "k", value: "h")
w = from(bucket: "{b}") |> range(start: -7d) |> filter(fn: (r) => r._measurement == "wolf_derived" and r._field == "strom_w")
  |> aggregateWindow(every: 1m, fn: last, createEmpty: false) |> keep(columns: ["_time", "_value"]) |> set(key: "k", value: "w")
union(tables: [h, w]) |> pivot(rowKey: ["_time"], columnKey: ["k"], valueColumn: "_value") |> sort(columns: ["_time"])''')
    head = None; last_w = None; since_start = 0; on, off = {}, []
    for line in lines:
        cols = line.split(",")
        if head is None:
            if "_time" in cols:
                head = {c.strip(): i for i, c in enumerate(cols)}
            continue
        if len(cols) < len(head):
            continue
        h = num(cols[head["h"]]) if "h" in head else None
        w = num(cols[head["w"]]) if "w" in head else None
        if w is not None:                       # die Wolf meldet nur Änderungen: weitertragen
            since_start = 0 if (last_w or 0) == 0 and w > 0 else since_start
            last_w = w
        since_start += 1
        if h is None or last_w is None:
            continue
        if last_w == 0:
            off.append(h)
        elif since_start > 5:
            on.setdefault(last_w, []).append(h)
    if len(off) < 600:
        LOG.info("Eichung Wärmepumpe: zu wenig Minuten ohne Wärmepumpe (%d)", len(off)); return
    med = lambda v: sorted(v)[len(v) // 2]
    base = med(off)
    pts = [(w, med(v) - base, len(v)) for w, v in on.items() if len(v) >= 20]
    if len(pts) < 3:
        LOG.info("Eichung Wärmepumpe: zu wenig Stufen (%d)", len(pts)); return
    sw = sum(n for _, _, n in pts); mx = sum(w * n for w, _, n in pts) / sw; my = sum(y * n for _, y, n in pts) / sw
    var = sum(n * (w - mx) ** 2 for w, _, n in pts)
    if var <= 0:
        return
    slope = sum(n * (w - mx) * (y - my) for w, y, n in pts) / var
    offset = my - slope * mx
    if not 0.3 <= slope <= 1.3:                 # außerhalb davon misst die Gerade etwas anderes als die Wärmepumpe
        LOG.warning("Eichung Wärmepumpe verworfen: Steigung %.2f", slope); return
    with lock:
        wolf_cal.update({"slope": round(slope, 3), "offset": round(offset, 1), "n": sw, "basis": "measured", "at": int(time.time())})
    LOG.info("Eichung Wärmepumpe: echt ≈ %.2f × Wolf + %.0f W (%d Minuten, %d Stufen, Grundlast %.0f W)", slope, offset, sw, len(pts), base)


def wolf_real(w):
    if w is None or w <= 0:
        return w
    return max(0.0, wolf_cal["slope"] * w + wolf_cal["offset"])


# ---------------- Zusammenführen ----------------

def item_cfg(cid):
    return (cfg.get("devices") or {}).get(cid) or {}


def collect():
    items = []
    with lock:
        devs = list(shellies.values())
    for dev in devs:
        try:
            chans = read_shelly(dev); ok = True
        except Exception:
            chans = [(n, None, None, None) for _, n in dev["channels"]]; ok = False
        for n, w, tot, ret in chans:
            cid = dev["id"] if len(chans) == 1 else f"{dev['id']}:{n}"
            kwh, ret_kwh = today_kwh(cid, tot, ret)
            c = item_cfg(cid)
            name = c.get("name") or dev.get("name") or (dev["model"] + (f" {n + 1}" if len(chans) > 1 else ""))
            role = c.get("role") or "auto"
            if role == "auto":
                # Mehr zurückgespeist als bezogen (heute oder gerade deutlich negativ): ein Erzeuger. Die Einstufung
                # bleibt hängen – sonst wechselte die Steckdose am Wechselrichter nachts bei 0 W in die Verbraucherliste.
                src = days.setdefault("sources", [])
                if ((ret_kwh or 0) > max(0.01, kwh or 0) or (w is not None and w < -5)) and cid not in src:
                    src.append(cid)
                role = "source" if cid in src else "consumer"
            items.append({"id": cid, "name": name, "icon": c.get("icon") or guess_icon(name), "kind": "shelly",
                          "host": dev["host"], "model": dev["model"], "w": None if w is None else round(w, 1),
                          "kwh_today": None if kwh is None else round(kwh, 3), "role": role, "ok": ok})
    now = time.time()
    with lock:
        if wolf["ts"] and now - wolf["ts"] < 600:
            c = item_cfg("wolf")
            stale = wolf["seen"] is not None and now - wolf["seen"] > WOLF_STALE_S
            w = None if stale else wolf_real(wolf["w"])
            items.append({"id": "wolf", "name": c.get("name") or "Wärmepumpe", "icon": c.get("icon") or "heatpump",
                          "kind": "wolf", "w": None if w is None else round(w, 1), "w_reported": None if stale else wolf["w"],
                          "state": "offline" if stale else None,
                          "estimated": True, "cal": dict(wolf_cal), "kwh_today_reported": wolf["kwh_today"],
                          # Der Tageszähler der Wolf ist aus derselben Rechnung gebildet: geeicht wie die Leistung,
                          # der Sockel der Geraden über die Laufzeit des Tages
                          "kwh_today": None if wolf["kwh_today"] is None else round(
                              wolf["kwh_today"] * wolf_cal["slope"] + wolf_cal["offset"] * (wolf.get("run_min") or 0) / 60000.0, 2),
                          "role": c.get("role") if c.get("role") not in (None, "auto") else "consumer", "ok": not stale})
        for cid, v in (cfg.get("virtual") or {}).items():
            w = num(v.get("w"))
            if w is None and num(v.get("kwh_year")) is not None:
                w = num(v["kwh_year"]) * 1000.0 / 8760.0
            if w is None:
                continue
            vid = f"virt-{cid}"
            c = item_cfg(vid)                       # Umbenennen auf der Einstellungsseite gilt auch für virtuelle Geräte
            name = c.get("name") or v.get("name") or cid
            items.append({"id": vid, "name": name, "icon": c.get("icon") or v.get("icon") or guess_icon(name),
                          "kind": "virtual", "w": round(w, 1), "estimated": True,
                          "kwh_today": integrate_kwh(vid, w, now),
                          "role": c.get("role") if c.get("role") not in (None, "auto") else "consumer", "ok": True})
        for cid, p in ext.items():
            if now - p.get("_ts", 0) > EXT_STALE_S:
                continue
            c = item_cfg(cid)
            name = c.get("name") or p.get("name") or cid
            items.append({"id": cid, "name": name, "icon": c.get("icon") or p.get("icon") or guess_icon(name),
                          "kind": p.get("kind") or "ext", "w": num(p.get("w")),
                          "kwh_today": integrate_kwh(cid, num(p.get("w")), now) if p.get("integrate") else num(p.get("kwh_today")),
                          "state": p.get("state"), "remaining_min": num(p.get("remaining_min")),
                          "estimated": bool(p.get("estimated")),
                          "role": c.get("role") if c.get("role") not in (None, "auto") else "consumer",
                          "ok": p.get("ok", True)})
    return items


def influx_write(items):
    if not (INFLUX_TOKEN and INFLUX_ORG and INFLUX_BUCKET):
        return
    esc = lambda s: str(s).replace(" ", r"\ ").replace(",", r"\,").replace("=", r"\=")
    lines = []
    for it in items:
        f = [f"{k}={float(it[k])}" for k in ("w", "kwh_today") if it.get(k) is not None]
        if f:
            lines.append(f"consumer,id={esc(it['id'])},name={esc(it['name'])},role={it['role']} {','.join(f)}")
    if not lines:
        return
    q = urllib.parse.urlencode({"org": INFLUX_ORG, "bucket": INFLUX_BUCKET, "precision": "s"})
    try:
        req = urllib.request.Request(f"{INFLUX_URL}/api/v2/write?{q}", data="\n".join(lines).encode(), method="POST",
                                     headers={"Authorization": f"Token {INFLUX_TOKEN}", "Content-Type": "text/plain"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        LOG.debug("Influx: %s", e)


def on_message(client, userdata, msg):
    parts = msg.topic.split("/")
    try:
        if parts[-2:] == ["config", "consumers"]:
            d = json.loads(msg.payload or b"{}")
            with lock:
                cfg.clear(); cfg.update({"devices": {}, **d})
        elif parts[-2:] == ["wolf", "derived"]:
            d = json.loads(msg.payload)
            with lock:
                if d.get("strom_w") is not None:
                    wolf["w"] = num(d["strom_w"]); wolf["ts"] = time.time()
                if d.get("laufzeit_min_heute") is not None:
                    wolf["run_min"] = num(d["laufzeit_min_heute"])
        elif parts[-2:] == ["wolf", "status"]:
            d = json.loads(msg.payload)
            hp = next((x for x in d.get("devices", []) if x.get("id") == "heatpump"), None)
            with lock:
                wolf["seen"] = None if not hp or hp.get("age") is None else float(d.get("ts", time.time())) - float(hp["age"])
        elif parts[-3:] == ["wolf", "heatpump", "state"]:
            d = json.loads(msg.payload)
            with lock:
                if d.get("verbrauch_aktueller_tag") is not None:
                    wolf["kwh_today"] = num(d["verbrauch_aktueller_tag"])
        elif parts[-2:] == ["consumers", "identify"]:
            # Knopf „LED blinken" der Einstellungsseite: Shelly.Identify lässt die LED des Steckers blinken, ohne
            # zu schalten – wichtig, denn hinter einem Stecker kann das IT-Rack hängen.
            cid = msg.payload.decode().strip().split(":")[0]
            with lock:
                dev = shellies.get(cid)
            if dev and dev["gen"] >= 2:
                threading.Thread(target=lambda h=dev["host"]: _safe(lambda: get_json(f"http://{h}/rpc/Shelly.Identify")), daemon=True).start()
                LOG.info("LED blinkt: %s (%s)", cid, dev["host"])
        elif len(parts) >= 2 and parts[-2] == "ext" and parts[-3] == "consumers":
            with lock:
                if not msg.payload:
                    ext.pop(parts[-1], None)
                else:
                    d = json.loads(msg.payload); d["_ts"] = time.time(); ext[parts[-1]] = d
    except Exception as e:
        LOG.debug("message %s: %s", msg.topic, e)


def _safe(fn):
    try:
        fn()
    except Exception as e:
        LOG.warning("%s: %s", fn.__name__, e)


def main():
    day_load()
    client = mqtt.Client(client_id="grolo-consumers", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = lambda c, u, f, rc, p=None: c.subscribe([
        (f"{BASE}/grolo/config/consumers", 0), (f"{BASE}/grolo/wolf/derived", 0),
        (f"{BASE}/grolo/wolf/heatpump/state", 0), (f"{BASE}/grolo/wolf/status", 0), (f"{BASE}/grolo/consumers/ext/+", 0),
        (f"{BASE}/grolo/consumers/identify", 0)])
    client.on_message = on_message
    mqtt_watchdog.install(client, "consumers", idle_s=1800.0)
    while True:
        try:
            client.connect(HOST, PORT, 60); break
        except Exception as e:
            LOG.warning("MQTT nicht erreichbar (%s)", e); time.sleep(5)
    client.loop_start()
    last_scan = 0.0; last_save = 0.0; last_cal = 0.0
    while True:
        t0 = time.time()
        if t0 - last_scan > SCAN_S:
            last_scan = t0
            threading.Thread(target=scan, daemon=True).start()
        if t0 - last_cal > WOLF_CAL_S:
            last_cal = t0
            threading.Thread(target=lambda: _safe(calibrate_wolf), daemon=True).start()
        items = collect()
        averages(items, t0)
        client.publish(f"{BASE}/grolo/consumers/state", json.dumps({"ts": int(t0), "items": items}), retain=True)
        influx_write(items)
        if t0 - last_save > 60:
            day_save(); last_save = t0
        time.sleep(max(1.0, INTERVAL - (time.time() - t0)))


if __name__ == "__main__":
    main()
