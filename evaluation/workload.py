"""The two benchmarks the evaluation corpus is measured from.

Each invocation is one run: a fresh process, its own warmups, then measured
repetitions, printed as one JSON sample per line in the run artifact's sample
shape. Running each in its own process matters. Run-to-run noise comes from
process-level state, and a corpus collected inside one long-lived process
would not contain it.

    python -m evaluation.workload matmul
    python -m evaluation.workload periodic
"""

from __future__ import annotations

import json
import os
import resource
import socket
import sys
import threading
import time

import numpy as np

MATMUL = {"warmups": 5, "repetitions": 200, "size": 256}
PERIODIC = {"warmups": 10, "repetitions": 150, "period_ns": 20_000_000, "size": 128}
# 8M doubles is 64 MiB per array, far past any cache on the development
# machine, so the triad measures memory rather than cache bandwidth.
MEMBW = {"warmups": 3, "repetitions": 30, "elements": 8_000_000}
NETWORK = {"warmups": 3, "repetitions": 30, "bytes": 32 * 2**20, "chunk": 2**20}
GPU = {"warmups": 5, "repetitions": 50, "size": 4096, "matmuls": 10}


def _origin() -> int:
    """The monotonic origin of every t_offset_ns, shared with a recorder if one asks.

    A recorder sets TRACELAB_T0_FILE so it can place this process's samples on
    its own timeline. Without it the workload behaves exactly as before.
    """
    gate = os.environ.get("TRACELAB_GATE_FILE")
    if gate:
        # A recorder whose collectors take time to attach, such as eBPF,
        # holds the workload here until they are all listening.
        while not os.path.exists(gate):
            time.sleep(0.005)
    t0 = time.monotonic_ns()
    target = os.environ.get("TRACELAB_T0_FILE")
    if target:
        with open(target, "w") as handle:
            handle.write(str(t0))
    return t0


def _max_rss_bytes() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Darwin reports bytes and Linux reports kilobytes.
    return float(rss if sys.platform == "darwin" else rss * 1024)


def _emit(metric: str, iteration: int, warmup: bool, value: float, unit: str, t0: int) -> None:
    sample = {
        "metric": metric,
        "iteration": iteration,
        "warmup": warmup,
        "value": value,
        "unit": unit,
        "t_offset_ns": time.monotonic_ns() - t0,
    }
    print(json.dumps(sample))


def matmul() -> None:
    rng = np.random.default_rng(0)
    a = rng.random((MATMUL["size"], MATMUL["size"]))
    b = rng.random((MATMUL["size"], MATMUL["size"]))
    t0 = _origin()
    for i in range(MATMUL["warmups"] + MATMUL["repetitions"]):
        start = time.perf_counter_ns()
        for _ in range(8):
            a @ b
        elapsed = time.perf_counter_ns() - start
        warmup = i < MATMUL["warmups"]
        _emit("iteration_latency", i, warmup, float(elapsed), "ns", t0)
        _emit("max_rss", i, warmup, _max_rss_bytes(), "bytes", t0)


def periodic() -> None:
    """A fixed-rate loop with real work in each tick, measured by its intervals.

    Deadlines are absolute, so one late tick does not shift every later one,
    and the interval recorded is between consecutive tick starts, which is
    what a downstream consumer of the loop would observe.
    """
    rng = np.random.default_rng(0)
    a = rng.random((PERIODIC["size"], PERIODIC["size"]))
    period = PERIODIC["period_ns"]
    t0 = _origin()
    previous = time.perf_counter_ns()
    deadline = previous
    for i in range(PERIODIC["warmups"] + PERIODIC["repetitions"]):
        deadline += period
        a @ a
        remaining = deadline - time.perf_counter_ns()
        if remaining > 0:
            time.sleep(remaining / 1e9)
        now = time.perf_counter_ns()
        _emit("tick_interval", i, i < PERIODIC["warmups"], float(now - previous), "ns", t0)
        previous = now


def membw() -> None:
    """STREAM's triad, a = b + s * c, reported as bytes moved per second.

    numpy needs a temporary for s * c, so each iteration reads b, c and the
    temporary and writes the temporary and a: five arrays of traffic. The
    count is part of the metric's definition, not an estimate of hardware
    traffic, so only changes in it are meaningful, not its absolute value.
    """
    n = MEMBW["elements"]
    a, b, c, tmp = (np.ones(n) for _ in range(4))
    moved = 5 * 8 * n
    t0 = _origin()
    for i in range(MEMBW["warmups"] + MEMBW["repetitions"]):
        cpu = time.process_time_ns()
        start = time.perf_counter_ns()
        np.multiply(c, 3.0, out=tmp)
        np.add(b, tmp, out=a)
        elapsed = time.perf_counter_ns() - start
        warmup = i < MEMBW["warmups"]
        _emit("iteration_latency", i, warmup, float(elapsed), "ns", t0)
        _emit("memory_bandwidth", i, warmup, moved / (elapsed / 1e9), "ops_per_s", t0)
        _emit("cpu_time", i, warmup, float(time.process_time_ns() - cpu), "ns", t0)


def network() -> None:
    """Loopback TCP: push a fixed payload to a reader thread and time it.

    Loopback never touches a NIC, so this measures the kernel's TCP path and
    the scheduler, which is exactly what contention and a kernel change move.
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    total, chunk = NETWORK["bytes"], NETWORK["chunk"]

    def drain() -> None:
        conn, _ = server.accept()
        with conn:
            while conn.recv(chunk):
                pass

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    client = socket.create_connection(server.getsockname())
    payload = b"\0" * chunk
    t0 = _origin()
    for i in range(NETWORK["warmups"] + NETWORK["repetitions"]):
        cpu = time.process_time_ns()
        start = time.perf_counter_ns()
        for _ in range(total // chunk):
            client.sendall(payload)
        elapsed = time.perf_counter_ns() - start
        warmup = i < NETWORK["warmups"]
        _emit("iteration_latency", i, warmup, float(elapsed), "ns", t0)
        _emit("network_throughput", i, warmup, total / (elapsed / 1e9), "ops_per_s", t0)
        _emit("cpu_time", i, warmup, float(time.process_time_ns() - cpu), "ns", t0)
    client.close()


def gpu() -> None:
    """fp16 matrix multiplies on a CUDA device, timed on the device itself.

    CUDA events bracket the work on the GPU's own timeline, so the latency
    is what the device spent, not what the host waited. The rate is FLOP per
    second from 2 * n^3 per multiply, the conventional count, so changes in it
    are meaningful and its absolute value is comparable to a datasheet.
    """
    import pynvml
    import torch

    # The device reports its own state each iteration, so a run carries the
    # clock and temperature it was measured at, and a verdict can be checked
    # against them: v5's only false regressions came while the GPU warmed.
    pynvml.nvmlInit()
    handle = pynvml.nvmlDeviceGetHandleByIndex(torch.cuda.current_device())
    n, count = GPU["size"], GPU["matmuls"]
    a = torch.randn(n, n, device="cuda", dtype=torch.float16)
    b = torch.randn(n, n, device="cuda", dtype=torch.float16)
    start_event = torch.cuda.Event(enable_timing=True)
    end_event = torch.cuda.Event(enable_timing=True)
    flops = 2 * n**3 * count
    t0 = _origin()
    for i in range(GPU["warmups"] + GPU["repetitions"]):
        start_event.record()
        for _ in range(count):
            a @ b
        end_event.record()
        end_event.synchronize()
        elapsed_ns = start_event.elapsed_time(end_event) * 1e6
        warmup = i < GPU["warmups"]
        _emit("iteration_latency", i, warmup, elapsed_ns, "ns", t0)
        _emit("matmul_throughput", i, warmup, flops / (elapsed_ns / 1e9), "ops_per_s", t0)
        _emit("gpu_memory", i, warmup, float(torch.cuda.max_memory_allocated()), "bytes", t0)
        clock = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_SM)
        temperature = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        _emit("gpu_sm_clock", i, warmup, float(clock), "unitless", t0)
        _emit("gpu_temperature", i, warmup, float(temperature), "celsius", t0)


WORKLOADS = {
    "matmul": matmul,
    "periodic": periodic,
    "membw": membw,
    "network": network,
    "gpu": gpu,
}

if __name__ == "__main__":
    WORKLOADS[sys.argv[1]]()
