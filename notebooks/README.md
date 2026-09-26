# Attendee notebooks

`iiswc_tutorial.ipynb` is the published attendee page — the tutorial website's
`src/data/instructions.ts`, which lives in a separate repository — as a notebook, for the
per-seat JupyterLab interface.

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
| 0.2 | `ssh -i <key> xilinx@10.42.0.N` from the room WiFi | the board finds you; `lab.board_status()` asks |
| 0.4 | the board looks up `aws-N.iiswc` | superseded — you are already on the instance |
| 0.5 | the `aws_*.sh` relay, plus two fixes about the key's path and mode | the agent's ten verbs; no key fixes, because there is no key |
| 1.4 | `aws_run.sh` — pull, load, console, in one | `board_put` → `run` → `get`, over the tunnel |

0.4 and 0.5 keep a note explaining what they were and why they are gone: useful orientation
for anyone who saw the page. It is only the *instructions to act* that were removed.

**The page needs the same correction and has not had it.** It lives in a different
repository — the tutorial website — and this lab did not edit it. The problem is bigger
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
| `mb_lab/` | the ModelBlaster + LLM lab (`docs/MB_ATTENDEE.md`). Four generated notebooks (the lab, and the same lab done with the tools' own commands, each with a solved copy) plus their helpers and tools. See its README. |

## What is here, and what the board half needs that is not

`lab.board()` reaches a card through `board_link.py` — the instance-side half of the
board's forced command, one protocol with the card's `tunnel_agent.sh`. **Neither is in
this repository**, and neither is the key they use (`~/.ssh/iiswc-board-agent`, or
`$IISWC_BOARD_KEY`): a key is a credential and never travels in a repository. So in a
fresh clone `lab.board()` answers `stub`, which is a designed state and not a failure —
`_board_link()` searches `$IISWC_BOARD_LINK`, beside this module, the working directory,
`~/tut/fpga/pynq-z2/host`, `~/iiswc-tutorial/fpga/pynq-z2/host` and `/opt/iiswc/host`, finds
nothing, and says so. **It never invents
output: a non-`connected` result has stdout `None`.** Everything that runs on the instance
— the builds, the kernel gate, the trace unpack, the scheduling solve — works as it is.

Put `board_link.py` on one of those paths and install the key, and the same cells drive a
real card with nothing else changed. That is the point of routing every board call through
one function.

```bash
python3 notebooks/tools/build_notebook.py     # regenerate after editing the generator
```

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
