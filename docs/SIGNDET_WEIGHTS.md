# SignDetLite's weights: what is here, what is not, and what runs without them

`scripts/90_b156_tacit_window.sh` — the dual-core TACIT demo — puts a **sign detector**
(SignDetLite) on hart 0 and a **keyword spotter** on hart 1, traces both from the reset
vector, and merges the two lanes into one timeline. Everything that lab needs is in this
repository **except the detector's trained weights**.

This page says why, what ships instead, which gates mean what in each mode, and what an
operator has to do to put the real weights on a machine.

---

## 1. Why the weights are not here

SignDetLite is trained by `fpga/pynq-z2/modelblaster/signdet/train.py` on a set that
`make_data.py` builds from two sources:

* **GTSDB** — the German Traffic Sign Detection Benchmark, distributed as `FullIJCNN2013`:

  > S. Houben, J. Stallkamp, J. Salmen, M. Schlipsing and C. Igel, *"Detection of Traffic
  > Signs in Real-World Images: The German Traffic Sign Detection Benchmark"*, International
  > Joint Conference on Neural Networks (IJCNN), 2013.

  It supplies the real road scenes, the real sign optics, and — the part that matters most
  for this network — the real **negatives**, including the other 41 sign classes.

* **US-style composites** that this project draws itself, over backgrounds it also generates.

**We could not establish a licence for GTSDB.** Its canonical site does not resolve from our
network and the mirrors we found state no licence terms. We therefore do not publish the
weights that were trained on it — not in this repository, not on a model host, and not behind
a URL this repository knows about. That means all of the following are absent, deliberately:

| absent | why |
|---|---|
| `signdet_b144.pt` | the trained checkpoint itself |
| the lowered `gen/` tree's `weights.c` / `weights.h` values | the same numbers, quantised |
| `model.c`'s baked requantisation constants | derived from the trained weights — **and from the calibration count**, see §6 |
| `test_golden.bin` | a golden computed *by* the trained model |
| any fetch URL for the above | a URL would be publication |

GTSDB itself is not redistributed here either. If you want to train your own, obtain the
dataset **from its own source**; its terms are the ones that apply to it and this repository
makes no claim about them.

The same applies to GTSRB (the *recognition* benchmark) which the separate grey-scale
classifier labs use: `scripts/82_sign_host_tests.sh` has **no default dataset path** and
refuses to run until you point `MODELBLASTER_GTSRB_DATA` or `--data` at a copy you obtained
yourself.

> **Gap, stated plainly:** this repository has no `LICENSE` and no `NOTICE` file. Most source
> files carry `SPDX-License-Identifier: Apache-2.0`, but there is no top-level licence text
> and no third-party notice file to attribute datasets and dependencies in one place. That is
> a real gap and this page is not a substitute for it.

---

## 2. What ships instead: a deterministic random-weight model

`fpga/pynq-z2/modelblaster/signdet/random_weights.py` builds a SignDetLite checkpoint with

* **the same architecture** — `signdet/model.py`, unmodified;
* **the same tensor shapes** — the generated `weights.h` is byte-identical to the real
  model's;
* **the same interface quantisation scales** — input `1/127`, output `1/127`, so
  `SIGN_IN_SCALE_RECIP=127` and `SD_OUT_SCALE_PPB=7874016` stay correct and the board's
  percentages are decoded on the right grid.

**The seed is 144**, after Lab B144, and it is the only seed this repository uses.
`--seed` takes another; whichever ran is recorded in the manifest.

Determinism is **numpy's PCG64** (`np.random.default_rng`), not `torch.manual_seed`: NEP 19
pins that stream for the life of the API, so two clones with different PyTorch builds get
identical tensor values. Weights and biases are drawn from
`U(-1/√fan_in, +1/√fan_in)` — exactly `torch.nn.Conv2d`'s own initialisation family, so the
activation magnitudes the quantiser observes are in the range a freshly-initialised
SignDetLite really has.

**One deliberate deviation.** A freshly-initialised head produces logits near zero, so its
softmax never approaches 1.0 and the observed output scale would land near `0.4/127` — which
would make every percentage the board prints wrong by a factor nobody would notice. The head
is therefore **gained by the smallest power of two that makes float32 softmax saturate to
exactly 1.0** somewhere on the calibration set (in practice ×128). The gain and the resulting
maximum are both in the manifest. `scripts/84` then *gates* both scales after lowering: if
either misses, it refuses rather than shipping a silently wrong decode.

The calibration frames are synthetic too — sums of a few low-frequency sinusoids plus grain,
stretched to fill `[0, 255]` — because white noise gives a conv stack almost no spatial
structure and collapses every observed range.

### What random mode demonstrates, and what it cannot

| demonstrates | because |
|---|---|
| **the tracing mechanism** — two from-reset TACIT lanes, sized from a rate measured on the hardware's own `TR_SK_DMA_COUNT`, neither overrunning its region nor the other lane | none of it depends on what the weights are |
| **the scheduling result** — both lanes busy for the whole wall-clock window, no lane reduced to a recording of an idle hart | the workload's *cost* is set by the graph's shape, which is identical |
| **the lane convergence** — both lanes' model work ending at the same cycle by construction | `DUO_WINDOW_MS` bounds both harts, not the work |
| the whole compiler flow — quantisation, kernel selection, codegen, the guest build | the IR has the same ops, and the same curated kernels are picked |
| the per-inference cost — 7,532,544 MAC, ~240 ms at 40 MHz | the MAC count is a property of the architecture |
| **cannot: detection correctness** | a random network has no detection ability, full stop |

---

## 3. Which gates are meaningful in which mode

`scripts/90` runs seven gates. **Exactly one depends on the weights.**

| gate | what it asserts | random | real |
|---|---|---|---|
| 1 LANES | ≥10 distinct function names per lane; hart 0 running the detector, not the harness; the `[hw]`/`[sw]` split derived from the ELF matches the lanes | **gates** | gates |
| 2 BUFFERS | `COUNT[h] ≤ span[h]` and `base[1] ≥ base[0]+COUNT[0]`, from the board's own registers — the sink has no limit register, so an overrun is reported by nothing | **gates** | gates |
| 3 COVERAGE | both lanes' first slice is in the reset vector / early boot, not `main()` | **gates** | gates |
| 4 REPLAY | 8/8 decisions match, 8/8 tensors match, max &#124;d&#124; = 0 against a **host** run of the model | **NOT APPLICABLE** | gates |
| 5 RATE | measured bytes per core cycle per hart | **gates** | gates |
| B156 busy | every bucket of the window busy on **both** lanes | **gates** | gates |
| B156 converge | the two lanes' model work ends together | **gates** | gates |

Gate 4 is **not silently passed and not counted as a failure.** With random weights
`scripts/lib/b153_gates.py` still replays all eight frames, still prints every row, and then
reports:

```
  => GATE 4 NOT APPLICABLE -- random weights.
     The 8 frames ran and the board answered for all 8; the answers are compared
     against a HOST run of the REAL model, which is not the model in this image,
     so a mismatch here is the expected result and says nothing about the board.
```

and the overall line reads `RESULT: GATES 1, 2, 3, 5 PASS  (GATE 4 not applicable — random
weights)`. A random-weight run that fails 1, 2, 3 or 5 still **fails**.

The mode is not inferred from the result — a detector that disagrees with the baked answers
looks identical whether it is random or broken. It is **read** from
`out/signdet/gen/signdet_weights.json`, which `scripts/84` and `scripts/91` write, before
anything is built. A gen tree with **no** manifest is treated as **real**: that is the
direction that fails loudly rather than excusing a failure whose cause it cannot see.

---

## 4. The eight replay frames — ours, and shipped

`fpga/pynq-z2/modelblaster/signdet/bake/` holds `signdet_frames.{c,h,json}` (2.8 MB of
generated C). These are **our own HM01B0 captures on our own bench** — a hand holding a
printed prop sign under the lab's ceiling tubes — pulled off the board by
`scripts/85_cam_snap_pull.sh` and baked to C by `signdet/bake_live_frames.py`. No
third-party image is in them. A person is present, backlit to silhouette: a hand, a
shoulder, the back of a head. No face is legible in any frame.

They are shipped because they are the only claim about detection that does not need a person
at the bench, and they stay valid the moment real weights are installed. Each entry carries
the raw 105,624-byte Bayer frame exactly as the DMA delivers it, plus the **whole 192-byte
output tensor** the host-C build of the model produced for it — so a board that agrees on the
class but not on the tensor is still caught.

The filenames (`snap_015_PRIORITY`, …) are **not labels**: they record an older GTSRB
classifier's *wrong* guess, which is part of why this detector exists. `truth` is the hand
label.

---

## 5. Putting the real weights on a machine

The real model reaches a machine one way: **the tutorial image bakes the lowered tree onto
its own filesystem.** There is no download.

### Which private artefact embeds them — and what that costs it

§1 says which artefacts do **not** embed these weights, and that list is complete for anything
this project *publishes*. One artefact does embed them, and naming it is the point of this
subsection: pretending the numbers exist nowhere is how a copy gets shared by somebody who was
never told.

| artefact | embeds the trained weights | may it be shared |
|---|---|---|
| this repository, every branch | no | yes — that is what it is for |
| the tutorial notebooks / the published page | no | yes |
| a model host, an S3 object, any fetch URL | **does not exist** | — |
| **the private seat AMI** `iiswc-2026-seat-jupyter-2026-09-26-b190` (`ami-0d8605f3346cbee37`) and every later rebake of it | **yes**, at `/opt/iiswc/signdet`, installed into the gen tree by the script below | ***NEVER*** |

> ### An image that carries these weights must never be shared
>
> Not made public, not shared with another AWS account, not copied to a public snapshot — and
> the snapshots an image references are shareable *independently of it*, so they carry the same
> rule. The distinction is **publication, not baking**: a private image the account owns is not
> a published artefact; a shared one is. The image states this in its own description and tags,
> and on the instance at `/opt/iiswc/SIGNDET_WEIGHTS.txt`. Mechanism and evidence:
> `docs/TUTORIAL_SEAT_BUILD.md` §0 and §11 (private tree).

Two things follow that are easy to get wrong in the other direction:

* **Baking them is not a mistake to be corrected.** The demo needs a detector that detects; gate
  4 REPLAY is the only claim about detection that the tutorial can make, and it needs the real
  numbers. "Never bake a weight" was never the rule — *never publish one* is.
* **The install destination is still `out/signdet/gen`**, inside the image's clone and therefore
  inside a git worktree, and that is deliberate rather than an oversight: `out/` is git-ignored,
  `git check-ignore` is asked before anything is written (step 5 below), and a destination
  *outside* the worktree cannot be checked by it at all — `git check-ignore` on such a path exits
  128 with *"is outside repository"*, which is a refusal, not a pass. Invisible-to-git is the
  property that matters, and it is the one that is checked.

```
./scripts/91_signdet_install_model.sh --from /path/on/this/image/signdet
```

That script:

1. **refuses a URL.** There is no `--url`, no fetch, no socket. A value that looks like a
   remote is refused with an explanation rather than reinterpreted as a path.
2. checks the tree is a complete lowering (the fourteen files the guest links).
3. **verifies `SHA256SUMS`** — every listed file present and matching, *and* no unlisted file
   present, because `sha256sum --check` says nothing about a stray artefact.
4. gates that it is a SignDetLite lowering this guest can link: every op curated with no
   reference-C fallback, a 192-byte output, and the two guest scales.
5. **refuses any destination git can see.** `git check-ignore` is the authority; if the
   destination is not ignored, it stops rather than leaving an unpublishable tree where an
   `git add -A` would find it.
6. installs atomically into `out/signdet/gen` (keeping the previous tree at
   `…/gen.previous`) and writes `signdet_weights.json` with `weights_mode: real`, which flips
   gate 4 back to a gate.

Optional: `--calib <file.npy>` installs the numerical control set `scripts/86` wants, and
`--bake <dir>` installs a replay set baked against those weights instead of the tracked one.

**Whoever builds the image runs the manifest step once, at the source:**

```
./scripts/91_signdet_install_model.sh --from <dir> --write-manifest
```

### Operator checklist for an instance

1. Bake the lowered tree (`model.* kernels.* weights.* buffers.c kernel_picks.json
   footprint.json test_io.* test_input.bin test_golden.bin`, optionally `ir/graph.json` and
   `calib_X.npy`) into the image at a fixed path. **It never enters a git repository.**
   **Lower it with `--ncal 256`** (§6): the script defaults to 64, and 64 gives a different
   `weights.c`, `test_golden.bin` and `ir/graph.json` from the tree every lab number was
   measured against. Check `ir/graph.json` is `5e3ac070` before baking.
2. Generate `SHA256SUMS` beside it with `--write-manifest`, once.
3. On the instance: `./scripts/91_signdet_install_model.sh --from <that path>`.
4. Confirm `out/signdet/gen/signdet_weights.json` says `"weights_mode": "real"`.
5. Run the lab; gate 4 now gates.

---

## 6. Doing it yourself

If you have your own GTSDB copy and want to reproduce the real model:

```
python3 fpga/pynq-z2/modelblaster/signdet/make_data.py --gtsdb <your FullIJCNN2013> --out <ds>
python3 fpga/pynq-z2/modelblaster/signdet/train.py    --ds <ds> --out <run>
./scripts/84_signdet_lower.sh --ckpt <run>/signdet_b144.pt --calib <run>/calib_X.npy --ncal 256
```

**`--ncal 256` IS NOT OPTIONAL HERE, AND THE SCRIPT'S DEFAULT IS 64.** Until 2026-09-26 this
recipe omitted the flag, so following it produced a *correct* lowering of the same weights on a
**different requantisation grid** — and nothing said so. The int8 PTQ sets every activation scale
from what it observes on the calibration frames, so the count is an input to the model, not a
speed knob. Measured on the same checkpoint and the same `calib_X.npy`:

| `--ncal` | `weights.c` | `test_golden.bin` | `ir/graph.json` | is this the tree the labs were measured against? |
|---|---|---|---|---|
| 64 (the script's default) | `e3143ae1` | `c7cf47fa` | `70a0ac6d` | **no** |
| **256** | `d729a0a1` | `69727ae7` | `5e3ac070` | **yes** — 11 of 11 common files match |

Why it matters beyond tidiness: install a 64-frame tree with `scripts/91`, and GATE 4 REPLAY stops
being NOT APPLICABLE and starts comparing the board's answers against a `test_golden.bin` computed
on a grid the board's `model.c` was not built for. **The failure is silent** — the manifest
`signdet_weights.json` does not record `num_calibration`, and nothing in the script, the gates or
`kernel_picks.json` mentions it. Recording and gating the count is open work; this line is the
part that stops a reader reproducing the wrong tree today.

`$SIGNDET_WORK` (default `out/signdet_work`) is where every tool in `signdet/` looks for
training-side inputs; no tool in that directory carries an absolute path any more.

And to re-bake the replay frames against *your* weights, `scripts/87_signdet_live.sh` does it
from a directory of captures pulled by `scripts/85_cam_snap_pull.sh`.

---

## 7. The short version

* Random weights are the default and the demo runs end to end from a clone.
* Six of seven gates gate in both modes; **gate 4, REPLAY, is the only one that needs the
  real weights**, and in random mode it is reported *not applicable*, never *pass* and never
  *fail*.
* The real weights ship with the tutorial image and are installed by
  `scripts/91_signdet_install_model.sh` from a local directory, with a checksum manifest and
  no network.
* **That image is therefore the one artefact that must never be shared** — not public, not with
  another AWS account, not as a public snapshot. §5 names it.
* GTSDB belongs to its authors, is cited in `signdet/make_data.py`, and must be obtained from
  its own source.
