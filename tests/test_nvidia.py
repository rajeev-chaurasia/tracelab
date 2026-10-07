"""NVIDIA collectors against fixtures built to the documented formats.

There is no NVIDIA GPU on the development machine, so these pin the parsing
and the timeline placement, not the behaviour of a real driver.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from tracelab.collect.nvidia import import_nsys, parse_smi

SMI = """\
2026/10/07 15:30:01.100, 0, 87, 45, 12034, 210.53, 67, 1785
2026/10/07 15:30:01.200, 0, 91, 47, 12040, [N/A], 67, 1785
garbage line
"""


def test_smi_lines_become_scaled_samples_on_the_wall_clock() -> None:
    samples = parse_smi(SMI)

    first = {s.name: s for s in samples[:6]}
    assert first["gpu_util"].value == pytest.approx(0.87)
    assert first["gpu_memory_used"].value == 12034 * 2**20
    assert first["gpu_power"].value == pytest.approx(210.53)
    assert {s.source for s in samples} == {"gpu0"}
    assert {s.domain for s in samples} == {"wall"}
    expected = int(datetime(2026, 10, 7, 15, 30, 1, 100000).timestamp() * 1e9)
    assert first["gpu_util"].t_ns == expected


def test_an_unsupported_reading_is_left_out_not_zeroed() -> None:
    second = [s for s in parse_smi(SMI) if s.t_ns != parse_smi(SMI)[0].t_ns]

    assert "gpu_power" not in {s.name for s in second}
    assert len(second) == 5


def nsys_fixture(path: Path) -> Path:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE TARGET_INFO_SESSION_START_TIME (
            utcEpochNs INTEGER, utcTime TEXT, localTime TEXT);
        CREATE TABLE StringIds (id INTEGER PRIMARY KEY, value TEXT);
        CREATE TABLE CUPTI_ACTIVITY_KIND_KERNEL (
            start INTEGER, end INTEGER, deviceId INTEGER, streamId INTEGER, demangledName INTEGER);
        CREATE TABLE CUPTI_ACTIVITY_KIND_MEMCPY (
            start INTEGER, end INTEGER, deviceId INTEGER, bytes INTEGER, copyKind INTEGER);
        INSERT INTO TARGET_INFO_SESSION_START_TIME VALUES (1791400000000000000, '', '');
        INSERT INTO StringIds VALUES (7, 'rmsnorm_fused_kernel');
        INSERT INTO CUPTI_ACTIVITY_KIND_KERNEL VALUES (5000, 9000, 0, 13, 7);
        INSERT INTO CUPTI_ACTIVITY_KIND_MEMCPY VALUES (1000, 3000, 0, 1048576, 1);
        """
    )
    db.commit()
    db.close()
    return path


def test_nsys_kernels_and_copies_land_at_session_start_plus_offset(tmp_path: Path) -> None:
    samples = import_nsys(nsys_fixture(tmp_path / "report.sqlite"))

    kernel = next(s for s in samples if s.name == "rmsnorm_fused_kernel")
    copy = next(s for s in samples if s.name.startswith("memcpy"))
    assert kernel.t_ns == 1791400000000000000 + 5000
    assert kernel.duration_ns == 4000
    assert kernel.source == "gpu0.stream13"
    assert copy.value == 1048576
    assert copy.duration_ns == 2000
    assert {s.domain for s in samples} == {"wall"}
