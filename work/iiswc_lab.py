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

import gzip
import json
import os
import shlex
import socket
import subprocess
import threading
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
    progressed = False      # whether a `... N lines` progress line needs clearing
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
                    progressed = True
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

    # Everything the live loop above did not print, because it stops at `head`.
    #
    # The condition here used to be `len(chunks) > head + tail`, and that silently DROPPED
    # every line after the first `head` of any output shorter than head + tail: a 16-line
    # result printed 12 lines, no notice, and the other 4 reachable only through
    # `Result.stdout`. Measured on a tutorial seat, 2026-09-26, on the Kconfig cell in 1.6.
    rest = [] if full else chunks[shown:]
    if rest and not quiet:
        elided = max(len(rest) - tail, 0)
        if progressed:
            sys.stdout.write("\r" + " " * 48 + "\r")
        if elided:
            warn = sum(1 for ln in chunks if "warning:" in ln)
            err = sum(1 for ln in chunks if "error:" in ln)
            counts = f"  ({warn} warning lines, {err} error lines)" if warn or err else ""
            print(f"    ... {elided} lines elided{counts}")
            if log:
                print(f"    full output: {log}")
        print("".join(rest[-tail:]), end="")

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
# Putting a RE-TUNED guest on the card without sending the guest.
#
# The speech guest is 63.76 MiB because every weight is inside it. Thirty seats pushing one
# each is ~1,912 MiB, about eight minutes of the room's whole channel at the 4.021 MiB/s the
# fleet measured -- so a fleet-wide guest update is not something a tutorial can spend.
#
# But two builds of the same sample are 99.6 % the same bytes (L413), and the difference is
# the code that changed plus the pointer tables holding its addresses. So the cell sends the
# difference: a ~257 KB patch that the card rebuilds the image from and verifies before
# anything boots it. One named call, because every step of it is a place to get a digest
# wrong, and a cell that gets a digest wrong looks exactly like a cell that worked.
# --------------------------------------------------------------------------------------
def board_guest_delta(base_image: str | Path, new_image: str | Path, base: str = "staged",
                      patch_name: str = "guest.zpatch", work: str | Path | None = None,
                      verbose: bool = True) -> dict:
    """How few bytes it takes to put a re-tuned guest on your card -- and the md5 that proves
    the card rebuilt exactly the image you built here.

    `base_image` is your local copy of the guest the card is already holding, `new_image` is
    the one you just built, and `base` names which pre-staged guest on the card to rebuild
    from (`speech`, `signdet`, `boot_info`, `cam_snap`, or `staged` for one you pushed with
    `lab.board_put(..., "base.bin")`).

    It REFUSES before building anything if the card's base is not byte-identical to yours.
    That refusal is the whole safety property: a delta against the wrong base reconstructs
    nothing, while looking entirely healthy right up to the digest that catches it. There is
    no flag to skip it.

    Returns a dict of the measured numbers -- `patch_bytes`, `push_s`, `apply_s`,
    `result_md5`, `identical` -- so the cell after it can assert on them instead of on
    scrollback.
    """
    base_image, new_image = Path(base_image), Path(new_image)
    out: dict = {"base": base, "identical": False}

    tool = repo_file("fpga/pynq-z2/host/guest_delta.py")
    if tool is None:
        print("guest_delta.py is not in any checkout on this instance, so no delta can be "
              "built here. Nothing was sent. (fpga/pynq-z2/host/guest_delta.py)")
        return out
    for p in (base_image, new_image):
        if not p.is_file():
            print(f"{p} is not a file, so there is nothing to diff. Nothing was sent.")
            return out

    link = _board_link()
    if link is None:
        print("STUB: board_link.py is not on this instance. No delta was built and nothing "
              "was sent to any card.")
        return out
    if not link.probe()["connected"]:
        print("board offline. No delta was built and nothing was sent.")
        return out

    # (1) WHAT THE CARD IS ACTUALLY HOLDING.  Asked, not assumed -- and asked BEFORE the four
    # seconds of diffing, so a mismatch costs a round trip instead of a patch nobody can use.
    try:
        on_card = link.base_md5(base)
    except Exception as exc:                       # noqa: BLE001 - a status line, not a trace
        print(f"could not ask the card what it holds: {type(exc).__name__}: {exc}")
        return out
    mine = _md5_file(base_image)
    out["card_base_md5"], out["local_base_md5"] = on_card, mine
    if on_card is None:
        print(f"this card carries no '{base}' base, so there is nothing to rebuild from.\n"
              f"  Push one first:  lab.board_put({base_image.name!r}, 'base.bin')  "
              f"then use base='staged'.")
        return out
    if on_card != mine:
        print(f"REFUSING to build a delta.\n"
              f"  the card's '{base}' base is md5 {on_card}\n"
              f"  {base_image.name} here is md5 {mine}\n"
              f"  A patch only reconstructs the image it was computed against. Point\n"
              f"  base_image at what the card has, or push the new guest whole.")
        return out

    # (2) THE PATCH.  --card-base-md5 makes the tool refuse the same mismatch independently;
    # the check above is so the message is a sentence rather than a traceback.
    work = Path(work) if work else (new_image.parent / "delta")
    work.mkdir(parents=True, exist_ok=True)
    patch = work / patch_name
    t0 = time.time()
    made = sh(f"{shlex.quote(sys.executable)} {shlex.quote(str(tool))} make "
              f"{shlex.quote(str(base_image))} {shlex.quote(str(new_image))} "
              f"-o {shlex.quote(str(patch))} "
              f"--manifest {shlex.quote(str(patch.with_suffix('.json')))} "
              f"--card-base-md5 {mine} --json", quiet=True)
    out["make_s"] = round(time.time() - t0, 2)
    if made.returncode != 0 or not patch.is_file():
        # `sh()` merges stderr into stdout, so the tool's refusal is in there and there is no
        # `.stderr` to read -- an earlier draft of this helper reached for one and would have
        # turned a clear refusal into an AttributeError two frames down.
        print(f"the delta could not be built:\n{made.stdout.strip()}")
        return out
    out["manifest"] = {}
    for line in reversed(made.stdout.strip().splitlines()):
        if line.startswith("{"):
            try:
                out["manifest"] = json.loads(line)
            except ValueError:
                pass
            break
    out["patch_bytes"] = patch.stat().st_size
    out["image_bytes"] = new_image.stat().st_size
    out["result_md5"] = _md5_file(new_image)
    out["shrink"] = round(out["image_bytes"] / max(out["patch_bytes"], 1), 1)

    # (3) THE PUSH, timed -- because "how much of the room's channel did this cost" is the
    # question the whole mechanism exists to answer.
    t0 = time.time()
    try:
        out["put"] = link.put_file(str(patch), patch_name)
    except Exception as exc:                       # noqa: BLE001
        print(f"the card refused the patch upload: {type(exc).__name__}: {exc}")
        return out
    out["push_s"] = round(time.time() - t0, 2)

    # (4) THE REBUILD.  The card verifies the base, rebuilds, verifies the result, and only
    # then lets it become zephyr.bin. A failure here leaves the guest the card had.
    t0 = time.time()
    try:
        reply = link.patch(patch_name, base, out["result_md5"])
    except Exception as exc:                       # noqa: BLE001
        print(f"the card did not install the rebuild: {type(exc).__name__}: {exc}\n"
              f"  Its previous guest is untouched. `lab.board('get', 'patch.log')` says why.")
        out["apply_s"] = round(time.time() - t0, 2)
        return out
    out["apply_s"] = round(time.time() - t0, 2)
    out["reply"] = reply
    out["identical"] = reply.get("md5") == out["result_md5"]

    if verbose:
        print(f"base on card   {on_card}  ({base})")
        print(f"image          {out['image_bytes']:,} B   {out['result_md5']}")
        print(f"patch          {out['patch_bytes']:,} B   {out['shrink']}x smaller, "
              f"built in {out['make_s']}s")
        print(f"pushed         {out['push_s']}s")
        print(f"rebuilt        {out['apply_s']}s on the card "
              f"({reply.get('apply_s', '?')}s of it inside the applier)")
        print(f"card's md5     {reply.get('md5')}")
        print("VERIFIED: the card's guest is byte-identical to the image built here"
              if out["identical"] else
              "NOT IDENTICAL -- the card refused to install it, and said so above")
    return out


def _md5_file(path: str | Path, chunk: int = 1 << 20) -> str:
    """The md5 of a file, read in chunks so a 64 MB image never lands in RAM whole."""
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------------------------------
# Reading a guest's console.
#
# Every Zephyr guest in this notebook reports through tagged console lines -- `CAM_FRAME`,
# `DUO_TRACE_HART`, `MB_PEXT_OP`. Three cells used to repeat the same two steps: decode the
# bytes the card returned, then keep the lines whose tag matters. The tags are the lesson,
# so they stay in the cell; the decoding and the filtering do not, so they live here.
# --------------------------------------------------------------------------------------
def console_text(result: BoardResult) -> str:
    """The guest's console as text, from `board("get", "console.out", binary=True)`."""
    return result.stdout.decode("utf-8", "replace") if result.stdout else ""


def show_console_lines(console: str, *tags: str) -> None:
    """Print the console lines carrying one of these tags, in the order the guest printed them."""
    for line in console.splitlines():
        if line.startswith(tags):
            print(line)


def console_field(console: str, tag: str, field: str, cast=str):
    """One `field=value` off the first console line carrying `tag`.

    A guest prints its numbers as `key=value` pairs on a tagged line, so a cell that wants
    one of them wants this and not a regular expression. Raises if the line or the field is
    absent, because the silent alternative is a number quietly read off the wrong line.
    """
    line = next((l for l in console.splitlines() if l.startswith(tag)), None)
    if line is None:
        raise LookupError(f"this console has no {tag!r} line")
    for word in line.split():
        if word.startswith(f"{field}="):
            return cast(word.split("=", 1)[1])
    raise LookupError(f"the {tag!r} line has no {field}= field: {line}")


# --------------------------------------------------------------------------------------
# Camera frame validation and display.
# --------------------------------------------------------------------------------------
def camera_source(rel: str = "") -> Path:
    """Resolve camera sources shipped beside this helper, never from another checkout."""
    root = Path(__file__).resolve().parent / ".backend/ospi-camera"
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Camera source path must stay inside the shipped backend")
    if not path.exists():
        raise FileNotFoundError(f"Camera source is missing from the seat content: {path}")
    return path


def camera_build() -> Result:
    """Build the shipped camera guest using the seat's preinstalled Zephyr environment."""
    app = camera_source("samples/cam_capture")
    root = camera_source()
    env = Path.home() / "tut/env.sh"
    if not env.is_file():
        raise FileNotFoundError(f"The seat's Zephyr environment is missing: {env}")
    command = (
        f"cd {shlex.quote(str(env.parent))} && source {shlex.quote(str(env))} && "
        "west build -p always -b chipyard_pynqz1_all_f40 "
        f"-d ~/out/cam_capture {shlex.quote(str(app))} "
        f"-- -DBOARD_ROOT={shlex.quote(str(root))} -DCAM_MCLKDIV=2")
    return sh(command, timeout=900)


def show_camera_driver_config() -> None:
    """Show the camera Kconfig entry from the same module used by camera_build()."""
    source = camera_source("modules/ospi_camera/Kconfig").read_text()
    entry = source[source.index("config OSPI_HM01B0\n"):].split("\n\n", 1)[0]
    print("---\n" + entry)


def camera_frame_metadata(console: str) -> dict:
    """Require an unambiguous, complete frame before asking the board to read it."""
    records = [line for line in console.splitlines() if line.startswith("CAM_FRAME ")]
    if len(records) != 1:
        raise ValueError(f"Expected one CAM_FRAME record, found {len(records)}")
    fields = dict(part.split("=", 1) for part in records[0].split()[1:] if "=" in part)
    required = ("ok", "rc", "addr", "phys", "width", "height", "bytes", "sum", "saweof")
    try:
        meta = {key: int(fields[key], 0) for key in required}
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Invalid CAM_FRAME record: {records[0]}") from exc
    if meta["ok"] != 1 or meta["rc"] != 0 or meta["saweof"] != 1:
        raise ValueError("Camera did not report a complete successful frame")
    width, height, count = meta["width"], meta["height"], meta["bytes"]
    if not (4 <= width <= 511 and 2 <= height <= 511 and width % 2 == 0 and height % 2 == 0
            and count == width * height and 0 < count < 256 * 1024):
        raise ValueError("Invalid camera geometry or byte count")
    if not (0x80000000 <= meta["addr"] <= 0x90000000 - count and meta["addr"] % 8 == 0):
        raise ValueError("Camera buffer is outside aligned Rocket DRAM")
    if meta["phys"] != 0x10000000 + meta["addr"] - 0x80000000:
        raise ValueError("Camera's Rocket and ARM addresses disagree")
    if not 0 <= meta["sum"] <= count * 255:
        raise ValueError("Invalid camera checksum")
    # The camera backend fixes IMAGE_ORIENTATION to zero on this shield.
    meta["bayer"] = fields.get("bayer", "BGGR")
    meta["rotate"] = int(fields.get("rotate", "180"))
    if meta["bayer"] not in {"BGGR", "RGGB", "GBRG", "GRBG"} or meta["rotate"] not in {0, 180}:
        raise ValueError("Unsupported camera orientation or Bayer pattern")
    return meta


def camera_save_frame(raw: bytes, meta: dict, destination: Path | str = "frame.raw") -> Path:
    """Reject truncated/stale DRAM before writing or rendering a frame."""
    if not isinstance(raw, bytes) or len(raw) != meta["bytes"]:
        raise ValueError("Frame readback length differs from the guest")
    if sum(raw) != meta["sum"]:
        raise ValueError("Frame checksum differs from the guest; DRAM may be stale")
    path = Path(destination).resolve()
    path.write_bytes(raw)
    return path


def camera_render_frame(raw_path: Path, meta: dict) -> Path:
    """Render a colour-denoised photo with automatic Bayer order, WB, CCM and sRGB."""
    renderer = camera_source("modules/ospi_camera/host/frame-to-colour.py")
    command = [sys.executable, str(renderer), str(raw_path),
               str(meta["width"]), "1", "--ccm", "0.7", "--denoise"]
    if meta["rotate"] == 180:
        command.append("--rotate180")
    subprocess.run(command, check=True, timeout=60, capture_output=True)
    return raw_path.with_name(raw_path.stem + "-colour.png")


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



def fetch_drained_lanes(drained: BoardResult, dest: Path | None = None) -> list[Path]:
    """Bring each drained trace lane across from the card, and say what arrived.

    The card declares each lane's compressed length before sending it, and this prints that
    next to the bytes that actually landed, because a short read across the tunnel and a
    short trace look identical in a file listing.
    """
    d = json.loads(drained.stdout)
    print(f'{d["lanes"]} lanes, {d["bytes"]:,} bytes of trace, '
          f'{d["gz_bytes"]:,} bytes compressed')
    here = []
    for lane in d["drained"]:
        got = board("get", lane["name"], binary=True, verbose=False).stdout
        path = Path(dest or Path.cwd()) / lane["name"]
        path.write_bytes(got)
        here.append(path)
        print(f'  hart {lane["hart"]}: {lane["name"]}, {len(got):,} B '
              f'(the card declared {lane["gz_bytes"]:,})')
    return here


def _decoder() -> Path:
    """The TACIT decoder, or a message naming what to do about it.

    env.sh resolves TACIT_DECODER into the repository's third_party/ tree, which a seat does
    not have; /etc/profile.d/iiswc-mbtools.sh points it at ~/mb-tools instead. lab.sh() runs a
    login shell so a cell sees that, but a bare os.environ in this kernel may not, so both are
    consulted before giving up.
    """
    for cand in (os.environ.get("TACIT_DECODER", ""),
                 str(Path.home() / "mb-tools" / "bin" / "ltrace-decoder")):
        if cand and os.access(cand, os.X_OK):
            return Path(cand)
    raise FileNotFoundError(
        "no TACIT decoder on this instance. It is installed as "
        "~/mb-tools/bin/ltrace-decoder; ask an instructor if it is absent.")


#: Frames that mean "this hart is doing model work", and the ones that mean it is not.
#: Both lanes are matched against the same two sets so the two percentages are comparable;
#: a lane simply reports which of them it actually contains, in `match`.
MODEL_FRAMES = ("mb_pext_conv", "fe_log2_q8")
#: The scheduler's idle frame is the honest measure of "this hart had nothing to do".
#: arch_spin_relax alone misses almost all of it: on a measured capture arch_cpu_idle held
#: the stack for 93% of one lane's window while the spin frame accounted for 6%.
IDLE_FRAMES = ("arch_cpu_idle",)
TIMELINE_BUCKETS = 13


def _covered(evs: list, patterns: tuple) -> tuple[list, int]:
    """The intervals where at least one matching frame was ON THE STACK, and the last one's end.

    Counting events would answer a different question: a frame that is entered once and runs
    for a second is one event and a second of work. So this walks B/E in timestamp order and
    keeps a depth, which is what "on the stack" means, and merges nesting into one interval.
    """
    marks = []
    for e in evs:
        if not any(p in e["name"] for p in patterns):
            continue
        ts = e.get("ts", 0)
        if e.get("ph") == "X":
            marks.append((ts, +1)); marks.append((ts + e.get("dur", 0), -1))
        elif e.get("ph") == "B":
            marks.append((ts, +1))
        elif e.get("ph") == "E":
            marks.append((ts, -1))
    marks.sort()
    spans, depth, opened, last_end = [], 0, None, 0
    for ts, d in marks:
        was = depth
        depth += d
        if was == 0 and depth > 0:
            opened = ts
        elif was > 0 and depth <= 0:
            if opened is not None:
                spans.append((opened, ts)); last_end = ts
            opened, depth = None, 0
    return spans, last_end


def _minus(spans: list, other: list) -> list:
    """`spans` with every part that overlaps `other` removed.

    Needed because the frames NEST: the scheduler's idle frame is an ANCESTOR of the work
    done inside it, so counting "idle was on the stack" and "a model frame was on the stack"
    separately gave 16.9% model and 93.2% idle on one measured lane -- 110% of a window.
    Idle has to mean idle AND NOT working for the two numbers to be readable together.
    """
    out = []
    for a, b in spans:
        cur = [(a, b)]
        for c, d in other:
            nxt = []
            for x, y in cur:
                if d <= x or c >= y:
                    nxt.append((x, y)); continue
                if c > x:
                    nxt.append((x, min(c, y)))
                if d < y:
                    nxt.append((max(d, x), y))
            cur = nxt
            if not cur:
                break
        out.extend(cur)
    return out


def _pct_per_bucket(spans: list, window: int, n: int) -> list:
    """What fraction of each of n equal slices of the window those spans cover, as percentages."""
    if window <= 0 or not spans:
        return [0.0] * n
    edge = window / n
    out = []
    for i in range(n):
        lo, hi = i * edge, (i + 1) * edge
        got = sum(max(0.0, min(hi, b) - max(lo, a)) for a, b in spans)
        out.append(100.0 * got / edge)
    return out


def _summarise_timeline(merged: Path, trace_label: str) -> dict:
    """Reduce a merged Perfetto file to the per-lane table the timeline cells read.

    WHAT IS COUNTED AND WHY. A decode that exits zero can still be empty, and size tells you
    nothing: a lane that stopped mid-packet and a lane that ran the whole window produce files
    of similar length. The two fields that separate them are the number of DISTINCT function
    names on each lane and the name of its first event, so both are recorded here.

    The rest of the table is what 3.6 reads: how much of the one shared window each hart spent
    inside model frames, the same split into buckets so it can be drawn, and where each lane's
    model work stopped -- which is the comparison the unit exists to make.
    """
    events = json.loads(merged.read_text())
    events = events["traceEvents"] if isinstance(events, dict) else events
    per: dict = {}
    for e in events:
        if e.get("ph") not in ("B", "E", "X") or "name" not in e:
            continue
        per.setdefault(e.get("tid", e.get("pid")), []).append(e)

    lanes, ends = {}, {}
    for tid, evs in sorted(per.items()):
        evs.sort(key=lambda e: e.get("ts", 0))
        frames = [e for e in evs if e.get("ph") in ("B", "X")]
        names: dict = {}
        for e in frames:
            names[e["name"]] = names.get(e["name"], 0) + 1
        ts = [e.get("ts", 0) for e in evs]
        window = max(ts) if ts else 0
        model_spans, model_last = _covered(evs, MODEL_FRAMES)
        idle_spans = _minus(_covered(evs, IDLE_FRAMES)[0], model_spans)
        tot = lambda sp: sum(b - a for a, b in sp)
        lanes[str(tid)] = {
            "name": str(tid),
            "events": len(frames),
            "distinct": len(names),
            "ts_min": min(ts) if ts else 0,
            "ts_max": window,
            "first_ev": [frames[0]["name"], frames[0].get("ts", 0)] if frames else ["", 0],
            "last_ev": [frames[-1]["name"], frames[-1].get("ts", 0)] if frames else ["", 0],
            "top": [[n, c] for n, c in sorted(names.items(), key=lambda kv: -kv[1])[:5]],
            "match": {p: sum(c for n, c in names.items() if p in n)
                      for p in MODEL_FRAMES if any(p in n for n in names)},
            "model_time_pct": 100.0 * tot(model_spans) / window if window else 0.0,
            "idle_time_pct": 100.0 * tot(idle_spans) / window if window else 0.0,
            "model_pct_per_bucket": _pct_per_bucket(model_spans, window, TIMELINE_BUCKETS),
            "model_last": model_last,
        }
        ends[str(tid)] = model_last

    span = max((l["ts_max"] for l in lanes.values()), default=0)
    apart = (max(ends.values()) - min(ends.values())) if len(ends) > 1 else 0
    busy_ok = all(l["model_time_pct"] > 1.0 for l in lanes.values())
    together_ok = span > 0 and (100.0 * apart / span) < 5.0
    failures = []
    if not busy_ok:
        failures.append("a lane recorded almost no model work")
    if not together_ok:
        failures.append("the lanes' model work did not stop together")
    return {
        "trace": trace_label,
        "lanes": lanes,
        "ends_together_s": apart / 40e6,
        "ends_together_pct": (100.0 * apart / span) if span else 0.0,
        "gates": {"busy_ok": busy_ok, "ends_together_ok": together_ok, "failures": failures},
    }


DEMO_STEPS = ("speech", "speech_small", "signdet")
_demo_thread = None
_demo_result: dict = {}


def start_demo(step: str = "signdet") -> None:
    """Start a demo on the board and return at once, leaving it running.

    THE BOARD DOES NOT STOP WHEN THIS CELL DOES. The card loads the guest, releases the
    core and reads the console for a short while; when that read ends it stops READING and
    never resets the SoC. The guest keeps running, so the board goes on demonstrating until
    something else is loaded onto it -- another cell, or a restart.

    That is why this does not wait. A cell that blocked for the length of the demo made the
    attendee watch a progress bar instead of the board, and the interesting moment usually
    happened while they were looking at the wrong thing.
    """
    global _demo_thread
    if step not in DEMO_STEPS:
        raise ValueError(f"unknown demo {step!r}; choose one of {', '.join(DEMO_STEPS)}")
    if _demo_thread is not None and _demo_thread.is_alive():
        print("a demo is already starting on your board -- wait for it to report, then run this again")
        return

    def _go():
        try:
            r = board("run", step, timeout=300, verbose=False)
            _demo_result[step] = r
        except Exception as exc:                     # noqa: BLE001 -- reported, not raised
            _demo_result[step] = exc

    _demo_result.pop(step, None)
    _demo_thread = threading.Thread(target=_go, name=f"demo-{step}", daemon=True)
    _demo_thread.start()
    print(f"{step} is starting on your board. Watch the board, not this cell.")
    print("It keeps running until you load something else onto the card.")


def show_demo(step: str = "signdet", *tags: str) -> None:
    """What the board printed while it was starting, once the card has reported back.

    The demo is still running when this prints: these are the first seconds of console, kept
    so the start can be checked without watching the glass.
    """
    if _demo_thread is not None and _demo_thread.is_alive():
        print("still starting -- run this cell again in a few seconds")
        return
    r = _demo_result.get(step)
    if r is None:
        print(f"no start recorded for {step!r} in this kernel -- run lab.start_demo({step!r}) first")
        return
    if isinstance(r, Exception):
        print(f"the start failed: {type(r).__name__}: {r}")
        return
    if not r.ok:
        print(f"the card did not start it: {(r.note or str(r))[:200]}")
        return
    c = board("get", "console.out", binary=True, verbose=False)
    if not c.ok:
        print("the board is running, but its console could not be read back")
        return
    show_console_lines(console_text(c), *(tags or ("SD_BOOT", "SD_CAM", "SD_FRAME", "SD_RESULT")))


def decode_capture(lanes: list[Path] | list[str], elf: str | Path,
                   out: str = "lane_timeline.json", timeout: int = 1800) -> dict:
    """Decode the two drained lanes into one timeline of function calls.

    The encoders wrote compressed instruction deltas; only the decoder, holding the same
    binary the board ran, can turn those back into named calls. Pass the .elf that produced
    the capture: a decoder given a different build reports a mismatch rather than nonsense.
    """
    dec = _decoder()
    elf = Path(elf)
    if not elf.is_file():
        raise FileNotFoundError(f"no such binary: {elf}. Decode needs the .elf the board ran.")
    raw = []
    for lane in sorted(Path(p) for p in lanes):
        if lane.suffix == ".gz":
            plain = lane.with_suffix("")
            with gzip.open(lane, "rb") as fh:
                plain.write_bytes(fh.read())
            lane = plain
        raw.append(lane)
    if len(raw) != 2:
        raise ValueError(f"expected two lanes to decode, got {len(raw)}: {raw}")
    merged = raw[0].parent / "trace.merged.perfetto.json"
    labels = ("hart 0 (BIG, MBP) signdet_live", "hart 1 (LITTLE, scalar) kws_live")
    cmd = [str(dec), "--binary", str(elf), "--encoder", "rtl", "--to-perfetto"]
    for i, (lane, label) in enumerate(zip(raw, labels)):
        cmd += ["--trace", f"{lane}:{i}:{label}"]
    cmd += ["--merged-perfetto", str(merged)]
    print(f"decoding {sum(p.stat().st_size for p in raw):,} bytes with {dec.name} "
          f"-- this takes several minutes")
    t0 = time.time()
    # --to-txt is deliberately NOT passed: it writes gigabytes of per-instruction text and
    # roughly triples the wall clock, and nothing downstream of here reads it.
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        raise RuntimeError("decode failed:\n  " + "\n  ".join(tail))
    print(f"  decoded in {time.time() - t0:.0f}s -> {merged.name}")
    summary = _summarise_timeline(merged, str(merged))
    Path(out).write_text(json.dumps(summary, indent=2))
    return summary


def show_decode_check(summary: dict) -> None:
    """What a real decode looks like, next to what an empty one looks like.

    A decoder that finds no synchronisation point still exits zero and still writes a file.
    The counts below are what separate the two, so they are printed rather than assumed.
    """
    for tid, lane in sorted(summary["lanes"].items()):
        print(f"lane {tid}: {lane['events']:,} events, {lane['distinct']} distinct "
              f"function names")
        print(f"  first  {lane['first_ev'][0]}")
        busiest = ", ".join(f"{n} x{c:,}" for n, c in lane["top"][:3])
        print(f"  busiest  {busiest}")
    if any(l["distinct"] < 10 for l in summary["lanes"].values()):
        print("\nA lane with almost no distinct names did not decode. Ask an instructor.")


def lane_table(asset: str = "lane_timeline.json") -> dict:
    """The measured per-lane table from a decoded two-hart capture."""
    path = Path(asset)
    if not path.exists():
        path = ASSETS / asset
    return json.loads(path.read_text())


def show_lane_table(lanes: dict) -> None:
    """What each hart did with the one window: events, time in the model, time idle.

    Both lanes are bounded by one wall clock, so the two numbers to read together are
    each lane's share of the window and how far apart the two lanes stop.
    """
    for pid, lane in lanes["lanes"].items():
        print(f'pid {pid}  {lane["name"]}')
        print(f'    {lane["events"]:>7,} events, {lane["distinct"]} distinct frames')
        print(f'    in the model {lane["model_time_pct"]:5.1f}% of the window, '
              f'spin/console/idle {lane["idle_time_pct"]:5.1f}%')
    print(f'\nmodel work on the two lanes ends {lanes["ends_together_s"]:.2f} s apart '
          f'= {lanes["ends_together_pct"]:.1f}% of the window')
    gates = lanes["gates"]
    print("checks:", ", ".join(f"{k} {'pass' if v else 'FAIL'}"
                               for k, v in gates.items() if isinstance(v, bool)),
          f'-- {len(gates["failures"])} failures')


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
# Unit 4: an LLM kernel-optimization run.
#
# The optimizer is the lab in the repository on this instance (`mb`, which runs
# scripts/95_mb_kernel_llm.sh).  Its run directory is the whole record: what the model was
# asked, what it answered, what each candidate scored on spike, and what the board
# measured.  The helpers below start a run and read one; facts print as text and pictures
# are figures, in the same style as the rest of this notebook.
# --------------------------------------------------------------------------------------
MB_REPO = Path.home() / "iiswc-tutorial"
MB_RUNS = MB_REPO / "out" / "mb_lab"
MB = MB_REPO / "fpga/pynq-z2/host/mb"
MB_KERNELS = Path.home() / "work/modelblaster-llm-lab/your-kernel"   # where `mb start` copies to
MB_GOAL = 10.0                              # cycles per output to aim for in 4.9
_NO_ANSI = __import__("re").compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _jsonf(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def _blank(v) -> bool:
    """A value the attendee has not filled in yet (`...`, or a list containing it)."""
    return v is Ellipsis or (isinstance(v, (list, tuple)) and any(x is Ellipsis for x in v))


def _s8(x: int) -> int:
    x &= 0xFF
    return x - 256 if x > 127 else x


def _max8(a, b) -> list[int]:
    """MBP.MAX8 in Python: eight int8 lanes, the larger of each pair."""
    return [max(_s8(x), _s8(y)) for x, y in zip(a, b)]


def _lanes_figure(rows, marks: dict, title: str):
    """Rows of eight int8 lanes as boxes; marks: {(row, lane): colour} for the lanes to fill."""
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9.6, 0.52 * len(rows) + 0.7))
    for r, (label, vals) in enumerate(rows):
        y = len(rows) - 1 - r
        ax.annotate(label, (-0.25, y), ha="right", va="center", fontsize=8.5, color=INK)
        for i, v in enumerate(vals):
            fill = marks.get((r, i))
            ax.add_patch(plt.Rectangle((i + 0.04, y - 0.36), 0.92, 0.72, facecolor=fill or "#fcfcfb",
                                       edgecolor=fill or GRID, lw=0.8, zorder=2))
            ax.annotate(f"{v}", (i + 0.5, y), ha="center", va="center", fontsize=9,
                        color="white" if fill else INK, weight="bold" if fill else "normal",
                        family="monospace", zorder=3)
    for i in range(8):
        ax.annotate(f"lane {i}", (i + 0.5, len(rows) - 0.45), ha="center", va="bottom", fontsize=7, color=INK2)
    ax.set_xlim(-3.2, 8.1)
    ax.set_ylim(-0.6, len(rows) - 0.1)
    ax.axis("off")
    ax.set_title(title, fontsize=10, color=INK, loc="left", pad=6)
    plt.close(fig)
    return fig


def show_max8(seed: int | None = None):
    """How MBP.MAX8 does a 2x2 max pool: four outputs from two instructions, on random bytes."""
    import random
    rnd = random.Random(seed)
    r0 = [rnd.randint(-128, 127) for _ in range(8)]
    r1 = [rnd.randint(-128, 127) for _ in range(8)]
    v = _max8(r0, r1)
    shifted = v[1:] + [0]                           # (uint64_t)v >> 8: every lane moves down one
    m = _max8(v, shifted)
    outs = [m[0], m[2], m[4], m[6]]
    ref = [max(r0[2 * k], r0[2 * k + 1], r1[2 * k], r1[2 * k + 1]) for k in range(4)]
    print(f"outputs, lanes 0 2 4 6   {outs}")
    print(f"the reference kernel     {ref}   {'identical' if outs == ref else 'DIFFERENT'}")
    print("cost: 2 loads and 2 MAX8, where the reference kernel does 16 loads and 16 compares")
    rows = [("input row 2·oh", r0), ("input row 2·oh+1", r1), ("v = MAX8(row0, row1)", v),
            ("v >> 8", shifted), ("m = MAX8(v, v >> 8)", m)]
    return _lanes_figure(rows, {(4, i): S2 for i in (0, 2, 4, 6)},
                         "A 2×2 max pool with MBP.MAX8: the orange lanes are the four outputs")


def check_max8(row0, row1, prediction):
    """Your prediction of MBP.MAX8(row0, row1), lane by lane."""
    def int8s(v):
        return (isinstance(v, (list, tuple)) and len(v) == 8
                and all(isinstance(x, int) and -128 <= x <= 127 for x in v))
    for name, v in (("row0", row0), ("row1", row1), ("prediction", prediction)):
        if _blank(v) or not int8s(v):
            print(f"{name} needs eight whole numbers between -128 and 127, e.g. "
                  f"[12, -7, 100, 3, -128, 55, 0, 9]; replace the ... and run the cell again.")
            return None
    got = _max8(row0, row1)
    wrong = [i for i in range(8) if prediction[i] != got[i]]
    print(f"{8 - len(wrong)} of 8 lanes correct" + (f"; wrong: lane {', '.join(map(str, wrong))}" if wrong else ""))
    rows = [("row0", list(row0)), ("row1", list(row1)), ("MBP.MAX8(row0, row1)", got), ("your prediction", list(prediction))]
    marks = {(3, i): BAD for i in wrong} | {(3, i): GOOD for i in range(8) if i not in wrong}
    return _lanes_figure(rows, marks, "Your prediction: green lanes are right, red ones are not")


TUT = Path.home() / "tut"
MB_SYNC_EXCLUDES = ("/out/", ".git", "/*.local.md", "/board.conf", "/secrets/", "__pycache__",
                    "/.board.lock*", "/zephyr-chipyard-sw/", "/third_party/")   # the lab uses ~/mb-tools


def mb_sync(src: Path = TUT, dst: Path | None = None) -> bool:
    """Bring the lab in ~/iiswc-tutorial up to the repo commit this seat's content pins.

    ~/tut is advanced to that commit by seat-content.sh on every publish; ~/iiswc-tutorial is
    the copy the lab runs from (scripts/96_seat_mb_setup.sh made it). Copying one into the
    other when the pin moves means a publish updates the lab on every seat, with no per-seat
    step. Skipped while a lab run is in progress, so a running script is never rewritten.
    """
    dst = dst or MB_REPO
    if not (src / "scripts/95_mb_kernel_llm.sh").exists() or not dst.is_dir():
        return False
    head = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    stamp = dst / ".synced-from"
    if not head or (stamp.exists() and stamp.read_text().strip() == head):
        return False
    busy = subprocess.run(["pgrep", "-u", str(os.getuid()), "-f", "95_mb_kernel_llm[.]sh"],
                          capture_output=True).returncode == 0
    if busy:
        return False
    cmd = ["rsync", "-a", "--delete", *[f"--exclude={x}" for x in MB_SYNC_EXCLUDES],
           f"{src}/", f"{dst}/"]
    if subprocess.run(cmd, capture_output=True).returncode != 0:
        return False
    stamp.write_text(head + "\n")
    print(f"the lab on this seat now matches the repo at {head[:12]}")
    return True


def mb_preflight() -> bool:
    """What this seat needs to run the optimizer live, and which pieces it has.

    Five separate things, because they fail separately and the message that says
    "not provisioned" is useless when only one of them is missing.
    """
    mb_sync()
    checks = [
        ("the model key", Path.home() / ".config/iiswc/bedrock.env",
         "an instructor runs scripts/93_bedrock_key.sh distribute"),
        ("the dev environment", Path.home() / ".config/iiswc/dev.env",
         "an instructor runs scripts/96_seat_mb_setup.sh"),
        ("the spike that knows MBP", Path.home() / "mb-tools/bin/spike",
         "an instructor runs scripts/96_seat_mb_setup.sh"),
        ("the optimizer", MB_REPO / "scripts/95_mb_kernel_llm.sh",
         "an instructor runs scripts/96_seat_mb_setup.sh"),
        ("the key to your board", BOARD_KEY,
         "an instructor installs the seat's board key"),
    ]
    ready = True
    for name, path, fix in checks:
        have = path.exists()
        ready &= have
        print(f"  {'yes' if have else 'NO ':<4} {name:<26} {path}")
        if not have:
            print(f"       {fix}")
    board = BOARD_KEY.exists() and _board_answers()
    print(f"  {'yes' if board else 'NO ':<4} {'your board answers':<26} through its tunnel")
    if not board:
        print("       is it on and on the WiFi? `mb doctor` in a terminal says why; a run waits for it")
    print()
    print("This seat can run the optimizer." if ready else
          "This seat cannot run the optimization loop until the NO lines above are fixed.\n"
          "Ask an instructor.")
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


def _progress():
    """scripts/lib/mb_progress.py, which turns a run directory into the rows of its chart: the
    repository's copy when the repository is on this instance, else the one beside this module."""
    for lib in (MB_REPO / "scripts/lib", Path(__file__).resolve().parent):
        if (lib / "mb_progress.py").exists():
            if str(lib) not in sys.path:
                sys.path.insert(0, str(lib))
            break
    import mb_progress  # noqa: PLC0415
    return mb_progress


def _board(run) -> dict:
    b = _jsonf(Path(run) / "board.json") or (_jsonf(Path(run) / "run.json") or {}).get("board")
    return b if isinstance(b, dict) else {}


def _compared(sp: float) -> str:
    """'16.2x faster on your FPGA', 'about as fast on your FPGA', or '2.0x slower on your FPGA'."""
    if sp >= 1.05:
        return f"{sp:.1f}x faster on your FPGA"
    if sp > 0.95:
        return "about as fast on your FPGA"
    return f"{1 / sp:.1f}x slower on your FPGA"


def _total(run) -> str:
    """'16.2x faster on your FPGA (11.0x of it the MBP)', or '' before the board has run."""
    b = _board(run)
    if not b.get("speedup"):
        return ""
    s = _compared(b["speedup"])
    return s + (f" ({b['speedup_accel']:.1f}x of it the MBP)" if b.get("speedup_accel") else "")


def optimizer_figure(run: str | Path, title: str = "Every kernel the search tried"):
    """Every kernel the search produced, in cycles per output: spike for each candidate,
    and the FPGA for the round's best, with and without the MBP."""
    if _no_run(run, "plot"):
        return
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    from matplotlib.ticker import FixedLocator, NullLocator
    run = Path(run)
    d = _progress().collect(run)
    n = d["n"] or 1
    rows = [r for r in d["rows"] if r[1] or r[2]] + [r for r in d["rows"] if not (r[1] or r[2])]
    b = _board(run)
    if rows and rows[0][0] == "reference" and not rows[0][2] and (b.get("before") or {}).get("op_cycles"):
        rows[0] = rows[0][:2] + (b["before"]["op_cycles"],) + rows[0][3:]
    fig, ax = plt.subplots(figsize=(9.6, 0.5 * max(len(rows), 1) + 1.2))
    ys = list(range(len(rows)))[::-1]
    fpga = [r[2] for r in rows[1:] if r[2]]
    best = min(fpga) if fpga else None
    for y, (label, spike, fc, idea, ok) in zip(ys, rows):
        if spike:
            ax.barh(y + 0.17, spike / n, height=0.3, color=S1, zorder=3)
            ax.annotate(f"{spike / n:,.1f}", (spike / n, y + 0.17), textcoords="offset points", xytext=(4, 0),
                        va="center", fontsize=7.5, color=S1)
        off = d["mbpoff"].get(label)
        if off:
            ax.barh(y - 0.17, off / n, height=0.3, fill=False, edgecolor=S2, ls="--", lw=0.8, zorder=2)
            ax.annotate(f"{off / n:,.1f} MBP off", (off / n, y - 0.17), textcoords="offset points", xytext=(4, 0),
                        va="center", fontsize=7.5, color=S2)
        if fc:
            ax.barh(y - 0.17, fc / n, height=0.3, color=S2, zorder=3)
            ax.annotate(f"{fc / n:,.1f}" + ("  best on the FPGA" if fc == best else ""), (fc / n, y - 0.17),
                        textcoords="offset points", xytext=(4, 0), va="center", fontsize=7.5, color=S2,
                        weight="bold" if fc == best else "normal", zorder=4,
                        bbox=dict(facecolor="#fcfcfb", edgecolor="none", pad=0.6))
        ax.annotate(idea[:80], (1.01, y), xycoords=("axes fraction", "data"), va="center", fontsize=7.5,
                    color=INK2 if ok else BAD)
    ax.axvline(MB_GOAL, color=GOOD, ls=":", lw=1, zorder=1)
    ax.set_xscale("log")
    ticks = [t for t in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000)]
    ax.xaxis.set_major_locator(FixedLocator(ticks))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set_xticklabels([str(t) for t in ticks])
    lo = min([v / n for r in rows for v in (r[1], r[2]) if v] + [MB_GOAL]) / 1.5
    hi = max([v / n for r in rows for v in (r[1], r[2]) if v] + [v / n for v in d["mbpoff"].values()] + [MB_GOAL]) * 1.6
    ax.set_xlim(lo, hi)
    ax.set_yticks(ys)
    ax.set_yticklabels([r[0] for r in rows], fontsize=8.5)
    ax.set_xlabel("cycles per output (log scale)", fontsize=8, color=INK2)
    ax.legend(handles=[Patch(color=S1, label="spike"), Patch(color=S2, label="your FPGA"),
                       Patch(fill=False, edgecolor=S2, ls="--", label="FPGA, MBP off"),
                       Line2D([], [], color=GOOD, ls=":", label=f"goal, {MB_GOAL:g} per output")],
              fontsize=7.5, frameon=False, loc="upper left", bbox_to_anchor=(0, -0.5 / max(len(rows), 1) - 0.12),
              ncol=4, handlelength=1.6)
    st = d.get("status") or {}
    total = _total(run)
    ax.set_title(f"{title}  ({st.get('step', '')})" if st.get("state") == "running"
                 else f"{title}: {total}" if total else title, fontsize=10, color=INK, loc="left", pad=8)
    _axes_style(ax)
    fig.subplots_adjust(left=0.14, right=0.6)
    plt.close(fig)
    return fig


def _png(fig):
    import io
    from IPython.display import Image
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    return Image(buf.getvalue())


BOARD_KEY = Path.home() / ".ssh/iiswc-board-agent"
MB_BOARD_WAIT = 120     # seconds a run waits for a board that does not answer


def _board_answers() -> bool:
    """Your board's agent answers through its reverse tunnel, or is busy with another run."""
    lock = Path.home() / ".mb-board.lock"
    if lock.exists():
        import fcntl
        with open(lock) as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True                 # another run holds the board; this one queues
    cmd = ["ssh", "-p", os.environ.get("MB_BOARD_AGENT_PORT", "19022"), "-i", str(BOARD_KEY),
           "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=10",
           "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
           "-o", "LogLevel=ERROR", "xilinx@localhost", "ping"]
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=20).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def _wait_for_board(status) -> bool:
    """Up to MB_BOARD_WAIT seconds for the board, so a run is not left on spike by a blip."""
    from IPython.display import Pretty
    t0 = time.time()
    while not _board_answers():
        if time.time() - t0 >= MB_BOARD_WAIT:
            return False
        status.update(Pretty(f"[{time.time() - t0:4.0f} s]  waiting for your board to answer "
                             f"through its tunnel (up to {MB_BOARD_WAIT} s) ..."))
        time.sleep(10)
    return True


def _mb(words: list[str], timeout: int) -> Path | None:
    """Run `mb <words>` on this instance: its current step prints on one line, and the chart
    of the candidates redraws in place as they are scored.  Returns the run directory."""
    import queue
    import threading
    from IPython.display import Pretty, display
    mb_sync()
    status = display(Pretty("checking your board ..."), display_id=True)
    if BOARD_KEY.exists() and not _wait_for_board(status):
        print(f"YOUR BOARD DID NOT ANSWER IN {MB_BOARD_WAIT} s: this run is on spike only and nothing "
              "runs on the FPGA.\n`mb doctor` in a terminal says why; run the cell again once it answers.")
    t0 = time.time()
    proc = subprocess.Popen([str(MB), *words], cwd=MB_REPO, env=_clean_env(), text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1)
    q: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=lambda: [q.put(l) for l in proc.stdout], daemon=True).start()
    lines: list[str] = []
    name, last_draw = "", 0.0
    status.update(Pretty("starting ..."))
    chart = display(Pretty(""), display_id=True)
    try:
        while True:
            while not q.empty():
                ln = _NO_ANSI.sub("", q.get()).rstrip()
                if ln.strip().startswith("run: "):
                    name = ln.strip()[5:].strip()
                if ln.strip():
                    lines.append(ln)
            st = (_jsonf(MB_RUNS / name / "status.json") or {}) if name else {}
            status.update(Pretty(f"[{time.time() - t0:4.0f} s]  {st.get('step') or (lines[-1].strip() if lines else '')}"))
            if name and (MB_RUNS / name).is_dir() and time.time() - last_draw > 10:
                try:
                    chart.update(_png(optimizer_figure(MB_RUNS / name, "The search so far")))
                    last_draw = time.time()
                except Exception:           # the run directory is still being written
                    pass
            if proc.poll() is not None and q.empty():
                break
            if time.time() - t0 > timeout:
                proc.kill()
                print(f"[timed out after {timeout} s -- killed]")
                break
            time.sleep(2)
    except KeyboardInterrupt:
        proc.kill()
        raise
    run = MB_RUNS / name if name else None
    total = _total(run) if run else ""
    status.update(Pretty(f"[rc={proc.returncode}  {time.time() - t0:.0f} s]  run: {run}"
                         + (f"\n{total}, bit exact" if total and _exact(run) else f"\n{total}" if total else "")))
    for ln in lines:
        if "does not answer" in ln and ("spike only" in ln or "replaying" in ln):
            print("fallback:", ln.strip())
    if run and (run / "run.json").exists():
        try:
            chart.update(_png(optimizer_figure(run)))
        except Exception:
            pass
    else:
        print("\n".join(lines[-25:]))
    return run


def mb_optimize(ready: bool, op: str = "maxpool2d_s8", rounds: int = 2, beam: int = 2,
                expansions: int = 2, max_calls: int = 8) -> Path | None:
    """One optimization, with your board in the loop.  It waits up to MB_BOARD_WAIT seconds
    for the board; `mb` itself falls back to a recorded kernel if the model does not
    answer, and to spike alone if the board does not, and says so.  A seat that is not
    set up gets no run, not a recording in its place."""
    if not ready:
        print("no run: this seat is not set up for the optimization loop (4.3 lists what is\n"
              "missing). Ask an instructor.")
        return None
    return _mb(["go", op, "--rounds", str(rounds), "--beam", str(beam),
                "--expansions", str(expansions), "--max-calls", str(max_calls)], timeout=3600)


def mb_start(op: str = "maxpool2d_s8") -> Path:
    """Copy the starting kernel (ModelBlaster's reference) to a file you can edit."""
    mb_sync()
    r = sh(f"{MB} start {op}", timeout=60, quiet=True)
    path = MB_KERNELS / f"{op}.c"
    print(next((l.strip() for l in _NO_ANSI.sub("", r.stdout).splitlines() if "<-" in l), r.stdout.strip()))
    print(f"\nopen it in the file browser on the left, edit it, save it: {path}")
    return path


def mb_try(path: str | Path | None = None, op: str = "maxpool2d_s8") -> Path | None:
    """Your kernel: checked on spike first, then run on your board with and without the MBP."""
    return _mb(["try", op] + ([str(path)] if path else []), timeout=1200)


def _calls(run) -> list[dict]:
    p = Path(run) / "after/transcript.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def _no_run(run, what: str = "read") -> bool:
    """True when there is no run to read, having said so in words that name the cause.

    mb_start() and mb_try() return None ON PURPOSE when a run could not happen: no model
    credentials on this instance, or a board that never answered. Handing that None to a
    reader raised `TypeError: argument should be a str or an os.PathLike object` three
    frames further down, which names neither the cell that failed nor the reason.
    """
    if run is not None:
        return False
    print(f"no run to {what}: the cell above did not produce one.")
    print("      Its own output says why -- usually no model credentials on this")
    print("      instance, or a board that did not answer in time.")
    return True


def show_search_shape(run: str | Path) -> None:
    """How big the search was: rounds, phases, calls, and what each call cost.

    Read from the run's own call log rather than from the flags, because the flags say
    what was ASKED for and the log says what happened -- a round that found no
    improvement stops early, and then the two disagree.
    """
    if _no_run(run, "read"):
        return
    rows = _calls(run)
    if not rows:
        print(f"no calls in {Path(run).name}: a replayed or hand-written kernel calls no model")
        return
    print(f"{len(rows)} calls to {rows[0].get('model', '?')}\n")
    print(f"  {'call':>4}  {'round':>5}  {'phase':<26}{'in':>8}{'out':>7}{'s':>7}   what it tried")
    for i, r in enumerate(rows, 1):
        idea = _progress().idea_of(r.get("response")) or ""
        print(f"  {i:>4}  {r.get('round', '?'):>5}  {str(r.get('phase', '')):<26}"
              f"{r.get('input_tokens') or 0:>8,}{r.get('output_tokens') or 0:>7,}"
              f"{r.get('latency_s') or 0:>7.1f}   {idea[:60]}")
    ti = sum(r.get("input_tokens") or 0 for r in rows)
    to = sum(r.get("output_tokens") or 0 for r in rows)
    print(f"\n  {'total':>4}{'':>9}{'':<26}{ti:>8,}{to:>7,}"
          f"{sum(r.get('latency_s') or 0 for r in rows):>7.0f}")
    errs = [r.get("error") for r in rows if r.get("error")]
    print(f"  errors: {errs if errs else 'none'}")


# What a recorded system prompt carried: ModelBlaster's target guide for the op's target,
# and what the lab appends to it.  Runs recorded before the MBP guide was the target guide
# appended an earlier version of it instead.
_GUIDES = (("# MBP (pext) kernel optimization guide", "the MBP optimization guide"),
           ("# Kernel optimization guide", "ModelBlaster's scalar guide"))
_ADDED = (("packed-SIMD integer extension (MBP)", "an earlier MBP guide, appended"),
          ("One more output rule: say what this version tries", "the idea-line rule"),
          ("### Measured on the real FPGA", "the board's numbers"),
          ("### Measured on the FPGA", "the board's numbers"),
          ("### Hardware in the loop feedback", "the board's numbers"))


def _feedback_text(system: str) -> str:
    at = min((system.find(m) for m, name in _ADDED if name == "the board's numbers" and m in system), default=-1)
    return system[at:].strip() if at >= 0 else ""


def show_model_inputs(run: str | Path) -> None:
    """What each call's system prompt held besides ModelBlaster's own instructions, and
    every prompt and answer, one collapsible entry per call."""
    if _no_run(run, "read"):
        return
    import html
    calls = _calls(run)
    if not calls:
        print(f"no calls in {Path(run).name}: a replayed or hand-written kernel calls no model")
        return
    for c in calls:
        system = c.get("system") or ""
        guide = next((name for mark, name in _GUIDES if mark in system), "none")
        added = list(dict.fromkeys(name for mark, name in _ADDED if mark in system))
        print(f"  #{c.get('n')}  round {c.get('round')}  {c.get('phase', ''):<24} target guide: {guide};"
              f"  appended: {', '.join(added) or 'nothing'}")
    print(f"\nthe MBP optimization guide: {MB_REPO / 'fpga/pynq-z2/modelblaster/prompts/optimization_guide_pext.md'}")
    from IPython.display import HTML, display
    box = "font:12px/1.5 monospace;white-space:pre-wrap;border:1px solid #888;padding:6px;margin:4px 0 8px 0"
    parts = []
    for c in calls:
        hil = _feedback_text(c.get("system") or "")
        parts.append(
            f"<details style='margin:2px 0'><summary style='cursor:pointer;font:12px monospace'>#{c.get('n')}  "
            f"round {c.get('round')}  {html.escape(str(c.get('phase')))}: the prompt and the answer</summary>"
            + (f"<div style='{box}'>{html.escape(hil)}</div>" if hil else "")
            + f"<div style='{box}'>{html.escape((c.get('user') or '').strip())}</div>"
            f"<div style='{box}'>{html.escape((c.get('response') or c.get('error') or '').strip())}</div></details>")
    display(HTML("".join(parts)))


def show_board_feedback(run: str | Path) -> None:
    """What the board told the model between rounds -- the hardware in the loop.

    Spike scores every candidate and has no memory timing, so this file is the only
    thing in the loop that knows what the silicon actually did.  A run that kept only its
    prompts has the same text in the last round's system prompt.
    """
    if _no_run(run, "read"):
        return
    run = Path(run)
    fb = next((p for p in (run / "after/board_feedback.md", run / "fpga-feedback.md") if p.exists()), None)
    text = fb.read_text().rstrip() if fb else next(
        (t for t in (_feedback_text(c.get("system") or "") for c in reversed(_calls(run))) if t), "")
    print(text or f"no board feedback in {run.name}: it was scored on spike alone")


def _kernel_file(run: Path):
    j = _jsonf(run / "run.json") or {}
    after = Path(j.get("after_kernel") or "")
    if str(after) and not after.is_absolute():
        after = run / after
    elif str(after) and not after.exists() and run.name in after.parts:    # a run unpacked elsewhere
        after = run.joinpath(*after.parts[after.parts.index(run.name) + 1:])
    if not after.is_file():
        cands = (sorted(run.glob("after/round*.kernel.c")) or sorted(run.glob("**/cache/*.c"))
                 or sorted(run.glob("kernels_replay/*/*.c")))
        after = cands[-1] if cands else None
    return after


def show_llm_kernel(run: str | Path, around: str = "", lines: int = 24) -> None:
    """The kernel the model wrote, as it was compiled, opened on the loop that uses the MBP.
    The lines that call it are marked with >>."""
    if _no_run(run, "read"):
        return
    src = _kernel_file(Path(run))
    if src is None:
        print(f"no kernel source under {run}")
        return
    text = src.read_text(errors="replace").splitlines()
    uses = [i for i, l in enumerate(text) if "mb_pext_" in l or "MB_PEXT_LD8" in l]
    anchor = next((i for i, l in enumerate(text) if around and around in l), None)
    start = max((anchor if anchor is not None else (uses[0] - 4 if uses else next(
        (i for i, l in enumerate(text) if "for (" in l), 0))), 0)
    print(f"{src}\n(the lines marked >> use the MBP: its 8-byte loads and mb_pext_max8)\n")
    for i in range(start, min(start + lines, len(text))):
        mark = ">>" if ("mb_pext_" in text[i] or "MB_PEXT_" in text[i]) else "  "
        print(f"{mark}{i + 1:>4}  {text[i]}")


def show_on_accelerator(run: str | Path) -> None:
    """The evidence that the kernel ran on the MBP, from the images the board ran."""
    if _no_run(run, "read"):
        return
    b = _board(run)
    if not b:
        print(f"no board run in {Path(run).name}: it was scored on spike alone")
        return
    img = (b.get("after") or {}).get("image") or {}
    ops = {}
    for fn, n in (img.get("mbp_by_function") or {}).items():
        if fn != "neg_worker":
            for k, v in n.items():
                ops[k] = ops.get(k, 0) + v
    used = ", ".join(f"MBP.{k.upper()} x{v}" for k, v in ops.items() if v) or "no MBP instruction"
    print(f"  compiled into the new kernel:   {used}")
    neg = (b.get("after") or {}).get("neg") or {}
    if neg:
        print(f"  the same instruction on hart 1: {'trapped' if neg.get('trapped') else 'DID NOT TRAP'}"
              f" (mcause {neg.get('mcause')}), so hart 0 really executes it")
    off = (b.get("mbpoff") or {}).get("image") or {}
    if off:
        print(f"  MBP instructions left in the MBP-off image: {off.get('mbp_outside_negtest')}")


def _exact(run) -> bool:
    b = _board(run)
    arms = [b.get(a) for a in ("before", "after", "mbpoff") if isinstance(b.get(a), dict)]
    return bool(arms) and all((a.get("run") or {}).get("max_abs_err", 0) == 0 for a in arms)


def show_board_verdict(run: str | Path) -> None:
    """The three board arms of an optimizer run, and each arm's correctness gate.

    Three images, one bitstream, same data: the reference kernel, the new kernel, and the
    new kernel with the MBP instruction replaced by a C model of it.
    """
    if _no_run(run, "read"):
        return
    run = Path(run)
    b = _board(run)
    j = _jsonf(run / "run.json") or {}
    kind = ("your kernel" if j.get("kernel_file") else "a recorded LLM kernel, replayed (no model call)"
            if j.get("replay") else "a live LLM run")
    if not (b.get("before") or {}).get("op_cycles"):
        print(f"{run.name}: {kind}, scored on spike alone (the board step did not run)")
        be, af = j.get("before") or {}, j.get("after") or {}
        if be.get("cycles") and af.get("cycles"):
            print(f"\n{'arm':<10}{'spike cycles':>14}{'per output':>13}{'vs reference':>14}   correctness")
            for name, a in (("before", be), ("after", af)):
                err = a.get("golden_max_abs_err")
                note = "-" if err is None else ("bit-exact" if err == 0 else f"max |d| = {err:g}")
                print(f"{name:<10}{a['cycles']:>14,}{a.get('cycles_per_output') or 0:>13.1f}"
                      f"{be['cycles'] / a['cycles']:>13.2f}x   {note}")
            print("\nspike estimate only: it does not model memory timing, so the board usually measures less")
        print("for the FPGA numbers, run the cell again once `mb doctor` says your board answers")
        return
    print(f"{run.name}: {kind}, measured on {b.get('board', '?')} ({b.get('magic', '?')}, "
          f"{(b.get('fclk_hz') or 0) / 1e6:.0f} MHz)\n")
    sp = b.get("speedup")
    if sp:
        than = "as" if 0.95 < sp < 1.05 else "than"
        print(f"total: {_compared(sp)} {than} the reference kernel"
              f"{', bit exact' if _exact(run) else ', OUTPUT DIFFERS'}\n")
    print(f"{'arm':<10}{'cycles':>14}{'per output':>13}{'vs reference':>14}   correctness")
    ref = None
    for name in ("before", "after", "mbpoff"):
        a = b.get(name)
        if not isinstance(a, dict) or a.get("op_cycles") is None:
            continue
        cyc = a["op_cycles"]
        per = cyc / a["out_len"] if a.get("out_len") else cyc
        ref = ref or cyc
        err = (a.get("run") or {}).get("max_abs_err")
        note = "-" if err is None else ("bit-exact" if err == 0 else f"max |d| = {err}")
        print(f"{name:<10}{cyc:>14,}{per:>13.1f}{ref / cyc:>13.2f}x   {note}")
    if b.get("speedup_accel"):
        print(f"\nof the total, the MBP instruction: {b['speedup_accel']:.1f}x  (the same kernel, MBP on against off)")
    if j.get("speedup"):
        print(f"spike estimated {j['speedup']:.1f}x; it does not model memory timing")


def compare_guess(guess, run: str | Path) -> None:
    """Your guess against what the board measured."""
    if _blank(guess) or not isinstance(guess, (int, float)) or isinstance(guess, bool):
        print("my_guess needs a number, e.g. my_guess = 10; set it and run the cell again.")
        return
    got = _board(run).get("speedup")
    if not got:
        print("no board measurement in this run to compare against")
        return
    word = "higher than" if got > guess * 1.1 else "lower than" if got < guess * 0.9 else "close to"
    print(f"you guessed {guess}x; the board measured {got:.1f}x, {word} your guess")

# --------------------------------------------------------------------------------------
# Reading a recorded board run (Unit 2).
# --------------------------------------------------------------------------------------
def plain_name(label: str) -> str:
    """A run or scheduler name with the internal experiment tag stripped off it.

    Recorded artifacts name their arms after the lab that produced them -- `b189_pext`,
    `cpsat_warmbest_b157`. Nothing an attendee reads should carry that, and the tag is not
    part of what the row says, so it comes off at the point of printing rather than by
    rewriting recorded files.
    """
    import re

    return re.sub(r"(^b\d+_)|(_b\d+$)", "", label)


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
        print(f'{plain_name(arm["run_name"]):<14} custom-0 instructions in the binary '
              f'{arm["custom0_instructions_in_elf"]:>3}   '
              f'output vs golden: {arm["gate"]["board_vs_golden_bytes_differ"]} of 192 bytes '
              f'differ, max |d| = {arm["gate"]["max_abs_err"]}')


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
# Reading an XPU-RT solve, and the recorded co-location sweep (Unit 5).
#
# Two things used to sit in the cells here: paths assembled out of environment variables,
# and a glob over an output directory. Both are plumbing, and both were wrong in a way a
# cell cannot show -- the notebook kernel is started by systemd with a bare environment,
# so nothing in /etc/profile.d has run in it and `os.environ` has no XPURT_ROOT on a seat
# that has XPU-RT installed. Resolving that once, here, is what lets a cell ask its
# question.
# --------------------------------------------------------------------------------------
#: Where the instance's XPU-RT install announces itself. A login shell reads this; the
#: notebook kernel never does, so these helpers read it directly.
XPURT_PROFILE = Path("/etc/profile.d/xpurt.sh")

#: The schedule 5.1 solves. XPU-RT names every artifact after the networks file it was
#: given, so this one name fixes both the metrics file and the plot.
XPURT_SCHEDULE = "networks_pynqz1_enc_w20_cpsat_profiled"



def _xpurt_exports() -> dict:
    """XPURT_* as the instance's profile script sets them, whether or not it has run."""
    out: dict[str, str] = {}
    if XPURT_PROFILE.exists():
        for line in XPURT_PROFILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("export ") and "=" in line:
                k, _, v = line[len("export "):].partition("=")
                out[k.strip()] = v.strip().strip('"\'')
    return out


def xpurt_root() -> Path | None:
    """The XPU-RT checkout to solve in, or None if this machine has none."""
    for cand in (os.environ.get("XPURT_ROOT"), _xpurt_exports().get("XPURT_ROOT"),
                 "/opt/xpurt/XPU-RT"):
        if cand and Path(cand, "scripts").is_dir():
            return Path(cand)
    return None


def xpurt_python() -> Path | None:
    """An interpreter that has ortools, which is never the one running this notebook.

    XPU-RT solves the CP-SAT model in a subprocess and looks for ortools in that
    subprocess, so the interpreter matters even in cells that import nothing.
    """
    ex = _xpurt_exports()
    for cand in (os.environ.get("XPURT_PY"), os.environ.get("XPURT_PYTHON"),
                 ex.get("XPURT_PYTHON"), ex.get("XPURT_CPSAT_PYTHON"),
                 "/opt/xpurt/venv/bin/python"):
        if cand and os.access(cand, os.X_OK):
            return Path(cand)
    return None


def solved_metrics(name: str = XPURT_SCHEDULE) -> dict:
    """The metrics file the solve just wrote, read back from the XPU-RT tree."""
    root = xpurt_root()
    if root is None:
        raise FileNotFoundError("no XPU-RT tree on this machine, so no solve to read")
    path = root / "schedules" / f"scheduled_{name}_metrics.json"
    # THE IMAGE SHIPS A PASSING METRICS FILE.  A bare read cannot tell a solve that ran from
    # one that never did, so a cell whose solve failed still prints MATCH.  Anything older
    # than this instance's boot came out of the image, not out of the cell above.
    try:
        boot = next(float(l.split()[1]) for l in open("/proc/stat") if l.startswith("btime"))
        if path.stat().st_mtime < boot:
            print(f"NOTE: {path.name} predates this instance's boot, so it is the image's\n"
                  f"      schedule and not one you solved.  Run the cell above first.")
    except (OSError, StopIteration, ValueError):
        pass          # a freshness hint is never worth failing the read for
    return json.loads(path.read_text())


def check_solved_makespan(want_us: float, name: str = XPURT_SCHEDULE) -> bool:
    """Did the solve on this instance land the makespan the notebook quotes?

    The operator durations were measured on silicon, so this number is a property of the
    SoC and not of the instance: it is the same on four vCPUs and on a workstation.
    """
    return expect("makespan_us", round(solved_metrics(name)["makespan_us"], 2), want_us)


def schedule_plot(name: str = XPURT_SCHEDULE):
    """The placement the solve found, as the picture XPU-RT drew of it."""
    from IPython.display import Image

    root = xpurt_root()
    if root is None:
        raise FileNotFoundError("no XPU-RT tree on this machine, so no plot to show")
    return Image(filename=str(root / "plots" / f"{name}.png"))




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


def _axes_style(ax):
    ax.set_facecolor("#fcfcfb")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8, length=3)
    ax.grid(axis="x", color=GRID, lw=0.6, zorder=0)
    ax.set_axisbelow(True)


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
    plt.close(fig)   # the inline backend auto-shows a live figure, and
                     # returning it renders a second copy
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
    plt.close(fig)   # the inline backend auto-shows a live figure, and
                     # returning it renders a second copy
    return fig


# --------------------------------------------------------------------------------------
# Measuring an operator profile on your own board, and solving from it (Unit 5).
#
# The schedule 5.1 solves is built from per-dispatch costs that were measured on a board:
# one cycle count per operator invocation, read off a console. These helpers close that
# loop inside the notebook -- run the detector on this seat's own card, convert what it
# printed with XPU-RT's own converter, and point the scheduler at the result.
#
# The board's profiling image prints one `MB_PEXT_OP` line per dispatch, carrying the
# same five fields (`dispatch_id`, `name`, `op`, `shape`, `cycles`) that a ModelBlaster
# harness prints between its `MODELBLASTER_PROFILE` markers. `scripts/uartlog_to_profile.py`
# reads that block and writes the results.csv; putting the console's fields into the block
# is the whole conversion, and it is the reason the board needs no separate ingest path.
# --------------------------------------------------------------------------------------
#: The detector image that profiles itself: one cycle count per dispatch, on the console.
PROFILE_IMAGE = "signdet_profile.bin"

#: The PL clock the profiling bitstream runs the core at. Cycles become times here and
#: nowhere else, so a board at another clock is one number's worth of change.
PROFILE_CLOCK_MHZ = 40.0

#: Where the measured profile tree and its workload spec are written.
MEASURED_DIR = Path.home() / "work" / "measured_profile"

#: The workload spec the measured profile is solved from, and the schedule it names.
MEASURED_SPEC = "networks_measured_on_your_board.json"
MEASURED_SCHEDULE = "networks_measured_on_your_board_cpsat_profiled"

#: The shipped workload spec 5.1 solves. Everything about it is reused except the one
#: profile tree the measured costs replace.
SHIPPED_SPEC = "data/toplevel/networks_b154_gate.json"

#: The console tag the profiling image prints one of per dispatch.
_OP_TAG = "MB_PEXT_OP "


def profile_image() -> Path:
    """The detector profiling image to push to the card."""
    path = Path(PROFILE_IMAGE)
    return path if path.exists() else ASSETS / PROFILE_IMAGE


def measured_dispatch_rows(console: str) -> list[dict]:
    """The board's per-dispatch cycle counts, one row per operator invocation.

    Each `MB_PEXT_OP` line is `key=value` pairs, so this reads pairs rather than columns:
    a line that gains a field keeps parsing. The dispatch id is the join key everywhere
    downstream -- names repeat across a network, ids do not.
    """
    rows: list[dict] = []
    for line in console.splitlines():
        line = line.strip()
        if not line.startswith(_OP_TAG):
            continue
        kv = dict(tok.split("=", 1) for tok in line[len(_OP_TAG):].split()
                  if "=" in tok)
        if "id" not in kv or "cycles" not in kv:
            continue
        rows.append({"dispatch_id": int(kv["id"]), "name": kv.get("name", ""),
                     "op": kv.get("op", ""), "shape": kv.get("shape", ""),
                     "cycles": int(kv["cycles"])})
    return rows


def _profile_csv(spec: dict, core: str = "cpu_p") -> Path | None:
    """The results.csv a workload spec reads for one of its two machines.

    XPU-RT finds a profile by building a path out of the spec: the profile tree, the
    backend label for that machine, the target, the model and the core topology. This
    walks the same path so a cell can read the file the solver read.
    """
    root = xpurt_root()
    if root is None:
        return None
    prof = spec.get("hardware", {}).get("profile", {})
    hw = spec.get("hardware", {}).get("profile_hw", {}).get(core)
    nets = spec.get("networks", {})
    if not (hw and nets and prof.get("target")):
        return None
    net = next(iter(nets))
    gen = Path(prof.get("gen_root", "gen"))
    base = (gen if gen.is_absolute() else root / gen) / "profile" / hw / prof["target"] / net
    topo = prof.get("topo_tag", "topo_0")
    for pat in (f"{net}.*/{topo}/results.csv", f"{net}.*/*/{topo}/results.csv"):
        hits = sorted(base.glob(pat))
        if hits:
            return hits[0]
    return None


def _csv_cycles(path: Path | None) -> dict[int, int]:
    """dispatch_id -> cycles, out of a profile results.csv."""
    import csv as _csv

    out: dict[int, int] = {}
    if path is None or not Path(path).exists():
        return out
    with open(path, newline="") as fh:
        for row in _csv.DictReader(fh):
            try:
                out[int(row["dispatch_id"])] = int(row["cycles"])
            except (KeyError, TypeError, ValueError):
                continue     # a sentinel row carries no cycle count, by design
    return out


def _shipped_spec() -> dict | None:
    root = xpurt_root()
    if root is None or not (root / SHIPPED_SPEC).exists():
        return None
    return json.loads((root / SHIPPED_SPEC).read_text())


def show_measured_dispatches(rows: list[dict]) -> None:
    """Every dispatch the board timed, beside the cost the shipped schedule used."""
    if not rows:
        print("No MB_PEXT_OP lines in that console, so nothing was timed. Check that the\n"
              "board cells above reported a run that finished.")
        return
    spec = _shipped_spec()
    was = _csv_cycles(_profile_csv(spec)) if spec else {}
    print(f'{"id":>2} {"operator":<9} {"kernel op":<14} {"cycles":>11} {"ms":>8}'
          + (f' {"shipped":>11} {"diff":>8}' if was else ""))
    for r in rows:
        line = (f'{r["dispatch_id"]:>2} {r["name"]:<9} {r["op"]:<14} '
                f'{r["cycles"]:>11,} {r["cycles"] / PROFILE_CLOCK_MHZ / 1000:>8.2f}')
        old = was.get(r["dispatch_id"])
        if old:
            line += f' {old:>11,} {100.0 * (r["cycles"] - old) / old:>+7.2f}%'
        print(line)
    total = sum(r["cycles"] for r in rows)
    line = (f'{"":>2} {"total":<9} {"":<14} {total:>11,} '
            f'{total / PROFILE_CLOCK_MHZ / 1000:>8.2f}')
    old_total = sum(was.get(r["dispatch_id"], 0) for r in rows)
    if old_total:
        line += f' {old_total:>11,} {100.0 * (total - old_total) / old_total:>+7.2f}%'
    print(line)


def _profile_block(rows: list[dict], net: str) -> str:
    """The rows in the shape the profile converter reads: one CSV block between markers."""
    out = [f"=== MODELBLASTER_PROFILE_BEGIN [{net}] ===",
           "dispatch_id,name,op,shape,cycles"]
    out += [f'{r["dispatch_id"]},{r["name"]},{r["op"]},{r["shape"]},{r["cycles"]}'
            for r in rows]
    out.append(f"=== MODELBLASTER_PROFILE_END [{net}] ===")
    return "\n".join(out) + "\n"


#: Relative to a ModelBlaster checkout: the module the profile converter imports. Its
#: presence is what makes a candidate directory the right one -- a checkout that carries
#: the tree but not this file is the shape a partial copy has.
_MB_PROBE = Path("modelblaster") / "pipeline" / "profile_writer.py"


def _modelblaster_root() -> Path | None:
    """The ModelBlaster checkout whose profile writer the converter imports.

    The source checkout comes first: it is the tree this notebook points at everywhere
    else, so the converter reads the same ModelBlaster the attendee can open. `ZCS`
    overrides it where something has been staged deliberately.
    """
    repo = repo_root()
    cands = [os.environ.get("ZCS"),
             str(repo / "zephyr-chipyard-sw") if repo else "",
             str(Path.home() / "tut" / "zephyr-chipyard-sw"),
             str(Path.home() / "mb-tools" / "zephyr-chipyard-sw")]
    for cand in cands:
        if cand and (Path(cand) / _MB_PROBE).exists():
            return Path(cand)
    return None


def write_measured_profile(rows: list[dict], clock_mhz: float = PROFILE_CLOCK_MHZ) -> Path | None:
    """Convert the board's rows into a profile tree, and write the spec that reads it.

    The scheduler reads per-dispatch costs as a results.csv under a tree named by
    backend, target, model and core topology; `scripts/uartlog_to_profile.py` is the
    converter that writes one. Everything else in the workload is reused unchanged --
    the dispatch graph, the periods, the second machine's file -- so the only difference
    between this solve and 5.1's is which cycle counts the costs came from.

    Returns the workload spec to solve, or None with the reason printed.
    """
    if not rows:
        print("Nothing to convert: the console carried no per-dispatch rows.")
        return None
    root, py, mb = xpurt_root(), xpurt_python(), _modelblaster_root()
    if root is None:
        print("Not written: no XPU-RT checkout on this machine, so there is no profile "
              "tree to write into and no converter to write it.")
        return None
    if py is None:
        print("Not written: no interpreter with ortools on this machine.")
        return None
    if mb is None:
        print("Not written: the ModelBlaster checkout the converter imports its profile "
              "writer from is not on this machine.")
        return None
    spec = _shipped_spec()
    if spec is None:
        print(f"Not written: this XPU-RT checkout does not carry {SHIPPED_SPEC}, so there "
              "is no workload to re-cost.")
        return None
    net = next(iter(spec["networks"]))
    prof = spec["hardware"]["profile"]
    fast, engine = spec["hardware"]["profile_hw"]["cpu_p"], spec["hardware"]["profile_hw"]["cpu_e"]

    MEASURED_DIR.mkdir(parents=True, exist_ok=True)
    block = MEASURED_DIR / "board.profile"
    block.write_text(_profile_block(rows, net))
    out_root = MEASURED_DIR / "profile"

    r = sh(f"cd {root} && PYTHONPATH={mb} {py} scripts/uartlog_to_profile.py "
           f"--uartlog {block} --model {net} --quant int8 --backend {fast} "
           f"--cpu {prof['target']} --source board --cores 0 "
           f"--clock-mhz {clock_mhz:g} --out-root {out_root} --tag {net}",
           timeout=300, quiet=True)
    if r.returncode != 0:
        print(r.stdout.strip()[-600:])
        print("The converter did not write a profile.")
        return None
    print(r.stdout.strip())

    # The second machine's file is copied, not measured: it records that no kernel for
    # these operators exists for that machine, which no board run can discover.
    shipped_engine = _profile_csv(spec, "cpu_e")
    if shipped_engine is None:
        print(f"Not written: the shipped tree has no {engine} file to carry over.")
        return None
    dst = (out_root / engine / prof["target"] / net / f"{net}.int8"
           / prof.get("topo_tag", "topo_0") / "results.csv")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    dst.symlink_to(shipped_engine)
    print(f"  carried over {engine} unchanged: {dst}")

    # The spec: the measured tree replaces the profile tree, and the dispatch graph is
    # named absolutely because it stays in the checkout the graph was generated in.
    spec["hardware"]["profile"]["gen_root"] = str(MEASURED_DIR)
    for info in spec["networks"].values():
        deps = info.get("dispatch_deps_path", "")
        if deps and not os.path.isabs(deps):
            info["dispatch_deps_path"] = str(root / deps)
    path = MEASURED_DIR / MEASURED_SPEC
    path.write_text(json.dumps(spec, indent=2))
    print(f"  workload to solve: {path}")
    return path


def compare_measured_makespan() -> None:
    """The shipped schedule's duration beside the one your board's costs produced."""
    shipped, mine = _shipped_spec(), None
    path = MEASURED_DIR / MEASURED_SPEC
    if path.exists():
        mine = json.loads(path.read_text())
    if shipped is None or mine is None:
        print("No measured solve to compare -- the cells above did not produce one.")
        return
    was = sum(_csv_cycles(_profile_csv(shipped)).values())
    now = sum(_csv_cycles(_profile_csv(mine)).values())
    try:
        a, b = solved_metrics(XPURT_SCHEDULE), solved_metrics(MEASURED_SCHEDULE)
    except (FileNotFoundError, OSError) as exc:
        print(f"One of the two solves has written no metrics file yet ({exc}). Run 5.1's "
              "cell and the solve above, then this one.")
        return
    print(f'{"shipped profile":<18} makespan_us {a["makespan_us"]:>7.2f}   '
          f'{was:>11,} cycles   op_deadline_miss {a["op_deadline_miss_count"]}')
    print(f'{"your board":<18} makespan_us {b["makespan_us"]:>7.2f}   '
          f'{now:>11,} cycles   op_deadline_miss {b["op_deadline_miss_count"]}')
    if was:
        print(f'\nYour board ran the frame in {now - was:+,} cycles, '
              f'{100.0 * (now - was) / was:+.3f}% of the recorded frame.')
# --------------------------------------------------------------------------------------
# Running a model on the card and reading ModelBlaster's per-layer profile (Unit 2).
#
# ModelBlaster's profiling harness times every operator invocation separately. It reads the
# cycle counter either side of each kernel call and prints one `MB_PEXT_OP` line per
# dispatch, carrying the dispatch id, the operator's name in the model, the kernel op the
# lowering bound to it, the tensor shape, and the cycles that invocation took. That is the
# per-layer profile, and it comes off the board's console -- the same lines whichever model
# the harness was built against.
#
# `measured_dispatch_rows` above parses those lines and is reused unchanged here. What this
# section adds is the reading of them: which layer dominates, which core ran it, and a
# figure that puts every dispatch's share of the frame in one picture.
#
# TWO CORES, AND ONLY ONE OF THEM NEEDS THE PROFILE TO SAY SO. The first core carries the
# MBP instructions; the second carries a RoCC engine reached over a different custom opcode.
# A kernel bound to the engine still has its cycles counted on the first core, because that
# is where the dispatch is issued from and where it waits -- so an engine dispatch's cycle
# count is its whole cost, hand-off and wait included, which is what a schedule needs. The
# runtime prints a separate `MB_ROCCMOON` line saying how many dispatches actually reached
# the engine and how many fell back, and that line is the one to read before believing a
# kernel choice: a lowering can bind a kernel to the engine and the kernel can still decline
# every shape it is handed.
# --------------------------------------------------------------------------------------
#: The speech image that profiles itself: Moonshine's encoder, lowered with the engine
#: backend, timing every one of its dispatches.
SPEECH_PROFILE_IMAGE = "speech_profile.bin"

#: The operator kinds the RoCC engine on the second core has a kernel for. Everything else
#: in a network runs on the first core, out of the MBP kernel library. A per-channel
#: quantised convolution (`conv2d_s8_pc`) is deliberately not in this list: there is no
#: engine kernel for it at all, which is why the detector runs entirely on the first core.
ENGINE_OPS = ("conv2d_s8", "linear_s8")

#: The console tag the engine runtime prints one of per phase, when it is linked in.
_ENGINE_TAG = "MB_ROCCMOON "


def speech_profile_image() -> Path:
    """The speech profiling image to push to the card."""
    path = Path(SPEECH_PROFILE_IMAGE)
    return path if path.exists() else ASSETS / SPEECH_PROFILE_IMAGE


def measured_engine_totals(console: str) -> dict:
    """The engine runtime's own totals for the run, or `{}` if the engine was not linked.

    The `total` phase is the one to read: the runtime prints a `warm` phase as well, whose
    counters cover only the warm-up frame and are zero when there was none.
    """
    for line in console.splitlines():
        line = line.strip()
        if not line.startswith(_ENGINE_TAG):
            continue
        kv = dict(tok.split("=", 1) for tok in line[len(_ENGINE_TAG):].split()
                  if "=" in tok)
        if kv.get("phase") != "total":
            continue
        out = {}
        for k, v in kv.items():
            try:
                out[k] = int(v)
            except ValueError:
                out[k] = v
        return out
    return {}


def show_engine_totals(console: str) -> None:
    """Where the dispatches actually ran: the engine's count, and what fell back."""
    t = measured_engine_totals(console)
    if not t:
        print("No engine line on this console, so this image has no engine kernel bound in\n"
              "it and every dispatch ran on the first core.")
        return
    served, fell = t.get("calls_engine", 0), t.get("calls_fallback", 0)
    print(f'engine dispatches {served:,}   fell back to the first core {fell:,}')
    if t.get("bytes_wgt"):
        print(f'weights streamed into the engine {t["bytes_wgt"]:,} bytes in '
              f'{t.get("loads_wgt", 0):,} loads')
    if t.get("bytes_act"):
        print(f'activations streamed in          {t["bytes_act"]:,} bytes in '
              f'{t.get("loads_act", 0):,} loads')
    if t.get("image_cycles"):
        print(f'one-off weight image load        {t["image_cycles"]:,} cycles '
              f'({t["image_cycles"] / PROFILE_CLOCK_MHZ / 1000:,.2f} ms), outside the frame')
    if served == 0:
        print("\nThe engine is linked in and served nothing: every kernel bound to it "
              "declined\nthe shapes this model handed it.")


def dispatch_shares(rows: list[dict], engine_ops: tuple = ENGINE_OPS) -> list[dict]:
    """The rows with each dispatch's share of the model's total cycles added.

    `pct` is that share, `where` names the core the kernel ran on, and the list comes back
    in dispatch order -- the order the model executes in, which is the order a reader
    follows a network in.
    """
    total = sum(r["cycles"] for r in rows) or 1
    return [dict(r, pct=100.0 * r["cycles"] / total,
                 where="engine" if r["op"] in engine_ops else "first core")
            for r in sorted(rows, key=lambda r: r["dispatch_id"])]


def show_dispatch_profile(rows: list[dict], top: int = 16,
                          clock_mhz: float = PROFILE_CLOCK_MHZ,
                          engine_ops: tuple = ENGINE_OPS) -> None:
    """ModelBlaster's per-layer profile: one line per dispatch, with its share of the frame.

    A detector has eight dispatches and every one of them fits on a screen. An encoder has
    a hundred and twenty-nine, and a hundred and twenty-nine lines is not a profile anybody
    reads -- so above `top` rows this prints the largest `top` by cycles, in dispatch order,
    and collapses the rest into one line. The largest are what a reader is looking for: the
    cost is never spread evenly.
    """
    if not rows:
        print("No MB_PEXT_OP lines in that console, so nothing was timed. Check that the\n"
              "board cells above reported a run that finished.")
        return
    shares = dispatch_shares(rows, engine_ops)
    total = sum(r["cycles"] for r in shares)
    keep = shares if len(shares) <= top else sorted(
        sorted(shares, key=lambda r: -r["cycles"])[:top],
        key=lambda r: r["dispatch_id"])
    kept_ids = {r["dispatch_id"] for r in keep}
    print(f'{"id":>4} {"operator":<30} {"kernel op":<14} {"cycles":>13} {"ms":>8} '
          f'{"% of frame":>10}  ran on')
    for r in keep:
        print(f'{r["dispatch_id"]:>4} {r["name"][:30]:<30} {r["op"]:<14} '
              f'{r["cycles"]:>13,} {r["cycles"] / clock_mhz / 1000:>8.2f} '
              f'{r["pct"]:>9.2f}%  {r["where"]}')
    rest = [r for r in shares if r["dispatch_id"] not in kept_ids]
    if rest:
        c = sum(r["cycles"] for r in rest)
        print(f'{"":>4} {f"the other {len(rest)} dispatches":<30} {"":<14} '
              f'{c:>13,} {c / clock_mhz / 1000:>8.2f} {100.0 * c / total:>9.2f}%')
    print(f'{"":>4} {"whole frame":<30} {f"{len(shares)} dispatches":<14} '
          f'{total:>13,} {total / clock_mhz / 1000:>8.2f} {100.0:>9.2f}%')


def show_operator_rollup(rows: list[dict], clock_mhz: float = PROFILE_CLOCK_MHZ,
                         engine_ops: tuple = ENGINE_OPS) -> None:
    """The same profile summed by kernel op, largest first.

    Per dispatch the profile says which layer is expensive. Summed by op it says which
    *kind* of arithmetic the model spends its time in, which is the question a kernel author
    asks: twenty-four cheap dispatches of one op can outweigh one expensive dispatch of
    another.
    """
    if not rows:
        print("Nothing to roll up: the console carried no per-dispatch rows.")
        return
    shares = dispatch_shares(rows, engine_ops)
    total = sum(r["cycles"] for r in shares)
    by: dict[str, dict] = {}
    for r in shares:
        e = by.setdefault(r["op"], {"n": 0, "cycles": 0, "where": r["where"]})
        e["n"] += 1
        e["cycles"] += r["cycles"]
    print(f'{"kernel op":<16} {"calls":>6} {"cycles":>13} {"ms":>8} {"% of frame":>10}  ran on')
    for op, e in sorted(by.items(), key=lambda kv: -kv[1]["cycles"]):
        print(f'{op:<16} {e["n"]:>6} {e["cycles"]:>13,} '
              f'{e["cycles"] / clock_mhz / 1000:>8.2f} '
              f'{100.0 * e["cycles"] / total:>9.2f}%  {e["where"]}')


def _trim_ticks(ax, widest: float) -> None:
    """Drop the x ticks that sit past the data.

    The axis is stretched beyond the longest bar to leave room for that bar's label, and
    matplotlib fills the empty stretch with ticks -- a `120%` and a `140%` on an axis of
    shares, which is a label that cannot be true.
    """
    ax.set_xticks([t for t in ax.get_xticks() if t <= widest * 1.15])


def dispatch_contribution_figure(rows: list[dict], model: str = "", top: int = 12,
                                 clock_mhz: float = PROFILE_CLOCK_MHZ,
                                 engine_ops: tuple = ENGINE_OPS):
    """Each dispatch's share of the frame, and the same shares summed by kernel op.

    Two panels, because "which layer is expensive" and "which op is expensive" are two
    questions and a network answers them differently. Both panels are shares of the SAME
    total -- the sum of the per-dispatch cycle counts the board printed -- so a bar in one
    panel is comparable with a bar in the other.

    A dispatch is drawn in the colour of the core that ran it. When nothing reached the
    engine there is one colour and no legend: the title says what ran.
    """
    import matplotlib.pyplot as plt

    if not rows:
        raise ValueError("no dispatch rows to draw -- the console carried none")
    shares = dispatch_shares(rows, engine_ops)
    total = sum(r["cycles"] for r in shares)
    ms = total / clock_mhz / 1000
    on_engine = sum(1 for r in shares if r["where"] == "engine")
    colour = {"engine": S2, "first core": S1}
    label = f"{model} " if model else ""

    biggest = sorted(shares, key=lambda r: -r["cycles"])[:top]
    bars = [(f'{r["name"][:34]}\n({r["op"]})', r["pct"], colour[r["where"]], r["cycles"])
            for r in biggest]
    covered = sum(r["pct"] for r in biggest)

    by: dict[str, list] = {}
    for r in shares:
        e = by.setdefault(r["op"], [0, 0, r["where"]])
        e[0] += 1
        e[1] += r["cycles"]
    ops = sorted(by.items(), key=lambda kv: -kv[1][1])

    fig, (a, b) = plt.subplots(1, 2, figsize=(11.4, max(4.2, 0.42 * max(len(bars), len(ops)) + 1.6)),
                               gridspec_kw={"width_ratios": [1.15, 1.0]})

    # -- panel A: the individual dispatches -------------------------------------------
    ys = list(range(len(bars)))[::-1]
    widest = max(w for _, w, _, _ in bars)
    for y, (name, pct, col, cyc) in zip(ys, bars):
        a.barh(y, pct, height=0.62, color=col, zorder=3)
        a.annotate(f"{pct:.2f}%   {cyc:,}", (pct, y), textcoords="offset points",
                   xytext=(6, 0), va="center", fontsize=8, color=INK)
    a.set_yticks(ys)
    a.set_yticklabels([n for n, _, _, _ in bars], fontsize=7.5)
    a.set_xlim(0, widest * 1.62)
    a.set_ylim(-0.7, len(bars) - 0.3)
    a.set_xlabel("% of the frame's cycles", fontsize=8.5, color=INK2)
    a.set_title(f"{label}by dispatch"
                + ("" if len(bars) == len(shares)
                   else f": the {len(bars)} most expensive, {covered:.1f}% of the frame"),
                fontsize=10, color=INK, loc="left", pad=8)
    _axes_style(a)
    _trim_ticks(a, widest)

    # -- panel B: the same shares, summed by kernel op ---------------------------------
    ys = list(range(len(ops)))[::-1]
    widest = max(e[1] for _, e in ops) * 100.0 / total
    for y, (op, (n, cyc, where)) in zip(ys, ops):
        pct = 100.0 * cyc / total
        b.barh(y, pct, height=0.62, color=colour[where], zorder=3)
        calls = "call" if n == 1 else "calls"
        b.annotate(f"{pct:.2f}%   {n} {calls}", (pct, y), textcoords="offset points",
                   xytext=(6, 0), va="center", fontsize=8, color=INK)
    b.set_yticks(ys)
    b.set_yticklabels([op for op, _ in ops], fontsize=8)
    b.set_xlim(0, widest * 1.58)
    b.set_ylim(-0.7, len(ops) - 0.3)
    b.set_xlabel("% of the frame's cycles", fontsize=8.5, color=INK2)
    b.set_title(f"{label}by kernel op, all {len(shares)} dispatches",
                fontsize=10, color=INK, loc="left", pad=8)
    _axes_style(b)
    _trim_ticks(b, widest)

    if on_engine:
        handles = [plt.Rectangle((0, 0), 1, 1, color=S1),
                   plt.Rectangle((0, 0), 1, 1, color=S2)]
        b.legend(handles, ["first core (MBP kernels)", "second core (RoCC engine)"],
                 frameon=False, fontsize=8, loc="lower right")
    fig.suptitle(f"{len(shares)} dispatches, {total:,} cycles, {ms:,.2f} ms at "
                 f"{clock_mhz:g} MHz"
                 + (f" -- {on_engine} of them on the engine" if on_engine
                    else " -- all of them on the first core"),
                 fontsize=9.5, color=INK2, x=0.01, ha="left", y=0.995)
    fig.tight_layout(rect=(0, 0, 1, 0.965))
    plt.close(fig)   # the inline backend auto-shows a live figure, and
                     # returning it renders a second copy
    return fig


# --------------------------------------------------------------------------------------
# Unit 5: scheduling the speech encoder, and the recorded two-model comparison.
#
# 5.1 and 5.2 schedule ONE model -- Moonshine's encoder over a 2 s window, the same 129
# dispatches Unit 2 ran on the attendee's card. It is worth scheduling because its work
# lands on BOTH machines: 39 dispatches are engine kernels on hart 1 and 90 are MBP
# kernels on hart 0, so the two lanes can overlap and the schedule's duration is below
# the sum of its costs. The detector cannot teach that -- all 8 of its dispatches are
# hart-0 kernels in a chain, so its schedule IS the sum and there is nothing to place.
#
# 5.3 reads a recorded two-model result and needs no solver, no XPU-RT and no board.
# --------------------------------------------------------------------------------------

#: The encoder workload 5.1 solves, and the artifacts XPU-RT names after it.
ENC_SPEC = "data/toplevel/networks_pynqz1_enc_w20.json"
ENC_SCHEDULE = XPURT_SCHEDULE

#: The makespan the recorded solve of that workload reached, in ms.
ENC_MAKESPAN_MS = 8280.64

#: Where 5.2 writes the profile tree built from the attendee's own board, and the spec
#: and schedule that read it.
ENC_MEASURED_DIR = Path.home() / "work" / "measured_encoder"
ENC_MEASURED_SPEC = "networks_encoder_measured_on_your_board.json"
ENC_MEASURED_SCHEDULE = "networks_encoder_measured_on_your_board_cpsat_profiled"


def _enc_spec(rel: str = ENC_SPEC) -> dict | None:
    root = xpurt_root()
    if root is None or not (root / rel).exists():
        return None
    return json.loads((root / rel).read_text())


def _spec_impls(spec: dict) -> dict[str, str]:
    """`CPU_P#0` -> `hart0_pext`, for every machine the spec declares.

    XPU-RT names a machine one way in a dispatch graph (`CPU_P#0`) and another in the
    profile paths (`hart0_pext`). Both names come out of the spec, so the mapping is
    read from it rather than hard-coded.
    """
    out = {}
    for kind, n in spec.get("hardware", {}).get("machines", {}).items():
        impl = spec.get("hardware", {}).get("profile_hw", {}).get(kind)
        for i in range(int(n)):
            out[f"{kind.upper()}#{i}"] = impl
    return out


def _enc_home(spec: dict) -> dict[int, str]:
    """dispatch id -> the implementation that actually ran it.

    The dispatch graph names the machines that did NOT run a dispatch, in
    `infeasible_machines`; whatever is left is the one that did.
    """
    root = xpurt_root()
    net = next(iter(spec["networks"]))
    deps = spec["networks"][net]["dispatch_deps_path"]
    path = Path(deps) if os.path.isabs(deps) else root / deps
    if not path.exists():
        return {}
    graph = json.loads(path.read_text())
    impls = _spec_impls(spec)
    home = {}
    for info in graph["dispatches"].values():
        left = [m for m in impls if m not in set(info.get("infeasible_machines", []))]
        if len(left) == 1:
            home[int(info["id"])] = impls[left[0]]
    return home


#: Machine ids as XPU-RT spells them, and as the tutorial names them to a reader.
_LANE_NAMES = {"CPU_P#0": "hart 0, P-extension", "CPU_E#0": "hart 1, engine"}


def show_encoder_schedule(name: str = ENC_SCHEDULE) -> None:
    """What the solve found: where the work went, and what the overlap was worth.

    Reads the schedule XPU-RT just wrote, so every number here is that solve's own.
    """
    root = xpurt_root()
    if root is None:
        print("No XPU-RT tree on this machine, so there is no schedule to read.")
        return
    sched = root / "schedules" / f"scheduled_{name}.json"
    if not sched.exists():
        print(f"No schedule at {sched.name} yet -- run the solve cell above first.")
        return
    ds = list(json.loads(sched.read_text())["dispatches"].values())
    m = solved_metrics(name)
    lanes: dict[str, list] = {}
    for d in ds:
        lanes.setdefault(d["hardware_target"], []).append(d)
    serial = sum(d["duration"] for d in ds)
    mk = float(m["makespan_ms"])
    cp = float(m.get("critical_path_ms", 0.0))

    def row(label, val, note=""):
        print(f'{label:<26} {val:>12}   {note}')

    row("dispatches", f"{len(ds):,}",
        ", ".join(f'{len(v)} on {_LANE_NAMES.get(k, k).split(",")[0]}'
                  for k, v in sorted(lanes.items(), reverse=True)))
    row("one lane, end to end", f"{serial:,.2f} ms", "every dispatch serialised")
    row("dependency chain", f"{cp:,.2f} ms", "the longest path: no schedule is shorter")
    row("this schedule", f"{mk:,.2f} ms", f"{serial - mk:,.2f} ms below one lane")
    print()
    for k, v in sorted(lanes.items(), reverse=True):
        row(f"{_LANE_NAMES.get(k, k)} busy", f'{sum(d["duration"] for d in v):,.2f} ms', "")
    row("cross-machine handoffs", f'{m.get("cross_device_transitions", 0):,}', "")
    row("late dispatches", f'{m.get("op_deadline_miss_count", 0):,}',
        "this workload carries no deadline")


def write_measured_encoder_profile(rows: list[dict]) -> Path | None:
    """Rewrite the encoder workload's per-dispatch costs with your board's cycles.

    The scheduler reads costs as a `results.csv` per machine, one row per dispatch.
    This copies the two shipped files and replaces the cost of every MEASURED row with
    the cycles your board reported for that dispatch id. Both files are rewritten,
    because both machines really ran work: 90 dispatches on the first core and 39 on the
    engine, and your board timed all of them.

    The rows that are not timings are carried over untouched. A dispatch's cell on the
    machine that did not run it records that the machine cannot run it -- an absent
    opcode, or a kernel nothing has ever timed there -- and no board run can measure
    that. Everything else in the workload is reused: the dispatch graph, the machine
    model, the two cores' instruction sets.

    Returns the workload spec to solve, or None with the reason printed.
    """
    import csv as _csv

    if not rows:
        print("Nothing to convert: the console carried no per-dispatch rows.")
        return None
    root = xpurt_root()
    if root is None:
        print("Not written: no XPU-RT checkout on this machine, so there is no profile "
              "tree to re-cost and no solver to read it.")
        return None
    spec = _enc_spec()
    if spec is None:
        print(f"Not written: this XPU-RT checkout does not carry {ENC_SPEC}, so there is "
              "no workload to re-cost.")
        return None
    home = _enc_home(spec)
    if not home:
        print("Not written: the workload's dispatch graph is not in this checkout, so "
              "which machine ran which dispatch cannot be read.")
        return None

    cycles = {r["dispatch_id"]: r["cycles"] for r in rows}
    net = next(iter(spec["networks"]))
    prof = spec["hardware"]["profile"]
    ENC_MEASURED_DIR.mkdir(parents=True, exist_ok=True)
    wrote = 0
    for kind, impl in spec["hardware"]["profile_hw"].items():
        src = _profile_csv(spec, kind)
        if src is None:
            print(f"Not written: the shipped tree has no {impl} file to re-cost.")
            return None
        with open(src, newline="") as fh:
            rd = _csv.DictReader(fh)
            fields, out = list(rd.fieldnames or []), list(rd)
        n = 0
        for r in out:
            did = int(r["dispatch_id"])
            if r.get("cell_class") != "measured" or did not in cycles:
                continue      # an exclusion, not a timing: carried over unchanged
            c = cycles[did]
            ms = c / PROFILE_CLOCK_MHZ / 1000.0
            r["mean_time"], r["mean_unit"] = f"{ms:.9f}", "ms"
            r["mean_time_ns"] = f"{ms * 1e6:.3f}"
            r["cycles"], r["source"] = str(c), "board_measured_on_your_card"
            n += 1
        dst = (ENC_MEASURED_DIR / "profile" / impl / prof["target"] / net
               / f"{net}.int8" / prof.get("topo_tag", "topo_0") / "results.csv")
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, "w", newline="") as fh:
            w = _csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(out)
        print(f"  {impl:<13} {n:>3} of {len(out)} rows re-costed from your board  {dst}")
        wrote += n

    if wrote != len(cycles):
        print(f"  NOTE: your console carried {len(cycles)} dispatches and {wrote} rows "
              f"were re-costed. A dispatch the workload does not hold is ignored.")
    spec["hardware"]["profile"]["gen_root"] = str(ENC_MEASURED_DIR)
    for info in spec["networks"].values():
        deps = info.get("dispatch_deps_path", "")
        if deps and not os.path.isabs(deps):
            info["dispatch_deps_path"] = str(root / deps)
    path = ENC_MEASURED_DIR / ENC_MEASURED_SPEC
    path.write_text(json.dumps(spec, indent=2))
    print(f"  workload to solve: {path}")
    return path


def compare_measured_encoder_makespan() -> None:
    """The recorded schedule's duration beside the one your board's costs produced."""
    shipped = _enc_spec()
    path = ENC_MEASURED_DIR / ENC_MEASURED_SPEC
    mine = json.loads(path.read_text()) if path.exists() else None
    if shipped is None or mine is None:
        print("No measured solve to compare -- the cells above did not produce one.")
        return
    try:
        a, b = solved_metrics(ENC_SCHEDULE), solved_metrics(ENC_MEASURED_SCHEDULE)
    except (FileNotFoundError, OSError) as exc:
        print(f"One of the two solves has written no metrics file yet ({exc}). Run 5.1's "
              "solve and the solve above, then this cell.")
        return
    def total(spec: dict) -> int:
        # Each machine's file carries cycle counts only for the dispatches that machine
        # ran, so the two files add up to the frame without counting anything twice.
        return sum(sum(_csv_cycles(_profile_csv(spec, k)).values())
                   for k in spec["hardware"]["profile_hw"])

    was, now = total(shipped), total(mine)
    for label, m, cyc in (("recorded profile", a, was), ("your board", b, now)):
        print(f'{label:<18} makespan {m["makespan_ms"]:>9.2f} ms   '
              f'{cyc:>12,} cycles   {m.get("cross_device_transitions", 0):>3} handoffs   '
              f'late {m.get("op_deadline_miss_count", 0)}')
    if was:
        print(f'\nYour board ran the 129 dispatches in {now - was:+,} cycles, '
              f'{100.0 * (now - was) / was:+.3f}% of the recorded profile.')
    print(f'The schedule solved from your costs is '
          f'{float(b["makespan_ms"]) - float(a["makespan_ms"]):+,.2f} ms against the '
          f'recorded one.')


# --------------------------------------------------------------------------------------
# 5.3: the recorded two-model comparison. No solver, no XPU-RT, no board.
# --------------------------------------------------------------------------------------

#: The recorded two-model schedules 5.3 reads. SignDetLite periodic at 1,000 ms with
#: periodic stripping on; Moonshine non-periodic and carrying no deadline.
SCHEDULE_GOLDEN = "schedule_two_models.json"

#: The workload those schedules were solved from, and where 5.4 writes the copy it
#: solves -- the shipped file with the contention model switched off.
TWO_MODEL_SPEC = "data/toplevel/networks_pynqz1_coloc2m_sdp_b4_T1000.json"
TWO_MODEL_DIR = Path.home() / "work" / "two_model"


def two_model_schedules(asset: str = SCHEDULE_GOLDEN) -> dict:
    """The recorded schedules for the detector and the speech model sharing one SoC."""
    for cand in (ASSETS / asset, repo_file(f"notebooks/assets/{asset}")):
        if cand and Path(cand).exists():
            return json.loads(Path(cand).read_text())
    raise SystemExit(f"This checkout does not ship {asset} -- the recorded schedules "
                     "live in the curated tree the seats carry.")


def show_policy_table(golden: dict) -> None:
    """Where each policy lands the speech model, and how many detector windows it broke."""
    print(f'{"policy":<16} {"speech finishes":>16}   {"late detector dispatches":>24}')
    for p in sorted(golden["policies"], key=lambda r: r["makespan_ms"]):
        flag = "on time" if p["deadline_misses"] == 0 else ""
        print(f'{plain_name(p["scheduler"]):<16} {p["makespan_ms"]:>13,.2f} ms   '
              f'{p["deadline_misses"]:>21,}   {flag}')
    print(f'\n{golden["dispatches_per_schedule"]:,} dispatches per schedule   '
          f'detector period {golden["period_ms"]:,.0f} ms x '
          f'{golden["num_instances"]} instances')


def _late_mask(g: dict, period: float) -> list[bool]:
    """Which packed dispatches are detector work that ends after its own window."""
    out = []
    for s, d, j in zip(g["start_ms"], g["duration_ms"], g["job"]):
        out.append(j > 0 and s + d > j * period + 1e-9)
    return out


def schedule_gantt_figure(golden: dict, drawn: tuple = ("heft", "edf")):
    """The two recorded schedules as Gantt charts: two machine lanes, one bar per dispatch.

    One panel per policy on a shared millisecond axis. The detector's four instances are
    drawn in the second slot colour and its late dispatches in the status colour, so the
    trade-off is visible: the fast policy overruns the detector's windows, and the
    on-time one pays for it in speech latency.
    """
    import matplotlib.pyplot as plt

    period, n_inst = golden["period_ms"], golden["num_instances"]
    labels = golden["machine_labels"]
    fig, axes = plt.subplots(len(drawn), 1, figsize=(9.6, 4.8), sharex=True)
    span = max(golden["drawn"][p]["makespan_ms"] for p in drawn) * 1.02

    for ax, pol in zip(axes, drawn):
        g = golden["drawn"][pol]
        late = _late_mask(g, period)
        _axes_style(ax)
        ax.grid(axis="x", color=GRID, lw=0.6, zorder=0)
        # The detector's windows, behind everything.
        for i in range(1, n_inst + 1):
            ax.axvline(i * period, color=GRID, lw=1.0, ls=(0, (4, 3)), zorder=1)
        # One broken_barh per (lane, class) so 2,285 bars draw as three calls a lane.
        for lane in (0, 1):
            y = 0.55 - lane * 0.42
            for cls, colour, z in ((0, S1, 2), (1, S2, 3), (2, BAD, 4)):
                bars = [(s, d) for s, d, m, j, lt
                        in zip(g["start_ms"], g["duration_ms"], g["machine"],
                               g["job"], late)
                        if m == lane and (2 if lt else (1 if j else 0)) == cls]
                if bars:
                    ax.broken_barh(bars, (y, 0.3), facecolors=colour, zorder=z)
        ax.set_yticks([0.70, 0.28])
        ax.set_yticklabels(labels, fontsize=8)
        ax.set_ylim(0.02, 1.02)
        ax.set_xlim(0, span)
        miss = g["deadline_misses"]
        ax.set_title(
            f'{plain_name(pol)} -- speech finishes at {g["makespan_ms"]:,.0f} ms, '
            + ("every detector window met" if not miss
               else f"{miss} detector dispatches late"),
            fontsize=9.5, color=INK, loc="left", pad=6)
        ax.axvline(g["makespan_ms"], color=INK2, lw=1.2, zorder=5)

    axes[-1].set_xlabel("milliseconds", fontsize=8.5, color=INK2)
    handles = [plt.Rectangle((0, 0), 1, 1, fc=c) for c in (S1, S2, BAD)]
    fig.legend(handles, ("speech model", "detector, on time", "detector, late"),
               loc="upper left", fontsize=8, frameon=False, ncol=3,
               bbox_to_anchor=(0.215, 1.0))
    # Explicit margins rather than tight_layout: the lane labels are wide and the
    # figure legend sits outside every axes, which tight_layout cannot account for.
    fig.subplots_adjust(left=0.215, right=0.98, top=0.86, bottom=0.12, hspace=0.46)
    plt.close(fig)   # the inline backend auto-shows a live figure, and
                     # returning it renders a second copy
    return fig


def solve_two_model_cell(policy: str = "edf") -> None:
    """Solve ONE policy of the two-model workload here, and check it against the record.

    A list policy needs no CP-SAT and no `ortools`: only the XPU-RT checkout and the
    workload's own profile tree. The recorded comparison above carries the whole result
    without either.
    """
    root, rel = xpurt_root(), TWO_MODEL_SPEC
    if root is None:
        print("Not run: no XPU-RT checkout on this machine. The comparison above "
              "carries the whole result without one.")
        return
    if not (root / rel).exists():
        print(f"Not run: this XPU-RT checkout does not carry {rel}.")
        return
    # Solve a copy with the memory-contention model switched off, which is the basis
    # the recorded comparison is on. Left to the shipped workload the answer would
    # depend on whether this checkout happens to carry the contention measurement, and
    # a checkout without it falls back silently rather than saying so.
    spec = json.loads((root / rel).read_text())
    spec.setdefault("contention", {})["enabled"] = False
    for info in spec["networks"].values():
        deps = info.get("dispatch_deps_path", "")
        if deps and not os.path.isabs(deps):
            info["dispatch_deps_path"] = str(root / deps)
    gen = spec.get("hardware", {}).get("profile", {}).get("gen_root")
    if gen and not os.path.isabs(gen):
        spec["hardware"]["profile"]["gen_root"] = str(root / gen)
    TWO_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    path = TWO_MODEL_DIR / Path(rel).name
    path.write_text(json.dumps(spec, indent=2))

    r = sh(f"cd {root} && XPURT_CPSAT_WORKERS=1 {xpurt_python() or sys.executable} "
           f"scripts/run_xpurt_schedule.py --networks-json {path} "
           f"--scheduler {policy} --profiled", timeout=900, quiet=True)
    for line in r.stdout.splitlines():
        if "makespan_us" in line:
            print(line.strip())
    rec = next((p for p in two_model_schedules()["policies"]
                if p["scheduler"] == policy), None)
    if rec:
        print(f'the record says makespan {rec["makespan_ms"]:,.2f} ms, '
              f'{rec["deadline_misses"]} late detector dispatches')
