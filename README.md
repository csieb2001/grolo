# GroLo · For Growatt but Local

**Local monitoring and control for the Growatt NEXA 2000 balcony battery and a Wolf CHA heat pump, without the manufacturer clouds.**
A Docker Compose stack: TLS MQTT broker for the Growatt Wi-Fi dongle, [GroBro](https://github.com/robertzaage/GroBro) for decoding,
InfluxDB + Grafana for history, a bilingual settings page (EN/DE), and three small helper services for hardware info, raw registers and
an optional, switchable relay to the Growatt cloud.

Version **2026.40.2** · Runs on any host with Docker (developed on macOS, tested with NEXA 2000 firmware 4.0.2.6 and two battery
packs, and a Wolf CHA-10 on a WOLF Link home with firmware 4.50.0).

## Screenshots

**Grafana dashboard** (English, German available): live tiles, power history and state of charge.

![Grafana: live tiles and power history](docs/screenshot-grafana-live.png)

Energy per day, where today's PV energy went and where the house energy came from.

![Grafana: energy](docs/screenshot-grafana-energy.png)

PV strings with detected inputs, temperatures, cell voltages, packs, firmware and dongle.

![Grafana: strings, battery and technical](docs/screenshot-grafana-strings-battery.png)

**GroLo settings page**: device and hardware, battery packs, dongle.

![GroLo: device and hardware](docs/screenshot-grolo-device.png)

Operating mode, battery limits, output and operating switches.

![GroLo: settings](docs/screenshot-grolo-settings.png)

Time slots, cloud relay with status, dongle settings and log.

![GroLo: slots, cloud, dongle](docs/screenshot-grolo-slots-cloud.png)

## How it works

```
NEXA dongle ──TLS :7006──▶ Mosquitto ──▶ GroBro (decode) ──▶ Mosquitto :1883 ──▶ Telegraf ──▶ InfluxDB ──▶ Grafana :3000
                                             │                      │
                                             │                      └──▶ GroLo settings page :8080 (WebSocket :9001)
                                             └──▶ cloud-gate ──TLS──▶ mqtt.growatt.com   (optional, switchable, filtered)
```

The dongle is never reconfigured. Instead your router resolves `mqtt.growatt.com` to the host running this stack, and the
stack presents a certificate from a public CA that the dongle's firmware trusts. Everything the dongle sends is decoded
locally; nothing leaves your network unless you switch the cloud relay on.

**Why a real certificate?** The Growatt firmware only trusts built-in public root CAs and verifies the complete chain, but it
does **not** check the hostname. A free Let's Encrypt certificate for any domain you control is enough. Self-signed
certificates are rejected. Details: [GroBro CERTIFICATES.md](https://github.com/robertzaage/GroBro/blob/main/CERTIFICATES.md).

## Requirements

- A host in the same LAN as the NEXA with Docker and Docker Compose (Linux, macOS with Docker Desktop, Colima or OrbStack, Windows with WSL2).
- A router whose DNS lets you add local records (UniFi, FritzBox with Pi-hole/AdGuard, OpenWrt, pfSense, …).
- A DNS name you control for the certificate. The easiest free option is a [DuckDNS](https://www.duckdns.org) subdomain.
- [acme.sh](https://github.com/acmesh-official/acme.sh) for the certificate (`brew install acme.sh` or the install script), plus
  `mosquitto_sub`/`mosquitto_pub` for the checks (`brew install mosquitto` or `apt install mosquitto-clients`).
- Python 3 and Node.js are only needed for the optional test scripts.

## Installation

### 1. Clone and configure

```bash
git clone https://github.com/csieb2001/grolo.git && cd grolo
cp .env.example .env
```

Edit `.env`:

| Variable | Meaning |
|---|---|
| `DUCKDNS_DOMAIN`, `DUCKDNS_TOKEN` | your DuckDNS subdomain (without `.duckdns.org`) and the account token |
| `ACME_EMAIL` | contact address for Let's Encrypt expiry mails (optional) |
| `INFLUX_ADMIN_PASSWORD`, `INFLUX_TOKEN`, `GRAFANA_ADMIN_PASSWORD` | choose your own; `openssl rand -hex 32` for the token |
| `CLOUD_HOSTS` | IP addresses of `mqtt.growatt.com` as seen from **outside** your LAN (`dig mqtt.growatt.com @1.1.1.1`) |
| `GROBRO_MAX_SLOTS` | number of time slots to expose (NEXA: 9) |
| `WEATHER_LAT`, `WEATHER_LON` | plant location for weather and sun position (starting value; the settings page can change it later) |
| `STRING1_TILT`, `STRING1_AZIMUTH`, `STRING1_WP` … | panel tilt/azimuth/Wp per string for the expected-yield model (optional, also editable on the settings page) |

### 2. DuckDNS

1. Sign in at [duckdns.org](https://www.duckdns.org) (GitHub, Google, Reddit or X login).
2. Add a subdomain, e.g. `my-nexa` → `my-nexa.duckdns.org`.
3. Set its IP to the **LAN IP of your Docker host** (e.g. `192.168.1.50`). The record is public but points into your LAN, which is
   fine: the certificate is issued via DNS-01 and never needs an inbound connection.
4. Copy the token shown at the top of the page into `.env`.

### 3. Certificate

```bash
scripts/setup-certs.sh
```

The script issues a Let's Encrypt certificate via `acme.sh --dns dns_duckdns`, installs `cert.pem`, `privkey.pem` and the
intermediates into `mosquitto/certs/` and runs `scripts/build-chain.sh`, which appends the matching ISRG root and verifies
the chain with OpenSSL. Renewal runs through acme.sh's cron or, on macOS, a launchd job (see comments in the script); the
reload hook rebuilds the chain and restarts Mosquitto.

Notes that cost us time:

- acme.sh defaults to ZeroSSL, whose root is **not** in the Growatt firmware. The script forces `--server letsencrypt`.
- Since 2026 the Let's Encrypt chain has **four** certificates (`cert → YE1 → Root YE → ISRG Root X2`). The dongle needs all of
  them including the root, which is why `chain-full.pem` is built explicitly instead of using acme.sh's `fullchain.cer`.
- The private key is world-readable (0644) inside `mosquitto/certs/` because the Mosquitto container runs as uid 1883 and
  bind mounts from macOS do not map uids. Acceptable for a home setup; use a named volume if you prefer.

### 4. Start

```bash
docker compose up -d
scripts/verify.sh
```

`verify.sh` runs 14 checks: certificate chain and expiry, TLS handshake exactly like the dongle does it (root as the only trust
anchor), MQTT round trip, GroBro connected to both listeners, ports reachable on the LAN IP, InfluxDB receiving data, all
Grafana panel queries, settings page and WebSocket, hardware-info sidecar, raw-register sidecar and cloud relay.

### 5. Router: DNS and firewall

1. **Local DNS records** (Router → DNS / Local DNS / DNS rewrite):
   - `mqtt.growatt.com` → LAN IP of the Docker host
   - `<your-sub>.duckdns.org` → LAN IP of the Docker host (optional if the public DuckDNS record already points there)

   The dongle resolves `mqtt.growatt.com`, gets your host, opens TLS, sees a valid public-CA chain and logs in with its serial
   number. No change on the device is needed. GroBro's guide describes the alternative of changing the broker in the device via
   [openapi.growatt.com](https://openapi.growatt.com) → Datalogger Setting.
2. **Block the dongle from the internet** (Firewall → policy: source = the dongle's MAC/IP, destination = WAN, drop). Without this the
   dongle would reconnect to the real cloud as soon as your DNS record is gone, and the cloud could rewrite its broker settings.
3. Make the dongle reconnect once: reboot it from the settings page later, or briefly block its current connection. It reconnects
   within seconds and lands on your broker. `docker compose logs -f mosquitto` shows `New client connected … as <serial>`.

UniFi example: Settings → Routing → DNS for the records; Settings → Security → Firewall → Create Policy, zone Internal →
External, source the dongle client, action Block.

### 6. Open

| Service | URL | Notes |
|---|---|---|
| GroLo settings page | `http://<host>:8080` (also `http://<host>:3000/public/grolo/index.html`, the *Settings* link in Grafana) | EN/DE toggle, no login |
| Grafana | `http://<host>:3000` | English dashboard is the home page, German via the link at the top; viewing without login, editing as `admin` |
| InfluxDB | `http://127.0.0.1:8086` | bound to localhost only |

### Running on Proxmox (LXC)

The stack runs fine in an unprivileged Debian 12 container with Docker inside. Create it with nesting and keyctl enabled,
install Docker from the official repository, clone the repo to `/opt/growatt` (the directory name becomes the Compose
project name and thus the volume prefix) and continue with the steps above. Give the container a fixed IP in your router so
the DNS records stay valid.

```bash
pct create 103 local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst --hostname grolo --unprivileged 1 \
  --features nesting=1,keyctl=1 --cores 2 --memory 3072 --rootfs local-lvm:24 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp --onboot 1
```

Moving an existing installation: `influx backup` / `influx restore --full` for the database, `tar` the small volumes
(`grafana-data`, `mosquitto-data`, `cloud-gate-state`), copy `.env`, `mosquitto/certs/` and `~/.acme.sh/`, then switch the
two DNS records. The dongle reconnects within seconds; the gap in our move was a single 5-second sample.

## What you get

**Settings page (GroLo)**: a live **power flow** schema at the top (solar → NEXA/battery → house, grid ↔ house with a Shelly;
animated dots show direction and speed shows power, like the energy-flow screen of the Anker SOLIX or Growatt apps), device and hardware (serial, firmware register, packs with serial/SoC/temperature, PV inputs in use,
dongle model/software/Wi-Fi signal), operating mode switch, charge/discharge limits, output power, operating switches, all
9 time slots, cloud relay switch with status, dongle settings (interval, time zone, clock sync, restart, pairing mode = IOT
module off via dongle parameter 35, see below), and a log with
confirmations from the device. Controls are generated from GroBro's Home Assistant discovery, so anything GroBro exposes appears
automatically. Every write is confirmed by reading the register back. A **Location and panels** section sets the plant location
by place or postcode search (Open-Meteo geocoding), tilt/azimuth/Wp per string and an optional **name per string** (e.g. “balcony
south”) that replaces “String n” on the settings page, the website and in Grafana (dashboard variables `s1`–`s4`, fed from the retained
`grolo/config/string_names` via Telegraf); it is stored as a retained MQTT message
(`homeassistant/grolo/config/site`) and picked up by the weather service immediately. **Electricity price and savings** stores
your tariff (ct/kWh), an optional feed-in rate and the system price as a retained message (`homeassistant/grolo/config/tariff`);
Telegraf copies it to InfluxDB (measurement `tariff`) and web-push to the website, and the page shows the money at the current
power (saving per hour from the NEXA output, grid cost per hour from the Shelly).

**Grafana** (rows in this order): **Now** (PV from the strings, output from register 116, battery as balance, SoC, household),
**Zero feed-in (Shelly)** with controller status, grid import and export today, **Power and state of charge**, **Energy**
(daily bars, energy split pies, today/month/year/total), **Costs and savings** (price in use, saved today/month/year/total =
output to house × price, grid cost today/month and self-sufficiency from the Shelly, payback gauge against the system price,
savings and grid cost per day), **Year and records** (records table: strongest and weakest day, highest PV peak, highest household consumption, most grid import,
best earnings and most expensive grid day at the configured tariff, most battery discharge, PV and savings year to date, CO₂ avoided
at 0.38 kg/kWh; a day × month heatmap of the daily PV yield), **PV strings**, **Daily peaks, sun position and model** (see below), **Weather**, **Battery and
technical**, and a research row with the raw registers GroBro does not know yet. Without a tariff entry the dashboard assumes
30 ct/kWh. Free MPPT inputs read about 7 V on the NEXA, connected panels 30 V and more.

**MQTT topics** (prefix `homeassistant/`, GroBro's namespace):

| Topic | Purpose |
|---|---|
| `grobro/<serial>/state` | decoded state JSON, every few seconds |
| `number|switch|select|time/grobro/<serial>/<name>/set` and `.../get` | write a setting, read back the confirmation |
| `button/grobro/<serial>/read_all/read` | read every setting from the device (about a minute) |
| `config/grobro/<serial>/<register>/set` | dongle configuration: 4 interval, 30 time zone, 31 clock sync, 32 restart |
| `grobro/<serial>/dongle` | dongle hardware info (retained, from the `dongle-info` sidecar) |
| `grobro/<serial>/raw_input`, `.../raw_holding` | all raw registers (from the `raw-registers` sidecar) |
| `switch/grobro/cloud_forward/set`, `.../get`, `grobro/cloud_forward/status` | cloud relay switch and status |

Example:

```bash
mosquitto_pub -h <host> -t homeassistant/number/grobro/<serial>/slot1_power/set -m 300
mosquitto_sub -h <host> -t 'homeassistant/+/grobro/<serial>/+/get' -v
```

## Weather (Open-Meteo)

The `weather` service fetches solar-relevant weather for the plant location from [Open-Meteo](https://open-meteo.com) (free, no
API key) every 10 minutes: temperature, cloud cover, WMO weather code, global/direct/diffuse irradiance, wind, sunrise and
sunset, forecast sunshine hours and daily irradiation sum, plus an hourly 48-hour forecast. Set `WEATHER_LAT` / `WEATHER_LON`
in `.env`. Current values go to `homeassistant/grolo/weather/current` (retained JSON with plain-text conditions in EN/DE),
`.../weather/state` feeds Telegraf (measurement `weather`), and the forecast is written straight into InfluxDB as
`weather_forecast` with the forecast hour as timestamp, so newer forecasts overwrite older ones.

Grafana row **Weather**: outdoor temperature, conditions, cloud cover, global irradiance, sunrise/sunset, sunshine hours,
irradiation sum today/tomorrow, irradiance versus PV power on two axes (shading, orientation and soiling show up here),
cloud cover and temperature, and the 48-hour forecast of irradiance and cloud cover.

## Daily peaks, sun position and expected yield

The weather service also computes the **sun position** (azimuth/elevation, NOAA algorithm in `grobro/sidecar/solar.py`) every
minute (`homeassistant/grolo/sun`, measurement `sun`) and, for every string with tilt/azimuth configured, the **expected power**
per hour for the last 24 h and the next 48 h: Open-Meteo direct-normal, diffuse and global irradiance transposed onto the panel
plane (isotropic sky model), times Wp times a performance ratio (`STRING_PR`, default 0.85). Rows go to `homeassistant/grolo/pv_model`
and measurement `pv_model` (tag `string`).

Grafana row **Daily peaks, sun position and model**: table of the highest one-minute power per string and day with the time and
the sun position at that moment, daily peak bars per string, hour × day heatmaps (total PV and a selectable string; shifting
patterns reveal orientation and shading), the strongest string per hour as a state timeline, measured vs. expected per string,
sun elevation/azimuth, and power plotted against sun azimuth (the centre of the cloud shows where a string faces, a dip at a
fixed azimuth is an obstacle).

Do not know tilt and azimuth? The weather service **estimates the orientation** once a day (`FIT_INTERVAL`, default 24 h) and
whenever you press *Estimate orientation now* on the settings page (topic `homeassistant/grolo/fit/run`): it pulls the hourly
string power of the last `FIT_DAYS` days from InfluxDB and the matching irradiance from Open-Meteo, fits the model for every
orientation in 5° steps and publishes the best match per string with quality (R², sunny hours, range of equally good solutions)
as retained `homeassistant/grolo/fit` and measurement `pv_fit`. The settings page shows it next to the inputs with an *Apply*
button, Grafana in the table *Estimated orientation per string*, the website as chips and as a hollow diamond in the sun-path
chart. It needs a few sunny days with direct sun and says "not determinable yet" until then. `scripts/fit-orientation.py` runs
the same estimate from the shell (`--apply` writes the result to the settings topic).

**Recommendations.** With every estimate the weather service also builds a year model for the location from the Open-Meteo
archive (12 months of hourly DNI/DHI/GHI) and compares the current orientation of each string (configured, else estimated) with
the site optimum and practical alternatives: same direction with the best tilt, vertical (balcony) with the best azimuth, flat.
It reports kWh per kWp and year, the share of the optimum, the gain of each alternative and the winter share, and it checks the
measurements for **shading**: sun directions in which the measured power stays far below the model while it fits elsewhere are
reported as zones with today's times and the lost share of sunny-hour energy. Results: retained `homeassistant/grolo/advice`,
measurement `pv_advice`, the Grafana table *Recommendations per string*, the settings page below the estimate, and the website
under the sun-path chart. Strings without configured panels get an **assumed orientation** for the expected curve (the estimate,
else the site optimum), labelled as such, until you enter the panels.

The optional [GroLo website](https://github.com/csieb2001/grolo-web) receives location, sun position and the model and shows a
**sun-path polar chart** (paths for solstices, equinox and the selected day, the daily peak of each string as a dot at the sun
position of that moment, panel orientations as squares), a day slider that animates the sun and lets the strings glow with their
power, an hour × day heatmap per string, measured vs. expected for the selected day, and per string tiles with the day's peak,
the daylight mean and the all-time high and all-time daylight mean.

## Zero feed-in with a Shelly (local "Smart" mode)

The NEXA refuses the manufacturer's Smart mode without a meter paired in the Growatt cloud. The `shelly-control` sidecar
reproduces it locally: it reads a Shelly meter (Pro 3EM, EM, 1PM or a Gen1 Shelly) every couple of seconds over local HTTP
and adjusts the NEXA output power (slot power, a RAM register safe for frequent writes) so the grid draw stays at a small
setpoint and nothing is exported. No Growatt cloud, no meter pairing.

Configure it on the settings page under **Zero feed-in (Shelly)**: enter the Shelly address, setpoint (grid draw to hold,
default 20 W), and the output range (min/max W). Choosing **Smart** in the operating-mode control turns the controller on and
keeps the slot in Load First underneath; choosing Load first / Battery first turns it off and writes the real mode. The
config is a retained message `<base>/grolo/config/shelly`; the controller publishes `<base>/grolo/shelly/state` (grid,
household, output, target, ok, limited, reason, soc, soc_limit) which the settings page, InfluxDB (measurement `shelly`) and
the website mirror. On the website the **Grid** and **Household** tiles and the grid node of the power-flow schema appear once
the controller runs. If the Shelly is unreachable for 30 s the output falls back to a safe value (default 0 W) and the state is
flagged. Works for any Shelly, so other users can use it by pointing it at their own meter.

`reason` tells you why the output does not match the target: `ok`, `shelly_unreachable`, `device_offline` (no data from the
dongle), `battery_low` (the pack sits at the discharge limit; the NEXA stops the output there and resumes a few percent
higher, which looks like a 10 ↔ 13 % cycle on an empty battery) or `device_limited` (the NEXA delivers less than requested
for another reason). While limited, the controller stops winding the target up and holds it `hold_w` (default 100 W) above
the measured output, so nothing is exported when the NEXA resumes; it releases the limit as soon as the output follows again.
Tolerance and delay are set on the settings page under **Response and not-following detection** (`hold_w`, `hold_s`, default
100 W after 60 s). The NEXA follows a new slot power only after 30–60 s, so the controller adjusts at most every `write_s`
(default 15 s) and only once the NEXA has reached the last value, using the Shelly mean since the last write; export above
`export_w` (default 30 W) is corrected immediately. Each transition is logged (`docker compose logs shelly-control`).

## Heat pump (WOLF CHA over the local WOLF Link)

A Wolf heat pump with a **WOLF Link home/pro** interface can be read *and* controlled completely locally — no Wolf Smartset
portal involved. Besides its configuration website on port 80, the Link speaks the ISM7 protocol on **TCP 9092 over TLS**,
authenticated with the same device password you use for `http://<link-ip>/`. [ism7mqtt](https://github.com/zivillian/ism7mqtt)
implements that protocol and ships Wolf's own parameter resources, so names, units, limits and selection lists are exactly
the ones the Smartset app shows.

```
WOLF Link ──TLS :9092──▶ ism7mqtt ──▶ mosquitto ──▶ wolf-bridge ──▶ mosquitto ──▶ Telegraf ──▶ InfluxDB ──▶ Grafana
                             ▲                           │
                             └──── writes ───────────────┴──▶ control page :8080/wolf.html
```

`ism7mqtt` publishes one JSON topic per bus device (`Wolf/<ip>/CHA_0x8`), but only with the values read in that cycle.
`wolf-bridge` keeps the full picture, flattens it into stable field names for InfluxDB, computes the figures the Wolf does not
provide, and turns control requests from the page back into ISM7 writes.

### Setup

1. Put the Link's address and password into `.env`, and enable the profile the two services live in:

   ```
   COMPOSE_PROFILES=wolf
   WOLF_HOST=192.168.1.57
   WOLF_PASSWORD=<the device password>
   WOLF_EXPERT_PIN=1111        # code for the installer level of the control page, empty = no code
   ```

2. Ask the Link which devices are on the eBus, then build the catalogue:

   ```bash
   scripts/wolf-config.sh      # -> wolf/parameter.json
   scripts/wolf-catalog.py     # -> wolf/catalog.json
   scripts/wolf-dashboard.py   # -> grafana/dashboards/wolf-de.json, wolf-en.json
   scripts/wolf-nexa-link.py   # adds the heat pump row to the NEXA dashboards
   docker compose up -d
   ```

   `wolf-config.sh` runs Wolf's own `ism7config` and stops the `wolf` service while it does, because the Link accepts only one
   local connection at a time. `wolf-catalog.py` downloads Wolf's `parameter.xml`, `gui.xml` and `dictionary.xml` (about 12 MB,
   cached in `wolf/.resources/`, not committed) and keeps only what your system actually has, with German and English labels
   and the original menu structure including the installer level.

On our system (CHA-10 with BM-2, a mixer module and one mixer circuit) that is **329 parameters across 5 bus devices, 182 of
them writable, 184 on the installer level**.

### What you get

- **Dashboards** `/d/wolf-de` and `/d/wolf-en`: live tiles, temperatures, power and COP, compressor and pumps, flow rate and
  spread, an operating-mode timeline, efficiency (SPF from the Wolf's own counters plus daily performance factors computed
  from heat and electricity per day), **cycling** (see below), the refrigerant circuit, heating circuit and hot water, the
  Wolf's own statistics registers, and a house row combining PV, household (Shelly) and heat pump with the real cost per kWh
  of heat.
### Cycling

Short cycling is the most common thing to get wrong on a heat pump, and daily totals cannot see it: they say *how often* the
compressor started, not *how* it ran. `wolf-bridge` therefore records **every compressor run on its own** — runtime, the pause
before it, operating mode, average and peak frequency, outside temperature, flow temperature, heat produced and the cycle's
COP. Each finished run goes out on `homeassistant/grolo/wolf/cycle` and lands in the InfluxDB measurement `wolf_cycle`; the
rolling evaluation of the last fourteen days is published retained on `homeassistant/grolo/wolf/cycling`.

The yardstick is the uninterrupted runtime, not a maximum number of starts — the German heat pump association deliberately
names no fixed limit. Below ten minutes is short cycling, ten to twenty is common, thirty to sixty is the ideal; ten to
fifteen starts a day is a good value, and under 2000 starts a year counts as optimal while 6000 measurably costs compressor
life. In the Fraunhofer ISE field test real systems ranged from 540 to 15,820 starts a year.

The decisive view is **cycles by outside temperature**. A bump at eight to fifteen degrees is shoulder-season cycling: the
house needs less than the compressor can turn down to, which is a control problem (flatten the heating curve, widen
`hysterese_heizbetrieb`, drop the night setback, open the room thermostats). A bump at freezing is hydraulic: too little flow
or water volume (VDI 4645 says about 20 l per kW), or the unit is simply oversized. From that the sidecar forms a **verdict**
in German and English that names the parameter to turn — `Cycles.judge()` in `grobro/sidecar/wolf_bridge.py`. Nothing is
adjusted automatically; the verdict points at the control page and that is where you change it. Dashboards and the GroLo
website show the same sentence, because both read it from the same place.

- **Control page** `http://<host>:8080/wolf.html` (also linked from the settings page and both dashboards): live tiles, the
  user level (operating mode, target temperatures, time programs, party and holiday mode), the **installer level** behind
  `WOLF_EXPERT_PIN`, and a searchable table of every parameter with its InfluxDB field name. Bilingual, same look as the
  settings page, no login.
- **A row in the NEXA dashboards** that puts solar, household and heat pump power in one picture.
- **A heat pump section on the GroLo website** (if `WEB_URL`/`WEB_TOKEN` are set): the same figures plus the split of the heat
  pump's electricity into solar, battery and grid, cost per kWh of heat and CO₂ against a gas boiler. `web-push` sends the heat
  pump samples with the same timestamps as the NEXA samples, which is what lets the site pair the two minute by minute.

### Writing values

The page publishes to `homeassistant/grolo/wolf/set`, and so can anything else:

```bash
mosquitto_pub -h <host> -t homeassistant/grolo/wolf/set \
  -m '{"device":"dhw","key":"warmwassersolltemperatur_eingestellt_350009","value":50}'
mosquitto_pub -h <host> -t homeassistant/grolo/wolf/set \
  -m '{"device":"heatpump","key":"bivalenzpunkt_e_heizung","value":-7,"pin":"1111"}'
```

`wolf-bridge` checks every request against the catalogue before it touches the bus: the parameter must exist, be writable,
stay inside the limits Wolf itself allows, and — for installer-level parameters — carry the right code. The answer comes back
on `homeassistant/grolo/wolf/set/result` with a reason when it is refused. Device ids are `heatpump`, `control`, `dhw`,
`circuit`, `mixer` and `gateway`; field names are in `wolf/catalog.json` and in the "All values" table on the page.

### Worth knowing

- The Link accepts **one** local connection. While the `wolf` service runs, the Smartset app can no longer connect locally —
  through the portal it keeps working, and the portal connection is unaffected by all of this.
- The Wolf reports its power input only in **whole kW**, so the live COP is coarsely stepped. The daily, monthly and yearly
  performance factors come from the kWh counters and are accurate.
- After the initial full read, `ism7mqtt` only sends values that changed. `wolf-bridge` therefore keeps the last known value
  of every parameter in `/state/wolf.json`; without that a restart would leave you with only the handful of values that moved
  since. Restarting the `wolf` service forces a fresh full read (a few minutes for all parameters).
- Switching times (`DaySwitchTimes`) are shown but not editable here; the time *program* selection (1/2/3) is.
- The `gateway` device (the Link's own network settings) has no eBus values, so it stays empty — that is why the check reports
  5 of 6 devices.

## Rooms (tado° X over Matter, fully local)

tado offers **no local API** for the X line — their own words: "tado provides no local API — neither on their
classic devices nor on the new Tado X line." Both the official Home Assistant integration and the community
ones go through the cloud, which since 1 January 2026 is rate-limited to 100 requests a day without a
subscription. The local road is **Matter**: tado° X is Matter over Thread, and tado supports multi-admin, so the
same device can join a second fabric without leaving tado.

```
tado° X (Thread) ──▶ Thread Border Router ──IPv6──▶ matter (controller) ──ws──▶ tado-bridge ──▶ mosquitto ──▶ …
```

Three things have to be true, and they are worth checking in this order:

1. **IPv6 on the host.** Matter over Thread is IPv6-only. On Proxmox, `ipv6.disable=1` on the kernel command
   line switches it off for every container; it has to go, and the container needs `ip6=auto`.
2. **A route into the Thread mesh.** The border router announces the mesh prefix as a Route Information Option
   in its Router Advertisements, and Linux ignores those by default. `accept_ra_rt_info_max_plen=64` (and
   `accept_ra=2`, so Docker's forwarding does not disable RA processing) fixes it — see
   `/etc/sysctl.d/60-grolo-matter.conf`. Any Thread border router in the house will do; ours are Apple's.
3. **A pairing code per device.** tado app → Settings → Rooms and Devices → device → Matter device linking →
   copy code. The window closes after about 15 minutes. Paste it on the settings page with a room name; the
   name is written into the device itself (`NodeLabel`), not into a config file here. No Thread credentials are
   needed: the devices are already on a Thread network, so this is on-network commissioning.

**Devices with the same name form one room.** That is how two radiators in one room end up together, and how a
Wireless Temperature Sensor X joins its thermostat. Where a room has a sensor, **its** temperature counts — a
thermostat sits on the radiator and measures its heat build-up as well. The difference between the two is
published as `radiator_offset_k`: how much too warm the thermostat reads, and therefore how much too early it
throttles. If the sensor drops out the thermostat takes over and the room is flagged `temp_fallback`.

What a tado° X radiator thermostat actually exposes over Matter, read off the device (firmware 1.4.289):
local temperature, target temperature, system mode, humidity and a battery **level** — no valve position
(`PIHeatingDemand`) and no battery percentage; tado keeps those to their cloud. Writable: target temperature,
mode and the node label.

The room temperature feeds straight back into the annual forecast. A house kept at 21.9 °C rather than the
assumed 20 °C moves the heating limit by the same 1.9 K and shifts the heating curve's base point with it.

### Shifting heat into the sunny hours

`heat-shift` raises the target temperature by half a degree while the PV produces more than the house draws,
and puts it back when the surplus is gone — the building as a short-term heat store. It is **off by default**,
capped at 1.5 K and at 23 °C absolute, never lowers anything, stays out of the night, and always restores the
original target, including after a restart or a fault. Turn a thermostat by hand while it is raised and your
value wins. Be realistic about the size: with 1.3 kWp and an 800 W output limit this earns cents, and least of
all in winter when the heating actually runs.

## Annual forecast

Everything else in GroLo reports what happened. This one answers the question a bill asks: **what will the year cost, and
is the monthly payment to the supplier the right size?** The `forecast` sidecar models a full year hour by hour, with the
real weather of your location from the Open-Meteo archive (ERA5, one year of hourly irradiance *and* air temperature):

- **Heat pump** — the building's heat demand spread over the year in proportion to heating degree hours, plus hot water
  as a weather-independent base load, divided by the COP of each hour. The COP comes from Carnot with a quality factor
  and the flow temperature of the **Wolf's own heating curve**, read live off the eBus; once enough individual cycles
  have been recorded, the quality factor is fitted to your measured `wolf_cycle` data instead. Above the unit's rated
  output the **immersion heater** takes over at a performance factor of 1, which is why the coldest weeks cost more than
  their share of the degree hours. After about twenty heating days the model stops taking the heat demand from your gas
  figure and **measures the building instead**: a straight line of daily heat against mean outdoor temperature gives the
  heat load in kWh per kelvin and day and the real heating limit, with insulation, ventilation and how warm you like it
  already baked in.
- **PV** — irradiance on each string's surface from its tilt, azimuth and Wp, the same model the expectation curve uses.
  When no modules are configured it falls back to the orientation the weather service estimates from the measurements.
  Either way the result is **calibrated against what actually arrived**: measured yield over the last 30 days divided by
  what the model would have expected for the same hours of real weather. That one factor carries shading, dirt and a
  wrong guess at the orientation — but it moves the level only, not the shape over the year, so entering tilt and azimuth
  still pays.
- **Household** — your annual figure from the last bill if you entered one, otherwise the median of at least fourteen full
  measured days (Shelly minus the heat pump's own draw), otherwise a named default.
- **NEXA** — direct use and battery, hour by hour, inside the 800 W output limit and the usable capacity of the packs.

From the hourly grid import it builds twelve monthly totals and the year:

```
annual bill      = grid import × unit price + base fee × 12 − feed-in × feed-in rate
monthly payment  = annual bill / 12, rounded up to the next 5 €
```

and says whether the payment you actually make is too high (an interest-free loan to the supplier), too low (a bill to
settle at the end of the year) or right. **A forecast is a calculation with assumptions, not a measurement**, so every
result carries its assumption list: each figure is marked as measured, your entry, a named default or missing. The
`quality` field summarises it as `measured`, `partial` or `assumed`, and it is shown as such on all three surfaces.

Each assumption also carries a **spread**, weighted by how much of the grid import it actually drives and added in
quadrature, because the inputs can be wrong independently of each other. The result is a band around the bill rather
than a single number. The recommended payment is the expected value rounded up to the next 5 €; the upper edge of the
band is given separately for anyone who would rather build a credit than risk a bill in January.

What you enter on the settings page, under "House, heat demand and annual forecast" and in the tariff card: annual
electricity from your last bill, annual gas of the previous years with the old boiler's efficiency (the best anchor for
the heat demand), floor area as a fallback, hot water share, usable battery capacity, base fee per month and the monthly
payment you currently make. Everything else the model takes from the measurements and from your existing location and
module settings. It is published retained on `homeassistant/grolo/forecast`, lands in InfluxDB as `forecast` (year) and
`forecast_month` (per month), and appears in the Grafana row "Annual forecast", on the settings page and on the website.

## Backup

Everything here is reproducible except two things: the measurement history in InfluxDB, and a handful of small
files that are worth more than their size suggests. `growatt_matter-data` holds the keys of our Matter fabric —
lose it and every tado° X device has to be paired again by hand. `growatt_wolf-state` holds the compressor
cycle history. `.env` and the Mosquitto certificates are the only things in `/opt/growatt` that are not on
GitHub, and without them the stack does not start.

Three [restic](https://restic.net/) jobs, all encrypted, deduplicating and incremental:

| Script | Where | When | Contents | Retention |
|---|---|---|---|---|
| `scripts/grolo-backup.sh` | in the container | daily 03:20 | InfluxDB dump, volumes, `.env`, certificates | 7 daily, 4 weekly, 6 monthly |
| `scripts/pve-backup.sh` | on the host | Sundays 03:00 | `/etc/pve`, network, GRUB line, storage config | 7 / 4 / 6 |
| `scripts/pve-image-backup.sh` | on the host | 1st of month 02:00 | full `vzdump` of the container | 2 snapshots |

`scripts/backup-install.sh` installs both sides and their systemd timers; the credentials live in
`/etc/grolo-backup.env` (mode 600) and never in the repository.

Three details that are easy to get wrong:

- **Do not copy InfluxDB's live data directory.** It keeps writing while you copy, and a half-written TSM file
  is worthless on restore. `influx backup` produces a consistent snapshot instead — and it arrives already
  compressed, which turned 602 MB of raw data into a 249 MB dump here.
- **The image backup is a separate job with its own tag.** A `vzdump` is one zstd archive: restic can barely
  deduplicate it, so every snapshot costs the full 2.1 GB. Run under the same retention rule as the daily data,
  one policy would delete the other's snapshots.
- **On Backblaze B2, set the bucket lifecycle to keep only the last version.** With "keep all versions",
  restic's `prune` deletes objects but B2 keeps them as hidden versions that still count against your quota:
  the repository looks small while the free tier quietly fills up. `daysFromHidingToDeleting: 1` is the fix.

The daily run reports how much the repository occupies and warns above 8 GB, because the free tier on B2 ends
at 10. As set up here that is 2.4 GB after the first full round.

**An unverified backup is not a backup.** After setting it up, restore something and compare it with the
original — `restic restore latest --target /tmp/x --include "*/opt/.env"` — and let `restic check
--read-data-subset=5%` read part of the data back. Both are in the commit history of this repository because
both were actually run.

## Cloud relay (optional)

With the switch on, GroBro forwards the raw frames through the `cloud-gate` service to Growatt (TLS, SNI `mqtt.growatt.com`,
certificate verified against the container's trust store) and plays the cloud's answers back to the device, so ShinePhone keeps
working while the dongle itself stays blocked. `cloud-gate` reads the return direction on MQTT packet level and **drops
configuration writes** from the cloud to the dongle (Growatt message types 0x0110/0x0118). In our tests the cloud tried to
rewrite the dongle's broker within 20 seconds of the first connection. With the switch off, `cloud-gate` answers GroBro as a
silent broker and nothing leaves the network.

The cloud IPs are configured in `.env` because `mqtt.growatt.com` resolves to your own host inside the LAN.

## Services

| Service | Image | Role |
|---|---|---|
| `mosquitto` | eclipse-mosquitto:2 | TLS listener 7006 (dongle), plain 1883 (everything else), WebSocket 9001 (settings page) |
| `grobro` | ghcr.io/robertzaage/grobro | decodes Growatt frames, Home Assistant discovery, writes settings |
| `telegraf` / `influxdb` / `grafana` | official images | history and dashboards |
| `settings` | nginx | serves `settings-ui/index.html` |
| `dongle-info` | grobro image + `grobro/sidecar/dongle_info.py` | decodes the dongle's 0xFE19 configuration message (GroBro ignores it for NEXA) |
| `raw-registers` | grobro image + `grobro/sidecar/raw_registers.py` | publishes registers GroBro does not map, for research |
| `cloud-gate` | grobro image + `grobro/sidecar/cloud_gate.py` | switchable, filtering TLS relay to the Growatt cloud |
| `weather` | grobro image + `grobro/sidecar/weather.py` | Open-Meteo weather and 48 h irradiance forecast, sun position every minute, expected power per string (`solar.py`) |
| `forecast` | grobro image + `grobro/sidecar/forecast.py` | annual model of consumption, electricity bill and the monthly payment to recommend, hour by hour over a full weather year |
| `matter` | ghcr.io/matter-js/matterjs-server | local Matter controller (Open Home Foundation, matter.js), WebSocket on 5580, host network for mDNS and IPv6 (profile `tado`) |
| `tado-bridge` | grobro image + `grobro/sidecar/tado_bridge.py` | reads the tado° X rooms over Matter and writes target temperature, mode and room name back (profile `tado`) |
| `heat-shift` | grobro image + `grobro/sidecar/heat_shift.py` | raises the target temperature while the PV has a surplus and puts it back afterwards; off by default (profile `tado`) |
| `web-push` | grobro image + `grobro/sidecar/web_push.py` | pushes cleaned samples, weather and heat pump data to the optional GroLo website (Vercel) |
| `shelly-control` | grobro image + `grobro/sidecar/shelly_control.py` | local zero-feed-in: reads a Shelly meter and steers the NEXA output power |
| `wolf` | zivillian/ism7mqtt | ISM7 protocol to the WOLF Link on TLS 9092, one JSON topic per bus device (profile `wolf`) |
| `wolf-bridge` | grobro image + `grobro/sidecar/wolf_bridge.py` | keeps the full heat pump state, computes COP, spread and performance factors, records every compressor run and judges the cycling, validates and forwards control writes (profile `wolf`) |

`grobro/registers/growatt_nexa_registers.json` is a copy of GroBro's NEXA register map extended with the firmware registers
(119/120) and the serial/temperature registers of battery packs 2–4. It is mounted into the GroBro container and can be removed
once an official image ships these fields.

## Register findings (NEXA 2000)

Things learned from the raw frames that GroBro does not (yet) expose, kept here for other tinkerers:

| Register | Meaning |
|---|---|
| Input 33–40 / 45–52 / 57–64 | serial numbers of battery packs 2/3/4 (ASCII), SoC at 41/53/65, temperature at 42/54/66 (same layout as NOAH) |
| Input 119/120 | firmware version, four byte-sized parts |
| Input 116 | **actual AC output power** in steps of 0.1 W with offset 30000 = 0 W (38000 = 800 W, checked against the SoC drop of two packs). On firmware 4.0.2.6 the registers `pac` (5), battery power (11) and the energy counters (`eacToday` …) stay at 0, so the dashboard computes output from 116, PV from string voltage × current, and battery power as PV − output |
| Input 115 | grid voltage ×0.01 (≈ 234 V when grid-tied, ≈ 0 V when disconnected) |
| Input 367/368 | probably pack voltages ×10 (16s LFP ≈ 52 V) |
| Input 113, 371, 372 | temperatures ×100 |
| Holding 45–49 | device clock (year, month, day, hour, minute) |
| Holding 56–70 | model/type code string, 208–216 serial, 328–331 version string `9.0.0.0` |
| Holding 299 | 800, probably max AC output (cloud "Power+" switches to 1000 W); 341…357 one value per slot (100) |
| Message 0x0103 | hourly dump of all holding registers; 0xFE19 dongle configuration on connect; 0x6F64 smart-meter JSON |

Settings the Growatt cloud knows but no register is known for: Power+ (1000 W), AC coupling, anti-backflow limit,
"never power off". Identify them by toggling in the app and comparing the next hourly dump.

**Output above 800 W?** Tested 2026-09-14: holding register 299 (reads 800) is writable through a mapped GroBro register
(values 900 and 1000 were accepted and read back), but it does **not** lift the slot power limit: with 299 = 1000 a slot power
of 850 or 1000 W is still rejected and the NEXA keeps 800 W (register 116 = 800.0 W). The 1000 W option in ShinePhone is
therefore a different, so far unknown setting (possibly the cloud-only "Power+" / `ac_couple_power_control`). Register 299 was
set back to 800 and the mapping removed. In Germany the simplified balcony registration ends at 800 W anyway.

## Dongle parameters and the Shelly lab

The Wi-Fi dongle (an ESP32, `GTSW0000`) keeps about 145 configuration parameters that can be read with message type
0x0119 and written with 0x0118 on `s/33/<serial>`; answers arrive on `c/33/<serial>`. `scripts/dongle-param.sh <id>`
reads one parameter in clear text. Known ids: 4 interval, 17-19 broker, 30 time zone, 31 clock, 32 restart, **35 IOT
module off** (the dongle leaves the Wi-Fi; a short press on the NEXA's IOT button then starts the pairing mode and
ShinePhone can set the Wi-Fi again over Bluetooth), 56/57 Wi-Fi SSID and password (readable in clear text by anyone on
the broker, keep port 1883 inside the LAN), 76 Wi-Fi signal, 102/122 read-only device status, 118 forces a reconnect.
Setting every other zero-valued parameter to 1 had no effect, so the smart-meter pairing is not a simple dongle flag.

The `dongle-info` sidecar exposes `<base>/grolo/dongle/param/read` (`{"reg": 20}`) and `.../set` (`{"reg": 35, "value": "1"}`,
only ids in `WRITABLE_PARAMS`, default 35); results come back on `.../param/result`. The settings page uses it for the
**Pairing mode** button. `scripts/shelly-lab.py` (runs in the GroBro image) reads, writes and probes parameters with a
block list and reports to `grolo/lab/log`, shown live by `settings-ui/shelly-lab.html`. `cloud-gate` decodes every command
the cloud sends to the device, stores it under `grobro/dump/cloud_down/`, reports it to the same live log and blocks only
writes to protected dongle parameters (`PROTECTED_PARAMS`).

## Safety notes

- Slot power (`slotN_power`) is held in RAM and safe for frequent writes; `default_power` is written to flash, change it rarely
  (see GroBro's hardware notes).
- Do not write to unknown registers. Register 7 of the dongle configuration is its password.
- Broker/port/restart controls on the settings page ask for confirmation; a wrong value disconnects the dongle from your stack.

## Credits and links

- [GroBro](https://github.com/robertzaage/GroBro) by Robert Zaage: the decoder this stack is built on. Read its
  [CONFIGURATION.md](https://github.com/robertzaage/GroBro/blob/main/CONFIGURATION.md) and
  [CERTIFICATES.md](https://github.com/robertzaage/GroBro/blob/main/CERTIFICATES.md).
- [nexa-mqtt](https://github.com/mgerczuk/nexa-mqtt) for the list of cloud parameters, [Grott](https://github.com/johanmeijer/grott)
  for the datalogger register notes.
- [ism7mqtt](https://github.com/zivillian/ism7mqtt) by zivillian: the local ISM7 protocol implementation this stack talks to the
  WOLF Link with, including Wolf's own parameter, menu and translation resources. Its
  [PROTOCOL.md](https://github.com/zivillian/ism7mqtt/blob/master/PROTOCOL.md) documents the wire format.
- Home Assistant's [wolflink](https://www.home-assistant.io/integrations/wolflink/) integration for the parameter list to
  cross-check against — it goes through the Wolf cloud and is read-only, which is why this stack does not use it.
- [acme.sh](https://github.com/acmesh-official/acme.sh), [DuckDNS](https://www.duckdns.org), [Let's Encrypt chain](https://letsencrypt.org/certificates/).

## Repository layout

```
docker-compose.yml           all services
.env.example                 configuration template
mosquitto/config/            broker configuration (three listeners)
mosquitto/certs/             certificates (gitignored)
scripts/setup-certs.sh       Let's Encrypt via acme.sh + DuckDNS
scripts/build-chain.sh       assemble and verify the full chain
scripts/verify.sh            16-step health check
scripts/test-panels.py       run every Grafana panel query
scripts/fit-orientation.py   estimate tilt/azimuth per string from the measurements
grobro/sidecar/forecast.py   annual forecast: consumption, bill and recommended monthly payment
scripts/wolf-config.sh       ask the WOLF Link which devices are on the eBus -> wolf/parameter.json
scripts/wolf-catalog.py      build wolf/catalog.json from Wolf's own resources (names, units, limits, menus)
scripts/wolf-dashboard.py    generates grafana/dashboards/wolf-de.json and wolf-en.json
scripts/wolf-nexa-link.py    adds the heat pump row and link to the NEXA dashboards
telegraf/telegraf.conf       MQTT → InfluxDB
grafana/build-dashboard.py   generates grafana/dashboards/nexa-en.json and nexa-de.json
grafana/provisioning/        data source and dashboard provider
settings-ui/                 GroLo settings page and wolf.html heat pump controls (static, MQTT over WebSocket)
grobro/sidecar/              dongle_info.py, raw_registers.py, cloud_gate.py, weather.py, solar.py, web_push.py,
                             shelly_control.py, wolf_bridge.py
grobro/registers/            extended NEXA register map
wolf/                        parameter.json and catalog.json of the heat pump installation (generated)
docs/                        screenshots (serial numbers masked)
VERSION                      2026.40.2
```

License: MIT.
