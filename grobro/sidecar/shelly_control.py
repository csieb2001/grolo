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

Veröffentlicht retained <BASE>/grolo/shelly/state (JSON: grid_w, household_w, out_w, target_w, setpoint_w, ok, limited, reason)
und schreibt dieselben Größen nach InfluxDB (Measurement "shelly"). Die Website (web-push) spiegelt den Zustand.

reason: ok | shelly_unreachable | device_offline | battery_low | device_limited.
  limited = der NEXA liefert seit > 60 s deutlich weniger als angefordert (z. B. Batterie an der Entladegrenze). Der Regler
  zieht das Ziel dann nicht weiter auf (kein Integrator-Windup), sondern hält es knapp über dem gemessenen Ausgang.

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

state = {"cfg": dict(DEFAULTS), "device": os.getenv("DEVICE_ID") or None, "out_w": None, "out_ts": 0.0,
         "last_write": 0.0, "last_target": None, "fail_since": None,
         "soc": None, "soc_limit": None, "online": None, "lag_since": None, "limited": False}
LAG_W = 100.0       # ab dieser Abweichung Ziel > Ausgang gilt der NEXA als "folgt nicht"
LAG_S = 60.0        # ... wenn sie so lange anhält
STALE_S = 120.0     # Gerätewerte älter als das gelten als unbekannt
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


def publish_state(grid, household, out, target, ok, reason, limited=False):
    payload = {"ts": int(time.time()), "grid_w": grid, "household_w": household, "out_w": out,
               "target_w": target, "setpoint_w": state["cfg"]["setpoint_w"], "ok": ok, "limited": bool(limited), "reason": reason,
               "soc": state["soc"], "soc_limit": state["soc_limit"], "min_w": state["cfg"]["min_w"], "max_w": state["cfg"]["max_w"],
               "enabled": state["cfg"]["enabled"], "host": state["cfg"]["host"]}
    if mq is not None:
        mq.publish(f"{BASE}/grolo/shelly/state", json.dumps(payload), retain=True)
    influx_write(grid, household, out, target, ok, limited, reason)


def influx_write(grid, household, out, target, ok, limited=False, reason="ok"):
    if not (INFLUX_TOKEN and INFLUX_ORG and INFLUX_BUCKET):
        return
    fields = []
    for k, v in (("grid_w", grid), ("household_w", household), ("out_w", out), ("target_w", target)):
        if v is not None:
            fields.append(f"{k}={float(v)}")
    fields.append(f"ok={'true' if ok else 'false'}")
    fields.append(f"limited={'true' if limited else 'false'}")
    fields.append(f'reason="{reason}"')
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
    now = time.time()
    try:
        grid = read_shelly(cfg["host"])
    except Exception as e:
        # Shelly nicht erreichbar: nach 30 s auf Sicherheitsleistung, weiter melden
        if state["fail_since"] is None:
            state["fail_since"] = now; LOG.warning("Shelly nicht erreichbar: %s", e)
        if now - state["fail_since"] > 30:
            if state["last_target"] != cfg["fallback_w"]:
                write_output(cfg["fallback_w"]); LOG.warning("Shelly-Ausfall > 30 s, Ausgang auf %s W", cfg["fallback_w"])
        publish_state(None, None, state["out_w"], state["last_target"], False, "shelly_unreachable")
        return
    if state["fail_since"] is not None:
        LOG.info("Shelly wieder erreichbar (Netz %.0f W), Regelung läuft weiter", grid)
    state["fail_since"] = None
    # Gerätewerte: gemessener Ausgang (Reg. 116) nur, wenn frisch und der NEXA online ist
    device_ok = state["online"] is not False and state["out_w"] is not None and now - state["out_ts"] < STALE_S
    real_out = state["out_w"] if device_ok else None
    # Regler-Integrator auf Basis des zuletzt kommandierten Werts (glatt, keine Schwingung bei traeger Rueckmeldung)
    cur = state["last_target"] if state["last_target"] is not None else (real_out or 0)
    error = grid - cfg["setpoint_w"]
    target = (cur or 0) + cfg["gain"] * error
    target = max(cfg["min_w"], min(cfg["max_w"], target))
    # Folgt der NEXA nicht (Ziel liegt dauerhaft weit über dem gemessenen Ausgang, z. B. Batterie an der Entladegrenze),
    # Ziel nicht weiter aufziehen, sondern knapp über dem Ausgang halten; sobald er wieder liefert, geht es normal weiter.
    if not state["limited"]:
        if device_ok and state["last_target"] is not None and state["last_target"] - real_out > LAG_W:
            state["lag_since"] = state["lag_since"] or now
        else:
            state["lag_since"] = None
        limited = state["lag_since"] is not None and now - state["lag_since"] > LAG_S
    else:
        # Hysterese: erst loslassen, wenn der NEXA dem (begrenzten) Ziel bis auf die halbe Toleranz folgt
        limited = not (device_ok and state["last_target"] is not None and real_out >= state["last_target"] - LAG_W / 2)
        if not limited:
            state["lag_since"] = None
    if limited:
        target = max(cfg["min_w"], min(target, real_out + LAG_W))
    if limited != state["limited"]:
        state["limited"] = limited
        if limited:
            LOG.warning("NEXA folgt nicht: Ziel %s W, Ausgang %.0f W, SoC %s %% (Grenze %s %%). Ziel wird begrenzt.",
                        state["last_target"], real_out, state["soc"], state["soc_limit"])
        else:
            LOG.info("NEXA liefert wieder (Ausgang %.0f W), Begrenzung aufgehoben", real_out or 0)
    if (state["last_target"] is None or abs(target - state["last_target"]) >= cfg["deadband_w"]) and now - state["last_write"] >= max(1.0, cfg["interval_s"]):
        write_output(target)
    if state["online"] is False:
        reason = "device_offline"
    elif limited:
        reason = "battery_low" if (state["soc"] is not None and state["soc_limit"] is not None and state["soc"] <= state["soc_limit"] + 5) else "device_limited"
    else:
        reason = "ok"
    # Anzeige: echter gemessener Ausgang (falls vorhanden) fuer Hausverbrauch, sonst der kommandierte Wert
    shown_out = real_out if real_out is not None else (0.0 if state["online"] is False else cur)
    household = grid + (shown_out or 0)
    publish_state(grid, household, shown_out, state["last_target"], True, reason, limited)


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
        switched_off = bool(was and not cfg["enabled"])
        if switched_off:
            state["last_target"] = None; state["lag_since"] = None; state["limited"] = False  # bei Abschalten Regelung loslassen
    if switched_off:
        publish_state(None, None, state["out_w"], None, True, "disabled")


def on_connect(client, userdata, flags, rc, props=None):
    LOG.info("MQTT verbunden %s:%s", HOST, PORT)
    client.subscribe([(f"{BASE}/grolo/config/shelly", 0), (f"{BASE}/grobro/+/state", 0), (f"{BASE}/grobro/+/availability", 0)])


def on_message(client, userdata, msg):
    if msg.topic == f"{BASE}/grolo/config/shelly":
        apply_cfg(msg.payload); return
    parts = msg.topic.split("/")
    if len(parts) == 4 and parts[1] == "grobro" and parts[3] == "availability":
        dev = state["cfg"].get("device") or state["device"]
        if dev and parts[2] != dev:
            return
        online = msg.payload.decode(errors="ignore").strip().lower() == "online"
        if state["online"] is not None and online != state["online"]:
            LOG.warning("NEXA %s", "wieder online" if online else "offline (keine Daten vom Dongle)")
        state["online"] = online
        return
    if len(parts) == 4 and parts[1] == "grobro" and parts[3] == "state":
        dev = state["cfg"].get("device") or state["device"]
        if dev and parts[2] != dev:
            return
        try:
            st = json.loads(msg.payload)
        except Exception:
            return
        state["device"] = state["device"] or parts[2]
        og = num(st.get("onGridPower"))
        if og is not None:
            state["out_w"] = (og - 30000.0) / 10.0; state["out_ts"] = time.time()
        if num(st.get("totalBatteryPackSoc")) is not None:
            state["soc"] = num(st.get("totalBatteryPackSoc"))
        if num(st.get("dischargeSocLimit")) is not None:
            state["soc_limit"] = num(st.get("dischargeSocLimit"))


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
