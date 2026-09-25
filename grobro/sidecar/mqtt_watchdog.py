"""Wachhund gegen die Leerlaufschleife von paho.loop_forever().

Am 25.09.2026 stand dongle-info 17 Stunden lang auf 100 % CPU, ohne eine einzige Nachricht zu
verarbeiten: paho 2.1.0 dreht nach einem halb offenen Socket in loop_forever() weiter, statt die
Verbindung neu aufzubauen. Der Prozess sah von außen gesund aus – Container "Up", TCP-Verbindung
"ESTABLISHED", kein Fehler im Log – und lieferte trotzdem nichts mehr. Erkennbar war es nur an
/proc/self/stat: State R, 2,5 Mio. unfreiwillige gegen 6,6 Tausend freiwillige Kontextwechsel.

Erkennungsregel: viel CPU *und* keine eingehende Nachricht. Nur zusammen ist es eine Leerlaufschleife.
Ein stiller Broker verbraucht keine CPU, ein wirklich beschäftigter Dienst bekommt Nachrichten – beide
lösen nicht aus. Ausgelöst wird der Prozess beendet; die restart-policy des Containers holt ihn zurück.
"""
import logging
import os
import resource
import threading
import time

LOG = logging.getLogger("mqtt-watchdog")


def _cpu_seconds():
    """Verbrauchte CPU-Zeit dieses Prozesses (Benutzer + System) in Sekunden.

    Über resource statt /proc/self/stat: das gibt es auch dort, wo kein procfs eingehängt ist,
    und es lässt sich auf dem Mac testen, ohne den Linux-Pfad nachzubauen.
    """
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def install(client, name, idle_s=600.0, busy_pct=50.0, check_s=60.0):
    """Hängt sich in client.on_message und überwacht den Prozess im Hintergrund.

    idle_s   – so lange darf keine Nachricht kommen, bevor es verdächtig wird
    busy_pct – so viel CPU muss er dabei ziehen, damit es als Leerlaufschleife gilt
    """
    state = {"last": time.time()}
    inner = client.on_message

    def on_message(c, userdata, msg):
        state["last"] = time.time()
        if inner is not None:
            inner(c, userdata, msg)

    client.on_message = on_message
    threading.Thread(target=_watch, args=(state, name, idle_s, busy_pct, check_s), daemon=True).start()
    LOG.debug("Wachhund aktiv (%s): Abbruch bei > %.0f %% CPU und > %.0f s ohne Nachricht",
              name, busy_pct, idle_s)


def _watch(state, name, idle_s, busy_pct, check_s):
    try:
        _watch_loop(state, name, idle_s, busy_pct, check_s)
    except Exception:
        # Ein Wächter, der unbemerkt ausfällt, ist schlimmer als keiner: laut sein und aufgeben.
        LOG.exception("Wachhund (%s) abgestürzt – der Dienst läuft ab jetzt unbewacht weiter", name)


def _watch_loop(state, name, idle_s, busy_pct, check_s):
    last_cpu, last_wall = _cpu_seconds(), time.monotonic()
    while True:
        time.sleep(check_s)
        cpu, wall = _cpu_seconds(), time.monotonic()
        pct = (cpu - last_cpu) / max(wall - last_wall, 1e-6) * 100.0
        last_cpu, last_wall = cpu, wall
        idle = time.time() - state["last"]
        if pct > busy_pct and idle > idle_s:
            LOG.error("%s dreht leer: %.0f %% CPU, aber seit %.0f s keine Nachricht. "
                      "Prozess wird beendet, der Container startet ihn neu.", name, pct, idle)
            os._exit(1)
