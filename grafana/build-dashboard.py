#!/usr/bin/env python3
"""GroLo: erzeugt die Grafana-Dashboards für den Growatt NEXA 2000 in Englisch und Deutsch.

    python3 grafana/build-dashboard.py

Ausgabe: grafana/dashboards/nexa-en.json (uid nexa2000, Startseite) und grafana/dashboards/nexa-de.json (uid nexa2000-de).
Grafana lädt die Dateien per Provisioning automatisch nach (alle 30 s). Die Dashboards verlinken sich gegenseitig.
"""
import json
import os

DS = {"type": "influxdb", "uid": "influx-nexa"}
BUCKET = os.environ.get("INFLUX_BUCKET", "nexa")
TZ = os.environ.get("TZ", "Europe/Berlin")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboards")

# ----------------------------------------------------------------- Übersetzung: Deutsch ist der Schlüssel
EN = {
    # Jahresprognose
    "Unsicherheit der Prognose": "Forecast uncertainty",
    "Rechnung, untere Kante": "Bill, lower edge",
    "Rechnung, obere Kante": "Bill, upper edge",
    "Heizstab im Jahr": "Immersion heater per year",
    "PV-Eichfaktor": "PV calibration factor",
    "Wie weit die Rechnung danebenliegen kann. Jede Annahme bringt eine eigene Streuung mit – Wärmebedarf, COP, Haushalt, PV, Wetterjahr –, und sie werden quadratisch addiert, weil sie unabhängig voneinander danebenliegen. Je mehr Angaben eingetragen und je mehr Wochen gemessen sind, desto schmaler wird das Band.": "How far the calculation can be off. Every assumption brings its own spread – heat demand, COP, household, PV, weather year – and they are added in quadrature because they can be wrong independently of each other. The more entries made and the more weeks measured, the narrower the band gets.",
    "Wer keine Nachzahlung riskieren will, legt den Abschlag auf diese Kante geteilt durch zwölf.": "If you would rather not risk a bill at the end, set the monthly payment to this edge divided by twelve.",
    "Wärme, die der Verdichter an den kältesten Stunden nicht mehr schafft und die der Heizstab elektrisch nachlegt – mit Arbeitszahl 1, also zum vollen Strompreis.": "Heat the compressor can no longer deliver in the coldest hours and the immersion heater adds electrically – at a performance factor of 1, so at the full electricity price.",
    "Gemessener Ertrag geteilt durch den, den das Modell für dieselben Sonnenstunden erwartet hätte. Unter 1 heißt: Verschattung, Schmutz oder eine zu optimistische Annahme zur Ausrichtung. Ein Wert weit unter 1 ist ein Grund, Neigung und Azimut nachzutragen.": "Measured yield divided by what the model would have expected for the same sunny hours. Below 1 means shading, dirt or too optimistic an assumption about the orientation. A value far below 1 is a reason to enter tilt and azimuth.",
    "Monat": "Month",
    "Jan": "Jan",
    "Feb": "Feb",
    "Mär": "Mar",
    "Apr": "Apr",
    "Mai": "May",
    "Jun": "Jun",
    "Jul": "Jul",
    "Aug": "Aug",
    "Sep": "Sep",
    "Okt": "Oct",
    "Nov": "Nov",
    "Dez": "Dec",
    "Jahresprognose": "Annual forecast",
    "Verbrauch im Jahr": "Consumption per year",
    "davon Wärmepumpe": "of it heat pump",
    "PV-Ertrag": "PV yield",
    "Netzbezug im Jahr": "Grid import per year",
    "Autarkie": "Self-sufficiency",
    "Güte der Prognose": "Forecast quality",
    "Jahresrechnung": "Annual bill",
    "davon Grundpreis": "of it base fee",
    "Ersparnis durch die Anlage": "Saved by the system",
    "Empfohlener Abschlag": "Recommended monthly payment",
    "Aktueller Abschlag": "Current monthly payment",
    "Verbrauch je Monat": "Consumption per month",
    "Netzbezug und Kosten je Monat": "Grid import and cost per month",
    "aus Messwerten": "from measurements",
    "teils geschätzt": "partly estimated",
    "angenommen": "assumed",
    "Haushalt plus Wärmepumpe, modelliert über ein volles Jahr mit dem echten Wetter des Standorts. Grundlage sind deine Angaben auf der Einstellungsseite und alles, was bis jetzt gemessen wurde.": "Household plus heat pump, modelled over a full year with the real weather of your location. It builds on your entries on the settings page and on everything measured so far.",
    "Wärmebedarf des Hauses, verteilt nach Heizgradstunden, geteilt durch den COP der jeweiligen Stunde.": "The building's heat demand, spread by heating degree hours, divided by the COP of each hour.",
    "Aus Neigung, Azimut und Wp jedes Strings und der Einstrahlung des Wetterjahres. Leer, solange keine Modulleistung eingetragen ist.": "From tilt, azimuth and Wp of each string and the irradiance of the weather year. Empty as long as no module power is configured.",
    "Was nach Direktverbrauch und Batterie übrig bleibt. Die Abgabegrenze von 800 W begrenzt, wie viel der NEXA überhaupt beisteuern kann.": "What is left after direct use and the battery. The 800 W output limit caps how much the NEXA can contribute at all.",
    "Anteil des Jahresverbrauchs, den die eigene Anlage deckt.": "Share of the annual consumption covered by your own system.",
    "„aus Messwerten“ heißt: Wärmebedarf, Haushalt, Module und COP-Kennlinie stehen alle auf eigenen Zahlen. „angenommen“ heißt, es fehlen noch Angaben auf der Einstellungsseite.": "“From measurements” means heat demand, household, modules and the COP curve all rest on your own figures. “Assumed” means entries are still missing on the settings page.",
    "Netzbezug × Arbeitspreis plus Grundpreis × 12, abzüglich Einspeisevergütung.": "Grid import × unit price plus base fee × 12, less any feed-in payment.",
    "Differenz zu einer Jahresrechnung ohne PV und Batterie, bei gleichem Verbrauch.": "Difference from an annual bill without PV and battery, at the same consumption.",
    "Jahresrechnung geteilt durch zwölf, auf die nächsten fünf Euro aufgerundet. Zu niedrig heißt Nachzahlung, zu hoch heißt, dem Versorger zinslos Geld zu leihen.": "The annual bill divided by twelve, rounded up to the next five euros. Too low means paying on top later, too high means lending the supplier money interest-free.",
    "Was du laut Einstellungsseite heute zahlst. 0 = noch nicht eingetragen.": "What you pay today according to the settings page. 0 = not entered yet.",
    "Das modellierte Jahr, aufgeteilt auf die Monate. Der Winter trägt fast den ganzen Wärmepumpenanteil, die PV steht ihm genau gegenläufig.": "The modelled year split across the months. Winter carries almost the whole heat pump share, and the PV runs exactly counter to it.",
    "Woher der Abschlag kommt: die Monate sind sehr ungleich, der Abschlag glättet sie auf zwölf gleiche Raten.": "Where the monthly payment comes from: the months are very uneven, and the payment smooths them into twelve equal instalments.",
    "Haushalt": "Household",
    "Kosten": "Cost",
    "Jetzt": "Now", "PV-Leistung": "PV power", "Aktuelle Leistung aller PV-Strings": "Current power of all PV strings",
    "Ausgang ins Haus": "Output to house", "AC-Ausgangsleistung des NEXA": "AC output power of the NEXA",
    "Batterie-Leistung": "Battery power", "Lade-/Entladeleistung der Batterie": "Battery charge/discharge power",
    "Ladezustand": "State of charge", "Hausverbrauch": "Household load", "Nur mit Smart Meter / GroPlug befüllt, sonst 0": "Only filled with a smart meter / GroPlug, otherwise 0",
    "Nulleinspeisung (Shelly)": "Zero feed-in (Shelly)", "Netzbezug": "Grid draw", "Netzleistung am Zähler: positiv = Bezug, negativ = Einspeisung": "Grid power at the meter: positive = draw, negative = feed-in",
    "Hausverbrauch (Shelly)": "Household (Shelly)", "Gemessener Hausverbrauch aus dem Shelly (Netz + NEXA-Ausgang)": "Measured household load from the Shelly (grid + NEXA output)", "Gemessen aus dem Shelly (Netz + NEXA-Ausgang). Nur mit konfiguriertem Shelly.": "Measured from the Shelly (grid + NEXA output). Only with a configured Shelly.",
    "Zielleistung": "Target output", "Vom Regler angeforderte NEXA-Ausgangsleistung": "NEXA output power requested by the controller",
    "Verlauf Nulleinspeisung": "Zero feed-in history", "Netz": "Grid", "Haushalt": "Household", "Ausgabe": "Output",
    "Netz und Haushalt, sobald ein Shelly konfiguriert ist; Ausgabe und Ziel nur bei laufender Regelung": "Grid and household as soon as a Shelly is configured; output and target only while the control runs",
    "Regler-Status": "Controller status", "regelt": "regulating", "Shelly nicht erreichbar": "Shelly unreachable", "NEXA offline": "NEXA offline", "Modus nicht Last zuerst": "Mode not Load first",
    "Batterie an Entladegrenze": "Battery at discharge limit", "NEXA liefert weniger als Ziel": "NEXA delivers less than target", "aus": "off",
    "Zustand des Shelly-Reglers. „Batterie an Entladegrenze“: der NEXA liefert weniger als angefordert, weil der Akku leer ist; der Regler hält das Ziel dann knapp über dem Ausgang, bis wieder Energie da ist.": "State of the Shelly controller. “Battery at discharge limit”: the NEXA delivers less than requested because the pack is empty; the controller then holds the target just above the output until energy is available again.",
    "Netzbezug heute": "Grid import today", "Aus dem Shelly integriert (nur positive Netzleistung), seit Tagesbeginn": "Integrated from the Shelly (positive grid power only), since midnight",
    "Eingespeist heute": "Exported today", "Aus dem Shelly integriert (nur negative Netzleistung). Bei funktionierender Nulleinspeisung nahe 0.": "Integrated from the Shelly (negative grid power only). Close to 0 while zero feed-in works.",
    "Jahr und Rekorde": "Year and records", "Rekorde des Jahres": "Records of the year", "Rekord": "Record", "Stärkster Tag (kWh PV)": "Strongest day (kWh PV)",
    "Schwächster Tag (kWh PV)": "Weakest day (kWh PV)", "Höchste PV-Spitze (W)": "Highest PV peak (W)", "Höchster Hausverbrauch (kWh)": "Highest household consumption (kWh)",
    "Meister Netzbezug (kWh)": "Most grid import (kWh)", "Jahr bisher (kWh PV)": "Year so far (kWh PV)", "Tage mit Daten": "Days with data",
    "Bester Verdienst (€)": "Best earnings (€)", "Teuerster Netztag (€)": "Most expensive grid day (€)", "Ersparnis Jahr bisher (€)": "Saved this year (€)",
    "Meiste Batterieentladung (kWh)": "Most battery discharge (kWh)", "CO₂ vermieden Jahr (kg, 0,38 kg/kWh)": "CO₂ avoided this year (kg, 0.38 kg/kWh)",
    "Seit Jahresbeginn (lokale Zeit) aus den Tagessummen der Minutenmittel. Schwächster Tag nur unter Tagen mit mindestens 12 Stunden Daten.": "Since the start of the year (local time) from daily sums of minute means. Weakest day only among days with at least 12 hours of data.",
    "PV-Ertrag: Tag × Monat (Jahr)": "PV yield: day × month (year)", "Tagessumme des PV-Ertrags in kWh, Zeile = Monat, Spalte = Tag im Monat. Leer = keine Daten.": "Daily PV yield in kWh, row = month, column = day of month. Empty = no data.",
    "Kosten und Ersparnis": "Costs and savings", "Strompreis": "Electricity price", "Auf der Einstellungsseite unter „Strompreis und Ersparnis“ eingestellt (retained grolo/config/tariff). Ohne Eintrag rechnet das Dashboard mit 30 ct/kWh.": "Set on the settings page under “Electricity price and savings” (retained grolo/config/tariff). Without an entry the dashboard assumes 30 ct/kWh.",
    "Ersparnis heute": "Saved today", "Ersparnis Monat": "Saved this month", "Ersparnis Jahr": "Saved this year", "Ersparnis gesamt": "Saved in total",
    "Ins Haus abgegebene Energie × Strompreis: so viel Netzstrom musste nicht gekauft werden.": "Energy delivered to the house × electricity price: grid power that did not have to be bought.",
    "Netzkosten heute": "Grid cost today", "Netzkosten Monat": "Grid cost this month", "Netzbezug laut Shelly × Strompreis. Nur mit laufender Shelly-Regelung.": "Grid import per Shelly × electricity price. Only with the Shelly control running.",
    "Amortisation": "Payback", "Gesamte Ersparnis im Verhältnis zum Anlagenpreis (Einstellungsseite). 0 %, solange kein Anlagenpreis eingetragen ist.": "Total savings relative to the system price (settings page). 0 % until a system price is entered.",
    "Ersparnis und Netzkosten pro Tag (30 Tage)": "Savings and grid cost per day (30 days)", "Ersparnis": "Savings", "Netzkosten": "Grid cost",
    "Ersparnis = Abgabe ins Haus × Strompreis, Netzkosten = Netzbezug (Shelly) × Strompreis. Balken auf Tagesmitte.": "Savings = output to house × electricity price, grid cost = grid import (Shelly) × electricity price. Bars on midday.",
    "Hausverbrauch heute": "Household today", "Netz + NEXA-Ausgang laut Shelly, integriert seit Tagesbeginn": "Grid + NEXA output per Shelly, integrated since midnight",
    "Eigenversorgung heute": "Self-sufficiency today", "Anteil des Hausverbrauchs, den der NEXA geliefert hat (Shelly)": "Share of the household load supplied by the NEXA (Shelly)",
    "Batterie-Status": "Battery status", "Statusregister 10 des Geräts. Auf aktueller Firmware oft „Ruhe“, obwohl die Bilanz Laden oder Entladen zeigt.": "Device status register 10. On current firmware often \"Idle\" although the balance shows charging or discharging.", "Betriebsmodus": "Operating mode", "Systemtemperatur": "System temperature", "Batterietemperatur": "Battery temperature",
    "Lädt": "Charging", "Entlädt": "Discharging", "Ruhe": "Idle", "Last zuerst": "Load first", "Batterie zuerst": "Battery first", "Smart": "Smart",
    "Leistung und Ladezustand": "Power and state of charge", "Leistungsverlauf": "Power history", "PV": "PV", "Ins Haus": "To house",
    "Batterie (+ laden / − entladen)": "Battery (+ charge / − discharge)", "Batterie: positiv = laden, negativ = entladen": "Battery: positive = charging, negative = discharging",
    "Energie": "Energy", "PV-Ertrag pro Tag (30 Tage)": "PV yield per day (30 days)", "PV-Ertrag": "PV yield",
    "Aus den gemessenen Leistungswerten integriert, Tagesgrenzen lokale Zeit, Balken auf Tagesmitte. Zeitraum auf 30 Tage stellen, um alle Tage zu sehen.": "Integrated from measured power (sum of minute means, gaps count as zero), day boundaries in local time, bars at midday. Set the time range to 30 days to see all days.",
    "Abgabe ins Haus pro Tag (30 Tage)": "Output to house per day (30 days)",
    "Wohin ging der PV-Strom heute?": "Where did today's PV energy go?", "Direkt ins Haus": "Directly to house", "In die Batterie": "Into the battery",
    "Woher kam der Hausstrom heute?": "Where did today's house energy come from?", "Direkt aus PV": "Directly from PV", "Aus der Batterie": "From the battery",
    "PV heute": "PV today", "Aus Messungen berechnet. Die Energiezähler-Register des Geräts (eacToday usw.) bleiben auf aktueller Firmware bei 0.": "Computed from measurements. The device energy counter registers (eacToday etc.) stay at 0 on current firmware.", "Aus Messungen berechnet, seit Monatsbeginn": "Computed from measurements, since start of month", "Aus Messungen berechnet, seit Jahresbeginn": "Computed from measurements, since start of year", "Aus Messungen berechnet, seit Aufzeichnungsbeginn": "Computed from measurements, since recording began", "PV Monat": "PV month", "PV Jahr": "PV year", "PV gesamt": "PV total",
    "Heute aus Messwerten": "Today from measurements", "Aus dem Netz geladen": "Charged from the grid",
    "Ins Haus = positiver Ausgang (Register 116). Aus dem Netz geladen = negativer Ausgang, der NEXA zieht Netzstrom in die Batterie (z. B. Batterie zuerst).": "To house = positive output (register 116). Charged from the grid = negative output, the NEXA pulls grid power into the battery (e.g. Battery first).",
    "AC-Ausgang aus Register 116 (0,1-W-Schritte, Offset 30000). Negativ = der NEXA zieht Netzstrom in die Batterie (AC-Laden). Das Register pac (5) meldet auf aktueller Firmware dauerhaft 0.": "AC output from register 116 (0.1 W steps, offset 30000). Negative = the NEXA pulls grid power into the battery (AC charging). Register pac (5) reports a constant 0 on current firmware.", "Batterie geladen": "Battery charged", "Batterie entladen": "Battery discharged",
    "PV-Strings": "PV strings", "Leistung je String": "Power per string", "Leistung = Spannung × Strom je Eingang": "Power = voltage × current per input",
    "Spannung je String": "Voltage per string", "Strom je String": "Current per string", "String": "String",
    "Tagesspitzen, Sonnenstand und Modell": "Daily peaks, sun position and model",
    "Tagesspitze je String (30 Tage)": "Daily peak per string (30 days)",
    "Tag": "Day",
    "Spitze": "Peak",
    "Uhrzeit": "Time",
    "Sonnenazimut": "Sun azimuth",
    "Sonnenhöhe": "Sun elevation",
    "Höchste Minutenleistung je String und Tag mit Uhrzeit und Sonnenstand in diesem Moment. Nur Strings mit mehr als 5 W.": "Highest one-minute power per string and day with the time and the sun position at that moment. Only strings above 5 W.",
    "Tagesspitzen je String (30 Tage)": "Daily peaks per string (30 days)",
    "Höchste Minutenleistung je Tag, Balken auf Tagesmitte.": "Highest one-minute power per day, bars on midday.",
    "PV gesamt: Stunde × Tag (30 Tage)": "PV total: hour × day (30 days)",
    "String $string: Stunde × Tag (30 Tage)": "String $string: hour × day (30 days)",
    "Stundenmittel der Leistung, Zeile = Tag, Spalte = Stunde (lokale Zeit). Dunkel = wenig, hell = viel. Wandernde Muster zeigen Ausrichtung und Verschattung.": "Hourly mean power, row = day, column = hour (local time). Dark = little, bright = much. Shifting patterns reveal orientation and shading.",
    "Stärkster String je Stunde": "Strongest string per hour",
    "Welcher String im Stundenmittel am meisten liefert. Ost-Strings führen morgens, West-Strings nachmittags. Grau = unter 5 W.": "Which string delivers the most in the hourly mean. East strings lead in the morning, west strings in the afternoon. Grey = below 5 W.",
    "Nacht": "Night",
    "Gemessen vs. erwartet je String": "Measured vs. expected per string",
    "Erwartet": "Expected",
    "Gemessen: Spannung × Strom je String. Erwartet: Open-Meteo-Strahlung (DNI/DHI/GHI) auf die konfigurierte Modulfläche umgerechnet, mal Wp mal Performance-Ratio (Sidecar weather). Ohne Neigung/Ausrichtung auf der Einstellungsseite bleibt „Erwartet“ leer. Wiederkehrende Einbrüche zur gleichen Uhrzeit = Verschattung.": "Measured: voltage × current per string. Expected: Open-Meteo irradiance (DNI/DHI/GHI) transposed onto the configured panel plane, times Wp times performance ratio (weather sidecar). Without tilt/azimuth on the settings page, “Expected” stays empty. Recurring dips at the same time of day = shading.",
    "Sonnenstand": "Sun position",
    "Azimut": "Azimuth",
    "Höhe": "Elevation",
    "Sonne jetzt": "Sun now",
    "Azimut 0 = Nord, 90 = Ost, 180 = Süd, 270 = West. Höhe über dem Horizont, jede Minute vom Sidecar weather berechnet (NOAA).": "Azimuth 0 = north, 90 = east, 180 = south, 270 = west. Elevation above the horizon, computed every minute by the weather sidecar (NOAA).",
    "Leistung über Sonnenazimut": "Power vs. sun azimuth",
    "Jeder Punkt ein 5-Minuten-Mittel im gewählten Zeitraum. Der Schwerpunkt der Punktwolke zeigt, wohin ein String schaut; ein Einbruch bei einem festen Azimut ist ein Hindernis. Für ein Sonnenbahn-Polardiagramm siehe die GroLo-Website.": "Each point is a 5-minute mean in the selected range. The centre of the cloud shows where a string faces; a dip at a fixed azimuth is an obstacle. For a sun-path polar chart see the GroLo website.",
    "Geschätzte Ausrichtung je String": "Estimated orientation per string",
    "Empfehlungen je String (Jahresmodell und Verschattung)": "Recommendations per string (year model and shading)", "Basis": "Basis", "vom Optimum": "of optimum", "Optimum": "Optimum", "Gewinn Optimum": "Gain optimum",
    "Gleiche Richtung, Neigung": "Same direction, tilt", "Gewinn": "Gain", "Senkrecht nach": "Vertical facing", "Gewinn senkrecht": "Gain vertical", "Gewinn flach": "Gain flat", "Winteranteil": "Winter share", "Verschattung": "Shading", "Zonen": "Zones",
    "konfiguriert": "configured", "geschätzt": "estimated",
    "Täglich mit der Schätzung berechnet. Jahresmodell aus dem Open-Meteo-Archiv für den Standort: Ertrag der aktuellen Ausrichtung (konfiguriert, sonst geschätzt) in kWh je kWp, Anteil am Optimum und Gewinn durch Alternativen (gleiche Richtung mit bester Neigung, senkrecht mit bestem Azimut, flach, Optimum). Verschattung: Anteil der Sonnenstunden-Energie, der in Sonnenrichtungen fehlt, in denen die Messung weit unter dem Modell bleibt; Zonen mit Uhrzeiten auf der Einstellungsseite und der Website.": "Computed daily with the estimate. Year model from the Open-Meteo archive for the location: yield of the current orientation (configured, else estimated) in kWh per kWp, share of the optimum and gain from alternatives (same direction with best tilt, vertical with best azimuth, flat, optimum). Shading: share of sunny-hour energy missing in sun directions where the measurement stays far below the model; zones with times on the settings page and the website.", "Neigung": "Tilt", "Stunden": "Hours", "Güte": "Quality", "Stand": "As of",
    "gut": "good", "unsicher": "uncertain", "noch nicht bestimmbar": "not determinable yet", "kein Modul": "no panel",
    "Täglich (und per Knopf auf der Einstellungsseite) schätzt der Sidecar weather Neigung, Azimut und Wp jedes Strings aus den Stundenkurven der letzten 30 Tage gegen das Einstrahlungsmodell. Braucht mehrere sonnige Tage. Übernehmen auf der Einstellungsseite unter „Standort und Module“, dann füllt sich „Erwartet“.": "Once a day (and on request from the settings page) the weather sidecar estimates tilt, azimuth and Wp of every string from the hourly curves of the last 30 days against the irradiance model. Needs several sunny days. Apply it on the settings page under “Location and panels”, then “Expected” fills in.",
    "Batterie und Technik": "Battery and technical", "Temperaturen": "Temperatures", "System": "System", "Batterie": "Battery",
    "Zellspannung (min / max)": "Cell voltage (min / max)", "Ein großer Abstand deutet auf unausgeglichene Zellen hin": "A large spread indicates unbalanced cells",
    "Zyklen": "Cycles", "Gesundheit (SoH)": "Health (SoH)", "Batteriepacks": "Battery packs",
    "Entlade-Grenze": "Discharge limit", "Unter diesen SoC entlädt die Batterie nicht": "The battery does not discharge below this SoC", "Lade-Grenze": "Charge limit",
    "Netzspannung": "Grid voltage", "Register 115. Nahe 0 V, wenn der NEXA vom Netz getrennt ist.": "Register 115. Near 0 V when the NEXA is disconnected from the grid.",
    "Netzleistung": "Grid power", "Register 116, Offset 30000 = 0, Schritt 0,1 W (38000 = 800 W)": "Register 116, offset 30000 = 0, step 0.1 W (38000 = 800 W)",
    "Zellspannung Differenz": "Cell voltage spread", "Letzte Nachricht": "Last message", "Zeitpunkt des letzten Datensatzes vom Dongle": "Time of the last data packet from the dongle",
    "Firmware NEXA (Reg. 119/120)": "NEXA firmware (reg. 119/120)", "Rohteile aus den Registern, Zusammensetzung laut Growatt unbekannt": "Raw parts from the registers, Growatt's composition unknown",
    "Dongle Firmware / Modell": "Dongle firmware / model", "WLAN-Signal Dongle": "Dongle Wi-Fi signal", "Wird nur beim Verbindungsaufbau des Dongles gemeldet": "Only reported when the dongle connects",
    "Batteriepacks erkannt": "Battery packs detected", "Zellspannung min": "Cell voltage min",
    "SoC je Batteriepack": "SoC per battery pack", "Pack": "Pack", "Temperatur je Pack": "Temperature per pack",
    "Seriennummern Erweiterungspacks": "Serial numbers of expansion packs", "Pack 1 ist das Hauptgerät mit der Geräteseriennummer. Leer = kein Erweiterungspack.": "Pack 1 is the main unit with the device serial. Empty = no expansion pack.",
    "Unbekannte Register (Forschung)": "Unknown registers (research)", "Unbekannte Eingangsregister, Verlauf (nur ≠ 0)": "Unknown input registers, history (≠ 0 only)",
    "Rohwerte der Register, die die Bridge nicht kennt. 30000 = Offset für 0 bei vorzeichenbehafteten Werten.": "Raw values of registers the bridge does not know. 30000 = offset for 0 in signed values.",
    "Halteregister-Dump (unbekannt, ≠ 0)": "Holding register dump (unknown, ≠ 0)", "Kommt stündlich vom Gerät (Nachricht 0x0103).": "Sent hourly by the device (message 0x0103).",
    "Register": "Register", "Wert": "Value",
    "Wetter": "Weather", "Außentemperatur": "Outdoor temperature", "Open-Meteo für den Anlagenstandort, alle 10 Minuten": "Open-Meteo for the plant location, every 10 minutes",
    "Wetterzustand": "Conditions", "WMO-Wettercode von Open-Meteo": "WMO weather code from Open-Meteo", "Bewölkung": "Cloud cover", "Globalstrahlung": "Global irradiance",
    "Kurzwellige Einstrahlung auf die Horizontale in W/m², Referenz für die PV-Leistung": "Shortwave irradiance on the horizontal in W/m², reference for PV power",
    "Sonnenschein heute": "Sunshine today", "Prognostizierte Sonnenscheindauer des Tages": "Forecast sunshine duration for the day",
    "Strahlungssumme heute / morgen": "Irradiation sum today / tomorrow", "Tagessumme der Globalstrahlung in MJ/m² laut Vorhersage (1 MJ/m² ≈ 0,28 kWh/m²)": "Daily sum of global irradiance in MJ/m² per forecast (1 MJ/m² ≈ 0.28 kWh/m²)",
    "Globalstrahlung und PV-Leistung": "Global irradiance and PV power", "Globalstrahlung W/m²": "Irradiance W/m²", "PV-Leistung W": "PV power W",
    "Verhältnis von PV-Leistung zu Einstrahlung zeigt Verschattung, Ausrichtung und Verschmutzung. Strahlung links (W/m²), PV rechts (W).": "The ratio of PV power to irradiance reveals shading, orientation and soiling. Irradiance left (W/m²), PV right (W).",
    "Bewölkung und Temperatur": "Cloud cover and temperature", "Bewölkung %": "Cloud cover %", "Temperatur °C": "Temperature °C",
    "Vorhersage 48 h: Einstrahlung und Bewölkung": "Forecast 48 h: irradiance and cloud cover",
    "Stündliche Open-Meteo-Vorhersage ab jetzt. Zeitraum des Dashboards ist hier ohne Wirkung, das Panel zeigt immer die nächsten 48 Stunden.": "Hourly Open-Meteo forecast from now. The dashboard time range has no effect here, the panel always shows the next 48 hours.",
    "Sonnenaufgang / -untergang": "Sunrise / sunset", "Stunde": "Hour", "So": "Sun", "Mo": "Mon", "Di": "Tue", "Mi": "Wed", "Do": "Thu", "Fr": "Fri", "Sa": "Sat", "Heute, lokale Zeit": "Today, local time", "heute": "today", "morgen": "tomorrow",
    "Klar": "Clear", "Überwiegend klar": "Mainly clear", "Teilweise bewölkt": "Partly cloudy", "Bedeckt": "Overcast", "Nebel": "Fog", "Reifnebel": "Rime fog", "Sprühregen": "Drizzle",
    "Leichter Regen": "Light rain", "Regen": "Rain", "Starker Regen": "Heavy rain", "Schneefall": "Snow", "Regenschauer": "Showers", "Gewitter": "Thunderstorm", "PV aus Strings": "PV from strings", "PV (Register 7)": "PV (register 7)", "Geräteregister zum Vergleich": "Device registers for comparison",
    "AC-Ausgang aus Register 116 (0,1-W-Schritte, Offset 30000). Das Register pac (5) meldet auf aktueller Firmware dauerhaft 0.": "AC output from register 116 (0.1 W steps, offset 30000). Register pac (5) reports a constant 0 on current firmware.",
    "Bilanz PV minus Ausgang: positiv = laden, negativ = entladen. Register 11 meldet auf aktueller Firmware dauerhaft 0.": "Balance PV minus output: positive = charging, negative = discharging. Register 11 reports a constant 0 on current firmware.",
    "PV aus Spannung × Strom der Strings, Ausgang aus Register 116, Batterie als Bilanz PV minus Ausgang (positiv = laden). Gestrichelt das gerundete PV-Register des Geräts.": "PV from voltage × current of the strings, output from register 116, battery as balance PV minus output (positive = charging). Dashed: the device's rounded PV register.",
    "Rohwerte der Geräteregister. pac und Batterieleistung bleiben auf aktueller Firmware bei 0, deshalb rechnet das Dashboard mit Register 116 und den Strings.": "Raw device registers. pac and battery power stay at 0 on current firmware, which is why the dashboard uses register 116 and the strings.",
    "Summe Spannung × Strom aller Strings. Feiner und aktueller als das Geräteregister, das auf ganze Watt rundet.": "Sum of voltage × current of all strings. Finer and more current than the device register, which rounds to whole watts.", "PV-Eingänge belegt": "PV inputs in use", "belegt": "in use",
    "Eingänge mit mehr als 15 V in den letzten 24 h, von 4 MPPT-Eingängen. Freie Eingänge zeigen etwa 7 V.": "Inputs with more than 15 V in the last 24 h, out of 4 MPPT inputs. Free inputs show about 7 V.",
    "Top-Strings 24 h": "Top strings 24 h", "Top-Strings 7 Tage": "Top strings 7 days", "Top-Strings 30 Tage": "Top strings 30 days", "Anteil": "Share",
    "Energie je PV-Eingang (Spannung × Strom, Minutenmittel) im Zeitraum, absteigend sortiert. Anteil an der Summe aller Eingänge.": "Energy per PV input (voltage × current, minute means) in the window, sorted descending. Share of the total of all inputs.",
    "PV-Eingang 1 bis 4": "PV input 1 to 4", "Maximale Spannung je Eingang in 24 h. Grün = Panel angeschlossen (> 15 V), rot = frei.": "Maximum voltage per input in 24 h. Green = panel connected (> 15 V), red = free.", "Einstellungen": "Settings", "Einstellungsseite (GroLo)": "Settings page (GroLo)",
}


def build(lang):
    _ = (lambda s: s) if lang == "de" else (lambda s: EN.get(s, s))
    HEAD = f'import "timezone"\nimport "math"\nimport "date"\nimport "join"\nimport "array"\noption location = timezone.location(name: "{TZ}")\n'

    # ------------------------------------------------------------- Flux-Bausteine
    def q_series(field, label, fn="mean"):
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "nexa" and r._field == "{field}")
  |> aggregateWindow(every: v.windowPeriod, fn: {fn}, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    def q_last(field, measurement="nexa", rng="-1h"):
        return f'''from(bucket: "{BUCKET}")
  |> range(start: {rng})
  |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "{field}")
  |> last()
  |> keep(columns: ["_time", "_value"])'''

    def q_sh_last(field):
        return f'''from(bucket: "{BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "shelly" and r._field == "{field}")
  |> last()
  |> keep(columns: ["_time", "_value"])'''

    def q_last_named(field, label, measurement="nexa", rng="-1h"):
        """Nur der Wert, benannt: für Stat-Panels mit mehreren Abfragen (kein Zeitfeld, das den Namen stiehlt)."""
        return f'''from(bucket: "{BUCKET}")
  |> range(start: {rng})
  |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "{field}")
  |> last()
  |> keep(columns: ["_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    def q_pv_detected(label):
        """Anzahl PV-Eingänge mit Spannung > 5 V in den letzten 24 h."""
        return f'''from(bucket: "{BUCKET}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "nexa" and (r._field == "pv1Voltage" or r._field == "pv2Voltage" or r._field == "pv3Voltage" or r._field == "pv4Voltage"))
  |> max()
  |> map(fn: (r) => ({{ r with _value: if r._value > 15.0 then 1 else 0 }}))
  |> group()
  |> sum()
  |> keep(columns: ["_value"])
  |> rename(columns: {{_value: "Value"}})'''

    def q_pivot_map(fields, expr_map):
        filt = " or ".join(f'r._field == "{f}"' for f in fields)
        maps = ", ".join(f'"{k}": {v}' for k, v in expr_map.items())
        keep = ", ".join(f'"{k}"' for k in expr_map)
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "nexa" and ({filt}))
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> map(fn: (r) => ({{ _time: r._time, {maps} }}))
  |> keep(columns: ["_time", {keep}])'''

    def q_daily_energy(field, label, days=30):
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r._measurement == "nexa" and r._field == "{field}")
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> aggregateWindow(every: 1d, fn: sum, timeSrc: "_start", createEmpty: false)
  |> map(fn: (r) => ({{ r with _value: r._value / 60000.0 }}))
  |> keep(columns: ["_time", "_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    SIGNED_BAT = ('if r.totalBatteryPackChargingStatus == "Discharging" then -1.0 * math.abs(x: r.totalBatteryPackChargingPower) '
                  'else math.abs(x: r.totalBatteryPackChargingPower)')

    def q_signed_battery(label):
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "nexa" and (r._field == "totalBatteryPackChargingPower" or r._field == "totalBatteryPackChargingStatus"))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.totalBatteryPackChargingPower and exists r.totalBatteryPackChargingStatus)
  |> map(fn: (r) => ({{ r with _value: {SIGNED_BAT} }}))
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    PV_FIELDS = [f"pv{i}{s}" for i in range(1, 5) for s in ("Voltage", "Current")]
    PV_EXPR = " + ".join(f"r.pv{i}Voltage * r.pv{i}Current" for i in range(1, 5))
    OUT_EXPR = "(r.onGridPower - 30000.0) / 10.0"  # Register 116 in 0,1-W-Schritten
    FLOW_FIELDS = PV_FIELDS + ["onGridPower"]
    FLOW_FILT = " or ".join(f'r._field == "{f}"' for f in FLOW_FIELDS)

    def q_flow_series(expr_map):
        """Zeitreihen aus PV (Strings) und Ausgang (Reg. 116): expr_map Label -> Flux-Ausdruck mit pv/out."""
        maps = ", ".join(f'"{k}": {v}' for k, v in expr_map.items())
        keep = ", ".join(f'"{k}"' for k in expr_map)
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "nexa" and ({FLOW_FILT}))
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.onGridPower and exists r.pv1Voltage)
  |> map(fn: (r) => {{
      pv = {PV_EXPR}
      out = {OUT_EXPR}
      outp = if out > 0.0 then out else 0.0
      acin = if out < 0.0 then -out else 0.0
      direct = if pv < outp then pv else outp
      return {{ _time: r._time, {maps} }}
    }})
  |> keep(columns: ["_time", {keep}])'''

    def q_flow_last(expr):
        """Letzter Wert eines Ausdrucks aus pv/out."""
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "nexa" and ({FLOW_FILT}))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.onGridPower and exists r.pv1Voltage)
  |> last(column: "onGridPower")
  |> map(fn: (r) => {{
      pv = {PV_EXPR}
      out = {OUT_EXPR}
      outp = if out > 0.0 then out else 0.0
      acin = if out < 0.0 then -out else 0.0
      direct = if pv < outp then pv else outp
      return {{ _time: r._time, _value: {expr} }}
    }})
  |> keep(columns: ["_time", "_value"])'''

    def q_flow_daily(expr, label, days=30):
        """Tagesenergie (kWh) eines Ausdrucks aus pv/out."""
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r._measurement == "nexa" and ({FLOW_FILT}))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.onGridPower and exists r.pv1Voltage)
  |> map(fn: (r) => {{
      pv = {PV_EXPR}
      out = {OUT_EXPR}
      outp = if out > 0.0 then out else 0.0
      acin = if out < 0.0 then -out else 0.0
      direct = if pv < outp then pv else outp
      return {{ r with _value: {expr} }}
    }})
  |> aggregateWindow(every: 1d, fn: sum, timeSrc: "_start", createEmpty: false)
  |> timeShift(duration: 12h)
  |> map(fn: (r) => ({{ r with _value: r._value / 60000.0 }}))
  |> keep(columns: ["_time", "_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    def q_flow_today(expr, label):
        """Energie heute (kWh) eines Ausdrucks aus pv/out; chg/dis = Batterie laden/entladen aus der Bilanz."""
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: today())
  |> filter(fn: (r) => r._measurement == "nexa" and ({FLOW_FILT}))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.onGridPower and exists r.pv1Voltage)
  |> map(fn: (r) => {{
      pv = {PV_EXPR}
      out = {OUT_EXPR}
      outp = if out > 0.0 then out else 0.0
      acin = if out < 0.0 then -out else 0.0
      direct = if pv < outp then pv else outp
      chg = if pv - out > 0.0 then pv - out else 0.0
      dis = if out - pv > 0.0 then out - pv else 0.0
      return {{ r with _value: {expr} }}
    }})
  |> sum()
  |> map(fn: (r) => ({{ r with _value: r._value / 60000.0 }}))
  |> keep(columns: ["_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    def q_flow_range(expr, label, start):
        """Energie (kWh) eines Ausdrucks aus pv/out über einen Zeitraum (Minutenmittel, dann Integral)."""
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: {start})
  |> filter(fn: (r) => r._measurement == "nexa" and ({FLOW_FILT}))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.onGridPower and exists r.pv1Voltage)
  |> map(fn: (r) => {{
      pv = {PV_EXPR}
      out = {OUT_EXPR}
      outp = if out > 0.0 then out else 0.0
      acin = if out < 0.0 then -out else 0.0
      direct = if pv < outp then pv else outp
      return {{ r with _value: {expr} }}
    }})
  |> sum()
  |> map(fn: (r) => ({{ r with _value: r._value / 60000.0 }}))
  |> keep(columns: ["_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    # Strompreis aus der retained Konfiguration (Telegraf -> Measurement tariff), Fallback 30 ct/kWh. Als Flux-Präfix vor Abfragen.
    def tariff_head(field="price_ct_kwh", default="30.0", name="price"):
        return f'''{name} = (array.concat(arr: from(bucket: "{BUCKET}")
  |> range(start: -10y)
  |> filter(fn: (r) => r._measurement == "tariff" and r._field == "{field}")
  |> last()
  |> findColumn(fn: (key) => true, column: "_value"), v: [{default}]))[0]
'''

    def q_tariff_value(field="price_ct_kwh", default="30.0"):
        return HEAD + tariff_head(field, default) + '''array.from(rows: [{_time: now(), _value: price}])'''

    def q_sh_energy(sign, start, label="Value", factor="1.0"):
        """Energie (kWh) aus der Shelly-Netzleistung: sign '+' = Bezug, '-' = Einspeisung, 'h' = Hausverbrauch; optional × Faktor (Preis)."""
        expr = {"+": "if r._value > 0.0 then r._value else 0.0", "-": "if r._value < 0.0 then -r._value else 0.0", "h": "r._value"}[sign]
        field = "household_w" if sign == "h" else "grid_w"
        return f'''from(bucket: "{BUCKET}")
  |> range(start: {start})
  |> filter(fn: (r) => r._measurement == "shelly" and r._field == "{field}")
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> map(fn: (r) => ({{ r with _value: {expr} }}))
  |> sum()
  |> map(fn: (r) => ({{ r with _value: r._value / 60000.0 * {factor} }}))
  |> keep(columns: ["_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    def q_saved(start, label="Value"):
        """Ersparnis in EUR: ins Haus abgegebene Energie × Strompreis."""
        return HEAD + tariff_head() + f'''from(bucket: "{BUCKET}")
  |> range(start: {start})
  |> filter(fn: (r) => r._measurement == "nexa" and ({FLOW_FILT}))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.onGridPower and exists r.pv1Voltage)
  |> map(fn: (r) => ({{ r with _value: if {OUT_EXPR} > 0.0 then {OUT_EXPR} else 0.0 }}))
  |> sum()
  |> map(fn: (r) => ({{ r with _value: r._value / 60000.0 * price / 100.0 }}))
  |> keep(columns: ["_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    def q_saved_daily(label, days=30):
        return HEAD + tariff_head() + f'''from(bucket: "{BUCKET}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r._measurement == "nexa" and ({FLOW_FILT}))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.onGridPower and exists r.pv1Voltage)
  |> map(fn: (r) => ({{ r with _value: if {OUT_EXPR} > 0.0 then {OUT_EXPR} else 0.0 }}))
  |> aggregateWindow(every: 1d, fn: sum, timeSrc: "_start", createEmpty: false)
  |> timeShift(duration: 12h)
  |> map(fn: (r) => ({{ r with _value: r._value / 60000.0 * price / 100.0 }}))
  |> keep(columns: ["_time", "_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    def q_gridcost_daily(label, days=30):
        return HEAD + tariff_head() + f'''from(bucket: "{BUCKET}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r._measurement == "shelly" and r._field == "grid_w")
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> map(fn: (r) => ({{ r with _value: if r._value > 0.0 then r._value else 0.0 }}))
  |> aggregateWindow(every: 1d, fn: sum, timeSrc: "_start", createEmpty: false)
  |> timeShift(duration: 12h)
  |> map(fn: (r) => ({{ r with _value: r._value / 60000.0 * price / 100.0 }}))
  |> keep(columns: ["_time", "_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    def q_payback():
        """Gesamte Ersparnis in Prozent des Anlagenpreises (0, wenn kein Preis eingetragen)."""
        return HEAD + tariff_head() + tariff_head("system_cost_eur", "0.0", "cost") + f'''from(bucket: "{BUCKET}")
  |> range(start: 0)
  |> filter(fn: (r) => r._measurement == "nexa" and ({FLOW_FILT}))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.onGridPower and exists r.pv1Voltage)
  |> map(fn: (r) => ({{ r with _value: if {OUT_EXPR} > 0.0 then {OUT_EXPR} else 0.0 }}))
  |> sum()
  |> map(fn: (r) => ({{ r with _value: if cost > 0.0 then (r._value / 60000.0 * price / 100.0) / cost * 100.0 else 0.0 }}))
  |> keep(columns: ["_value"])
  |> rename(columns: {{_value: "Value"}})'''

    PIE_FIELDS = ["ppv", "pac", "totalBatteryPackChargingPower", "totalBatteryPackChargingStatus"]

    def q_integral_today(expr, label):
        filt = " or ".join(f'r._field == "{f}"' for f in PIE_FIELDS)
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: today())
  |> filter(fn: (r) => r._measurement == "nexa" and ({filt}))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.ppv and exists r.pac and exists r.totalBatteryPackChargingPower)
  |> map(fn: (r) => {{
      p = math.abs(x: r.totalBatteryPackChargingPower)
      chg = if r.totalBatteryPackChargingStatus == "Charging" then p else 0.0
      dis = if r.totalBatteryPackChargingStatus == "Discharging" then p else 0.0
      return {{ r with _value: {expr} }}
    }})
  |> sum()
  |> map(fn: (r) => ({{ r with _value: r._value / 60000.0 }}))
  |> keep(columns: ["_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    # ------------------------------------------------------------- Panel-Helfer
    ids = [0]

    def nid():
        ids[0] += 1
        return ids[0]

    def target(query, ref="A"):
        return {"datasource": DS, "query": query, "refId": ref}

    def panel(ptype, title, x, y, w, h, targets, unit=None, opts=None, defaults=None, overrides=None, desc=None):
        fc = {"defaults": {}, "overrides": overrides or []}
        if unit:
            fc["defaults"]["unit"] = unit
        if defaults:
            fc["defaults"].update(defaults)
        p = {"id": nid(), "type": ptype, "title": title, "gridPos": {"x": x, "y": y, "w": w, "h": h},
             "datasource": DS, "targets": targets, "fieldConfig": fc, "options": opts or {}}
        if desc:
            p["description"] = desc
        return p

    def row(title, y):
        return {"id": nid(), "type": "row", "title": title, "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}

    def thresholds(*steps):
        return {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in steps]}

    def stat(title, x, y, w, h, query, unit=None, color="blue", decimals=None, mapping=None, desc=None, thr=None, text_mode="value", extra_targets=None):
        d = {"color": {"mode": "fixed", "fixedColor": color}}
        if thr:
            d["color"] = {"mode": "thresholds"}; d["thresholds"] = thr
        if decimals is not None:
            d["decimals"] = decimals
        if mapping:
            d["mappings"] = mapping
        opts = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/^Value$/", "values": False},
                "colorMode": "value", "graphMode": "none", "textMode": text_mode, "justifyMode": "center"}
        return panel("stat", title, x, y, w, h, [target(query)] + (extra_targets or []), unit, opts=opts, defaults=d, desc=desc)

    def stat_field(title, x, y, w, h, field, unit=None, color="blue", decimals=None, mapping=None, desc=None, thr=None):
        return stat(title, x, y, w, h, q_last(field), unit, color, decimals, mapping, desc, thr)

    def ts(title, x, y, w, h, targets, unit, fill=10, stack=False, overrides=None, desc=None, mn=None, mx=None, bars=False, defaults_extra=None):
        custom = {"drawStyle": "bars" if bars else "line", "lineWidth": 2, "fillOpacity": fill, "gradientMode": "opacity", "showPoints": "never", "spanNulls": True,
                  "stacking": {"mode": "normal" if stack else "none", "group": "A"}}
        if bars:
            custom.update({"fillOpacity": 80, "lineWidth": 1, "barWidthFactor": 0.8})
        d = {"custom": custom}
        if mn is not None:
            d["min"] = mn
        if mx is not None:
            d["max"] = mx
        if defaults_extra:
            d.update(defaults_extra)
        return panel("timeseries", title, x, y, w, h, targets, unit,
                     opts={"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True, "calcs": ["mean", "max", "lastNotNull"]},
                           "tooltip": {"mode": "multi", "sort": "desc"}}, defaults=d, overrides=overrides, desc=desc)

    def color_override(name, color):
        return {"matcher": {"id": "byName", "options": name}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}]}

    def name_by_ref(ref, name):
        return {"matcher": {"id": "byFrameRefID", "options": ref}, "properties": [{"id": "displayName", "value": name}]}

    C_PV, C_HOUSE, C_BAT, C_SOC = "yellow", "orange", "green", "green"
    status_map = [{"type": "value", "options": {"Charging": {"text": _("Lädt"), "color": "green"}, "Discharging": {"text": _("Entlädt"), "color": "orange"}, "Idle": {"text": _("Ruhe"), "color": "blue"}}}]
    mode_map = [{"type": "value", "options": {"Load First": {"text": _("Last zuerst"), "color": "orange"}, "Battery First": {"text": _("Batterie zuerst"), "color": "green"}, "Smart Mode": {"text": _("Smart"), "color": "blue"}}}]
    temp_thr = thresholds((None, "blue"), (10, "green"), (35, "orange"), (45, "red"))

    panels = []
    y = 0
    heat_opts = {"calculate": False, "cellGap": 1, "cellValues": {"unit": "watt"}, "color": {"mode": "scheme", "scheme": "YlOrRd", "steps": 48, "fill": "dark-orange", "reverse": False, "exponent": 0.6, "min": 0},
                 "yAxis": {"axisPlacement": "left", "reverse": False, "unit": "none", "decimals": 0}, "rowsFrame": {"layout": "ge", "value": "W"}, "tooltip": {"mode": "single", "yHistogram": False, "showColorScale": False},
                 "legend": {"show": True}, "exemplars": {"color": "rgba(255,0,255,0.7)"}, "filterValues": {"le": 1e-9}, "showValue": "never"}

    # ============================================================ Jetzt
    panels.append(row(_("Jetzt"), y)); y += 1
    panels += [
        stat(_("PV-Leistung"), 0, y, 4, 5, q_flow_last("pv"), "watt", C_PV, desc=_("Summe Spannung × Strom aller Strings. Feiner und aktueller als das Geräteregister, das auf ganze Watt rundet.")),
        stat(_("Ausgang ins Haus"), 4, y, 4, 5, q_flow_last("out"), "watt", C_HOUSE, desc=_("AC-Ausgang aus Register 116 (0,1-W-Schritte, Offset 30000). Negativ = der NEXA zieht Netzstrom in die Batterie (AC-Laden). Das Register pac (5) meldet auf aktueller Firmware dauerhaft 0.")),
        stat(_("Batterie-Leistung"), 8, y, 4, 5, q_flow_last("pv - out"), "watt", C_BAT, desc=_("Bilanz PV minus Ausgang: positiv = laden, negativ = entladen. Register 11 meldet auf aktueller Firmware dauerhaft 0.")),
        panel("gauge", _("Ladezustand"), 12, y, 6, 10, [target(q_last("totalBatteryPackSoc"))], "percent",
              opts={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/^Value$/", "values": False}, "showThresholdLabels": False, "showThresholdMarkers": True},
              defaults={"min": 0, "max": 100, "decimals": 0, "thresholds": thresholds((None, "red"), (20, "orange"), (50, "yellow"), (80, "green"))}),
        stat(_("Hausverbrauch"), 18, y, 6, 5, q_sh_last("household_w"), "watt", C_HOUSE, desc=_("Gemessen aus dem Shelly (Netz + NEXA-Ausgang). Nur mit konfiguriertem Shelly.")),
        stat_field(_("Batterie-Status"), 0, y + 5, 4, 5, "totalBatteryPackChargingStatus", None, "blue", mapping=status_map, desc=_("Statusregister 10 des Geräts. Auf aktueller Firmware oft „Ruhe“, obwohl die Bilanz Laden oder Entladen zeigt.")),
        stat_field(_("Betriebsmodus"), 4, y + 5, 4, 5, "workMode", None, "orange", mapping=mode_map),
        stat_field(_("Systemtemperatur"), 8, y + 5, 4, 5, "systemTemp", "celsius", None, 1, thr=thresholds((None, "blue"), (35, "green"), (50, "orange"), (60, "red"))),
        stat_field(_("Batterietemperatur"), 18, y + 5, 6, 5, "battery1Temp", "celsius", None, 1, thr=temp_thr),
    ]
    y += 10

    # ============================================================ Nulleinspeisung (Shelly)
    def q_sh_series(field, label):
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "shelly" and r._field == "{field}")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
  |> rename(columns: {{_value: "{label}"}})'''
    panels.append(row(_("Nulleinspeisung (Shelly)"), y)); y += 1
    reason_map = [{"type": "value", "options": {"ok": {"text": _("regelt"), "color": "green"}, "shelly_unreachable": {"text": _("Shelly nicht erreichbar"), "color": "red"},
                   "device_offline": {"text": _("NEXA offline"), "color": "red"}, "wrong_mode": {"text": _("Modus nicht Last zuerst"), "color": "orange"}, "battery_low": {"text": _("Batterie an Entladegrenze"), "color": "orange"},
                   "device_limited": {"text": _("NEXA liefert weniger als Ziel"), "color": "orange"}, "disabled": {"text": _("aus"), "color": "text"}}}]
    panels += [
        stat(_("Netzbezug"), 0, y, 4, 4, q_sh_last("grid_w"), "watt", "red", desc=_("Netzleistung am Zähler: positiv = Bezug, negativ = Einspeisung")),
        stat(_("Hausverbrauch (Shelly)"), 4, y, 4, 4, q_sh_last("household_w"), "watt", C_HOUSE, desc=_("Gemessener Hausverbrauch aus dem Shelly (Netz + NEXA-Ausgang)")),
        stat(_("Zielleistung"), 8, y, 4, 4, q_sh_last("target_w"), "watt", C_PV, desc=_("Vom Regler angeforderte NEXA-Ausgangsleistung")),
        stat(_("Regler-Status"), 0, y + 4, 4, 4, q_last("reason", "shelly"), None, "text", mapping=reason_map,
             desc=_("Zustand des Shelly-Reglers. „Batterie an Entladegrenze“: der NEXA liefert weniger als angefordert, weil der Akku leer ist; der Regler hält das Ziel dann knapp über dem Ausgang, bis wieder Energie da ist.")),
        stat(_("Netzbezug heute"), 4, y + 4, 4, 4, HEAD + q_sh_energy("+", "today()"), "kwatth", "red", 2, desc=_("Aus dem Shelly integriert (nur positive Netzleistung), seit Tagesbeginn")),
        stat(_("Eingespeist heute"), 8, y + 4, 4, 4, HEAD + q_sh_energy("-", "today()"), "kwatth", "blue", 3, desc=_("Aus dem Shelly integriert (nur negative Netzleistung). Bei funktionierender Nulleinspeisung nahe 0.")),
        ts(_("Verlauf Nulleinspeisung"), 12, y, 12, 8, [
            target(q_sh_series("grid_w", _("Netz")), "A"), target(q_sh_series("household_w", _("Haushalt")), "B"), target(q_sh_series("out_w", _("Ausgabe")), "C"), target(q_sh_series("target_w", _("Zielleistung")), "D"),
        ], "watt", overrides=[color_override(_("Netz"), "red"), color_override(_("Haushalt"), C_HOUSE), color_override(_("Ausgabe"), C_PV),
                              {"matcher": {"id": "byName", "options": _("Zielleistung")}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "dark-yellow"}}, {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [6, 4]}}, {"id": "custom.lineWidth", "value": 1}, {"id": "custom.fillOpacity", "value": 0}]}],
           desc=_("Netz und Haushalt, sobald ein Shelly konfiguriert ist; Ausgabe und Ziel nur bei laufender Regelung")),
    ]
    y += 8

    # ============================================================ Leistung
    panels.append(row(_("Leistung und Ladezustand"), y)); y += 1
    panels += [
        ts(_("Leistungsverlauf"), 0, y, 16, 10, [
            target(q_flow_series({_("PV"): "pv", _("Ins Haus"): "out", _("Batterie (+ laden / − entladen)"): "pv - out"}), "A"),
            target(q_series("ppv", _("PV (Register 7)")), "B"),
        ], "watt", overrides=[color_override(_("PV"), C_PV), color_override(_("PV (Register 7)"), "dark-yellow"), color_override(_("Ins Haus"), C_HOUSE), color_override(_("Batterie (+ laden / − entladen)"), C_BAT),
                              {"matcher": {"id": "byName", "options": _("PV (Register 7)")}, "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [6, 4]}}, {"id": "custom.lineWidth", "value": 1}]}],
           desc=_("PV aus Spannung × Strom der Strings, Ausgang aus Register 116, Batterie als Bilanz PV minus Ausgang (positiv = laden). Gestrichelt das gerundete PV-Register des Geräts.")),
        ts(_("Ladezustand"), 16, y, 8, 10, [target(q_series("totalBatteryPackSoc", "SoC"))], "percent", fill=25, overrides=[color_override("SoC", C_SOC)], mn=0, mx=100),
    ]
    y += 10

    # ============================================================ Energie
    panels.append(row(_("Energie"), y)); y += 1
    panels += [
        ts(_("PV-Ertrag pro Tag (30 Tage)"), 0, y, 12, 9, [target(q_flow_daily("pv", _("PV-Ertrag")))], "kwatth", bars=True,
           overrides=[color_override(_("PV-Ertrag"), C_PV)], desc=_("Aus den gemessenen Leistungswerten integriert, Tagesgrenzen lokale Zeit, Balken auf Tagesmitte. Zeitraum auf 30 Tage stellen, um alle Tage zu sehen.")),
        ts(_("Abgabe ins Haus pro Tag (30 Tage)"), 12, y, 12, 9, [target(q_flow_daily("outp", _("Ins Haus")), "A"), target(q_flow_daily("acin", _("Aus dem Netz geladen")), "B")], "kwatth", bars=True, overrides=[color_override(_("Ins Haus"), C_HOUSE), color_override(_("Aus dem Netz geladen"), "blue")],
           desc=_("Ins Haus = positiver Ausgang (Register 116). Aus dem Netz geladen = negativer Ausgang, der NEXA zieht Netzstrom in die Batterie (z. B. Batterie zuerst).")),
    ]
    y += 9
    pie_opts = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "pieType": "donut", "displayLabels": ["percent"],
                "legend": {"displayMode": "table", "placement": "bottom", "showLegend": True, "values": ["value", "percent"]}}
    panels += [
        panel("piechart", _("Wohin ging der PV-Strom heute?"), 0, y, 6, 9, [
            target(q_flow_today("direct", _("Direkt ins Haus")), "A"),
            target(q_flow_today("pv - direct", _("In die Batterie")), "B")], "kwatth", opts=pie_opts, defaults={"decimals": 2},
              overrides=[color_override(_("Direkt ins Haus"), C_HOUSE), color_override(_("In die Batterie"), C_BAT)]),
        panel("piechart", _("Woher kam der Hausstrom heute?"), 6, y, 6, 9, [
            target(q_flow_today("direct", _("Direkt aus PV")), "A"),
            target(q_flow_today("outp - direct", _("Aus der Batterie")), "B")], "kwatth", opts=pie_opts, defaults={"decimals": 2},
              overrides=[color_override(_("Direkt aus PV"), C_PV), color_override(_("Aus der Batterie"), C_BAT)]),
        stat(_("PV heute"), 12, y, 3, 4, q_flow_range("pv", "Value", "today()"), "kwatth", C_PV, desc=_("Aus Messungen berechnet. Die Energiezähler-Register des Geräts (eacToday usw.) bleiben auf aktueller Firmware bei 0.")),
        stat(_("PV Monat"), 15, y, 3, 4, q_flow_range("pv", "Value", "date.truncate(t: now(), unit: 1mo)"), "kwatth", C_PV, desc=_("Aus Messungen berechnet, seit Monatsbeginn")),
        stat(_("PV Jahr"), 18, y, 3, 4, q_flow_range("pv", "Value", "date.truncate(t: now(), unit: 1y)"), "kwatth", C_PV, desc=_("Aus Messungen berechnet, seit Jahresbeginn")),
        stat(_("PV gesamt"), 21, y, 3, 4, q_flow_range("pv", "Value", "0"), "kwatth", C_PV, desc=_("Aus Messungen berechnet, seit Aufzeichnungsbeginn")),
        panel("stat", _("Heute aus Messwerten"), 12, y + 4, 12, 5, [
            target(q_flow_today("pv", _("PV-Ertrag")), "A"), target(q_flow_today("outp", _("Ins Haus")), "B"),
            target(q_flow_today("chg", _("Batterie geladen")), "C"), target(q_flow_today("dis", _("Batterie entladen")), "D"), target(q_flow_today("acin", _("Aus dem Netz geladen")), "E"),
            target(HEAD + q_sh_energy("h", "today()", _("Hausverbrauch (Shelly)")), "F"), target(HEAD + q_sh_energy("+", "today()", _("Netzbezug")), "G")], "kwatth",
              opts={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "value", "graphMode": "none", "textMode": "value_and_name", "justifyMode": "center"},
              defaults={"decimals": 2, "color": {"mode": "fixed", "fixedColor": "text"}},
              overrides=[color_override(_("PV-Ertrag"), C_PV), color_override(_("Ins Haus"), C_HOUSE), color_override(_("Batterie geladen"), C_BAT), color_override(_("Batterie entladen"), "orange"), color_override(_("Aus dem Netz geladen"), "blue"), color_override(_("Hausverbrauch (Shelly)"), "purple"), color_override(_("Netzbezug"), "red")]),
    ]
    y += 9

    # ============================================================ Kosten und Ersparnis
    panels.append(row(_("Kosten und Ersparnis"), y)); y += 1
    C_EUR = "green"
    saved_desc = _("Ins Haus abgegebene Energie × Strompreis: so viel Netzstrom musste nicht gekauft werden.")
    panels += [
        stat(_("Strompreis"), 0, y, 4, 4, q_tariff_value(), "suffix: ct/kWh", "text", 1, desc=_("Auf der Einstellungsseite unter „Strompreis und Ersparnis“ eingestellt (retained grolo/config/tariff). Ohne Eintrag rechnet das Dashboard mit 30 ct/kWh.")),
        stat(_("Ersparnis heute"), 4, y, 4, 4, q_saved("today()"), "currencyEUR", C_EUR, 2, desc=saved_desc),
        stat(_("Ersparnis Monat"), 8, y, 4, 4, q_saved("date.truncate(t: now(), unit: 1mo)"), "currencyEUR", C_EUR, 2, desc=saved_desc),
        stat(_("Ersparnis Jahr"), 12, y, 4, 4, q_saved("date.truncate(t: now(), unit: 1y)"), "currencyEUR", C_EUR, 2, desc=saved_desc),
        stat(_("Ersparnis gesamt"), 16, y, 4, 4, q_saved("0"), "currencyEUR", C_EUR, 2, desc=saved_desc),
        panel("gauge", _("Amortisation"), 20, y, 4, 8, [target(q_payback())], "percent",
              opts={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/^Value$/", "values": False}, "showThresholdLabels": False, "showThresholdMarkers": True},
              defaults={"min": 0, "max": 100, "decimals": 1, "thresholds": thresholds((None, "red"), (25, "orange"), (50, "yellow"), (100, "green"))},
              desc=_("Gesamte Ersparnis im Verhältnis zum Anlagenpreis (Einstellungsseite). 0 %, solange kein Anlagenpreis eingetragen ist.")),
        stat(_("Netzkosten heute"), 0, y + 4, 4, 4, HEAD + tariff_head() + q_sh_energy("+", "today()", factor="price / 100.0"), "currencyEUR", "red", 2, desc=_("Netzbezug laut Shelly × Strompreis. Nur mit laufender Shelly-Regelung.")),
        stat(_("Netzkosten Monat"), 4, y + 4, 4, 4, HEAD + tariff_head() + q_sh_energy("+", "date.truncate(t: now(), unit: 1mo)", factor="price / 100.0"), "currencyEUR", "red", 2, desc=_("Netzbezug laut Shelly × Strompreis. Nur mit laufender Shelly-Regelung.")),
        stat(_("Hausverbrauch heute"), 8, y + 4, 4, 4, HEAD + q_sh_energy("h", "today()"), "kwatth", C_HOUSE, 2, desc=_("Netz + NEXA-Ausgang laut Shelly, integriert seit Tagesbeginn")),
        stat(_("Eigenversorgung heute"), 12, y + 4, 4, 4, HEAD + f'''house = {q_sh_energy("h", "today()", "house")}
  |> findColumn(fn: (key) => true, column: "house")
from(bucket: "{BUCKET}")
  |> range(start: today())
  |> filter(fn: (r) => r._measurement == "nexa" and ({FLOW_FILT}))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.onGridPower and exists r.pv1Voltage)
  |> map(fn: (r) => ({{ r with _value: if {OUT_EXPR} > 0.0 then {OUT_EXPR} else 0.0 }}))
  |> sum()
  |> map(fn: (r) => ({{ r with _value: if length(arr: house) > 0 and house[0] > 0.0 then r._value / 60000.0 / house[0] * 100.0 else 0.0 }}))
  |> keep(columns: ["_value"])
  |> rename(columns: {{_value: "Value"}})''', "percent", None, 0, thr=thresholds((None, "red"), (20, "orange"), (50, "yellow"), (80, "green")),
             desc=_("Anteil des Hausverbrauchs, den der NEXA geliefert hat (Shelly)")),
        ts(_("Ersparnis und Netzkosten pro Tag (30 Tage)"), 0, y + 8, 24, 8, [target(q_saved_daily(_("Ersparnis")), "A"), target(q_gridcost_daily(_("Netzkosten")), "B")], "currencyEUR", bars=True,
           overrides=[color_override(_("Ersparnis"), C_EUR), color_override(_("Netzkosten"), "red")],
           desc=_("Ersparnis = Abgabe ins Haus × Strompreis, Netzkosten = Netzbezug (Shelly) × Strompreis. Balken auf Tagesmitte.")),
    ]
    y += 16

    # ============================================================ Jahresprognose
    # Nichts davon ist gemessen: der Sidecar forecast rechnet ein volles Jahr Stunde für Stunde mit dem echten
    # Wetter des Standorts durch und legt das Ergebnis als Measurement "forecast" (Jahr) und "forecast_month"
    # (je Monat) ab. Hier wird es nur gezeigt.
    def q_fc(field, rng="-30d"):
        return q_last(field, measurement="forecast", rng=rng)

    def bar_cat(title, x, y, w, h, query, unit=None, overrides=None, desc=None, xfield=None):
        """Balken über einer Kategorieachse statt über der Zeit: die Monatszeilen der Prognose tragen
        keinen Zeitstempel, den man zeigen wollte, sondern einen Monatsnamen."""
        opts = {"xTickLabelRotation": 0, "showValue": "auto", "stacking": "none",
                "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                "tooltip": {"mode": "multi"}}
        if xfield:
            opts["xField"] = xfield
        return panel("barchart", title, x, y, w, h, [target(query)], unit,
                     opts=opts, defaults={"custom": {"lineWidth": 1, "fillOpacity": 80}},
                     overrides=overrides, desc=desc)

    MONTHS = [_("Jan"), _("Feb"), _("Mär"), _("Apr"), _("Mai"), _("Jun"),
              _("Jul"), _("Aug"), _("Sep"), _("Okt"), _("Nov"), _("Dez")]

    def month_label():
        """Flux-Ausdruck, der aus dem Tag month den Monatsnamen macht."""
        parts = [f'if r.month == "{i + 1:02d}" then "{name}" ' for i, name in enumerate(MONTHS[:-1])]
        return "".join("else " + p if i else p for i, p in enumerate(parts)) + f'else "{MONTHS[-1]}"'

    def q_fc_month(fields):
        """Eine Tabelle: je Monat eine Zeile, je gewünschtem Feld eine Spalte mit sprechendem Namen."""
        filt = " or ".join(f'r._field == "{f}"' for f, _label in fields)
        cols = ", ".join(f'"{label}": r.{f}' for f, label in fields)
        return f'''from(bucket: "{BUCKET}")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "forecast_month" and ({filt}))
  |> last()
  |> group()
  |> pivot(rowKey: ["month"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["month"])
  |> map(fn: (r) => ({{ "{_("Monat")}": {month_label()}, {cols} }}))'''

    fc_quality = [{"type": "value", "options": {
        "measured": {"text": _("aus Messwerten"), "color": "green"},
        "partial": {"text": _("teils geschätzt"), "color": "blue"},
        "assumed": {"text": _("angenommen"), "color": "orange"}}}]
    panels.append(row(_("Jahresprognose"), y)); y += 1
    panels += [
        stat(_("Verbrauch im Jahr"), 0, y, 4, 5, q_fc("load_kwh"), "kwatth", C_HOUSE, 0,
             desc=_("Haushalt plus Wärmepumpe, modelliert über ein volles Jahr mit dem echten Wetter des Standorts. Grundlage sind deine Angaben auf der Einstellungsseite und alles, was bis jetzt gemessen wurde.")),
        stat(_("davon Wärmepumpe"), 4, y, 4, 5, q_fc("heatpump_kwh"), "kwatth", "purple", 0,
             desc=_("Wärmebedarf des Hauses, verteilt nach Heizgradstunden, geteilt durch den COP der jeweiligen Stunde.")),
        stat(_("PV-Ertrag"), 8, y, 4, 5, q_fc("pv_kwh"), "kwatth", C_PV, 0,
             desc=_("Aus Neigung, Azimut und Wp jedes Strings und der Einstrahlung des Wetterjahres. Leer, solange keine Modulleistung eingetragen ist.")),
        stat(_("Netzbezug im Jahr"), 12, y, 4, 5, q_fc("grid_kwh"), "kwatth", "red", 0,
             desc=_("Was nach Direktverbrauch und Batterie übrig bleibt. Die Abgabegrenze von 800 W begrenzt, wie viel der NEXA überhaupt beisteuern kann.")),
        stat(_("Autarkie"), 16, y, 4, 5, q_fc("autarky_pct"), "percent", C_BAT, 0,
             desc=_("Anteil des Jahresverbrauchs, den die eigene Anlage deckt.")),
        stat(_("Güte der Prognose"), 20, y, 4, 5, q_fc("quality"), None, "text", mapping=fc_quality,
             desc=_("„aus Messwerten“ heißt: Wärmebedarf, Haushalt, Module und COP-Kennlinie stehen alle auf eigenen Zahlen. „angenommen“ heißt, es fehlen noch Angaben auf der Einstellungsseite.")),
        stat(_("Jahresrechnung"), 0, y + 5, 6, 5, q_fc("cost_total_eur"), "currencyEUR", "red", 0,
             desc=_("Netzbezug x Arbeitspreis plus Grundpreis x 12, abzüglich Einspeisevergütung.")),
        stat(_("davon Grundpreis"), 6, y + 5, 4, 5, q_fc("cost_base_eur"), "currencyEUR", None, 0),
        stat(_("Ersparnis durch die Anlage"), 10, y + 5, 5, 5, q_fc("saving_eur"), "currencyEUR", C_EUR, 0,
             desc=_("Differenz zu einer Jahresrechnung ohne PV und Batterie, bei gleichem Verbrauch.")),
        stat(_("Empfohlener Abschlag"), 15, y + 5, 5, 5, q_fc("abschlag_eur"), "currencyEUR", C_BAT, 0,
             desc=_("Jahresrechnung geteilt durch zwölf, auf die nächsten fünf Euro aufgerundet. Zu niedrig heißt Nachzahlung, zu hoch heißt, dem Versorger zinslos Geld zu leihen.")),
        stat(_("Aktueller Abschlag"), 20, y + 5, 4, 5, q_fc("abschlag_ist_eur"), "currencyEUR", None, 0,
             desc=_("Was du laut Einstellungsseite heute zahlst. 0 = noch nicht eingetragen.")),
        stat(_("Unsicherheit der Prognose"), 0, y + 10, 5, 4, q_fc("uncertainty_pct"), "percent", None, 0,
             thr=thresholds((None, "green"), (10, "yellow"), (18, "orange"), (28, "red")),
             desc=_("Wie weit die Rechnung danebenliegen kann. Jede Annahme bringt eine eigene Streuung mit – Wärmebedarf, COP, Haushalt, PV, Wetterjahr –, und sie werden quadratisch addiert, weil sie unabhängig voneinander danebenliegen. Je mehr Angaben eingetragen und je mehr Wochen gemessen sind, desto schmaler wird das Band.")),
        stat(_("Rechnung, untere Kante"), 5, y + 10, 5, 4, q_fc("cost_low_eur"), "currencyEUR", C_BAT, 0),
        stat(_("Rechnung, obere Kante"), 10, y + 10, 5, 4, q_fc("cost_high_eur"), "currencyEUR", "red", 0,
             desc=_("Wer keine Nachzahlung riskieren will, legt den Abschlag auf diese Kante geteilt durch zwölf.")),
        stat(_("Heizstab im Jahr"), 15, y + 10, 4, 4, q_fc("eheat_kwh"), "kwatth", "orange", 0,
             desc=_("Wärme, die der Verdichter an den kältesten Stunden nicht mehr schafft und die der Heizstab elektrisch nachlegt – mit Arbeitszahl 1, also zum vollen Strompreis.")),
        stat(_("PV-Eichfaktor"), 19, y + 10, 5, 4, q_fc("pv_factor"), None, None, 2,
             thr=thresholds((None, "red"), (0.7, "orange"), (0.85, "yellow"), (0.95, "green")),
             desc=_("Gemessener Ertrag geteilt durch den, den das Modell für dieselben Sonnenstunden erwartet hätte. Unter 1 heißt: Verschattung, Schmutz oder eine zu optimistische Annahme zur Ausrichtung. Ein Wert weit unter 1 ist ein Grund, Neigung und Azimut nachzutragen.")),
        bar_cat(_("Verbrauch je Monat"), 0, y + 14, 12, 9,
                q_fc_month([("household_kwh", _("Haushalt")), ("heatpump_kwh", _("Wärmepumpe")), ("pv_kwh", _("PV-Ertrag"))]),
                "kwatth", xfield=_("Monat"),
                overrides=[color_override(_("Haushalt"), C_HOUSE), color_override(_("Wärmepumpe"), "purple"), color_override(_("PV-Ertrag"), C_PV)],
                desc=_("Das modellierte Jahr, aufgeteilt auf die Monate. Der Winter trägt fast den ganzen Wärmepumpenanteil, die PV steht ihm genau gegenläufig.")),
        bar_cat(_("Netzbezug und Kosten je Monat"), 12, y + 14, 12, 9,
                q_fc_month([("grid_kwh", _("Netzbezug")), ("cost_eur", _("Kosten"))]), "kwatth", xfield=_("Monat"),
                overrides=[color_override(_("Netzbezug"), "red"),
                           {"matcher": {"id": "byName", "options": _("Kosten")},
                            "properties": [{"id": "unit", "value": "currencyEUR"}, {"id": "custom.axisPlacement", "value": "right"},
                                           {"id": "color", "value": {"mode": "fixed", "fixedColor": C_EUR}}]}],
                desc=_("Woher der Abschlag kommt: die Monate sind sehr ungleich, der Abschlag glättet sie auf zwölf gleiche Raten.")),
    ]
    y += 24

    # ============================================================ Jahr und Rekorde
    panels.append(row(_("Jahr und Rekorde"), y)); y += 1
    def q_daily_year(expr, need_minutes=0):
        """Tagessummen (kWh) seit Jahresbeginn aus Minutenmitteln; Tage mit weniger als need_minutes Datenminuten ausblenden.
        Ohne join: kWh und Minutenzahl als zwei Reihen (Spalte k) und per pivot zusammenführen."""
        return HEAD + f'''base = from(bucket: "{BUCKET}")
  |> range(start: date.truncate(t: now(), unit: 1y))
  |> filter(fn: (r) => r._measurement == "nexa" and ({FLOW_FILT}))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.onGridPower and exists r.pv1Voltage)
  |> map(fn: (r) => {{
      pv = {PV_EXPR}
      out = {OUT_EXPR}
      outp = if out > 0.0 then out else 0.0
      return {{ _time: r._time, _start: r._start, _stop: r._stop, _value: {expr}, n: 1.0 }}
    }})
  |> group(columns: ["_start", "_stop"])
kwh = base |> aggregateWindow(every: 1d, fn: sum, timeSrc: "_start", createEmpty: false) |> map(fn: (r) => ({{ _time: r._time, k: "kwh", _value: r._value / 60000.0 }}))
mins = base |> map(fn: (r) => ({{ r with _value: r.n }})) |> aggregateWindow(every: 1d, fn: sum, timeSrc: "_start", createEmpty: false) |> map(fn: (r) => ({{ _time: r._time, k: "min", _value: r._value }}))
union(tables: [kwh, mins])
  |> group()
  |> pivot(rowKey: ["_time"], columnKey: ["k"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.kwh and exists r.min and r.min >= {float(need_minutes)})
  |> map(fn: (r) => ({{ _time: r._time, _value: r.kwh, minutes: r.min }}))
'''
    def q_sh_daily_year(field, sign):
        expr = {"+": "if r._value > 0.0 then r._value else 0.0", "h": "r._value"}[sign]
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: date.truncate(t: now(), unit: 1y))
  |> filter(fn: (r) => r._measurement == "shelly" and r._field == "{field}")
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> map(fn: (r) => ({{ r with _value: {expr} }}))
  |> aggregateWindow(every: 1d, fn: sum, timeSrc: "_start", createEmpty: false)
  |> map(fn: (r) => ({{ r with _value: r._value / 60000.0 }}))
  |> keep(columns: ["_time", "_value"])
'''
    def rec(q, label, pick, extra=""):
        """Ein Rekord als Tabellenzeile: pick = top/bottom, Spalten Rekord, Tag, Wert."""
        return q + f'''  |> group()
  |> keep(columns: ["_time", "_value"]){extra}
  |> {pick}(n: 1, columns: ["_value"])
  |> map(fn: (r) => ({{ "{_("Rekord")}": "{label}", "{_("Tag")}": r._time, "{_("Wert")}": r._value }}))
'''
    q_peak_year = HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: date.truncate(t: now(), unit: 1y))
  |> filter(fn: (r) => r._measurement == "nexa" and ({" or ".join(f'r._field == "{f}"' for f in PV_FIELDS)}))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.pv1Voltage and exists r.pv1Current)
  |> map(fn: (r) => ({{ _time: r._time, _value: {PV_EXPR} }}))
'''
    q_year_total = HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: date.truncate(t: now(), unit: 1y))
  |> filter(fn: (r) => r._measurement == "nexa" and ({FLOW_FILT}))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.onGridPower and exists r.pv1Voltage)
  |> map(fn: (r) => ({{ _time: r._time, _value: {PV_EXPR} }}))
  |> sum()
  |> map(fn: (r) => ({{ "{_("Rekord")}": "{_("Jahr bisher (kWh PV)")}", "{_("Tag")}": now(), "{_("Wert")}": r._value / 60000.0 }}))
  |> group()
'''
    rec_ovr = [{"matcher": {"id": "byName", "options": _("Tag")}, "properties": [{"id": "unit", "value": "time: DD.MM.YYYY HH:mm"}, {"id": "custom.width", "value": 150}]},
               {"matcher": {"id": "byName", "options": _("Wert")}, "properties": [{"id": "decimals", "value": 2}, {"id": "custom.width", "value": 110}]}]
    panels += [
        panel("table", _("Rekorde des Jahres"), 0, y, 10, 9, [
            target(rec(q_daily_year("pv", 60), _("Stärkster Tag (kWh PV)"), "top"), "A"),
            target(rec(q_daily_year("pv", 720), _("Schwächster Tag (kWh PV)"), "bottom", '\n  |> filter(fn: (r) => r._value > 0.05)'), "B"),
            target(rec(q_peak_year, _("Höchste PV-Spitze (W)"), "top"), "C"),
            target(rec(q_sh_daily_year("household_w", "h"), _("Höchster Hausverbrauch (kWh)"), "top"), "D"),
            target(rec(q_sh_daily_year("grid_w", "+"), _("Meister Netzbezug (kWh)"), "top"), "E"),
            target(q_year_total, "F"),
            target(rec(q_daily_year("outp", 60).replace(HEAD, HEAD + tariff_head(), 1), _("Bester Verdienst (€)"), "top", "\n  |> map(fn: (r) => ({ r with _value: r._value * price / 100.0 }))"), "G"),
            target(rec(q_sh_daily_year("grid_w", "+").replace(HEAD, HEAD + tariff_head(), 1), _("Teuerster Netztag (€)"), "top", "\n  |> map(fn: (r) => ({ r with _value: r._value * price / 100.0 }))"), "H"),
            target(rec(q_daily_year("if pv - out > 0.0 then 0.0 else out - pv", 60), _("Meiste Batterieentladung (kWh)"), "top"), "I"),
            target(q_year_total.replace(f'"{_("Jahr bisher (kWh PV)")}"', f'"{_("Ersparnis Jahr bisher (€)")}"').replace(f"_value: {PV_EXPR}", f"_value: if {OUT_EXPR} > 0.0 then {OUT_EXPR} else 0.0").replace("r._value / 60000.0 }", "r._value / 60000.0 * price / 100.0 }").replace(HEAD, HEAD + tariff_head()), "J"),
            target(q_year_total.replace(f'"{_("Jahr bisher (kWh PV)")}"', f'"{_("CO₂ vermieden Jahr (kg, 0,38 kg/kWh)")}"').replace(f"_value: {PV_EXPR}", f"_value: if {OUT_EXPR} > 0.0 then {OUT_EXPR} else 0.0").replace("r._value / 60000.0 }", "r._value / 60000.0 * 0.38 }"), "K")], None,
              opts={"showHeader": True, "cellHeight": "sm"}, overrides=rec_ovr,
              desc=_("Seit Jahresbeginn (lokale Zeit) aus den Tagessummen der Minutenmittel. Schwächster Tag nur unter Tagen mit mindestens 12 Stunden Daten.")),
        {**panel("heatmap", _("PV-Ertrag: Tag × Monat (Jahr)"), 10, y, 14, 9, [target(q_daily_year("pv", 60) + '''  |> map(fn: (r) => ({ _time: date.truncate(t: r._time, unit: 1mo), day: string(v: date.monthDay(t: r._time)), _value: r._value }))
  |> group()
  |> pivot(rowKey: ["_time"], columnKey: ["day"], valueColumn: "_value")
  |> sort(columns: ["_time"])''')], "kwatth",
                   opts={**heat_opts, "cellValues": {"unit": "kwatth", "decimals": 1}, "rowsFrame": {"layout": "ge", "value": "kWh"}, "showValue": "auto", "yAxis": {"axisPlacement": "left", "reverse": False, "unit": "none", "decimals": 0}},
                   defaults={"custom": {"hideFrom": {"legend": False, "tooltip": False, "viz": False}, "scaleDistribution": {"type": "linear"}}},
                   desc=_("Tagessumme des PV-Ertrags in kWh, Zeile = Monat, Spalte = Tag im Monat. Leer = keine Daten.")),
         "transformations": [{"id": "organize", "options": {"indexByName": {"_time": 0, **{str(d): d for d in range(1, 32)}}}}]},
    ]
    y += 9

    # ============================================================ PV-Strings
    panels.append(row(_("PV-Strings"), y)); y += 1
    sf = [f"pv{i}{s}" for i in range(1, 5) for s in ("Voltage", "Current")]
    S = _("String")
    S_COL = {1: "yellow", 2: "orange", 3: "light-blue", 4: "purple"}
    string_ovr = [{"matcher": {"id": "byRegexp", "options": f"^{S} {i}$"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}, {"id": "displayName", "value": f"${{s{i}}}"}]} for i, c in S_COL.items()]
    expected_ovr = [{"matcher": {"id": "byRegexp", "options": f"^{_('Erwartet')} {i}$"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}, {"id": "displayName", "value": f"{_('Erwartet')} ${{s{i}}}"},
                     {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [6, 4]}}, {"id": "custom.fillOpacity", "value": 0}, {"id": "custom.lineWidth", "value": 1}]} for i, c in S_COL.items()]
    panels += [
        ts(_("Leistung je String"), 0, y, 12, 9, [target(q_pivot_map(sf, {f"{S} {i}": f"r.pv{i}Voltage * r.pv{i}Current" for i in range(1, 5)}))], "watt", fill=5, stack=True, overrides=string_ovr,
           desc=_("Leistung = Spannung × Strom je Eingang")),
        ts(_("Spannung je String"), 12, y, 6, 9, [target(q_pivot_map(sf, {f"{S} {i}": f"r.pv{i}Voltage" for i in range(1, 5)}))], "volt", fill=0, overrides=string_ovr),
        ts(_("Strom je String"), 18, y, 6, 9, [target(q_pivot_map(sf, {f"{S} {i}": f"r.pv{i}Current" for i in range(1, 5)}))], "amp", fill=0, overrides=string_ovr),
    ]
    y += 9
    panels += [
        stat(_("PV-Eingänge belegt"), 0, y, 4, 4, q_pv_detected(_("belegt")), None, "yellow", 0, desc=_("Eingänge mit mehr als 15 V in den letzten 24 h, von 4 MPPT-Eingängen. Freie Eingänge zeigen etwa 7 V.")),
        panel("stat", _("PV-Eingang 1 bis 4"), 4, y, 20, 4, [target(f'''from(bucket: "{BUCKET}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "nexa" and r._field == "pv{i}Voltage")
  |> max()
  |> keep(columns: ["_value"])
  |> rename(columns: {{_value: "{S} {i}"}})''', r) for i, r in zip(range(1, 5), "ABCD")], "volt",
              opts={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "background", "graphMode": "none", "textMode": "value_and_name", "justifyMode": "center"},
              defaults={"decimals": 1, "color": {"mode": "thresholds"}, "thresholds": thresholds((None, "dark-red"), (15, "green"))},
              overrides=[{"matcher": {"id": "byRegexp", "options": f"^{S} {i}$"}, "properties": [{"id": "displayName", "value": f"${{s{i}}}"}]} for i in range(1, 5)],
              desc=_("Maximale Spannung je Eingang in 24 h. Grün = Panel angeschlossen (> 15 V), rot = frei.")),
    ]
    y += 4

    def q_rank(start):
        """Energie je String (kWh) im Zeitraum, absteigend, mit Anteil an der Summe."""
        pv_filt = " or ".join(f'r._field == "{f}"' for f in sf)
        maps = ", ".join(f'b |> map(fn: (r) => ({{_time: r._time, _start: r._start, _stop: r._stop, string: "{S} {i}", _value: r.pv{i}Voltage * r.pv{i}Current}}))' for i in range(1, 5))
        return HEAD + f'''b = from(bucket: "{BUCKET}")
  |> range(start: {start})
  |> filter(fn: (r) => r._measurement == "nexa" and ({pv_filt}))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.pv1Voltage and exists r.pv1Current)
e = union(tables: [{maps}])
  |> group(columns: ["string", "_start", "_stop"])
  |> sum()
  |> map(fn: (r) => ({{ string: r.string, _value: r._value / 60000.0 }}))
  |> group()
tot = (array.concat(arr: e |> sum() |> findColumn(fn: (key) => true, column: "_value"), v: [0.0]))[0]
e
  |> map(fn: (r) => ({{ "{S}": r.string, "kWh": r._value, "{_("Anteil")}": if tot > 0.0 then r._value / tot * 100.0 else 0.0 }}))
  |> sort(columns: ["kWh"], desc: true)'''

    rank_desc = _("Energie je PV-Eingang (Spannung × Strom, Minutenmittel) im Zeitraum, absteigend sortiert. Anteil an der Summe aller Eingänge.")
    rank_ovr = [{"matcher": {"id": "byName", "options": "kWh"}, "properties": [{"id": "unit", "value": "kwatth"}, {"id": "decimals", "value": 3}, {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "gradient"}}, {"id": "color", "value": {"mode": "fixed", "fixedColor": C_PV}}, {"id": "min", "value": 0}]},
                {"matcher": {"id": "byName", "options": _("Anteil")}, "properties": [{"id": "unit", "value": "percent"}, {"id": "decimals", "value": 0}, {"id": "custom.width", "value": 70}]},
                {"matcher": {"id": "byName", "options": S}, "properties": [{"id": "custom.width", "value": 90}]}]
    panels += [
        panel("table", _(title), x, y, 8, 7, [target(q_rank(start))], None, opts={"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": "kWh", "desc": True}]}, overrides=rank_ovr, desc=rank_desc)
        for title, x, start in (("Top-Strings 24 h", 0, "-24h"), ("Top-Strings 7 Tage", 8, "-7d"), ("Top-Strings 30 Tage", 16, "-30d"))
    ]
    y += 7

    # ============================================================ Tagesspitzen, Sonnenstand, Modell
    panels.append(row(_("Tagesspitzen, Sonnenstand und Modell"), y)); y += 1
    PV_FILT = " or ".join(f'r._field == "{f}"' for f in sf)

    def q_string_union(start, stop, every):
        """Leistung je String als eine Tabelle pro String (Spalte string = "1".."4")."""
        maps = ", ".join(f'b |> map(fn: (r) => ({{_time: r._time, string: "{i}", _value: r.pv{i}Voltage * r.pv{i}Current}}))' for i in range(1, 5))
        return HEAD + f'''b = from(bucket: "{BUCKET}")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "nexa" and ({PV_FILT}))
  |> aggregateWindow(every: {every}, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.pv1Voltage and exists r.pv1Current)
u = union(tables: [{maps}])
  |> group(columns: ["string"])
'''

    q_peaks_table = q_string_union("-30d", "now()", "1m") + f'''sun = from(bucket: "{BUCKET}")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "sun")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["_time", "azimuth", "elevation"])
  |> group()
peaks = u
  |> window(every: 1d)
  |> max()
  |> filter(fn: (r) => r._value > 5.0)
  |> group()
join.left(left: peaks, right: sun, on: (l, r) => l._time == r._time, as: (l, r) => ({{
    "{_("Tag")}": l._start, "{_("String")}": "{_("String")} " + l.string, "{_("Spitze")}": l._value, "{_("Uhrzeit")}": l._time,
    "{_("Sonnenazimut")}": r.azimuth, "{_("Sonnenhöhe")}": r.elevation }}))
  |> sort(columns: ["{_("Tag")}", "{_("String")}"], desc: true)'''

    q_peaks_bars = q_string_union("-30d", "now()", "1m") + '''u
  |> aggregateWindow(every: 1d, fn: max, createEmpty: false, timeSrc: "_start")
  |> timeShift(duration: 12h)
  |> keep(columns: ["_time", "_value", "string"])'''

    def q_heat(expr):
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "nexa" and ({PV_FILT}))
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false, timeSrc: "_start")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.pv1Voltage and exists r.pv1Current)
  |> map(fn: (r) => ({{ _time: date.truncate(t: r._time, unit: 1d), hour: string(v: date.hour(t: r._time)), _value: {expr} }}))
  |> filter(fn: (r) => r._value > 2.0)
  |> group()
  |> pivot(rowKey: ["_time"], columnKey: ["hour"], valueColumn: "_value")
  |> sort(columns: ["_time"])'''
    heat_tf = [{"id": "organize", "options": {"indexByName": {"_time": 0, **{str(h): h + 1 for h in range(24)}}}}]

    q_strongest = HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "nexa" and ({PV_FILT}))
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false, timeSrc: "_start")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.pv1Voltage and exists r.pv1Current)
  |> map(fn: (r) => {{
      p1 = r.pv1Voltage * r.pv1Current
      p2 = r.pv2Voltage * r.pv2Current
      p3 = r.pv3Voltage * r.pv3Current
      p4 = r.pv4Voltage * r.pv4Current
      best = if p1 >= p2 and p1 >= p3 and p1 >= p4 then "1" else if p2 >= p3 and p2 >= p4 then "2" else if p3 >= p4 then "3" else "4"
      top = if p1 >= p2 and p1 >= p3 and p1 >= p4 then p1 else if p2 >= p3 and p2 >= p4 then p2 else if p3 >= p4 then p3 else p4
      return {{ _time: r._time, _value: if top > 5.0 then best else "-" }}
    }})
  |> keep(columns: ["_time", "_value"])'''

    q_expected = HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "pv_model" and r._field == "expected_w")
  |> keep(columns: ["_time", "_value", "string"])'''

    def q_sun(field, label):
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "sun" and r._field == "{field}")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
  |> rename(columns: {{_value: "{label}"}})'''

    def q_xy(i):
        return HEAD + f'''sun = from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "sun" and r._field == "azimuth")
  |> aggregateWindow(every: 5m, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
  |> rename(columns: {{_value: "azimuth"}})
pv = from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "nexa" and (r._field == "pv{i}Voltage" or r._field == "pv{i}Current"))
  |> aggregateWindow(every: 5m, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.pv{i}Voltage and exists r.pv{i}Current)
  |> map(fn: (r) => ({{ _time: r._time, power: r.pv{i}Voltage * r.pv{i}Current }}))
  |> keep(columns: ["_time", "power"])
join.inner(left: sun, right: pv, on: (l, r) => l._time == r._time, as: (l, r) => ({{ azimuth: l.azimuth, power: r.power }}))
  |> filter(fn: (r) => r.power > 1.0)'''

    panels += [
        panel("table", _("Tagesspitze je String (30 Tage)"), 0, y, 10, 10, [target(q_peaks_table)], None,
              opts={"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": _("Tag"), "desc": True}]},
              defaults={"custom": {"align": "auto", "cellOptions": {"type": "auto"}}},
              overrides=[{"matcher": {"id": "byName", "options": _("Tag")}, "properties": [{"id": "unit", "value": "time: DD.MM."}, {"id": "custom.width", "value": 70}]},
                         {"matcher": {"id": "byName", "options": _("Uhrzeit")}, "properties": [{"id": "unit", "value": "time: HH:mm"}, {"id": "custom.width", "value": 70}]},
                         {"matcher": {"id": "byName", "options": _("Spitze")}, "properties": [{"id": "unit", "value": "watt"}, {"id": "decimals", "value": 0}, {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "gradient"}}, {"id": "color", "value": {"mode": "fixed", "fixedColor": C_PV}}, {"id": "min", "value": 0}]},
                         {"matcher": {"id": "byName", "options": _("Sonnenazimut")}, "properties": [{"id": "unit", "value": "degree"}, {"id": "decimals", "value": 0}]},
                         {"matcher": {"id": "byName", "options": _("Sonnenhöhe")}, "properties": [{"id": "unit", "value": "degree"}, {"id": "decimals", "value": 0}]}],
              desc=_("Höchste Minutenleistung je String und Tag mit Uhrzeit und Sonnenstand in diesem Moment. Nur Strings mit mehr als 5 W.")),
        ts(_("Tagesspitzen je String (30 Tage)"), 10, y, 14, 10, [target(q_peaks_bars)], "watt", bars=True, overrides=string_ovr,
           defaults_extra={"displayName": S + " ${__field.labels.string}"}, desc=_("Höchste Minutenleistung je Tag, Balken auf Tagesmitte.")),
    ]
    y += 10
    heat_desc = _("Stundenmittel der Leistung, Zeile = Tag, Spalte = Stunde (lokale Zeit). Dunkel = wenig, hell = viel. Wandernde Muster zeigen Ausrichtung und Verschattung.")
    heat_def = {"custom": {"hideFrom": {"legend": False, "tooltip": False, "viz": False}, "scaleDistribution": {"type": "linear"}}}
    panels += [
        {**panel("heatmap", _("PV gesamt: Stunde × Tag (30 Tage)"), 0, y, 12, 9, [target(q_heat(PV_EXPR))], "watt", opts=heat_opts, defaults=heat_def, desc=heat_desc), "transformations": heat_tf},
        {**panel("heatmap", _("String $string: Stunde × Tag (30 Tage)"), 12, y, 12, 9, [target(q_heat("r.pv${string}Voltage * r.pv${string}Current"))], "watt", opts=heat_opts, defaults=heat_def, desc=heat_desc), "transformations": heat_tf},
    ]
    y += 9
    panels += [
        panel("state-timeline", _("Stärkster String je Stunde"), 0, y, 24, 5, [target(q_strongest)], None,
              opts={"showValue": "auto", "rowHeight": 0.9, "mergeValues": True, "alignValue": "center", "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}, "tooltip": {"mode": "single"}},
              defaults={"displayName": S, "color": {"mode": "thresholds"}, "custom": {"fillOpacity": 80, "lineWidth": 0},
                        "mappings": [{"type": "value", "options": {**{str(i): {"text": f"{S} {i}", "color": c, "index": i} for i, c in S_COL.items()}, "-": {"text": _("Nacht"), "color": "dark-gray", "index": 0}}}]},
              desc=_("Welcher String im Stundenmittel am meisten liefert. Ost-Strings führen morgens, West-Strings nachmittags. Grau = unter 5 W.")),
    ]
    y += 5
    panels += [
        ts(_("Gemessen vs. erwartet je String"), 0, y, 16, 10, [
            target(q_pivot_map(sf, {f"{S} {i}": f"r.pv{i}Voltage * r.pv{i}Current" for i in range(1, 5)}), "A"),
            target(q_expected, "B")], "watt", fill=8,
           overrides=string_ovr + expected_ovr + [{"matcher": {"id": "byFrameRefID", "options": "B"}, "properties": [{"id": "displayName", "value": _("Erwartet") + " ${__field.labels.string}"}]}],
           desc=_("Gemessen: Spannung × Strom je String. Erwartet: Open-Meteo-Strahlung (DNI/DHI/GHI) auf die konfigurierte Modulfläche umgerechnet, mal Wp mal Performance-Ratio (Sidecar weather). Ohne Neigung/Ausrichtung auf der Einstellungsseite bleibt „Erwartet“ leer. Wiederkehrende Einbrüche zur gleichen Uhrzeit = Verschattung.")),
        ts(_("Sonnenstand"), 16, y, 8, 10, [target(q_sun("elevation", _("Höhe")), "A"), target(q_sun("azimuth", _("Azimut")), "B")], "degree", fill=15,
           overrides=[color_override(_("Höhe"), C_PV), color_override(_("Azimut"), "blue"),
                      {"matcher": {"id": "byName", "options": _("Azimut")}, "properties": [{"id": "custom.axisPlacement", "value": "right"}, {"id": "custom.fillOpacity", "value": 0}, {"id": "min", "value": 0}, {"id": "max", "value": 360}]},
                      {"matcher": {"id": "byName", "options": _("Höhe")}, "properties": [{"id": "min", "value": -20}, {"id": "max", "value": 70}]}],
           desc=_("Azimut 0 = Nord, 90 = Ost, 180 = Süd, 270 = West. Höhe über dem Horizont, jede Minute vom Sidecar weather berechnet (NOAA).")),
    ]
    y += 10
    panels += [
        panel("xychart", _("Leistung über Sonnenazimut"), 0, y, 16, 10, [target(q_xy(i), r) for i, r in zip(range(1, 5), "ABCD")], None,
              opts={"mapping": "auto", "series": [{"x": {"matcher": {"id": "byName", "options": "azimuth"}}, "y": {"matcher": {"id": "byName", "options": "power"}}}],
                    "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}, "tooltip": {"mode": "single"}},
              defaults={"custom": {"show": "points", "pointSize": {"fixed": 4}, "pointShape": "circle", "pointStrokeWidth": 1, "fillOpacity": 60, "axisPlacement": "auto", "axisLabel": "", "axisGridShow": True}},
              overrides=[{"matcher": {"id": "byFrameRefID", "options": r}, "properties": [{"id": "displayName", "value": f"{S} {i}"}, {"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]} for i, r, c in zip(range(1, 5), "ABCD", S_COL.values())]
                        + [{"matcher": {"id": "byName", "options": "azimuth"}, "properties": [{"id": "unit", "value": "degree"}, {"id": "min", "value": 60}, {"id": "max", "value": 300}, {"id": "displayName", "value": _("Sonnenazimut")}]},
                           {"matcher": {"id": "byName", "options": "power"}, "properties": [{"id": "unit", "value": "watt"}, {"id": "min", "value": 0}]}],
              desc=_("Jeder Punkt ein 5-Minuten-Mittel im gewählten Zeitraum. Der Schwerpunkt der Punktwolke zeigt, wohin ein String schaut; ein Einbruch bei einem festen Azimut ist ein Hindernis. Für ein Sonnenbahn-Polardiagramm siehe die GroLo-Website.")),
        panel("stat", _("Sonne jetzt"), 16, y, 8, 4, [target(q_last_named("azimuth", _("Azimut"), "sun", "-10m"), "A"), target(q_last_named("elevation", _("Höhe"), "sun", "-10m"), "B")], "degree",
              opts={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "value", "graphMode": "none", "textMode": "value_and_name", "justifyMode": "center"},
              defaults={"decimals": 1, "color": {"mode": "fixed", "fixedColor": C_PV}}, overrides=[color_override(_("Azimut"), "blue")]),
        panel("table", _("Geschätzte Ausrichtung je String"), 16, y + 4, 8, 6, [target(f'''from(bucket: "{BUCKET}")
  |> range(start: -3d)
  |> filter(fn: (r) => r._measurement == "pv_fit")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> group()
  |> map(fn: (r) => ({{ "{S}": r.string, "{_("Azimut")}": r.azimuth, "{_("Neigung")}": r.tilt, "Wp": r.wp, "R²": r.r2, "{_("Stunden")}": r.hours, "{_("Güte")}": r.quality, "{_("Stand")}": r._time }}))
  |> sort(columns: ["{S}"])''')], None,
              opts={"showHeader": True, "cellHeight": "sm"},
              overrides=[{"matcher": {"id": "byName", "options": _("Azimut")}, "properties": [{"id": "unit", "value": "degree"}, {"id": "decimals", "value": 0}]},
                         {"matcher": {"id": "byName", "options": _("Neigung")}, "properties": [{"id": "unit", "value": "degree"}, {"id": "decimals", "value": 0}]},
                         {"matcher": {"id": "byName", "options": "R²"}, "properties": [{"id": "decimals", "value": 2}]},
                         {"matcher": {"id": "byName", "options": _("Stand")}, "properties": [{"id": "unit", "value": "time: DD.MM. HH:mm"}]},
                         {"matcher": {"id": "byName", "options": _("Güte")}, "properties": [{"id": "mappings", "value": [{"type": "value", "options": {"ok": {"text": _("gut"), "color": "green"}, "uncertain": {"text": _("unsicher"), "color": "orange"}, "insufficient": {"text": _("noch nicht bestimmbar"), "color": "dark-gray"}, "unused": {"text": _("kein Modul"), "color": "dark-gray"}}}]}, {"id": "custom.cellOptions", "value": {"type": "color-text"}}]}],
              desc=_("Täglich (und per Knopf auf der Einstellungsseite) schätzt der Sidecar weather Neigung, Azimut und Wp jedes Strings aus den Stundenkurven der letzten 30 Tage gegen das Einstrahlungsmodell. Braucht mehrere sonnige Tage. Übernehmen auf der Einstellungsseite unter „Standort und Module“, dann füllt sich „Erwartet“.")),
    ]
    y += 10
    panels += [
        panel("table", _("Empfehlungen je String (Jahresmodell und Verschattung)"), 0, y, 24, 6, [target(f'''from(bucket: "{BUCKET}")
  |> range(start: -3d)
  |> filter(fn: (r) => r._measurement == "pv_advice")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> group()
  |> map(fn: (r) => ({{ "{S}": r.string, "{_("Basis")}": r.basis, "{_("Azimut")}": r.azimuth, "{_("Neigung")}": r.tilt, "kWh/kWp": r.kwh_kwp, "{_("vom Optimum")}": r.pct_of_best,
      "{_("Optimum")}": string(v: int(v: r.best_azimuth)) + "° / " + string(v: int(v: r.best_tilt)) + "°", "{_("Gewinn Optimum")}": r.best_gain_pct,
      "{_("Gleiche Richtung, Neigung")}": string(v: int(v: r.same_azimuth_tilt)) + "°", "{_("Gewinn")}": r.same_azimuth_gain_pct,
      "{_("Senkrecht nach")}": string(v: int(v: r.vertical_azimuth)) + "°", "{_("Gewinn senkrecht")}": r.vertical_gain_pct, "{_("Gewinn flach")}": r.flat_gain_pct,
      "{_("Winteranteil")}": r.winter_share_pct, "{_("Verschattung")}": r.shading_loss_pct, "{_("Zonen")}": r.shading_zones, "{_("Stand")}": r._time }}))
  |> sort(columns: ["{S}"])''')], None,
              opts={"showHeader": True, "cellHeight": "sm"},
              overrides=[{"matcher": {"id": "byRegexp", "options": f"^({_('Azimut')}|{_('Neigung')})$"}, "properties": [{"id": "unit", "value": "degree"}, {"id": "decimals", "value": 0}]},
                         {"matcher": {"id": "byRegexp", "options": f"^({_('vom Optimum')}|{_('Winteranteil')}|{_('Verschattung')})$"}, "properties": [{"id": "unit", "value": "percent"}, {"id": "decimals", "value": 0}]},
                         {"matcher": {"id": "byRegexp", "options": f"^{_('Gewinn')}.*"}, "properties": [{"id": "unit", "value": "percent"}, {"id": "decimals", "value": 0}, {"id": "custom.cellOptions", "value": {"type": "color-text"}}, {"id": "color", "value": {"mode": "thresholds"}}, {"id": "thresholds", "value": thresholds((None, "text"), (5, "yellow"), (20, "green"))}]},
                         {"matcher": {"id": "byName", "options": _("Verschattung")}, "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}}, {"id": "color", "value": {"mode": "thresholds"}}, {"id": "thresholds", "value": thresholds((None, "green"), (5, "orange"), (15, "red"))}]},
                         {"matcher": {"id": "byName", "options": _("Stand")}, "properties": [{"id": "unit", "value": "time: DD.MM. HH:mm"}]},
                         {"matcher": {"id": "byName", "options": _("Basis")}, "properties": [{"id": "mappings", "value": [{"type": "value", "options": {"config": {"text": _("konfiguriert")}, "fit": {"text": _("geschätzt")}}}]}]}],
              desc=_("Täglich mit der Schätzung berechnet. Jahresmodell aus dem Open-Meteo-Archiv für den Standort: Ertrag der aktuellen Ausrichtung (konfiguriert, sonst geschätzt) in kWh je kWp, Anteil am Optimum und Gewinn durch Alternativen (gleiche Richtung mit bester Neigung, senkrecht mit bestem Azimut, flach, Optimum). Verschattung: Anteil der Sonnenstunden-Energie, der in Sonnenrichtungen fehlt, in denen die Messung weit unter dem Modell bleibt; Zonen mit Uhrzeiten auf der Einstellungsseite und der Website.")),
    ]
    y += 6

    # ============================================================ Wetter
    panels.append(row(_("Wetter"), y)); y += 1
    def q_weather_last(field, rng="-2h"):
        return f'''from(bucket: "{BUCKET}")
  |> range(start: {rng})
  |> filter(fn: (r) => r._measurement == "weather" and r._field == "{field}")
  |> last()
  |> keep(columns: ["_time", "_value"])'''
    def q_weather_series(field, label, measurement="weather"):
        return HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "{field}")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
  |> rename(columns: {{_value: "{label}"}})'''
    cond_map = [{"type": "value", "options": {str(k): {"text": _(v)} for k, v in {0: "Klar", 1: "Überwiegend klar", 2: "Teilweise bewölkt", 3: "Bedeckt", 45: "Nebel", 48: "Reifnebel",
                 51: "Sprühregen", 53: "Sprühregen", 55: "Sprühregen", 61: "Leichter Regen", 63: "Regen", 65: "Starker Regen", 71: "Schneefall", 73: "Schneefall", 75: "Schneefall",
                 80: "Regenschauer", 81: "Regenschauer", 82: "Regenschauer", 95: "Gewitter", 96: "Gewitter", 99: "Gewitter"}.items()}}]
    panels += [
        stat(_("Außentemperatur"), 0, y, 4, 4, q_weather_last("temperature"), "celsius", None, 1, thr=thresholds((None, "blue"), (5, "light-blue"), (15, "green"), (25, "orange"), (30, "red")),
             desc=_("Open-Meteo für den Anlagenstandort, alle 10 Minuten")),
        stat(_("Wetterzustand"), 4, y, 4, 4, q_weather_last("weather_code"), None, "text", mapping=cond_map, desc=_("WMO-Wettercode von Open-Meteo")),
        stat(_("Bewölkung"), 8, y, 4, 4, q_weather_last("cloud_cover"), "percent", None, 0, thr=thresholds((None, "yellow"), (40, "light-yellow"), (70, "blue"), (90, "dark-blue"))),
        stat(_("Globalstrahlung"), 12, y, 4, 4, q_weather_last("shortwave_radiation"), "suffix: W/m²", C_PV, 0, desc=_("Kurzwellige Einstrahlung auf die Horizontale in W/m², Referenz für die PV-Leistung")),
        panel("stat", _("Sonnenaufgang / -untergang"), 16, y, 4, 4, [target(q_last_named("sunrise", "↑", "weather", "-2h"), "A"), target(q_last_named("sunset", "↓", "weather", "-2h"), "B")], None,
              opts={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/.*/", "values": False}, "colorMode": "none", "graphMode": "none", "textMode": "value_and_name", "justifyMode": "center"},
              defaults={"color": {"mode": "fixed", "fixedColor": "text"}}, desc=_("Heute, lokale Zeit")),
        stat(_("Sonnenschein heute"), 20, y, 4, 4, q_weather_last("sunshine_hours_today"), "suffix: h", C_PV, 1, desc=_("Prognostizierte Sonnenscheindauer des Tages")),
    ]
    y += 4
    panels += [
        panel("stat", _("Strahlungssumme heute / morgen"), 0, y, 4, 9, [target(q_last_named("radiation_sum_today", _("heute"), "weather", "-2h"), "A"), target(q_last_named("radiation_sum_tomorrow", _("morgen"), "weather", "-2h"), "B")], None,
              opts={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "value", "graphMode": "none", "textMode": "value_and_name", "justifyMode": "center", "orientation": "vertical"},
              defaults={"decimals": 1, "unit": "suffix: MJ/m²", "color": {"mode": "fixed", "fixedColor": C_PV}}, desc=_("Tagessumme der Globalstrahlung in MJ/m² laut Vorhersage (1 MJ/m² ≈ 0,28 kWh/m²)")),
        panel("timeseries", _("Globalstrahlung und PV-Leistung"), 4, y, 10, 9, [
            target(q_weather_series("shortwave_radiation", _("Globalstrahlung W/m²")), "A"),
            target(q_flow_series({_("PV-Leistung W"): "pv"}), "B")], None,
              opts={"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True, "calcs": ["mean", "max"]}, "tooltip": {"mode": "multi", "sort": "none"}},
              defaults={"custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 8, "gradientMode": "opacity", "showPoints": "never", "spanNulls": True}},
              overrides=[color_override(_("Globalstrahlung W/m²"), "orange"),
                         {"matcher": {"id": "byName", "options": _("PV-Leistung W")}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": C_PV}}, {"id": "custom.axisPlacement", "value": "right"}, {"id": "unit", "value": "watt"}]}],
              desc=_("Verhältnis von PV-Leistung zu Einstrahlung zeigt Verschattung, Ausrichtung und Verschmutzung. Strahlung links (W/m²), PV rechts (W).")),
        ts(_("Bewölkung und Temperatur"), 14, y, 10, 9, [target(q_weather_series("cloud_cover", _("Bewölkung %")), "A"), target(q_weather_series("temperature", _("Temperatur °C")), "B")], None, fill=5,
           overrides=[color_override(_("Bewölkung %"), "blue"), {"matcher": {"id": "byName", "options": _("Temperatur °C")}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}, {"id": "custom.axisPlacement", "value": "right"}, {"id": "unit", "value": "celsius"}]}]),
    ]
    y += 9
    panels += [
        panel("barchart", _("Vorhersage 48 h: Einstrahlung und Bewölkung"), 0, y, 24, 8, [target(HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: -1h, stop: 48h)
  |> filter(fn: (r) => r._measurement == "weather_forecast" and (r._field == "shortwave_radiation" or r._field == "cloud_cover"))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> map(fn: (r) => {{
      wd = date.weekDay(t: r._time, location: location)
      names = ["{_("So")}", "{_("Mo")}", "{_("Di")}", "{_("Mi")}", "{_("Do")}", "{_("Fr")}", "{_("Sa")}"]
      h = date.hour(t: r._time, location: location)
      return {{ _time: r._time, "{_("Stunde")}": names[wd] + " " + (if h < 10 then "0" else "") + string(v: h) + ":00", "{_("Globalstrahlung W/m²")}": r.shortwave_radiation, "{_("Bewölkung %")}": r.cloud_cover }}
    }})
  |> keep(columns: ["{_("Stunde")}", "{_("Globalstrahlung W/m²")}", "{_("Bewölkung %")}"])''')], None,
              opts={"xField": _("Stunde"), "orientation": "auto", "barWidth": 0.8, "groupWidth": 0.7, "showValue": "never", "stacking": "none",
                    "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}, "tooltip": {"mode": "multi", "sort": "none"}, "xTickLabelRotation": -45, "xTickLabelSpacing": 100},
              defaults={"color": {"mode": "palette-classic"}, "custom": {"fillOpacity": 80, "lineWidth": 1}},
              overrides=[color_override(_("Globalstrahlung W/m²"), "orange"), color_override(_("Bewölkung %"), "blue")],
              desc=_("Stündliche Open-Meteo-Vorhersage ab jetzt. Zeitraum des Dashboards ist hier ohne Wirkung, das Panel zeigt immer die nächsten 48 Stunden.")),
    ]
    y += 8

    # ============================================================ Batterie und Technik
    panels.append(row(_("Batterie und Technik"), y)); y += 1
    B = _("Batterie")
    panels += [
        ts(_("Temperaturen"), 0, y, 12, 9, [
            target(q_series("systemTemp", _("System")), "A"),
            target(q_series("battery1Temp", f"{B} 1"), "B"), target(q_series("battery2Temp", f"{B} 2"), "G"),
            target(q_series("battery3Temp", f"{B} 3"), "H"), target(q_series("battery4Temp", f"{B} 4"), "I"),
            target(q_series("pv1Temp", "PV1"), "C"), target(q_series("pv2Temp", "PV2"), "D"), target(q_series("pv3Temp", "PV3"), "E"), target(q_series("pv4Temp", "PV4"), "F"),
        ], "celsius", fill=0),
        ts(_("Zellspannung (min / max)"), 12, y, 8, 9, [target(q_series("maxCellVoltage", "max"), "A"), target(q_series("minCellVoltage", "min"), "B")], "volt", fill=0,
           desc=_("Ein großer Abstand deutet auf unausgeglichene Zellen hin")),
        stat_field(_("Zyklen"), 20, y, 4, 3, "batteryCycles", None, "blue", 0),
        stat_field(_("Gesundheit (SoH)"), 20, y + 3, 4, 3, "batterySoh", "percent", None, 0, thr=thresholds((None, "red"), (80, "orange"), (90, "green"))),
        stat_field(_("Batteriepacks"), 20, y + 6, 4, 3, "batteryPackageQuantity", None, "blue", 0),
    ]
    y += 9
    panels += [
        stat_field(_("Entlade-Grenze"), 0, y, 4, 4, "dischargeSocLimit", "percent", "orange", 0, desc=_("Unter diesen SoC entlädt die Batterie nicht")),
        stat_field(_("Lade-Grenze"), 4, y, 4, 4, "chargeSocLimit", "percent", "green", 0),
        stat_field(_("Netzspannung"), 8, y, 4, 4, "onGridVoltage", "volt", "purple", 1, desc=_("Register 115. Nahe 0 V, wenn der NEXA vom Netz getrennt ist.")),
        panel("stat", _("Geräteregister zum Vergleich"), 12, y, 4, 4, [target(q_last_named("pac", "pac (5)"), "A"), target(q_last_named("totalBatteryPackChargingPower", _("Batterie") + " (11)"), "B"), target(q_last_named("ppv", "ppv (7)"), "C")], "watt",
              opts={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "none", "graphMode": "none", "textMode": "value_and_name", "justifyMode": "center"},
              defaults={"color": {"mode": "fixed", "fixedColor": "purple"}}, desc=_("Rohwerte der Geräteregister. pac und Batterieleistung bleiben auf aktueller Firmware bei 0, deshalb rechnet das Dashboard mit Register 116 und den Strings.")),
        stat(_("Zellspannung Differenz"), 16, y, 4, 4, HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "nexa" and (r._field == "maxCellVoltage" or r._field == "minCellVoltage"))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.maxCellVoltage and exists r.minCellVoltage)
  |> last(column: "maxCellVoltage")
  |> map(fn: (r) => ({{ _time: r._time, _value: r.maxCellVoltage - r.minCellVoltage }}))
  |> keep(columns: ["_time", "_value"])''', "volt", None, 3, thr=thresholds((None, "green"), (0.05, "orange"), (0.1, "red"))),
        stat(_("Letzte Nachricht"), 20, y, 4, 4, f'''from(bucket: "{BUCKET}")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "nexa" and r._field == "ppv")
  |> last()
  |> map(fn: (r) => ({{ _time: r._time, _value: float(v: uint(v: r._time)) / 1000000.0 }}))
  |> keep(columns: ["_time", "_value"])''', "dateTimeFromNow", "text", desc=_("Zeitpunkt des letzten Datensatzes vom Dongle")),
    ]
    y += 4
    panels += [
        stat(_("Firmware NEXA (Reg. 119/120)"), 0, y, 6, 4, HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "nexa" and (r._field == "fw_version_part_1" or r._field == "fw_version_part_2" or r._field == "fw_version_part_3" or r._field == "fw_version_part_4"))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.fw_version_part_1 and exists r.fw_version_part_2 and exists r.fw_version_part_3 and exists r.fw_version_part_4)
  |> last(column: "fw_version_part_1")
  |> map(fn: (r) => ({{ _time: r._time, _value: string(v: int(v: r.fw_version_part_1)) + "." + string(v: int(v: r.fw_version_part_2)) + "." + string(v: int(v: r.fw_version_part_3)) + "." + string(v: int(v: r.fw_version_part_4)) }}))
  |> keep(columns: ["_time", "_value"])''', None, "text", desc=_("Rohteile aus den Registern, Zusammensetzung laut Growatt unbekannt")),
        stat(_("Dongle Firmware / Modell"), 6, y, 6, 4, f'''from(bucket: "{BUCKET}")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "dongle" and r._field == "wifi_signal")
  |> last()
  |> map(fn: (r) => ({{ _time: r._time, _value: r.model_id + "  SW " + r.sw_version + "  HW " + r.hw_version }}))
  |> keep(columns: ["_time", "_value"])''', None, "text"),
        stat(_("WLAN-Signal Dongle"), 12, y, 4, 4, q_last("wifi_signal", "dongle", "-30d"), "dBm", None, 0, thr=thresholds((None, "red"), (-80, "orange"), (-67, "green")),
             desc=_("Wird nur beim Verbindungsaufbau des Dongles gemeldet")),
        stat_field(_("Batteriepacks erkannt"), 16, y, 4, 4, "batteryPackageQuantity", None, "blue", 0),
        stat_field(_("Zellspannung min"), 20, y, 4, 4, "minCellVoltage", "volt", "green", 3),
    ]
    y += 4
    P = _("Pack")
    panels += [
        ts(_("SoC je Batteriepack"), 0, y, 12, 8, [target(q_series(f"battery{i}Soc", f"{P} {i}"), r) for i, r in zip(range(1, 5), "ABCD")], "percent", fill=5, mn=0, mx=100),
        panel("stat", _("Temperatur je Pack"), 12, y, 12, 4, [target(q_last_named(f"battery{i}Temp", f"{P} {i}"), r) for i, r in zip(range(1, 5), "ABCD")], "celsius",
              opts={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "value", "graphMode": "none", "textMode": "value_and_name", "justifyMode": "center"},
              defaults={"decimals": 1, "color": {"mode": "thresholds"}, "thresholds": temp_thr}),
        panel("stat", _("Seriennummern Erweiterungspacks"), 12, y + 4, 12, 4, [target(q_last_named(f"bat{i}_serial", f"{P} {i}", rng="-7d"), r) for i, r in zip(range(2, 5), "ABC")], None,
              opts={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/.*/", "values": False}, "colorMode": "none", "graphMode": "none", "textMode": "value_and_name", "justifyMode": "center"},
              defaults={"color": {"mode": "fixed", "fixedColor": "text"}},
              desc=_("Pack 1 ist das Hauptgerät mit der Geräteseriennummer. Leer = kein Erweiterungspack.")),
    ]
    y += 8

    # ============================================================ Forschung
    panels.append(row(_("Unbekannte Register (Forschung)"), y)); y += 1
    panels += [
        ts(_("Unbekannte Eingangsregister, Verlauf (nur ≠ 0)"), 0, y, 16, 10, [target(HEAD + f'''from(bucket: "{BUCKET}")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "raw" and r.kind == "raw_input")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> filter(fn: (r) => r._value != 0.0)
  |> keep(columns: ["_time", "_value", "_field"])''')], None, fill=0, desc=_("Rohwerte der Register, die die Bridge nicht kennt. 30000 = Offset für 0 bei vorzeichenbehafteten Werten."),
           defaults_extra={"displayName": "${__field.labels._field}"}),
        panel("table", _("Halteregister-Dump (unbekannt, ≠ 0)"), 16, y, 8, 10, [target(f'''from(bucket: "{BUCKET}")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "raw" and r.kind == "raw_holding")
  |> last()
  |> filter(fn: (r) => r._value != 0)
  |> keep(columns: ["_field", "_value"])
  |> rename(columns: {{_field: "{_("Register")}", _value: "{_("Wert")}"}})''')], None,
              opts={"showHeader": True, "sortBy": [{"displayName": _("Register"), "desc": False}]}, desc=_("Kommt stündlich vom Gerät (Nachricht 0x0103).")),
    ]

    return {
        "uid": "nexa2000" if lang == "en" else "nexa2000-de",
        "title": "GroLo · Growatt NEXA 2000" + (" (DE)" if lang == "de" else ""),
        "tags": ["solar", "growatt", "nexa", "grolo", lang],
        "timezone": "browser", "editable": True, "graphTooltip": 1, "refresh": "30s",
        "time": {"from": "now-24h", "to": "now"}, "timepicker": {"refresh_intervals": ["10s", "30s", "1m", "5m", "15m"]},
        "links": [
            {"title": _("Einstellungen"), "type": "link", "url": "/public/grolo/index.html", "icon": "external link", "tooltip": _("Einstellungsseite (GroLo)"), "targetBlank": True, "asDropdown": False},
            {"title": "Deutsch" if lang == "en" else "English", "type": "link", "url": "/d/nexa2000-de" if lang == "en" else "/d/nexa2000", "icon": "external link", "tooltip": "", "targetBlank": False, "asDropdown": False, "keepTime": True},
        ],
        "schemaVersion": 39, "version": 1, "panels": panels, "annotations": {"list": []},
        "templating": {"list": [{"type": "custom", "name": "string", "label": _("String") + " (Heatmap)", "query": "1,2,3,4", "current": {"text": "1", "value": "1", "selected": True},
                                 "options": [{"text": str(i), "value": str(i), "selected": i == 1} for i in range(1, 5)], "hide": 0, "includeAll": False, "multi": False}]
                       + [{"type": "query", "name": f"s{i}", "label": f"{_('String')} {i}", "hide": 2, "refresh": 1, "datasource": DS, "includeAll": False, "multi": False,
                           "query": {"query": f'''import "array"
array.from(rows: [{{_value: (array.concat(arr: from(bucket: "{BUCKET}") |> range(start: -10y) |> filter(fn: (r) => r._measurement == "string_names" and r._field == "s{i}") |> last() |> findColumn(fn: (key) => true, column: "_value"), v: ["{_('String')} {i}"]))[0]}}])'''},
                           "current": {"text": f"{_('String')} {i}", "value": f"{_('String')} {i}", "selected": True}, "options": []} for i in range(1, 5)]},
    }


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    old = os.path.join(OUT_DIR, "nexa.json")
    if os.path.exists(old):
        os.remove(old)
    for lang in ("en", "de"):
        d = build(lang)
        out = os.path.join(OUT_DIR, f"nexa-{lang}.json")
        with open(out, "w") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
        print(f"{out}: {len(d['panels'])} Panels")
