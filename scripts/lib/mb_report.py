#!/usr/bin/env python3
"""The text report of a scripts/95_mb_kernel_llm.sh run, the list of runs, and the LLM calls.

    python3 scripts/lib/mb_report.py out/mb_lab/<run>           # write + print <run>/report.txt
    python3 scripts/lib/mb_report.py --list out/mb_lab          # every run, one line each
    python3 scripts/lib/mb_report.py --commands out/mb_lab/<run>  # every command the run executed
    python3 scripts/lib/mb_report.py --calls out/mb_lab/<run>   # every LLM prompt and answer

All output is plain text. `mb report`, `mb list`, `mb commands` and `mb calls` print it on the
seat or the board; the notebook (notebooks/mb_lab/mb_lab.py) reuses show_commands().
"""
import json
import os
import sys
from pathlib import Path


def write_atomic(p: Path, text: str) -> None:
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, p)


def short_diag(d) -> str:
    """First clause of a ModelBlaster diag, which is a whole build log joined with ' | '."""
    return (d or "").split(" | ")[0].strip()[:100]


def fmt(n) -> str:
    return f"{n:,}" if isinstance(n, int) else str(n)


def board_state(r):
    """Return (state, board dict). state is 'done' once board.json has been merged in,
    'pending' while the board step is outstanding, and None for a run on spike only."""
    bd = r.get("board")
    if not isinstance(bd, dict):
        return None, None
    return ("pending" if bd.get("state") == "pending" else "done"), bd


def mbp_ops(image) -> str:
    """MBP instructions compiled into the kernel, e.g. 'MAX8 x2'; the negative test is excluded."""
    c = {}
    for fn, d in ((image or {}).get("mbp_by_function") or {}).items():
        if fn != "neg_worker":
            for k, v in d.items():
                c[k] = c.get(k, 0) + v
    return ", ".join(f"{k.upper()} x{v}" for k, v in c.items() if v)


def accel_line(r) -> str:
    """One line saying whether the new kernel uses the MBP and how much of the speedup it gives."""
    bs, bd = board_state(r)
    if bs != "done":
        return ""
    ops = mbp_ops((bd.get("after") or {}).get("image"))
    if not ops:
        return "on the accelerator: NO. The new kernel uses no MBP instruction (plain RISC-V code)"
    s = f"on the accelerator: YES. The new kernel issues MBP {ops} (compiled in, run on hart 0)"
    if bd.get("speedup_accel") and bd.get("speedup_code"):
        s += f"; with the MBP switched off the same kernel is {bd['speedup_accel']:.1f}x slower"
        if bd["speedup_code"] >= 1:
            s += (f", so of the {bd['speedup']:.1f}x, {bd['speedup_accel']:.1f}x is the accelerator and "
                  f"{bd['speedup_code']:.1f}x the rewritten loop")
        else:   # without the MBP the rewritten loop is slower than the reference
            s += (f", which is slower even than the reference ({bd['speedup_code']:.1f}x): the rewrite only pays "
                  f"off because the accelerator runs it")
    return s


def text_report(run: Path, r) -> str:
    b, a, e = r["before"], r["after"], r.get("enumerated")
    L = [f"{r['op']} on {r.get('target', '?')}: {r['verdict']}   ({run.name})"
         + ("   [REPLAY: no model call]" if r.get("replay") else "")
         + (f"   [YOUR KERNEL: {r['kernel_file']}]" if r.get("kernel_file") else ""), ""]
    for nm, d, p in (("before", b, r.get("pick_before", "?")), ("after", a, r.get("pick_after", "?"))):
        L.append(f"  {nm:<7} {p:<28} {fmt(d['cycles']):>12} cycles  "
                 f"{d['cycles_per_output']:8.1f}/out   golden err {d['golden_max_abs_err']:g}")
    L.append(f"  speedup {r['speedup']:.1f}x on spike ({a.get('shape', '?')})")
    if e:
        L.append(f"  enumerated: {'BIT-EXACT' if e['bit_exact'] else 'NOT BIT-EXACT'}, "
                 f"{e['differing']:,} of {e['cases']:,} cases differ ({e.get('domain', '?')})")
    else:
        L.append("  enumerated: n/a for this op (host verify + spike golden only)")
    if r.get("backend") == "llm":
        L.append(f"  LLM: {r.get('llm_calls', 0)} calls to {r.get('model')}, "
                 f"{r.get('llm_tokens_in', 0):,} in / {r.get('llm_tokens_out', 0):,} out tokens, "
                 f"{r.get('llm_seconds', 0):.0f} s waiting on the model, {r.get('llm_wall_s', '?')} s for the arm")
        for h in (r.get("optimize") or {}).get("history", []):
            res = h.get("result", "?")
            what = (f"{fmt(h.get('parent_cycles'))} -> {fmt(h.get('cycles'))}" if res == "ok"
                    else f"{res}{(': ' + short_diag(h['diag'])) if h.get('diag') else ''}")
            L.append(f"    round {h.get('round', 1)} parent {h.get('parent_idx')} exp {h.get('exp_idx')}: {what}")
    bs, bd = board_state(r)
    if bs == "pending":
        L.append(f"  board: pending; on the board, run:  {bd.get('command')}")
    elif bs == "done":
        L.append(f"  board ({bd.get('magic')}, {bd.get('board')}): {bd.get('verdict')}, "
                 f"speedup {fmt(round(bd['speedup'], 1)) if bd.get('speedup') else '?'}x")
        for arm in ("before", "after", "mbpoff"):
            x = bd.get(arm)
            if not x:
                continue
            cpo = x.get("cycles_per_output")
            L.append(f"    {arm:<7} {fmt(x.get('op_cycles')):>14} cycles  "
                     f"{(f'{cpo:8.1f}' if cpo else '       ?')}/out  bit-exact {x.get('bit_exact')}"
                     + ("   <- the after kernel, accelerator OFF" if arm == "mbpoff" else ""))
        acc = accel_line(r)
        if acc:
            L.append(f"  {acc}")
    if r.get("cycles_note"):
        L.append(f"  {r['cycles_note']}")
    L.append(f"  kernel: {r.get('after_kernel')}")
    return "\n".join(L) + "\n"


def jload(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def list_runs(top: Path) -> None:
    latest = (top / "latest").resolve().name if (top / "latest").exists() else None
    print("  (* = the newest run; spike and board are speedups over the reference)")
    print(f"  {'run':<40} {'verdict':<9} {'spike':>8} {'board':>8} {'calls':>6}  kernel / step")
    runs = [d for d in top.iterdir() if d.is_dir() and not d.is_symlink()
            and ((d / "run.json").exists() or (d / "status.json").exists())] if top.is_dir() else []
    for d in sorted(runs, key=lambda d: d.name):
        mark = "*" if d.name == latest else " "
        r = jload(d / "run.json")
        if r:
            sp = r.get("speedup")
            bs = (r.get("board") or {}).get("speedup") if isinstance(r.get("board"), dict) else None
            print(f"{mark} {d.name:<40} {r.get('verdict', '?'):<9} "
                  f"{(f'{sp:.1f}x' if isinstance(sp, (int, float)) else '?'):>8} "
                  f"{(f'{bs:.1f}x' if isinstance(bs, (int, float)) else '-'):>8} "
                  f"{r.get('llm_calls', 0):>6}  {r.get('pick_after', '?')}")
        else:
            st = jload(d / "status.json") or {}
            print(f"{mark} {d.name:<40} {st.get('state', '?'):<9} {'':>8} {'':>8} {'':>6}  {st.get('step', '')}")


# Labels for logged commands, matched by substring, for readers new to the tools.
TOOLS = [
    ("modelblaster.pipeline.extract_graph", "ModelBlaster: the PyTorch model -> an int8 op graph (IR)"),
    ("mb_llm_tap.py", "ModelBlaster with the LLM: generate_kernels --backend llm --optimize, every call recorded"),
    ("generate_kernels", "ModelBlaster: the IR -> C kernels (reference or curated), verified on the host"),
    ("-b spike_riscv64", "Zephyr (west) builds ModelBlaster's harness for the simulator"),
    ("west build", "Zephyr (west) builds the image for the board (chipyard_pynqz1_all_f40)"),
    ("/spike ", "spike (TACIT, with the MBP instructions) runs the image and counts cycles"),
    ("xilinx@localhost", "the board's agent, through its reverse tunnel: upload / run / fetch"),
]


def show_commands(run: Path) -> None:
    """Print the commands a run executed, grouped by tool, with paths shortened to $RUN and ~."""
    p = run / "commands.sh"
    if not p.exists():
        print(f"no commands.sh in {run.name} (a run from before commands were logged: see commands.txt)")
        return
    home = str(Path.home())
    print(f"# the exact commands of {run.name}, in order.  Before pasting any of them:")
    print(f"export RUN={str(run.resolve()).replace(home, '~')}\n")
    last = None
    for line in p.read_text().splitlines():
        what = next((w for k, w in TOOLS if k in line), "")
        short = line.replace(str(run.resolve()), "$RUN").replace(str(run), "$RUN").replace(home, "~")
        if what != last:
            print(f"\n# --- {what}" if what else "")
            last = what
        print(short)


def show_calls(run: Path) -> None:
    """Print each LLM call of a run in order: the user prompt and the response."""
    p = run / "after" / "transcript.jsonl"
    if not p.exists():
        print(f"no LLM calls recorded for {run.name} (a replay or your own kernel makes none)")
        return
    for line in p.read_text().splitlines():
        try:
            c = json.loads(line)
        except ValueError:
            continue
        print("=" * 78)
        print(f"call {c.get('n')}  round {c.get('round')}  {c.get('phase')}  {c.get('model')}  "
              f"{c.get('latency_s', 0):.0f} s  {c.get('input_tokens', 0):,} in / {c.get('output_tokens', 0):,} out")
        print("-" * 30 + " asked " + "-" * 41)
        print((c.get("user") or "").strip())
        print("-" * 30 + " answer " + "-" * 40)
        print((c.get("response") or c.get("error") or "").strip())
    print("=" * 78)
    print("(the system prompt of each call is in after/transcript.jsonl, field 'system')")


def main() -> int:
    a = sys.argv[1:]
    if len(a) == 2 and a[0] == "--list":
        list_runs(Path(a[1]))
        return 0
    if len(a) == 2 and a[0] == "--commands":
        show_commands(Path(a[1]))
        return 0
    if len(a) == 2 and a[0] == "--calls":
        show_calls(Path(a[1]))
        return 0
    if len(a) == 1 and not a[0].startswith("-"):
        run = Path(a[0])
        r = jload(run / "run.json")
        if r is None:
            print(f"no run.json in {run}", file=sys.stderr)
            return 1
        t = text_report(run, r)
        write_atomic(run / "report.txt", t)
        sys.stdout.write(t)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
