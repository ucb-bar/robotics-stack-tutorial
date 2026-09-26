/* maxpool2d_s8: YOUR STARTING POINT (docs/MB_MAXPOOL_WALKTHROUGH.md, "Your turn").
 *
 * This is ModelBlaster's reference kernel, unchanged: one byte, one compare at a time.  On the
 * board it costs ~118 cycles per output.  Make it use the MBP accelerator:
 *
 *   mb edit maxpool2d_s8      edit this file (your copy, on your seat)
 *   mb try  maxpool2d_s8      verify it, then run the reference, yours, and yours with the MBP off
 *                             on the FPGA
 *
 * The rules (the full MBP instruction guide: mb show fpga/pynq-z2/modelblaster/mb_ops/pext_isa_guide.md):
 *   a) int64_t mb_pext_max8(int64_t a, int64_t b): eight int8 lanes, r.byte[i] = max(a.byte[i], b.byte[i]).
 *   b) MB_PEXT_LD8(p) loads 8 bytes at p as one int64_t (byte 0 = p[0]).  p MUST be aligned to
 *      8 bytes; test it with MB_PEXT_ALIGNED8(p).  A misaligned load traps; it is not emulated.
 *   c) Keep this scalar loop as the fallback for every shape the fast path does not handle.
 *
 * Hints, one at a time (stop reading when you have an idea):
 *   1. The model's pool is 2x2, stride 2, no padding, 64x64 in, NCHW: one input row is contiguous.
 *   2. Load 8 columns of row 2*oh and 8 of row 2*oh+1.  One MAX8 gives the 8 vertical maxima.
 *   3. Each output needs the max of a PAIR of those bytes: bytes (0,1), (2,3), (4,5), (6,7).
 *      Shift the 8-byte value right by one byte and MAX8 it with itself.
 *   4. The four outputs are now in bytes 0, 2, 4 and 6: (int8_t)m, (int8_t)(m >> 16), ...
 *
 * The answer: fpga/pynq-z2/modelblaster/mb_ops/exercises/maxpool2d_s8_solution.c (15.3x on the FPGA).
 */
#include <stddef.h>
#include <stdint.h>
#include "pext.h"

void kernel_maxpool2d_s8(const int8_t *input, int8_t *output, int N, int C, int IH, int IW, int KH, int KW, int SH, int SW, int PH, int PW, int DH, int DW) {
    int OH = (IH + 2*PH - DH*(KH-1) - 1) / SH + 1;
    int OW = (IW + 2*PW - DW*(KW-1) - 1) / SW + 1;
    for (int n = 0; n < N; n++) {
        for (int c = 0; c < C; c++) {
            for (int oh = 0; oh < OH; oh++) {
                for (int ow = 0; ow < OW; ow++) {
                    int8_t m = INT8_MIN;
                    for (int kh = 0; kh < KH; kh++) {
                        int ih = oh*SH - PH + kh*DH;
                        if (ih < 0 || ih >= IH) continue;
                        for (int kw = 0; kw < KW; kw++) {
                            int iw = ow*SW - PW + kw*DW;
                            if (iw < 0 || iw >= IW) continue;
                            int8_t v = input[((n*C + c)*IH + ih)*IW + iw];
                            if (v > m) m = v;
                        }
                    }
                    output[((n*C + c)*OH + oh)*OW + ow] = m;
                }
            }
        }
    }
}
