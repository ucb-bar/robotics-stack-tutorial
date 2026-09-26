#!/usr/bin/env python3
"""Check that an int8 pointwise kernel is bit-exact over its whole input domain.

    python3 scripts/lib/mb_enum_check.py --op gelu_s8 --candidate kernel.c --build-dir D
                                          [--json out.json] [--scales S,S[,S] ...]

Under symmetric quantisation with one scale per tensor, a unary int8 op (gelu, tanh, silu,
sigmoid) maps 256 bytes to 256 bytes for each (scale_in, scale_out), and a binary op (mul,
add) maps 65,536 byte pairs for each (scale_a, scale_b, scale_out), so the domain can be
enumerated. ModelBlaster's own checks sample it instead: host verify uses random inputs at the
IR's shapes, and the spike golden is one tensor at one set of scales. Rewriting `y / scale_out`
as `y * (1/scale_out)` is off by one ulp on some values, which changes roundf() only where the
quotient sits exactly halfway between two integers; sampling rarely hits those.

The reference is KERNEL_SPECS[op].reference_impl, ModelBlaster's own source, compiled next to
the candidate under another name. Both are built with the host cc at -O2 against the same libm,
so any difference comes from the candidate's arithmetic. (The board links picolibc for both; this
checks that the two expressions agree.) Unary ops run all 256 inputs over a 24 x 24 log grid of
scale pairs from 0.004 to 0.25; binary ops run all 65,536 pairs over a 6 x 6 x 6 grid of scale
triples. Scales given with --scales are added (the lab passes the IR's).

On gelu_s8 it catches a x1.00001 factor before roundf (33 of 147,456 cases, 1 LSB) and a
corrupted table index (8,428 cases, up to 37 LSB).
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

UNARY = {"gelu_s8", "tanh_s8", "silu_s8", "sigmoid_s8"}
BINARY = {"mul_s8", "add_s8"}

HARNESS_UNARY = r"""
#include <stdint.h>
#include <stdio.h>
void ref_k(const int8_t *, int8_t *, int, float, float, int, int);
void cand_k(const int8_t *, int8_t *, int, float, float, int, int);
int main(void) {
    /* n = 256 * 8 so the candidate is past any small-n guard and its unrolled paths run */
    enum { REP = 8, N = 256 * REP };
    static int8_t in[N], a[N], b[N];
    for (int i = 0; i < N; i++) in[i] = (int8_t)(i % 256 - 128);
    int npairs = 0, maxd = 0, bad = 0; long diffs = 0;
    float s_in, s_out;
    while (scanf("%f %f", &s_in, &s_out) == 2) {
        ref_k(in, a, N, s_in, s_out, -128, 127);
        cand_k(in, b, N, s_in, s_out, -128, 127);
        int here = 0;
        for (int i = 0; i < 256; i++) {           /* one full period is the domain */
            int d = a[i] - b[i]; if (d < 0) d = -d;
            if (d) { diffs++; here++; if (d > maxd) maxd = d;
                     if (diffs <= 5) printf("DIFF s_in=%.9g s_out=%.9g x=%d ref=%d cand=%d\n",
                                            s_in, s_out, in[i], a[i], b[i]); }
        }
        for (int i = 256; i < N; i++)             /* and every period must agree with it */
            if (b[i] != b[i % 256]) { printf("PERIOD_MISMATCH i=%d\n", i); return 2; }
        bad += here != 0; npairs++;
    }
    printf("RESULT pairs=%d cases=%ld differing=%ld pairs_with_diff=%d max_abs_diff=%d\n",
           npairs, (long)npairs * 256, diffs, bad, maxd);
    return 0;
}
"""

HARNESS_BINARY = r"""
#include <stdint.h>
#include <stdio.h>
void ref_k(const int8_t *, const int8_t *, int8_t *, int, float, float, float, int, int);
void cand_k(const int8_t *, const int8_t *, int8_t *, int, float, float, float, int, int);
int main(void) {
    enum { N = 65536 };
    static int8_t x[N], y[N], a[N], b[N];
    for (int i = 0; i < N; i++) { x[i] = (int8_t)(i & 255); y[i] = (int8_t)(i >> 8); }
    int ntrip = 0, maxd = 0, bad = 0; long diffs = 0;
    float sa, sb, so;
    while (scanf("%f %f %f", &sa, &sb, &so) == 3) {
        ref_k(x, y, a, N, sa, sb, so, -128, 127);
        cand_k(x, y, b, N, sa, sb, so, -128, 127);
        int here = 0;
        for (int i = 0; i < N; i++) {
            int d = a[i] - b[i]; if (d < 0) d = -d;
            if (d) { diffs++; here++; if (d > maxd) maxd = d;
                     if (diffs <= 5) printf("DIFF sa=%.9g sb=%.9g so=%.9g a=%d b=%d ref=%d cand=%d\n",
                                            sa, sb, so, x[i], y[i], a[i], b[i]); }
        }
        bad += here != 0; ntrip++;
    }
    printf("RESULT pairs=%d cases=%ld differing=%ld pairs_with_diff=%d max_abs_diff=%d\n",
           ntrip, (long)ntrip * N, diffs, bad, maxd);
    return 0;
}
"""


def rename(src: str, op: str, new: str) -> str:
    # The only exported symbol is kernel_<op> (possibly with a model suffix, e.g. _kb_bench).
    out, n = re.subn(rf"\bkernel_{op}\w*\s*\(", f"{new}(", src)
    if n == 0:
        raise SystemExit(f"no kernel_{op} definition found in the candidate")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--op", default="gelu_s8")
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--build-dir", required=True)
    ap.add_argument("--json")
    ap.add_argument("--scales", action="append", default=[],
                    help="extra scales, separated by commas, to include (e.g. the IR's)")
    ap.add_argument("--scale-pair", action="append", default=[], help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.op not in UNARY | BINARY:
        raise SystemExit(f"{a.op} is not an enumerable pointwise op")
    binary = a.op in BINARY

    from modelblaster.pipeline.reference_kernels import KERNEL_SPECS

    d = Path(a.build_dir)
    d.mkdir(parents=True, exist_ok=True)
    prelude = "#include <stdint.h>\n#include <math.h>\n#include <string.h>\n"
    (d / "ref.c").write_text(prelude + rename(KERNEL_SPECS[a.op].reference_impl, a.op, "ref_k"))
    cand = Path(a.candidate).read_text()
    # Drop includes of the generated model headers, which a standalone kernel does not need.
    # Helpers such as pext.h and fexact32.h stay; they are found on CPATH.
    gen_hdrs = ("kernels.h", "model.h", "weights.h", "test_io.h", "buffers.h")
    cand = "\n".join(l for l in cand.splitlines()
                     if not (l.startswith('#include "') and any(h in l for h in gen_hdrs)))
    (d / "cand.c").write_text(prelude + rename(cand, a.op, "cand_k"))
    (d / "main.c").write_text(HARNESS_BINARY if binary else HARNESS_UNARY)
    cc = os.environ.get("HOST_CC", "cc")
    cmd = [cc, "-O2", "-o", str(d / "enum"), str(d / "main.c"), str(d / "ref.c"),
           str(d / "cand.c"), "-lm"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        print(p.stderr[-3000:], file=sys.stderr)
        raise SystemExit("host compile of the enumeration check failed")

    if binary:
        g = [0.004 * (0.25 / 0.004) ** (k / 5) for k in range(6)]
        rows = [(x, y, z) for x in g for y in g for z in g]
    else:
        g = [0.004 * (0.25 / 0.004) ** (k / 23) for k in range(24)]
        rows = [(x, y) for x in g for y in g]
    for s in a.scales + a.scale_pair:
        vals = tuple(float(v) for v in s.split(","))
        if len(vals) == (3 if binary else 2):
            rows.append(vals)
    stdin = "".join(" ".join(f"{v:.9g}" for v in r) + "\n" for r in rows)
    r = subprocess.run([str(d / "enum")], input=stdin, capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        raise SystemExit("enumeration harness failed")
    m = re.search(r"RESULT pairs=(\d+) cases=(\d+) differing=(\d+) pairs_with_diff=(\d+) "
                  r"max_abs_diff=(\d+)", r.stdout)
    res = {k: int(v) for k, v in zip(
        ("pairs", "cases", "differing", "pairs_with_diff", "max_abs_diff"), m.groups())}
    res["bit_exact"] = res["differing"] == 0
    res["domain"] = ("65,536 input pairs x %d scale triples" if binary
                     else "256 inputs x %d scale pairs") % res["pairs"]
    res["examples"] = [l for l in r.stdout.splitlines() if l.startswith("DIFF")]
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=2) + "\n")
    print(f"enumerated: {'BIT-EXACT' if res['bit_exact'] else 'NOT bit-exact'} "
          f"({res['differing']:,} of {res['cases']:,} cases differ, max {res['max_abs_diff']} LSB"
          + (f", across {res['pairs_with_diff']} of {res['pairs']} scale pairs)" if res['differing'] else ")"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
