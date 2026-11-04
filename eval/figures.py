"""Paper figures (static SVG, light theme for print) built from the benchmark results.

    python -m eval.figures            # -> eval/results/figures/*.svg

fig_patients_reevaluated.svg  patients re-evaluated per knowledge change (10,000 patients)
fig_time_per_change.svg       wall-clock time per knowledge change (10,000 patients)
fig_vus_resolution.svg        share of 2023 VUS reclassified by 2026, per gene

Colors: validated categorical slots 1-2 (blue = GE-EHR / to P/LP, orange = full recompute /
to B/LB); text uses ink tokens, never the series color; bars <= 24px with a 4px rounded
data end, square at the baseline; values at the bar tip; legend for the two series.
Alert-once re-evaluates nothing (0 patients, ~0 s of work), so it is stated in the
subtitle rather than drawn as an empty bar.
"""

import csv
import math
import statistics
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "eval" / "results"

INK = {
    "primary": "#0b0b0b",
    "secondary": "#52514e",
    "muted": "#898781",
    "grid": "#e1e0d9",
    "baseline": "#c3c2b7",
    "surface": "#fcfcfb",
}
SERIES = ("#2a78d6", "#eb6834")  # categorical slots 1, 2 (validated light-mode pair)
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'
SCENARIO_LABELS = {
    "real": "ClinVar 2023 → 2026",
    "stepwise": "2023 → 2023-12 → 2026",
    "rule_revision": "Rule D4 v1 → v2",
}


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def nice_ticks(vmax, target=4):
    """Round tick values 0..top with a 1/2/2.5/5 x 10^k step, about `target` intervals."""
    if vmax <= 0:
        return [0, 1]
    raw = vmax / target
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    n = math.ceil(vmax / step - 1e-9)
    return [round(step * i, 10) for i in range(n + 1)]


def wrap(text, limit):
    """Greedy word wrap into lines of at most `limit` characters."""
    lines, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > limit:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    return lines + ([cur] if cur else [])


def bar_path(x0, y, length, h, r=4):
    """Horizontal bar, square at the baseline x0, 4px rounded data end."""
    if length <= 0:
        return ""
    r = min(r, length, h / 2)
    x1 = x0 + length
    return (
        f"M{x0:.1f},{y:.1f} H{x1 - r:.1f} Q{x1:.1f},{y:.1f} {x1:.1f},{y + r:.1f} "
        f"V{y + h - r:.1f} Q{x1:.1f},{y + h:.1f} {x1 - r:.1f},{y + h:.1f} H{x0:.1f} Z"
    )


def hbar_chart(
    title, subtitle, groups, series_names, values, labels, axis_label, tick_fmt, width=720
):
    """Grouped horizontal bars. values[g][s] numbers, labels[g][s] text at the tip."""
    sub_lines = wrap(subtitle, int((width - 32) / 6.4))  # ~6.4px per char at 12.5px
    legend_y = 58 + 17 * (len(sub_lines) - 1)
    left, right, top = 190, 120, legend_y + 20
    bar_h, bar_gap, group_gap = 18, 2, 18
    group_h = len(series_names) * bar_h + (len(series_names) - 1) * bar_gap
    plot_h = len(groups) * group_h + (len(groups) - 1) * group_gap
    height = top + plot_h + 56
    plot_w = width - left - right
    ticks = nice_ticks(max(v for row in values for v in row))
    vmax = ticks[-1]

    def sx(v):
        return left + plot_w * v / vmax

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="t d" font-family=\'{FONT}\'>',
        f'<title id="t">{escape(title)}</title><desc id="d">{escape(subtitle)}</desc>',
        f'<rect width="{width}" height="{height}" fill="{INK["surface"]}"/>',
        f'<text x="16" y="28" font-size="17" font-weight="600" fill="{INK["primary"]}">{escape(title)}</text>',
    ]
    for i, line in enumerate(sub_lines):
        out.append(
            f'<text x="16" y="{48 + 17 * i}" font-size="12.5" fill="{INK["secondary"]}">{escape(line)}</text>'
        )
    # legend (always present for two series)
    lx = left
    for name, color in zip(series_names, SERIES, strict=False):
        out.append(f'<rect x="{lx}" y="{legend_y}" width="10" height="10" rx="2" fill="{color}"/>')
        out.append(
            f'<text x="{lx + 15}" y="{legend_y + 9}" font-size="12" fill="{INK["secondary"]}">{escape(name)}</text>'
        )
        lx += 22 + 7 * len(name)
    # gridlines + ticks
    for t in ticks:
        x = sx(t)
        out.append(
            f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top - 6}" y2="{top + plot_h + 4}" '
            f'stroke="{INK["grid"] if t else INK["baseline"]}" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{x:.1f}" y="{top + plot_h + 20}" font-size="11.5" text-anchor="middle" '
            f'fill="{INK["muted"]}" font-variant-numeric="tabular-nums">{escape(tick_fmt(t))}</text>'
        )
    out.append(
        f'<text x="{left + plot_w / 2:.1f}" y="{top + plot_h + 42}" font-size="12" text-anchor="middle" '
        f'fill="{INK["secondary"]}">{escape(axis_label)}</text>'
    )
    # bars
    y = top
    for g, group in enumerate(groups):
        out.append(
            f'<text x="{left - 12}" y="{y + group_h / 2 + 4:.1f}" font-size="12.5" text-anchor="end" '
            f'fill="{INK["primary"]}">{escape(group)}</text>'
        )
        for s, color in enumerate(SERIES[: len(series_names)]):
            v = values[g][s]
            by = y + s * (bar_h + bar_gap)
            if v > 0:
                out.append(
                    f'<path d="{bar_path(left, by, sx(v) - left, bar_h)}" fill="{color}">'
                    f"<title>{escape(series_names[s])}: {escape(labels[g][s])}</title></path>"
                )
            out.append(
                f'<text x="{sx(v) + 6:.1f}" y="{by + bar_h / 2 + 4:.1f}" font-size="11.5" '
                f'fill="{INK["secondary"]}" font-variant-numeric="tabular-nums">{escape(labels[g][s])}</text>'
            )
        y += group_h + group_gap
    out.append("</svg>")
    return "\n".join(out) + "\n"


def scale_results(scale_dir):
    """{scenario: {strategy: (patients re-evaluated, median seconds, n_patients)}}"""
    out = {}
    for scen in SCENARIO_LABELS:
        d = scale_dir / scen
        if not (d / "runs.csv").exists():
            continue
        runs, metrics = read_csv(d / "runs.csv"), read_csv(d / "metrics.csv")
        per = {}
        for strategy in ("alert_once", "full", "change_driven"):
            reps = {}
            for r in runs:
                if r["strategy"] == strategy:
                    reps[r["repeat"]] = reps.get(r["repeat"], 0.0) + float(r["ms"])
            patients = sum(
                int(m["value"])
                for m in metrics
                if m["strategy"] == strategy and m["metric"] == "patients_reevaluated"
            )
            per[strategy] = (patients, statistics.median(reps.values()) / 1000 if reps else 0.0)
        out[scen] = per
    return out


def main(results=RESULTS):
    results = Path(results)
    out_dir = results / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    scale = scale_results(results / "scale_10000")
    if scale:
        groups = [SCENARIO_LABELS[s] for s in scale]
        series = ("GE-EHR (change-driven)", "Full recompute")
        pats = [[scale[s]["change_driven"][0], scale[s]["full"][0]] for s in scale]
        secs = [[scale[s]["change_driven"][1], scale[s]["full"][1]] for s in scale]
        sub = (
            "10,000-patient cohort. Both strategies end in identical alerts; "
            "alert-once re-evaluates no one and leaves stale alerts."
        )
        (out_dir / "fig_patients_reevaluated.svg").write_text(
            hbar_chart(
                "Patients re-evaluated per knowledge change",
                sub,
                groups,
                series,
                pats,
                [[f"{v:,}" for v in row] for row in pats],
                "Patients re-evaluated",
                lambda t: f"{t / 1000:g}K" if t >= 1000 else f"{t:g}",
            )
        )
        (out_dir / "fig_time_per_change.svg").write_text(
            hbar_chart(
                "Time per knowledge change",
                sub.replace("Both", "Median of repeats; both"),
                groups,
                series,
                secs,
                [[f"{v:.1f} s" for v in row] for row in secs],
                "Seconds (wall clock)",
                lambda t: f"{t:g}",
            )
        )
        written += ["fig_patients_reevaluated.svg", "fig_time_per_change.svg"]

    rec = results / "reclassification.csv"
    if rec.exists():
        rows = [
            r
            for r in read_csv(rec)
            if r["pair"].startswith("2023-01-07") and r["pair"].endswith("2026-10-19")
        ]
        rows = [r for r in rows if int(r["vus_before"]) > 0]
        groups = [r["gene"] for r in rows]
        vals = [
            [
                100 * int(r["vus_to_pathogenic"]) / int(r["vus_before"]),
                100 * int(r["vus_to_benign"]) / int(r["vus_before"]),
            ]
            for r in rows
        ]
        labels = [
            [
                f"{r['vus_to_pathogenic']} of {r['vus_before']} ({v[0]:.1f}%)",
                f"{r['vus_to_benign']} ({v[1]:.1f}%)",
            ]
            for r, v in zip(rows, vals, strict=True)
        ]
        (out_dir / "fig_vus_resolution.svg").write_text(
            hbar_chart(
                "Where 2023 VUS went by 2026",
                "Share of each gene's ClinVar 2023-01-07 VUS that are P/LP or B/LB in 2026-10-19.",
                groups,
                ("Became P/LP (upgrade)", "Became B/LB"),
                vals,
                labels,
                "Share of the gene's 2023 VUS",
                lambda t: f"{t:g}%",
            )
        )
        written.append("fig_vus_resolution.svg")

    for w in written:
        print(f"Wrote {out_dir / w}")
    return written


if __name__ == "__main__":
    main()
