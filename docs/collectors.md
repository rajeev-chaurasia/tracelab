# Collectors and the timeline

A verdict says a metric moved. A trace says why. TraceLab records a benchmark
with several collectors at once, each on its own clock, and puts every sample
on one timeline that opens in the Perfetto UI.

```
uv run tracelab record --out trace.json -- python -m evaluation.workload periodic
script/ebpf_record.sh periodic evidence/traces/periodic-ebpf.json        # adds eBPF, in Linux
```

## What is collected, and on which clock

| collector | what | clock | status |
| --- | --- | --- | --- |
| workload | every iteration as a slice, every metric as a point | monotonic, in the workload process | run on every recording |
| process | CPU user and system time, RSS, voluntary and involuntary context switches, every 10 ms | monotonic, in the recorder | run on every recording |
| system | machine-wide and busiest-core CPU utilisation, every 250 ms | wall clock, in a separate process | run on every recording |
| eBPF | run-queue waits and preemptions of the workload's main thread | CLOCK_MONOTONIC in the kernel | run in a Linux container, see below |
| nvidia-smi | GPU and memory utilisation, memory used, power, temperature, SM clock | wall clock | run on an NVIDIA L4 in GCP |
| Nsight Systems | CUDA kernels and memory copies from an `nsys export` SQLite file | session-relative, placed by its UTC start | run on an NVIDIA L4 in GCP, nsys 2025.1 |

The system sampler runs out of process and stamps wall time on purpose, the way
an external tool does, so every recording exercises the alignment rather than
only a test.

## How alignment is measured

Each clock-to-clock reading is a sandwich: reference clock, other clock,
reference clock. The other clock was read somewhere inside it, so half its
width bounds that reading's error. A sync thread takes the tightest of 64
sandwiches every 100 ms through the run, a line is fitted through them so
drift is modelled, and the reported bound is the worst sandwich half-width
plus the worst distance of any reading from the line.

Measured across the 30 traced runs sealed in `corpus/v4`, each with its own
`alignment.json`: a wall-to-monotonic bound with a median of 0.36
microseconds and a maximum of 0.63, from sandwiches about 42 ns wide and fit
residuals of a few hundred nanoseconds. The fit also finds the wall clock
running about 9 parts per million slow against the monotonic clock, which is
NTP slewing, and why the mapping is a line and not an offset.

Inside Linux, the eBPF and workload samples need no mapping at all:
`bpf_ktime_get_ns` and Python's `time.monotonic_ns` both read CLOCK_MONOTONIC.

## GPU: kernels on the same timeline

On an L4 in a GCP VM, `tracelab record --gpu --nsys` put five sources on one
trace: workload iterations timed with CUDA events, process and system
samples, nvidia-smi telemetry, and 552 CUDA kernels from Nsight Systems
(`evidence/traces/gpu-nsys.trace.json`). Those sources share no clock: the
iterations are on the monotonic clock, the kernels on nsys's session clock
placed through the wall clock.

The check in `evaluation/gpu_alignment.py` asks whether the work lines up
anyway. It does. All 50 measured iterations contain exactly the 10 GEMM kernels
they launched, the kernels account for 99.5 to 99.7% of each iteration's
CUDA-event time, and the slack at either end is about 50 and 37 microseconds,
which is the host stamping the iteration after its synchronize returns, not
the clock mapping.

The run also corrected the importer. The fixtures had a memcpy table; a real
profile with no copies has none, and the importer now handles that. And it
confirmed the time origin: a host wall-clock stamp taken just after a
synchronize lands 12 to 16 microseconds after session start plus the last
kernel's end.

In v5 the same telemetry explained a verdict: the only false regressions came
while the L4 was heating from 37 to 52 degrees and its SM clock was falling
from 1,005 to 960 MHz.

## GPU occupancy

Nsight Compute's Occupancy and Launch Statistics sections, run on the L4 and
summarised by `evaluation/occupancy.py` into `evidence/traces/gpu-occupancy.json`:

| kernel | registers per thread | theoretical | achieved | limited by |
| --- | ---: | ---: | ---: | --- |
| cuBLAS `ampere_fp16_s1688gemm_fp16_128x128` | 234 | 16.67% | 16.26% | registers, two blocks per SM |
| PyTorch normal-distribution RNG | 40 | 100% | 67.95% | nothing, full theoretical occupancy |

The GEMM's low occupancy is a choice, not a defect: a large tiled matrix
multiply spends registers on register-level blocking rather than on more warps,
and still ran at about 50 TFLOP/s. Occupancy is measured here by profiling a
few launches, which replays each kernel; it is not collected on every run,
because the replay changes the timing it would be explaining.

## eBPF: what the scheduler explains

The eBPF collector loads a bpftrace program on `sched_wakeup` and
`sched_switch` and reports every interval the workload's main thread spent
runnable but not running. Docker Desktop's Linux 6.10 kernel supports it; the
container runs privileged in the host PID namespace so PIDs mean the same
thing to the tracepoints and the recorder.

Recorded with the 20 ms periodic loop, quiet and then with twelve busy loops
on the VM's ten CPUs (`evidence/traces/attribution.json`):

| | quiet | contended |
| --- | ---: | ---: |
| late ticks, over 25 ms | 0 of 150 | 90 of 150 |
| run-queue waits | 351 | 6,401 |
| run-queue wait p99 | 0.8 ms | 11.9 ms |
| longest wait | 1.7 ms | 36 ms |
| worst wait inside a tick, median | 18 us | 10.5 ms late, 4.9 ms on time |
| correlation of tick length with worst wait | 0.25 | 0.56 |

Under contention the late ticks are the ones that waited for a CPU, and the
waits alone are long enough to account for the lateness. On a quiet machine
the scheduler explains almost nothing, which says the occasional late tick
there comes from somewhere it cannot see, such as timer slack.

## Two collector bugs the recordings found

Both were caught because a published number looked wrong, and both are fixed
and covered by tests.

- **Only wakeups started a wait.** A preempted thread goes back on a run queue
  with no wakeup event, so the first contended recording saw one wait in 150
  ticks. The program now starts timing when the thread is switched out while
  still runnable.
- **bpftrace's output was not drained during the run.** The pipe filled within
  about a second and a half under contention, and the trace silently stopped.
  The collector now reads on a thread for the whole recording.

A third problem was in the harness: the first "contended" recording used busy
loops that never started, because the interpreter they named did not exist yet
in a fresh container. Machine-wide CPU in the trace exposed it, and the loops
are now plain shell.
