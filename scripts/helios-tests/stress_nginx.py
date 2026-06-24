"""
Stress test for Nginx — measures latency, error rate, throughput.
Usage:
    pip install aiohttp
    python stress_nginx.py --url http://192.168.148.158:31080 --duration 120 --workers 50
"""

import argparse
import asyncio
import csv
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import aiohttp


@dataclass
class Sample:
    ts: float
    latency_ms: float
    status: int
    error: str = ""


@dataclass
class Results:
    samples: list = field(default_factory=list)
    start: float = 0.0
    end: float = 0.0

    def add(self, s: Sample):
        self.samples.append(s)

    def successes(self):
        return [s for s in self.samples if 200 <= s.status < 400]

    def errors(self):
        return [s for s in self.samples if s.status >= 400 or s.error]

    def latencies(self):
        return [s.latency_ms for s in self.successes()]

    def summary(self) -> dict:
        total = len(self.samples)
        ok = len(self.successes())
        err = len(self.errors())
        duration = self.end - self.start or 1
        lats = sorted(self.latencies()) or [0]

        def pct(p):
            idx = int(len(lats) * p / 100)
            return round(lats[min(idx, len(lats) - 1)], 2)

        return {
            "total_requests": total,
            "successful": ok,
            "errors": err,
            "error_rate_pct": round(err / total * 100, 2) if total else 0,
            "availability_pct": round(ok / total * 100, 2) if total else 0,
            "throughput_rps": round(total / duration, 2),
            "latency_p50_ms": pct(50),
            "latency_p95_ms": pct(95),
            "latency_p99_ms": pct(99),
            "latency_max_ms": round(max(lats), 2),
            "latency_mean_ms": round(statistics.mean(lats), 2),
        }

    def errors_by_type(self) -> dict:
        counts = defaultdict(int)
        for s in self.errors():
            key = s.error if s.error else f"HTTP_{s.status}"
            counts[key] += 1
        return dict(counts)


async def worker(session: aiohttp.ClientSession, url: str, results: Results,
                 stop_event: asyncio.Event, ramp_end: float):
    while not stop_event.is_set():
        # Respect ramp-up: start slow, reach full speed after ramp
        if time.monotonic() < ramp_end:
            await asyncio.sleep(0.05)

        t0 = time.monotonic()
        status, error = 0, ""
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                await resp.read()
                status = resp.status
        except asyncio.TimeoutError:
            error = "TIMEOUT"
        except aiohttp.ClientConnectorError:
            error = "CONNECTION_REFUSED"
        except aiohttp.ServerDisconnectedError:
            error = "SERVER_DISCONNECTED"
        except Exception as e:
            error = type(e).__name__

        latency = (time.monotonic() - t0) * 1000
        results.add(Sample(ts=time.time(), latency_ms=latency, status=status, error=error))


def print_progress(results: Results, elapsed: float, duration: int):
    s = results.summary()
    bar_len = 30
    filled = int(bar_len * elapsed / duration)
    bar = "=" * filled + ">" + " " * (bar_len - filled)
    print(
        f"\r[{bar}] {elapsed:.0f}s/{duration}s | "
        f"RPS:{s['throughput_rps']} | "
        f"OK:{s['availability_pct']}% | "
        f"p95:{s['latency_p95_ms']}ms | "
        f"ERR:{s['error_rate_pct']}%",
        end="", flush=True,
    )


async def run(url: str, duration: int, workers: int, ramp_seconds: int, output_csv: str):
    results = Results()
    stop_event = asyncio.Event()

    connector = aiohttp.TCPConnector(limit=workers + 10, force_close=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        results.start = time.monotonic()
        ramp_end = results.start + ramp_seconds

        tasks = [
            asyncio.create_task(worker(session, url, results, stop_event, ramp_end))
            for _ in range(workers)
        ]

        deadline = results.start + duration
        while time.monotonic() < deadline:
            await asyncio.sleep(1)
            print_progress(results, time.monotonic() - results.start, duration)

        stop_event.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        results.end = time.monotonic()

    print()  # newline after progress bar
    return results


def save_csv(results: Results, path: str):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_iso", "latency_ms", "status", "error"])
        for s in results.samples:
            w.writerow([
                datetime.fromtimestamp(s.ts).isoformat(),
                round(s.latency_ms, 2),
                s.status,
                s.error,
            ])
    print(f"Raw samples saved → {path}")


def evaluate(summary: dict, thresholds: dict) -> list[str]:
    """Return list of SLO violations."""
    violations = []
    if summary["availability_pct"] < thresholds["availability_pct"]:
        violations.append(
            f"Availability {summary['availability_pct']}% < {thresholds['availability_pct']}%"
        )
    if summary["latency_p95_ms"] > thresholds["latency_p95_ms"]:
        violations.append(
            f"p95 latency {summary['latency_p95_ms']}ms > {thresholds['latency_p95_ms']}ms"
        )
    if summary["error_rate_pct"] > thresholds["error_rate_pct"]:
        violations.append(
            f"Error rate {summary['error_rate_pct']}% > {thresholds['error_rate_pct']}%"
        )
    return violations


def main():
    parser = argparse.ArgumentParser(description="Nginx stress test for HELIOS resilience validation")
    parser.add_argument("--url", default="http://192.168.148.158:31080", help="Target URL")
    parser.add_argument("--duration", type=int, default=120, help="Test duration in seconds")
    parser.add_argument("--workers", type=int, default=50, help="Concurrent HTTP workers")
    parser.add_argument("--ramp", type=int, default=10, help="Ramp-up period in seconds")
    parser.add_argument("--output", default="stress_results.csv", help="CSV output file")
    # SLO thresholds
    parser.add_argument("--slo-availability", type=float, default=99.0)
    parser.add_argument("--slo-p95-ms", type=float, default=500.0)
    parser.add_argument("--slo-error-rate", type=float, default=1.0)
    args = parser.parse_args()

    print(f"[HELIOS Stress Test] Target: {args.url}")
    print(f"Workers: {args.workers} | Duration: {args.duration}s | Ramp: {args.ramp}s")
    print(f"SLOs — Availability ≥ {args.slo_availability}% | p95 ≤ {args.slo_p95_ms}ms | "
          f"Error rate ≤ {args.slo_error_rate}%\n")

    results = asyncio.run(run(args.url, args.duration, args.workers, args.ramp, args.output))

    summary = results.summary()
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:<25} {v}")

    errors = results.errors_by_type()
    if errors:
        print("\nError breakdown:")
        for k, v in sorted(errors.items(), key=lambda x: -x[1]):
            print(f"  {k:<30} {v}")

    thresholds = {
        "availability_pct": args.slo_availability,
        "latency_p95_ms": args.slo_p95_ms,
        "error_rate_pct": args.slo_error_rate,
    }
    violations = evaluate(summary, thresholds)
    print("\n" + "=" * 60)
    if violations:
        print("SLO VIOLATIONS DETECTED (system should have triggered HELIOS remediation):")
        for v in violations:
            print(f"  ✗ {v}")
        print("\nExpected HELIOS behavior: detection → SCALE_DEPLOYMENT or RESTART_POD → RESOLVED")
    else:
        print("✓ All SLOs met — no remediation expected")
    print("=" * 60)

    save_csv(results, args.output)


if __name__ == "__main__":
    main()
