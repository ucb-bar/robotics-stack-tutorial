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



# --------------------------------------------------------------------------------------
# On the instance
# --------------------------------------------------------------------------------------
@dataclass
class Result:
    cmd: str
    returncode: int
    stdout: str
    seconds: float
    log: str = ""      # where the complete output was written, when it was truncated

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


SH_HEAD = 12     # lines shown from the start
SH_TAIL = 30     # lines shown from the end
SH_LOG_DIR = Path.home() / "work" / "logs"


def sh(cmd: str, timeout: int = 600, cwd: str | None = None, quiet: bool = False,
       head: int = SH_HEAD, tail: int = SH_TAIL, full: bool = False) -> Result:
    """Run a command on this instance, with a hard timeout.

    The timeout is not optional decoration: a cell that can hang is the failure mode
    this whole notebook is designed against.

    Long output is truncated. A `west build` prints thousands of lines, most of them
    repeated compiler warnings, and scrolling past them to reach the result is worse
    than not seeing them. The first `head` and last `tail` lines are shown, the count
    of elided lines is stated, and the complete output is always written to a file
    under ~/work/logs whose path is printed. Pass `full=True` to stream everything.
    """
    t0 = time.time()
    chunks: list[str] = []
    shown = 0
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
                if full or shown < head:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    shown += 1
                elif len(chunks) % 200 == 0:
                    # One overwritten line, so a long build shows progress without
                    # filling the cell.
                    sys.stdout.write(f"\r    ... {len(chunks)} lines, {time.time()-t0:.0f} s")
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
    out = "".join(chunks)

    log = None
    if len(chunks) > head + tail and not full:
        try:
            SH_LOG_DIR.mkdir(parents=True, exist_ok=True)
            log = SH_LOG_DIR / f"sh-{int(t0)}.log"
            log.write_text(out)
        except OSError:
            log = None   # a read-only home is not a reason to lose the result

    if not quiet and not full and len(chunks) > head + tail:
        elided = len(chunks) - head - tail
        warn = sum(1 for ln in chunks if "warning:" in ln)
        err = sum(1 for ln in chunks if "error:" in ln)
        counts = f"  ({warn} warning lines, {err} error lines)" if warn or err else ""
        sys.stdout.write("\r" + " " * 48 + "\r")
        print(f"    ... {elided} lines elided{counts}")
        if log:
            print(f"    full output: {log}")
        print("".join(chunks[-tail:]), end="")

    if not quiet:
        print(f"[rc={rc}  {dt:.1f} s]")
    return Result(cmd=cmd, returncode=rc, stdout=out, seconds=dt, log=str(log) if log else "")


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


#: Where the instance-side client may have landed. It is the other end of
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

    Returns the `BoardLink` instance, or None when `board_link.py` is not on this
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
    a shell on the board. `lab.board("status")` is the whole interface.
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

     -- because every byte crosses
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
# --------------------------------------------------------------------------------------


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



def show_perfetto(trace_path: str | Path) -> None:
    """Open a Perfetto timeline inside this notebook, without the file leaving the instance.

    The viewer runs in an iframe and is handed the bytes over `postMessage`, so nothing is
    uploaded anywhere and nothing is downloaded to the reader's laptop.  The handshake is
    the fiddly part and it is all here: the viewer answers PING with PONG only once it has
    booted, and a buffer posted before that is dropped silently.
    """
    import os
    from IPython.display import HTML, display

    t = Path(trace_path)
    # The path is relative to the root this instance's Jupyter is serving, because that is
    # what /files/ resolves against.
    home = Path.home()
    root = next((r for r in (home / "work", home) if r in t.parents), home)
    url = "/files/" + os.path.relpath(t, root)
    print("fetching", url)

    # NO BACKSLASH BELOW, ON PURPOSE.  The HTML is an ordinary Python string, so a JS
    # "join('\n')" written here would become a literal newline inside a JS string literal --
    # a syntax error, and the symptom is a script that silently never runs.
    display(HTML("""
<div id="pflog" style="font:12px/1.5 monospace;white-space:pre;border:1px solid #888;padding:6px">loading ...</div>
<iframe id="pfui" src="https://ui.perfetto.dev/#!/"
        style="width:100%;height:520px;border:1px solid #888;margin-top:6px"></iframe>
<script>
(function () {
  var NL = String.fromCharCode(10), lines = [];
  var log = document.getElementById('pflog');
  function say(s) { lines.push(s); log.textContent = lines.join(NL); }
  var f = document.getElementById('pfui'), buf = null, sent = false, tries = 0, timer = null;
  f.addEventListener('load', function () { say('viewer loaded'); });
  fetch('""" + url + """', {credentials: 'same-origin'})
    .then(function (r) { say('fetch -> HTTP ' + r.status); return r.arrayBuffer(); })
    .then(function (b) { buf = b; say('trace in the page: ' + b.byteLength + ' bytes'); })
    .catch(function (e) { say('fetch failed: ' + e); });
  window.addEventListener('message', function (e) {
    if (e.data !== 'PONG' || sent || !buf) { return; }
    clearInterval(timer); sent = true;
    f.contentWindow.postMessage({perfetto: {buffer: buf, title: 'Rocket SoC'}},
                                'https://ui.perfetto.dev');
    say('handed to the viewer -- answer Yes in the frame below');
  });
  timer = setInterval(function () {
    tries++;
    f.contentWindow.postMessage('PING', 'https://ui.perfetto.dev');
    if (tries > 60) { clearInterval(timer); say('the viewer did not answer'); }
  }, 500);
})();
</script>
"""))


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
# Reading a ModelBlaster lowering (Unit 2).
#
# Every one of these used to be an ad-hoc block in a notebook cell -- a regex over
# weights.c, a scrape of the lowering script's stdout, three separate copies of "find a
# line and print a numbered window".  The parsing is not the lesson; what the files
# CONTAIN is the lesson.  So the parsing lives here under a name that says what it
# answers, and a cell asks the question in one line.
# --------------------------------------------------------------------------------------
class Lowering:
    """One lowered model on disk -- what a lowering run left behind.

    `ir/` holds the graph and the quantised arrays, `gen/` holds the C the board
    compiles, and they always travel together, so a notebook names the run once and
    reads from it by attribute instead of rebuilding paths in every cell.
    """

    def __init__(self, gen_dir: str | Path):
        self.gen = Path(gen_dir)
        self.root = self.gen.parent
        self.ir = self.root / "ir"
        self._cache: dict = {}

    @classmethod
    def from_run(cls, result) -> "Lowering":
        """Locate the lowering a script just produced, from the script's own output.

        The script prints its output directory as the last word of a `gen ...` line.
        Reading it back is what lets a cell pass `--name` freely: a hardcoded path is
        wrong the moment somebody changes the name, and wrong by silently reading the
        PREVIOUS run, which still parses.
        """
        for line in reversed(result.stdout.splitlines()):
            if line.strip().startswith("gen "):
                return cls(line.split()[-1])
        raise LookupError(
            "that run printed no 'gen <path>' line, so it produced no lowering -- "
            "re-run the cell with quiet=False to see why")

    def __repr__(self) -> str:
        return f"Lowering({self.root.name})"

    @property
    def graph(self) -> dict:
        """`ir/graph.json` -- operators, tensors, and which of them reach a kernel."""
        if "graph" not in self._cache:
            self._cache["graph"] = json.loads((self.ir / "graph.json").read_text())
        return self._cache["graph"]

    @property
    def picks(self) -> dict:
        """`gen/kernel_picks.json` -- the algorithm chosen for each operator kind."""
        if "picks" not in self._cache:
            self._cache["picks"] = json.loads(
                (self.gen / "kernel_picks.json").read_text())["picks"]
        return self._cache["picks"]

    @property
    def weights(self):
        """`ir/weights.npz` -- the quantised arrays, including the requantise grid."""
        if "weights" not in self._cache:
            import numpy as np
            self._cache["weights"] = np.load(self.ir / "weights.npz")
        return self._cache["weights"]

    def md5(self, rel: str) -> str:
        """The digest of one file in this lowering, named relative to the run root."""
        import hashlib
        return hashlib.md5((self.root / rel).read_bytes()).hexdigest()

    def line_count(self, rel: str) -> int:
        """How many lines one generated file has."""
        return len((self.root / rel).read_text().splitlines())


def show_source(path: str | Path, around: str, lines: int = 16, before: int = 0) -> None:
    """Print a numbered window of a source file, anchored on the first line containing `around`.

    Anchored rather than a fixed line range because these files are GENERATED: a
    different calibration or backend moves every line number, and a window pinned to
    numbers quietly shows the wrong code rather than failing.
    """
    text = Path(path).read_text().splitlines()
    hit = next((i for i, l in enumerate(text) if around in l), None)
    if hit is None:
        raise LookupError(f"{Path(path).name} has no line containing {around!r}")
    start = max(hit - before, 0)
    for i, line in enumerate(text[start:start + lines], start=start + 1):
        print(f"{i:>4}  {line}")


def show_graph(low: Lowering) -> None:
    """The whole model as the compiler sees it: operators, shapes, and its digest."""
    g = low.graph
    print(f'{g["name"]}  {g["quant"]}  {len(g["ops"])} operators, '
          f'{len(g["dispatches"])} of them dispatched to a kernel')
    print(f'input  {g["input"]["tensor"]:<8} {g["tensors"][g["input"]["tensor"]]["shape"]}')
    print(f'output {g["output"]["tensor"]:<8} {g["tensors"][g["output"]["tensor"]]["shape"]}')
    print()
    for n in g["ops"]:
        shape = " ".join(f"{k}={v}" for k, v in n.get("shape", {}).items())
        print(f'  {n["name"]:<9} {n["op"]:<14} {n["inputs"][0]:>8} -> {n["outputs"][0]:<9} {shape}')
    print()
    print("graph.json md5", low.md5("ir/graph.json"))


def show_kernel_choices(low: Lowering) -> None:
    """Which algorithm each operator kind was lowered to, and how much work it carries."""
    g, picks = low.graph, low.picks
    for op in sorted(picks):
        n = sum(1 for o in g["ops"] if o["op"] == op)
        print(f'{op:<14} {picks[op]["source"]:<18} {picks[op]["algorithm"]}')
        print(f'{"":<14} serves {n} of the {len(g["ops"])} operators')
        print(f'{"":<14} {picks[op]["path"]}')
    mac = sum(o["shape"]["OH"] * o["shape"]["OW"] * o["shape"]["OC"] * o["shape"]["IC"]
              * o["shape"]["KH"] * o["shape"]["KW"]
              for o in g["ops"] if o["op"] == "conv2d_s8_pc")
    print(f'\nconv2d_s8_pc carries {mac:,} multiply-accumulates -- every one in the graph.')


def requant_arrays(low: Lowering) -> list[tuple[str, int, int, int]]:
    """Find the per-channel requantise arrays in `gen/weights.c`.

    Returns (name, entries, first line, last line) for each, 1-based, so a caller can
    both count them and print one.
    """
    import re

    w = (low.gen / "weights.c").read_text().splitlines()
    found, i = [], 0
    while i < len(w):
        m = re.search(r"(\w+_output_(?:multiplier|shift)_per_oc_\w+)\[(\d+)\]", w[i])
        if not m:
            i += 1
            continue
        j = i
        while "};" not in w[j]:
            j += 1
        found.append((m.group(1), int(m.group(2)), i + 1, j + 1))
        i = j + 1
    return found


def show_requant_arrays(low: Lowering) -> None:
    """How much of the generated weights file is the requantise grid, and the first one."""
    w = (low.gen / "weights.c").read_text().splitlines()
    arrays = requant_arrays(low)
    total = sum(b - a + 1 for _, _, a, b in arrays)
    values = sum(n for _, n, _, _ in arrays)
    print(f"weights.c is {len(w):,} lines. {total} of them are the requantise grid: "
          f"{len(arrays)} arrays, {values} int32 values.\n")
    for name, n, a, b in arrays:
        print(f"  line {a:>5}  {name:<52} [{n}]")
    print()
    print("\n".join(w[arrays[0][2] - 1:arrays[1][3]]))


def show_requant_channels(low: Lowering, op: str = "conv1", channels: int = 4) -> None:
    """The float-to-int arithmetic for one operator, channel by channel.

    Shows what the integer multiplier and shift actually encode: a per-channel weight
    scale, which is why the stored weights can all saturate the int8 range.
    """
    import numpy as np

    g, z = low.graph, low.weights
    mult, shift = z[f"{op}.output_multiplier_per_oc"], z[f"{op}.output_shift_per_oc"]
    node = next(n for n in g["ops"] if n["name"] == op)
    s_in = g["tensors"][node["inputs"][0]]["quant"]["scale"]
    s_out = g["tensors"][node["outputs"][0]]["quant"]["scale"]
    print(f"{op}   input scale {s_in:.12f} (= 1/{1 / s_in:.0f})   "
          f"output scale {s_out:.12f}\n")
    print("  oc   multiplier  shift          M   implied weight scale   max |W| that fits")
    for oc in range(channels):
        M = float(mult[oc]) / 2**31 / 2**int(shift[oc])
        s_w = M * s_out / s_in
        print(f"  {oc:>2}   {mult[oc]:>10}  {shift[oc]:>5}   {M:.8f}         {s_w:.8f}   "
              f"{s_w * 127:.6f}")
    print(f"\n  ... and {len(mult) - channels} more channels. Every channel saturates at "
          f"{int(np.abs(z[f'{op}.weight_q']).max())}, which is what per-channel means: the "
          f"multiplier absorbs the range, not the weights.")


def compare_calibration(few: Lowering, many: Lowering,
                        few_label: str = "64 frames", many_label: str = "256 frames") -> None:
    """What changes between two calibration runs of the same model, and what does not."""
    print(f"graph.json md5  {few_label:>10}", few.md5("ir/graph.json"))
    print(f"                {many_label:>10}", many.md5("ir/graph.json"))
    a, b = few.weights, many.weights
    print(f"\n{'array':<34}{'entries':>9}{'differ':>9}")
    for k in a.files:
        if "output_" in k:
            print(f"{k:<34}{a[k].size:>9}{int((a[k] != b[k]).sum()):>9}")
    print("\nweight_q arrays identical:",
          all((a[k] == b[k]).all() for k in a.files if k.endswith("weight_q")))


def compare_backends(a: Lowering, b: Lowering,
                     a_label: str = "pext", b_label: str = "scalar") -> None:
    """The same model lowered for two backends: what the choice changes on disk."""
    pa, pb = a.picks, b.picks
    print()
    for op in sorted(pa):
        print(f'{op:<14} {a_label:<7} {pa[op]["source"]:<18} {pa[op]["algorithm"]}')
        print(f'{"":<14} {b_label:<7} {pb[op]["source"]:<18} {pb[op]["algorithm"]}')
    print()
    for f in ("ir/graph.json", "gen/weights.c", "gen/kernels.c"):
        x, y = a.md5(f), b.md5(f)
        print(f'{f:<16} {"same" if x == y else "differs"}   {x}  {y}')
    print(f'\nkernels.c   {a.line_count("gen/kernels.c"):>5} lines on {a_label}, '
          f'{b.line_count("gen/kernels.c"):>4} on {b_label}')


# --------------------------------------------------------------------------------------
# Reading an LLM kernel-optimization run (Unit 4).
#
# The run directory is the whole record: what the model was asked, what it answered, what
# each candidate scored on spike, and what the board measured.  These read it.
# --------------------------------------------------------------------------------------
MB_RUNS = Path.home() / "iiswc-tutorial" / "out" / "mb_lab"


def mb_preflight() -> bool:
    """What this seat needs to run the optimizer live, and which pieces it has.

    Four separate things, because they fail separately and the message that says
    "not provisioned" is useless when only one of them is missing.
    """
    checks = [
        ("the model key", Path.home() / ".config/iiswc/bedrock.env",
         "an instructor runs scripts/93_bedrock_key.sh distribute"),
        ("the dev environment", Path.home() / ".config/iiswc/dev.env",
         "an instructor runs scripts/96_seat_mb_setup.sh"),
        ("the spike that knows MBP", Path.home() / "mb-tools/bin/spike",
         "an instructor runs scripts/96_seat_mb_setup.sh"),
        ("the optimizer", Path.home() / "iiswc-tutorial/scripts/95_mb_kernel_llm.sh",
         "an instructor runs scripts/96_seat_mb_setup.sh"),
    ]
    ready = True
    for name, path, fix in checks:
        have = path.exists()
        ready &= have
        print(f"  {'yes' if have else 'NO ':<4} {name:<26} {path}")
        if not have:
            print(f"       {fix}")
    print()
    print("This seat can run the optimizer." if ready else
          "This seat cannot run the optimizer live. The cells below read a finished run\n"
          "instead, and every number in this unit came from one.")
    return ready


def mb_latest_run(name: str = "") -> Path | None:
    """The run directory to read: one you name, else the most recent on this seat."""
    if name:
        p = MB_RUNS / name
        return p if p.is_dir() else None
    if (MB_RUNS / "latest").is_dir():
        return (MB_RUNS / "latest").resolve()
    runs = sorted((p for p in MB_RUNS.glob("*") if p.is_dir() and p.name != "latest"),
                  key=lambda p: p.stat().st_mtime)
    return runs[-1] if runs else None


def show_search_shape(run: str | Path) -> None:
    """How big the search was: rounds, phases, calls, and what each call cost.

    Read from the run's own call log rather than from the flags, because the flags say
    what was ASKED for and the log says what happened -- a round that found no
    improvement stops early, and then the two disagree.
    """
    run = Path(run)
    log = next((p for p in (run / "after/calls.jsonl", run / "llm-calls.jsonl") if p.exists()), None)
    if log is None:
        print(f"no call log under {run} -- this run did not call the model")
        return
    rows = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    print(f"{len(rows)} calls to {rows[0].get('model', '?')}\n")
    print(f"  {'call':>4}  {'round':>5}  {'phase':<26}{'in':>8}{'out':>7}{'s':>7}")
    for i, r in enumerate(rows, 1):
        print(f"  {i:>4}  {r.get('round', '?'):>5}  {str(r.get('phase', '')):<26}"
              f"{r.get('input_tokens') or 0:>8,}{r.get('output_tokens') or 0:>7,}"
              f"{r.get('latency_s') or 0:>7.1f}")
    ti = sum(r.get("input_tokens") or 0 for r in rows)
    to = sum(r.get("output_tokens") or 0 for r in rows)
    print(f"\n  {'total':>4}{'':>9}{'':<26}{ti:>8,}{to:>7,}"
          f"{sum(r.get('latency_s') or 0 for r in rows):>7.0f}")
    errs = [r.get("error") for r in rows if r.get("error")]
    print(f"  errors: {errs if errs else 'none'}")


def show_board_feedback(run: str | Path) -> None:
    """What the board told the model between rounds -- the hardware in the loop.

    Spike scores every candidate and has no memory timing, so this file is the only
    thing in the loop that knows what the silicon actually did.
    """
    run = Path(run)
    fb = next((p for p in (run / "after/board_feedback.md", run / "fpga-feedback.md")
               if p.exists()), None)
    if fb is None:
        print(f"no board feedback under {run} -- this run was scored on spike alone")
        return
    print(fb.read_text().rstrip())


def show_llm_kernel(run: str | Path, around: str = "", lines: int = 22) -> None:
    """The kernel the model wrote, as it was compiled.

    Anchored on a line you name so the window lands on the loop rather than the
    licence header; with no anchor it shows the top of the function.
    """
    run = Path(run)
    cands = sorted(run.glob("after/round*.kernel.c")) or sorted(run.glob("**/cache/*.c"))
    if not cands:
        print(f"no kernel source under {run}")
        return
    src = cands[-1]
    print(f"{src}\n")
    show_source(src, around or "for (", lines=lines)

# --------------------------------------------------------------------------------------
# Reading a recorded board run (Unit 2).
# --------------------------------------------------------------------------------------
def show_board_provenance(board: dict, arm: str = "pext") -> None:
    """Where these cycle counts came from, and the console one arm printed."""
    print(f'{board["script"]}, {board["measured"]} on {board["measured_on"]},')
    print(f'bitstream {board["soc_magic"]} at {board["clk_hz"] // 10**6} MHz, '
          f'median of {board["iters"]} inferences.\n')
    for line in board["arms"][arm]["console"]:
        print(line[:200] + " ..." if len(line) > 200 else line)


def show_board_arms(board: dict, fast: str = "pext", slow: str = "scalar") -> None:
    """Per-operator cycles for two backends side by side, with each arm's own gate."""
    p, s = board["arms"][fast], board["arms"][slow]
    print(f'{"":<9}{"":<15}{"MBP kernels":>14}{"reference C":>15}{"factor":>9}')
    for a, b in zip(p["ops"], s["ops"]):
        print(f'{a["name"]:<9}{a["op"]:<15}{a["cycles"]:>14,}{b["cycles"]:>15,}'
              f'{b["cycles"] / a["cycles"]:>8.2f}x')
    print(f'{"frame":<24}{p["cycles"]["median"]:>14,}{s["cycles"]["median"]:>15,}'
          f'{s["cycles"]["median"] / p["cycles"]["median"]:>8.2f}x')
    print(f'{"ms at 40 MHz":<24}{p["ms_at_clk"]:>14,.2f}{s["ms_at_clk"]:>15,.2f}')
    print()
    for arm in (p, s):
        print(f'{arm["run_name"]:<14} custom-0 instructions in the image '
              f'{arm["custom0_instructions_in_elf"]:>3}   '
              f'output vs golden: {arm["gate"]["board_vs_golden_bytes_differ"]} of 192 bytes '
              f'differ, max |d| = {arm["gate"]["max_abs_err"]}')


def show_board_verdict(run: str | Path) -> None:
    """The three board arms of an optimizer run, and each arm's correctness gate.

    Three images, one bitstream, same data: the reference kernel, the new kernel, and the
    new kernel with the MBP instruction replaced by a C model of it.
    """
    run = Path(run)
    jf = run / "board.json"
    if not jf.exists():
        print(f"no board.json under {run} -- this run was scored on spike alone")
        return
    b = json.loads(jf.read_text())
    arms = b.get("arms", b)
    print(f"{'arm':<10}{'cycles':>14}{'per output':>13}{'vs reference':>14}   correctness")
    ref = None
    for name in ("before", "after", "mbpoff"):
        a = arms.get(name)
        if not isinstance(a, dict):
            continue
        cyc = a.get("cycles", {}).get("median") if isinstance(a.get("cycles"), dict) else a.get("cycles")
        per = a.get("cycles_per_output")
        if cyc is None:
            continue
        if ref is None:
            ref = cyc
        gate = a.get("gate", {})
        ok = gate.get("board_vs_golden_bytes_differ")
        note = ("bit-exact" if ok == 0 else f"{ok} bytes differ") if ok is not None else "-"
        print(f"{name:<10}{cyc:>14,}{(per if per else cyc):>13.1f}"
              f"{ref / cyc:>13.2f}x   {note}")


def show_model_shapes(asset: str = "moonshine_shape.json") -> None:
    """A one-shot detector and an autoregressive model, in the terms the compiler sees.

    The contrast the numbers carry: the same compiler, two very different shapes of
    work per unit of weight read.
    """
    path = Path(asset)
    if not path.exists():
        path = ASSETS / asset
    m = json.loads(path.read_text())
    d, k = m["signdet"], m["moonshine"]
    print(f'{d["name"]} runs {d["runs"]}.')
    print(f'  {d["dispatches_per_run"]} dispatches, {d["macs_per_run"]:,} multiply-accumulates,')
    print(f'  {d["cycles_median"]:,} cycles = {d["ms_at_clock"]:.0f} ms at {m["clock_mhz"]} MHz,')
    print(f'  {d["weight_bytes"]:,} bytes of weights.')
    print()
    print(f'{k["name"]} runs {k["runs"]}.')
    print(f'  encoder   {k["encoder_dispatches"]:>4} dispatches, every matrix multiply {k["encoder_gemm_rows"]} rows tall')
    print(f'  prologue  {k["prologue_dispatches"]:>4} dispatches, also {k["encoder_gemm_rows"]} rows tall')
    print(f'  decoder   {k["decoder_dispatches_later_step"]:>4} dispatches per step, every matrix multiply'
          f' {k["decoder_gemm_rows"]} row tall')
    print(f'            {k["decoder_dispatches_first_step"]} at the first step: there is no cache to append to yet')
    print()
    print(f'  {k["macs_per_token"]:,} multiply-accumulates per token against'
          f' {k["weight_bytes_per_token"]:,} weight bytes')
    print(f'  = {k["mac_per_weight_byte"]} multiply-accumulates per byte of weight read.')
    print(f'  The encoder does {k["encoder_gemm_rows"]} rows of arithmetic on the same weights;'
          f' the decoder does one.')
    print()
    print(f'  Self-attention reads {k["self_attention_keys_first_step"]} key at the first step and'
          f' {k["self_attention_keys_last_step"]} at the last.')
    print(f'  The model stops when it emits end-of-sequence, on average after'
          f' {k["steps_mean_measured"]:.2f} of the {k["steps_unrolled"]} steps that are compiled in.')
    per_utt = (k["encoder_dispatches"] + k["prologue_dispatches"]
               + k["decoder_dispatches_first_step"]
               + (k["steps_mean_measured"] - 1) * k["decoder_dispatches_later_step"])
    print()
    print(f'  About {per_utt:,.0f} dispatches for one utterance, against'
          f' {d["dispatches_per_run"]} for one frame of the detector.')


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


def schedule_comparison_figure(golden: dict):
    """The schedule result, drawn from the committed golden -- no solve, no artifacts.

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


def lane_timeline_figure(lanes: dict):
    """The two harts, drawn from the measured lane table (`assets/lane_timeline.json`).

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


def kernel_speedup_figure(board: dict):
    """SignDetLite's frame, measured on silicon on both backends.

    Drawn from `assets/signdet_board_cycles.json`, which is two `scripts/86_signdet_board.sh`
    records: the same graph and the same weights with the curated MBP kernels bound, and
    again with ModelBlaster's reference C bound.

    Two panels, because the two questions have different units. The top one is the frame,
    to scale, and the pext bar is a seventeenth of the scalar one. The bottom one is each
    dispatch's share of its own frame, which is the only way to compare a 2,117-cycle
    permute with a 50,937,793-cycle convolution in the same picture.
    """
    import matplotlib.pyplot as plt

    pext, scal = board["arms"]["pext"], board["arms"]["scalar"]
    names = [o["name"] for o in pext["ops"]]
    fig, (a, b) = plt.subplots(2, 1, figsize=(9.6, 6.4),
                               gridspec_kw={"height_ratios": [0.62, 1.0]})

    # -- panel A: the whole frame, to scale -------------------------------------------
    for i, (arm, colour) in enumerate(((scal, S2), (pext, S1))):
        ms = arm["ms_at_clk"]
        a.barh(i, ms, height=0.5, color=colour, zorder=3)
        a.annotate(f"{ms:,.2f} ms   {arm['cycles']['median']:,} cycles   "
                   f"{arm['cyc_per_mac']:.4f} cycles/MAC",
                   (ms, i), textcoords="offset points", xytext=(8, 0),
                   va="center", fontsize=8.5, color=INK)
    factor = scal["cycles"]["median"] / pext["cycles"]["median"]
    a.annotate(f"{factor:.2f}x", (scal["ms_at_clk"] * 0.42, 0.5),
               ha="center", va="center", fontsize=13, color=INK, weight="bold")
    a.set_yticks([0, 1])
    a.set_yticklabels(["reference C", "curated MBP\nkernels"], fontsize=8.5)
    a.set_ylim(-0.6, 1.6)
    a.set_xlim(0, scal["ms_at_clk"] * 1.75)
    a.set_xlabel(f"milliseconds for one 64x64 frame at {board['clk_hz'] / 1e6:.0f} MHz, "
                 f"median of {board['iters']}", fontsize=8.5, color=INK2)
    a.set_title("End to end, and both arms returned byte-identical output",
                fontsize=10, color=INK, loc="left", pad=8)
    _axes_style(a)

    # -- panel B: each dispatch's share of its own frame -------------------------------
    ys = list(range(len(names)))[::-1]
    h = 0.36
    for arm, colour, label, off in ((pext, S1, "curated MBP kernels", +h / 2),
                                    (scal, S2, "reference C", -h / 2)):
        b.barh([y + off for y in ys], [o["pct"] for o in arm["ops"]], height=h,
               color=colour, zorder=3, label=label)
    for y, p, s in zip(ys, pext["ops"], scal["ops"]):
        b.annotate(f"{s['cycles'] / p['cycles']:.2f}x", (36.5, y), ha="right",
                   va="center", fontsize=8, color=INK)
        b.annotate(f"{p['cycles']:>11,}   {s['cycles']:>12,}", (38.5, y), ha="left",
                   va="center", fontsize=7.5, color=INK2, family="monospace")
    b.annotate("factor", (36.5, len(names) - 0.4), ha="right", va="center",
               fontsize=7.5, color=INK2)
    b.annotate("  MBP cycles    reference C", (38.5, len(names) - 0.4), ha="left",
               va="center", fontsize=7.5, color=INK2, family="monospace")
    b.set_yticks(ys)
    b.set_yticklabels([f"{o['name']}  ({o['op']})" for o in pext["ops"]], fontsize=8.5)
    b.set_xlim(0, 62)
    b.set_xticks([0, 10, 20, 30])
    b.set_ylim(-0.7, len(names) - 0.1)
    b.set_xlabel("% of that arm's own frame", fontsize=8.5, color=INK2)
    b.set_title("By layer, as a share of each arm's own frame",
                fontsize=10, color=INK, loc="left", pad=18)
    b.legend(frameon=False, fontsize=8, loc="upper left",
             bbox_to_anchor=(0.0, 1.10), ncols=2)
    _axes_style(b)
    fig.tight_layout()
    return fig
