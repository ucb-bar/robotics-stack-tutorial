"""Helpers for the IISWC 2026 attendee notebook.

Three rules, and the whole module exists to keep them:

1. **Every cell says where it runs.** Work on the instance goes through `sh()`.
   Work on the board goes through `board()`, and through nothing else.
2. **The board may be absent.** `board_status()` answers in under a few seconds and
   `board()` never blocks forever, so a missing card is a status line and not a
   hung kernel.
3. **A stub says it is a stub.** The transport that carries `board()` to the card
   (`/opt/iiswc/host/tunnel_agent.sh` over the reverse tunnel, see
   `docs/TUTORIAL_INTERFACE_NOTES.md` §4c/§4f) is not built yet. Until it is,
   `board()` returns a result whose state is `stub` and whose stdout is `None`.
   It never invents output.

Swapping the real transport in is one edit: `_board_exec()`, below.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------------------
# Where the board lives, once it is there.  TUTORIAL_INTERFACE_NOTES.md §4f.
#
# The tunnel endpoint, the board user and the key are NOT repeated here: they belong to
# `fpga/pynq-z2/host/board_link.py`, which is the other end of the board's forced command.
# Two copies of a protocol constant is one copy too many.
# --------------------------------------------------------------------------------------

# Measured room aggregate, B170 / L412: ten radios deliver what one radio delivers.
ROOM_MIB_PER_S = 4.021
ROOM_SEATS = 30


# --------------------------------------------------------------------------------------
# On the instance
# --------------------------------------------------------------------------------------
@dataclass
class Result:
    cmd: str
    returncode: int
    stdout: str
    seconds: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def __repr__(self) -> str:
        return f"<{'ok' if self.ok else 'rc=' + str(self.returncode)} in {self.seconds:.1f}s>"


#: Variables an IPython kernel exports into every child it spawns, and which break
#: commands that are correct in a shell. `MPLBACKEND=module://matplotlib_inline...`
#: is the one that bites: XPU-RT's solve (Unit 4) imports matplotlib from a DIFFERENT
#: interpreter (/opt/xpurt/venv), which has no `matplotlib_inline`, so the published
#: command works at a prompt and dies with `ValueError: Key backend` in a notebook.
#: Measured on a tutorial instance, 2026-09-24.
_KERNEL_LEAKS = ("MPLBACKEND",)


def _clean_env() -> dict:
    env = dict(os.environ)
    for k in _KERNEL_LEAKS:
        env.pop(k, None)
    return env


def sh(cmd: str, timeout: int = 600, cwd: str | None = None, quiet: bool = False) -> Result:
    """Run a command **on this instance**, streaming as it goes, with a hard timeout.

    The timeout is not optional decoration: a cell that can hang is the failure mode
    this whole notebook is designed against.
    """
    t0 = time.time()
    chunks: list[str] = []
    proc = subprocess.Popen(
        ["bash", "-lc", cmd],
        cwd=cwd,
        env=_clean_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            chunks.append(line)
            if not quiet:
                sys.stdout.write(line)
                sys.stdout.flush()
            if time.time() - t0 > timeout:
                proc.kill()
                print(f"\n[timed out after {timeout} s -- killed]")
                break
        proc.wait(timeout=10)
    except KeyboardInterrupt:
        proc.kill()
        raise
    dt = time.time() - t0
    rc = proc.returncode if proc.returncode is not None else -1
    if not quiet:
        print(f"[rc={rc}  {dt:.1f} s]")
    return Result(cmd=cmd, returncode=rc, stdout="".join(chunks), seconds=dt)


# --------------------------------------------------------------------------------------
# The board
# --------------------------------------------------------------------------------------
OFFLINE = "offline"      # nothing is listening: the card has not dialled in
STUB = "stub"            # a path exists, but the transport that uses it is not built
CONNECTED = "connected"  # a command really ran on the card


@dataclass
class BoardResult:
    state: str
    cmd: str
    stdout: str | None = None
    returncode: int | None = None
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.state == CONNECTED and self.returncode == 0

    def __repr__(self) -> str:
        head = {OFFLINE: "board offline", STUB: "STUB -- nothing ran on the board",
                CONNECTED: "board"}.get(self.state, self.state)
        body = self.stdout if self.stdout is not None else "(no output: nothing ran)"
        return f"[{head}] {self.cmd}\n{self.note}\n{body}".strip()


#: Where B176's instance-side client may have landed. It is the other end of
#: `/opt/iiswc/host/tunnel_agent.sh` and the two are one protocol.
_BOARD_LINK_PATHS = (
    os.environ.get("IISWC_BOARD_LINK", ""),
    str(Path(__file__).resolve().parent),   # beside this module, however it was started
    str(Path.cwd()),
    str(Path.home() / "tut" / "fpga" / "pynq-z2" / "host"),
    str(Path.home() / "iiswc-tutorial" / "fpga" / "pynq-z2" / "host"),
    "/opt/iiswc/host",
)


def _board_link():
    """THE transport, and the only place that knows how a board is reached.

    Returns B176's `BoardLink` instance, or None when `board_link.py` is not on this
    instance yet. Everything else in this module goes through it, so moving to a
    different transport is an edit here and nowhere else.
    """
    for p in _BOARD_LINK_PATHS:
        if p and (Path(p) / "board_link.py").exists():
            if p not in sys.path:
                sys.path.insert(0, p)
            try:
                import board_link  # noqa: PLC0415 - located at call time on purpose
            except ImportError:
                return None
            return board_link.BoardLink()
    return None


def board_status(verbose: bool = True) -> str:
    """`connected` / `stub` / `offline`, in a few seconds, never hanging.

    This is the cell to re-run when something on the board stops answering.

    It does **not** merely check that something is listening on the tunnel port. That
    port belongs to this instance's sshd, which keeps accepting connections for as long
    as a dead session survives -- so `connect()` succeeds for a board that is gone.
    `board_link.probe()` waits for the SSH identification string, which can only have
    come from the board, and reports "nothing is listening" and "tunnel is stale" apart.
    """
    link = _board_link()
    if link is None:
        state, why = STUB, (
            "board_link.py is not on this instance, so there is no transport to the "
            "card and no board cell can run. Nothing here is faking it. "
            "(fpga/pynq-z2/host/board_link.py, TUTORIAL_INTERFACE_NOTES.md §4f.)")
    else:
        line = link.status_line()
        state = CONNECTED if line.startswith("board connected") else OFFLINE
        why = line
        if state == CONNECTED and "agent not answering" in line:
            state = STUB
    if verbose:
        print(why if link is not None else
              f"board path NOT BUILT (stub)\n  {why}")
    return state


def board(*words: str, timeout: float | None = None, stdin: bytes = b"",
          binary: bool = False, verbose: bool = True) -> BoardResult:
    """Run one agent verb **on your card**, or say plainly why it did not run.

    The card does not take arbitrary commands: its key carries a forced command that
    accepts a fixed verb set -- `ping`, `status`, `help`, `ls`, `put`, `get`,
    `bitstream`, `run`, `camera`, `mic` -- so that a compromised instance cannot obtain
    a shell on the board (B176). `lab.board("status")` is the whole interface.
    """
    cmd = " ".join(words)
    link = _board_link()
    if link is None:
        r = BoardResult(state=STUB, cmd=cmd, note=(
            "STUB: board_link.py is not on this instance. Nothing ran on the card and "
            "no output is being invented."))
    else:
        probe = link.probe()
        if not probe["connected"]:
            r = BoardResult(state=OFFLINE, cmd=cmd, note=(
                f"board offline ({probe['reason']}) -- {probe.get('detail', '')}"))
        else:
            try:
                reply = link.call(*words, stdin=stdin, binary=binary, timeout=timeout)
                out = reply if binary else json.dumps(reply, indent=2)
                r = BoardResult(state=CONNECTED, cmd=cmd, stdout=out, returncode=0)
            except Exception as exc:  # BoardError, TimeoutExpired, anything
                r = BoardResult(state=CONNECTED, cmd=cmd, returncode=1,
                                note=f"the card refused or failed: {type(exc).__name__}: {exc}")
    if verbose:
        print(repr(r))
    return r


def board_put(path: str | Path, name: str | None = None, verbose: bool = True) -> BoardResult:
    """Push one file to the card's incoming directory, length and md5 declared.

    Check what it costs the room first -- `lab.budget()` -- because every byte crosses
    the one shared 2.4 GHz channel that ten boards already saturate.
    """
    path = Path(path)
    cmd = f"put {name or path.name} ({path.stat().st_size:,} B)"
    link = _board_link()
    if link is None:
        r = BoardResult(state=STUB, cmd=cmd, note=(
            "STUB: board_link.py is not on this instance. Nothing was sent."))
    elif not link.probe()["connected"]:
        r = BoardResult(state=OFFLINE, cmd=cmd, note="board offline. Nothing was sent.")
    else:
        try:
            reply = link.put_file(str(path), name)
            r = BoardResult(state=CONNECTED, cmd=cmd,
                            stdout=json.dumps(reply, indent=2), returncode=0)
        except Exception as exc:
            r = BoardResult(state=CONNECTED, cmd=cmd, returncode=1,
                            note=f"the card refused the push: {type(exc).__name__}: {exc}")
    if verbose:
        print(repr(r))
    return r


# --------------------------------------------------------------------------------------
# The room's airtime budget -- B170 / L412
# --------------------------------------------------------------------------------------
def room_seconds(nbytes: int, seats: int = ROOM_SEATS) -> float:
    """How long `nbytes` per seat occupies the room's ONE shared 2.4 GHz channel."""
    return (nbytes * seats) / (ROOM_MIB_PER_S * 1024 * 1024)


def budget(label: str, nbytes: int, seats: int = ROOM_SEATS) -> None:
    s = room_seconds(nbytes, seats)
    print(f"{label:<34} {nbytes:>12,} B/seat   {seats} seats -> {s:7.1f} s of room airtime")


# --------------------------------------------------------------------------------------
# Did my run match?
# --------------------------------------------------------------------------------------
def expect(label: str, got, want, tol: float | None = None) -> bool:
    if tol is not None and isinstance(got, (int, float)) and isinstance(want, (int, float)):
        good = abs(got - want) <= tol
    else:
        good = got == want
    print(f"{'MATCH  ' if good else 'DIFFERS'}  {label}: got {got!r}, expected {want!r}")
    return good


def where_am_i() -> None:
    """One line of orientation: this cell runs on your instance, not on your card."""
    seat_file = Path("/etc/iiswc/seat")
    seat = seat_file.read_text().strip() if seat_file.exists() else "not provisioned"
    print(f"host   {socket.gethostname()}")
    print(f"python {sys.version.split()[0]}  ({sys.executable})")
    print(f"cwd    {os.getcwd()}")
    print(f"seat   {seat}")


# --------------------------------------------------------------------------------------
# Pre-seeded artifacts (Unit 3): a TACIT capture, so nobody waits for a capture path
# that has no attendee sequence yet.
# --------------------------------------------------------------------------------------
ASSETS = Path(__file__).resolve().parent / "assets"


def unpack_trace(name: str = "rocket_tacit_trace", dest: Path | None = None) -> Path:
    """Expand the shipped trace next to the notebook and print its provenance."""
    import gzip

    meta = json.loads((ASSETS / f"{name}.meta.json").read_text())
    out = Path(dest or Path.cwd()) / f"{name}.perfetto.json"
    out.write_bytes(gzip.decompress((ASSETS / f"{name}.perfetto.json.gz").read_bytes()))
    print(f"{out}  ({out.stat().st_size:,} B)")
    print(f"  captured {meta['captured']} on {meta['captured_on']}")
    print(f"  {meta['instructions_decoded']:,} instructions, "
          f"{meta['packets_decoded']:,} packets, "
          f"{meta['bits_per_instruction']} bits/instruction")
    print(f"  NOT captured by you: {meta['bitstream_note']}")
    return out


def trace_summary(path: str | Path, top: int = 10) -> None:
    """Where the SoC actually spent its time, from a Perfetto B/E timeline."""
    import collections

    ev = json.loads(Path(path).read_text())["traceEvents"]
    ts = [e["ts"] for e in ev]
    stack: list[list] = []
    self_ = collections.Counter()
    calls = collections.Counter()
    for e in ev:
        if e["ph"] == "B":
            stack.append([e["name"], e["ts"], 0])
            calls[e["name"]] += 1
        elif e["ph"] == "E" and stack:
            name, t0, child = stack.pop()
            dur = e["ts"] - t0
            self_[name] += dur - child
            if stack:
                stack[-1][2] += dur
    total = sum(self_.values()) or 1
    print(f"{len(ev):,} events, {len(self_)} distinct functions, "
          f"span {max(ts) - min(ts):,} trace ticks, {len(stack)} unclosed")
    print(f"\n{'function':<26}{'self ticks':>12}{'share':>8}{'calls':>9}")
    for name, v in self_.most_common(top):
        print(f"{name:<26}{v:>12,}{100 * v / total:>7.1f}%{calls[name]:>9,}")


# --------------------------------------------------------------------------------------
# Finding this repository's committed data, wherever the notebook was started
# --------------------------------------------------------------------------------------
_REPO_HINTS = ("expected", "scripts/lib", "fpga/pynq-z2")


def _repo_roots():
    here = Path(__file__).resolve()
    for base in (*here.parents, Path.cwd(), Path.home() / "tut",
                 Path.home() / "iiswc-tutorial", Path.home() / "iiswc-public"):
        if all((base / h).is_dir() for h in _REPO_HINTS):
            yield base


def repo_root() -> Path | None:
    """The tutorial checkout this notebook is shipping inside, or None."""
    return next(_repo_roots(), None)


def repo_file(rel: str) -> Path | None:
    """The first checkout on hand that actually HAS this file.

    More than one tree can look like a checkout on the same box -- a full clone and a
    curated public one -- and they do not carry the same files. Returning the first
    root and letting the caller open a path that is not there turns "this tree does not
    ship it" into a TypeError three cells later.
    """
    for root in _repo_roots():
        if (root / rel).exists():
            return root / rel
    return None


# --------------------------------------------------------------------------------------
# Figures.
#
# Palette: the two categorical slots are blue #2a78d6 and orange #eb6834, validated
# together for all pairs on a light surface (CVD dE 24.7 protan, normal-vision 33.6,
# both clear of the floors).  Status colours are the reserved pair and always carry a
# word as well, never colour alone.  Light surface only: a notebook renders a PNG and
# cannot follow the reader's theme, so the figures do not pretend to.
# --------------------------------------------------------------------------------------
INK, INK2, GRID = "#0b0b0b", "#52514e", "#dddcd8"
S1, S2 = "#2a78d6", "#eb6834"          # slot 1, slot 2
GOOD, BAD = "#0ca30c", "#d03b3b"       # status, never alone
REFERENCE_MS = 4000.0


def _axes_style(ax):
    ax.set_facecolor("#fcfcfb")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8, length=3)
    ax.grid(axis="x", color=GRID, lw=0.6, zorder=0)
    ax.set_axisbelow(True)


def b157_figure(golden: dict):
    """The B157 result, drawn from the committed golden -- no solve, no artifacts.

    Two panels on ONE shared millisecond axis:
      * what the compaction post-pass moves on the two CP-SAT arms;
      * where all 36 heuristic cells land, and that none clears both objectives.
    """
    import matplotlib.pyplot as plt

    h, rows = golden["headline_cells"], golden["rows"]
    fig, (a, b) = plt.subplots(2, 1, figsize=(9.6, 6.0), sharex=True,
                               gridspec_kw={"height_ratios": [1.0, 1.0]})

    # -- panel A: plain -> compact ---------------------------------------------------
    arms = [("dram\nmeasured DRAM contention", h["cpsat_dram_plain"], h["cpsat_dram_compact"]),
            ("none\nuncontended", h["cpsat_none_plain"], h["cpsat_none_compact"])]
    for i, (label, plain, compact) in enumerate(arms):
        x0, x1 = plain["moonshine_end_ms"], compact["moonshine_end_ms"]
        a.plot([x1, x0], [i, i], color=INK2, lw=2, zorder=2, solid_capstyle="round")
        a.plot([x0], [i], "o", ms=10, color=S1, zorder=3,
               label="no compaction" if i == 0 else None)
        a.plot([x1], [i], "o", ms=10, color=S2, zorder=3,
               label="XPURT_COMPACT=1" if i == 0 else None)
        for x, cell in ((x0, plain), (x1, compact)):
            ok = cell["real_time_ok"]
            a.annotate(f"{x:,.0f}", (x, i), textcoords="offset points", xytext=(0, 13),
                       ha="center", fontsize=8.5, color=INK, weight="bold")
            a.annotate("PASSES" if ok else "FAILS", (x, i), textcoords="offset points",
                       xytext=(0, -20), ha="center", fontsize=7.5,
                       color=GOOD if ok else BAD, weight="bold")
        a.annotate(f"{x0 - x1:,.2f} ms recovered,\nno window traded",
                   ((x0 + x1) / 2, i), textcoords="offset points", xytext=(0, -42),
                   ha="center", fontsize=7.5, color=INK2)
    a.set_yticks(range(len(arms)))
    a.set_yticklabels([lbl for lbl, _, _ in arms], fontsize=8.5)
    a.set_ylim(-0.75, len(arms) - 0.25)
    a.set_title("Compaction is what crosses the window  ·  CP-SAT, all four detector "
                "windows land in every cell here",
                fontsize=10, color=INK, loc="left", pad=22)
    a.legend(frameon=False, fontsize=8, loc="upper left",
             bbox_to_anchor=(0.0, 1.16), ncols=2)
    _axes_style(a)

    # -- panel B: every heuristic cell ------------------------------------------------
    heur = [r for r in rows if r["policy"] not in ("cpsat", "cpsat_warmbest_b157")
            and r["compaction"] == "plain"]
    land = [r for r in heur if len(r["windows_landed"].strip("-")) == 4]
    miss = [r for r in heur if r not in land]
    b.plot([r["moonshine_end_ms"] for r in miss], [0.3] * len(miss), "o", ms=7,
           color=S1, alpha=0.6, zorder=3, label=f"misses a window  ({len(miss)} cells)")
    b.plot([r["moonshine_end_ms"] for r in land], [-0.3] * len(land), "o", ms=7,
           color=S2, zorder=3, label=f"lands all four  ({len(land)} cells)")
    best = min(heur, key=lambda r: r["moonshine_end_ms"])
    b.annotate(f"fastest heuristic {best['moonshine_end_ms']:,.0f} ms ({best['policy']})\n"
               f"and it lands only {len(best['windows_landed'].strip('-'))} of 4 windows",
               (best["moonshine_end_ms"], 0.3), textcoords="offset points",
               xytext=(0, 16), ha="left", fontsize=7.5, color=INK)
    if land:
        edf = min(land, key=lambda r: r["moonshine_end_ms"])
        b.annotate(f"{edf['policy']} lands all four, at {edf['moonshine_end_ms']:,.0f} ms\n"
                   f"{edf['moonshine_end_ms'] - REFERENCE_MS:,.0f} ms past the reference",
                   (edf["moonshine_end_ms"], -0.3), textcoords="offset points",
                   xytext=(0, -34), ha="center", fontsize=7.5, color=INK)
    b.set_yticks([])
    b.set_ylim(-1.0, 1.0)
    b.set_xlabel("Moonshine end (ms) — lower is better", fontsize=8.5, color=INK2)
    b.set_title(f"No heuristic clears both objectives  ·  {len(heur)} cells "
                "(compaction moves 0 dispatches on all nine, so only the plain arm is drawn)",
                fontsize=9.2, color=INK, loc="left", pad=22)
    b.legend(frameon=False, fontsize=8, loc="upper left",
             bbox_to_anchor=(0.0, 1.16), ncols=2)
    _axes_style(b)

    lo = min(r["moonshine_end_ms"] for r in rows) - 500
    hi = max(r["moonshine_end_ms"] for r in rows) + 500
    b.set_xlim(lo, hi)
    for ax in (a, b):
        ax.axvline(REFERENCE_MS, color=BAD, lw=1.4, ls=(0, (5, 3)), zorder=1)
    b.annotate("4,000 ms reference\n(RTF 1 on a 4 s utterance)", (REFERENCE_MS, 0.97),
               xycoords=("data", "axes fraction"), textcoords="offset points",
               xytext=(-8, 0), ha="right", va="top", fontsize=7.5, color=BAD)
    fig.tight_layout()
    return fig


def b156_figure(lanes: dict):
    """B156's two harts, drawn from the measured lane table (`assets/b156_lanes.json`).

    `model_pct_per_bucket` is gate A's series: the fraction of each bucket with an
    `mb_pext_conv` frame on the stack.  One wall clock bounds both lanes, so what the
    picture is for is that both series stay up and then stop together.
    """
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9.6, 4.0))
    span_s = max(l["ts_max"] for l in lanes["lanes"].values()) / 40e6
    ends = []
    for pid, colour in (("0", S1), ("1", S2)):
        lane = lanes["lanes"][pid]
        series = lane["model_pct_per_bucket"]
        xs = [(i + 0.5) * span_s / len(series) for i in range(len(series))]
        ax.plot(xs, series, "-o", ms=6, lw=2, color=colour, zorder=3,
                label=f"{lane['name']} — in the model {lane['model_time_pct']:.1f}% "
                      f"of the window")
        end = lane["model_last"] / 40e6
        ends.append(end)
        ax.axvline(end, color=colour, lw=1.2, ls=(0, (4, 3)), zorder=2)
    ax.annotate(f"both lanes' model work ends here,\n"
                f"{lanes['ends_together_s']:.2f} s apart "
                f"({lanes['ends_together_pct']:.1f}% of the window)",
                (max(ends), 52), textcoords="offset points", xytext=(-10, 0),
                ha="right", fontsize=7.5, color=INK,
                bbox=dict(facecolor="#fcfcfb", edgecolor=GRID, pad=2.5))
    ax.set_xlim(0, span_s * 1.02)
    ax.set_ylim(-4, 104)
    ax.set_xlabel("seconds from reset", fontsize=8.5, color=INK2)
    ax.set_ylabel("% of bucket inside the model", fontsize=8.5, color=INK2)
    ax.set_title(f"One wall clock bounds both harts — {lanes['ends_together_s']:.2f} s "
                 f"apart at the end, {lanes['ends_together_pct']:.1f}% of a "
                 f"{span_s:.2f} s window", fontsize=10, color=INK, loc="left", pad=10)
    ax.legend(frameon=False, fontsize=8, loc="lower center", ncols=1)
    _axes_style(ax)
    ax.grid(axis="y", color=GRID, lw=0.6, zorder=0)
    fig.tight_layout()
    return fig
