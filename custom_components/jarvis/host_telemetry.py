"""JARVIS — Zorin/Linux host hardware telemetry.

Reads the *physical* server's stress signals straight from the kernel's
pseudo-filesystems (``/proc`` and ``/sys``) — CPU package temperature, system
memory pressure (which is where a runaway local Ollama model shows up first),
and NVMe I/O saturation. When any of these cross a critical threshold they feed
the same infrastructure audit that already speaks urgent warnings, so host
stress that would otherwise only show up as sluggish AI latency becomes an
explicit, spoken alert.

Design constraints, in the spirit of the rest of JARVIS-AIO:
  - **Zero new dependencies.** No psutil, no native wheels. Everything here is
    stdlib file reads against ``/proc`` and ``/sys``. On a host (or container)
    that doesn't expose a given file, that metric is simply ``None`` and the
    grader stays quiet about it — it never guesses and never crashes.
  - **Off the event loop.** ``read_metrics()`` does blocking file reads and a
    short sleep to sample NVMe utilisation across an interval, so callers must
    run it via ``hass.async_add_executor_job`` — never directly on the loop.
  - **Pure grading.** ``grade()`` takes an already-read metrics dict and returns
    plain finding dicts, so it can run anywhere (including inside the synchronous
    InfrastructureTriage.evaluate) and is trivially unit-testable.

Nothing here raises to the caller; every reader is individually guarded.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# Severity ranks mirror diagnostics.monitor so findings compose cleanly.
_SEV_CRITICAL = 3
_SEV_WARNING = 2

# ── Thresholds ───────────────────────────────────────────────────────────────
# Conservative on purpose: a warning is a nudge, a critical is "this is degrading
# the assistant now". NVMe utilisation spikes to 100% for a moment on any real
# write, so it only warns and only at a genuinely saturated sustained level.
CPU_TEMP_WARN_C = 82.0
CPU_TEMP_CRIT_C = 92.0
MEM_USED_WARN_PCT = 90.0
MEM_USED_CRIT_PCT = 95.0
MEM_PSI_WARN = 20.0        # /proc/pressure/memory "some avg10" — % of time stalled
MEM_PSI_CRIT = 40.0
NVME_UTIL_WARN_PCT = 95.0

_SECTOR_BYTES = 512
_NVME_SAMPLE_GAP_S = 0.25  # spacing between the two diskstats reads


def _cfg(key: str, default):
    try:
        from . import jarvis_config
        val = jarvis_config.get(key, default)
        return val if val is not None else default
    except Exception:
        return default


def is_enabled() -> bool:
    """Host telemetry is on by default. It is self-limiting — on a host that
    doesn't expose these kernel files every metric reads ``None`` and nothing is
    graded — so the default costs nothing where it can't apply."""
    return bool(_cfg("host_telemetry", True))


# ── Individual readers (each fully guarded, return None on any problem) ────────
def _read_cpu_temp_c() -> Optional[float]:
    """Hottest sane CPU/SoC temperature in °C, or None.

    Prefers ``/sys/class/thermal/thermal_zone*/temp`` (milli-°C); falls back to
    ``hwmon`` ``temp*_input``. Values outside a plausible band are discarded so a
    bogus sensor can't fabricate an alert.
    """
    best: Optional[float] = None
    candidates: list[Path] = []
    try:
        candidates += sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp"))
    except Exception:
        pass
    try:
        candidates += sorted(Path("/sys/class/hwmon").glob("hwmon*/temp*_input"))
    except Exception:
        pass
    for path in candidates:
        try:
            raw = path.read_text().strip()
            milli = float(raw)
        except Exception:
            continue
        celsius = milli / 1000.0
        # Plausible physical range for silicon; rejects 0/garbage and °C files
        # that were already in °C would read absurdly low after /1000 → dropped.
        if 20.0 <= celsius <= 130.0 and (best is None or celsius > best):
            best = celsius
    return round(best, 1) if best is not None else None


def _read_meminfo() -> dict:
    """Parse the fields of /proc/meminfo we care about (values in kB)."""
    out: dict = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts:
                try:
                    out[key.strip()] = float(parts[0])
                except ValueError:
                    continue
    except Exception:
        return {}
    return out


def _read_mem_used_pct() -> Optional[float]:
    mi = _read_meminfo()
    total = mi.get("MemTotal")
    avail = mi.get("MemAvailable")
    if not total or avail is None or total <= 0:
        return None
    used = max(0.0, min(100.0, (1.0 - avail / total) * 100.0))
    return round(used, 1)


def _read_mem_psi_avg10() -> Optional[float]:
    """Memory pressure from PSI: the "some avg10" figure — the percentage of the
    last 10s at least one task stalled waiting on memory. The earliest, clearest
    signal that the box (often a hungry local model) is thrashing."""
    try:
        for line in Path("/proc/pressure/memory").read_text().splitlines():
            if line.startswith("some"):
                for tok in line.split():
                    if tok.startswith("avg10="):
                        return round(float(tok.split("=", 1)[1]), 1)
    except Exception:
        return None
    return None


def _read_nvme_counters() -> dict:
    """Map of nvme block device -> (ms_doing_io, sectors_read, sectors_written)
    from /proc/diskstats. Only whole devices (e.g. ``nvme0n1``), not partitions
    (``nvme0n1p2``), so I/O isn't double counted."""
    out: dict = {}
    try:
        for line in Path("/proc/diskstats").read_text().splitlines():
            f = line.split()
            if len(f) < 14:
                continue
            name = f[2]
            if not name.startswith("nvme"):
                continue
            if "p" in name.split("n", 1)[-1]:   # nvme0n1p2 -> has a partition suffix
                continue
            try:
                sectors_read = float(f[5])
                sectors_written = float(f[9])
                ms_doing_io = float(f[12])
            except (ValueError, IndexError):
                continue
            out[name] = (ms_doing_io, sectors_read, sectors_written)
    except Exception:
        return {}
    return out


def _read_nvme_activity() -> dict:
    """Sample diskstats twice to derive instantaneous NVMe busy % and throughput.

    Returns {util_pct, read_mbps, write_mbps} across the busiest device, or an
    empty dict if there are no NVMe devices / diskstats is unreadable. Blocking
    (it sleeps ``_NVME_SAMPLE_GAP_S``) — call via an executor.
    """
    first = _read_nvme_counters()
    if not first:
        return {}
    t0 = time.monotonic()
    time.sleep(_NVME_SAMPLE_GAP_S)
    second = _read_nvme_counters()
    dt = time.monotonic() - t0
    if not second or dt <= 0:
        return {}

    best_util = 0.0
    best_read = 0.0
    best_write = 0.0
    for name, (ms1, r1, w1) in first.items():
        if name not in second:
            continue
        ms2, r2, w2 = second[name]
        busy_ms = max(0.0, ms2 - ms1)
        util = min(100.0, busy_ms / (dt * 1000.0) * 100.0)
        read_mbps = max(0.0, (r2 - r1) * _SECTOR_BYTES / dt / 1_000_000.0)
        write_mbps = max(0.0, (w2 - w1) * _SECTOR_BYTES / dt / 1_000_000.0)
        if util >= best_util:
            best_util = util
            best_read = read_mbps
            best_write = write_mbps
    return {
        "util_pct": round(best_util, 1),
        "read_mbps": round(best_read, 1),
        "write_mbps": round(best_write, 1),
    }


# ── Public API ────────────────────────────────────────────────────────────────
def read_metrics() -> dict:
    """Read every host metric once. Blocking — run via an executor. Keys are
    always present; a metric the host doesn't expose is ``None``."""
    nvme = _read_nvme_activity()
    return {
        "cpu_temp_c": _read_cpu_temp_c(),
        "mem_used_pct": _read_mem_used_pct(),
        "mem_psi_avg10": _read_mem_psi_avg10(),
        "nvme_util_pct": nvme.get("util_pct"),
        "nvme_read_mbps": nvme.get("read_mbps"),
        "nvme_write_mbps": nvme.get("write_mbps"),
    }


def has_any(metrics: dict) -> bool:
    """True if the host exposed at least one of the graded stress metrics."""
    return any(
        metrics.get(k) is not None
        for k in ("cpu_temp_c", "mem_used_pct", "mem_psi_avg10", "nvme_util_pct")
    )


def grade(metrics: dict) -> list[dict]:
    """Grade already-read metrics into finding dicts, most-severe kept as-is.

    Each finding is ``{severity, phrase, label}`` where ``phrase`` is a
    self-contained spoken clause in JARVIS's register and ``severity`` matches
    diagnostics.monitor's ranks, so InfrastructureTriage can fold these straight
    into its verdict. Pure — no I/O, never raises on well-formed input.
    """
    findings: list[dict] = []
    if not metrics:
        return findings

    temp = metrics.get("cpu_temp_c")
    if temp is not None:
        if temp > CPU_TEMP_CRIT_C:
            findings.append({"severity": _SEV_CRITICAL, "label": "CPU temperature",
                             "phrase": f"the CPU is running critically hot at {temp:.0f} degrees"})
        elif temp > CPU_TEMP_WARN_C:
            findings.append({"severity": _SEV_WARNING, "label": "CPU temperature",
                             "phrase": f"the CPU is warm at {temp:.0f} degrees"})

    mem = metrics.get("mem_used_pct")
    if mem is not None:
        if mem > MEM_USED_CRIT_PCT:
            findings.append({"severity": _SEV_CRITICAL, "label": "host memory",
                             "phrase": f"host memory is nearly exhausted at {mem:.0f} percent"})
        elif mem > MEM_USED_WARN_PCT:
            findings.append({"severity": _SEV_WARNING, "label": "host memory",
                             "phrase": f"host memory is high at {mem:.0f} percent"})

    psi = metrics.get("mem_psi_avg10")
    if psi is not None:
        if psi > MEM_PSI_CRIT:
            findings.append({"severity": _SEV_CRITICAL, "label": "memory pressure",
                             "phrase": f"the host is thrashing — memory pressure is at {psi:.0f} percent"})
        elif psi > MEM_PSI_WARN:
            findings.append({"severity": _SEV_WARNING, "label": "memory pressure",
                             "phrase": f"memory pressure is building at {psi:.0f} percent"})

    nvme = metrics.get("nvme_util_pct")
    if nvme is not None and nvme > NVME_UTIL_WARN_PCT:
        findings.append({"severity": _SEV_WARNING, "label": "NVMe I/O",
                         "phrase": f"the NVMe drive is saturated at {nvme:.0f} percent utilisation"})

    return findings


def snapshot() -> dict:
    """Read + grade in one call, for the ``system_diagnostics`` tool / service
    health. Blocking — run via an executor. Returns
    ``{available, metrics, findings, critical}``."""
    metrics = read_metrics()
    findings = grade(metrics)
    return {
        "available": has_any(metrics),
        "metrics": metrics,
        "findings": findings,
        "critical": any(f["severity"] >= _SEV_CRITICAL for f in findings),
    }
