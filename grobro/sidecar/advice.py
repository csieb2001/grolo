#!/usr/bin/env python3
"""advice: Veränderungsempfehlungen je String aus Standort, Ausrichtung und Messdaten.

0. Gemessene Horizontmaske, wenn eine vorliegt (grolo/config/site: horizon). Sie hat Vorrang vor allem,
   was sich aus Betriebsdaten erschließen lässt – siehe den Abschnitt "Vorrang der gemessenen Maske".
1. Verschattung zuerst: gemessene Stundenleistung der letzten Tage gegen das Modell mit echtem Wetter; Sonnenazimut-Bereiche
   (10°-Bins), in denen die Messung deutlich unter dem Modell bleibt, während es sonst passt, werden als Hindernis gemeldet,
   mit Uhrzeit (heute) und Verlustanteil. Daraus ein Verschattungsprofil: je Sonnenazimut, wieviel Direktstrahl durchkommt.
2. Jahresmodell: ein Jahr Stundenwerte GHI/DNI/DHI aus dem Open-Meteo-Archiv (ERA5, kostenlos) für den Standort, daraus
   die Einstrahlung auf jede Ausrichtung (Azimut 10°, Neigung 10°, um das Optimum auf 5° verfeinert) in kWh/kWp und Jahr –
   mit dem Verschattungsprofil im Direktstrahl. Verglichen wird die aktuelle Ausrichtung (konfiguriert, sonst geschätzt)
   mit dem Optimum und praktischen Alternativen: gleicher Azimut mit bester Neigung, senkrecht (Balkon) mit bestem Azimut,
   flach. Dazu die Winter-/Sommerverteilung.

Die Reihenfolge ist der Punkt: ein Optimum allein aus der Einstrahlung kann genau in ein Hindernis zeigen. Solange keine
Verschattung gemessen ist, wird deshalb gar kein Optimum genannt (site_best = null, Begründung unter "optimum", die
unbereinigte Rechnung nur als site_best_unshaded für das Erwartungsmodell). Siehe den Kommentar über shaded_optimum().

Ergebnis (retained <BASE>/grolo/advice, Website site.advice):
  {"updated", "period": ["2025-09-01", "2026-08-31"], "free_inputs": 3, "shading_known": true | false,
   "horizon": {"points", "az_from", "az_to", "max_el", "svf", "source", "updated"} | null,
   "site_best": {"tilt", "azimuth", "kwh_kwp", "shading_corrected"} | null,
   "site_best_unshaded": {...}  (nur wenn site_best null ist),
   "optimum": {"status": "shading_corrected" | "unknown_shading", "de", "en"},
   "strings": {"1": {"basis": "config" | "fit" | null, "tilt", "azimuth", "kwh_kwp", "pct_of_best", "shading_corrected",
                     "best": {"tilt", "azimuth", "kwh_kwp", "gain_pct"}, "same_azimuth": {"tilt", "gain_pct"},
                     "vertical": {"azimuth", "kwh_kwp", "gain_pct"}, "flat": {"kwh_kwp", "gain_pct"},
                     "winter_share_pct", "best_winter_share_pct",
                     "shading": [{"az_from", "az_to", "ratio", "hours", "time_from", "time_to"}], "shading_loss_pct", "ratio_median"}}}
  Ohne gemessene Verschattung sind pct_of_best, best, same_azimuth, vertical, flat und best_winter_share_pct null:
  beschreibende Werte bleiben, Empfehlungen nicht.
"""
import datetime, json, math, os, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from solar import sun_position, poa_irradiance, horizon_profile, horizon_filter  # noqa: E402

_ARCHIVE = {}   # (lat, lon, monat) -> Stundenliste [(ts, ghi, dni, dhi, az, el)]


def annual_hours(lat, lon):
    """Ein volles Jahr bis zum letzten Monatsende, nur Stunden mit Sonne; Cache je Monat."""
    today = datetime.date.today()
    end = today.replace(day=1) - datetime.timedelta(days=1)
    start = datetime.date(end.year - 1 if end.month != 12 else end.year, end.month % 12 + 1, 1)   # genau 12 Monate
    key = (round(lat, 2), round(lon, 2), end.isoformat())
    if key in _ARCHIVE:
        return _ARCHIVE[key], start, end
    url = (f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}&start_date={start}&end_date={end}"
           "&hourly=shortwave_radiation,direct_normal_irradiance,diffuse_radiation&timezone=UTC&timeformat=unixtime")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "GroLo advice"}), timeout=60) as r:
        d = json.load(r)["hourly"]
    hours = []
    for i, ts in enumerate(d["time"]):
        ghi, dni, dhi = d["shortwave_radiation"][i], d["direct_normal_irradiance"][i], d["diffuse_radiation"][i]
        if not ghi or ghi < 5 or dni is None or dhi is None:
            continue
        az, el = sun_position(int(ts) - 1800, lat, lon)
        if el <= 0:
            continue
        hours.append((int(ts), ghi, dni, dhi, az, el))
    _ARCHIVE.clear(); _ARCHIVE[key] = hours
    return hours, start, end


# ---------------------------------------------------------------------------------------------------------
# Vorrang der gemessenen Maske
#
# Eine vermessene Horizontmaske (Oberflächenmodell des Landesvermessungsamts, Neigungsmesser vom Dach aus)
# schlägt jede aus Betriebsdaten erschlossene Verschattung. Der Sektorvergleich unten misst RELATIV gegen
# den Median der eigenen Sektoren; ein Schatten, der den halben Himmel einnimmt, wird selbst zum Median und
# verschwindet aus der eigenen Messung (siehe BREITER_EINBRUCH in build_advice). Die Maske hat dieses
# Problem nicht – sie weiß, wo der Baum steht, ohne ihn aus Erträgen erraten zu müssen.
#
# Liegt eine vor, gilt deshalb:
#   * Sie ersetzt das erschlossene Profil vollständig (mask = None). Beides zu mischen hieße, denselben
#     Schatten zweimal abzuziehen – einmal als Höhenwinkel, einmal als Durchlassfaktor.
#   * Die Verschattung gilt als bekannt, und das Optimum wird wieder gerechnet. Genau dafür war
#     shaded_optimum() gebaut.
#   * Auch der Soll-Ist-Vergleich in shading() rechnet dann hinter dem Horizont. Das ist die ehrliche
#     Gegenprobe: erklärt die Maske den breiten Einbruch, steigt ratio_median in die Nähe von 1.
#
# Die Maske gilt für den Zustand, in dem sie gemessen wurde. Steht sie aus dem belaubten Sommer, ist das
# Modell im Winter zu pessimistisch – um wieviel, steht nirgends, und es wird hier auch nicht geraten.
# ---------------------------------------------------------------------------------------------------------
def horizon_hours(hours, profile):
    """Die Jahresstunden hinter dem gemessenen Horizont; ohne Maske unverändert (dieselbe Liste)."""
    if not profile:
        return hours
    out = []
    for ts, g, dn, df, az, el in hours:
        g2, dn2, df2 = horizon_filter(profile, g, dn, df, az, el)
        out.append((ts, g2, dn2, df2, az, el))
    return out


def beam_factor(mask, sun_az):
    """Wieviel Direktstrahl aus dieser Sonnenrichtung durchkommt; 1,0 heißt frei."""
    return mask.get(int(sun_az // 10) * 10, 1.0) if mask else 1.0


def yearly_kwh_kwp(hours, tilt, azimuth, pr=0.85, mask=None):
    return sum(poa_irradiance(g, dn * beam_factor(mask, az), df, az, el, tilt, azimuth)
               for _, g, dn, df, az, el in hours) / 1000.0 * pr


def winter_share(hours, tilt, azimuth, mask=None):
    """Anteil des Jahresertrags im Winterhalbjahr (Oktober bis März)."""
    w = t = 0.0
    for ts, g, dn, df, az, el in hours:
        v = poa_irradiance(g, dn * beam_factor(mask, az), df, az, el, tilt, azimuth); t += v
        if datetime.datetime.utcfromtimestamp(ts).month in (10, 11, 12, 1, 2, 3):
            w += v
    return 100.0 * w / t if t else 0.0


def shading_mask(entries):
    """Verschattungsprofil aus den gemeldeten Zonen aller Strings: Sonnenazimut (10°-Bin) -> Durchlass.

    Ein Hindernis steht dort, wo es steht, unabhängig davon, wie die Module gedreht sind – deshalb ist das
    Profil eine Eigenschaft des Standorts und wird über alle Strings zusammengezogen (je Bin der schlechteste
    gemessene Wert). Gedämpft wird nur der Direktstrahl: Diffus- und Bodenanteil kommen auch am Baum vorbei.
    """
    mask = {}
    for e in entries:
        for z in e.get("shading") or []:
            try:
                a, b, r = int(z["az_from"]), int(z["az_to"]), float(z["ratio"])
            except (TypeError, ValueError, KeyError):
                continue
            for deg in range(a - a % 10, b, 10):
                mask[deg % 360] = min(mask.get(deg % 360, 1.0), max(0.0, min(1.0, r)))
    return mask


# ---------------------------------------------------------------------------------------------------------
# Warum das Optimum die Verschattung kennen muss – und warum es ohne sie gar nicht genannt wird.
#
# Rein nach Einstrahlung kommt für diesen Standort 180° Azimut und 35° Neigung heraus. Südlich des Hauses
# steht aber eine große Eiche, genau in Richtung Süd, die erheblich verschattet (Angabe des Betreibers; der
# PV-Eichfaktor der Prognose liegt passend dazu bei 0,45, das Modell erwartet also gut doppelt so viel wie
# ankommt). Die Empfehlung „nach Süden drehen" zeigt hier direkt in den Schatten und würde den Ertrag
# verschlechtern. Deshalb:
#
#   * Liegt ein gemessenes Verschattungsprofil vor, wird es ins Modell hineingenommen und die Optimierung
#     damit neu gerechnet. Das ist das etablierte Verfahren (PVsyst dokumentiert ausdrücklich, dass sich das
#     Optimum dann im Azimut verschiebt). Dass Verschattungsverluste mit der Neigung wachsen, ergibt sich
#     hier von selbst: eine steile, zum Hindernis gedrehte Fläche verliert mehr Direktstrahl als eine flache,
#     und bei einem nahen hohen Hindernis kann flacher netto besser sein.
#   * Liegt keines vor, wird KEIN Optimum ausgegeben, sondern ein Satz, der sagt, was fehlt. Eine falsche
#     Empfehlung ist schlechter als keine.
#
# Was hier ausdrücklich NICHT behauptet wird: eine Regel „Süden verschattet, also Ost-West". Die ist nicht
# belegt und steht nirgends im Code. Wohin das Optimum wandert, sagt allein die Rechnung mit dem gemessenen
# Profil – bei einem Süd-Hindernis fällt vor allem die Neigung, der Azimut wandert nur wenig.
#
# Wer das zurückbaut, weil „das Optimum doch einfach die beste Einstrahlung ist": es ist die beste
# Einstrahlung an einer Stelle, an der ein Baum steht.
# ---------------------------------------------------------------------------------------------------------
def shaded_optimum(hours, pr, mask=None):
    best = None
    for azimuth in range(0, 360, 10):
        for tilt in range(0, 91, 10):
            v = yearly_kwh_kwp(hours, tilt, azimuth, pr, mask)
            if best is None or v > best[0]:
                best = (v, tilt, azimuth)
    v0, t0, a0 = best
    for azimuth in range(a0 - 10, a0 + 11, 5):
        for tilt in range(max(0, t0 - 10), min(90, t0 + 10) + 1, 5):
            v = yearly_kwh_kwp(hours, tilt, azimuth % 360, pr, mask)
            if v > best[0]:
                best = (v, tilt, azimuth % 360)
    return {"kwh_kwp": round(best[0]), "tilt": best[1], "azimuth": best[2]}


def site_optimum(hours, pr):
    """Optimum ohne jede Verschattung – nur noch als Notausrichtung für das Erwartungsmodell, nicht als Rat."""
    return shaded_optimum(hours, pr, None)


UNSHADED_DE = ("Für diesen Standort ist noch keine Verschattung gemessen, deshalb steht hier keine Empfehlung zur "
               "Ausrichtung. Ein Optimum allein aus der Einstrahlung kann genau in ein Hindernis zeigen und den "
               "Ertrag verschlechtern – südlich des Hauses steht eine große Eiche. Die Verschattungsanalyse braucht "
               "je String Neigung, Azimut und Wp auf der Einstellungsseite und danach einige sonnige Tage; sobald "
               "sie läuft, wird das Optimum mit dem gemessenen Verschattungsprofil gerechnet.")
UNSHADED_EN = ("No shading has been measured for this site yet, so there is no orientation advice here. An optimum "
               "derived from irradiance alone can point straight at an obstacle and make the yield worse – there is "
               "a large oak to the south of the house. The shading analysis needs tilt, azimuth and Wp per string on "
               "the settings page and then a few sunny days; once it runs, the optimum is computed with the measured "
               "shading profile.")


def hhmm_today(lat, lon, target_az, tz_offset):
    """Lokale Uhrzeit (heute), zu der die Sonne den Azimut erreicht; None, wenn nicht über dem Horizont."""
    day = int(time.time()) // 86400 * 86400 - tz_offset
    prev = None
    for m in range(0, 1440, 5):
        ts = day + m * 60
        az, el = sun_position(ts, lat, lon)
        if prev is not None and el > 0 and prev <= target_az < az:
            lt = time.localtime(ts)
            return "%02d:%02d" % (lt.tm_hour, lt.tm_min)
        prev = az
    return None


def shading(power, wx, lat, lon, tilt, azimuth, wp_eff, horizon=None):
    """Messung/Modell je 10°-Sonnenazimut. Liefert (Liste auffälliger Bereiche, Verlust in %, Median-Verhältnis).

    Mit gemessener Horizontmaske rechnet das Modell hinter dem Horizont: dann misst dieser Vergleich nicht
    mehr gegen einen freien Himmel, sondern gegen den, den die Module wirklich sehen.
    """
    bins = {}
    tot_exp = tot_meas = 0.0
    for ts, p in power.items():
        w = wx.get(ts)
        if not w or w[1] < 150:
            continue
        az, el = sun_position(ts - 1800, lat, lon)
        if el < 5:
            continue
        exp = poa_irradiance(w[0], w[1], w[2], az, el, tilt, azimuth, horizon=horizon) / 1000.0 * wp_eff
        if exp < 15:
            continue
        b = int(az // 10) * 10
        e, m, n = bins.get(b, (0.0, 0.0, 0)); bins[b] = (e + exp, m + p, n + 1)
        tot_exp += exp; tot_meas += p
    if len(bins) < 3:
        return [], 0.0, None
    ratios = sorted(m / e for e, m, n in bins.values() if e > 0)
    # Bezugsgröße bleibt bewusst der Median, nicht das obere Ende. Am 27.09.2026 auf das 90. Perzentil
    # umgestellt und wieder zurückgenommen: der Median findet nur SCHMALE Hindernisse (ein breiter Schatten
    # zieht ihn mit sich und vergleicht sich am Ende mit sich selbst), das obere Ende dagegen meldete
    # 14 von 17 Sektoren als verschattet, also den halben Himmel. Mit drei Wochen Herbstdaten lässt sich
    # Verschattung nicht von einem allgemein zu optimistischen Modell trennen – Temperatur, Verschmutzung,
    # MPPT-Verluste und eine steile Neigung im September drücken dasselbe Verhältnis. Deshalb hier die
    # vorsichtige Variante, und der breite Einbruch wird stattdessen über ratio_median gemeldet (siehe
    # BREITER_EINBRUCH unten): eine Zahl, die niemand als Sektor ausgibt, den es so nicht gibt.
    ref = ratios[len(ratios) // 2]
    tz_offset = -time.timezone if not time.localtime().tm_isdst else -time.altzone
    flagged = []; loss = 0.0
    for b in sorted(bins):
        e, m, n = bins[b]; r = m / e if e else 0.0
        if ref > 0.3 and r < 0.6 * ref and n >= 2:
            flagged.append({"az_from": b, "az_to": b + 10, "ratio": round(r / ref, 2), "hours": n, "time_from": hhmm_today(lat, lon, b, tz_offset), "time_to": hhmm_today(lat, lon, b + 10, tz_offset)})
            loss += (ref * e - m)
    merged = []
    for f in flagged:
        if merged and merged[-1]["az_to"] == f["az_from"]:
            last = merged[-1]; last["az_to"] = f["az_to"]; last["time_to"] = f["time_to"]; last["hours"] += f["hours"]; last["ratio"] = round(min(last["ratio"], f["ratio"]), 2)
        else:
            merged.append(dict(f))
    return merged, (100.0 * loss / (ref * tot_exp) if tot_exp and ref else 0.0), round(ref, 2)


def build_advice(lat, lon, strings_cfg, fit, power, wx, pr=0.85, horizon=None, horizon_source=None,
                 horizon_updated=None):
    raw_hours, start, end = annual_hours(lat, lon)
    profile = horizon_profile(horizon)
    hours = horizon_hours(raw_hours, profile)
    out = {"updated": int(time.time()), "period": [start.isoformat(), end.isoformat()], "strings": {}, "free_inputs": 0}
    out["horizon"] = None if not profile else {
        "points": [[a, h] for a, h in profile["points"]], "az_from": profile["az_from"], "az_to": profile["az_to"],
        "max_el": profile["max_el"], "svf": profile["svf"],
        "source": horizon_source or "", "updated": horizon_updated}

    # ---------------------------------------------------------------- Durchgang 1: Ausrichtung und Verschattung
    # Erst messen, dann optimieren. Das Optimum hängt vom Verschattungsprofil ab, also muss das Profil
    # stehen, bevor gerechnet wird – die frühere Reihenfolge (Optimum zuerst) war genau der Fehler.
    todo = {}
    for s_ in ("1", "2", "3", "4"):
        p = power.get(s_, {}); pk = max(p.values()) if p else 0.0
        f = (fit or {}).get("strings", {}).get(s_, {}) if fit else {}
        cfg = (strings_cfg or {}).get(int(s_)) or (strings_cfg or {}).get(s_)
        if pk < 20 and not cfg:
            out["free_inputs"] += 1; out["strings"][s_] = {"basis": None, "unused": True}; continue
        if cfg and cfg.get("tilt") is not None:
            basis, tilt, azimuth = "config", float(cfg["tilt"]), float(cfg["azimuth"])
            wp_eff = float(cfg.get("wp") or 0) * pr or float(f.get("wp_eff") or 0)
        elif f.get("status") in ("ok", "uncertain"):
            basis, tilt, azimuth, wp_eff = "fit", float(f["tilt"]), float(f["azimuth"]), float(f["wp_eff"])
        else:
            out["strings"][s_] = {"basis": None, "unused": False, "peak_w": round(pk, 1)}; continue
        entry = {"basis": basis, "tilt": tilt, "azimuth": azimuth, "peak_w": round(pk, 1), "wp_eff": round(wp_eff)}
        if wp_eff > 0 and p:
            sh, loss, med = shading(p, wx, lat, lon, tilt, azimuth, wp_eff, profile)
            entry.update({"shading": sh, "shading_loss_pct": round(loss, 1), "ratio_median": med})
        out["strings"][s_] = entry
        todo[s_] = entry

    # ---------------------------------------------------------------- Das Verschattungsprofil des Standorts
    # "gemessen" ist nur wahr, wenn die Analyse für mindestens einen String überhaupt gelaufen ist. Ein
    # leeres Profil heißt dann "hingeschaut, nichts gefunden" – etwas völlig anderes als "nie hingeschaut".
    measured = [e for e in todo.values() if "shading_loss_pct" in e]
    # Mit gemessener Maske steckt die Verschattung schon in den Jahresstunden. Das erschlossene Profil
    # käme sonst obendrauf und zöge denselben Baum ein zweites Mal ab.
    mask = {} if profile else shading_mask(measured)
    out["shading_known"] = bool(profile or measured)

    # ---------------------------------------------------------------- Breiter Einbruch: BREITER_EINBRUCH
    # Der Sektorvergleich misst RELATIV – er findet, was aus der eigenen Umgebung herausfällt. Liegt die
    # ganze Fläche weit unter dem Einstrahlungsmodell, findet er nichts, weil es nichts gibt, wogegen der
    # Einbruch abstäche. Genau das war hier am 27.09.2026 der Fall: Verhältnis im Median 0,21, also ein
    # Fünftel des Modells, und trotzdem null gemeldete Sektoren. Daraus dann "keine Verschattung gefunden,
    # das Optimum steht frei" zu machen und nach Süden zu zeigen, wo nachweislich eine große Eiche steht,
    # ist schlimmer als gar keine Aussage – es ist eine falsche mit Prüfsiegel.
    # Also: Ein Median deutlich unter dem Plausiblen heißt "hier fehlt viel, und dieses Verfahren kann nicht
    # sagen woher". Kein Optimum, sondern die Zahl und der Satz dazu. Die Grenze 0,6 ist bewusst großzügig:
    # Temperatur, Verschmutzung, MPPT-Verluste und eine steile Neigung im Herbst kosten zusammen gut ein
    # Viertel, darunter wird es zur Verschattungsfrage.
    mediane = [e["ratio_median"] for e in measured if e.get("ratio_median")]
    breiter_einbruch = bool(mediane) and max(mediane) < 0.6
    if breiter_einbruch:
        out["broad_deficit"] = {"ratio": round(max(mediane), 2), "of": "poa_model"}

    # ---------------------------------------------------------------- Das Optimum – oder ausdrücklich keines
    if profile:
        # Die Maske ist gemessen, also ist die Verschattung bekannt, also darf gerechnet werden – auch dann,
        # wenn der Sektorvergleich weiter einen breiten Einbruch sieht. Der breite Einbruch war der Befund
        # "hier fehlt viel, und dieses Verfahren kann nicht sagen woher"; die Maske sagt es.
        best_site = dict(shaded_optimum(hours, pr, None), shading_corrected=True, horizon_corrected=True)
        out["site_best"] = best_site
        src = f" Quelle: {horizon_source}." if horizon_source else ""
        src_en = f" Source: {horizon_source}." if horizon_source else ""
        out["optimum"] = {
            "status": "horizon_measured", "points": len(profile["points"]),
            "de": (f"Das Optimum ist mit der vermessenen Horizontmaske gerechnet: {len(profile['points'])} "
                   f"Stützstellen zwischen {profile['az_from']:.0f}° und {profile['az_to']:.0f}° Azimut, bis "
                   f"{profile['max_el']:.0f}° hoch; sichtbarer Himmel {round(profile['svf'] * 100)} %. Unter dem "
                   "Horizont fällt der Direktstrahl ganz weg, Diffus- und Bodenanteil sind um den fehlenden Himmel "
                   f"gedämpft.{src} Die Maske gilt für den Zustand, in dem sie aufgenommen wurde – ist das der "
                   "belaubte Sommer, rechnet das Modell den Winter zu dunkel."),
                "en": (f"The optimum is computed with the surveyed horizon mask: {len(profile['points'])} support "
                   f"points between {profile['az_from']:.0f}° and {profile['az_to']:.0f}° azimuth, up to "
                   f"{profile['max_el']:.0f}° high; visible sky {round(profile['svf'] * 100)} %. Below the horizon "
                   "the direct beam is gone entirely, diffuse and ground-reflected light are damped by the missing "
                   f"sky.{src_en} The mask describes the state it was recorded in – if that is summer with the "
                   "leaves on, the model paints winter too dark.")}
    elif measured and not (breiter_einbruch and not mask):
        best_site = dict(shaded_optimum(hours, pr, mask), shading_corrected=True, horizon_corrected=False,
                         shaded_azimuths=sorted(mask))
        out["site_best"] = best_site
        out["optimum"] = {
            "status": "shading_corrected", "zones": len(mask),
            "de": (f"Das Optimum ist mit dem gemessenen Verschattungsprofil gerechnet ({len(mask)} verschattete "
                   "Azimutbereiche à 10°)." if mask else
                   "Gemessen, aber keine Verschattung gefunden – das Optimum steht frei."),
            "en": (f"The optimum is computed with the measured shading profile ({len(mask)} shaded 10° azimuth "
                   "bands)." if mask else
                   "Measured, but no shading found – the optimum is unobstructed.")}
    else:
        # Die unbereinigte Rechnung bleibt erhalten, aber unter einem Namen, der sagt, was sie ist: weather.py
        # braucht irgendeine Fläche, um einen String mit messbarer Leistung überhaupt modellieren zu können.
        # Als Rat an den Betreiber ist sie gesperrt.
        best_site = None
        out["site_best"] = None
        out["site_best_unshaded"] = dict(site_optimum(hours, pr), shading_corrected=False)
        if breiter_einbruch:
            q = round(max(mediane) * 100)
            out["optimum"] = {"status": "broad_deficit",
                "de": (f"Die Anlage liefert im Mittel nur {q} % dessen, was die Einstrahlung hergäbe – über alle "
                       "Himmelsrichtungen hinweg, nicht in einzelnen Sektoren. Ein so breiter Einbruch lässt sich "
                       "mit dieser Messung keiner Richtung zuordnen, deshalb steht hier keine Empfehlung zur "
                       "Ausrichtung. Ein Optimum allein aus der Einstrahlung wäre hier irreführend."),
                "en": (f"The array delivers only {q} % of what the irradiance would allow – across all directions, "
                       "not in single sectors. A deficit that broad cannot be attributed to a direction from this "
                       "measurement, so no orientation advice is given here. An optimum from irradiance alone "
                       "would be misleading.")}
        else:
            out["optimum"] = {"status": "unknown_shading", "de": UNSHADED_DE, "en": UNSHADED_EN}

    # ---------------------------------------------------------------- Durchgang 2: Ertrag und Alternativen
    for s_, entry in todo.items():
        tilt, azimuth = entry["tilt"], entry["azimuth"]
        cur = yearly_kwh_kwp(hours, tilt, azimuth, pr, mask)
        entry["kwh_kwp"] = round(cur)
        entry["winter_share_pct"] = round(winter_share(hours, tilt, azimuth, mask))
        # Mit der vermessenen Maske steckt die Verschattung in den Jahresstunden, nicht in mask – sonst
        # stünde hier "unbereinigt" an einer Zahl, die gerade eben bereinigt wurde.
        entry["shading_corrected"] = bool(mask or profile)
        entry["horizon_corrected"] = bool(profile)
        # Die Bedingung ist das Fehlen des Optimums, nicht das Fehlen der Messung: seit der Regel für den
        # breiten Einbruch kann beides zugleich gelten – gemessen, aber bewusst ohne Optimum. Stand hier
        # "if not measured", lief der Vergleich unten in best_site["kwh_kwp"] gegen None.
        if best_site is None:
            # Beschreibungen der aktuellen Ausrichtung bleiben stehen; jeder Vergleich mit einem Optimum,
            # das die Verschattung nicht kennt, fällt weg. Eine falsche Empfehlung ist schlechter als keine.
            entry.update({"pct_of_best": None, "best": None, "same_azimuth": None, "vertical": None,
                          "flat": None, "best_winter_share_pct": None})
            continue
        same_az = max(((yearly_kwh_kwp(hours, t, azimuth, pr, mask), t) for t in range(0, 91, 5)))
        vert = max(((yearly_kwh_kwp(hours, 90, a, pr, mask), a) for a in range(0, 360, 10)))
        flat = yearly_kwh_kwp(hours, 0, 180, pr, mask)
        gain = lambda v: round(100.0 * (v - cur) / cur, 1) if cur else None
        entry.update({
            "pct_of_best": round(100.0 * cur / best_site["kwh_kwp"]) if best_site["kwh_kwp"] else None,
            "best": {**best_site, "gain_pct": gain(best_site["kwh_kwp"])},
            "same_azimuth": {"tilt": same_az[1], "kwh_kwp": round(same_az[0]), "gain_pct": gain(same_az[0])},
            "vertical": {"azimuth": vert[1], "kwh_kwp": round(vert[0]), "gain_pct": gain(vert[0])},
            "flat": {"kwh_kwp": round(flat), "gain_pct": gain(flat)},
            "best_winter_share_pct": round(winter_share(hours, best_site["tilt"], best_site["azimuth"], mask))})
    return out
