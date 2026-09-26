#!/usr/bin/env python3
"""Live terminal view of one lab run: each kernel the LLM tried, with its cycles on spike and,
with --board-loop, on the FPGA, drawn as bars on a log scale.

    python3 scripts/lib/mb_progress.py out/mb_lab/<run> [--width 72]

Prints one frame and exits; `mb` on the board calls it every few seconds. It reads only the
files the lab writes, and tolerates them being written concurrently:
    before/spike.json                 the reference's cycles (spike)
    after/transcript.jsonl            every LLM call, in order (the tap appends per call)
    after/round<R>.trajectory.jsonl   each finished round's candidates (ModelBlaster)
    after/gen/beam_search_trajectory.jsonl   the round in progress
    board_rounds.jsonl                the FPGA's cycles for each round's best (--board-loop)
    status.json                       the step the lab is on
"""
import argparse
import json
import math
import re
import time
from pathlib import Path


def jl(p):
    out = []
    try:
        for line in open(p):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass                    # partial line, still being written
    except OSError:
        pass
    return out


def js(p):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return None


IDEA = re.compile(r"(?://|/\*)\s*idea\s*:\s*(.+?)(?:\*/)?\s*$", re.I | re.M)


def idea_of(resp):
    """Return the candidate's `// idea: ...` line (the lab asks for one), else its first
    nontrivial comment."""
    if not resp:
        return ""
    m = IDEA.search(resp)
    if m:
        return m.group(1).strip()
    for c in re.findall(r"//\s*(.+)$|/\*\s*(.+?)\s*\*/", resp, re.M):
        t = (c[0] or c[1]).strip()
        if len(t) > 12 and not t.lower().startswith(("include", "copyright")):
            return t
    return ""


def collect(run: Path) -> dict:
    """Collect the chart data for a run; frame() and the notebook's SVG (notebooks/mb_lab/mb_lab.py)
    both draw from it. rows: (label, spike cycles, FPGA cycles, idea, ok);
    mbpoff: {row label: FPGA cycles with the MBP switched off}."""
    st = js(run / "status.json") or {}
    before = js(run / "before" / "spike.json") or {}
    n = before.get("n_out") or 0
    unit = "per output" if n else "cycles"
    calls = jl(run / "after" / "transcript.jsonl")
    synth = [c for c in calls if (c.get("phase") or "").startswith("synth")]
    opt_by_round = {}
    for c in calls:
        if (c.get("phase") or "").startswith("optimize"):
            opt_by_round.setdefault(c.get("round", 1), []).append(c)

    rows = []                                   # (label, spike cycles, fpga cycles, idea, ok)
    mbpoff = {}
    if before.get("cycles"):
        rows.append(("reference", before["cycles"], None, "ModelBlaster's reference kernel", True))
    board = {b.get("round"): b for b in jl(run / "board_rounds.jsonl")}
    if board.get(1, {}).get("before_cycles") and rows:
        rows[0] = rows[0][:2] + (board[1]["before_cycles"],) + rows[0][3:]
    rounds = sorted({int(m.group(1)) for p in (run / "after").glob("round*.trajectory.jsonl")
                     if (m := re.match(r"round(\d+)\.", p.name))})
    trajs = [(r, jl(run / "after" / f"round{r}.trajectory.jsonl")) for r in rounds]
    live = jl(run / "after" / "gen" / "beam_search_trajectory.jsonl")
    if live and st.get("state") == "running":
        trajs.append(((rounds[-1] + 1) if rounds else 1, live))
    if trajs and trajs[0][1]:
        base = trajs[0][1][0].get("baseline_cycles")
        if base:
            rows.append(("LLM first draft", base, None, idea_of(synth[0]["response"]) if synth else "", True))
    elif synth:
        rows.append(("LLM first draft", None, None, idea_of(synth[-1].get("response")) + "  (verifying…)", True))
    for r, tr in trajs:
        ops = opt_by_round.get(r, [])
        for i, h in enumerate(tr):
            ok = h.get("result") == "ok"
            idea = idea_of(ops[i]["response"]) if i < len(ops) else ""
            rows.append((f"round {r} · try {h.get('exp_idx', i + 1)}", h.get("cycles") if ok else None,
                         None, idea if ok else f"{h.get('result')}: {idea}", ok))
        b = board.get(r)
        if b and b.get("after_cycles"):
            # attach the FPGA cycles to this round's best spike row
            best = min((k for k, x in enumerate(rows) if x[0].startswith(f"round {r} ") and x[1]),
                       key=lambda k: rows[k][1], default=None)
            if best is not None:
                lab, sc, _, idea, ok = rows[best]
                rows[best] = (lab, sc, b["after_cycles"], idea, ok)
                if b.get("mbpoff_cycles"):
                    mbpoff[lab] = b["mbpoff_cycles"]
    if not trajs and not synth:
        # Replay, attendee kernel or curated kernel: a single "after" row from spike.json and,
        # once the board has run, board.json (which also holds the reference's FPGA cycles).
        r = js(run / "run.json") or {}
        a = js(run / "after" / "spike.json") or {}
        bj = js(run / "board.json") or {}
        lab = ("your kernel" if r.get("kernel_file") else "replayed LLM kernel" if r.get("replay")
               else "after")
        if a.get("cycles") or bj.get("after"):
            rows.append((lab, a.get("cycles"), (bj.get("after") or {}).get("op_cycles"),
                         Path(r["kernel_file"]).name if r.get("kernel_file") else "", True))
            if (bj.get("mbpoff") or {}).get("op_cycles"):
                mbpoff[lab] = bj["mbpoff"]["op_cycles"]
        if rows and rows[0][0] == "reference" and not rows[0][2] and (bj.get("before") or {}).get("op_cycles"):
            rows[0] = rows[0][:2] + (bj["before"]["op_cycles"],) + rows[0][3:]
    return {"status": st, "n": n, "unit": unit, "rows": rows, "mbpoff": mbpoff,
            "calls": len(calls), "model": (calls[-1].get("model") if calls else None)}


def frame(run: Path, width: int) -> str:
    d = collect(run)
    st, n, unit, rows = d["status"], d["n"], d["unit"], d["rows"]
    per = (lambda c: c / n) if n else (lambda c: c)
    vals = [per(v) for x in rows for v in (x[1], x[2]) if v]
    hi = max(vals) if vals else 1.0
    lo = min(vals) if vals else 1.0
    lhi, llo = math.log10(hi * 1.05), math.log10(max(lo / 1.5, 1e-9))

    labw, numw = 16, 8
    barw = max(10, width - labw - 2 * numw - 6)

    def bar(v):
        if not v:
            return " " * barw
        f = (math.log10(per(v)) - llo) / (lhi - llo) if lhi > llo else 1.0
        k = max(1, min(barw, round(f * barw)))
        return "█" * k + " " * (barw - k)

    def num(v):
        return f"{per(v):>{numw},.1f}" if v else " " * numw

    best_fpga = min((x[2] for x in rows if x[2]), default=None)
    best_spike = min((x[1] for x in rows[1:] if x[1]), default=None)
    el = int(time.time() - st.get("t0", time.time())) if st.get("t0") else None
    L = [f"  {run.name}   {st.get('step', '')}" + (f"   {el // 60}m{el % 60:02d}s" if el is not None else ""),
         f"  {'cycles ' + unit + ' (log scale)':<{labw + barw + 1}} {'spike':>{numw}} {'FPGA':>{numw}}"]
    for lab, sc, fc, idea, ok in rows:
        mark = " ◀ best on the FPGA" if fc and fc == best_fpga and lab != "reference" else ""
        L.append(f"  {lab[:labw]:<{labw}} {bar(sc)} {num(sc)} {num(fc)}{mark}")
        if idea:
            L.append(f"  {'':<{labw}} \033[2m{idea[:width - labw - 4]}\033[0m")
    if best_spike and rows and rows[0][1]:
        s = f"  so far: {rows[0][1] / best_spike:.1f}x on spike"
        if best_fpga and rows[0][2]:
            s += f",  {rows[0][2] / best_fpga:.1f}x on the FPGA"
        L.append(s)
    if st.get("state") == "running" and "LLM call" in (st.get("step") or "") and "waiting for" in (st.get("step") or ""):
        L.append("  \033[2m… the LLM is thinking\033[0m")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--width", type=int, default=72)
    a = ap.parse_args()
    print(frame(Path(a.run), a.width), end="")


if __name__ == "__main__":
    main()
