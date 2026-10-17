"""Compare the naive and PoNR-aware warning policies.

Usage: python -m risk_monitor.demo [--out timeline.png]
"""

from __future__ import annotations

import argparse

from .scenario import Trace, run

CASES = [
    ("empty", False, 6.0),
    ("loaded", True, 10.0),
]


def summarize(traces):
    print("%-7s %-6s %6s %6s %8s %7s %8s" % ("truck", "policy", "t_warn", "t_ponr", "lead_s", "before", "outcome"))
    for tr in traces:
        lead = tr.lead_time
        print(
            "%-7s %-6s %6s %6.2f %8s %7s %8s"
            % (
                "loaded" if tr.loaded else "empty",
                tr.policy,
                "-" if tr.t_warn is None else "%.2f" % tr.t_warn,
                tr.t_ponr,
                "-" if lead is None else "%+.2f" % lead,
                "yes" if tr.warned_before_ponr else "no",
                "CRASH" if tr.collided else "stopped",
            )
        )
    before = sum(tr.warned_before_ponr for tr in traces if tr.policy == "ponr")
    naive_before = sum(tr.warned_before_ponr for tr in traces if tr.policy == "naive")
    n = len(CASES)
    print("\nwarnings before PoNR: naive %d/%d, ponr %d/%d" % (naive_before, n, before, n))


def plot(traces, out: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(len(CASES), 1, figsize=(9, 3.2 * len(CASES)), sharex=True)
    for ax, (name, loaded, _) in zip(axes, CASES):
        case = [tr for tr in traces if tr.loaded == loaded]
        for tr, color in zip(case, ("tab:red", "tab:green")):
            ax.plot(tr.t, tr.distance, color=color, label="%s policy" % tr.policy)
            if tr.t_warn is not None:
                ax.axvline(tr.t_warn, color=color, linestyle=":", linewidth=1.5)
                ax.plot(tr.t_warn, tr.distance[tr.t.index(tr.t_warn)], "v", color=color, markersize=9)
        ax.axvline(case[0].t_ponr, color="black", linestyle="--", label="PoNR")
        ax.axhline(0, color="grey", linewidth=0.8)
        ax.set_ylabel("obstacle distance (m)")
        ax.set_title("%s haul truck" % name)
        ax.legend(loc="upper right")
    axes[-1].set_xlabel("time (s)")
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    print("plot: " + out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="timeline.png")
    args = parser.parse_args()
    traces = [run(policy, loaded, v0=v0) for _, loaded, v0 in CASES for policy in ("naive", "ponr")]
    summarize(traces)
    plot(traces, args.out)


if __name__ == "__main__":
    main()
