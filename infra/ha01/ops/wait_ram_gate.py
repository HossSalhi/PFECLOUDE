#!/usr/bin/env python3
"""
HA-01 — attend que la RAM disponible de l'hôte soit >= SEUIL pendant toute une fenêtre (défaut 2000 Mo / 10 min), d'après
l'échantillonneur (samples-*.jsonl, une mesure toutes les ~10 s), puis relance ops/gate0_check.py et écrit le verdict.
Sort avec le code de gate0_check (0 = GATE-0 VALIDÉ). Sans effet sur le cluster (lecture seule).
  python wait_ram_gate.py [--threshold 2000] [--window-min 10] [--max-wait-min 480]
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.normpath(os.path.join(HERE, "..", "results", "2026-11-03"))
SAMPLES = os.path.join(RES, "samples-baseline-p0.jsonl")


def epoch(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def window(min_window_s):
    rows = []
    with open(SAMPLES, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        fh.seek(max(0, fh.tell() - 400000))
        for line in fh.read().decode("utf-8", "replace").splitlines():
            if '"host_avail_mb"' in line:
                try:
                    r = json.loads(line)
                    rows.append((epoch(r["ts"]), r["host_avail_mb"]))
                except Exception:  # noqa: BLE001
                    pass
    now = time.time()
    return [v for t, v in rows if now - t <= min_window_s], (now - rows[-1][0]) if rows else 9e9


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=int, default=2000)
    ap.add_argument("--window-min", type=int, default=10)
    ap.add_argument("--max-wait-min", type=int, default=480)
    a = ap.parse_args()
    t_end = time.time() + a.max_wait_min * 60
    last_log = 0
    while time.time() < t_end:
        vals, age = window(a.window_min * 60)
        ok = len(vals) >= 3 and age < 40 and min(vals) >= a.threshold and (time.time() - 0) > 0
        # la fenêtre doit être COUVERTE : au moins 90 % des échantillons attendus
        if ok and len(vals) >= a.window_min * 60 / 10 * 0.9:
            print("%s RAM >= %d Mo sur %d min (min=%d, n=%d) -> évaluation du GATE-0" % (datetime.now(timezone.utc).strftime("%H:%M:%SZ"), a.threshold, a.window_min, min(vals), len(vals)), flush=True)
            out = subprocess.run([sys.executable, os.path.join(HERE, "gate0_check.py")], capture_output=True, text=True, encoding="utf-8")
            open(os.path.join(RES, "GATE-0-mesures.md"), "w", encoding="utf-8").write(out.stdout)
            print(out.stdout[-1500:], flush=True)
            return out.returncode
        if time.time() - last_log > 300:
            last_log = time.time()
            print("%s en attente: RAM hôte actuelle=%s Mo, min fenêtre=%s Mo (n=%d)" % (datetime.now(timezone.utc).strftime("%H:%M:%SZ"), vals[-1] if vals else None, min(vals) if vals else None, len(vals)), flush=True)
        time.sleep(15)
    print("délai maximal atteint sans RAM suffisante", flush=True)
    return 3


if __name__ == "__main__":
    sys.exit(main())
