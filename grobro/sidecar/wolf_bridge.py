#!/usr/bin/env python3
"""wolf-bridge: übersetzt zwischen ism7mqtt (Wolf Link, lokal) und dem GroLo-Stack.

ism7mqtt spricht direkt mit dem WOLF Link auf Port 9092 und veröffentlicht je Busteilnehmer ein Topic
    Wolf/<ip>/<Gerät>_<Busadresse>
mit JSON-Teilaktualisierungen: nur die Parameter, die gerade gelesen wurden, benannt wie im Wolf-Menü
(„Erzeugte Wärmemenge Vortag"), Auswahlparameter als {"value":…, "text":…}, mehrfach vergebene Namen als
{"<PTID>": …}. Dieses Sidecar hält daraus den vollständigen Zustand je Gerät und veröffentlicht:

  <BASE>/grolo/wolf/<gerät>/state   flache Zahlenfelder (Feldnamen aus wolf/catalog.json) für Telegraf,
                                    Klartext von Auswahlparametern zusätzlich als „<feld>_text"
  <BASE>/grolo/wolf/derived         berechnete Kennzahlen, die die Wolf selbst nicht liefert: momentaner
                                    COP, Spreizung, Taktung, Abtauzyklen, Laufzeitanteile Heizen/Warmwasser,
                                    Arbeitszahlen für Tag/Monat/Jahr
  <BASE>/grolo/wolf/cycle           je beendetem Verdichterlauf eine Nachricht: Dauer, Pause davor,
                                    Betriebsart, Frequenz, Außentemperatur, Wärme und COP des Takts
  <BASE>/grolo/wolf/cycling         Auswertung der Takt-Historie (retained): Verteilung der Laufzeiten,
                                    Takte je Außentemperatur, Hochrechnung aufs Jahr und der Befund daraus
  <BASE>/grolo/wolf/catalog         der Parameterkatalog mit den zur Laufzeit gefundenen Geräten (retained,
                                    Grundlage für die Bedienseite wolf.html)
  <BASE>/grolo/wolf/status          Verbindungszustand und gefundene Busteilnehmer (retained)

Schreiben: eine Nachricht auf <BASE>/grolo/wolf/set
    {"device": "heatpump", "key": "warmwassersolltemperatur", "value": 52}
    {"device": "circuit",  "key": "programmwahl_360051",      "text": "Auto"}
wird gegen den Katalog geprüft (schreibbar, Grenzwerte, erlaubte Auswahl) und als
    Wolf/<ip>/<Gerät>_<Bus>/set   {"Warmwassersolltemperatur": 52}
weitergereicht. Das Ergebnis kommt auf <BASE>/grolo/wolf/set/result.

Die Tageszähler (Takte, Laufzeiten, Abtauungen) und der zuletzt bekannte Wert jedes Parameters liegen in
/state/wolf.json und überleben einen Neustart. Das ist nötig, weil ism7mqtt nach dem ersten vollständigen
Lesedurchlauf nur noch geänderte Werte schickt: ohne gespeicherten Zustand hätte die Bridge nach einem
Neustart nur noch die paar Parameter, die sich seitdem bewegt haben.

Parameter der Fachmann-Ebene (in der Smartset-App hinter dem Fachmann-Code) verlangen zusätzlich
    {"device": "heatpump", "key": "bivalenzpunkt_e_heizung", "value": -7, "pin": "1111"}
sofern WOLF_EXPERT_PIN gesetzt ist. Der Code lässt sich vorab prüfen: <BASE>/grolo/wolf/unlock mit
{"pin": "…"} antwortet auf <BASE>/grolo/wolf/unlock/result.

Umgebung: MQTT_HOST, MQTT_PORT, HA_BASE_TOPIC, WOLF_CATALOG (Standard /wolf/catalog.json),
          WOLF_STATE (Standard /state/wolf.json), WOLF_PUBLISH_INTERVAL (s, Standard 10),
          WOLF_EXPERT_PIN (leer = Fachmann-Ebene ohne Code), TZ
"""
import json, logging, math, os, threading, time
import paho.mqtt.client as mqtt

BASE = os.getenv("HA_BASE_TOPIC", "homeassistant")
HOST = os.getenv("MQTT_HOST", "mosquitto"); PORT = int(os.getenv("MQTT_PORT", "1883"))
CATALOG = os.getenv("WOLF_CATALOG", "/wolf/catalog.json")
STATE_FILE = os.getenv("WOLF_STATE", "/state/wolf.json")
INTERVAL = float(os.getenv("WOLF_PUBLISH_INTERVAL", "10"))
EXPERT_PIN = os.getenv("WOLF_EXPERT_PIN", "").strip()   # leer = Fachmann-Ebene ohne Code schreibbar
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("wolf-bridge")

# Verdichterstatus (PTID 270051) und Betriebsart Heizgerät (270050), soweit wir sie für Kennzahlen brauchen
ST_BETRIEB, ST_ABTAU = 5, 6
ST_SPERRE = {8, 9, 10}
MODE_WW = {7, 8, 9}          # Warmwasser, WW-Nachlauf, Antilegionellenfunktion
MODE_HZ = {10, 11}           # Heizbetrieb, HZ-Nachlauf
MODE_KUEHL = {12, 17}        # Aktive Kühlung, Nachlauf K


class Device:
    """Ein Busteilnehmer: Katalogbeschreibung plus der zuletzt gesehene Wert je Parameter."""

    def __init__(self, entry, ident):
        self.ident = ident                       # unser stabiler Name, z. B. "heatpump"
        self.dtid = entry["dtid"]
        self.bus = entry["read"]
        self.role = entry["role"]
        self.label_de, self.label_en = entry["de"], entry["en"]
        self.params = entry["params"]
        self.topic = None                        # Wolf/<ip>/<Gerät>_<Bus>, sobald erkannt
        self.values = {}                         # key -> Zahl
        self.texts = {}                          # key -> Klartext
        self.seen = 0
        self.by_name = {}                        # Wolf-Name -> [Eintrag, …]
        self.by_key = {}
        for p in self.params:
            self.by_name.setdefault(p["de"], []).append(p)
            self.by_key[p["key"]] = p

    def match(self, names):
        """Wie gut passen die Parameternamen einer eingehenden Nachricht zu diesem Gerät?"""
        return sum(1 for n in names if n in self.by_name)

    def entry_for(self, name, ptid):
        candidates = self.by_name.get(name)
        if not candidates:
            return None
        if ptid is None:
            return candidates[0] if len(candidates) == 1 else None
        for c in candidates:
            if c["ptid"] == ptid:
                return c
        return None

    def apply(self, payload):
        """Teilaktualisierung von ism7mqtt einarbeiten."""
        changed = 0
        for name, node in payload.items():
            for ptid, value, text in unpack(node):
                entry = self.entry_for(name, ptid)
                if entry is None:
                    continue
                key = entry["key"]
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self.values[key] = value
                if text is not None:
                    self.texts[key] = text
                elif isinstance(value, str):
                    self.texts[key] = value
                if entry.get("options") and key in self.values:
                    want = str(int(self.values[key])) if float(self.values[key]).is_integer() else str(self.values[key])
                    for o in entry["options"]:
                        if o["v"] == want:
                            self.texts[key] = o["de"]
                            break
                changed += 1
        if changed:
            self.seen = time.time()
        return changed

    def state_payload(self):
        out = dict(self.values)
        for key, text in self.texts.items():
            out[f"{key}_text"] = text
        return out

    def restore(self, saved):
        """Zuletzt bekannte Werte übernehmen, aber nur für Parameter, die es noch gibt."""
        self.values = {k: v for k, v in saved.get("values", {}).items() if k in self.by_key}
        self.texts = {k: v for k, v in saved.get("texts", {}).items() if k in self.by_key}
        return len(self.values)


def unpack(node):
    """Ein JSON-Knoten von ism7mqtt -> Liste aus (PTID oder None, Zahl/Text, Klartext oder None).

    Möglich sind: 5.1  ·  {"value":1,"text":"Ein"}  ·  {"350009":55,"350014":55}
                  ·  {"360051":{"value":1,"text":"Auto"},"360058":1}
    """
    if isinstance(node, dict):
        if "value" in node or "text" in node:
            return [(None, node.get("value"), node.get("text"))]
        out = []
        for k, sub in node.items():
            if not k.isdigit():
                continue
            ptid = int(k)
            if isinstance(sub, dict):
                out.append((ptid, sub.get("value"), sub.get("text")))
            else:
                out.append((ptid, sub, None))
        return out
    return [(None, node, None)]


def mode_name(modus):
    """Betriebsart der Wolf -> kurzer Name, unter dem wir einen Takt einsortieren."""
    if modus in MODE_WW:
        return "ww"
    if modus in MODE_HZ:
        return "hz"
    if modus in MODE_KUEHL:
        return "kuehl"
    return "?"


def median(values):
    v = sorted(values)
    if not v:
        return None
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2.0


def quantile(values, q):
    v = sorted(values)
    if not v:
        return None
    i = min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))
    return v[i]


def span_phrase(days):
    """„die bisherigen 7 Stunden" bzw. „die bisherigen 2 Tage" – am ersten Tag sind Tage keine sinnvolle Einheit."""
    if days < 1:
        h = max(1, int(round(days * 24)))
        return {"de": f"die bisherigen {h} Stunden" if h > 1 else "die bisherige Stunde",
                "en": f"the {h} hours so far" if h > 1 else "the first hour"}
    d = int(round(days))
    return {"de": f"die bisherigen {d} Tage" if d > 1 else "den bisherigen Tag",
            "en": f"the {d} days so far" if d > 1 else "the first day"}


def days_phrase(days):
    """„2 Tage" / „1 Tag", aufgerundet: eine angebrochene Wartezeit ist eine ganze."""
    d = max(1, math.ceil(days))
    return {"de": f"{d} Tage" if d > 1 else "1 Tag", "en": f"{d} days" if d > 1 else "1 day"}


def takte(n):
    """„1 Takt" / „4 Takte", zweisprachig – Zahl und Einheit gehören im Satz zusammen."""
    return {"de": f"{n} Takt" if n == 1 else f"{n} Takte", "en": f"{n} cycle" if n == 1 else f"{n} cycles"}


def P(device, key, de, en):
    """Zeiger auf den Parameter, an dem man dreht. Die Bedienseite verlinkt darauf, verstellt wird nichts."""
    return {"device": device, "key": key, "de": de, "en": en}


class Cycles:
    """Die einzelnen Verdichterläufe und der Befund daraus.

    Die Tageszähler sagen, *wie oft* getaktet wird; erst der einzelne Takt sagt, *wie*. Der BWP nennt bewusst
    keine feste Startzahl als Grenze, sondern die Laufzeit am Stück: unter zehn Minuten ist Kurztakten, 30 bis
    60 Minuten sind der gesunde Bereich. Und ob Kurztakten stört, hängt an der Außentemperatur des Takts:
    in der Übergangszeit fällt die Gebäudelast unter die Mindestleistung des Verdichters – das ist ein
    Regelungsthema (Heizkurve, Hysterese). Bei Frost dagegen ist Kurztakten hydraulisch (Durchfluss, Volumen,
    Puffer; VDI 4645 nennt 20 l je kW) oder die Anlage ist schlicht zu groß. Nur mit der Temperatur je Takt
    lassen sich die beiden auseinanderhalten, deshalb wird sie mitgeschrieben.
    """

    KEEP = 4000          # Takte in der Historie, rund ein Jahr bei zehn Starts am Tag
    SHORT = 10.0         # Minuten am Stück: darunter Kurztakt
    GOOD = 30.0          # ab hier gesunde Laufzeit
    WINDOW = 14          # Tage, über die der Befund gebildet wird
    MIN_DAYS = 3         # darunter sagen wir lieber nichts
    WARM = 8.0           # Außentemperatur der Übergangszeit
    COLD = 3.0           # Außentemperatur, ab der es kalt genug für volle Last wäre
    BUCKETS = ((0, 10), (10, 20), (20, 30), (30, 60), (60, None))

    def __init__(self):
        self.items = []          # abgeschlossene Takte, älteste zuerst
        self.open = None         # der gerade laufende Takt
        self.last_end = None     # Ende des vorigen Takts, für die Pause

    # ---------------------------------------------------------------- aufzeichnen
    def load(self, saved):
        items = saved.get("cycles") or []
        self.items = [c for c in items if isinstance(c, dict) and c.get("min") is not None][-self.KEEP:]
        self.last_end = self.items[-1]["end"] if self.items else None
        if self.items:
            LOG.info("Takt-Historie übernommen: %d Takte", len(self.items))

    def begin(self, now, hp):
        self.open = {"start": now, "n": 0, "freq": 0.0, "freq_max": 0.0, "t_out": 0.0, "t_n": 0,
                     "flow": 0.0, "flow_n": 0, "kwh": 0.0, "el_kwh": 0.0, "defrost": 0,
                     "mode": mode_name(hp.get("betriebsart_heizgeraet"))}

    def sample(self, hp, minutes):
        """Einen Zeitschritt in den laufenden Takt einrechnen."""
        c = self.open
        if c is None:
            return
        freq = hp.get("verdichterfrequenz")
        if freq is not None:
            c["n"] += 1
            c["freq"] += freq
            c["freq_max"] = max(c["freq_max"], freq)
        for key, total, count in (("aussentemperatur", "t_out", "t_n"), ("kesseltemperatur", "flow", "flow_n")):
            value = hp.get(key)
            if value is not None:
                c[total] += value
                c[count] += 1
        p_th, p_el = hp.get("aktuelle_sekundaerleistung"), hp.get("leistungsaufnahme_wp_ehz")
        if p_th:
            c["kwh"] += p_th * minutes / 60.0
        if p_el:
            c["el_kwh"] += p_el * minutes / 60.0
        if hp.get("verdichterstatus") == ST_ABTAU:
            c["defrost"] = 1
        if c["mode"] == "?":
            c["mode"] = mode_name(hp.get("betriebsart_heizgeraet"))

    def end(self, now):
        """Den laufenden Takt abschließen und zurückgeben, damit ihn die Bridge veröffentlicht."""
        c, self.open = self.open, None
        if c is None:
            return None
        minutes = round((now - c["start"]) / 60.0, 1)
        if minutes < 0.2:
            return None                      # ein einzelner Messpunkt ist kein Takt
        item = {"start": int(c["start"]), "end": int(now), "min": minutes, "mode": c["mode"],
                "pause_min": None if self.last_end is None else round((c["start"] - self.last_end) / 60.0, 1),
                "defrost": c["defrost"],
                "freq": round(c["freq"] / c["n"], 1) if c["n"] else None,
                "freq_max": round(c["freq_max"], 1) if c["n"] else None,
                "t_out": round(c["t_out"] / c["t_n"], 1) if c["t_n"] else None,
                "flow_c": round(c["flow"] / c["flow_n"], 1) if c["flow_n"] else None,
                "kwh": round(c["kwh"], 3), "el_kwh": round(c["el_kwh"], 3)}
        if item["el_kwh"] > 0.01:
            item["cop"] = round(item["kwh"] / item["el_kwh"], 2)
        self.last_end = now
        self.items.append(item)
        del self.items[:-self.KEEP]
        return item

    # ---------------------------------------------------------------- auswerten
    def window(self):
        since = time.time() - self.WINDOW * 86400
        return [c for c in self.items if c["start"] >= since]

    def stats(self, hp=None, today=None):
        """Verteilung, Temperaturabhängigkeit, Hochrechnung und Befund über das Auswertefenster.

        `today` sind die Tageszähler. Sie tragen den ersten Tag, an dem es noch keine Historie gibt: wie oft
        der Verdichter heute lief, steht ab dem ersten Takt fest und muss nicht auf drei Tage warten.
        """
        items = self.window()
        now = time.time()
        span = 0.0 if not items else max((now - items[0]["start"]) / 86400.0, 1.0 / 24)
        day = today or {}
        out = {"window_days": self.WINDOW, "span_days": round(span, 2), "cycles": len(items),
               "cycles_today": int(day.get("takte", 0)), "runtime_today_min": round(day.get("laufzeit_min", 0.0), 1)}
        if int(day.get("takte", 0)) >= 1:
            out["per_cycle_today_min"] = round(day["laufzeit_min"] / day["takte"], 1)
        if not items:
            laufend = {"de": " Der Verdichter läuft gerade seinen ersten Lauf.",
                       "en": " The compressor is on its first run right now."} if out["cycles_today"] >= 1 else ""
            out["verdict"] = self.verdict("keine_takte", laufend=laufend)
            return out

        runs = [c["min"] for c in items]
        hz = [c for c in items if c["mode"] == "hz"]
        ww = [c for c in items if c["mode"] == "ww"]
        short = [c for c in items if c["min"] < self.SHORT]
        pauses = [c["pause_min"] for c in items if c.get("pause_min") is not None and c["pause_min"] < 720]

        # Anteile und Mittelwerte gelten ab dem ersten Takt. Alles, was „je Tag" heißt, erst ab einem ganzen
        # Tag – sonst wären zwei Takte in der ersten Stunde 48 Takte am Tag, und die Kachel stünde grundlos rot.
        out.update({
            "median_min": round(median(runs), 1),
            "p25_min": round(quantile(runs, 0.25), 1),
            "p75_min": round(quantile(runs, 0.75), 1),
            "median_hz_min": round(median([c["min"] for c in hz]), 1) if hz else None,
            "median_pause_min": round(median(pauses), 1) if pauses else None,
            "short_share": round(len(short) / len(items), 3),
            "defrost_share": round(sum(c["defrost"] for c in items) / len(items), 3),
            "by_mode": {m: sum(1 for c in items if c["mode"] == m) for m in ("hz", "ww", "kuehl", "?")},
            "buckets": [{"from": lo, "to": hi,
                         "n": sum(1 for c in items if c["min"] >= lo and (hi is None or c["min"] < hi))}
                        for lo, hi in self.BUCKETS],
        })
        if span >= 1.0:
            out.update({
                "per_day": round(len(items) / span, 1),
                "short_per_day": round(len(short) / span, 1),
                "ww_per_day": round(len(ww) / span, 1),
                "runtime_share": round(sum(runs) / (span * 1440.0), 3),
            })
        # Takte je 2-K-Klasse der Außentemperatur: hier wird sichtbar, ob das Takten aus der Übergangszeit kommt
        bins = {}
        for c in items:
            if c.get("t_out") is None:
                continue
            key = int(c["t_out"] // 2) * 2
            b = bins.setdefault(key, {"t": key, "n": 0, "runs": []})
            b["n"] += 1
            b["runs"].append(c["min"])
        out["by_temp"] = [{"t": b["t"], "n": b["n"], "median_min": round(median(b["runs"]), 1)}
                          for b in sorted(bins.values(), key=lambda b: b["t"])]

        # Hochrechnung aufs Jahr erst, wenn die Datenbasis trägt – sonst ist sie eine Zufallszahl mal 365
        if span >= self.MIN_DAYS:
            out["starts_per_year"] = int(round(len(items) / span * 365))
        if hp:
            starts, hours = hp.get("verdichterstarts"), hp.get("betriebsstunden_verdichter")
            if starts:
                out["starts_total"] = starts
                if hours:
                    out["lifetime_per_cycle_min"] = round(hours * 60.0 / starts, 1)
            if hp.get("verdichter_max_starts_pro_stunde") is not None:
                out["max_starts_hour"] = hp["verdichter_max_starts_pro_stunde"]

        out["verdict"] = self.judge(out, items, short, hp or {})
        out["notes"] = self.notes(out, items, hp or {})
        return out

    # ---------------------------------------------------------------- Befund
    TEXTS = {
        "keine_takte": (
            "Noch kein abgeschlossener Takt aufgezeichnet.{laufend} Die Auswertung beginnt mit dem ersten "
            "beendeten Verdichterlauf.",
            "No completed cycle recorded yet.{laufend} The evaluation starts with the first finished "
            "compressor run.", "info", []),
        "erste_takte": (
            "Heute {today}, zusammen {today_min} min Laufzeit{today_je}. Über {seit}: {n}, im Mittel "
            "{median} min am Stück, {short_pct} % davon unter {short} min. Für den vollen Befund fehlen "
            "noch {fehlt}.",
            "Today {today}, {today_min} min of runtime in total{today_je}. Over {seit}: {n}, {median} min "
            "per run on average, {short_pct} % of them below {short} min. The full verdict needs another "
            "{fehlt}.",
            "info", []),
        "erste_takte_kurz": (
            "Heute {today}, zusammen {today_min} min Laufzeit{today_je}. Schon jetzt bleiben "
            "{short_pct} % der Takte unter {short} min, im Mittel {median} min am Stück – das sieht nach "
            "Kurztakten aus. Ein belastbarer Befund braucht noch {fehlt}; bis dahin ist die Hysterese "
            "Heizbetrieb die erste Stellschraube, die man beobachten sollte.",
            "Today {today}, {today_min} min of runtime in total{today_je}. Already {short_pct} % of "
            "the cycles stay below {short} min, {median} min per run on average – that looks like short "
            "cycling. A solid verdict needs another {fehlt}; until then the heating hysteresis is the "
            "first thing to watch.",
            "warn", [P("heatpump", "hysterese_heizbetrieb", "Hysterese Heizbetrieb größer", "widen heating hysteresis")]),
        "gesund": (
            "Die Taktung ist gesund: im Mittel {median} min am Stück bei {per_day} Starts am Tag. "
            "Nichts zu tun.",
            "Cycling is healthy: {median} min per run on average at {per_day} starts a day. Nothing to do.",
            "ok", []),
        "uebergangszeit": (
            "Kurztakten in der Übergangszeit: {short_pct} % der Takte bleiben unter {short} min, und die "
            "meisten davon liegen über {warm} °C Außentemperatur. Dann fällt die Last des Hauses unter die "
            "Mindestleistung des Verdichters. Die Heizkurve flacher stellen und die Hysterese Heizbetrieb "
            "vergrößern – Nachtabsenkung raus und Einzelraumregler öffnen wirken in dieselbe Richtung.",
            "Short cycling in the shoulder season: {short_pct} % of runs stay below {short} min, and most of "
            "them sit above {warm} °C outside. The house needs less than the compressor can turn down to. "
            "Flatten the heating curve and widen the heating hysteresis; dropping the night setback and "
            "opening the room thermostats pull the same way.",
            "warn", [P("circuit", "vorlauftemperatur_heizkurve", "Heizkurve flacher", "flatten heating curve"),
                     P("heatpump", "hysterese_heizbetrieb", "Hysterese Heizbetrieb größer", "widen heating hysteresis")]),
        "hydraulik": (
            "Kurztakten bei Kälte: {short_pct} % der Takte bleiben unter {short} min, und das auch unter "
            "{cold} °C, wo die Anlage eigentlich durchlaufen sollte. Das ist kein Regelungsthema, sondern "
            "hydraulisch: zu wenig Durchfluss oder zu wenig Wasservolumen im Kreis (VDI 4645: rund 20 l je kW). "
            "Pumpenleistung und Spreizung prüfen, Heizkreise öffnen – oder die 10-kW-Klasse ist für das Haus zu groß.",
            "Short cycling in the cold: {short_pct} % of runs stay below {short} min, even below {cold} °C where "
            "the unit should simply run through. That is not a control issue but a hydraulic one: too little flow "
            "or too little water volume in the circuit (VDI 4645: about 20 l per kW). Check pump output and spread, "
            "open the circuits – or the 10 kW class is oversized for this house.",
            "bad", [P("heatpump", "pumpenleistung_hk_minimal", "Pumpenleistung HK minimal", "minimum circuit pump output"),
                    P("heatpump", "freigabe_spreizungsregelung", "Spreizungsregelung", "spread control")]),
        "kurztakt": (
            "Die Anlage taktet kurz: {short_pct} % der Takte bleiben unter {short} min, im Mittel {median} min "
            "am Stück. Über die Außentemperatur verteilt sich das gleichmäßig, es ist also weder rein die "
            "Übergangszeit noch rein die Hydraulik. Zuerst die Hysterese Heizbetrieb vergrößern und beobachten.",
            "The unit cycles short: {short_pct} % of runs stay below {short} min, {median} min per run on "
            "average. It spreads evenly over outside temperature, so it is neither purely the shoulder season "
            "nor purely hydraulics. Widen the heating hysteresis first and watch.",
            "warn", [P("heatpump", "hysterese_heizbetrieb", "Hysterese Heizbetrieb größer", "widen heating hysteresis")]),
        "hysterese": (
            "Kurze Läufe und kurze Pausen: {median} min an, {pause} min aus. So eng geschaltet erreicht der "
            "Verdichter seinen Arbeitspunkt nie. Die Hysterese Heizbetrieb ist zu klein gewählt.",
            "Short runs and short pauses: {median} min on, {pause} min off. Switched that tightly the compressor "
            "never reaches its working point. The heating hysteresis is set too small.",
            "warn", [P("heatpump", "hysterese_heizbetrieb", "Hysterese Heizbetrieb größer", "widen heating hysteresis")]),
        "warmwasser": (
            "Das Warmwasser treibt die Taktung: {ww_per_day} Ladungen am Tag. Eine größere Hysterese "
            "Warmwasserbetrieb oder ein Zeitprogramm mit wenigen, längeren Ladungen spart Starts, ohne dass "
            "jemand kalt duscht.",
            "Hot water drives the cycling: {ww_per_day} charges a day. A wider hot-water hysteresis, or a time "
            "programme with few long charges, saves starts without anyone showering cold.",
            "info", [P("heatpump", "hysterese_warmwasserbetrieb", "Hysterese Warmwasserbetrieb größer", "widen hot-water hysteresis")]),
        "viele_takte": (
            "{per_day} Starts am Tag sind mehr als die üblichen 10 bis 15, auch wenn die einzelnen Takte mit "
            "{median} min lang genug sind. Hochgerechnet sind das {per_year} Starts im Jahr.",
            "{per_day} starts a day is more than the usual 10 to 15, even though the individual runs are long "
            "enough at {median} min. Projected over a year that is {per_year} starts.",
            "warn", [P("heatpump", "hysterese_heizbetrieb", "Hysterese Heizbetrieb größer", "widen heating hysteresis")]),
        "ordentlich": (
            "Die Taktung ist in Ordnung: {median} min am Stück bei {per_day} Starts am Tag. Zwischen 10 und 20 "
            "Minuten je Takt ist üblich, 30 bis 60 wären das Ideal.",
            "Cycling is fine: {median} min per run at {per_day} starts a day. 10 to 20 minutes per cycle is "
            "common, 30 to 60 would be ideal.",
            "info", []),
    }

    def verdict(self, code, **fmt):
        """Einen Befund in beiden Sprachen bauen. Ein Platzhalter darf als {"de": …, "en": …} kommen,
        wenn der eingesetzte Text selbst übersetzt werden muss."""
        de, en, severity, params = self.TEXTS[code]
        base = {"short": int(self.SHORT), "warm": int(self.WARM), "cold": int(self.COLD)}
        base.update(fmt)

        def values(lang):
            return {k: (v[lang] if isinstance(v, dict) and lang in v else v) for k, v in base.items()}

        return {"code": code, "severity": severity, "params": params,
                "de": de.format(**values("de")), "en": en.format(**values("en"))}

    def judge(self, st, items, short, hp):
        """Aus den Kennzahlen einen Befund machen. Reihenfolge ist Rangfolge: was am meisten stört, gewinnt."""
        per_day, ww_per_day = st.get("per_day"), st.get("ww_per_day")
        fmt = {"median": st["median_min"], "per_day": per_day, "pause": st.get("median_pause_min"),
               "short_pct": int(round(st["short_share"] * 100)), "ww_per_day": ww_per_day,
               "per_year": st.get("starts_per_year"), "n": st["cycles"],
               "days": round(st["span_days"], 1), "fehlt": round(self.MIN_DAYS - st["span_days"], 1),
               "today": st["cycles_today"], "today_min": int(round(st["runtime_today_min"]))}

        # Unter drei Tagen taugt die Historie noch nicht für eine Diagnose – der Tag selbst aber schon.
        # Deshalb sagen wir, was heute war, und nennen die Verteilung, so weit sie trägt.
        if st["span_days"] < self.MIN_DAYS:
            je = st.get("per_cycle_today_min")
            fmt["today_je"] = "" if je is None else {"de": f", im Mittel {je:.0f} min je Takt",
                                                     "en": f", {je:.0f} min per cycle on average"}
            fmt["median"] = round(st["median_min"])
            fmt["seit"] = span_phrase(st["span_days"])
            fmt["fehlt"] = days_phrase(self.MIN_DAYS - st["span_days"])
            fmt["today"] = takte(st["cycles_today"])
            fmt["n"] = takte(st["cycles"])
            early = "erste_takte_kurz" if (st["cycles"] >= 5 and st["short_share"] >= 0.4) else "erste_takte"
            return self.verdict(early, **fmt)

        if st["median_min"] >= 25 and st["short_share"] < 0.2 and per_day <= 15:
            return self.verdict("gesund", **fmt)

        if st["short_share"] >= 0.25:
            if sum(1 for c in short if c["mode"] == "ww") / len(short) >= 0.6:
                return self.verdict("warmwasser", **fmt)   # kurze Ladungen, nicht kurze Heizläufe
            warm = [c for c in short if c.get("t_out") is not None and c["t_out"] >= self.WARM]
            cold = [c for c in short if c.get("t_out") is not None and c["t_out"] <= self.COLD]
            known = len(warm) + len(cold)
            if known and len(warm) / max(len(short), 1) >= 0.5:
                return self.verdict("uebergangszeit", **fmt)
            if known and len(cold) / max(len(short), 1) >= 0.4:
                return self.verdict("hydraulik", **fmt)
            return self.verdict("kurztakt", **fmt)

        if st.get("median_pause_min") is not None and st["median_pause_min"] < 10 and st["median_min"] < 20:
            return self.verdict("hysterese", **fmt)

        if ww_per_day >= 4 and ww_per_day >= 0.4 * per_day:
            return self.verdict("warmwasser", **fmt)

        if per_day > 20:
            return self.verdict("viele_takte", **fmt)

        return self.verdict("ordentlich", **fmt)

    def notes(self, st, items, hp):
        """Nebenbefunde: wahr, aber nicht die Hauptsache."""
        out = []
        if st["defrost_share"] >= 0.25:
            out.append({"code": "abtauen", "severity": "info",
                        "de": f"{int(round(st['defrost_share'] * 100))} % der Takte enthalten eine Abtauung. "
                              "Zwischen 0 und 5 °C mit Feuchte ist das normal und zählt nicht als Taktfehler.",
                        "en": f"{int(round(st['defrost_share'] * 100))} % of cycles include a defrost. Between "
                              "0 and 5 °C with humidity that is normal and does not count as a cycling fault."})
        limit = st.get("max_starts_hour")
        if limit and st.get("per_day") and st["per_day"] >= limit * 8:
            out.append({"code": "regelung_bremst", "severity": "warn",
                        "de": f"Die Starts liegen nahe an der eingestellten Grenze von {int(limit)} je Stunde. "
                              "Die Regelung bremst dann schon selbst – die Ursache liegt davor, nicht an dieser Grenze.",
                        "en": f"Starts run close to the configured limit of {int(limit)} per hour. The control is "
                              "already braking itself – the cause lies upstream, not at this limit."})
        year = st.get("starts_per_year")
        if year and year >= 6000:
            out.append({"code": "lebenskonto", "severity": "bad",
                        "de": f"Hochgerechnet {year} Starts im Jahr. Ab etwa 6000 zehrt das spürbar an der "
                              "Lebensdauer des Verdichters; unter 2000 gilt als optimal.",
                        "en": f"Projected {year} starts a year. From about 6000 on this measurably eats into "
                              "compressor life; below 2000 counts as optimal."})
        elif year and year >= 4000:
            out.append({"code": "lebenskonto", "severity": "warn",
                        "de": f"Hochgerechnet {year} Starts im Jahr. 4000 gilt als Grenze, unter 2000 als optimal.",
                        "en": f"Projected {year} starts a year. 4000 counts as the limit, below 2000 as optimal."})
        return out


class Counters:
    """Tageszähler, die die Wolf nicht führt: Takte, Laufzeiten, Abtauungen. Reset um Mitternacht.

    Nebenher läuft die Takt-Historie mit (self.cycles): dieselben Flanken, nur einzeln festgehalten
    statt aufsummiert.
    """

    FIELDS = ("takte", "laufzeit_min", "abtauungen", "abtau_min", "ww_min", "hz_min",
              "kuehl_min", "eheiz_min", "sperr_min", "standby_min")

    def __init__(self, path):
        self.path = path
        self.day = time.strftime("%Y-%m-%d")
        self.data = {f: 0.0 for f in self.FIELDS}
        self.prev = {}
        self.last_tick = None
        self.cycles = Cycles()
        self.load()

    def load(self):
        try:
            with open(self.path) as fh:
                saved = json.load(fh)
            if saved.get("day") == self.day:
                self.data.update({k: v for k, v in saved.get("data", {}).items() if k in self.FIELDS})
                LOG.info("Tageszähler übernommen: %s Takte, %.0f min Laufzeit", int(self.data["takte"]), self.data["laufzeit_min"])
            self.cycles.load(saved)          # die Historie gilt über den Tageswechsel hinaus
        except FileNotFoundError:
            pass
        except Exception as exc:
            LOG.warning("Zählerstand nicht lesbar (%s), fange bei null an", exc)

    def tick(self, hp):
        """Einen Zeitschritt verbuchen. hp sind die aktuellen Werte der Wärmepumpe.

        Rückgabe: der gerade beendete Takt, sonst None – die Bridge veröffentlicht ihn einzeln.
        """
        now = time.time()
        today = time.strftime("%Y-%m-%d")
        if today != self.day:
            self.day = today
            self.data = {f: 0.0 for f in self.FIELDS}
            LOG.info("Neuer Tag, Tageszähler zurückgesetzt")
        minutes = 0.0 if self.last_tick is None else min((now - self.last_tick) / 60.0, 5.0)
        self.last_tick = now
        if not hp:
            return None

        verdichter = hp.get("verdichter")
        status = hp.get("verdichterstatus")
        modus = hp.get("betriebsart_heizgeraet")
        eheiz = hp.get("e_heizung")

        done = None
        if verdichter == 1 and self.prev.get("verdichter") == 0:
            self.data["takte"] += 1
            self.cycles.begin(now, hp)
        elif verdichter == 0 and self.prev.get("verdichter") == 1:
            done = self.cycles.end(now)
        if verdichter == 1:
            self.cycles.sample(hp, minutes)
        if status == ST_ABTAU and self.prev.get("verdichterstatus") != ST_ABTAU:
            self.data["abtauungen"] += 1

        if verdichter == 1:
            self.data["laufzeit_min"] += minutes
        if status == ST_ABTAU:
            self.data["abtau_min"] += minutes
        if status in ST_SPERRE:
            self.data["sperr_min"] += minutes
        if eheiz == 1:
            self.data["eheiz_min"] += minutes
        if modus in MODE_WW:
            self.data["ww_min"] += minutes
        elif modus in MODE_HZ:
            self.data["hz_min"] += minutes
        elif modus in MODE_KUEHL:
            self.data["kuehl_min"] += minutes
        elif modus == 15:
            self.data["standby_min"] += minutes

        self.prev = {"verdichter": verdichter, "verdichterstatus": status}
        return done


class Bridge:
    def __init__(self):
        with open(CATALOG) as fh:
            self.catalog = json.load(fh)
        self.devices = []
        used = {}
        for entry in self.catalog["devices"]:
            role = entry["role"]
            used[role] = used.get(role, 0) + 1
            ident = role if used[role] == 1 else f"{role}{used[role]}"
            self.devices.append(Device(entry, ident))
        self.by_ident = {d.ident: d for d in self.devices}
        self.ip = None
        self.counters = Counters(STATE_FILE)
        self.cycling = {}                        # letzte Auswertung der Takt-Historie, siehe Cycles.stats
        self.restore()
        self.lock = threading.Lock()
        self.client = mqtt.Client(client_id=f"wolf-bridge-{os.getpid()}")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        LOG.info("Katalog: %d Geräte, %d Parameter",
                 len(self.devices), sum(len(d.params) for d in self.devices))

    def restore(self):
        """Den letzten bekannten Zustand aus /state/wolf.json holen."""
        try:
            with open(STATE_FILE) as fh:
                saved = json.load(fh)
        except FileNotFoundError:
            return
        except Exception as exc:
            LOG.warning("Zustand nicht lesbar (%s), fange leer an", exc)
            return
        self.ip = saved.get("ip") or self.ip
        total = 0
        for d in self.devices:
            entry = saved.get("devices", {}).get(d.ident)
            if not entry:
                continue
            d.topic = entry.get("topic")
            total += d.restore(entry)
        if total:
            LOG.info("Zustand übernommen: %d Werte aus %s", total, STATE_FILE)

    def save_state(self):
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            payload = {"day": self.counters.day, "data": self.counters.data, "ip": self.ip,
                       "cycles": self.counters.cycles.items,
                       "devices": {d.ident: {"topic": d.topic, "values": d.values, "texts": d.texts}
                                   for d in self.devices}}
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, STATE_FILE)
        except Exception as exc:
            LOG.warning("Zustand nicht schreibbar: %s", exc)

    # ---------------------------------------------------------------- MQTT
    def on_connect(self, client, userdata, flags, rc):
        LOG.info("MQTT verbunden (rc=%s)", rc)
        client.subscribe("Wolf/+/+")
        client.subscribe(f"{BASE}/grolo/wolf/set")
        client.subscribe(f"{BASE}/grolo/wolf/unlock")
        self.publish_catalog()

    def on_message(self, client, userdata, msg):
        try:
            if msg.topic.startswith("Wolf/"):
                self.on_wolf(msg)
            elif msg.topic.endswith("/wolf/set"):
                self.on_set(msg)
            elif msg.topic.endswith("/wolf/unlock"):
                self.on_unlock(msg)
        except Exception:
            LOG.exception("Nachricht auf %s nicht verarbeitet", msg.topic)

    def on_wolf(self, msg):
        parts = msg.topic.split("/")
        if len(parts) != 3 or parts[2].endswith("set"):
            return
        payload = json.loads(msg.payload.decode())
        if not isinstance(payload, dict):
            return
        with self.lock:
            self.ip = parts[1]
            device = self.bind(msg.topic, payload)
            if device is None:
                LOG.warning("Kein Katalogeintrag für %s", msg.topic)
                return
            device.apply(payload)

    def bind(self, topic, payload):
        """Wolf-Topic einem Katalogeintrag zuordnen: gleiche Busadresse, meiste passende Parameternamen."""
        for d in self.devices:
            if d.topic == topic:
                return d
        bus = topic.rsplit("_", 1)[-1].lower()
        names = list(payload)
        best, score = None, 0
        for d in self.devices:
            if d.topic is not None or int(d.bus, 16) != int(bus, 16):
                continue
            s = d.match(names)
            if s > score:
                best, score = d, s
        if best is None or score == 0:
            return None
        best.topic = topic
        LOG.info("%s -> %s (%s, %d passende Namen)", topic, best.ident, best.label_de, score)
        self.publish_catalog()
        return best

    # ---------------------------------------------------------------- schreiben
    def on_unlock(self, msg):
        """Fachmann-Code vorab prüfen, damit die Bedienseite den Bereich erst nach dem Code aufklappt."""
        try:
            pin = str(json.loads(msg.payload.decode()).get("pin", ""))
        except Exception:
            pin = ""
        ok = not EXPERT_PIN or pin == EXPERT_PIN
        if not ok:
            LOG.warning("Fachmann-Code abgelehnt")
        self.client.publish(f"{BASE}/grolo/wolf/unlock/result",
                            json.dumps({"ok": ok, "required": bool(EXPERT_PIN)}))

    def on_set(self, msg):
        req = json.loads(msg.payload.decode())
        ident, key = req.get("device"), req.get("key")
        result = {"device": ident, "key": key, "ok": False}
        device = self.by_ident.get(ident)
        if device is None:
            result["error"] = f"unbekanntes Gerät {ident}"
        elif device.topic is None:
            result["error"] = f"{ident} noch nicht auf dem Bus gesehen"
        else:
            entry = device.by_key.get(key)
            if entry is None:
                result["error"] = f"unbekannter Parameter {key}"
            elif not entry["rw"]:
                result["error"] = f"{entry['de']} ist nur lesbar"
            elif entry["menu"] == "expert" and EXPERT_PIN and str(req.get("pin", "")) != EXPERT_PIN:
                result["error"] = f"{entry['de']} gehört zur Fachmann-Ebene, der Code fehlt oder stimmt nicht"
            else:
                value, error = self.resolve(entry, req)
                if error:
                    result["error"] = error
                else:
                    node = {"value": value} if entry.get("options") else value
                    if entry["dup"]:
                        node = {str(entry["ptid"]): node}
                    topic = f"{device.topic}/set"
                    self.client.publish(topic, json.dumps({entry["de"]: node}, ensure_ascii=False))
                    LOG.info("schreibe %s.%s = %s (%s)", ident, key, value, entry["de"])
                    result.update(ok=True, value=value, wrote={entry["de"]: node}, topic=topic)
        if not result["ok"]:
            LOG.warning("Schreibauftrag abgelehnt: %s", result.get("error"))
        self.client.publish(f"{BASE}/grolo/wolf/set/result", json.dumps(result, ensure_ascii=False))

    @staticmethod
    def resolve(entry, req):
        """Wunschwert gegen den Katalog prüfen und in die Form bringen, die ism7mqtt erwartet."""
        if "text" in req and entry.get("options"):
            want = str(req["text"]).strip().lower()
            for o in entry["options"]:
                if want in (o["de"].lower(), o["en"].lower()):
                    return o["v"], None
            return None, f"'{req['text']}' ist keine Auswahl von {entry['de']}"
        if "value" not in req:
            return None, "weder value noch text angegeben"
        value = req["value"]
        if entry.get("options"):
            allowed = {o["v"] for o in entry["options"]}
            if str(value) not in allowed:
                return None, f"{value} ist keine Auswahl von {entry['de']}"
            return str(value), None
        if entry["type"] in ("time", "date", "text"):
            return str(value), None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None, f"{value} ist keine Zahl"
        lo, hi = entry.get("min"), entry.get("max")
        if lo is not None and number < lo:
            return None, f"{number} liegt unter dem Minimum {lo}"
        if hi is not None and number > hi:
            return None, f"{number} liegt über dem Maximum {hi}"
        return (int(number) if float(number).is_integer() else number), None

    # ---------------------------------------------------------------- veröffentlichen
    def publish_catalog(self):
        payload = {
            "generated": self.catalog.get("generated"), "ip": self.ip, "expert_pin": bool(EXPERT_PIN),
            "devices": [{"id": d.ident, "role": d.role, "dtid": d.dtid, "bus": d.bus,
                         "de": d.label_de, "en": d.label_en, "online": d.topic is not None,
                         "params": d.params} for d in self.devices],
        }
        self.client.publish(f"{BASE}/grolo/wolf/catalog", json.dumps(payload, ensure_ascii=False), retain=True)

    def derived(self, hp):
        """Kennzahlen, die die Wolf selbst nicht ausgibt."""
        v = hp.values
        out = {}
        get = v.get
        p_th, p_el = get("aktuelle_sekundaerleistung"), get("leistungsaufnahme_wp_ehz")
        if p_th is not None:
            out["waerme_kw"] = p_th
        if p_el is not None:
            out["strom_kw"] = p_el
            out["strom_w"] = round(p_el * 1000)
        if p_th and p_el and p_el > 0.2 and get("verdichter") == 1:
            out["cop"] = round(p_th / p_el, 2)
        kessel, ruecklauf = get("kesseltemperatur"), get("ruecklauftemperatur")
        if kessel is not None and ruecklauf is not None:
            out["spreizung"] = round(kessel - ruecklauf, 2)
        if get("soll_spreizung") is not None:
            out["spreizung_soll"] = get("soll_spreizung")
        for key in ("verdichter", "verdichterfrequenz", "aktuelle_leistungsvorgabe_verdichter",
                    "verdichterstarts", "betriebsstunden_verdichter", "betriebsstunden_e_heizung",
                    "jaz_aktuelles_jahr", "taz_vortag", "aussentemperatur", "warmwassertemperatur",
                    "heizkreisdurchfluss", "anlagendruck"):
            if get(key) is not None:
                out[key] = get(key)

        c = self.counters.data
        out["takte_heute"] = int(c["takte"])
        for field in ("laufzeit_min", "abtau_min", "ww_min", "hz_min", "kuehl_min", "eheiz_min", "sperr_min"):
            out[f"{field}_heute"] = round(c[field], 1)
        out["abtauungen_heute"] = int(c["abtauungen"])
        if c["takte"] >= 1:
            out["laufzeit_je_takt_min"] = round(c["laufzeit_min"] / c["takte"], 1)

        # Taktung über das Auswertefenster: die Zahlen für die Grafana-Kacheln, der Befund als Klartext
        st = self.cycling
        for src, dst in (("median_min", "takt_median_min"), ("p25_min", "takt_p25_min"),
                         ("median_pause_min", "takt_pause_min"), ("per_day", "takte_je_tag"),
                         ("short_per_day", "kurztakte_je_tag"), ("starts_per_year", "starts_jahr"),
                         ("cycles", "takte_fenster"), ("span_days", "takt_tage"),
                         ("lifetime_per_cycle_min", "takt_mittel_gesamt_min")):
            if st.get(src) is not None:
                out[dst] = st[src]
        if st.get("short_share") is not None:
            out["kurztakt_anteil"] = round(st["short_share"] * 100, 1)
        if st.get("runtime_share") is not None:
            out["laufzeitanteil"] = round(st["runtime_share"] * 100, 1)
        verdict = st.get("verdict") or {}
        if verdict:
            out["befund"] = verdict["code"]
            out["befund_de"] = verdict["de"]
            out["befund_en"] = verdict["en"]
            out["befund_ampel"] = {"ok": 0, "info": 1, "warn": 2, "bad": 3}.get(verdict["severity"], 1)

        # Arbeitszahlen aus den Wolf-Statistikregistern: erzeugte Wärme je verbrauchtem Strom
        for span, warm, strom in (("tag", "erzeugte_waermemenge_aktueller_tag", "verbrauch_aktueller_tag"),
                                  ("monat", "erzeugte_waermemenge_aktueller_monat", "verbrauch_aktueller_monat"),
                                  ("jahr", "erzeugte_waermemenge_aktuelles_jahr", "verbrauch_aktuelles_jahr")):
            w, s = get(warm), get(strom)
            if w is not None:
                out[f"waerme_{span}"] = w
            if s is not None:
                out[f"strom_{span}"] = s
            if w and s and s > 0:
                out[f"az_{span}"] = round(w / s, 2)
        return out

    def publish(self):
        with self.lock:
            hp = next((d for d in self.devices if d.role == "heatpump" and d.topic), None)
            done = self.counters.tick(hp.values if hp else None)
            if done:
                # Ein beendeter Takt geht einzeln raus (nicht retained: jede Nachricht ist ein Ereignis,
                # Telegraf macht daraus eine Zeile im Measurement wolf_cycle).
                event = {k: v for k, v in done.items() if v is not None}   # Telegraf stolpert über null
                self.client.publish(f"{BASE}/grolo/wolf/cycle", json.dumps(event, ensure_ascii=False))
                LOG.info("Takt beendet: %.1f min %s, Pause davor %s min, %s °C",
                         done["min"], done["mode"], done.get("pause_min"), done.get("t_out"))
            if hp:
                self.cycling = self.counters.cycles.stats(hp.values, self.counters.data)
                self.client.publish(f"{BASE}/grolo/wolf/cycling",
                                    json.dumps(self.cycling, ensure_ascii=False), retain=True)
            for d in self.devices:
                if d.topic is None or not d.values:
                    continue
                self.client.publish(f"{BASE}/grolo/wolf/{d.ident}/state",
                                    json.dumps(d.state_payload(), ensure_ascii=False), retain=True)
            if hp:
                self.client.publish(f"{BASE}/grolo/wolf/derived",
                                    json.dumps(self.derived(hp), ensure_ascii=False), retain=True)
            status = {
                "ip": self.ip, "ts": int(time.time()),
                "devices": [{"id": d.ident, "de": d.label_de, "en": d.label_en, "bus": d.bus,
                             "topic": d.topic, "values": len(d.values),
                             "age": round(time.time() - d.seen) if d.seen else None} for d in self.devices],
            }
            self.client.publish(f"{BASE}/grolo/wolf/status", json.dumps(status, ensure_ascii=False), retain=True)
        self.save_state()

    def run(self):
        self.client.connect(HOST, PORT, 60)
        self.client.loop_start()
        while True:
            time.sleep(INTERVAL)
            try:
                self.publish()
            except Exception:
                LOG.exception("Veröffentlichen fehlgeschlagen")


if __name__ == "__main__":
    Bridge().run()
