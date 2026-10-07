"""Scheduler run-queue latency for one process, measured with eBPF.

A periodic loop misses a deadline when its thread was ready to run and the
scheduler did not run it. Latency samples show that the tick was late; this
shows why. bpftrace attaches to the sched_wakeup and sched_switch tracepoints
and reports every interval the target thread spent runnable but not running,
whether it became runnable by being woken or by being preempted, and every
time it left a CPU.

Linux only, and it needs privileges to load programs. Timestamps come from
bpf_ktime_get_ns, which is CLOCK_MONOTONIC, the same clock Python's
time.monotonic_ns reads on Linux, so these samples share the workload's
domain with no mapping to fit. Only the target's main thread is followed;
helper threads, such as a BLAS pool, are not.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from .base import Sample

SCRIPT = r"""
tracepoint:sched:sched_wakeup,
tracepoint:sched:sched_wakeup_new
/args->pid == $1/
{
  @woke[args->pid] = nsecs;
}

tracepoint:sched:sched_switch
/@woke[args->next_pid]/
{
  printf("runq %llu %llu\n", nsecs, nsecs - @woke[args->next_pid]);
  delete(@woke[args->next_pid]);
}

tracepoint:sched:sched_switch
/args->prev_pid == $1/
{
  printf("offcpu %llu %d\n", nsecs, args->prev_state);
  // Preempted while still runnable: it goes straight back on a run queue
  // with no wakeup event, so its wait starts here. Without this, the waits
  // that matter most under contention are the ones never measured.
  if (args->prev_state == 0) {
    @woke[args->prev_pid] = nsecs;
  }
}

END
{
  clear(@woke);
}
"""


def parse(output: str) -> list[Sample]:
    """bpftrace's lines into samples; anything else it prints is ignored."""
    out: list[Sample] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        kind, stamp, value = parts
        if kind == "runq":
            # The thread became runnable `value` ns before `stamp`, so the
            # wait is drawn as a slice ending when it got the CPU.
            wait = int(value)
            out.append(
                Sample(
                    "ebpf", "runq_latency", int(stamp) - wait, float(wait), "ns", "monotonic", wait
                )
            )
        elif kind == "offcpu":
            # prev_state 0 means it was preempted while runnable; anything
            # else means it blocked or slept of its own accord.
            preempted = 1.0 if int(value) == 0 else 0.0
            out.append(Sample("ebpf", "preempted", int(stamp), preempted, "count", "monotonic"))
    return out


class RunQueueCollector:
    name = "ebpf"

    def __init__(self, pid: int, bpftrace: str = "bpftrace") -> None:
        self.pid = pid
        self.bpftrace = bpftrace
        self._proc: subprocess.Popen[str] | None = None
        self._lines: list[str] = []
        self._reader: threading.Thread | None = None

    def start(self) -> None:
        self._proc = subprocess.Popen(
            [self.bpftrace, "-e", SCRIPT, str(self.pid)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert self._proc.stdout is not None
        # bpftrace prints "Attaching N probes..." once its programs are
        # loaded; anything sampled before that would be silently missing.
        first = self._proc.stdout.readline()
        if not first.startswith("Attaching"):
            self._proc.kill()
            _, err = self._proc.communicate()
            raise RuntimeError(f"bpftrace did not attach: {first.strip()} {err.strip()}")
        # Drained for the whole run. Left unread, the pipe fills within a
        # second or two under contention, bpftrace blocks or drops events,
        # and the trace silently stops early, which is what the first
        # contended recording did.
        stream = self._proc.stdout
        self._reader = threading.Thread(target=lambda: self._lines.extend(stream), daemon=True)
        self._reader.start()

    def stop(self) -> None:
        if self._proc is None:
            return
        self._proc.send_signal(2)
        self._proc.wait(timeout=30)
        if self._reader is not None:
            self._reader.join(timeout=30)

    def samples(self) -> list[Sample]:
        return parse("".join(self._lines))

    def artifacts(self) -> list[Path]:
        return []
