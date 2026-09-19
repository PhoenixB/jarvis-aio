"""Tests for host hardware telemetry grading (Zorin/Linux server stress).

Stdlib-only; loads host_telemetry standalone. The file readers hit /proc and
/sys and are inherently host-specific, so these pin the pure ``grade`` /
``has_any`` logic and the threshold boundaries — the part that decides whether
host stress becomes a spoken alert.
"""
import importlib.util
import pathlib
import sys

COMP = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, COMP / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ht = _load("jarvis_host_telemetry", "host_telemetry.py")

_CRIT = 3
_WARN = 2


def _labels(findings):
    return {f["label"] for f in findings}


def _sev(findings, label):
    return next(f["severity"] for f in findings if f["label"] == label)


def test_all_none_metrics_grade_empty_and_not_available():
    metrics = {"cpu_temp_c": None, "mem_used_pct": None,
               "mem_psi_avg10": None, "nvme_util_pct": None}
    assert ht.grade(metrics) == []
    assert ht.has_any(metrics) is False


def test_empty_dict_is_safe():
    assert ht.grade({}) == []
    assert ht.has_any({}) is False


def test_healthy_metrics_produce_no_findings():
    metrics = {"cpu_temp_c": 45.0, "mem_used_pct": 60.0,
               "mem_psi_avg10": 2.0, "nvme_util_pct": 30.0}
    assert ht.grade(metrics) == []
    assert ht.has_any(metrics) is True          # readable, just not stressed


def test_cpu_warn_then_critical_boundaries():
    warn = ht.grade({"cpu_temp_c": ht.CPU_TEMP_WARN_C + 0.1})
    assert _sev(warn, "CPU temperature") == _WARN
    crit = ht.grade({"cpu_temp_c": ht.CPU_TEMP_CRIT_C + 0.1})
    assert _sev(crit, "CPU temperature") == _CRIT
    # exactly at the warn threshold is not yet a finding (strictly above)
    assert ht.grade({"cpu_temp_c": ht.CPU_TEMP_WARN_C}) == []


def test_memory_used_and_pressure_grade_independently():
    f = ht.grade({"mem_used_pct": ht.MEM_USED_CRIT_PCT + 1,
                  "mem_psi_avg10": ht.MEM_PSI_WARN + 1})
    assert _sev(f, "host memory") == _CRIT
    assert _sev(f, "memory pressure") == _WARN


def test_nvme_only_warns_and_only_when_saturated():
    assert ht.grade({"nvme_util_pct": 90.0}) == []          # below warn
    f = ht.grade({"nvme_util_pct": ht.NVME_UTIL_WARN_PCT + 1})
    assert _sev(f, "NVMe I/O") == _WARN


def test_snapshot_shape_and_critical_flag(monkeypatch):
    monkeypatch.setattr(ht, "read_metrics", lambda: {
        "cpu_temp_c": ht.CPU_TEMP_CRIT_C + 5, "mem_used_pct": 50.0,
        "mem_psi_avg10": 1.0, "nvme_util_pct": 10.0,
        "nvme_read_mbps": 0.0, "nvme_write_mbps": 0.0,
    })
    snap = ht.snapshot()
    assert snap["available"] is True
    assert snap["critical"] is True
    assert "CPU temperature" in _labels(snap["findings"])


def test_is_enabled_default_true_without_config():
    # No jarvis_config importable in this standalone context → defaults to on.
    assert ht.is_enabled() is True
