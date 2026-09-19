#!/usr/bin/env python3
"""wolf-catalog: baut aus den Wolf-Originalressourcen den Parameterkatalog für die eigene Anlage.

Eingabe:
  wolf/parameter.json   – von scripts/wolf-config.sh erzeugt, listet die erkannten Busteilnehmer
                          (DeviceTemplateId + Busadressen) und je Teilnehmer die vorhandenen Parameter-IDs.
  Die vier Ressourcendateien aus zivillian/ism7mqtt (Kopien der Wolf-Smartset-Ressourcen):
    parameter.xml  – Beschreibung jedes Parameters: Name, Einheit, Min/Max/Schrittweite, Auswahlliste,
                     ControlType und ReadOnlyConditionId (== "False" heißt schreibbar)
    gui.xml        – der originale Menübaum der Smartset-App, inkl. IsExpertView (Fachmann-Ebene),
                     Reiter, Gruppe und Sortierung je Parameter
    dictionary.xml – Wolfs eigene Übersetzungstabelle, hier DEU -> GBR für die englische Oberfläche
  Die XMLs werden beim Lauf heruntergeladen (rund 12 MB) und nicht im Repo abgelegt.

Ausgabe:
  wolf/catalog.json – nur die Parameter, die diese Anlage wirklich hat, mit deutschen und englischen
                      Bezeichnungen, Einheiten, Grenzwerten, Auswahllisten und Menüzuordnung.
                      Wird vom Sidecar wolf_bridge.py und von der Bedienseite wolf.html gelesen.

Aufruf:  scripts/wolf-catalog.py [--parameter wolf/parameter.json] [--out wolf/catalog.json] [--ref master]
"""
import argparse, datetime, json, os, re, sys, unicodedata, urllib.request
import xml.etree.ElementTree as ET

RAW = "https://raw.githubusercontent.com/zivillian/ism7mqtt/{ref}/src/{path}"
FILES = {
    "parameter.xml":  "ism7mqtt/Resources/parameter.xml",
    "dictionary.xml": "ism7mqtt/Resources/dictionary.xml",
    "gui.xml":        "ism7config/Resources/gui.xml",
}

# Busteilnehmer, die in einer Wolf-Anlage vorkommen können. Rolle steuert Gruppierung in Grafana und auf der Seite.
DEVICE_ROLES = {
    270000: ("heatpump", "Wärmepumpe", "Heat pump"),
    220000: ("control",  "Bedienmodul BM-2", "Control module BM-2"),
    350000: ("dhw",      "Warmwasser", "Hot water"),
    360000: ("circuit",  "Heizkreis", "Heating circuit"),
    340000: ("circuit",  "Direkter Heizkreis", "Direct heating circuit"),
    370000: ("circuit",  "Heizkreis", "Heating circuit"),
    40000:  ("mixer",    "Mischermodul", "Mixer module"),
    190000: ("gateway",  "WOLF Link", "WOLF Link"),
}
# ControlType der Wolf-Ressourcen -> Eingabeart auf unserer Seite
CONTROL_TYPES = {
    "NumericInput": "numeric", "ComboBoxText": "list", "CheckBox": "bool", "TimeInput": "time",
    "DateInput": "date", "TextInput": "text", "IPInput": "text", "TimeProgram": "program",
    "DaySwitchTimes": "switchtimes", "ProgramSelectionListView": "program", "TimedFunction": "timed",
    "NO_DISPLAY": "hidden", "Client_NO_DISPLAY": "hidden",
}


def fetch(name, ref, cache):
    """Ressourcendatei holen, im Cache-Verzeichnis ablegen, damit ein zweiter Lauf offline geht."""
    path = os.path.join(cache, name)
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path
    url = RAW.format(ref=ref, path=FILES[name])
    print(f"  lade {name} …", file=sys.stderr)
    os.makedirs(cache, exist_ok=True)
    urllib.request.urlretrieve(url, path)
    return path


def load_dictionary(path):
    """DEU -> GBR aus Wolfs Übersetzungstabelle. Mehrfache deutsche Einträge: der erste gewinnt."""
    out = {}
    for _, el in ET.iterparse(path, events=("end",)):
        if el.tag != "TextTableEntry":
            continue
        de, en = el.findtext("DEU"), el.findtext("GBR")
        if de and en and de not in out:
            out[de] = en
        el.clear()
    return out


def load_parameters(path):
    """PTID -> Beschreibung aus parameter.xml."""
    out = {}
    for _, el in ET.iterparse(path, events=("end",)):
        ptid = el.get("PTID")
        if ptid is None:
            continue
        g = lambda tag: el.findtext(tag)
        out[int(ptid)] = {
            "name": g("Name"), "unit": g("UnitName"), "control": g("ControlType"),
            "min": g("MinValueCondition"), "max": g("MaxValueCondition"), "step": g("StepWidth"),
            "dec": g("Decimals"), "rw": g("ReadOnlyConditionId") == "False", "kv": g("KeyValueList"),
        }
        el.clear()
    return out


def load_menu(path, wanted):
    """(dtid, ptid) -> Menüzuordnung aus dem originalen Smartset-Menübaum.

    Ein Parameter kann in mehreren Ansichten auftauchen. Die Benutzer-Ebene gewinnt vor der
    Fachmann-Ebene, darunter entscheidet die Sortiernummer der Ansicht.
    """
    out = {}
    for _, vt in ET.iterparse(path, events=("end",)):
        if vt.tag != "ViewTemplate":
            continue
        menu = vt.find("MenuEntry")
        menu = menu.get("Name") if menu is not None else None
        expert = vt.findtext("IsExpertView") == "true"
        tab = clean_label(vt.findtext("TabName"))
        group = clean_label(vt.findtext("GroupName"))
        try:
            sort = int(vt.findtext("SortId") or 0)
        except ValueError:
            sort = 0
        for ref in vt.findall("ParameterReference"):
            key = (int(ref.get("DTID")), int(ref.get("ParameterDescriptorId")))
            if key not in wanted:
                continue
            entry = {"menu": "expert" if expert else "user", "tab": tab, "group": group, "sort": sort,
                     "rank": (1 if expert else 0, sort)}
            prev = out.get(key)
            if prev is None or entry["rank"] < prev["rank"]:
                out[key] = entry
        vt.clear()
    for entry in out.values():
        entry.pop("rank")
    return out


def clean_label(text):
    """Platzhalter der Smartset-Vorlagen entfernen: „Mischerkreis <#X#>" -> „Mischerkreis"."""
    if not text or text == "NULL":
        return None
    return re.sub(r"\s*<?#X#>?", "", text).strip() or None


def slug(name):
    """Stabiler ASCII-Feldname für InfluxDB: „Erzeugte Wärmemenge Vortag" -> erzeugte_waermemenge_vortag."""
    s = name.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = s.replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    return re.sub(r"_+", "_", s)


def options(kv, dic):
    """KeyValueList „0;Aus;1;Ein" -> Auswahlliste mit deutschem und englischem Text."""
    if not kv:
        return None
    parts = kv.split(";")
    out = []
    for i in range(1, len(parts), 2):
        value, text = parts[i - 1], parts[i]
        out.append({"v": value, "de": text, "en": dic.get(text, text)})
    return out or None


def number(text):
    if text in (None, "", "None"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--parameter", default=os.path.join(root, "wolf", "parameter.json"))
    ap.add_argument("--out", default=os.path.join(root, "wolf", "catalog.json"))
    ap.add_argument("--cache", default=os.path.join(root, "wolf", ".resources"))
    ap.add_argument("--ref", default="master", help="Git-Ref von zivillian/ism7mqtt")
    args = ap.parse_args()

    with open(args.parameter) as fh:
        config = json.load(fh)
    devices = config.get("Devices", [])
    if not devices:
        sys.exit("parameter.json enthält keine Geräte – erst scripts/wolf-config.sh laufen lassen.")

    print("Ressourcen:", file=sys.stderr)
    params = load_parameters(fetch("parameter.xml", args.ref, args.cache))
    dic = load_dictionary(fetch("dictionary.xml", args.ref, args.cache))
    wanted = {(d["DeviceTemplateId"], p) for d in devices for p in d["Parameter"]}
    menu = load_menu(fetch("gui.xml", args.ref, args.cache), wanted)
    print(f"  {len(params)} Parameterbeschreibungen, {len(dic)} Übersetzungen, "
          f"{len(menu)} Menüzuordnungen für {len(wanted)} eigene Parameter", file=sys.stderr)

    out_devices = []
    for dev in devices:
        dtid = dev["DeviceTemplateId"]
        role, label_de, label_en = DEVICE_ROLES.get(dtid, ("other", f"Gerät {dtid}", f"Device {dtid}"))
        ptids = [p for p in dev["Parameter"] if p in params]
        # Namen, die in diesem Gerät mehrfach vorkommen, müssen beim Schreiben die PTID mitgeben
        counts = {}
        for ptid in ptids:
            counts[params[ptid]["name"]] = counts.get(params[ptid]["name"], 0) + 1
        keys, entries = set(), []
        for ptid in sorted(ptids):
            desc = params[ptid]
            name = desc["name"]
            dup = counts[name] > 1
            key = slug(name)
            if dup:                                       # gleicher Name, mehrere Parameter: PTID anhängen
                key = f"{key}_{ptid}"
            while key in keys:                            # Kollision zweier verschiedener Namen
                key += "_x"
            keys.add(key)
            m = menu.get((dtid, ptid), {})
            entry = {
                "ptid": ptid, "key": key, "dup": dup,
                "de": name, "en": dic.get(name, name),
                "unit": desc["unit"], "unit_en": dic.get(desc["unit"], desc["unit"]) if desc["unit"] else None,
                "type": CONTROL_TYPES.get(desc["control"], "other"), "rw": desc["rw"],
                "min": number(desc["min"]), "max": number(desc["max"]),
                "step": number(desc["step"]), "dec": int(desc["dec"]) if desc["dec"] else None,
                "menu": m.get("menu", "other"), "tab": m.get("tab"), "group": m.get("group"),
                "sort": m.get("sort", 0),
            }
            opts = options(desc["kv"], dic)
            if opts:
                entry["options"] = opts
            entries.append(entry)
        out_devices.append({
            "dtid": dtid, "read": dev["ReadBusAddress"], "write": dev["WriteBusAddress"],
            "role": role, "de": label_de, "en": label_en,
            "params": entries,
        })

    catalog = {
        "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": f"zivillian/ism7mqtt@{args.ref}",
        "port": config.get("TcpPort", 9092),
        "devices": out_devices,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(catalog, fh, ensure_ascii=False, indent=1, sort_keys=False)
        fh.write("\n")

    total = sum(len(d["params"]) for d in out_devices)
    rw = sum(1 for d in out_devices for p in d["params"] if p["rw"])
    expert = sum(1 for d in out_devices for p in d["params"] if p["menu"] == "expert")
    print(f"\n{args.out}: {len(out_devices)} Geräte, {total} Parameter "
          f"({rw} schreibbar, {expert} Fachmann-Ebene)", file=sys.stderr)
    for d in out_devices:
        print(f"  {d['de']:<22} {d['read']:>5}  {len(d['params']):>3} Parameter", file=sys.stderr)


if __name__ == "__main__":
    main()
