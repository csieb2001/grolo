#!/usr/bin/env python3
"""Register a NEXA 2000 with a Growatt plant, the way the ShinePhone app does it.

The ShinePhone app binds a NEXA to a plant with a single call that needs no check
code from the sticker -- unlike the generic datalogger path
(/gro/plant/datalog/addDatalog, datalogSn + verifyCode).  Endpoints and field
names taken from ShinePhone 8.4.9.0 (com.growatt.shinephone):

    POST /newTwoLoginAPIV2.do          userName, password, ...  -> session cookie
    POST /noahDeviceApi/nexa/nexaDeviceList      accountName
    POST /noahDeviceApi/nexa/getPlantInfoList    accountName, deviceSn
    POST /noahDeviceApi/nexa/addNexaDatalog      datalogSn, plantId
    POST /newTwoLoginAPI.do?op=getCpowerAuthToken  accountName

The password goes over the wire as MD5 hex where every single-digit byte is
prefixed with "c" instead of "0" (MD5andKL.encryptPassword).

Usage:
    scripts/growatt-register-nexa.py --user <name> [--password <pw>] \
        --serial <datalogger serial> [--plant <id>] [--bind]

Without --bind nothing is written: it only logs in and reports what the server
knows about the device and which plants it offers.
"""

import argparse
import getpass
import hashlib
import json
import sys
import time
from datetime import datetime

import requests

DEFAULT_SERVER = "https://server-api.growatt.com"
UA = "Dalvik/2.1.0 (Linux; U; Android 13; Pixel 6 Build/TQ3A.230805.001)"


def encrypt_password(password: str) -> str:
    """MD5andKL.encryptPassword: MD5 hex, single hex digits prefixed with 'c'."""
    out = []
    for b in hashlib.md5(password.encode()).digest():
        h = format(b, "x")
        out.append(("c" + h) if len(h) == 1 else h)
    return "".join(out)


def validate_timestamp() -> str:
    """SystemUtil.validateTimestamp: millis[0:11] + (digits 1,3,5,7 % 98)."""
    ms = str(int(time.time() * 1000))
    n = int(ms[1] + ms[3] + ms[5] + ms[7]) % 98
    return ms[:11] + ("%02d" % n)


def special(user: str) -> str:
    """SystemUtil.getSpecial: UUID(md5(user + salt + millis).hashCode(), 102633)."""
    digest = encrypt_password(user + "★☆i₰₭" + str(int(time.time() * 1000)))
    # java String.hashCode()
    h = 0
    for ch in digest:
        h = (31 * h + ord(ch)) & 0xFFFFFFFF
    if h >= 0x80000000:
        h -= 0x100000000
    hi = h & 0xFFFFFFFFFFFFFFFF
    lo = 102633
    raw = "%016x%016x" % (hi, lo)
    return "%s-%s-%s-%s-%s" % (raw[0:8], raw[8:12], raw[12:16], raw[16:20], raw[20:32])


class Shine:
    def __init__(self, server: str):
        self.server = server.rstrip("/")
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA

    def post(self, path: str, data: dict) -> dict:
        r = self.s.post(self.server + path, data=data, timeout=45,
                        allow_redirects=False)
        if r.status_code == 302:
            raise SystemExit("session expired (302) on %s -- log in again" % path)
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            return {"_raw": r.text[:2000]}

    def login(self, user: str, password: str) -> dict:
        return self.post("/newTwoLoginAPIV2.do", {
            "userName": user,
            "password": encrypt_password(password),
            "language": "1",
            "appType": "ShinePhone",
            "phoneSn": "grolo-tool",
            "phoneModel": "Pixel 6",
            "phoneType": "android",
            "systemVersion": "13",
            "shinephoneVersion": "8.4.9.0",
            "loginTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp": validate_timestamp(),
            "newLogin": "1",
            "ipvcpc": special(user),
        })


def show(title: str, payload) -> None:
    print("\n=== %s ===" % title)
    print(json.dumps(payload, indent=2, ensure_ascii=False)[:4000])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", required=True, help="Growatt account name")
    ap.add_argument("--password", help="account password (asked for if omitted)")
    ap.add_argument("--serial", required=True, help="datalogger serial of the NEXA")
    ap.add_argument("--plant", help="plant id to bind to")
    ap.add_argument("--server", default=DEFAULT_SERVER)
    ap.add_argument("--bind", action="store_true",
                    help="actually call addNexaDatalog (otherwise read-only)")
    ap.add_argument("--power-token", action="store_true",
                    help="also fetch the Cpower auth token (dongle parameter 54)")
    args = ap.parse_args()

    password = args.password or getpass.getpass("Growatt password: ")
    api = Shine(args.server)

    res = api.login(args.user, password)
    ok = str(res.get("back", {}).get("success", res.get("success", ""))).lower()
    show("login", res)
    if ok not in ("true", "1"):
        print("\nlogin failed -- stopping", file=sys.stderr)
        return 1

    show("nexaDeviceList (before)",
         api.post("/noahDeviceApi/nexa/nexaDeviceList", {"accountName": args.user}))
    show("getPlantInfoList",
         api.post("/noahDeviceApi/nexa/getPlantInfoList",
                  {"accountName": args.user, "deviceSn": args.serial}))

    if args.bind:
        if not args.plant:
            print("\n--bind needs --plant", file=sys.stderr)
            return 2
        show("addNexaDatalog",
             api.post("/noahDeviceApi/nexa/addNexaDatalog",
                      {"datalogSn": args.serial, "plantId": args.plant}))
        show("nexaDeviceList (after)",
             api.post("/noahDeviceApi/nexa/nexaDeviceList", {"accountName": args.user}))

    if args.power_token:
        show("getCpowerAuthToken",
             api.post("/newTwoLoginAPI.do?op=getCpowerAuthToken",
                      {"accountName": args.user}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
