#!/usr/bin/env python3
"""Parse samples/modelblaster_pext console output (and the runner's boot.log) for Lab B-LLM1's
board step (scripts/95_mb_kernel_llm.sh --where board). Called from scripts/lib/mb_board.sh.

    mb_board_parse.py console <console.txt> [--golden test_golden.bin] [--op maxpool2d_s8]
        -> one arm as JSON on stdout (for testing against archived runs)
    mb_board_parse.py boot <boot.log>
        -> MAGIC / FCLK0_HZ / the bitstream the runner says it loaded
    mb_board_parse.py board --run <out/mb_lab/RUN> --op <op> --board-dir <RUN/board> \
        [--bit-md5 ..] [--bit PATH] [--want-magic 0x5A5A0038] [--mtime-hz KHZ] [--iters N]
        [--board NAME] [--guest-board NAME]
        -> writes <RUN>/board.json, prints a short table, exits 1 unless the verdict is PASS:
           both arms bit-exact on the board, the expected MAGIC, identical goldens

Lines the guest prints (samples/modelblaster_pext/src/main.c, built without MB_DEC_AR), in order:
    MB_PEXT_BUILD model=<name> quant=int8 ops=<n> iters=<MB_ITERS> hw=<MB_PEXT_HW>
    MB_PEXT_BUILD main_cpu=0 main_hartid=0 cpus=2
    MB_PEXT_RUN cpu= mhartid= median= min= max= warm= mtime= max_abs_err=
        median/min/max over MB_ITERS timed runs of the whole model (rdcycle, irqs locked); warm
        is the warmup run, excluded from the median; max_abs_err is the guest's diff of the last
        run's output against the int8 golden baked into test_io.S
    MB_PEXT_OP id= name= op= shape= cycles=          one per dispatch, from the last run
    [MB_ROCCMOON / MB_SMX2 / MB_B100TAX]              only when those runtimes are linked
    MB_PEXT_OUT v0 v1 ...                             the whole output tensor, int8
       hart 0 (big): median X.YYY ms   max_abs_err=N
    [FAIL: max_abs_err=N against the baked int8 golden]
    MB_PEXT_NEG starting -- ...                       then a Zephyr fatal error dump (expected)
    MB_PEXT_NEG cpu=1 mhartid=1 trapped=1 reason= mcause=2 mtval=0x...
    RESULT: PASS|FAIL -- ...

RESULT also covers the negative test (MBP.DOT8 must trap on hart 1), which is a property of the
bitstream, not the kernel. The kernel verdict is therefore max_abs_err, from both the guest and a
diff on the host, element by element, against test_golden.bin; RESULT is recorded alongside it.
"""
import argparse
import hashlib
import json
import os
import re
import sys


def kv(line):
    return dict(re.findall(r'(\w+)=(\S+)', line))


def _int(v, default=None):
    try:
        return int(v, 0) if isinstance(v, str) else int(v)
    except (TypeError, ValueError):
        return default


def parse_console(text, op=None, golden=None):
    """One arm.  `golden` is a list of int8 or None."""
    lines = [l.rstrip('\r') for l in text.splitlines()]
    r = {'booted': any('*** Booting Zephyr OS' in l for l in lines)}

    b = {}
    for l in lines:
        if l.startswith('MB_PEXT_BUILD '):
            b.update(kv(l))
    r['build'] = {k: (_int(v) if k != 'model' and k != 'quant' else v) for k, v in b.items()}

    run = next((kv(l) for l in lines if l.startswith('MB_PEXT_RUN ')), None)
    r['run'] = {k: _int(v) for k, v in run.items()} if run else None

    ops = []
    for l in lines:
        if l.startswith('MB_PEXT_OP '):
            d = kv(l)
            ops.append({'id': _int(d.get('id')), 'name': d.get('name'), 'op': d.get('op'),
                        'shape': d.get('shape'), 'cycles': _int(d.get('cycles'))})
    r['ops'] = ops

    out_line = next((l for l in lines if l.startswith('MB_PEXT_OUT')), None)
    out = None
    if out_line is not None:
        try:
            out = [int(x) for x in out_line.split()[1:]]
        except ValueError:          # garbled console line: flag it instead of guessing
            out = None
            r['out_garbled'] = True
    r['out_len'] = len(out) if out is not None else None

    neg = next((kv(l) for l in lines if l.startswith('MB_PEXT_NEG cpu=')), None)
    r['neg'] = ({'cpu': _int(neg.get('cpu')), 'mhartid': _int(neg.get('mhartid')),
                 'trapped': neg.get('trapped') == '1', 'mcause': _int(neg.get('mcause')),
                 'mtval': neg.get('mtval')} if neg else None)

    m = next((re.match(r'RESULT: (PASS|FAIL)', l) for l in lines if l.startswith('RESULT:')), None)
    r['result'] = m.group(1) if m else None
    r['fail_lines'] = [l for l in lines if l.startswith('FAIL:')]
    r['warn_lines'] = [l for l in lines if l.startswith('WARN:')]

    # The op this lab is about.  Summed, in case codegen split it into several dispatches.
    mine = [o for o in ops if op is None or o['op'] == op]
    r['op'] = op
    r['op_dispatches'] = len(mine)
    r['op_cycles'] = sum(o['cycles'] for o in mine) if mine else None
    r['op_shape'] = mine[0]['shape'] if mine else None

    # Check on the host, element by element, independent of the guest's own diff.
    if golden is not None and out is not None:
        if len(out) == len(golden):
            r['host_max_abs_err'] = max((abs(a - g) for a, g in zip(out, golden)), default=0)
            r['host_differing'] = sum(1 for a, g in zip(out, golden) if a != g)
        else:
            r['host_max_abs_err'] = None
            r['host_len_mismatch'] = [len(out), len(golden)]
    n = r['out_len']
    r['cycles_per_output'] = (r['op_cycles'] / n) if (r['op_cycles'] and n) else None

    guest = r['run']['max_abs_err'] if r['run'] else None
    host = r.get('host_max_abs_err', 'unchecked')
    r['bit_exact'] = bool(r['booted'] and r['run'] and guest == 0
                          and (host == 'unchecked' or host == 0)
                          and not r.get('out_garbled')
                          and r['build'].get('hw') == 1)
    return r


def parse_boot(text):
    r = {}
    m = re.findall(r'MAGIC = (0x[0-9A-Fa-f]+)( OK)?', text)
    r['magic'] = m[-1][0] if m else None
    r['magics_seen'] = sorted({x[0] for x in m})
    m = re.findall(r'FCLK0_HZ = (\d+)', text)
    r['fclk_hz'] = int(m[-1]) if m else None
    m = re.search(r'loaded (\S+\.bit)', text)
    r['bitstream_loaded'] = m.group(1) if m else None
    m = re.findall(r'wrote (\d+) bytes to phys', text)
    r['bytes_written'] = [int(x) for x in m]
    return r


def read_golden(path):
    if not path or not os.path.exists(path):
        return None
    return [b - 256 if b > 127 else b for b in open(path, 'rb').read()]


def md5(p):
    return hashlib.md5(open(p, 'rb').read()).hexdigest() if os.path.exists(p) else None


def ratio(a, b):
    return (a / b) if (a and b) else None


def cmd_board(a):
    run, bd = a.run, a.board_dir
    boot_txt = open(os.path.join(bd, 'boot.log')).read() if os.path.exists(os.path.join(bd, 'boot.log')) else ''
    boot = parse_boot(boot_txt)
    # An fclk.json (host/fclk.py's SLCR readback) in the board dir wins over the runner's
    # boot.log line, which only a load or a --hold prints; with neither, --mtime-hz.
    fj = os.path.join(bd, 'fclk.json')
    fclk_read = None
    if os.path.exists(fj):
        try:
            m = json.load(open(fj))['fclk0']['mhz']
            fclk_read = int(round(m * 1e6)) if m else None
        except (ValueError, KeyError, TypeError):
            fclk_read = None
    fclk = fclk_read or boot['fclk_hz'] or a.mtime_hz * 1000
    arms = {}
    for arm in ('before', 'after', 'mbpoff'):
        if arm == 'mbpoff' and not os.path.exists(os.path.join(bd, arm, 'console.txt')):
            continue
        cpath = os.path.join(bd, arm, 'console.txt')
        txt = open(cpath).read() if os.path.exists(cpath) else ''
        g = read_golden(os.path.join(run, 'after' if arm == 'mbpoff' else arm, 'gen', 'test_golden.bin'))
        r = parse_console(txt, op=a.op, golden=g)
        r['console'] = os.path.relpath(cpath, run)
        r['bin_md5'] = md5(os.path.join(bd, arm, 'zephyr.bin'))
        if arm == 'mbpoff':
            # Only the kernel is built with MB_PEXT_HW=0; MODELBLASTER_KERNEL_CFLAGS reaches
            # kernels.c alone, so the harness still reports hw=1. The image gate
            # (mb_board_build_both: no MBP words outside neg_worker) checks that the kernel uses
            # the software model, so bit-exact here means guest and host error are both 0.
            guest = r['run']['max_abs_err'] if r['run'] else None
            r['bit_exact'] = bool(r['booted'] and r['run'] and guest == 0
                                  and r.get('host_max_abs_err', 'unchecked') in ('unchecked', 0)
                                  and not r.get('out_garbled'))
        img = os.path.join(bd, arm, 'image.json')
        r['image'] = json.load(open(img)) if os.path.exists(img) else None
        r['median_ms'] = (r['run']['median'] * 1000.0 / fclk) if r['run'] else None
        r['op_ms'] = (r['op_cycles'] * 1000.0 / fclk) if r['op_cycles'] else None
        arms[arm] = r
    b, f = arms['before'], arms['after']
    both_gold = read_golden(os.path.join(run, 'before', 'gen', 'test_golden.bin')) == \
        read_golden(os.path.join(run, 'after', 'gen', 'test_golden.bin'))
    j = {
        'lab': '95_mb_kernel_llm', 'where': 'board', 'op': a.op,
        'board': a.board or None, 'board_host': os.environ.get('PYNQ_HOST') or None,
        'bitstream': a.bit, 'bitstream_md5': a.bit_md5,
        'magic': boot['magic'], 'magic_expected': a.want_magic,
        'magic_ok': boot['magic'] == a.want_magic,
        'guest_board': a.guest_board,
        'fclk_hz': fclk, 'fclk_hz_boot_log': boot['fclk_hz'], 'fclk_hz_slcr': fclk_read,
        'mtime_hz_configured': a.mtime_hz * 1000,
        'iters': a.iters,
        'before': b, 'after': f,
        'goldens_identical': both_gold,
        # The op's own dispatch cycles, last timed run; the median of the whole model beside it.
        'speedup': ratio(b['op_cycles'], f['op_cycles']),
        'speedup_median': ratio(b['run'] and b['run']['median'], f['run'] and f['run']['median']),
        'max_abs_err': max((x for x in (
            b['run'] and b['run']['max_abs_err'], f['run'] and f['run']['max_abs_err'],
            b.get('host_max_abs_err'), f.get('host_max_abs_err')) if isinstance(x, int)),
            default=None),
        'note': 'board: Rocket WithoutFPU (soft-float), cycles by rdcycle on hart 0',
    }
    o = arms.get('mbpoff')
    if o:
        # Same kernel with and without the MBP: split the speedup into accelerator and loop.
        j['mbpoff'] = o
        j['speedup_accel'] = ratio(o['op_cycles'], f['op_cycles'])
        j['speedup_code'] = ratio(b['op_cycles'], o['op_cycles'])
    j['verdict'] = 'PASS' if (b['bit_exact'] and f['bit_exact'] and j['magic_ok']
                              and both_gold) else 'FAIL'
    json.dump(j, open(os.path.join(run, 'board.json'), 'w'), indent=2)

    def fmt(x, spec=',.0f'):
        return format(x, spec) if isinstance(x, (int, float)) else '-'
    print(f"    board: MAGIC {boot['magic']}  FCLK0 {fmt(fclk)} Hz  "
          f"bitstream md5 {a.bit_md5}")
    print(f"    {'arm':<7}{a.op + ' cycles':>18}{'cyc/el':>10}{'median':>14}{'ms':>10}"
          f"{'err':>5}{'host':>6}  RESULT")
    for arm in arms:
        r = arms[arm]
        print(f"    {arm:<7}{fmt(r['op_cycles']):>18}{fmt(r['cycles_per_output'], ',.1f'):>10}"
              f"{fmt(r['run'] and r['run']['median']):>14}{fmt(r['median_ms'], '.3f'):>10}"
              f"{str(r['run'] and r['run']['max_abs_err']):>5}{str(r.get('host_max_abs_err', '-')):>6}"
              f"  {r['result']}")
    print(f"    speedup on the board: {fmt(j['speedup'], '.1f')}x   verdict {j['verdict']}")
    if o:
        print(f"    of which the MBP accelerator: {fmt(j['speedup_accel'], '.1f')}x (after vs mbpoff), "
              f"the rewritten loop: {fmt(j['speedup_code'], '.1f')}x (before vs mbpoff)")
    return 0 if j['verdict'] == 'PASS' else 1


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sp = p.add_subparsers(dest='cmd', required=True)
    c = sp.add_parser('console'); c.add_argument('file'); c.add_argument('--golden'); c.add_argument('--op')
    c.add_argument('--full', action='store_true', help='also print the list of ops')
    bt = sp.add_parser('boot'); bt.add_argument('file')
    bo = sp.add_parser('board')
    bo.add_argument('--run', required=True); bo.add_argument('--op', required=True)
    bo.add_argument('--board-dir', required=True)
    bo.add_argument('--bit'); bo.add_argument('--bit-md5')
    bo.add_argument('--want-magic', default='0x5A5A0038')
    bo.add_argument('--mtime-hz', type=int, default=40000)
    bo.add_argument('--iters', type=int); bo.add_argument('--board')
    bo.add_argument('--guest-board')
    a = p.parse_args()
    if a.cmd == 'console':
        r = parse_console(open(a.file, errors='replace').read(), op=a.op, golden=read_golden(a.golden))
        if not a.full:
            r['ops'] = len(r['ops'])
        print(json.dumps(r, indent=2))
        return 0
    if a.cmd == 'boot':
        print(json.dumps(parse_boot(open(a.file, errors='replace').read()), indent=2))
        return 0
    return cmd_board(a)


if __name__ == '__main__':
    sys.exit(main())
