#!/usr/bin/env python3
"""Schickt jede Panel-Abfrage der Dashboards an Grafana und meldet Flux-Fehler.
Nutzt GRAFANA_ADMIN_USER / GRAFANA_ADMIN_PASSWORD / GRAFANA_PORT aus der Umgebung (.env).

Aufruf: scripts/test-panels.py [Dateimuster]   Standard: nexa-*.json wolf-*.json"""
import base64, json, os, sys, time, urllib.request

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
auth = base64.b64encode(f"{os.environ.get('GRAFANA_ADMIN_USER','admin')}:{os.environ.get('GRAFANA_ADMIN_PASSWORD','')}".encode()).decode()
port = os.environ.get("GRAFANA_PORT", "3000"); ghost = os.environ.get("GRAFANA_HOST", "127.0.0.1")
import glob
patterns = sys.argv[1:] or ["nexa-*.json", "wolf-*.json"]
files = sorted(f for pat in patterns for f in glob.glob(os.path.join(root, "grafana/dashboards", pat)))
d = {"panels": [p for f in files for p in json.load(open(f))["panels"]]}
now = int(time.time() * 1000); frm = now - 24 * 3600 * 1000
ok = bad = 0; errs = []
for p in d["panels"]:
    if p["type"] == "row":
        continue
    for t in p.get("targets", []):
        body = {"from": str(frm), "to": str(now), "queries": [{"refId": t["refId"], "datasource": {"type": "influxdb", "uid": "influx-nexa"},
                                                              "query": t["query"].replace("${string}", "1"), "intervalMs": 60000, "maxDataPoints": 500}]}
        req = urllib.request.Request(f"http://{ghost}:{port}/api/ds/query", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Basic " + auth})
        try:
            r = json.load(urllib.request.urlopen(req, timeout=60)); res = r["results"][t["refId"]]
            if res.get("error"):
                bad += 1; errs.append(f"{p['title']}/{t['refId']}: {res['error'][:120]}")
            else:
                ok += 1
        except urllib.error.HTTPError as e:
            bad += 1
            try: msg = json.load(e)["results"][t["refId"]]["error"][:120]
            except Exception: msg = f"HTTP {e.code}"
            errs.append(f"{p['title']}/{t['refId']}: {msg}")
        except Exception as e:
            bad += 1; errs.append(f"{p['title']}/{t['refId']}: {str(e)[:120]}")
print(f"{ok} {bad}")
for e in errs[:10]:
    print("  " + e)
sys.exit(1 if bad else 0)
