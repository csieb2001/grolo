#!/usr/bin/env python3
"""insights: Erkenntnisse aus der eigenen Messhistorie – was die Anlage über sich selbst verrät.

Keine Prognose und keine Kennzahl, die schon woanders steht, sondern die Fragen, die man einer Anlage
erst stellen kann, wenn sie eine Weile gemessen hat:

  1 Verschattungskarte  Leistung je String über Azimut und Höhe der Sonne. Je Feld das Maximum, damit
                        Wolken herausfallen; je String getrennt, damit sichtbar wird, wer als Einziger
                        einbricht. Und die Summe über die Sonnenhöhe, weil eine Verschattung, die alle
                        vier gleich trifft, im Quervergleich unsichtbar bleibt.
  2 Vorlauf             wärmemengengewichtete Vorlauftemperatur und Hub über die Außenluft, gegen die
                        Bänder von heatpumpmonitor.org (626 reale Anlagen).
  3 Taktung             Starts und Laufzeit über die Außentemperatur, unverändert aus der Auswertung
                        der wolf-bridge – doppelt zu rechnen hieße, zwei Wahrheiten zu veröffentlichen.
  4 Heizkurve           Räume dauerhaft über Soll *und* hoher Vorlauf. Nur beides zusammen ist ein Befund.
  5 Warmwasser          was dieselbe Ladung mittags gekostet hätte, in Cent.
  6 Zellspannung        Spreizung der Zellen, nur bei vergleichbarem Ladestand und vergleichbarer Temperatur.
  7 Standby/Umlauf      was der Speicher nachts allein verbraucht, und was von der geladenen Energie
                        wieder herauskommt.
  8 Was wäre wenn       zwei Vorschläge, keine Zustände: was ein Kürzen des nahen Baums brächte und was
                        es brächte, die Module höher an den First zu setzen. Beides über das Jahresmodell
                        aus advice.py, beides mit der vermessenen Horizontmaske als Ausgangspunkt, beides
                        im Konjunktiv und mit basis "modelled" – damit niemand es für eine Messung hält.

Zwei Regeln stehen über allem:

  * Die Arbeitszahl der Wolf kommt aus deren internem Zähler – Bilanzgrenze H1/H2. Die Vergleichswerte von
    heatpumpmonitor.org schließen Heizkreispumpen und Heizstab ein (H4) und liegen deshalb systematisch
    niedriger. Jede Arbeitszahl trägt diesen Vermerk im Text, sonst ist der Vergleich unehrlich. Sie heißt
    "gleitende 30-Tage-Arbeitszahl", nie JAZ: eine Jahreszahl ist es erst nach einem Jahr.
  * Lieber eine Erkenntnis weglassen als eine dünne behaupten. Wo die Daten nicht reichen, steht
    basis: "thin" und ein Satz, ab wann es belastbar wird.

Ausdrücklich nicht berechnet: Degradationsrate, Jahresvergleich, JAZ, Restlebensdauer aus Zyklen,
Ertragsprognosen. Die Historie beginnt am 06.09.2026 – dafür ist sie zu kurz, und die Zahlen sähen
belastbar aus, wären es aber nicht.

Veröffentlicht (retained):
  <BASE>/grolo/insights   {"updated": unix, "items": [...], "shading": {...}}

Umgebung: MQTT_HOST/PORT, HA_BASE_TOPIC, DATABASE_URL (oder PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE),
          INFLUX_URL/TOKEN/ORG/BUCKET, INSIGHTS_INTERVAL (s, Standard 600), TZ
"""
import base64, hashlib, hmac, json, logging, math, os, socket, struct, sys, threading, time
import urllib.parse, urllib.request
import paho.mqtt.client as mqtt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from solar import sun_position, horizon_profile  # noqa: E402
import advice  # noqa: E402  – Jahresmodell und Horizontrechnung, damit es sie nur einmal gibt
import mqtt_watchdog  # noqa: E402

BASE = os.getenv("HA_BASE_TOPIC", "homeassistant")
HOST = os.getenv("MQTT_HOST", "mosquitto"); PORT = int(os.getenv("MQTT_PORT", "1883"))
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", ""); INFLUX_ORG = os.getenv("INFLUX_ORG", "growatt")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "nexa")
INTERVAL = int(os.getenv("INSIGHTS_INTERVAL", "600"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("insights")

# ---------------------------------------------------------------------------- Schwellen, alle benannt
D = {
    "sun_min_el": 3.0,        # darunter ist jede Stringleistung Rauschen, nicht Sonne
    "az_step": 5.0, "el_step": 2.0,          # Rasterweite der Verschattungskarte
    "az_step_coarse": 10.0, "el_step_coarse": 4.0,   # gröber, solange die Datenlage dünn ist
    "min_cells": 120,         # so viele belegte Felder braucht das feine Raster, sonst das grobe
    "min_hits": 2,            # so viele Messpunkte muss ein Feld haben, damit es zählt
    "min_hits_dip": 5,        # für den Vergleich über die Sonnenhöhe mehr, der reagiert empfindlicher
    "deficit": 0.75,          # darunter gilt ein String gegenüber den anderen als verschattet
    "recovered": 0.90,        # ab hier hat er sich erholt
    "deficit_span": 15.0,     # so breit muss der Azimutbereich mindestens sein (Grad)
    "sum_drop": 0.80,         # Summe bei höherem Stand unter diesem Anteil des tieferen -> alle betroffen
    "pack_wh": 2048.0,        # Typenschild eines NEXA-Akkumoduls
    "night_from": 1, "night_to": 5,          # lokale Stunden, in denen der Speicher nichts zu tun hat
    "night_idle_w": 3.0,      # darüber arbeitet der Ausgang und es ist kein Standby mehr
    "gap_s": 120.0,           # längere Lücke zwischen zwei Samples wird nicht als Messzeit gezählt
    "price_ct_kwh": 30.0,     # Rückfall, solange kein Tarif eingetragen ist
    "over_k": 0.5,            # so weit über Soll gilt ein Raum als zu warm
    "over_share": 0.70,       # so oft muss das zutreffen, damit es "dauerhaft" heißt
    "over_rooms": 2,          # so viele Räume, sonst ist es ein Raumproblem und keine Heizkurve
}
# Vergleichsbänder aus heatpumpmonitor.org: mittlere Vorlauftemperatur am kältesten Tag, 626 reale
# Anlagen. Bilanzgrenze H4 – Heizkreispumpen und Heizstab zählen dort mit.
HPM = {"good_spf": 4.0, "good_flow": 36.6, "mid_spf": 3.5, "mid_flow": 39.5, "band": 5.5,
       "bad_from": 45.0, "bad_to": 50.0, "fleet_spf": 3.86, "fleet_n": 626}
MODE_WW = {7, 8, 9}
MODE_HZ = {10, 11}


def de(v, n=1):
    """Zahl mit Komma statt Punkt – der deutsche Satz soll sich lesen wie ein deutscher Satz."""
    return f"{v:.{n}f}".replace(".", ",")


def day_de(iso):
    """Ein Datum im deutschen Satz gehört auch deutsch geschrieben."""
    y, m, d = iso.split("-")
    return f"{d}.{m}.{y}"


def median(xs):
    s = sorted(xs)
    if not s:
        return None
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


# ---------------------------------------------------------------------------- Postgres ohne Treiber
# Die Messhistorie liegt in der Datenbank der Website, und im Abbild der Sidecars steckt kein psycopg.
# Einen Treiber nachzuinstallieren hieße, ein eigenes Abbild zu bauen und alle Sidecars daran zu binden –
# für eine Handvoll lesender Abfragen ist das Protokoll selbst der kleinere Eingriff. Gebraucht wird
# daraus nur: Startup, SCRAM-SHA-256 und die einfache Abfrage. Kein Prepare, keine Transaktion, kein
# Binärformat; alles kommt als Text zurück und wird hier umgewandelt.
class Pg:
    def __init__(self, dsn=None):
        u = urllib.parse.urlsplit(dsn or os.getenv("DATABASE_URL", "") or "")
        self.host = u.hostname or os.getenv("PGHOST", "grolo-db")
        self.port = u.port or int(os.getenv("PGPORT", "5432"))
        self.user = urllib.parse.unquote(u.username or "") or os.getenv("PGUSER", "grolo")
        self.password = urllib.parse.unquote(u.password or "") or os.getenv("PGPASSWORD", "")
        self.db = (u.path or "").lstrip("/") or os.getenv("PGDATABASE", "grolo")
        self.sock = None
        self.f = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    # -- Rahmen: ein Byte Kennzeichen, vier Byte Länge (sich selbst eingeschlossen), dann der Inhalt
    def _send(self, tag, body):
        self.sock.sendall((tag or b"") + struct.pack("!I", len(body) + 4) + body)

    def _recv(self):
        tag = self.f.read(1)
        if not tag:
            raise ConnectionError("Postgres hat die Verbindung geschlossen")
        (size,) = struct.unpack("!I", self.f.read(4))
        return tag, self.f.read(size - 4)

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=60)
        self.f = self.sock.makefile("rb")
        params = b"".join(k.encode() + b"\0" + v.encode() + b"\0" for k, v in (
            ("user", self.user), ("database", self.db), ("application_name", "grolo-insights"),
            ("client_encoding", "UTF8")))
        self._send(None, struct.pack("!I", 196608) + params + b"\0")   # Protokoll 3.0
        self._auth()
        while True:
            tag, body = self._recv()
            if tag == b"Z":        # ReadyForQuery
                return
            if tag == b"E":
                raise ConnectionError(self._error(body))

    def _auth(self):
        """Nur SCRAM-SHA-256 – postgres:18 verlangt es für alles, was nicht über den Unix-Socket kommt."""
        tag, body = self._recv()
        if tag == b"E":
            raise ConnectionError(self._error(body))
        (kind,) = struct.unpack("!I", body[:4])
        if kind == 0:
            return                                   # Vertrauensstellung, nichts zu tun
        if kind != 10:
            raise ConnectionError(f"Postgres verlangt Authentifizierung {kind}, unterstützt ist nur SCRAM")
        nonce = base64.b64encode(os.urandom(18)).decode()
        first_bare = f"n=,r={nonce}"
        self._send(b"p", b"SCRAM-SHA-256\0" + struct.pack("!i", len(first_bare) + 3) + b"n,," + first_bare.encode())
        tag, body = self._recv()
        if tag == b"E":
            raise ConnectionError(self._error(body))
        server_first = body[4:].decode()
        d = dict(p.split("=", 1) for p in server_first.split(","))
        salted = hashlib.pbkdf2_hmac("sha256", self.password.encode(), base64.b64decode(d["s"]), int(d["i"]))
        client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
        stored = hashlib.sha256(client_key).digest()
        final_bare = f"c=biws,r={d['r']}"
        auth_msg = f"{first_bare},{server_first},{final_bare}".encode()
        sig = hmac.new(stored, auth_msg, hashlib.sha256).digest()
        proof = base64.b64encode(bytes(a ^ b for a, b in zip(client_key, sig))).decode()
        self._send(b"p", f"{final_bare},p={proof}".encode())
        tag, body = self._recv()
        if tag == b"E":
            raise ConnectionError(self._error(body))
        # SASLFinal: die Signatur des Servers beweist, dass auch er das Passwort kennt. Ungeprüft
        # ließe sich ein untergeschobener Server nicht von dem echten unterscheiden.
        server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
        want = base64.b64encode(hmac.new(server_key, auth_msg, hashlib.sha256).digest()).decode()
        got = dict(p.split("=", 1) for p in body[4:].decode().split(","))
        if got.get("v") != want:
            raise ConnectionError("Postgres hat sich nicht ausgewiesen (SCRAM-Signatur falsch)")

    @staticmethod
    def _error(body):
        parts = {p[:1]: p[1:] for p in body.decode("utf-8", "replace").split("\0") if p}
        return parts.get("M", "unbekannter Fehler")

    def query(self, sql):
        """Eine Abfrage, alle Zeilen als Liste von Listen; NULL wird None, alles andere bleibt Text."""
        self._send(b"Q", sql.encode() + b"\0")
        rows, err = [], None
        while True:
            tag, body = self._recv()
            if tag == b"D":                                   # DataRow
                (n,) = struct.unpack("!H", body[:2])
                pos, row = 2, []
                for _ in range(n):
                    (ln,) = struct.unpack("!i", body[pos:pos + 4]); pos += 4
                    if ln < 0:
                        row.append(None)
                    else:
                        row.append(body[pos:pos + ln].decode("utf-8", "replace")); pos += ln
                rows.append(row)
            elif tag == b"E":
                err = self._error(body)
            elif tag == b"Z":                                 # ReadyForQuery beendet jede Abfrage
                break
        if err:
            raise ConnectionError(err)
        return rows

    def close(self):
        try:
            if self.sock:
                self._send(b"X", b"")
        except Exception:
            pass
        for h in (self.f, self.sock):
            try:
                h and h.close()
            except Exception:
                pass
        self.sock = self.f = None


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------- InfluxDB
def influx_query(flux):
    if not INFLUX_TOKEN:
        return []
    req = urllib.request.Request(f"{INFLUX_URL}/api/v2/query?org={urllib.parse.quote(INFLUX_ORG)}",
                                 data=flux.encode(), method="POST",
                                 headers={"Authorization": f"Token {INFLUX_TOKEN}",
                                          "Content-Type": "application/vnd.flux", "Accept": "application/csv"})
    with urllib.request.urlopen(req, timeout=90) as r:
        text = r.read().decode()
    rows, header = [], None
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


# ---------------------------------------------------------------------------- 1 Verschattungskarte
def single_string_days(rows):
    """Tage, an denen überhaupt nur ein String angeschlossen war.

    Vom 06. bis 12.09.2026 hing nur String 1 am Gerät. Die Punkte sehen aus wie eine vollständige
    Messung – vier Spannungen, vier Ströme – und sagen doch nur, dass drei Eingänge leer waren. Im
    Quervergleich wäre das eine Totalverschattung von S2 bis S4 über den halben Datensatz. Erkannt
    wird der Zustand nicht am Datum, sondern an sich selbst: einer liefert, die drei anderen zusammen
    nicht einmal 9 W. Ein fester Zeitraum wäre eine Zahl im Code, die niemand mehr nachprüft; die
    Regel prüft sich bei jedem Lauf selbst und greift auch beim nächsten Umbau am Gerät.
    """
    days = {}
    for ts, p in rows:
        if sum(p) < 20.0:
            continue                                   # Nacht und Dämmerung sagen dazu nichts
        d = time.strftime("%Y-%m-%d", time.gmtime(ts))
        s = days.setdefault(d, [0, 0])
        s[0] += 1
        if max(p) > 10.0 and sum(p) - max(p) < 9.0:
            s[1] += 1
    return {d for d, (n, solo) in days.items() if n >= 20 and solo / n >= 0.5}


def shading_map(rows, lat, lon, oriented):
    """Leistung je String über den Sonnenstand, gerastert; je Feld das Maximum.

    Das Maximum und nicht der Mittelwert: über zwei Wochen fällt auf jedes Feld irgendwann ein klarer
    Moment, und der ist die Obergrenze dessen, was dort möglich war. Wolken können nur nach unten
    ziehen, also verschwinden sie im Maximum von selbst. Bleibt ein Feld trotzdem niedrig, stand
    etwas davor – oder die Module zeigen dorthin nicht hin, und genau diese beiden Fälle kann die
    Karte allein nicht trennen.
    """
    excluded = single_string_days(rows)
    pts = []
    for ts, p in rows:
        if time.strftime("%Y-%m-%d", time.gmtime(ts)) in excluded:
            continue
        az, el = sun_position(ts, lat, lon)
        if el < D["sun_min_el"] or sum(p) < 5.0:
            continue
        pts.append((az, el, p))
    if not pts:
        return None

    def grid(az_step, el_step):
        cells = {}
        for az, el, p in pts:
            key = (int(az // az_step), int(el // el_step))
            c = cells.setdefault(key, {"n": 0, "p": [0.0] * 4})
            c["n"] += 1
            for i in range(4):
                if p[i] > c["p"][i]:
                    c["p"][i] = p[i]
        return {k: v for k, v in cells.items() if v["n"] >= D["min_hits"]}

    fine = grid(D["az_step"], D["el_step"])
    if len(fine) >= D["min_cells"]:
        cells, az_step, el_step, coarse = fine, D["az_step"], D["el_step"], False
    else:
        cells, az_step, el_step, coarse = grid(D["az_step_coarse"], D["el_step_coarse"]), \
            D["az_step_coarse"], D["el_step_coarse"], True

    # Je Azimut das Beste über alle Sonnenhöhen: so vergleichen sich die Strings untereinander, ohne
    # dass der Sonnenstand selbst den Unterschied macht.
    by_az = {}
    for (ai, ei), c in cells.items():
        b = by_az.setdefault(ai, [0.0] * 4)
        for i in range(4):
            if c["p"][i] > b[i]:
                b[i] = c["p"][i]
    peak = [max((b[i] for b in by_az.values()), default=0.0) for i in range(4)]
    azimuths = []
    for ai in sorted(by_az):
        b = by_az[ai]
        rel = [round(b[i] / peak[i], 3) if peak[i] > 20 else None for i in range(4)]
        azimuths.append({"az": round(ai * az_step + az_step / 2.0, 1), "w": [round(x) for x in b], "rel": rel})

    days = sorted({time.strftime("%Y-%m-%d", time.gmtime(ts)) for ts, _ in rows}) or [""]
    return {
        "az_step": az_step, "el_step": el_step, "coarse": coarse, "oriented": oriented,
        "points": len(pts), "cells": len(cells), "excluded_days": sorted(excluded),
        "from": days[0], "to": days[-1],
        "peak_w": [round(x) for x in peak],
        "azimuths": azimuths,
        "grid": [{"az": round(ai * az_step + az_step / 2.0, 1), "el": round(ei * el_step + el_step / 2.0, 1),
                  "n": c["n"], "w": [round(x) for x in c["p"]]}
                 for (ai, ei), c in sorted(cells.items())],
    }


def single_string_dips(sh):
    """Wo fällt ein String unter das, was die anderen im selben Moment noch können?

    Verglichen wird nicht in Watt, sondern im Anteil an der eigenen Bestleistung: die Strings haben
    unterschiedlich viele Module, und ein kleiner String ist nicht verschattet, nur weil er kleiner ist.
    """
    out = []
    for s in range(4):
        ratios = []
        for a in sh["azimuths"]:
            mine = a["rel"][s]
            others = [a["rel"][t] for t in range(4) if t != s and a["rel"][t] is not None]
            ref = sum(others) / len(others) if (mine is not None and len(others) >= 2) else None
            # Nur dort vergleichen, wo die anderen drei überhaupt nennenswert liefern: in der Dämmerung
            # sind alle vier nahe null, und ein Bruch zweier kleiner Zahlen ist keine Aussage.
            ratios.append(mine / ref if (ref and ref > 0.15) else None)
        runs, run = [], None
        for i, a in enumerate(sh["azimuths"]):
            if ratios[i] is not None and ratios[i] < D["deficit"]:
                run = run or {"i0": i, "ratios": []}
                run["i1"] = i; run["ratios"].append(ratios[i])
            elif run is not None:
                runs.append(run); run = None
        if run is not None:
            runs.append(run)
        for r in runs:
            a0, a1 = sh["azimuths"][r["i0"]], sh["azimuths"][r["i1"]]
            span = a1["az"] - a0["az"] + sh["az_step"]
            if span < D["deficit_span"]:
                continue
            # "Ab wo erholt er sich" heißt: das erste Feld danach, in dem er wieder mithält – nicht das
            # erste, in dem er nicht mehr unter der Schwelle liegt. Dazwischen liegt der Rand des Schattens.
            rec = next((sh["azimuths"][j]["az"] for j in range(r["i1"] + 1, len(ratios))
                        if ratios[j] is not None and ratios[j] >= D["recovered"]), None)
            mean = sum(r["ratios"]) / len(r["ratios"])
            out.append({"string": s + 1, "az_from": round(a0["az"] - sh["az_step"] / 2.0, 1),
                        "az_to": round(a1["az"] + sh["az_step"] / 2.0, 1), "span_deg": round(span, 1),
                        "worst_pct": round(min(r["ratios"]) * 100), "mean_pct": round(mean * 100),
                        "recovered_at": rec, "cost": round(span * (1.0 - mean), 1)})
    # Sortiert nach dem, was es kostet: ein tiefer Einbruch über fünf Grad wiegt weniger als ein
    # flacherer über vierzig. Die Tiefe allein würde den größeren Schatten hinter den kleineren stellen.
    out.sort(key=lambda r: -r["cost"])
    return out


def high_sun_sector(sh):
    """Wie viel alle vier Strings zusammen liefern, wenn die Sonne am höchsten steht.

    Die auffälligste Zahl der ganzen Karte und zugleich die, die am leichtesten zu einer falschen
    Aussage verführt: stehen alle vier beim höchsten Sonnenstand weit unter ihrer Bestleistung, sieht
    das nach einer Verschattung im Süden aus. Es sieht aber genauso aus, wenn die Module nach Westen
    zeigen – dann ist der Mittag einfach nicht ihre Zeit. Ohne eingetragene Ausrichtung bleibt es
    deshalb bei der Feststellung, und die Deutung wird ausdrücklich offengelassen.
    """
    cols = {}
    for g in sh["grid"]:
        c = cols.setdefault(g["az"], {"el": 0.0, "n": 0})
        if g["n"] >= D["min_hits_dip"]:
            c["el"] = max(c["el"], g["el"]); c["n"] += g["n"]
    el_top = max((c["el"] for c in cols.values()), default=0.0)
    if el_top < 10.0:
        return None
    rel = {a["az"]: [x for x in a["rel"] if x is not None] for a in sh["azimuths"]}
    high = [az for az, c in cols.items() if c["el"] >= 0.8 * el_top and len(rel.get(az, [])) >= 3]
    if len(high) < 3:
        return None
    best_az = max(rel, key=lambda az: sum(rel[az]) / len(rel[az]) if rel[az] else 0.0)
    best = sum(rel[best_az]) / len(rel[best_az])
    mean_high = sum(sum(rel[az]) / len(rel[az]) for az in high) / len(high)
    if best <= 0 or mean_high / best > 0.6:
        return None
    return {"az_from": round(min(high) - sh["az_step"] / 2.0, 1), "az_to": round(max(high) + sh["az_step"] / 2.0, 1),
            "el_top": round(el_top, 1), "pct_of_best": round(mean_high / best * 100),
            "best_az": round(best_az, 1), "columns": len(high)}


def common_dip(sh):
    """Verschattung, die alle vier gleich trifft: die Summe bricht ein, obwohl die Sonne höher steht.

    Der Quervergleich der Strings sieht so etwas nicht – wenn alle gleich wenig liefern, liegt keiner
    unter dem Durchschnitt der anderen. Sichtbar wird es nur im Verlauf über die Sonnenhöhe: normalerweise
    steigt die Summe mit der Sonne, nie umgekehrt.

    Verglichen wird ausschließlich innerhalb desselben Azimutfeldes. Über den ganzen Vor- oder Nachmittag
    hinweg zu vergleichen wäre bequemer und wäre falsch: die hohen Sonnenstände gibt es nur um die Mittags-
    zeit und nur an wenigen Tagen, die tiefen über den ganzen Tag und an allen. Das Maximum der tiefen
    Stände hat damit ungleich mehr Gelegenheiten, einen klaren Moment zu erwischen – und ein Einbruch
    wäre nur gezählte Gelegenheit, keine Verschattung. Innerhalb eines Azimutfeldes kommt der Unterschied
    in der Sonnenhöhe dagegen vom Lauf der Jahreszeit, und beide Stände hatten dieselben Tage zur Wahl.
    """
    per_az = {}
    for g in sh["grid"]:
        per_az.setdefault(g["az"], []).append((g["el"], sum(g["w"]), g["n"]))
    hits = {}
    for az, raw in per_az.items():
        # Das oberste und das unterste Feld einer Azimutspalte erwischt die Sonne nur an wenigen Tagen
        # am Rand des Messzeitraums. Ihr Maximum hatte entsprechend wenige Gelegenheiten, einen klaren
        # Moment zu treffen, und fällt allein deshalb niedrig aus. Genau diese Randfelder sähen aus wie
        # ein Einbruch bei hoher Sonne. Deshalb zählt nur, was gegenüber dem bestbesetzten Feld derselben
        # Spalte nicht abfällt: gleiche Gelegenheiten, dann ist der Vergleich einer.
        n_top = max(n for _, _, n in raw)
        els = sorted((el, w) for el, w, n in raw if n >= max(D["min_hits_dip"], 0.4 * n_top))
        if len(els) < 3:
            continue
        best, best_el, worst = 0.0, None, None
        for el, w in els:
            if best > 20.0 and w < D["sum_drop"] * best and (worst is None or w < worst[1]):
                worst = (el, w, best_el, best)
            if w > best:
                best, best_el = w, el
        if worst:
            hits[az] = {"az": az, "el": worst[0], "w": round(worst[1]), "ref_el": worst[2],
                        "ref_w": round(worst[3]), "loss_pct": round((1.0 - worst[1] / worst[3]) * 100)}
    # Ein einzelnes Feld ist Rauschen. Erst ein zusammenhängender Azimutbereich ist ein Gegenstand,
    # der dort steht – Bäume und Dächer hören nicht nach fünf Grad wieder auf.
    runs, run = [], []
    for az in sorted(hits):
        if run and az - run[-1]["az"] > sh["az_step"] + 0.01:
            runs.append(run); run = []
        run.append(hits[az])
    if run:
        runs.append(run)
    out = []
    for r in runs:
        if len(r) < 3:
            continue
        # Der Bezugsstand ist je Feld ein anderer; gemittelt wird deshalb über dieselben Felder, damit
        # der höhere Stand auch im Satz der höhere bleibt.
        out.append({"az_from": round(r[0]["az"] - sh["az_step"] / 2.0, 1),
                    "az_to": round(r[-1]["az"] + sh["az_step"] / 2.0, 1),
                    "el": round(sum(x["el"] for x in r) / len(r), 1),
                    "ref_el": round(sum(x["ref_el"] for x in r) / len(r), 1),
                    "loss_pct": round(sum(x["loss_pct"] for x in r) / len(r)),
                    "cells": len(r), "fields": r})
    out.sort(key=lambda f: -f["loss_pct"] * f["cells"])
    return out


# ---------------------------------------------------------------------------- 2 Vorlauf über Außenluft
def flow_lift(heat):
    """Wärmemengengewichtet, nicht über die Zeit gemittelt.

    Eine Stunde Stillstand bei 20 °C Vorlauf und eine Stunde Volllast bei 45 °C sind im Zeitmittel
    32,5 °C – eine Zahl, die die Anlage nie gefahren hat. Gewichtet nach gelieferter Wärme kommt
    heraus, bei welcher Temperatur die Wärme tatsächlich ins Haus ging, und nur die kostet COP.
    """
    q = qf = qo = ql = 0.0
    n = 0
    t_min = None
    for ts, hp_w, heat_w, flow_c, outside_c, comp, mode in heat:
        if mode not in MODE_HZ or not comp or heat_w is None or heat_w < 200 or flow_c is None or outside_c is None:
            continue
        q += heat_w; qf += heat_w * flow_c; qo += heat_w * outside_c; ql += heat_w * (flow_c - outside_c)
        n += 1
        t_min = outside_c if t_min is None else min(t_min, outside_c)
    if q <= 0 or n < 200:
        return None
    return {"flow_c": round(qf / q, 1), "outside_c": round(qo / q, 1), "lift_k": round(ql / q, 1),
            "points": n, "coldest_c": round(t_min, 1)}


def rolling_spf(day_rows, hours_per_day, days=30):
    """Gleitende 30-Tage-Arbeitszahl aus den Tageszählern der Wolf: Wärme durch Strom.

    Genommen werden die Zähler des Geräts, nicht die eigene Integration über die Momentanleistungen.
    Die beiden Wege kommen nicht auf dasselbe – über heat_w und hp_w aufsummiert steht hier eine 5,6,
    die Zähler der Wolf sagen 3,2 – und wenn zwei Wege zu zwei Zahlen führen, ist die Zahl des Geräts
    die, für die es geradesteht. Die eigene wäre nur die bequemere.

    Gezählt werden nur volle Messtage: an einem Tag, von dem die Hälfte fehlt, steht der Tageszähler
    trotzdem voll da, und der halbe Tag würde den Rest mit nach oben ziehen.

    Bilanzgrenze H1/H2 – die Wolf zählt ihren Verdichter und ihre eigene Elektrik, nicht die
    Heizkreispumpen und nicht den Heizstab. Wer sie mit den Werten von heatpumpmonitor.org vergleicht,
    vergleicht mit H4 und liegt damit systematisch zu gut. Der Vermerk gehört in jeden Satz, der die
    Zahl nennt.
    """
    today = time.strftime("%Y-%m-%d")
    cut = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    q = e = 0.0
    used = []
    for day, heat_kwh, el_kwh in day_rows:
        if day >= today or day < cut or not heat_kwh or not el_kwh:
            continue
        if hours_per_day.get(day, 0.0) < 20.0:
            continue
        q += heat_kwh; e += el_kwh; used.append(day)
    if e <= 0 or q <= 0 or len(used) < 3:
        return {"days": len(used), "enough": False, "window_days": days}
    return {"spf": round(q / e, 2), "window_days": days, "days": len(used), "enough": True,
            "from": used[0], "to": used[-1], "heat_kwh": round(q, 1), "el_kwh": round(e, 1)}


# ---------------------------------------------------------------------------- 4 Heizkurve zu steil
def rooms_over_setpoint(rows, heat_hours):
    """Räume, die dauerhaft über ihrem Soll stehen – gezählt nur in Stunden, in denen geheizt wurde.

    Außerhalb der Heizzeiten sagt eine zu warme Wohnung nichts über die Heizkurve, sondern über die
    Sonne. Erst wenn die Wärmepumpe in derselben Stunde Wärme geliefert hat, ist ein Überschuss ein
    Hinweis darauf, dass die Vorlauftemperatur höher liegt als das Haus braucht.
    """
    per_room = {}
    for room, hour, temp, setp, n in rows:
        if hour not in heat_hours or temp is None or setp is None:
            continue
        r = per_room.setdefault(room, {"n": 0, "over": 0, "sum": 0.0, "setp": 0.0})
        r["n"] += 1; r["sum"] += temp - setp; r["setp"] += setp
        if temp - setp > D["over_k"]:
            r["over"] += 1
    out = []
    for room, r in per_room.items():
        if r["n"] < 24:
            continue
        out.append({"room": room, "hours": r["n"], "share": round(r["over"] / r["n"], 3),
                    "over_k": round(r["sum"] / r["n"], 2), "setpoint_c": round(r["setp"] / r["n"], 1)})
    out.sort(key=lambda r: -r["over_k"])
    return out


# ---------------------------------------------------------------------------- 5 Warmwasser zur falschen Zeit
def dhw_timing(cycles, noon_temp, price_ct):
    """Was dieselbe Warmwasserladung mittags gekostet hätte.

    Die Wärme ist dieselbe, nur der Hub ist ein anderer: mittags ist es draußen wärmer, der Verdichter
    muss weniger weit hochheben, und der COP steigt in erster Näherung mit dem Kehrwert des Hubs
    (Carnot). Gerechnet wird also nicht mit einem Modell-COP, sondern mit dem gemessenen COP genau
    dieser Ladung, nur auf die Mittagstemperatur umgerechnet.
    """
    items = []
    for c in cycles:
        if c["mode"] != "ww" or not c["kwh"] or not c["cop"] or c["t_out"] is None or c["flow_c"] is None:
            continue
        day = time.strftime("%Y-%m-%d", time.localtime(c["start"] or c["ts"]))
        t_mid = noon_temp.get(day)
        if t_mid is None:
            continue
        lift_now = max(c["flow_c"] - c["t_out"], 5.0)
        lift_noon = max(c["flow_c"] - t_mid, 5.0)
        el_now = c["kwh"] / c["cop"]
        el_noon = el_now * lift_noon / lift_now
        items.append({"start": c["start"] or c["ts"], "hour": int(time.strftime("%H", time.localtime(c["start"] or c["ts"]))),
                      "t_out": round(c["t_out"], 1), "t_noon": round(t_mid, 1), "flow_c": round(c["flow_c"], 1),
                      "kwh": round(c["kwh"], 2), "cop": round(c["cop"], 2),
                      "ct": round((el_now - el_noon) * price_ct, 1)})
    if not items:
        return None
    total = sum(i["ct"] for i in items)
    hours = sorted(i["hour"] for i in items)
    return {"charges": len(items), "ct_total": round(total, 1), "ct_per_charge": round(total / len(items), 1),
            "hour_median": median(hours), "hour_min": hours[0], "hour_max": hours[-1],
            "t_out_mean": round(sum(i["t_out"] for i in items) / len(items), 1),
            "t_noon_mean": round(sum(i["t_noon"] for i in items) / len(items), 1),
            "items": items[-20:]}


# ---------------------------------------------------------------------------- 6 Zellspannungsspreizung
def cell_spread():
    """Spreizung zwischen der höchsten und der niedrigsten Zelle, monatsweise.

    Nur vergleichbar bei ähnlichem Ladestand und ähnlicher Temperatur: die Spreizung wächst an beiden
    Enden der Kennlinie und bei Kälte von selbst, ganz ohne driftende Zellen. Ein Monatstrend, der
    einen vollen Sommer gegen einen halben Winter stellt, misst das Wetter und nennt es Alterung.
    """
    rows = influx_query(f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -400d)
  |> filter(fn: (r) => r._measurement == "nexa" and (r._field == "maxCellVoltage" or r._field == "minCellVoltage"
        or r._field == "totalBatteryPackSoc" or r._field == "battery1Temp"))
  |> aggregateWindow(every: 10m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.maxCellVoltage and exists r.minCellVoltage and exists r.totalBatteryPackSoc)
  |> keep(columns: ["_time", "maxCellVoltage", "minCellVoltage", "totalBatteryPackSoc", "battery1Temp"])''')
    pts = []
    for r in rows:
        mx, mn, soc = num(r.get("maxCellVoltage")), num(r.get("minCellVoltage")), num(r.get("totalBatteryPackSoc"))
        if mx is None or mn is None or soc is None or not r.get("_time"):
            continue
        pts.append((r["_time"][:7], soc, num(r.get("battery1Temp")), (mx - mn) * 1000.0))
    if len(pts) < 200:
        return None
    socs = sorted(p[1] for p in pts)
    temps = sorted(p[2] for p in pts if p[2] is not None)
    soc_mid = socs[len(socs) // 2]
    t_mid = temps[len(temps) // 2] if temps else None
    band = [p for p in pts if abs(p[1] - soc_mid) <= 10.0 and (t_mid is None or p[2] is None or abs(p[2] - t_mid) <= 3.0)]
    months = {}
    for m, _, _, mv in band:
        months.setdefault(m, []).append(mv)
    per_month = [{"month": m, "n": len(v), "mv": round(median(v), 1)} for m, v in sorted(months.items()) if len(v) >= 100]
    return {"soc_band": [round(soc_mid - 10), round(soc_mid + 10)],
            "temp_band": [round(t_mid - 3, 1), round(t_mid + 3, 1)] if t_mid is not None else None,
            "points": len(band), "months": per_month}


# ---------------------------------------------------------------------------- 7 Standby und Umlaufwirkungsgrad
def standby(samples, wh_per_pct):
    """Was der Speicher nachts allein zieht, gemessen am Ladestand statt an einer Leistungsangabe.

    Der Eigenverbrauch des Geräts steht in keinem Register: die gemeldete Batterieleistung ist aus
    PV minus Abgabe gerechnet und enthält ihn nicht. Sichtbar wird er nur daran, dass der Ladestand
    in einer Nacht sinkt, in der nichts abgegeben wird. Der Ladestand kommt in ganzen Prozent, ein
    Prozent sind rund 41 Wh – deshalb zählt nur, was sich über viele Nächte summiert, und einzelne
    Nächte sind wertlos.
    """
    nights, cur = [], None
    for ts, pv, out, bat, soc in samples:
        lt = time.localtime(ts)
        idle = (D["night_from"] <= lt.tm_hour <= D["night_to"] and out is not None and abs(out) < D["night_idle_w"]
                and (pv or 0.0) < 5.0 and soc is not None)
        if idle:
            if cur is None or ts - cur["last"] > 900:
                cur = {"t0": ts, "last": ts, "soc0": soc, "soc1": soc}
                nights.append(cur)
            else:
                cur["last"] = ts; cur["soc1"] = soc
        else:
            cur = None
    hours = drop = 0.0
    used = 0
    for n in nights:
        h = (n["last"] - n["t0"]) / 3600.0
        d = n["soc0"] - n["soc1"]
        if h < 2.0 or d < 0:
            continue
        hours += h; drop += d; used += 1
    if used < 3 or hours < 8 or drop <= 0:
        return {"nights": used, "hours": round(hours, 1), "enough": False}
    w = drop * wh_per_pct / hours
    return {"nights": used, "hours": round(hours, 1), "pct": round(drop, 1), "w": round(w, 1),
            "kwh_year": round(w * 8.76, 1), "enough": True,
            # Ein Prozentpunkt ist die kleinste Stufe, die der Ladestand überhaupt melden kann.
            "resolution_w": round(wh_per_pct / hours, 1)}


def round_trip(samples, wh_per_pct):
    """Was von der geladenen Energie wieder herauskommt.

    Geladen und entladen wird aus derselben gerechneten Batterieleistung (PV minus Abgabe), deshalb
    stecken Laderegler, Wechselrichter und Zellen gemeinsam darin – es ist der Umlauf des ganzen
    Geräts, nicht der der Zellen. Der Ladestand am Anfang und am Ende ist selten derselbe; was am
    Ende mehr oder weniger drinsteht, wird gegengerechnet, sonst misst man die Restladung mit.
    """
    ch = dis = 0.0
    prev_ts = None
    soc0 = soc1 = None
    for ts, pv, out, bat, soc in samples:
        if bat is not None and prev_ts is not None:
            dt = min(ts - prev_ts, D["gap_s"])
            if bat > 0:
                ch += bat * dt
            else:
                dis += -bat * dt
        prev_ts = ts
        if soc is not None:
            soc0 = soc if soc0 is None else soc0
            soc1 = soc
    ch /= 3600.0; dis /= 3600.0                        # Wh
    if ch < 2000 or dis <= 0 or soc0 is None:
        return {"enough": False, "charged_kwh": round(ch / 1000.0, 2)}
    stored = (soc1 - soc0) * wh_per_pct                # was am Ende mehr im Speicher liegt als am Anfang
    usable = ch - stored
    if usable <= 0:
        return {"enough": False, "charged_kwh": round(ch / 1000.0, 2)}
    return {"enough": True, "charged_kwh": round(ch / 1000.0, 2), "discharged_kwh": round(dis / 1000.0, 2),
            "stored_kwh": round(stored / 1000.0, 2), "pct": round(dis / usable * 100, 1),
            "soc_from": soc0, "soc_to": soc1}


# ---------------------------------------------------------------------------- 8 Was wäre wenn
# Zwei Maßnahmen, die sich rechnen lassen, sobald eine vermessene Horizontmaske vorliegt: den nahen Baum
# kürzen und die Module höher an den First setzen. Beides sind Vorschläge. Drei Dinge entscheiden darüber,
# ob so eine Zahl brauchbar ist oder gefährlich:
#
#   1. Was stehen bleibt, bleibt stehen. Hinter dem nahen Baum steht ein Gehölzgürtel, der den Horizont
#      auch dann noch auf ein paar Grad hält, wenn der nahe Baum fällt. Ohne diesen Restschatten
#      (horizon_residual in der Standortkonfiguration) wird hier gar nicht gerechnet: eine Rechnung, die
#      nach dem Kürzen freien Himmel annimmt, verspricht zu viel, und zu viel versprochen ist schlimmer
#      als nichts gesagt. Weil der Restschatten eine Spanne ist, kommt auch das Ergebnis als Spanne.
#   2. Der wirksame Abstand wird aus der Maske selbst zurückgerechnet (d = Höhe / tan Höhenwinkel), nicht
#      aus der Kronenmitte. Die Maske ist die Messung; alles andere wäre eine zweite Schätzung daneben.
#      Eine gekürzte Krone kann den Horizont nie über das Gemessene heben und nie unter den Restschatten
#      drücken – beide Grenzen stehen in trimmed_mask().
#   3. Es bleibt gerechnet. basis ist "modelled", der Text steht im Konjunktiv, und die Maske gilt für den
#      belaubten Zustand. Im Winter fiele der Gewinn kleiner aus, weil ein kahler Baum ohnehin mehr
#      durchlässt – um wieviel, ist an diesem Standort nicht gemessen und wird deshalb nicht beziffert.
def trimmed_mask(mask, tree, cut_m, floor_deg):
    """Die Horizontmaske, nachdem der nahe Baum um cut_m Meter gekürzt wurde."""
    h = float(tree["height_m"]); a0, a1 = float(tree["az_from"]), float(tree["az_to"])
    out = {}
    for k, v in mask.items():
        az, el = float(k), float(v)
        if not (a0 <= az <= a1) or el <= 0.0:
            out[k] = el
            continue
        d = h / math.tan(math.radians(el))           # wirksamer Abstand aus der Messung
        rest = max(h - cut_m, 0.0)
        new = math.degrees(math.atan2(rest, d)) if d > 0 else el
        out[k] = round(min(el, max(new, float(floor_deg))), 1)
    return out


def relief_model(site, pr=0.85):
    """Was Ausästen und Umsetzen über das Jahresmodell brächten. None, sobald eine Voraussetzung fehlt."""
    mask = site.get("horizon") if isinstance(site.get("horizon"), dict) else None
    tree = site.get("horizon_tree") if isinstance(site.get("horizon_tree"), dict) else None
    res = site.get("horizon_residual")
    lat, lon = site.get("lat"), site.get("lon")
    if not mask or lat is None or lon is None:
        return None
    strings = []
    for v in (site.get("strings") or {}).values():
        try:
            if v.get("tilt") is None or v.get("azimuth") is None or not v.get("wp"):
                continue
            strings.append({"tilt": float(v["tilt"]), "azimuth": float(v["azimuth"]), "wp": float(v["wp"])})
        except (AttributeError, TypeError, ValueError):
            continue
    if not strings:
        return None
    hours, start, end = advice.annual_hours(float(lat), float(lon))

    def total(m):
        hh = advice.horizon_hours(hours, horizon_profile(m))
        return sum(advice.yearly_kwh_kwp(hh, c["tilt"], c["azimuth"], pr) * c["wp"] / 1000.0 for c in strings)

    base = total(mask)
    if base <= 0:
        return None
    out = {"base_kwh": round(base), "wp": round(sum(c["wp"] for c in strings)), "strings": len(strings),
           "period": [start.isoformat(), end.isoformat()], "source": site.get("horizon_source") or "",
           "trim": None, "ridge": None}

    # --- Ausästen: nur mit Baumgeometrie UND Restschatten, sonst gar nicht
    if tree and isinstance(res, (list, tuple)) and len(res) == 2:
        lo_deg, hi_deg = float(min(res)), float(max(res))
        steps = []
        for cut in (2.0, 4.0, 6.0, None):
            x = float(tree["height_m"]) if cut is None else cut
            opt = total(trimmed_mask(mask, tree, x, lo_deg))     # niedriger Restschatten -> mehr Gewinn
            pes = total(trimmed_mask(mask, tree, x, hi_deg))     # hoher Restschatten -> weniger Gewinn
            steps.append({"cut_m": cut, "kwh_min": round(min(opt, pes)), "kwh_max": round(max(opt, pes)),
                          "gain_min_pct": round(100.0 * (min(opt, pes) - base) / base, 1),
                          "gain_max_pct": round(100.0 * (max(opt, pes) - base) / base, 1)})
        out["trim"] = {"steps": steps, "residual_deg": [lo_deg, hi_deg], "tree": dict(tree)}

    # --- Umsetzen: braucht eine zweite, ebenfalls gemessene Maske für eine andere Stelle der Fläche
    best = None
    for name, m in (site.get("horizon_variants") or {}).items():
        if not isinstance(m, dict) or len(m) < 2:
            continue
        v = total(m)
        if best is None or v > best["kwh_year"]:
            gap = sorted(((float(mask.get(k, 0)) - float(el), k) for k, el in m.items() if k in mask), reverse=True)
            best = {"name": name, "kwh_year": round(v), "gain_pct": round(100.0 * (v - base) / base, 1),
                    "az": int(float(gap[0][1])) if gap else None,
                    "here_deg": float(mask[gap[0][1]]) if gap else None,
                    "there_deg": float(m[gap[0][1]]) if gap else None}
    out["ridge"] = best
    return out


# ---------------------------------------------------------------------------- Die Erkenntnisse bauen
def build(cfg, data):
    items = []
    shading = None

    def add(key, area, level, basis, de_text, en_text, detail=None):
        items.append({"key": key, "area": area, "level": level, "basis": basis,
                      "de": de_text, "en": en_text, "detail": detail or {}})

    price_ct = (cfg.get("tariff") or {}).get("price_ct_kwh") or D["price_ct_kwh"]
    names = (cfg.get("site") or {}).get("names") or {}

    def sname(i):
        return names.get(str(i)) or f"String {i}"

    # ------------------------------------------------------------------ 1 Verschattung
    sh = data.get("shading")
    if sh:
        shading = sh
        oriented = sh["oriented"]
        # Ohne eingetragene Ausrichtung ist die Karte eine Karte des Sonnenstands, nicht des Daches.
        # Ein String, der mittags einbricht, kann verschattet sein – oder schlicht nach Westen zeigen.
        # Diesen Unterschied kann die Messung allein nicht auflösen, und ihn zu verschweigen hieße,
        # eine Ausrichtung als Verschattung zu verkaufen.
        caveat_de = ("" if oriented else " Ob das Verschattung ist oder die Ausrichtung der Module, "
                     "kann die Karte nicht entscheiden: Neigung und Azimut sind auf der Einstellungsseite "
                     "nicht eingetragen, und nach Westen zeigende Module brechen am Vormittag genauso ein. "
                     "Trag sie ein, dann wird aus dem Muster ein Befund.")
        caveat_en = ("" if oriented else " Whether that is shading or simply the orientation of the modules, "
                     "the map cannot decide: tilt and azimuth are not entered on the settings page, and modules "
                     "facing west dip in the morning just the same. Enter them and the pattern becomes a finding.")
        basis = "measured" if oriented else "thin"
        dips = data.get("dips") or []
        if dips:
            d = dips[0]
            rec_de = (f"ab {de(d['recovered_at'], 0)}° hält er wieder mit" if d.get("recovered_at")
                      else "bis zum Ende des gemessenen Bereichs holt er nicht wieder auf")
            rec_en = (f"from {d['recovered_at']:.0f}° on it recovers" if d.get("recovered_at")
                      else "it does not recover within the measured range")
            rest = dips[1:3]
            more_de = ("" if not rest else " Dasselbe Muster zeigt " + " und ".join(
                f"{sname(o['string'])} zwischen {de(o['az_from'], 0)}° und {de(o['az_to'], 0)}° "
                f"({o['mean_pct']} %)" for o in rest) + "."
                + (f" Weitere {len(dips) - 3} Bereiche stehen in der Karte." if len(dips) > 3 else ""))
            more_en = ("" if not rest else " The same pattern shows for " + " and ".join(
                f"{sname(o['string'])} between {o['az_from']:.0f}° and {o['az_to']:.0f}° "
                f"({o['mean_pct']} %)" for o in rest) + "."
                + (f" {len(dips) - 3} further ranges are in the map." if len(dips) > 3 else ""))
            add("shading_string", "pv", "warn", basis,
                f"{sname(d['string'])} liefert zwischen {de(d['az_from'], 0)}° und {de(d['az_to'], 0)}° Sonnenazimut "
                f"nur {d['mean_pct']} % dessen, was die anderen drei im selben Moment schaffen, im schlechtesten "
                f"Feld {d['worst_pct']} % – {rec_de}. Gemessen an {len(sh['azimuths'])} Azimutfeldern aus "
                f"{sh['points']} Messpunkten.{more_de}{caveat_de}",
                f"{sname(d['string'])} delivers only {d['mean_pct']} % of what the other three manage at the same "
                f"moment between {d['az_from']:.0f}° and {d['az_to']:.0f}° solar azimuth, {d['worst_pct']} % in the "
                f"worst cell – {rec_en}. Measured across {len(sh['azimuths'])} azimuth cells from {sh['points']} "
                f"samples.{more_en}{caveat_en}",
                {"dips": dips, "az_step": sh["az_step"], "el_step": sh["el_step"], "peak_w": sh["peak_w"]})
        else:
            add("shading_string", "pv", "good", basis,
                f"Kein String fällt in einem Azimutbereich dauerhaft unter {round(D['deficit'] * 100)} % der anderen "
                f"drei – Einzelverschattung ist in {sh['points']} Messpunkten über {len(sh['azimuths'])} Azimutfelder "
                f"nicht zu sehen.{caveat_de}",
                f"No string stays below {round(D['deficit'] * 100)} % of the other three in any azimuth range – "
                f"single-string shading does not show in {sh['points']} samples across {len(sh['azimuths'])} azimuth "
                f"cells.{caveat_en}",
                {"az_step": sh["az_step"], "el_step": sh["el_step"], "peak_w": sh["peak_w"]})

        commons = data.get("commons") or []
        if commons:
            c = commons[0]
            add("shading_common", "pv", "warn", basis,
                f"Zwischen {de(c['az_from'], 0)}° und {de(c['az_to'], 0)}° Sonnenazimut liefern alle vier Strings "
                f"zusammen bei {de(c['el'], 0)}° Sonnenhöhe im Mittel {c['loss_pct']} % "
                f"weniger als beim tieferen Stand von {de(c['ref_el'], 0)}° im selben Azimutfeld – über "
                f"{c['cells']} benachbarte Felder hinweg. Eine Verschattung, die alle vier gleich trifft, fällt im "
                "Vergleich der Strings untereinander nicht auf: es liegt ja keiner unter dem Durchschnitt der "
                f"anderen.{caveat_de}",
                f"Between {c['az_from']:.0f}° and {c['az_to']:.0f}° solar azimuth, all four strings together deliver "
                f"{c['loss_pct']} % less on average at {c['el']:.0f}° solar elevation than "
                f"at the lower {c['ref_el']:.0f}° in the same azimuth cell – across {c['cells']} adjacent cells. "
                "Shading that hits all four alike does not show up when comparing the strings with each other: none "
                f"of them is below the average of the others.{caveat_en}",
                {"dips": commons})
        elif sh["cells"] >= D["min_cells"]:
            add("shading_common", "pv", "info", "thin",
                "Eine Verschattung, die alle vier Strings gleich trifft, ist nicht zu sehen – aber das heißt hier "
                "noch wenig: sie zeigt sich nur daran, dass die Summe bei höherer Sonne fällt, und dafür braucht es "
                "denselben Azimut bei deutlich verschiedener Sonnenhöhe. Den Unterschied macht die Jahreszeit, und "
                f"gemessen wird erst seit dem {day_de(sh['from'])}. Ab etwa drei Monaten trägt der Vergleich.",
                "Shading that hits all four strings alike does not show – but that means little yet: it only appears "
                "as the sum falling while the sun rises, and that needs the same azimuth at clearly different solar "
                f"elevations. The season makes that difference, and measurement only started on {sh['from']}. From "
                "about three months on the comparison carries.",
                {"cells": sh["cells"], "from": sh["from"]})

        hs = data.get("sector")
        if hs:
            # Die Feststellung steht, die Deutung nicht: ohne Neigung und Azimut sind "Eiche im Süden"
            # und "Module nach Westen" dieselbe Messkurve. Wer hier eine Verschattung behauptet, behauptet
            # sie auf gut Glück – und liegt in der Hälfte der Fälle falsch.
            add("shading_sector", "pv", "info", "thin" if not oriented else "measured",
                f"Wenn die Sonne am höchsten steht – zwischen {de(hs['az_from'], 0)}° und {de(hs['az_to'], 0)}° "
                f"Azimut, bis {de(hs['el_top'], 0)}° hoch –, liefern alle vier Strings zusammen nur "
                f"{hs['pct_of_best']} % dessen, was sie bei {de(hs['best_az'], 0)}° erreichen. Die beste Stunde des "
                "Tages ist hier nicht die hellste. Das kann ein Gegenstand im Süden sein, es kann aber genauso "
                "heißen, dass die Module nach Westen zeigen und der Mittag schlicht nicht ihre Zeit ist – beides "
                "ergibt dieselbe Kurve. Diese Karte kann das nicht trennen; eingetragene Neigung und Ausrichtung "
                "könnten es." if not oriented else
                f"Wenn die Sonne am höchsten steht – zwischen {de(hs['az_from'], 0)}° und {de(hs['az_to'], 0)}° "
                f"Azimut, bis {de(hs['el_top'], 0)}° hoch –, liefern alle vier Strings zusammen nur "
                f"{hs['pct_of_best']} % dessen, was sie bei {de(hs['best_az'], 0)}° erreichen.",
                f"When the sun stands highest – between {hs['az_from']:.0f}° and {hs['az_to']:.0f}° azimuth, up to "
                f"{hs['el_top']:.0f}° high – all four strings together deliver only {hs['pct_of_best']} % of what "
                f"they reach at {hs['best_az']:.0f}°. The brightest hour of the day is not the best one here. That "
                "may be an object to the south, but it may just as well mean the modules face west and midday simply "
                "is not their time – both produce the same curve. This map cannot separate them; entered tilt and "
                "azimuth could." if not oriented else
                f"When the sun stands highest – between {hs['az_from']:.0f}° and {hs['az_to']:.0f}° azimuth, up to "
                f"{hs['el_top']:.0f}° high – all four strings together deliver only {hs['pct_of_best']} % of what "
                f"they reach at {hs['best_az']:.0f}°.",
                hs)

    # ------------------------------------------------------------------ 8 Was wäre wenn
    # Zwei Vorschläge. Sie stehen zwischen lauter gemessenen Erkenntnissen, und deshalb sagt jeder Satz
    # im Konjunktiv, dass er eine Rechnung ist: "brächte", nicht "bringt"; "läge", nicht "liegt".
    rel = data.get("relief")
    if rel and rel.get("trim"):
        tr = rel["trim"]; lo, hi = tr["residual_deg"]
        # Die Spanne aus dem Restschatten öffnet sich erst, wenn der gekürzte Baum unter den Gürtel dahinter
        # fällt. Solange beide Enden dieselbe Zahl ergeben, steht auch nur eine da – "13,8 bis 13,8 %" wäre
        # eine Genauigkeit, die es nicht gibt, und eine Spanne, die es nicht ist.
        def span_de(x):
            a, b = x["gain_min_pct"], x["gain_max_pct"]
            return f"+{de(a)} %" if abs(b - a) < 0.1 else f"+{de(a)} bis +{de(b)} %"

        def span_en(x):
            a, b = x["gain_min_pct"], x["gain_max_pct"]
            return f"+{a:.1f} %" if abs(b - a) < 0.1 else f"+{a:.1f} to +{b:.1f} %"

        step_de = "; ".join((f"ganz gefällt {span_de(x)}" if x["cut_m"] is None
                             else f"um {de(x['cut_m'], 0)} m gekürzt {span_de(x)}") for x in tr["steps"])
        step_en = "; ".join((f"felled entirely {span_en(x)}" if x["cut_m"] is None
                             else f"shortened by {x['cut_m']:.0f} m {span_en(x)}") for x in tr["steps"])
        last = tr["steps"][-1]
        add("tree_trim", "pv", "info", "modelled",
            f"Angenommen, der nahe Baum würde gekürzt: zwischen {de(tr['tree']['az_from'], 0)}° und "
            f"{de(tr['tree']['az_to'], 0)}° Azimut steht er in der vermessenen Horizontmaske, und sein Wipfel "
            f"liegt {de(tr['tree']['height_m'])} m über der Modulebene. Über dasselbe Jahresmodell gerechnet, "
            f"mit dem auch die Ausrichtung beurteilt wird, käme dann heraus: {step_de}. Aus heute "
            f"{de(rel['base_kwh'], 0)} kWh im Jahr an {de(rel['wp'], 0)} Wp würden ganz ohne diesen Baum "
            f"{de(last['kwh_min'], 0)} bis {de(last['kwh_max'], 0)} kWh. Der Gehölzgürtel dahinter bleibt in der "
            f"Rechnung stehen – er hält den Horizont bei {de(lo, 0)} bis {de(hi, 0)}°. Wo eine Zahl oben eine "
            "Spanne hat, kommt sie von dort; bei den kleinen Kürzungen ragt der Baum noch über den Gürtel "
            "hinaus, und dann spielt es keine Rolle, wo genau der Gürtel steht. Der wirksame Abstand des Baums ist aus der Maske selbst "
            "zurückgerechnet, nicht aus der Kronenmitte; wo an einem Azimut ein anderer, näherer Baum die "
            "Silhouette bildet, nimmt ein Kürzen des nahen ihn nicht weg, und dort ist die Zahl eher zu "
            "freundlich. Das alles ist gerechnet und nicht gemessen, und es gilt für den belaubten Zustand, "
            "in dem die Maske aufgenommen wurde. Im Winter fiele der Gewinn kleiner aus, weil der kahle Baum "
            "ohnehin mehr durchlässt – wieviel mehr, ist hier nicht gemessen und wird deshalb nicht beziffert.",
            f"Supposing the near tree were shortened: it stands between {tr['tree']['az_from']:.0f}° and "
            f"{tr['tree']['az_to']:.0f}° azimuth in the surveyed horizon mask, its top {tr['tree']['height_m']:.1f} m "
            f"above the plane of the modules. Computed with the same annual model that judges the orientation, "
            f"the result would be: {step_en}. Today's {rel['base_kwh']:.0f} kWh a year from {rel['wp']:.0f} Wp would "
            f"become {last['kwh_min']:.0f} to {last['kwh_max']:.0f} kWh without that tree at all. The belt of trees "
            f"behind it stays standing in this calculation – it holds the horizon at {lo:.0f} to {hi:.0f}°. Where a "
            "figure above carries a span, that is where it comes from; for the small cuts the tree still rises "
            "above the belt, and then it does not matter where exactly the belt stands. The effective distance of the tree is derived back from the "
            "mask itself, not from the centre of the crown; where another, nearer tree forms the silhouette at some "
            "azimuth, shortening this one does not remove it, and there the figure is on the generous side. All of "
            "this is computed, not measured, and it holds for the leafed state the mask was recorded in. In winter "
            "the gain would be smaller because a bare tree lets more through anyway – how much more is not measured "
            "here and is therefore not given a number.",
            {**tr, "base_kwh": rel["base_kwh"], "wp": rel["wp"], "period": rel["period"], "source": rel["source"]})

    if rel and rel.get("ridge") and rel["ridge"]["gain_pct"] > 0:
        rg = rel["ridge"]
        east_de = (f" Am Azimut {rg['az']}° etwa steht der Horizont dort bei {de(rg['there_deg'], 0)}° statt bei "
                   f"{de(rg['here_deg'], 0)}°." if rg.get("az") is not None else "")
        east_en = (f" At {rg['az']}° azimuth, for instance, the horizon there sits at {rg['there_deg']:.0f}° instead "
                   f"of {rg['here_deg']:.0f}°." if rg.get("az") is not None else "")
        add("module_ridge", "pv", "info", "modelled",
            f"Für die Stelle „{rg['name']}“ auf derselben Dachfläche liegt eine zweite, ebenfalls vermessene "
            f"Horizontmaske vor – höher gelegen und deshalb weiter über dem, was im Süden steht.{east_de} Säßen die "
            f"Module dort statt an ihrem heutigen Platz, brächte dasselbe Jahresmodell {de(rg['gain_pct'])} % mehr: "
            f"{de(rg['kwh_year'], 0)} statt {de(rel['base_kwh'], 0)} kWh im Jahr. Das wäre die Maßnahme ohne Baum und "
            "ohne Genehmigung. Auch hier gilt: gerechnet, nicht gemessen, und für den belaubten Zustand. Ob die "
            "Fläche dort oben für alle Module reicht, wie sie befestigt würden und was das Umsetzen kostet, sagt "
            "eine Horizontmaske nicht.",
            f"For the spot “{rg['name']}” on the same roof surface a second, likewise surveyed horizon mask "
            f"exists – higher up and therefore further above whatever stands to the south.{east_en} Were the modules "
            f"to sit there instead of where they are today, the same annual model would give {rg['gain_pct']:.1f} % "
            f"more: {rg['kwh_year']:.0f} instead of {rel['base_kwh']:.0f} kWh a year. That would be the measure that "
            "needs no tree and no permission. Here too: computed, not measured, and for the leafed state. Whether "
            "there is room up there for all the modules, how they would be fixed and what moving them costs, a "
            "horizon mask does not say.",
            {**rg, "base_kwh": rel["base_kwh"], "wp": rel["wp"], "period": rel["period"], "source": rel["source"]})

    # ------------------------------------------------------------------ 2 Vorlauf
    fl = data.get("flow")
    spf = data.get("spf")
    if fl:
        # Der Vergleich ist bewusst der vorsichtige: die Bänder gelten für den kältesten Tag des Jahres.
        # Liegt der Vorlauf schon bei milder Witterung darüber, ist der Befund erst recht gültig; liegt er
        # darunter, sagt das noch nichts, weil der kalte Tag noch aussteht.
        v = fl["flow_c"]
        mild = fl["coldest_c"] > 0.0          # der kälteste Tag des Jahres steht noch aus
        above = v > HPM["good_flow"] + HPM["band"]
        if v >= HPM["bad_from"]:
            level, verdict_de, verdict_en = "warn", "gilt als ineffizient", "counts as inefficient"
        elif v > HPM["mid_flow"] + HPM["band"]:
            level, verdict_de, verdict_en = "warn", "liegt über beiden Bändern", "sits above both bands"
        elif above:
            level, verdict_de, verdict_en = "warn", "liegt über dem guten Band", "sits above the good band"
        else:
            level, verdict_de, verdict_en = "good", "liegt im guten Band", "sits inside the good band"
        # Über dem Band schon bei milder Witterung zu liegen ist ein Befund: am kalten Tag steigt der
        # Vorlauf weiter. Darunter zu liegen ist keiner – der Vergleichswert gilt für den kältesten Tag,
        # und der ist noch nicht gemessen. Nur die eine Richtung trägt, und das muss im Text stehen.
        basis = "thin" if (mild and not above) else "measured"
        spf_de = spf_en = ""
        if spf and spf.get("enough"):
            spf_de = (f" Die gleitende 30-Tage-Arbeitszahl steht bei {de(spf['spf'], 2)} (bisher "
                      f"{spf['days']} volle Messtage im Fenster) – gezählt vom internen Zähler der Wolf, "
                      f"Bilanzgrenze H1/H2. Die {HPM['fleet_n']} Anlagen von heatpumpmonitor.org zählen nach H4, "
                      f"also mit Heizkreispumpen und Heizstab, und kommen im Mittel auf {de(HPM['fleet_spf'], 2)}. "
                      "Die eigene Zahl ist deshalb systematisch zu gut, um direkt danebengestellt zu werden.")
            spf_en = (f" The rolling 30-day performance factor stands at {spf['spf']:.2f} ({spf['days']} full "
                      f"days of measurement in the window so far) – counted by the Wolf's internal meter, boundary H1/H2. The "
                      f"{HPM['fleet_n']} systems on heatpumpmonitor.org count to H4, i.e. including circulation "
                      f"pumps and immersion heater, and average {HPM['fleet_spf']:.2f}. The own figure is therefore "
                      "systematically too flattering to be put next to theirs.")
        if not mild:
            mild_de = mild_en = ""
        elif above:
            mild_de = (f" Gemessen wurde bei Außentemperaturen bis herunter auf {de(fl['coldest_c'])} °C, die Bänder "
                       "gelten für den kältesten Tag des Jahres – wer schon bei dieser Witterung darüber liegt, "
                       "liegt am kalten Tag erst recht darüber.")
            mild_en = (f" Measured down to outside temperatures of {fl['coldest_c']:.1f} °C, and the bands refer to "
                       "the coldest day of the year – being above them already in this weather means being above "
                       "them on a cold day as well.")
        else:
            mild_de = (f" Das ist noch kein Freispruch: gemessen wurde nur bis herunter auf {de(fl['coldest_c'])} °C "
                       "Außentemperatur, und die Bänder gelten für den kältesten Tag des Jahres. Mit der Kälte "
                       "steigt der Vorlauf, und erst dann steht die Zahl an der richtigen Stelle. Belastbar wird "
                       "der Vergleich nach dem ersten Frostabschnitt.")
            mild_en = (f" That is not an acquittal yet: measurement only reaches down to {fl['coldest_c']:.1f} °C "
                       "outside, and the bands refer to the coldest day of the year. The flow rises with the cold, "
                       "and only then does the figure stand where it belongs. The comparison carries after the "
                       "first spell of frost.")
        add("flow_temp", "heat", level, basis,
            f"Die Wärme geht im Mittel mit {de(v)} °C Vorlauf ins Haus, {de(fl['lift_k'])} K über der Außenluft – "
            f"wärmemengengewichtet über {fl['points']} Messpunkte im Heizbetrieb. Gute Anlagen "
            f"(Arbeitszahl ≥ {de(HPM['good_spf'], 1)}) fahren am kältesten Tag im Mittel {de(HPM['good_flow'])} °C "
            f"± {de(HPM['band'])} K, mittlere (≈ {de(HPM['mid_spf'], 1)}) {de(HPM['mid_flow'])} °C ± {de(HPM['band'])} K, "
            f"{de(HPM['bad_from'], 0)} bis {de(HPM['bad_to'], 0)} °C gilt als ineffizient. Der eigene Wert "
            f"{verdict_de}.{mild_de}{spf_de}",
            f"Heat enters the house at {v:.1f} °C flow on average, {fl['lift_k']:.1f} K above outside air – weighted "
            f"by delivered heat over {fl['points']} samples in heating mode. Good systems (performance factor "
            f"≥ {HPM['good_spf']:.1f}) run {HPM['good_flow']:.1f} °C ± {HPM['band']:.1f} K on the coldest day, "
            f"average ones (≈ {HPM['mid_spf']:.1f}) {HPM['mid_flow']:.1f} °C ± {HPM['band']:.1f} K, "
            f"{HPM['bad_from']:.0f} to {HPM['bad_to']:.0f} °C counts as inefficient. The own figure "
            f"{verdict_en}.{mild_en}{spf_en}",
            {**fl, "bands": HPM, "spf": spf})

    # ------------------------------------------------------------------ 3 Taktung
    cy = data.get("cycling")
    if cy and cy.get("cycles"):
        by_temp = [b for b in (cy.get("by_temp") or []) if b.get("n")]
        temp_de = "; ".join(f"bei {b['t']} °C {de(b['median_min'])} min ({b['n']})" for b in by_temp)
        temp_en = "; ".join(f"at {b['t']} °C {b['median_min']:.1f} min ({b['n']})" for b in by_temp)
        sev = (cy.get("verdict") or {}).get("severity")
        level = {"ok": "good", "warn": "warn", "bad": "warn"}.get(sev, "info")
        ww = (cy.get("by_mode") or {}).get("ww") or 0
        add("cycling", "heat", level, "measured",
            f"{cy['cycles']} Verdichterläufe in {de(cy.get('span_days', 0), 1)} Tagen, {de(cy.get('per_day', 0), 1)} "
            f"Starts am Tag, im Median {de(cy.get('median_min', 0))} min am Stück (Viertel darunter: "
            f"{de(cy.get('p25_min', 0))} min). Über die Außentemperatur: {temp_de}. "
            f"{ww} davon waren Warmwasser. {(cy.get('verdict') or {}).get('de', '')}",
            f"{cy['cycles']} compressor runs in {cy.get('span_days', 0):.1f} days, {cy.get('per_day', 0):.1f} starts "
            f"a day, {cy.get('median_min', 0):.1f} min per run at the median (lower quarter: "
            f"{cy.get('p25_min', 0):.1f} min). Against outside temperature: {temp_en}. "
            f"{ww} of them were hot water. {(cy.get('verdict') or {}).get('en', '')}",
            {"cycles": cy.get("cycles"), "per_day": cy.get("per_day"), "median_min": cy.get("median_min"),
             "p25_min": cy.get("p25_min"), "p75_min": cy.get("p75_min"), "span_days": cy.get("span_days"),
             "by_temp": by_temp, "buckets": cy.get("buckets"), "by_mode": cy.get("by_mode"),
             "runtime_share": cy.get("runtime_share"), "notes": cy.get("notes")})

    # ------------------------------------------------------------------ 4 Heizkurve zu steil
    rooms = data.get("rooms") or []
    hot = [r for r in rooms if r["share"] >= D["over_share"] and r["over_k"] >= D["over_k"]]
    if fl and rooms:
        flow_high = fl["flow_c"] > HPM["good_flow"] + HPM["band"]
        if len(hot) >= D["over_rooms"] and flow_high:
            worst = ", ".join(f"{r['room']} {de(r['over_k'], 1)} K" for r in hot[:4])
            worst_en = ", ".join(f"{r['room']} {r['over_k']:.1f} K" for r in hot[:4])
            cold = [r for r in rooms if r["over_k"] < -0.5]
            cold_de = (f" Gegenprobe: {', '.join(r['room'] for r in cold)} bleibt unter Soll – dort ist es kein "
                       "Vorlauf-, sondern ein Verteilungsproblem." if cold else "")
            cold_en = (f" Counter-check: {', '.join(r['room'] for r in cold)} stays below setpoint – there it is a "
                       "distribution problem, not a flow temperature one." if cold else "")
            add("heat_curve", "heat", "warn", "measured",
                f"{len(hot)} von {len(rooms)} Räumen stehen in den Heizstunden dauerhaft über ihrem Soll "
                f"({worst}), und gleichzeitig liegt der Vorlauf bei {de(fl['flow_c'])} °C. Beides zusammen ist das "
                "Bild einer zu steilen Heizkurve: das Haus bekommt mehr Temperatur, als es braucht, die Thermostate "
                "drosseln dagegen, und die Wärmepumpe zahlt den zu hohen Hub mit COP. Ein Schritt nach unten an der "
                f"Heizkurve kostet nichts und ist umkehrbar.{cold_de}",
                f"{len(hot)} of {len(rooms)} rooms stay above their setpoint throughout the heating hours "
                f"({worst_en}), and at the same time the flow sits at {fl['flow_c']:.1f} °C. Together that is the "
                "picture of a heating curve that is too steep: the house gets more temperature than it needs, the "
                "thermostats throttle against it, and the heat pump pays for the excess lift in COP. One step down "
                f"on the curve costs nothing and is reversible.{cold_en}",
                {"rooms": rooms, "flow_c": fl["flow_c"], "hot": [r["room"] for r in hot]})
        elif hot and not flow_high:
            who = ", ".join(f"{r['room']} {de(r['over_k'], 1)} K über Soll" for r in hot[:4])
            who_en = ", ".join(f"{r['room']} {r['over_k']:.1f} K above setpoint" for r in hot[:4])
            add("heat_curve", "heat", "info", "thin",
                f"{len(hot)} von {len(rooms)} Räumen stehen in den Heizstunden dauerhaft über ihrem Soll ({who}), "
                f"der Vorlauf liegt dabei aber nur bei {de(fl['flow_c'])} °C – bei {de(fl['outside_c'])} °C "
                "Außenluft. Für eine zu steile Heizkurve müsste beides zusammenfallen; so bleibt es ein Hinweis auf "
                "den Abgleich der einzelnen Räume. Die Heizkurve verrät sich erst, wenn es draußen kalt wird und "
                "der Vorlauf mit ihr steigt – dann zeigt sich, ob die Räume mitsteigen.",
                f"{len(hot)} of {len(rooms)} rooms stay above their setpoint throughout the heating hours ({who_en}), "
                f"but the flow only sits at {fl['flow_c']:.1f} °C – at {fl['outside_c']:.1f} °C outside. A heating "
                "curve that is too steep would need both at once; as it is, this points at the balancing of "
                "individual rooms. The curve only gives itself away once it turns cold outside and the flow rises "
                "with it – then it shows whether the rooms rise along.",
                {"rooms": rooms, "flow_c": fl["flow_c"], "outside_c": fl["outside_c"], "hot": [r["room"] for r in hot]})

    # ------------------------------------------------------------------ 5 Warmwasser zur falschen Zeit
    ww = data.get("dhw")
    if ww and ww["charges"] >= 5:
        level = "warn" if ww["ct_per_charge"] >= 2.0 else "info"
        basis = "modelled"    # der COP der Mittagsladung ist gerechnet, nicht gemessen
        add("dhw_timing", "heat", level, basis,
            f"Die {ww['charges']} Warmwasserladungen liefen im Median gegen {int(ww['hour_median'])} Uhr, bei im "
            f"Mittel {de(ww['t_out_mean'])} °C Außenluft. Mittags war es an denselben Tagen "
            f"{de(ww['t_noon_mean'])} °C warm; derselbe Hub weniger hätte je Ladung "
            f"{de(abs(ww['ct_per_charge']))} Cent gespart, zusammen {de(abs(ww['ct_total']))} Cent im gemessenen "
            f"Zeitraum. Gerechnet mit dem gemessenen COP jeder Ladung, auf die Mittagstemperatur umgerechnet, "
            f"und {de(price_ct, 0)} ct/kWh. Auf ein Jahr hochrechnen lässt sich das nicht: im Winter ist der "
            "Unterschied zwischen Nacht und Mittag ein anderer.",
            f"The {ww['charges']} hot water charges ran at {int(ww['hour_median']):02d}:00 at the median, at "
            f"{ww['t_out_mean']:.1f} °C outside air on average. At midday on the same days it was "
            f"{ww['t_noon_mean']:.1f} °C; that much less lift would have saved {abs(ww['ct_per_charge']):.1f} cents "
            f"per charge, {abs(ww['ct_total']):.1f} cents in total over the measured period. Calculated from the "
            f"measured COP of each charge, rescaled to the midday temperature, at {price_ct:.0f} ct/kWh. It cannot "
            "be extrapolated to a year: in winter the gap between night and midday is a different one.",
            ww)

    # ------------------------------------------------------------------ 6 Zellspannungsspreizung
    cs = data.get("cells")
    if cs and cs["months"]:
        m = cs["months"][-1]
        band_de = (f" bei {cs['soc_band'][0]} bis {cs['soc_band'][1]} % Ladestand"
                   + (f" und {de(cs['temp_band'][0])} bis {de(cs['temp_band'][1])} °C Zelltemperatur" if cs["temp_band"] else ""))
        band_en = (f" at {cs['soc_band'][0]} to {cs['soc_band'][1]} % state of charge"
                   + (f" and {cs['temp_band'][0]:.1f} to {cs['temp_band'][1]:.1f} °C cell temperature" if cs["temp_band"] else ""))
        if len(cs["months"]) >= 2:
            first = cs["months"][0]
            delta = m["mv"] - first["mv"]
            level = "warn" if delta > 5 else "good"
            add("cell_spread", "battery", level, "measured",
                f"Die Zellspannungen liegen {de(m['mv'])} mV auseinander ({m['month']}), im ersten gemessenen Monat "
                f"({first['month']}) waren es {de(first['mv'])} mV – {de(delta, 1)} mV Unterschied, verglichen nur"
                f"{band_de}.",
                f"Cell voltages sit {m['mv']:.1f} mV apart ({m['month']}); in the first measured month "
                f"({first['month']}) it was {first['mv']:.1f} mV – a difference of {delta:.1f} mV, compared only"
                f"{band_en}.", cs)
        else:
            add("cell_spread", "battery", "info", "thin",
                f"Die Zellen liegen zurzeit {de(m['mv'])} mV auseinander, gemessen an {m['n']} Punkten{band_de}. "
                "Das ist ein Stand, kein Trend: die Historie umfasst bisher einen einzigen Monat. Ein Trend wird "
                "erst sichtbar, wenn zwei volle Monate mit vergleichbarem Ladestand und vergleichbarer Temperatur "
                "nebeneinanderstehen.",
                f"The cells currently sit {m['mv']:.1f} mV apart, measured at {m['n']} points{band_en}. That is a "
                "level, not a trend: the history so far covers a single month. A trend only becomes visible once two "
                "full months with comparable state of charge and temperature stand side by side.", cs)

    # ------------------------------------------------------------------ 7 Standby und Umlauf
    sb = data.get("standby")
    if sb and sb.get("enough"):
        eur = sb["kwh_year"] * price_ct / 100.0
        add("standby", "battery", "warn" if sb["w"] >= 5 else "info", "measured",
            f"Der Speicher zieht nachts {de(sb['w'])} W, auch wenn er nichts abgibt – gemessen über {sb['nights']} "
            f"Nächte und {de(sb['hours'])} Stunden, in denen der Ausgang stand. Das sind {de(sb['kwh_year'])} kWh "
            f"im Jahr oder {de(eur, 0)} € bei {de(price_ct, 0)} ct/kWh. Der Ladestand kommt nur in ganzen Prozent, "
            f"ein Prozent sind rund {de(D['pack_wh'] * 2 / 100.0, 0)} Wh – die Auflösung dieser Messung liegt "
            f"deshalb bei etwa {de(sb['resolution_w'])} W.",
            f"The battery draws {sb['w']:.1f} W at night even when it delivers nothing – measured over "
            f"{sb['nights']} nights and {sb['hours']:.1f} hours with the output idle. That is {sb['kwh_year']:.1f} "
            f"kWh a year, or {eur:.0f} € at {price_ct:.0f} ct/kWh. State of charge is only reported in whole "
            f"percent, one percent being about {D['pack_wh'] * 2 / 100.0:.0f} Wh – so this measurement resolves to "
            f"roughly {sb['resolution_w']:.1f} W.", sb)
    elif sb:
        add("standby", "battery", "info", "thin",
            f"Für den Nachtverbrauch reichen die Daten noch nicht: nur {sb['nights']} Nächte mit stehendem Ausgang "
            f"und zusammen {de(sb['hours'])} Stunden. Belastbar wird die Zahl ab etwa drei Nächten mit je zwei "
            "Stunden Stillstand – solange der Speicher abends leerläuft und das Gerät sich abmeldet, kommen die "
            "nur langsam zusammen.",
            f"Not enough data for the night draw yet: only {sb['nights']} nights with the output idle, "
            f"{sb['hours']:.1f} hours in total. The figure becomes solid from about three nights of two idle hours "
            "each – while the battery runs empty in the evening and the device goes offline, they add up slowly.", sb)

    rt = data.get("roundtrip")
    if rt and rt.get("enough"):
        add("round_trip", "battery", "info" if rt["pct"] >= 80 else "warn", "measured",
            f"Von der Energie, die in den Speicher geht, kommen {de(rt['pct'])} % wieder heraus: "
            f"{de(rt['charged_kwh'], 1)} kWh hinein, {de(rt['discharged_kwh'], 1)} kWh heraus, "
            f"und der Ladestand, der am Ende um {de(abs(rt['stored_kwh']), 2)} kWh "
            f"{'höher' if rt['stored_kwh'] > 0 else 'niedriger'} lag als am Anfang, ist gegengerechnet. "
            "Darin stecken Laderegler, "
            "Wechselrichter und Zellen gemeinsam – es ist der Umlauf des ganzen Geräts, nicht der der Zellen. "
            "Der Standby von oben steckt mit darin, und die Abgabegrenze von 800 W drückt ihn zusätzlich: was "
            "darüber hinaus in den Speicher ging, muss langsam wieder heraus, und langsam heißt lange am Netz.",
            f"Of the energy that goes into the battery, {rt['pct']:.1f} % comes back out: {rt['charged_kwh']:.1f} kWh "
            f"in, {rt['discharged_kwh']:.1f} kWh out, and the state of charge ending {abs(rt['stored_kwh']):.2f} kWh "
            f"{'higher' if rt['stored_kwh'] > 0 else 'lower'} than it started is accounted for. Charge controller, inverter and cells are all in there – it is the round trip of the "
            "whole device, not of the cells. The standby above is part of it, and the 800 W output limit weighs on "
            "it as well: whatever went into the battery beyond that has to come out slowly, and slowly means many "
            "hours on the line.", rt)

    return {"updated": int(time.time()), "items": items, "shading": shading}


# ---------------------------------------------------------------------------- Daten einsammeln
def collect(cfg):
    data = {}
    site = cfg.get("site") or {}
    lat, lon = site.get("lat"), site.get("lon")
    oriented = bool([1 for v in (site.get("strings") or {}).values()
                     if isinstance(v, dict) and v.get("tilt") is not None and v.get("azimuth") is not None])
    packs = cfg.get("packs") or 2.0
    wh_per_pct = packs * D["pack_wh"] / 100.0

    with Pg() as pg:
        rows = pg.query("""SELECT extract(epoch from ts)::bigint, pv_v[1], pv_a[1], pv_v[2], pv_a[2],
                                  pv_v[3], pv_a[3], pv_v[4], pv_a[4], pv_w, out_w, bat_w, soc
                           FROM samples WHERE ts > now() - interval '400 days' ORDER BY ts""")
        heat_rows = pg.query("""SELECT extract(epoch from ts)::bigint, hp_w, heat_w, flow_c, outside_c,
                                       compressor, mode
                                FROM heat_samples WHERE ts > now() - interval '400 days' ORDER BY ts""")
        room_rows = pg.query("""SELECT room, extract(epoch from date_trunc('hour', ts))::bigint,
                                       avg(temp_c), avg(setpoint_c), count(*)
                                FROM room_samples WHERE ts > now() - interval '400 days'
                                GROUP BY 1, 2 ORDER BY 2""")
        day_rows = pg.query("""SELECT to_char(day, 'YYYY-MM-DD'), heat_kwh, el_kwh
                               FROM heat_days WHERE day > current_date - 60 ORDER BY day""")
        cycle_rows = pg.query("""SELECT extract(epoch from ts)::bigint,
                                        extract(epoch from coalesce(started, ts))::bigint,
                                        minutes, mode, t_out, flow_c, kwh, cop
                                 FROM heat_cycles WHERE ts > now() - interval '400 days' ORDER BY ts""")

    # --- PV
    pv, samples = [], []
    for r in rows:
        ts = int(float(r[0]))
        v = [num(r[1 + 2 * i]) for i in range(4)]
        a = [num(r[2 + 2 * i]) for i in range(4)]
        soc = num(r[12])
        # Ein voller Speicher regelt die Laderegler ab: Spannung und Strom sinken, obwohl die Sonne
        # unverändert scheint. In der Karte sähe das aus wie ein Schatten, der jeden Sommermittag
        # dieselbe Stelle trifft. Diese Punkte fliegen raus, statt später erklärt werden zu müssen.
        if all(x is not None for x in v + a) and (soc is None or soc < 97):
            pv.append((ts, [max(0.0, v[i] * a[i]) for i in range(4)]))
        samples.append((ts, num(r[9]), num(r[10]), num(r[11]), soc))
    if pv and lat is not None and lon is not None:
        sh = shading_map(pv, float(lat), float(lon), oriented)
        if sh:
            data["shading"] = sh
            data["dips"] = single_string_dips(sh)
            data["commons"] = common_dip(sh)
            data["sector"] = high_sun_sector(sh)

    # --- Wärmepumpe
    heat = []
    for r in heat_rows:
        heat.append((int(float(r[0])), num(r[1]), num(r[2]), num(r[3]), num(r[4]),
                     int(float(r[5])) if r[5] is not None else None,
                     int(float(r[6])) if r[6] is not None else None))
    data["flow"] = flow_lift(heat)

    # Stunden, in denen wirklich geheizt wurde – Bezugsraum für die Räume. Nebenbei: wie viele Stunden
    # je Tag überhaupt gemessen wurden (entscheidet, welcher Tageszähler zählt) und wie warm es mittags war.
    heat_hours, noon, covered = set(), {}, {}
    for ts, hp_w, heat_w, flow_c, outside_c, comp, mode in heat:
        if mode in MODE_HZ and comp and (heat_w or 0) > 200:
            heat_hours.add(ts - ts % 3600)
        lt = time.localtime(ts)
        day = time.strftime("%Y-%m-%d", lt)
        covered.setdefault(day, set()).add(lt.tm_hour)
        if 11 <= lt.tm_hour < 15 and outside_c is not None:
            d = noon.setdefault(day, [0.0, 0])
            d[0] += outside_c; d[1] += 1
    noon_temp = {d: v[0] / v[1] for d, v in noon.items() if v[1] >= 10}
    data["spf"] = rolling_spf([(r[0], num(r[1]), num(r[2])) for r in day_rows],
                              {d: len(h) for d, h in covered.items()})

    data["rooms"] = rooms_over_setpoint(
        [(r[0], int(float(r[1])), num(r[2]), num(r[3]), int(float(r[4]))) for r in room_rows], heat_hours)

    cycles = [{"ts": int(float(r[0])), "start": int(float(r[1])), "minutes": num(r[2]), "mode": r[3],
               "t_out": num(r[4]), "flow_c": num(r[5]), "kwh": num(r[6]), "cop": num(r[7])} for r in cycle_rows]
    price_ct = (cfg.get("tariff") or {}).get("price_ct_kwh") or D["price_ct_kwh"]
    data["dhw"] = dhw_timing(cycles, noon_temp, price_ct)

    # --- Speicher
    data["standby"] = standby(samples, wh_per_pct)
    data["roundtrip"] = round_trip(samples, wh_per_pct)
    try:
        data["cells"] = cell_spread()
    except Exception as exc:
        LOG.warning("Zellspannungen nicht lesbar: %s", exc)

    data["cycling"] = cfg.get("cycling")

    # Das Jahresmodell holt sich ein Jahr Stundenwerte aus dem Open-Meteo-Archiv; advice.py hält sie im
    # Speicher, bis der Monat wechselt. Fällt der Abruf aus, fehlen eben die beiden Vorschläge – sie sind
    # das Einzige hier, was von außen kommt, und nichts davon darf die gemessenen Erkenntnisse aufhalten.
    try:
        data["relief"] = relief_model(site)
    except Exception as exc:
        LOG.warning("Jahresmodell für Ausästen/Umsetzen nicht gerechnet: %s", exc)

    return data


# ---------------------------------------------------------------------------- Dienst
CFG = {"site": None, "tariff": None, "cycling": None, "packs": None}
wake = threading.Event()


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload)
    except Exception:
        return
    t = msg.topic
    # Geweckt wird nur, wenn sich wirklich etwas geändert hat. Ohne diese Bedingung weckt jede eingehende
    # Nachricht die Rechnung – und die Gerätezustände kommen alle fünf Sekunden. Der Dienst lief dadurch
    # im Dauerlauf und zog jedes Mal über 18 000 Messpunkte aus Postgres (bemerkt am 27.09.2026).
    def merke(schluessel, wert):
        if CFG.get(schluessel) == wert:
            return False
        CFG[schluessel] = wert
        return True

    if t.endswith("/config/site"):
        geaendert = merke("site", payload)
    elif t.endswith("/config/tariff"):
        geaendert = merke("tariff", payload)
    elif t.endswith("/wolf/cycling"):
        # Mitnehmen, aber nicht dafür wecken: die Taktauswertung trägt laufende Zähler und ändert sich
        # ständig. Wer darauf weckt, macht aus dem Mindestabstand die Taktrate – der Dienst lief dadurch
        # jede Minute statt alle zehn (bemerkt am 27.09.2026). Beim nächsten planmäßigen Lauf ist der
        # neueste Stand ohnehin da, weil er hier im Speicher liegt.
        CFG["cycling"] = payload
        return
    elif t.endswith("/wolf/derived"):
        # Ältere Stände der wolf-bridge hängen die Taktauswertung an "derived" statt an "cycling".
        # Beides annehmen, keines neu rechnen – und aus demselben Grund wie oben nicht dafür wecken.
        if isinstance(payload.get("cycling"), dict):
            CFG["cycling"] = payload["cycling"]
        return
    elif t.endswith("/state") and "/grobro/" in t:
        # Die Zahl der Batteriepacks ändert sich praktisch nie. Sie mitnehmen, aber niemals dafür wecken –
        # dieses Topic ist das häufigste im ganzen Broker.
        q = payload.get("batteryPackageQuantity")
        if q:
            CFG["packs"] = float(q)
        return
    else:
        return
    if geaendert:
        wake.set()


def run_once(client):
    site = CFG["site"] or {}
    if site.get("lat") is None:
        LOG.info("Noch kein Standort auf %s/grolo/config/site – warte", BASE)
    result = build(CFG, collect(CFG))
    client.publish(f"{BASE}/grolo/insights", json.dumps(result, ensure_ascii=False), retain=True)
    LOG.info("%d Erkenntnisse veröffentlicht: %s", len(result["items"]),
             ", ".join(f"{i['key']} ({i['level']}/{i['basis']})" for i in result["items"]) or "keine")
    return result


def main():
    client = mqtt.Client(client_id=f"grolo-insights-{os.getpid()}", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = lambda c, u, f, rc, p=None: (
        LOG.info("MQTT verbunden %s:%s", HOST, PORT),
        c.subscribe([(f"{BASE}/grolo/config/site", 0), (f"{BASE}/grolo/config/tariff", 0),
                     (f"{BASE}/grolo/wolf/cycling", 0), (f"{BASE}/grolo/wolf/derived", 0),
                     (f"{BASE}/grobro/+/state", 0)]))
    client.on_message = on_message
    # Pflicht in jedem Sidecar: paho dreht nach einem halb offenen Socket in der Empfangsschleife weiter,
    # ohne je wieder eine Nachricht zu liefern (siehe mqtt_watchdog.py). Der Wachhund beendet den Prozess,
    # der Container startet neu.
    mqtt_watchdog.install(client, "insights")
    while True:
        try:
            client.connect(HOST, PORT, 60); break
        except Exception as exc:
            LOG.warning("MQTT nicht erreichbar (%s), neuer Versuch in 10 s", exc); time.sleep(10)
    client.loop_start()
    wake.wait(60)          # den retained Konfigurationen Zeit geben, einzutreffen
    if os.getenv("INSIGHTS_ONCE"):
        # Einmal rechnen und das Ergebnis ausgeben, ohne es zu veröffentlichen: so lässt sich der
        # Dienst von Hand gegen die echten Daten laufen, ohne die retained Nachricht anzufassen.
        print(json.dumps(build(CFG, collect(CFG)), ensure_ascii=False, indent=1))
        return
    # Zweite Sicherung neben der Bedingung in on_message: geweckt wird nur noch von Standort und Tarif,
    # und selbst wenn die in Folge umgeschrieben werden, läuft die Rechnung höchstens alle MIN_GAP Sekunden. Sie kostet einen vollen
    # Durchlauf über die Messreihen und ist nichts, was man mehrmals je Minute anstoßen darf.
    MIN_GAP = min(60, INTERVAL)
    letzte = 0.0
    while True:
        wartezeit = MIN_GAP - (time.time() - letzte)
        if wartezeit > 0:
            time.sleep(wartezeit)
        wake.clear()
        letzte = time.time()
        try:
            run_once(client)
        except Exception:
            LOG.exception("Erkenntnisse fehlgeschlagen")
        wake.wait(INTERVAL)


if __name__ == "__main__":
    main()
