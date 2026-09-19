#!/usr/bin/env python3
"""web-push: schickt die bereinigten Messwerte alle 30 s an die GroLo-Website (Vercel).

Bildet aus dem GroBro-State dieselben Größen wie das Grafana-Dashboard:
  pv_w  = Summe Spannung × Strom der Strings          out_w = (Register 116 − 30000) / 10, Register zählt in 0,1 W
  bat_w = pv_w − out_w (positiv = laden)              soc, Packs, Temperaturen, Modus, Status
Mittelt über das Intervall, puffert bei Ausfall (bis 24 h) und schickt nach. Wetter, Standort, Sonnenstand und das
Erwartungsmodell je String (Sidecar weather) werden mitgeschickt, sobald sie sich ändern. Läuft die Shelly-Regelung, trägt
jedes Sample zusätzlich den gemittelten Netzbezug (grid_w) und Hausverbrauch (house_w) des Intervalls; der Strompreis
(retained <BASE>/grolo/config/tariff von der Einstellungsseite) geht als "tariff" mit, sobald er sich ändert.

Läuft die Wärmepumpe (Sidecar wolf-bridge), kommt "heat" dazu, mit demselben Zeitstempel wie die Samples, damit die Website
Wärmepumpe, Solar und Batterie Minute für Minute gegeneinander rechnen kann:
  heat.samples  Mittel des Intervalls: Aufnahme und Wärmeleistung, Vor-/Rücklauf, Warmwasser, Außentemperatur, Frequenz
  heat.days     Tageswerte aus den Zählern der Wolf (heute und Vortag, damit eine Lücke sich von selbst schließt)
  heat.state    der aktuelle Stand für die Kacheln, inklusive Klartext von Betriebsart und Verdichterstatus

Umgebung: WEB_URL (z. B. https://grolo.vercel.app), WEB_TOKEN, PUSH_INTERVAL (s, Standard 30), MQTT_HOST/MQTT_PORT, HA_BASE_TOPIC.
"""
import json, logging, os, threading, time, urllib.request, urllib.error
from collections import deque
import paho.mqtt.client as mqtt

BASE = os.getenv("HA_BASE_TOPIC", "homeassistant")
HOST = os.getenv("MQTT_HOST", "mosquitto"); PORT = int(os.getenv("MQTT_PORT", "1883"))
URL = os.getenv("WEB_URL", "").rstrip("/"); TOKEN = os.getenv("WEB_TOKEN", "")
INTERVAL = int(os.getenv("PUSH_INTERVAL", "30"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("web-push")

lock = threading.Lock()
acc = {}            # device -> {"n": int, sums: {...}, last: state}
queue = deque(maxlen=2880)   # gepufferte Samples (24 h bei 30 s)
QUEUE_FILE = os.getenv("QUEUE_FILE", "/state/queue.json")   # Puffer überlebt Neustarts, wenn /state ein Volume ist


def queue_save():
    try:
        os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)
        with open(QUEUE_FILE + ".tmp", "w") as f:
            json.dump({"samples": list(queue), "heat": list(heat_queue)}, f)
        os.replace(QUEUE_FILE + ".tmp", QUEUE_FILE)
    except Exception as e:
        LOG.debug("Puffer sichern: %s", e)


def queue_load():
    try:
        with open(QUEUE_FILE) as f:
            saved = json.load(f)
    except FileNotFoundError:
        return
    except Exception as e:
        LOG.warning("Puffer laden: %s", e); return
    items = saved if isinstance(saved, list) else saved.get("samples", [])   # ältere Puffer waren eine reine Liste
    heat = [] if isinstance(saved, list) else saved.get("heat", [])
    queue.extend(items); heat_queue.extend(heat)
    LOG.info("Puffer geladen: %d Samples, %d Wärmepumpe aus %s", len(items), len(heat), QUEUE_FILE)
info_pending = {}   # device -> info dict
weather = {"current": None, "forecast": None, "model": None, "fit": None, "advice": None, "dirty": False}
shelly = {"state": None, "dirty": False, "n": 0, "grid": 0.0, "house": 0.0}   # n/grid/house: Mittelwert über das Intervall
site_cfg = {"cfg": None}   # retained grolo/config/site (String-Namen)
tariff = {"cfg": None, "dirty": False}
# Wärmepumpe: letzter Zustand je Gerät der wolf-bridge plus Mittelwerte des laufenden Intervalls
wolf = {"state": {}, "derived": {}, "n": 0, "sums": {}, "last": {}}
WOLF_AVG = ("hp_w", "heat_w", "flow_c", "return_c", "dhw_c", "outside_c", "spread", "freq", "flow_lpm")
heat_queue = deque(maxlen=2880)


def pick(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def iso(v):
    """Zeitstempel als ISO-8601 (UTC); Unix-Sekunden werden umgerechnet, "HH:MM" wird auf das heutige lokale Datum gesetzt."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(v))
    v = str(v)
    if len(v) == 5 and v[2] == ":":
        # lokale Uhrzeit (TZ des Containers) -> heutiges Datum, in UTC ausgeben
        lt = time.localtime(); h, m = int(v[:2]), int(v[3:])
        t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    return v


def weather_payload():
    """Wetter im Vertrag der Website: akzeptiert Open-Meteo-Namen und die Kurzformen des weather-Sidecars."""
    c = weather["current"] or {}
    cur = {
        "temperature": pick(c, "temperature", "temperature_2m"), "cloud_cover": pick(c, "cloud_cover"),
        "shortwave_radiation": pick(c, "shortwave_radiation", "irradiance"), "direct_radiation": pick(c, "direct_radiation"),
        "diffuse_radiation": pick(c, "diffuse_radiation"), "wind_speed": pick(c, "wind_speed", "wind_speed_10m"),
        "weather_code": pick(c, "weather_code"), "is_day": pick(c, "is_day"),
        "condition_en": pick(c, "condition_en", "condition"), "condition_de": pick(c, "condition_de"),
        "sunrise": iso(pick(c, "sunrise")), "sunset": iso(pick(c, "sunset")),
        "sunshine_duration_today": pick(c, "sunshine_duration_today", "sunshine_duration",
                                        default=(pick(c, "sunshine_hours_today") or 0) * 3600 if pick(c, "sunshine_hours_today") is not None else None),
        "radiation_sum_today": pick(c, "radiation_sum_today", "shortwave_radiation_sum", "radiation_sum_mj_today", "radiation_today_mj"),
        "sun_azimuth": pick(c, "sun_azimuth"), "sun_elevation": pick(c, "sun_elevation"),
    }
    # Standort und Strings (Einstellungsseite bzw. Umgebung des weather-Sidecars) für Sonnenbahn und Erwartungsmodell der Website
    site_src = pick(c, "site", default={}) or {}
    site = {"name": site_src.get("name") or None, "lat": pick(site_src, "lat", default=pick(c, "latitude")), "lon": pick(site_src, "lon", default=pick(c, "longitude")),
            "strings": {str(k): {"tilt": v.get("tilt"), "azimuth": v.get("azimuth"), "wp": v.get("wp")} for k, v in (pick(c, "strings", default={}) or {}).items() if isinstance(v, dict)},
            "names": {str(k): str(v)[:40] for k, v in ((site_cfg["cfg"] or {}).get("names") or {}).items() if v}}
    m = weather["model"] or {}
    site["assumed"] = {k: {"tilt": v.get("tilt"), "azimuth": v.get("azimuth"), "wp": v.get("wp"), "source": v.get("source")} for k, v in ((m.get("strings") or {}) if isinstance(m, dict) else {}).items() if isinstance(v, dict) and v.get("assumed")}
    model = [{"t": iso(pick(h, "time", "t")), "string": int(h["string"]), "gti": h.get("gti"), "expected_w": h.get("expected_w")}
             for h in (pick(m, "hours", default=[]) if isinstance(m, dict) else m) or [] if h.get("string") is not None]
    fc = weather["forecast"]
    if isinstance(fc, dict):
        fc = pick(fc, "hourly", "forecast", default=[])
    fcl = []
    for h in (fc or [])[:48]:
        fcl.append({"t": iso(pick(h, "t", "time")), "shortwave_radiation": pick(h, "shortwave_radiation"), "cloud_cover": pick(h, "cloud_cover"),
                    "temperature": pick(h, "temperature", "temperature_2m"), "weather_code": pick(h, "weather_code")})
    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "current": cur, "forecast": fcl, "site": site, "model": model, "fit": weather["fit"], "advice": weather["advice"]}


def derive(st):
    pv = sum(float(st.get(f"pv{i}Voltage", 0) or 0) * float(st.get(f"pv{i}Current", 0) or 0) for i in range(1, 5))
    out = (float(st.get("onGridPower", 30000) or 30000) - 30000.0) / 10.0  # 0,1-W-Schritte, 38000 = 800 W
    return {
        "pv_w": pv, "out_w": out, "bat_w": pv - out, "soc": float(st.get("totalBatteryPackSoc", 0) or 0),
        "soc1": st.get("battery1Soc"), "soc2": st.get("battery2Soc"), "soc3": st.get("battery3Soc"), "soc4": st.get("battery4Soc"),
        "temp_sys": st.get("systemTemp"), "temp_bat1": st.get("battery1Temp"), "temp_bat2": st.get("battery2Temp"),
        "pv_v": [st.get(f"pv{i}Voltage") for i in range(1, 5)], "pv_a": [st.get(f"pv{i}Current") for i in range(1, 5)],
        "packs": st.get("batteryPackageQuantity"), "status": st.get("totalBatteryPackChargingStatus"), "mode": st.get("workMode"),
    }


def wolf_sample():
    """Aus dem letzten Stand der Wärmepumpe die Größen bilden, die die Website als Zeitreihe braucht."""
    hp = wolf["state"].get("heatpump") or {}
    d = wolf["derived"] or {}
    if not hp:
        return None
    return {
        "hp_w": d.get("strom_w"),                              # Leistungsaufnahme Wärmepumpe + Heizstab
        "heat_w": None if d.get("waerme_kw") is None else d["waerme_kw"] * 1000.0,
        "flow_c": hp.get("kesseltemperatur"), "return_c": hp.get("ruecklauftemperatur"),
        "dhw_c": hp.get("warmwassertemperatur"), "outside_c": hp.get("aussentemperatur"),
        "spread": d.get("spreizung"), "freq": hp.get("verdichterfrequenz"),
        "flow_lpm": hp.get("heizkreisdurchfluss"),
        "compressor": hp.get("verdichter"), "mode": hp.get("betriebsart_heizgeraet"),
    }


def wolf_accumulate():
    """Mittelwerte über das Push-Intervall bilden. Betriebsart und Verdichter werden nicht gemittelt,
    sondern zuletzt gesehen übernommen – ein halber Betriebszustand wäre sinnlos."""
    sample = wolf_sample()
    if sample is None:
        return
    wolf["n"] += 1
    for key in WOLF_AVG:
        v = sample.get(key)
        if v is not None:
            acc_key = wolf["sums"].setdefault(key, [0.0, 0])
            acc_key[0] += float(v); acc_key[1] += 1
    wolf["last"] = sample


def wolf_payload(now):
    """heat-Abschnitt für /api/ingest: gemitteltes Sample, Tageswerte aus den Zählern der Wolf, aktueller Stand."""
    hp = wolf["state"].get("heatpump") or {}
    d = wolf["derived"] or {}
    if not hp:
        return None

    if wolf["n"]:
        sample = {"ts": now}
        for key in WOLF_AVG:
            total, count = wolf["sums"].get(key, (0.0, 0))
            sample[key] = round(total / count, 2) if count else None
        sample["compressor"] = wolf["last"].get("compressor")
        sample["mode"] = wolf["last"].get("mode")
        heat_queue.append(sample)
    wolf["n"] = 0; wolf["sums"] = {}

    # Tageswerte: die Wolf zählt Wärme und Strom je Tag und setzt um Mitternacht zurück. Der Vortag geht
    # jedes Mal mit, damit ein Ausfall über Mitternacht den Tag nicht verschluckt.
    today = time.strftime("%Y-%m-%d")
    yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    days = []
    if hp.get("erzeugte_waermemenge_aktueller_tag") is not None:
        days.append({"day": today, "heat_kwh": hp.get("erzeugte_waermemenge_aktueller_tag"),
                     "el_kwh": hp.get("verbrauch_aktueller_tag")})
    if hp.get("erzeugte_waermemenge_vortag"):
        days.append({"day": yesterday, "heat_kwh": hp.get("erzeugte_waermemenge_vortag"),
                     "el_kwh": hp.get("verbrauch_vortag"), "spf": hp.get("taz_vortag")})

    state = {
        "mode": hp.get("betriebsart_heizgeraet"), "mode_text": hp.get("betriebsart_heizgeraet_text"),
        "compressor_status": hp.get("verdichterstatus"), "compressor_text": hp.get("verdichterstatus_text"),
        "compressor": hp.get("verdichter"), "eheat": hp.get("e_heizung"), "eheat_text": hp.get("e_heizung_text"),
        "hp_w": d.get("strom_w"), "heat_kw": d.get("waerme_kw"), "cop": d.get("cop"),
        "flow_c": hp.get("kesseltemperatur"), "return_c": hp.get("ruecklauftemperatur"), "spread": d.get("spreizung"),
        "dhw_c": hp.get("warmwassertemperatur"), "dhw_set_c": hp.get("warmwassersolltemperatur"),
        "outside_c": hp.get("aussentemperatur"), "freq": hp.get("verdichterfrequenz"),
        "flow_lpm": hp.get("heizkreisdurchfluss"), "pressure_bar": hp.get("anlagendruck"),
        "heat_today": hp.get("erzeugte_waermemenge_aktueller_tag"), "el_today": hp.get("verbrauch_aktueller_tag"),
        "heat_month": hp.get("erzeugte_waermemenge_aktueller_monat"), "el_month": hp.get("verbrauch_aktueller_monat"),
        "heat_year": hp.get("erzeugte_waermemenge_aktuelles_jahr"), "el_year": hp.get("verbrauch_aktuelles_jahr"),
        "spf_year": hp.get("jaz_aktuelles_jahr"), "spf_prev_year": hp.get("jaz_vorjahr"), "taz_yesterday": hp.get("taz_vortag"),
        "pf_today": d.get("az_tag"), "pf_month": d.get("az_monat"), "pf_year": d.get("az_jahr"),
        "cycles_today": d.get("takte_heute"), "runtime_today_min": d.get("laufzeit_min_heute"),
        "runtime_per_cycle_min": d.get("laufzeit_je_takt_min"), "defrosts_today": d.get("abtauungen_heute"),
        "dhw_min_today": d.get("ww_min_heute"), "heating_min_today": d.get("hz_min_heute"),
        "eheat_min_today": d.get("eheiz_min_heute"),
        "hours_compressor": hp.get("betriebsstunden_verdichter"), "hours_eheat": hp.get("betriebsstunden_e_heizung"),
        "starts": hp.get("verdichterstarts"), "serial": hp.get("seriennummer"), "power_class": hp.get("leistungsklasse"),
        "model": "Wolf CHA", "firmware": hp.get("hcm_4_firmware"),
    }
    return {"ts": now, "days": days, "state": state}


def on_message(client, userdata, msg):
    parts = msg.topic.split("/")
    try:
        if parts[-1] == "state" and len(parts) >= 3 and parts[-3] == "grobro":
            st = json.loads(msg.payload); device = parts[-2]; d = derive(st)
            with lock:
                a = acc.setdefault(device, {"n": 0, "pv": 0.0, "out": 0.0, "bat": 0.0, "soc": 0.0, "last": None})
                a["n"] += 1; a["pv"] += d["pv_w"]; a["out"] += d["out_w"]; a["bat"] += d["bat_w"]; a["soc"] += d["soc"]; a["last"] = d
        elif parts[-2:] == ["weather", "current"]:
            with lock:
                weather["current"] = json.loads(msg.payload); weather["dirty"] = True
        elif parts[-2:] == ["weather", "forecast"]:
            with lock:
                weather["forecast"] = json.loads(msg.payload); weather["dirty"] = True
        elif parts[-2:] == ["grolo", "pv_model"]:
            with lock:
                weather["model"] = json.loads(msg.payload); weather["dirty"] = True
        elif parts[-2:] == ["grolo", "fit"]:
            with lock:
                weather["fit"] = json.loads(msg.payload); weather["dirty"] = True
        elif parts[-2:] == ["grolo", "advice"]:
            with lock:
                weather["advice"] = json.loads(msg.payload); weather["dirty"] = True
        elif parts[-2:] == ["shelly", "state"]:
            sh = json.loads(msg.payload)
            with lock:
                shelly["state"] = sh; shelly["dirty"] = True
                if sh.get("ok") and sh.get("grid_w") is not None:   # auch bei ausgeschalteter Regelung (Messbetrieb)
                    shelly["n"] += 1; shelly["grid"] += float(sh["grid_w"]); shelly["house"] += float(sh.get("household_w") or 0)
        elif parts[-2:] == ["config", "site"]:
            with lock:
                site_cfg["cfg"] = json.loads(msg.payload); weather["dirty"] = True
        elif len(parts) >= 3 and parts[-3] == "wolf" and parts[-1] == "state":
            with lock:
                wolf["state"][parts[-2]] = json.loads(msg.payload)
                if parts[-2] == "heatpump":
                    wolf_accumulate()
        elif parts[-2:] == ["wolf", "derived"]:
            with lock:
                wolf["derived"] = json.loads(msg.payload)
                wolf_accumulate()
        elif parts[-2:] == ["config", "tariff"]:
            with lock:
                tariff["cfg"] = json.loads(msg.payload); tariff["dirty"] = True
        elif parts[-1] == "dongle":
            d = json.loads(msg.payload); device = parts[-2]
            with lock:
                info_pending[device] = {"device": device, "model": "Growatt NEXA 2000", "dongle_model": d.get("model_id"), "dongle_sw": d.get("sw_version"),
                                        "dongle_hw": d.get("hw_version"), "wifi_dbm": d.get("wifi_signal")}
    except Exception as e:
        LOG.debug("message %s: %s", msg.topic, e)


def flush():
    with lock:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        grid = round(shelly["grid"] / shelly["n"], 1) if shelly["n"] else None
        house = round(shelly["house"] / shelly["n"], 1) if shelly["n"] else None
        shelly.update({"n": 0, "grid": 0.0, "house": 0.0})
        for device, a in acc.items():
            if a["n"] == 0 or not a["last"]:
                continue
            s = dict(a["last"]); n = a["n"]
            s.update({"ts": now, "device": device, "pv_w": round(a["pv"] / n, 2), "out_w": round(a["out"] / n, 2), "bat_w": round(a["bat"] / n, 2), "soc": round(a["soc"] / n, 2),
                      "grid_w": grid, "house_w": house})
            queue.append(s); a.update({"n": 0, "pv": 0.0, "out": 0.0, "bat": 0.0, "soc": 0.0})
        info = dict(info_pending); info_pending.clear()
        wx = weather_payload() if (weather["dirty"] and weather["current"]) else None
        weather["dirty"] = False
        sh = shelly["state"] if shelly["dirty"] else None
        shelly["dirty"] = False
        tf = tariff["cfg"] if (tariff["dirty"] and tariff["cfg"]) else None
        tariff["dirty"] = False
        heat = wolf_payload(now)
    if not queue and not heat_queue and not info and not wx and not sh and not tf:
        return
    batch = list(queue)[:200]
    heat_batch = list(heat_queue)[:200]
    body = {"samples": batch}
    if info:
        body["info"] = next(iter(info.values()))
    if wx:
        body["weather"] = wx
    if sh:
        body["shelly"] = sh
    if tf:
        body["tariff"] = tf
    if heat or heat_batch:
        body["heat"] = {**(heat or {}), "samples": heat_batch}
    req = urllib.request.Request(f"{URL}/api/ingest", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read() or b"{}")
        for _ in batch:
            queue.popleft()
        for _ in heat_batch:
            heat_queue.popleft()
        if os.path.exists(QUEUE_FILE):
            queue_save() if (queue or heat_queue) else os.remove(QUEUE_FILE)
        LOG.info("gesendet: %d Samples%s (Antwort %s, Wetter %s%s), Puffer %d", len(batch),
                 f" + {len(heat_batch)} Wärmepumpe" if heat_batch else "", res.get("inserted"), res.get("weather"),
                 ", Tarif" if tf else "", len(queue) + len(heat_queue))
    except urllib.error.HTTPError as e:
        LOG.warning("HTTP %s von %s: %s", e.code, URL, e.read()[:200]); queue_save()
        with lock:
            info_pending.update(info); weather["dirty"] = weather["dirty"] or bool(wx); shelly["dirty"] = shelly["dirty"] or bool(sh); tariff["dirty"] = tariff["dirty"] or bool(tf)
    except Exception as e:
        LOG.warning("Senden fehlgeschlagen (%s), Puffer %d", e, len(queue)); queue_save()
        with lock:
            info_pending.update(info); weather["dirty"] = weather["dirty"] or bool(wx); shelly["dirty"] = shelly["dirty"] or bool(sh); tariff["dirty"] = tariff["dirty"] or bool(tf)


def main():
    if not URL or not TOKEN:
        LOG.error("WEB_URL oder WEB_TOKEN fehlt"); time.sleep(3600); return
    queue_load()
    client = mqtt.Client(client_id="grolo-web-push", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = lambda c, u, f, rc, p=None: (LOG.info("MQTT verbunden %s:%s, Ziel %s", HOST, PORT, URL),
                                                     c.subscribe([(f"{BASE}/grobro/+/state", 0), (f"{BASE}/grobro/+/dongle", 0), (f"{BASE}/grolo/weather/current", 0), (f"{BASE}/grolo/weather/forecast", 0), (f"{BASE}/grolo/pv_model", 0), (f"{BASE}/grolo/fit", 0), (f"{BASE}/grolo/advice", 0), (f"{BASE}/grolo/shelly/state", 0), (f"{BASE}/grolo/config/tariff", 0), (f"{BASE}/grolo/config/site", 0),
                                                                  (f"{BASE}/grolo/wolf/+/state", 0), (f"{BASE}/grolo/wolf/derived", 0)]))
    client.on_message = on_message
    while True:
        try:
            client.connect(HOST, PORT, 60); break
        except Exception as e:
            LOG.warning("MQTT nicht erreichbar (%s)", e); time.sleep(5)
    client.loop_start()
    while True:
        time.sleep(INTERVAL)
        try:
            flush()
        except Exception as e:
            LOG.warning("flush: %s", e)


main()
