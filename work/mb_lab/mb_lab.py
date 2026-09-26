"""The helpers notebooks/mb_lab/mb_lab.ipynb and mb_lab_solved.ipynb import (`import mb_lab as lab`).

Every function runs the same `mb` command a terminal would, on this seat, and draws what it
did with inline HTML and SVG -- no plotting library, nothing to install: the seat's Jupyter has
IPython and Pygments and that is all this uses.  go() and try_kernel() redraw a live chart in
place while the run is going (the data is scripts/lib/mb_progress.py's, the same the terminal
chart draws).
"""
import html
import json
import math
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

from IPython.display import HTML, display

REPO = Path(__file__).resolve().parents[2]            # this module lives in <repo>/notebooks/mb_lab
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mb_progress  # noqa: E402

HOME = Path.home()
RUNS = REPO / "out" / "mb_lab"                          # where the lab (mb) writes its runs
SOLVED = Path(__file__).resolve().parent / "assets" / "solved_runs.tar.gz"   # complete runs, for the solved notebook
KDIR = HOME / "work" / "modelblaster-llm-lab" / "your-kernel"     # where lab.start() puts your kernel
LOGS = HOME / "mb_logs"
ENV = ". ~/.config/iiswc/dev.env && cd ~/iiswc-tutorial && . ./env.sh >/dev/null 2>&1"
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
AGENT = ["ssh", "-p", os.environ.get("MB_BOARD_AGENT_PORT", "19022"),
         "-i", os.environ.get("MB_BOARD_AGENT_KEY", str(HOME / ".ssh" / "iiswc-board-agent")),
         "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=8",
         "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
         "xilinx@localhost"]
GOAL = 10.0                       # cycles per output to aim for in "your turn"


def solved_runs() -> Path:
    """Unpack the stored runs (tools/pack_solved_runs.py made them) once, and return
    where they are.  Unpacked under ~/.cache, keyed by the archive's size and time."""
    import tarfile
    st = SOLVED.stat()
    dest = HOME / ".cache" / "mb_lab_solved" / f"{st.st_size}-{int(st.st_mtime)}"
    if not dest.is_dir():
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.mkdir(parents=True, exist_ok=True)
        with tarfile.open(SOLVED) as tar:
            tar.extractall(tmp, filter="data") if hasattr(tarfile, "data_filter") else tar.extractall(tmp)
        tmp.rename(dest)
    return dest


def use_runs(folder) -> None:
    """Read runs from another folder -- the solved notebook points this at its stored artifacts."""
    global RUNS
    RUNS = Path(os.path.expanduser(str(folder)))
_last = {"run": None}

# ---- look ------------------------------------------------------------------------------
# Explicit colours everywhere (cards carry their own background), so a dark JupyterLab theme
# does not turn text invisible.
SPIKE, FPGA, OFF, GOOD, BAD, INK, MUTED = "#3b82f6", "#f97316", "#fdba74", "#16a34a", "#dc2626", "#1f2937", "#6b7280"
CSS = f"""<style>
.mb {{font-family: -apple-system, 'Segoe UI', Roboto, sans-serif; color:{INK}; background:#f8fafc;
      border:1px solid #e5e7eb; border-radius:12px; padding:14px 18px; margin:6px 0; max-width:980px}}
.mb h3 {{margin:0 0 8px 0; font-size:17px; color:{INK}}}
.mb .sub {{color:{MUTED}; font-size:12.5px}}
.mb .row {{display:flex; gap:12px; flex-wrap:wrap; align-items:stretch}}
.mb .tile {{background:#fff; border:1px solid #e5e7eb; border-radius:10px; padding:8px 14px; min-width:120px}}
.mb .big {{font-size:26px; font-weight:700; line-height:1.15}}
.mb .lbl {{font-size:11.5px; color:{MUTED}; text-transform:uppercase; letter-spacing:.04em}}
.mb .badge {{display:inline-block; padding:3px 10px; border-radius:999px; font-weight:700; font-size:12.5px}}
.mb .yes {{background:#dcfce7; color:#166534}} .mb .no {{background:#fee2e2; color:#991b1b}}
.mb .warn {{background:#fef3c7; color:#92400e}}
.mb table {{border-collapse:collapse; font-size:13px; color:{INK}}}
.mb td, .mb th {{padding:4px 10px; border-bottom:1px solid #eef0f3; text-align:left !important}}
.mb details {{background:#fff; border:1px solid #e5e7eb; border-radius:10px; padding:6px 12px; margin:6px 0}}
.mb summary {{cursor:pointer; font-weight:600}}
.mb pre {{white-space:pre-wrap; font-size:12px; background:#fff; color:{INK}; padding:8px; border-radius:8px;
          border:1px solid #eef0f3; max-height:380px; overflow:auto}}
.mb .code {{flex:1; min-width:380px; max-height:460px; overflow:auto; border:1px solid #e5e7eb;
            border-radius:10px; background:#fff; font-size:12px}}
.mb .code pre {{border:none; max-height:none; margin:0}}
</style>"""


def _card(inner: str) -> HTML:
    return HTML(CSS + f'<div class="mb">{inner}</div>')


def _esc(s) -> str:
    return html.escape(str(s))


def _sh(cmd: str) -> str:
    p = subprocess.run(["bash", "-c", f"{ENV} && {cmd}"], capture_output=True, text=True)
    return ANSI.sub("", p.stdout + p.stderr)


def _json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


# ---- 0. is everything ready? -----------------------------------------------------------

def doctor() -> None:
    """Your seat, your board (through its tunnel) and the LLM key, as a checklist."""
    out = _sh("mb doctor")
    items = []
    for line in out.splitlines():
        m = re.match(r"\s*(ok|warn|FAIL)\s+(.*)", line)
        if m:
            cls = {"ok": "yes", "warn": "warn", "FAIL": "no"}[m.group(1)]
            items.append(f'<tr><td><span class="badge {cls}">{m.group(1)}</span></td><td>{_esc(m.group(2))}</td></tr>')
        elif line.strip().startswith("->"):
            items.append(f'<tr><td></td><td class="sub">{_esc(line.strip()[2:].strip())}</td></tr>')
    ready = "ready: run" in out
    head = (f'<span class="badge yes">READY</span>' if ready else '<span class="badge no">NOT READY</span>')
    display(_card(f"<h3>Checklist {head}</h3><table>{''.join(items)}</table>"))
    board()


def board() -> None:
    """Your board, live, through its tunnel."""
    try:
        st = json.loads(subprocess.run(AGENT + ["status"], capture_output=True, text=True, timeout=20).stdout)
    except Exception:
        st = {}
    b = st.get("board") or {}
    model = ""
    try:
        for line in (HOME / ".config/iiswc/bedrock.env").read_text().splitlines():   # MODEL only
            if line.startswith("MODEL="):
                model = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    if not st.get("ok"):
        display(_card('<h3>Your board <span class="badge no">not answering</span></h3>'
                      '<div class="sub">Is it on and on the WiFi? Its tunnel comes up a minute after boot.</div>'))
        return
    sig = b.get("signal_dbm")
    bars = "▂▄▆█"[: max(1, min(4, 4 + int((sig or -90) + 50) // 10))] if isinstance(sig, int) else "?"
    fpga_ok = b.get("pl_state") == "operating"

    def tile(lbl, val, sub=""):
        return f'<div class="tile"><div class="lbl">{lbl}</div><div class="big" style="font-size:19px">{val}</div><div class="sub">{sub}</div></div>'
    display(_card(
        f"<h3>Your board: {_esc(b.get('hostname', '?'))}</h3><div class='row'>"
        + tile("address", _esc(b.get("ipv4", "?")), _esc(b.get("iface", "")))
        + tile("WiFi", bars, f"{_esc(b.get('ssid', ''))} · {sig} dBm")
        + tile("FPGA", f"<span class='badge {'yes' if fpga_ok else 'warn'}'>{_esc(b.get('pl_state', '?'))}</span>",
               "Rocket RV64 + MBP, 40 MHz")
        + tile("link to this seat", f"<span class='badge yes'>{_esc(st.get('tunnel_unit', '?'))}</span>", "reverse ssh tunnel")
        + tile("the LLM", _esc(model or "?"), "AWS Bedrock, called from this seat")
        + "</div>"))


# ---- 1. the accelerator in one picture -------------------------------------------------

def _s8(x: int) -> int:
    x &= 0xFF
    return x - 256 if x > 127 else x


def max8(a, b):
    """MBP.MAX8, as the hardware does it: eight int8 lanes, lane-wise maximum."""
    if any(not isinstance(x, int) for x in list(a) + list(b)) or len(a) != 8 or len(b) != 8:
        raise ValueError("fill in the blanks first: two lists of eight whole numbers, each from -128 to 127")
    return [max(_s8(x), _s8(y)) for x, y in zip(a, b)]


def _lanes_svg(rows, highlight=None, title=""):
    """rows: [(label, [8 values] | None)]; highlight: {(row, lane)} drawn in orange."""
    W, lw, bw, bh, gap = 820, 190, 70, 30, 12
    H = 28 + len(rows) * (bh + gap)
    out = [f'<svg width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg" font-family="monospace">',
           f'<text x="0" y="16" font-size="13" fill="{MUTED}" font-family="sans-serif">{_esc(title)}</text>']
    for r, (label, vals) in enumerate(rows):
        y = 28 + r * (bh + gap)
        out.append(f'<text x="0" y="{y + 20}" font-size="12.5" fill="{INK}" font-family="sans-serif">{_esc(label)}</text>')
        for i in range(8):
            x = lw + i * (bw + 4)
            v = None if vals is None else vals[i]
            hl = highlight and (r, i) in highlight
            fill = FPGA if hl else ("#e5e7eb" if v is None else ("#dbeafe" if v >= 0 else "#ede9fe"))
            out.append(f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="6" fill="{fill}" stroke="#cbd5e1"/>')
            out.append(f'<text x="{x + bw / 2}" y="{y + 20}" font-size="14" text-anchor="middle" '
                       f'fill="{"#fff" if hl else INK}" font-weight="{700 if hl else 400}">{"" if v is None else v}</text>')
    out.append("</svg>")
    return "".join(out)


def accelerator(seed=None) -> None:
    """How MBP.MAX8 does a 2x2 max pool: four outputs from two instructions, on random bytes."""
    rnd = random.Random(seed)
    r0 = [rnd.randint(-128, 127) for _ in range(8)]
    r1 = [rnd.randint(-128, 127) for _ in range(8)]
    v = max8(r0, r1)
    shifted = v[1:] + [0]
    m = max8(v, shifted)
    rows = [("input row 2·oh (8 columns)", r0), ("input row 2·oh+1", r1),
            ("v = MBP.MAX8(row0, row1)", v), ("v >> 8 (one byte along)", shifted),
            ("m = MBP.MAX8(v, v >> 8)", m)]
    hl = {(4, 0), (4, 2), (4, 4), (4, 6)}
    outs = [m[0], m[2], m[4], m[6]]
    ref = [max(r0[2 * k], r0[2 * k + 1], r1[2 * k], r1[2 * k + 1]) for k in range(4)]
    display(_card(
        "<h3>One MBP.MAX8 does eight comparisons</h3>"
        + _lanes_svg(rows, hl, "each box is one int8 lane of a 64-bit register")
        + f"<div class='row' style='margin-top:6px'><div class='tile'><div class='lbl'>the four outputs (orange)</div>"
          f"<div class='big' style='font-size:20px'>{outs}</div></div>"
          f"<div class='tile'><div class='lbl'>the reference computes</div><div class='big' style='font-size:20px'>{ref}</div>"
          f"<div class='sub'>{'identical ✓' if outs == ref else 'different!'}</div></div>"
          f"<div class='tile'><div class='lbl'>cost</div><div class='big' style='font-size:20px'>2 MAX8 + 2 loads</div>"
          f"<div class='sub'>instead of 16 loads + 16 compares</div></div></div>"
        + "<div class='sub' style='margin-top:6px'>Run it again for other bytes, or try max8([...], [...]) yourself.</div>"))


# ---- 2. the live race ------------------------------------------------------------------

def _chart_svg(d: dict) -> str:
    n = d["n"] or 1
    rows = [r for r in d["rows"] if r[1] or r[2]] + [r for r in d["rows"] if not (r[1] or r[2])]
    vals = [v / n for r in rows for v in (r[1], r[2]) if v] + [v / n for v in d["mbpoff"].values()]
    if not vals:
        return f'<div class="sub">waiting for the first measurement…</div>'
    hi, lo = max(vals) * 1.1, max(min(vals) / 1.6, 0.5)
    lh, ll = math.log10(hi), math.log10(lo)
    W, lw, bx, bwid, rh = 900, 150, 150, 560, 46
    H = 34 + rh * len(rows)
    X = lambda v: bx + bwid * (math.log10(max(v, lo)) - ll) / (lh - ll)
    best = min((r[2] for r in rows if r[2] and r[0] != "reference"), default=None)
    out = [f'<svg width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg" font-family="sans-serif">']
    for t in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000):
        if lo <= t <= hi:
            out.append(f'<line x1="{X(t):.1f}" y1="22" x2="{X(t):.1f}" y2="{H - 4}" stroke="#eef0f3"/>'
                       f'<text x="{X(t):.1f}" y="16" font-size="10.5" fill="{MUTED}" text-anchor="middle">{t}</text>')
    if lo <= GOAL <= hi:
        out.append(f'<line x1="{X(GOAL):.1f}" y1="22" x2="{X(GOAL):.1f}" y2="{H - 4}" stroke="{GOOD}" stroke-dasharray="4 3"/>'
                   f'<text x="{X(GOAL) + 3:.1f}" y="30" font-size="10" fill="{GOOD}">goal</text>')
    out.append(f'<text x="{bx + bwid + 12}" y="16" font-size="10.5" fill="{SPIKE}">spike</text>'
               f'<text x="{bx + bwid + 70}" y="16" font-size="10.5" fill="{FPGA}">FPGA</text>')
    for i, (lab, sc, fc, idea, ok) in enumerate(rows):
        y = 30 + i * rh
        col = INK if ok else MUTED
        out.append(f'<text x="0" y="{y + 12}" font-size="12.5" font-weight="600" fill="{col}">{_esc(lab)}</text>')
        if lab in d["mbpoff"]:
            v = d["mbpoff"][lab] / n
            out.append(f'<rect x="{bx}" y="{y + 12}" width="{X(v) - bx:.1f}" height="9" rx="3" fill="none" '
                       f'stroke="{OFF}" stroke-dasharray="3 2"/>')
        if sc:
            out.append(f'<rect x="{bx}" y="{y + 2}" width="{max(2, X(sc / n) - bx):.1f}" height="9" rx="3" fill="{SPIKE}"/>')
        if fc:
            out.append(f'<rect x="{bx}" y="{y + 12}" width="{max(2, X(fc / n) - bx):.1f}" height="9" rx="3" fill="{FPGA}"/>')
        out.append(f'<text x="{bx + bwid + 12}" y="{y + 12}" font-size="12" fill="{SPIKE}">{f"{sc / n:,.1f}" if sc else ""}</text>'
                   f'<text x="{bx + bwid + 70}" y="{y + 12}" font-size="12" font-weight="{700 if fc and fc == best else 400}" '
                   f'fill="{FPGA}">{f"{fc / n:,.1f}" if fc else ""}{" ★" if fc and fc == best else ""}</text>')
        if idea:
            out.append(f'<text x="{bx}" y="{y + 36}" font-size="11" fill="{MUTED}">{_esc(idea[:110])}</text>')
    out.append("</svg>")
    return "".join(out)


def _live_card(run: str, done: bool = False) -> HTML:
    d = mb_progress.collect(RUNS / run)
    st, n, rows = d["status"], d["n"] or 1, d["rows"]
    ref_s = rows[0][1] if rows and rows[0][0] == "reference" else None
    ref_f = rows[0][2] if rows and rows[0][0] == "reference" else None
    bs = min((r[1] for r in rows[1:] if r[1]), default=None)
    bf = min((r[2] for r in rows[1:] if r[2]), default=None)

    def tile(lbl, val, sub="", color=INK):
        return f'<div class="tile"><div class="lbl">{lbl}</div><div class="big" style="color:{color}">{val}</div><div class="sub">{sub}</div></div>'
    state = st.get("state", "starting")
    badge = {"running": "warn", "done": "yes", "failed": "no"}.get(state, "warn")
    tiles = (tile("on spike (simulated)", f"{ref_s / bs:.1f}×" if ref_s and bs else "–", "best kernel so far", SPIKE)
             + tile("on your FPGA", f"{ref_f / bf:.1f}×" if ref_f and bf else "–", "measured each round", FPGA)
             + tile("LLM calls", d["calls"], _esc(d["model"] or ""))
             + tile("kernels tried", max(0, len(rows) - 1), "each one checked for correct output"))
    return _card(
        f"<h3>{_esc(run)} <span class='badge {badge}'>{_esc(state)}</span></h3>"
        f"<div class='sub' style='margin-bottom:8px'>{_esc(st.get('step', ''))}</div>"
        f"<div class='row'>{tiles}</div>"
        f"<div style='margin-top:10px'><div class='lbl'>cycles per output, log scale · "
        f"<span style='color:{SPIKE}'>■ spike</span> <span style='color:{FPGA}'>■ your FPGA</span> "
        f"<span style='color:{OFF}'>▭ FPGA with the MBP off</span></div>{_chart_svg(d)}</div>")


def _follow(args: str, show_verdict: bool = True) -> None:
    LOGS.mkdir(exist_ok=True)
    log = LOGS / f"nb-{int(time.time())}.log"
    p = subprocess.Popen(["bash", "-c", f"{ENV} && mb {args} > {log} 2>&1"])
    h = display(_card("<h3>starting…</h3><div class='sub'>building the reference kernel</div>"), display_id=True)
    run = None
    while p.poll() is None:
        time.sleep(3)
        text = ANSI.sub("", log.read_text(errors="replace")) if log.exists() else ""
        if run is None:
            m = re.search(r"run: (\S+)", text)
            run = m.group(1) if m else None
        if run and (RUNS / run).is_dir():
            try:
                h.update(_live_card(run))
            except Exception:
                pass
    text = ANSI.sub("", log.read_text(errors="replace"))
    if run is None:
        m = re.search(r"\((\d{8}-\d{6}-[a-z0-9_]+)\)", text)
        run = m.group(1) if m else None
    _last["run"] = run
    if run and (RUNS / run / "run.json").exists():
        h.update(_live_card(run, done=True))
        if show_verdict:
            verdict(run)
    else:
        h.update(_card(f"<h3>the run did not finish <span class='badge no'>failed</span></h3>"
                       f"<pre>{_esc(text[-2500:])}</pre><div class='sub'>whole log: {_esc(log)}</div>"))


def show(run: str) -> None:
    """A finished run, drawn as go() drew it when it ended: the chart and the verdict."""
    run = _run(run)
    display(_live_card(run, done=True))
    verdict(run)


def go(op: str = "maxpool2d_s8", *flags: str) -> None:
    """The LLM optimizes <op>, with your FPGA in the loop.  e.g. go("maxpool2d_s8", "--replay")"""
    _follow(" ".join(["go", op, *flags]), show_verdict=False)
    print("the verdict, and where the speedup came from: verdict()")


# ---- 3. the verdict --------------------------------------------------------------------

def _run(run: str) -> str:
    run = run or _last["run"] or (RUNS / "latest").resolve().name
    if not (RUNS / run).is_dir():
        raise ValueError(f"no run {run} -- runs() lists them")
    return run


def verdict(run: str = "") -> None:
    """Where the speedup came from: the reference, the new kernel without the MBP, and with it."""
    r = RUNS / _run(run)
    j = _json(r / "run.json") or {}
    b = j.get("board") if isinstance(j.get("board"), dict) else {}
    if not b or not b.get("before"):
        display(_card(f"<h3>{_esc(r.name)}</h3><pre>{_esc((r / 'report.txt').read_text())}</pre>"))
        return
    n = b["after"].get("out_len") or 1
    ref, new = b["before"]["op_cycles"] / n, b["after"]["op_cycles"] / n
    off = (b.get("mbpoff") or {}).get("op_cycles")
    off = off / n if off else None
    ops = {}
    for fn, c in ((b["after"].get("image") or {}).get("mbp_by_function") or {}).items():
        if fn != "neg_worker":
            for k, v in c.items():
                ops[k] = ops.get(k, 0) + v
    ops = ", ".join(f"MBP.{k.upper()} ×{v}" for k, v in ops.items() if v)
    exact = all((b.get(a) or {}).get("bit_exact", True) for a in ("before", "after", "mbpoff"))
    bars = [("the reference", ref, "#9ca3af")] + ([("new kernel, MBP off", off, OFF)] if off else []) + [("new kernel, MBP on", new, FPGA)]
    W, lw, bw, rh = 820, 170, 500, 34
    svg = [f'<svg width="{W}" height="{rh * len(bars) + 10}" xmlns="http://www.w3.org/2000/svg" font-family="sans-serif">']
    for i, (lab, v, c) in enumerate(bars):
        y = 4 + i * rh
        w = max(3, bw * v / ref)
        svg.append(f'<text x="0" y="{y + 18}" font-size="13" fill="{INK}">{_esc(lab)}</text>'
                   f'<rect x="{lw}" y="{y + 4}" width="{w:.1f}" height="20" rx="4" fill="{c}"/>'
                   f'<text x="{lw + w + 8:.1f}" y="{y + 18}" font-size="13" font-weight="600" fill="{INK}">{v:,.1f} cycles/output</text>')
    svg.append("</svg>")

    def tile(lbl, val, sub="", color=INK):
        return f'<div class="tile"><div class="lbl">{lbl}</div><div class="big" style="color:{color}">{val}</div><div class="sub">{sub}</div></div>'
    tiles = (tile("faster on your FPGA", f"{ref / new:.1f}×", "bit-exact ✓" if exact else "NOT bit-exact", FPGA)
             if ref >= new else
             tile("SLOWER on your FPGA", f"{ref / new:.2f}×", "correct, but the LLM got stuck: run go() again", BAD))
    if off:
        tiles += tile("from the accelerator", f"{off / new:.1f}×", "same code, MBP off vs on", FPGA)
        # NOT "from the rewritten loop".  -DMB_PEXT_HW=0 keeps the kernel's packed 8-byte
        # loads and swaps only mb_pext_max8 for its C model, so this arm prices the PACKED
        # DATAFLOW with the SIMD emulated -- it is not the loop standing on its own.  The
        # arm that would be is the same kernel with use_mbp forced to 0, and measured it is
        # 0.97x: 3% SLOWER than the reference (L434).  Labelling this one "the loop" invites
        # the reader to split the total into two independent wins, and it does not split.
        tiles += tile("packed, SIMD emulated", f"{ref / off:.1f}×", "reference vs new code, MBP off")
    tiles += tile("spike predicted", f"{j.get('speedup', 0):.1f}×", "no memory timing", SPIKE)
    acc = (f"<span class='badge yes'>ON THE ACCELERATOR · {ops}</span>" if ops
           else "<span class='badge no'>NOT ON THE ACCELERATOR · plain RISC-V code</span>")
    display(_card(f"<h3>The verdict {acc}</h3><div class='row'>{tiles}</div>"
                  f"<div style='margin-top:10px'>{''.join(svg)}</div>"
                  f"<div class='sub'>{_esc(r.name)} · {_esc(j.get('pick_after', ''))} · measured on "
                  f"{_esc(b.get('board', '?'))}, MAGIC {_esc(b.get('magic', '?'))}, 40 MHz</div>"))


def report(run: str = "") -> None:
    """The verdict of a run (default: your last one), and its full text report."""
    verdict(run)
    print((RUNS / _run(run) / "report.txt").read_text())


# ---- 4. the code and the conversation --------------------------------------------------

def _hl(code: str) -> str:
    try:
        from pygments import highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import CLexer
        lines = [i + 1 for i, l in enumerate(code.splitlines()) if "mb_pext_" in l or "MB_PEXT_" in l]
        return highlight(code, CLexer(), HtmlFormatter(noclasses=True, style="friendly", hl_lines=lines,
                                                       hl_color="#ffedd5"))
    except Exception:
        return f"<pre>{_esc(code)}</pre>"


def kernels(run: str = "") -> None:
    """The reference and the new kernel, side by side; the accelerator calls are highlighted."""
    r = RUNS / _run(run)
    j = _json(r / "run.json") or {}
    after = Path(j.get("after_kernel") or "")
    if not after.is_absolute():
        after = r / after
    elif not after.exists() and r.name in after.parts:      # a run unpacked elsewhere (the solved runs)
        after = r.joinpath(*after.parts[after.parts.index(r.name) + 1:])
    gen = (r / "before" / "gen" / "kernels.c").read_text(errors="replace")
    m = re.search(r"void kernel_%s\w*\(.*?\n}\n" % re.escape(j.get("op", "")), gen, re.S)
    before = m.group(0) if m else gen
    aft = after.read_text(errors="replace") if after.exists() else "(no kernel file)"
    n_acc = sum(1 for l in aft.splitlines() if "mb_pext_" in l)
    display(_card(
        f"<h3>The kernel, before and after</h3><div class='sub' style='margin-bottom:8px'>"
        f"<span style='background:#ffedd5; padding:0 4px'>highlighted</span>: lines that use the accelerator "
        f"({n_acc} in the new kernel)</div><div class='row'>"
        f"<div class='code'><div class='lbl' style='padding:6px 10px'>before · the reference</div>{_hl(before)}</div>"
        f"<div class='code'><div class='lbl' style='padding:6px 10px'>after · {_esc(j.get('pick_after', ''))}</div>{_hl(aft)}</div>"
        "</div>"))


def calls(run: str = "") -> None:
    """Every LLM call: what it tried (its idea line), how long it took, the prompt and the answer."""
    r = RUNS / _run(run)
    tp = r / "after" / "transcript.jsonl"
    if not tp.exists():
        display(_card("<h3>No LLM calls</h3><div class='sub'>a replay or your own kernel makes none</div>"))
        return
    cards = []
    for line in tp.read_text().splitlines():
        try:
            c = json.loads(line)
        except ValueError:
            continue
        idea = mb_progress.idea_of(c.get("response")) or ""
        code = re.search(r"```c?\n(.*?)```", c.get("response") or "", re.S)
        cards.append(
            f"<details><summary>#{c.get('n')} · round {c.get('round')} · {_esc(c.get('phase'))} · "
            f"{c.get('latency_s', 0):.0f} s · {c.get('input_tokens', 0):,} → {c.get('output_tokens', 0):,} tokens"
            f"<div class='sub' style='font-weight:400'>{_esc(idea)}</div></summary>"
            f"<details><summary class='sub'>the prompt</summary><pre>{_esc((c.get('user') or '').strip())}</pre></details>"
            f"<div class='lbl' style='margin-top:6px'>the answer</div>"
            f"{_hl(code.group(1)) if code else '<pre>' + _esc(c.get('response') or c.get('error') or '') + '</pre>'}</details>")
    display(_card(f"<h3>The conversation · {len(cards)} calls</h3>" + "".join(cards)))


def commands(run: str = "") -> None:
    """The exact commands a run executed -- ModelBlaster, west, spike, the board's agent --
    grouped by tool.  Any of them can be pasted into a terminal (File -> New -> Terminal)."""
    import mb_report
    r = RUNS / _run(run)
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mb_report.show_commands(r)
    blocks, cur, title = [], [], "setup"
    for line in buf.getvalue().splitlines():
        if line.startswith("# --- "):
            if cur:
                blocks.append((title, "\n".join(cur)))
            title, cur = line[6:], []
        elif line.strip() or cur:
            cur.append(line)
    if cur:
        blocks.append((title, "\n".join(cur)))
    try:
        from pygments import highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import BashLexer
        hl = lambda c: highlight(c, BashLexer(), HtmlFormatter(noclasses=True, style="friendly"))
    except Exception:
        hl = lambda c: f"<pre>{_esc(c)}</pre>"
    parts = "".join(f"<div class='lbl' style='margin-top:10px'>{_esc(t)}</div><div class='code' style='max-height:220px'>{hl(c)}</div>"
                    for t, c in blocks)
    display(_card(f"<h3>Under the hood: the exact commands of {_esc(r.name)}</h3>"
                  "<div class='sub'>The lab is these tools, in this order. Paste any line into a terminal to run it yourself.</div>"
                  + parts))


def sh(cmd: str) -> None:
    """Run a command in the lab's environment (ModelBlaster, west, spike on the PATH) and show it."""
    print(f"$ {cmd}")
    print(_sh(cmd), end="")


# ---- 5. your turn ----------------------------------------------------------------------

def start(op: str = "maxpool2d_s8", from_run: str = "") -> None:
    """Put the unoptimized kernel (or a run's kernel) in your-kernel/<op>.c for you to edit."""
    out = _sh(f"mb start {op} {from_run}")
    ok = f"{op}.c" in out
    display(_card(f"<h3>{'Your kernel is ready' if ok else 'Could not start'}</h3>"
                  f"<div>Open <b>your-kernel/{op}.c</b> in the file browser on the left, edit it, save with "
                  f"<b>Ctrl-S</b>, then run <code>lab.try_kernel()</code>. Its header has the rules and four hints.</div>"
                  f"<pre>{_esc(out.strip())}</pre>"))


def try_kernel(op: str = "maxpool2d_s8", path: str = "") -> None:
    """Check your kernel on spike, then run it on your FPGA; then your scoreboard."""
    _follow(" ".join(["try", op, path]).strip())
    scoreboard()


def scoreboard() -> None:
    """Every kernel you tried with try_kernel(), against the goal."""
    rows = []
    for d in sorted(RUNS.iterdir()) if RUNS.is_dir() else []:
        j = _json(d / "run.json")
        if d.is_symlink() or not j or not j.get("kernel_file"):
            continue
        b = j.get("board") if isinstance(j.get("board"), dict) else {}
        n = ((b.get("after") or {}).get("out_len")) or 1
        cpo = (b.get("after") or {}).get("op_cycles")
        cpo = cpo / n if cpo else None
        acc = bool(((b.get("after") or {}).get("image") or {}).get("mbp_outside_negtest"))
        rows.append((d.name, j.get("kernel_file"), cpo, b.get("speedup"), acc, j.get("verdict")))
    if not rows:
        return
    best = min((r[2] for r in rows if r[2]), default=None)
    tr = "".join(
        f"<tr><td>{_esc(name[9:11] + ':' + name[11:13])}</td><td>{_esc(Path(kf).name)}</td>"
        f"<td><b>{f'{cpo:.1f}' if cpo else '–'}</b>{' ★' if cpo and cpo == best else ''}</td>"
        f"<td>{f'{sp:.1f}×' if sp else '–'}</td>"
        f"<td><span class='badge {'yes' if acc else 'no'}'>{'MBP' if acc else 'scalar'}</span></td>"
        f"<td><span class='badge {'yes' if v == 'PASS' else 'no'}'>{_esc(v)}</span></td></tr>"
        for name, kf, cpo, sp, acc, v in rows)
    goal = (f"<span class='badge yes'>goal reached: {best:.1f} &lt; {GOAL:g} cycles/output</span>" if best and best < GOAL
            else f"<span class='badge warn'>goal: under {GOAL:g} cycles/output on the FPGA</span>")
    display(_card(f"<h3>Your scoreboard {goal}</h3><table><tr><th>time</th><th>kernel</th><th>cycles/output</th>"
                  f"<th>speedup</th><th>uses</th><th>verdict</th></tr>{tr}</table>"))


def solution(op: str = "maxpool2d_s8") -> None:
    """The solution kernel (15.3x on the FPGA)."""
    code = (REPO / "fpga/pynq-z2/modelblaster/mb_ops/exercises" / f"{op}_solution.c").read_text()
    display(_card(f"<h3>A solution</h3><div class='code'>{_hl(code)}</div>"))


def _x(v) -> str:
    return f"{v:.1f}×" if isinstance(v, (int, float)) else ""


def runs() -> None:
    """Every run on this seat."""
    rows = []
    for d in sorted(RUNS.iterdir()) if RUNS.is_dir() else []:
        if d.is_symlink() or not d.is_dir():
            continue
        j = _json(d / "run.json")
        st = _json(d / "status.json") or {}
        if not j and not st:
            continue
        b = (j or {}).get("board") if isinstance((j or {}).get("board"), dict) else {}
        how = "yours" if (j or {}).get("kernel_file") else "replay" if (j or {}).get("replay") else (j or {}).get("backend", "")
        rows.append(f"<tr><td>{_esc(d.name)}</td><td>{_esc(how)}</td>"
                    f"<td>{_esc((j or {}).get('verdict') or st.get('state', ''))}</td>"
                    f"<td>{_x((j or {}).get('speedup'))}</td><td style='color:{FPGA}'>{_x(b.get('speedup'))}</td></tr>")
    display(_card("<h3>Your runs</h3><div class='sub'>report(&quot;&lt;run&gt;&quot;) shows one again</div>"
                  "<table><tr><th>run</th><th>how</th><th>verdict</th><th>spike</th><th>FPGA</th></tr>"
                  + "".join(rows) + "</table>"))
