#!/usr/bin/env python3
"""shelly-control: lokale Nulleinspeisung ("Smart"-Modus) mit einem Shelly-Zähler, ohne Growatt-Cloud.

Der NEXA lehnt den herstellereigenen Smart-Modus ohne in der Cloud gekoppelten Zähler ab. Dieser Dienst bildet den
Smart-Modus selbst nach: er liest zyklisch die Netzleistung eines Shelly (Pro 3EM, EM, 1PM oder Gen1) im lokalen Netz
und stellt die Ausgangsleistung des NEXA (Slot-Leistung, flüchtiges RAM-Register, unbedenklich für häufige Schreibzugriffe)
so nach, dass am Netzanschluss nur noch ein kleiner Sollwert bezogen und nichts eingespeist wird.

Konfiguration als retained MQTT-Nachricht <BASE>/grolo/config/shelly (Einstellungsseite), Felder:
  enabled     bool    Regelung an/aus
  host        str     IP oder Hostname des Shelly (z. B. 192.168.1.158)
  setpoint_w  int     angestrebter Netzbezug in W (Standard 20, leicht positiv = nie einspeisen)
  min_w,max_w int     Grenzen der Ausgangsleistung (Standard 0 / 800)
  slot        int     Zeitfenster, dessen Leistung gestellt wird (Standard 1, der ganztägige Slot)
  deadband_w  int     kleinste Änderung, die geschrieben wird (Standard 10)
  interval_s  num     Abfrageintervall in s (Standard 2)
  gain        num     Reglerverstärkung 0..1 (Standard 0.8)
  fallback_w  int     Ausgangsleistung, wenn der Shelly ausfällt (Standard 0)
  device      str     NEXA-Seriennummer (Standard: automatisch aus dem State-Topic)

Veröffentlicht retained <BASE>/grolo/shelly/state (JSON: grid_w, household_w, out_w, target_w, setpoint_w, ok, reason)
und schreibt dieselben Größen nach InfluxDB (Measurement "shelly"). Die Website (web-push) spiegelt den Zustand.

Umgebung: MQTT_HOST, MQTT_PORT, HA_BASE_TOPIC, INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET,
          SHELLY_HTTP_TIMEOUT (s, Standard 2), TZ.
"""
import json, logging, os, threading, time, urllib.parse, urllib.request
import paho.mqtt.client as mqtt

BASE = os.getenv("HA_BASE_TOPIC", "homeassistant")
HOST = os.getenv("MQTT_HOST", "mosquitto"); PORT = int(os.getenv("MQTT_PORT", "1883"))
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086"); INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", ""); INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "")
HTTP_TIMEOUT = float(os.getenv("SHELLY_HTTP_TIMEOUT", "2"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("shelly-control")

DEFAULTS = {"enabled": False, "host": "", "setpoint_w": 20, "min_w": 0, "max_w": 800,
            "slot": 1, "deadband_w": 10, "interval_s": 2.0, "gain": 0.8, "fallback_w": 0, "device": None}

state = {"cfg": dict(DEFAULTS), "device": os.getenv("DEVICE_ID") or None, "out_w": None,
         "last_write": 0.0, "last_target": None, "fail_since": None}
lock = threading.Lock()
mq = None


def num(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def read_shelly(host):
    """Netzleistung in W (positiv = Bezug aus dem Netz). Unterstützt Shelly Gen2 EM/EM1 und Gen1."""
    base = host if host.startswith("http") else f"http://{host}"
    # Gen2 Pro 3EM (dreiphasig)
    for path, key in (("/rpc/EM.GetStatus?id=0", "total_act_power"), ("/rpc/EM1.GetStatus?id=0", "act_power")):
        try:
            with urllib.request.urlopen(f"{base}{path}", timeout=HTTP_TIMEOUT) as r:
                d = json.loads(r.read())
            v = num(d.get(key))
            if v is not None:
                return v
        except Exception:
            pass
    # Gen1 (/status -> total_power) und Gen2 Shelly.GetStatus (em:0)
    try:
        with urllib.request.urlopen(f"{base}/status", timeout=HTTP_TIMEOUT) as r:
            d = json.loads(r.read())
        if "total_power" in d:
            return num(d["total_power"])
        if isinstance(d.get("meters"), list) and d["meters"]:
            return sum(num(m.get("power"), 0) for m in d["meters"])
    except Exception:
        pass
    raise RuntimeError("keine Zählerdaten vom Shelly")


def publish_state(grid, household, out, target, ok, reason):
    payload = {"ts": int(time.time()), "grid_w": grid, "household_w": household, "out_w": out,
               "target_w": target, "setpoint_w": state["cfg"]["setpoint_w"], "ok": ok, "reason": reason,
               "enabled": state["cfg"]["enabled"], "host": state["cfg"]["host"]}
    if mq is not None:
        mq.publish(f"{BASE}/grolo/shelly/state", json.dumps(payload), retain=True)
    influx_write(grid, household, out, target, ok)


def influx_write(grid, household, out, target, ok):
    if not (INFLUX_TOKEN and INFLUX_ORG and INFLUX_BUCKET):
        return
    fields = []
    for k, v in (("grid_w", grid), ("household_w", household), ("out_w", out), ("target_w", target)):
        if v is not None:
            fields.append(f"{k}={float(v)}")
    fields.append(f"ok={'true' if ok else 'false'}")
    line = "shelly " + ",".join(fields) + f" {int(time.time())}"
    try:
        q = urllib.parse.urlencode({"org": INFLUX_ORG, "bucket": INFLUX_BUCKET, "precision": "s"})
        req = urllib.request.Request(f"{INFLUX_URL}/api/v2/write?{q}", data=line.encode(), method="POST",
                                     headers={"Authorization": f"Token {INFLUX_TOKEN}", "Content-Type": "text/plain"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        LOG.debug("InfluxDB: %s", e)


def write_output(target):
    dev = state["cfg"].get("device") or state["device"]
    if not dev or mq is None:
        return False
    slot = int(state["cfg"]["slot"])
    mq.publish(f"{BASE}/number/grobro/{dev}/slot{slot}_power/set", str(int(round(target))))
    state["last_write"] = time.time(); state["last_target"] = int(round(target))
    return True


def control_step():
    cfg = state["cfg"]
    if not cfg.get("enabled") or not cfg.get("host"):
        return
    try:
        grid = read_shelly(cfg["host"])
    except Exception as e:
        # Shelly nicht erreichbar: nach 30 s auf Sicherheitsleistung, weiter melden
        if state["fail_since"] is None:
            state["fail_since"] = time.time(); LOG.warning("Shelly nicht erreichbar: %s", e)
        if time.time() - state["fail_since"] > 30:
            if state["last_target"] != cfg["fallback_w"]:
                write_output(cfg["fallback_w"]); LOG.warning("Shelly-Ausfall > 30 s, Ausgang auf %s W", cfg["fallback_w"])
        publish_state(None, None, state["out_w"], state["last_target"], False, "shelly_unreachable")
        return
    state["fail_since"] = None
    # Regler-Integrator auf Basis des zuletzt kommandierten Werts (glatt, keine Schwingung bei traeger Rueckmeldung)
    cur = state["last_target"] if state["last_target"] is not None else (state["out_w"] or 0)
    error = grid - cfg["setpoint_w"]
    target = (cur or 0) + cfg["gain"] * error
    target = max(cfg["min_w"], min(cfg["max_w"], target))
    now = time.time()
    if (state["last_target"] is None or abs(target - state["last_target"]) >= cfg["deadband_w"]) and now - state["last_write"] >= max(1.0, cfg["interval_s"]):
        write_output(target)
    # Anzeige: echter gemessener Ausgang (falls vorhanden) fuer Hausverbrauch, sonst der kommandierte Wert
    real_out = state["out_w"] if state["out_w"] is not None else cur
    household = grid + (real_out or 0)
    publish_state(grid, household, real_out, state["last_target"], True, "ok")


def apply_cfg(payload):
    try:
        d = json.loads(payload)
    except Exception:
        return
    with lock:
        cfg = dict(DEFAULTS)
        cfg.update({k: d[k] for k in DEFAULTS if k in d and d[k] is not None})
        for k in ("setpoint_w", "min_w", "max_w", "slot", "deadband_w", "fallback_w"):
            cfg[k] = int(num(cfg[k], DEFAULTS[k]))
        for k in ("interval_s", "gain"):
            cfg[k] = num(cfg[k], DEFAULTS[k])
        cfg["enabled"] = bool(cfg["enabled"])
        was = state["cfg"].get("enabled")
        state["cfg"] = cfg
        LOG.info("Konfiguration: %s", {k: cfg[k] for k in ("enabled", "host", "setpoint_w", "min_w", "max_w", "slot")})
        if was and not cfg["enabled"]:
            state["last_target"] = None  # bei Abschalten Regelung loslassen


def on_connect(client, userdata, flags, rc, props=None):
    LOG.info("MQTT verbunden %s:%s", HOST, PORT)
    client.subscribe([(f"{BASE}/grolo/config/shelly", 0), (f"{BASE}/grobro/+/state", 0)])


def on_message(client, userdata, msg):
    if msg.topic == f"{BASE}/grolo/config/shelly":
        apply_cfg(msg.payload); return
    parts = msg.topic.split("/")
    if len(parts) == 4 and parts[1] == "grobro" and parts[3] == "state":
        try:
            st = json.loads(msg.payload)
        except Exception:
            return
        state["device"] = state["device"] or parts[2]
        og = num(st.get("onGridPower"))
        if og is not None:
            state["out_w"] = (og - 30000.0) / 10.0


def loop():
    while True:
        try:
            control_step()
        except Exception as e:
            LOG.exception("Regelschritt: %s", e)
        time.sleep(max(1.0, state["cfg"].get("interval_s", 2.0)))


def main():
    global mq
    mq = mqtt.Client(client_id="grolo-shelly-control", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    mq.on_connect = on_connect; mq.on_message = on_message
    while True:
        try:
            mq.connect(HOST, PORT, 60); break
        except Exception as e:
            LOG.warning("MQTT nicht erreichbar (%s), neuer Versuch", e); time.sleep(5)
    mq.loop_start()
    threading.Thread(target=loop, daemon=True).start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
