#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# INSTALL A LOWERED SignDetLite MODEL FROM A LOCAL DIRECTORY, and verify it on the way in.
#
#   ./scripts/91_signdet_install_model.sh --from /opt/iiswc/signdet
#   ./scripts/91_signdet_install_model.sh --from <dir> --bake <dir> --calib <file.npy>
#   ./scripts/91_signdet_install_model.sh --from <dir> --write-manifest   # image builders
#
# ==========================================================================================
# WHY THIS SCRIPT EXISTS, AND WHAT IT DELIBERATELY CANNOT DO
# ==========================================================================================
# SignDetLite's trained weights are GTSDB-derived and this project publishes no artefact
# that embeds them -- not the checkpoint, not the lowered weights.c, not a golden computed
# from one (docs/SIGNDET_WEIGHTS.md).  The demo's default is therefore a deterministic
# random-weight model that scripts/84_signdet_lower.sh builds from nothing.
#
# The real model reaches a machine ONE way: it is baked into the tutorial image, at a path
# on that image's own filesystem.  This script copies it from there into the place
# scripts/86, 87 and 90 read, and gates it.
#
# ON A SERVED MACHINE, INSTALL OUTSIDE THE CHECKOUT.  The default destination is inside this
# repository's ignored out/, which is right on a bench and WRONG on a tutorial seat: the seat
# serves ~/work over HTTPS behind one shared passphrase, `~/work/repo` is a symlink to the
# clone, and the weights are then a 200 OK away for every attendee (measured, B190).  Pass
#     --dest /opt/iiswc/signdet-gen
# and point the labs at it with SIGN_GEN.  Step 4/5 checks which case it is and says so.
#
# IT DOES NOT FETCH.  There is no URL, no S3 bucket, no model host, no --url flag and no code
# path that opens a socket.  --from must be a local directory, and a value that looks like a
# remote is refused rather than quietly reinterpreted.  That is not a convenience decision:
# a fetch URL for these weights would be publication, which is the thing being avoided.
#
# IT REFUSES TO WRITE ANYWHERE GIT CAN SEE.  The destination must be a path git ignores.  A
# tree whose licence could not be established must not become a commit by accident.
#
# ==========================================================================================
# WHAT A SOURCE DIRECTORY LOOKS LIKE
# ==========================================================================================
#   <from>/
#     SHA256SUMS            the manifest -- `sha256sum *` run in this directory
#     model.c model.h       ModelBlaster's generated skeleton
#     kernels.c kernels.h   the curated kernels this graph selected
#     weights.c weights.h   THE TRAINED WEIGHTS.  This is the file that is not publishable.
#     buffers.c footprint.json kernel_picks.json
#     test_io.h test_io.S test_input.bin test_golden.bin
#     [ir/graph.json]       optional; if present the two guest scales are re-derived from it
#     [calib_X.npy]         optional; scripts/86's numerical control set
#     [bake/]               optional; a replay set baked against THESE weights
#
# --write-manifest generates SHA256SUMS in the source directory and stops.  That is the
# command the person building the image runs once, so that every later install verifies.
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

FROM=""
DEST="$IISWC_OUT/signdet/gen"
BAKE_SRC=""
BAKE_DEST="$IISWC_OUT/signdet/bake"
CALIB_SRC=""
MANIFEST=""
WRITE_MANIFEST=0

# The files a lowered tree must have for the guest to link.  A tree missing any of them is a
# half-copied one, and the failure it produces later is a link error nobody reads as this.
REQUIRED=(model.c model.h kernels.c kernels.h weights.c weights.h buffers.c
          kernel_picks.json footprint.json test_io.h test_io.S test_input.bin
          test_golden.bin)

while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM="${2:?}"; shift 2 ;;
    --dest) DEST="${2:?}"; shift 2 ;;
    --bake) BAKE_SRC="${2:?}"; shift 2 ;;
    --bake-dest) BAKE_DEST="${2:?}"; shift 2 ;;
    --calib) CALIB_SRC="${2:?}"; shift 2 ;;
    --manifest) MANIFEST="${2:?}"; shift 2 ;;
    --write-manifest) WRITE_MANIFEST=1; shift ;;
    -h|--help) sed -n '2,45p' "$0"; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[ -n "$FROM" ] || die "--from <dir> is required: the directory on THIS machine holding the
       lowered model.  On a tutorial instance that is the path the image bakes it at; ask
       the image, not the network.  See docs/SIGNDET_WEIGHTS.md.
       With no real model, ./scripts/84_signdet_lower.sh gives you the random-weight one."

# NOT A URL, AND THE REFUSAL IS EXPLICIT.  Somebody will eventually paste a link here, and a
# script that silently treated it as a relative path would produce a confusing "no such
# directory" instead of the answer, which is that these weights are not published.
case "$FROM" in
  *://*|www.*)
    die "--from takes a LOCAL DIRECTORY, not a URL.  This script never fetches: the
       SignDetLite weights are GTSDB-derived and are not published anywhere by this
       project, so there is no address that serves them.  docs/SIGNDET_WEIGHTS.md." ;;
esac
[ -d "$FROM" ] || die "--from $FROM is not a directory"
FROM="$(cd "$FROM" && pwd)"

########################################################################################
if [ "$WRITE_MANIFEST" = 1 ]; then
  step "write the checksum manifest into the source tree"
  ( cd "$FROM" && find . -type f ! -name SHA256SUMS -printf '%P\n' | LC_ALL=C sort \
      | xargs -r sha256sum > SHA256SUMS )
  info "wrote $FROM/SHA256SUMS ($(grep -c . "$FROM/SHA256SUMS") files)"
  info "ship this file beside the model; every install verifies against it."
  exit 0
fi

########################################################################################
step "1/5  the source tree"
info "from $FROM"
missing=()
for f in "${REQUIRED[@]}"; do [ -f "$FROM/$f" ] || missing+=("$f"); done
[ "${#missing[@]}" -eq 0 ] || die "this is not a complete lowered tree -- missing: ${missing[*]}
       A gen tree is what scripts/84_signdet_lower.sh writes into out/signdet/gen."

########################################################################################
step "2/5  verify the checksum manifest"
[ -n "$MANIFEST" ] || MANIFEST="$FROM/SHA256SUMS"
[ -f "$MANIFEST" ] || die "no checksum manifest at $MANIFEST.
       An unverified model tree is exactly the thing this script exists to refuse: the
       weights are the part of this demo nobody can eyeball.  Generate one at the source
       with:  ./scripts/91_signdet_install_model.sh --from $FROM --write-manifest"
( cd "$FROM" && sha256sum --quiet --check "$MANIFEST" ) \
  || die "the model tree does not match $MANIFEST -- refusing to install it"
NLISTED=$(grep -c . "$MANIFEST")
info "$NLISTED file(s) verified against $MANIFEST"

# AND NOTHING EXTRA.  sha256sum --check says every LISTED file is right; it says nothing
# about a file that is present and unlisted, which is how a stray artefact gets installed
# with the model and then shows up in a build as an unexplained symbol.  In python rather
# than comm(1) because comm's answer depends on the collation the two sorts ran under, and a
# set difference that is silently empty under the wrong locale is worse than no check.
UNLISTED="$(python3 - "$FROM" "$MANIFEST" <<'PYEOF'
import os, sys
root, man = sys.argv[1], sys.argv[2]
listed = set()
for line in open(man, errors="replace"):
    line = line.rstrip("\n")
    if not line.strip():
        continue
    # `sha256sum` writes "<hex>  <path>"; the binary marker is "<hex> *<path>".
    _, _, name = line.partition(" ")
    listed.add(name.lstrip(" *"))
present = set()
skip = os.path.basename(man)
for dirpath, _dirs, files in os.walk(root):
    for f in files:
        rel = os.path.relpath(os.path.join(dirpath, f), root)
        if rel == skip:
            continue
        present.add(rel)
for x in sorted(present - listed):
    print(x)
PYEOF
)"
[ -z "$UNLISTED" ] || die "files present in $FROM but absent from $MANIFEST:
$(printf '       %s\n' $UNLISTED)
       Regenerate the manifest at the source, or remove them.  An unlisted file is an
       unverified file, and this script will not install one."
info "no unlisted files"

########################################################################################
step "3/5  is this a SignDetLite lowering this repo's guest can link"
PY="$ZCS/tools/miniforge3/envs/zephyr/bin/python"; [ -x "$PY" ] || PY=python3
"$PY" - "$FROM" <<'PYEOF'
import json, os, sys
d = sys.argv[1]
p = json.load(open(os.path.join(d, "kernel_picks.json")))["picks"]
for k in sorted(p):
    print("    %-18s %-20s %s" % (k, p[k].get("source"), p[k].get("algorithm")))
bad = [k for k in p if p[k].get("source") == "reference"]
if bad:
    sys.exit("reference-C fallback for %s -- this tree was not lowered against the curated "
             "kernels and would run soft float on the board" % ", ".join(sorted(bad)))
# generate_skeleton names it MODEL_<network>_OUTPUT_SIZE and aliases MODEL_OUTPUT_SIZE to
# it, so the literal is on the first line and not the second.  samples/signdet_live
# BUILD_ASSERTs the baked frames against this number; catching it here turns a build error
# nobody reads as "wrong model" into a refusal that says so.
import re
h = open(os.path.join(d, "model.h")).read()
m = re.search(r"#define\s+\w*OUTPUT_SIZE\s+(\d+)", h)
if m is None:
    sys.exit("model.h defines no OUTPUT_SIZE -- this is not a ModelBlaster skeleton")
if int(m.group(1)) != 192:
    sys.exit("the model's output is %s bytes, not 192 -- this is not an 8x8x3 SignDetLite "
             "head, and the baked replay frames and samples/signdet_live both assume 192"
             % m.group(1))
print("    output size 192 bytes  (8x8 grid x 3 classes)")
print("    every op curated, no reference-C fallback")
PYEOF

# The two scales the guest is compiled with.  Re-derived from the tree's own IR when it
# carries one; otherwise taken from a manifest the source already wrote; otherwise the
# repository default, which is what both the real model and the random one produce.
read -r IN_RECIP OUT_PPB <<EOF
$("$PY" - "$FROM" <<'PYEOF'
import json, os, sys
d = sys.argv[1]
recip, ppb = 127, 7874016
g = os.path.join(d, "ir", "graph.json")
if os.path.exists(g):
    j = json.load(open(g)); t = j["tensors"]
    recip = round(1.0 / float(t[j["input"]["tensor"]]["quant"]["scale"]))
    ppb = round(float(t[j["output"]["tensor"]]["quant"]["scale"]) * 1e9)
else:
    m = os.path.join(d, "signdet_weights.json")
    if os.path.exists(m):
        try:
            j = json.load(open(m))
            recip = int(j.get("in_scale_recip", recip)); ppb = int(j.get("out_scale_ppb", ppb))
        except (ValueError, TypeError):
            pass
print(recip, ppb)
PYEOF
)
EOF
info "scales  SIGN_IN_SCALE_RECIP=$IN_RECIP  SD_OUT_SCALE_PPB=$OUT_PPB"
[ "$IN_RECIP" = 127 ] || warn "the input scale is not 1/127; sign_pre.c is compiled with
       SIGN_IN_SCALE_RECIP=127 and will quantise onto a different grid than this model expects"

########################################################################################
step "4/5  where it is going -- and that nothing can publish it from there"
# THE ONE CHECK THAT MATTERS MOST HERE, AND IT IS NOW TWO QUESTIONS, NOT ONE.
#
#   (a) CAN GIT SEE IT?  These weights must never become a commit, and git check-ignore is the
#       authority on whether they can.
#   (b) CAN A SERVED FILE BROWSER SEE IT?  Measured on a tutorial seat (B190): the seat serves
#       ~/work over HTTPS behind ONE shared passphrase, `~/work/repo` is a SYMLINK to the 18 GB
#       clone, and jupyter_server follows it -- so `GET /api/contents/repo/out/signdet/gen/
#       weights.c` returned 200 and 319,313 bytes to an ordinary session.  A tree inside the
#       clone is git-invisible and PUBLICLY DOWNLOADABLE at the same time.  Git-ignored is
#       necessary and it is not sufficient.
#
# So a destination OUTSIDE every git worktree is the strongest answer to (a), not a way around
# it, and on a served machine it is the only answer to (b).  It gets its own branch here rather
# than being caught by check-ignore's error path: `git check-ignore <path outside the repo>`
# exits 128 with "is outside repository", which the old single `if !` read as "not ignored" and
# refused -- so the safest destination was the one thing this script could not be told to use.
mkdir -p "$(dirname "$DEST")"
DEST_ABS="$(cd "$(dirname "$DEST")" && pwd)/$(basename "$DEST")"
# Which worktree, if any, CONTAINS the destination -- asked of the destination's own directory,
# not of $IISWC_ROOT, because those are different questions the moment $DEST is an absolute path
# somewhere else.
DEST_WT="$(git -C "$(dirname "$DEST_ABS")" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$DEST_WT" ]; then
  info "$DEST_ABS is OUTSIDE every git worktree -- git cannot see it at all"
  info "  (no .gitignore rule is needed or consulted for a path no repository contains)"
elif git -C "$DEST_WT" check-ignore -q "$DEST_ABS" 2>/dev/null; then
  info "$DEST_ABS is inside $DEST_WT and is git-ignored (checked, not assumed)"
  warn "it is inside a CHECKOUT.  On a machine that serves that checkout -- a tutorial seat
       serves ~/work and ~/work/repo is a symlink to it -- the tree is downloadable by anyone
       with the seat's passphrase even though git cannot see it.  Prefer a destination outside
       the checkout entirely:  --dest /opt/iiswc/signdet-gen"
else
  die "$DEST_ABS is inside the git worktree $DEST_WT and is NOT ignored there.  Refusing to
       install: these weights must never be committed.  Either install OUTSIDE the worktree
       (--dest /opt/iiswc/signdet-gen, which this script now prefers), install under an
       ignored path such as out/, or add the destination to .gitignore first."
fi

########################################################################################
step "5/5  install"
# STAGED THEN MOVED.  A half-copied gen tree links against a mixture of two models, and the
# only symptom is wrong numbers.
TMP="$DEST.installing.$$"
rm -rf "$TMP"; mkdir -p "$TMP"
( cd "$FROM" && tar -cf - --exclude "$(basename "$MANIFEST")" . ) | ( cd "$TMP" && tar -xf - )
cp "$MANIFEST" "$TMP/SHA256SUMS.source"

SHA_W="$(sha256sum "$TMP/weights.c" | cut -d' ' -f1)"
"$PY" - "$TMP/signdet_weights.json" "$FROM" "$IN_RECIP" "$OUT_PPB" "$SHA_W" <<'PYEOF'
import hashlib, json, os, sys
out, src, recip, ppb, shaw = sys.argv[1:6]
man = {
    "schema_version": 1,
    "model": "signdet_b144 (SignDetLite)",
    "weights_mode": "real",
    "provenance": "installed by scripts/91_signdet_install_model.sh from %s" % src,
    "source_manifest_sha256": None,
    "weights_c_sha256": shaw,
    "in_scale_recip": int(recip),
    "out_scale_ppb": int(ppb),
    "classes": 3,
    "replay_gate": "applicable",
    "detection": "meaningful; the replay gate in scripts/90 is a gate.",
    "licence_note": ("the trained weights are GTSDB-derived.  They are not redistributable "
                     "by this project and must not be committed; see "
                     "docs/SIGNDET_WEIGHTS.md."),
}
sm = os.path.join(os.path.dirname(out), "SHA256SUMS.source")
if os.path.exists(sm):
    man["source_manifest_sha256"] = hashlib.sha256(open(sm, "rb").read()).hexdigest()
ir = os.path.join(os.path.dirname(out), "ir", "graph.json")
if os.path.exists(ir):
    man["ir_md5"] = hashlib.md5(open(ir, "rb").read()).hexdigest()
with open(out, "w") as fh:
    json.dump(man, fh, indent=2)
PYEOF

rm -rf "$DEST.previous"
[ -d "$DEST" ] && mv "$DEST" "$DEST.previous"
mv "$TMP" "$DEST"
info "installed $DEST"
[ -d "$DEST.previous" ] && info "the tree that was there is kept at $DEST.previous"

if [ -n "$CALIB_SRC" ]; then
  need_file "$CALIB_SRC"
  mkdir -p "$(dirname "$DEST")"
  cp "$CALIB_SRC" "$(dirname "$DEST")/calib_X.npy"
  info "calibration set -> $(dirname "$DEST")/calib_X.npy  (scripts/86's control set)"
fi

if [ -n "$BAKE_SRC" ]; then
  [ -d "$BAKE_SRC" ] || die "--bake $BAKE_SRC is not a directory"
  need_file "$BAKE_SRC/signdet_frames.c"
  rm -rf "$BAKE_DEST.installing.$$"; mkdir -p "$BAKE_DEST.installing.$$"
  cp "$BAKE_SRC"/signdet_frames.* "$BAKE_DEST.installing.$$/"
  rm -rf "$BAKE_DEST"; mv "$BAKE_DEST.installing.$$" "$BAKE_DEST"
  info "replay frames -> $BAKE_DEST"
  info "run scripts/90 with BAKE=$BAKE_DEST to use them instead of the tracked set"
fi

step "Done"
info "weights_mode=real -- scripts/90's GATE 4 (REPLAY) is now a gate, not 'not applicable'"
info "next: PYNQ_HOST=xilinx@<your board> scripts/with_board.sh ./scripts/90_b156_tacit_window.sh"
