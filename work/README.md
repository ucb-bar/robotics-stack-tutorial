# Attendee notebooks

`iiswc_tutorial.ipynb` is the published attendee page —
`/scratch/dima/iiswc-site/src/data/instructions.ts` — as a notebook, for the per-seat
JupyterLab interface of `docs/TUTORIAL_INTERFACE_NOTES.md` §4e.

**It runs on the attendee's own AWS instance.** That is the whole point of the interface:
the attendee opens JupyterLab over their own internet with one shared passphrase, never
joins the room network, never holds an SSH key, and never types `ssh`. The board reaches
*them*, by holding a reverse tunnel open to the instance, so a card that drops its WiFi
reconnects instead of killing a session.

## The notebook and the page have diverged, on purpose

The published bench card describes the older flow. Four steps are therefore **re-expressed
here rather than transcribed**, and `tools/build_notebook.py` says so at the top:

| step | on the page | in the notebook |
|---|---|---|
| 0.2 | `ssh -i <key> xilinx@10.42.0.N` from the room WiFi | the board connects to the instance; `lab.board_status()` reports it |
| 0.4 | the board looks up `aws-N.iiswc` | superseded — you are already on the instance |
| 0.5 | the `aws_*.sh` relay, plus two fixes about the key's path and mode | the agent's ten verbs; no key fixes, because there is no key |
| 1.4 | `aws_run.sh` — pull, load, console, in one | `board_put` → `run` → `get`, over the tunnel |

**The notebook does not narrate the divergence to the attendee** (B183): 0.4 is one line
saying they are already on the instance, and 0.5 lists the verbs the card accepts. An
attendee needs the flow in front of them, not its history.

**The page needs the same correction and has not had it.** It lives in a different
repository (`/scratch/dima/iiswc-site`) and this lab did not edit it. The problem is bigger
than step 0.2: the page is served from the router at `10.42.0.1` and written to survive the
uplink dying, which assumes an attendee *on the room network* — and under §4e nobody is. So
its delivery mechanism is superseded along with its step 0.2. That is the page owner's call,
not something to quietly paper over in the notebook.

| file | what it is |
|---|---|
| `iiswc_tutorial.ipynb` | the notebook an attendee opens. **Generated — do not hand-edit.** |
| `tools/build_notebook.py` | the source of the notebook. Edit this, re-run it, commit both. |
| `iiswc_lab.py` | the helpers the notebook imports. Must sit beside the notebook. |
| `assets/` | anything pre-seeded so an attendee never watches a cold build. |

`board_link.py` must be on one of the paths `iiswc_lab._board_link()` searches, and the
key it uses (`~/.ssh/iiswc-board-agent`, or `$IISWC_BOARD_KEY`) must be present. Neither
is in this directory in the repository: `board_link.py` belongs beside `tunnel_agent.sh`
in `fpga/pynq-z2/host/`, and the key is a credential.

```bash
python3 notebooks/tools/build_notebook.py     # regenerate after editing the generator
```

**Prose rules for that file** (B183, after a rewrite that cut 27 % of the words): ordinary
sentences of varying length; the command, and what the screen should say. Explain the
technical content — the two harts and their extensions, what the trace encoder records,
what the solver optimises — and delete anything that explains the tutorial's own plumbing
(tunnels, keys, why a board reconnects, why the page differs). Headings name the task.
Statements of what does not work yet stay.

## The two helpers, and why there are exactly two

Several units are *about* where the computation happens, so a cell must never quietly move
work to whichever side is easier.

* `lab.sh("…")` runs on the instance. Always with a timeout.
* `lab.board("…")` runs on the card, and is the **only** way the notebook touches it.

`lab.board()` goes through one function, `iiswc_lab._board_link()`, which returns B176's
`BoardLink` from `fpga/pynq-z2/host/board_link.py` — the instance-side half of the board's
forced command. That file is **not copied here**: it and `tunnel_agent.sh` are one protocol
and must not drift. `_board_link()` looks beside this module, in the working directory, in
`~/tut/fpga/pynq-z2/host`, in `/opt/iiswc/host`, and at `$IISWC_BOARD_LINK`. **The seat AMI
has to put it on one of those paths.**

The card takes a fixed verb set, not commands — `ping status help ls put get bitstream run
camera mic` — because its key carries a forced command, so a broken-into instance cannot get
a shell on the board. `lab.board("status")` is the whole interface; `lab.board_put(path)`
pushes a file with its length and md5 declared.

States, and none of them fabricate output — a non-`connected` result has **stdout `None`**:

| state | meaning |
|---|---|
| `connected` | the board's own sshd answered and the agent replied |
| `stub` | `board_link.py` is not on this instance, so there is no transport at all |
| `offline` | `nothing is listening` (the card has not dialled in) or `tunnel is stale` |

**`offline` is two different things and the difference matters.** The tunnel port is bound by
*this instance's* sshd, which keeps accepting connections for as long as a dead session
survives — so a plain `connect()` returns success for a board that is gone. The probe waits
for the board's SSH identification string instead, because those bytes can only have come
from the card. Verified by standing a listener on 19022 that accepts and says nothing:
`connect()` succeeded, and the status cell said `board offline (tunnel is stale)`.

Boards in this fleet fail (card 27's sshd resets, card 14 never associates, card 21 reboots
itself), so every board call has a hard timeout and every instance-side unit proceeds
regardless.

## Committed without outputs

Outputs are cleared before commit: an executed notebook carries instance IPs and hostnames.
Expected output lives in markdown instead, quoted from the page's own 2026-09-23 bench run,
so an attendee can tell whether their run matched.

## Pre-seeded, so nobody watches a cold anything

| asset | why it is shipped |
|---|---|
| `assets/rocket_tacit_trace.perfetto.json.gz` | 96,680 B, 20.6x. One TACIT capture taken on silicon on 2026-09-16 (`out/rocket_tacit/`, 534,990 instructions, 2.021 bits/instruction). Unit 3's *capture* has no attendee sequence — the cards carry `0x5A5A0038`, TACIT needs `0x5A5A0039` — so the notebook ships the capture rather than pretending you can take one. |
| `assets/rocket_tacit_trace.meta.json` | its provenance, printed by `lab.unpack_trace()` so the notebook says out loud that you did not capture it. |
| `assets/b76_gate.expected.txt` | the kernel gate is 184 s. The run stays optional and the expected text is here to compare against. It also warns that the gate prints many `FAIL` lines *by design* — they are its poisoned control arms. |
| `assets/b156_lanes.json` | 8.9 KB, the measured B156 dual-hart lane table. Unit 3's extra section draws its figure from this. |

## Figures are generated, not embedded

Both charts are drawn at run time from committed data — `lab.b156_figure()` from
`assets/b156_lanes.json`, `lab.b157_figure()` from `expected/xpurt_coloc2m_b157.json` — so
they stay correct when the numbers change and cost the notebook nothing. **No PNG is
embedded anywhere.** Adding the two demos cost 8.9 KB of assets.

The one figure that cannot be regenerated is B157's per-cell Gantt pair
(`cpsat_dram_plain` vs `cpsat_dram_compact`): `b157_plots.py` needs each cell's own
schedule JSON, and the sweep's 132 schedule artifacts (~85 MB) are deliberately not
committed. Reproducing them is two 1,800 s CP-SAT solves. The renders are in the archive at
`b157/plots/native/`; the notebook carries the same result quantitatively in its own
figure instead.

Palette: categorical slots 1 and 2 (`#2a78d6`, `#eb6834`), validated together for all pairs
on a light surface (CVD ΔE 24.7 protan, normal-vision 33.6). Status colours are the reserved
pair and always carry a word as well, never colour alone. Light surface only — a notebook
renders a PNG and cannot follow the reader's theme, so the figures do not pretend to.

Unit 1's build is **not** pre-seeded: measured 23 s cold (`-p always`) on a `c6i.xlarge`.

## Timings measured on a tutorial instance, 2026-09-24

| step | wall |
|---|---|
| 1.2 `west build -p always` | 23.3 s |
| 2.1 `b76_gate.sh` | 183.9 s, `rc=0` |
| 4.1 XPU-RT CP-SAT solve | 2.9 s |

## Testing it

```bash
python3 notebooks/tools/build_notebook.py
jupyter nbconvert --to notebook --execute --allow-errors \
    --ExecutePreprocessor.timeout=900 --output /tmp/executed.ipynb notebooks/iiswc_tutorial.ipynb
```

Execute it somewhere disposable and **never commit the result** — an executed notebook carries
the instance's hostname. Executing it is not optional diligence: the first version of this
notebook collapsed every cell onto one line (nbformat wants each line to keep its `\n`) and
that is invisible in the JSON and in review. It only showed up as `SyntaxError` in a kernel.
