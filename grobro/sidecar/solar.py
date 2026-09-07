#!/usr/bin/env python3
"""solar: Sonnenstand und Einstrahlung auf geneigte Flächen, ohne Zusatzbibliotheken.

  sun_position(ts, lat, lon)          -> (Azimut in Grad, 0 = Nord, 90 = Ost, 180 = Süd; Höhe über Horizont in Grad)
  poa_irradiance(ghi, dni, dhi, az, el, tilt, azimuth) -> W/m² auf einer Fläche mit Neigung tilt und Ausrichtung azimuth
  string_config()                     -> konfigurierte Strings aus STRING<n>_TILT / STRING<n>_AZIMUTH / STRING<n>_WP

Sonnenstand nach dem NOAA-Algorithmus (Genauigkeit besser als 0,1°), Einstrahlung nach dem isotropen Himmelsmodell:
  direkt  = DNI · cos(Einfallswinkel)
  diffus  = DHI · (1 + cos tilt) / 2
  Boden   = GHI · Albedo · (1 − cos tilt) / 2
Die erwartete Leistung eines Strings ist Einstrahlung / 1000 · Wp · Performance-Ratio (Standard 0,85).
"""
import math
import os

ALBEDO = 0.2
PR_DEFAULT = 0.85


def sun_position(ts, lat, lon):
    """Azimut (0..360, von Nord über Ost) und Höhe (Grad) der Sonne zum Unix-Zeitpunkt ts (UTC)."""
    jd = ts / 86400.0 + 2440587.5
    t = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + t * (36000.76983 + 0.0003032 * t)) % 360.0
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    mr = math.radians(m)
    c = (math.sin(mr) * (1.914602 - t * (0.004817 + 0.000014 * t)) + math.sin(2 * mr) * (0.019993 - 0.000101 * t) + math.sin(3 * mr) * 0.000289)
    true_long = l0 + c
    omega = 125.04 - 1934.136 * t
    app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    eps0 = 23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
    eps = eps0 + 0.00256 * math.cos(math.radians(omega))
    decl = math.asin(math.sin(math.radians(eps)) * math.sin(math.radians(app_long)))
    y = math.tan(math.radians(eps / 2.0)) ** 2
    l0r = math.radians(l0)
    eqtime = 4.0 * math.degrees(y * math.sin(2 * l0r) - 2 * e * math.sin(mr) + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
                                - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * mr))
    minutes_utc = (ts % 86400) / 60.0
    tst = (minutes_utc + eqtime + 4.0 * lon) % 1440.0
    ha = tst / 4.0 - 180.0
    if ha < -180.0:
        ha += 360.0
    latr = math.radians(lat); har = math.radians(ha)
    cos_zen = math.sin(latr) * math.sin(decl) + math.cos(latr) * math.cos(decl) * math.cos(har)
    cos_zen = max(-1.0, min(1.0, cos_zen))
    zen = math.acos(cos_zen)
    el = 90.0 - math.degrees(zen)
    sin_zen = math.sin(zen)
    if abs(sin_zen) < 1e-9:
        az = 180.0
    else:
        cos_az = (math.sin(latr) * cos_zen - math.sin(decl)) / (math.cos(latr) * sin_zen)
        cos_az = max(-1.0, min(1.0, cos_az))
        az = math.degrees(math.acos(cos_az))
        az = (az + 180.0) % 360.0 if ha > 0 else (540.0 - az) % 360.0
    # atmosphärische Refraktion (NOAA), nur nahe am Horizont relevant
    if el > 85.0:
        refr = 0.0
    elif el > 5.0:
        te = math.tan(math.radians(el)); refr = (58.1 / te - 0.07 / te ** 3 + 0.000086 / te ** 5) / 3600.0
    elif el > -0.575:
        refr = (1735.0 + el * (-518.2 + el * (103.4 + el * (-12.79 + el * 0.711)))) / 3600.0
    else:
        refr = -20.772 / math.tan(math.radians(el)) / 3600.0
    return az, el + refr


def poa_irradiance(ghi, dni, dhi, sun_az, sun_el, tilt, azimuth, albedo=ALBEDO):
    """Einstrahlung (W/m²) auf eine Fläche mit Neigung tilt (0 = flach) und Ausrichtung azimuth (180 = Süd)."""
    ghi = max(float(ghi or 0.0), 0.0); dni = max(float(dni or 0.0), 0.0); dhi = max(float(dhi or 0.0), 0.0)
    if sun_el <= 0.0:
        return 0.0
    zen = math.radians(90.0 - sun_el); tl = math.radians(tilt)
    cos_aoi = math.cos(zen) * math.cos(tl) + math.sin(zen) * math.sin(tl) * math.cos(math.radians(sun_az - azimuth))
    beam = dni * max(cos_aoi, 0.0)
    diffuse = dhi * (1.0 + math.cos(tl)) / 2.0
    ground = ghi * albedo * (1.0 - math.cos(tl)) / 2.0
    return beam + diffuse + ground


def expected_w(gti, wp, pr=PR_DEFAULT):
    return gti / 1000.0 * float(wp) * float(pr)


def string_config(env=os.environ):
    """Strings mit Neigung/Ausrichtung aus der Umgebung: {1: {"tilt": 30.0, "azimuth": 180.0, "wp": 440.0, "pr": 0.85}, ...}"""
    out = {}
    pr = float(env.get("STRING_PR", PR_DEFAULT) or PR_DEFAULT)
    for i in range(1, 5):
        tilt = env.get(f"STRING{i}_TILT", ""); az = env.get(f"STRING{i}_AZIMUTH", "")
        if tilt == "" or az == "":
            continue
        try:
            out[i] = {"tilt": float(tilt), "azimuth": float(az), "wp": float(env.get(f"STRING{i}_WP", "") or 0.0), "pr": pr}
        except ValueError:
            continue
    return out


if __name__ == "__main__":
    import sys, time
    lat = float(sys.argv[1]) if len(sys.argv) > 2 else 52.52; lon = float(sys.argv[2]) if len(sys.argv) > 2 else 13.405
    ts = int(time.time())
    az, el = sun_position(ts, lat, lon)
    print(f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(ts))}  lat {lat} lon {lon}: Azimut {az:.1f}°, Höhe {el:.1f}°")
