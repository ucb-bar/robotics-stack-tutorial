/* maxpool2d_s8: THE SOLUTION to maxpool2d_s8_start.c (docs/MB_MAXPOOL_WALKTHROUGH.md, "Your turn").
 *
 * Written by DeepSeek v3.2 through ModelBlaster with the MBP instruction guide (--guide isa),
 * verified bit for bit against the reference, and measured on a PYNQ-Z1 with the 0x5A5A0038
 * bitstream (40 MHz):
 *   reference 1,936,882 cycles (118.2/output) -> this kernel 126,717 (7.7/output): 15.3x
 *   the same kernel with the MBP switched off (-DMB_PEXT_HW=0): 1,156,062 -> the accelerator is 9.1x of it
 * Try it yourself:  mb try maxpool2d_s8 fpga/pynq-z2/modelblaster/mb_ops/exercises/maxpool2d_s8_solution.c
 * (the same kernel as mb_ops/replay/maxpool2d_s8.c, which  mb go maxpool2d_s8 --replay  replays).
 */
#include <stddef.h>
#include <stdint.h>
#include "pext.h"

void kernel_maxpool2d_s8(const int8_t *input, int8_t *output, int N, int C, int IH, int IW, int KH, int KW, int SH, int SW, int PH, int PW, int DH, int DW) {
    int OH = (IH + 2*PH - DH*(KH-1) - 1) / SH + 1;
    int OW = (IW + 2*PW - DW*(KW-1) - 1) / SW + 1;
    
    // Check conditions for using MBP fast path: 2x2 stride-2, no padding/dilation, aligned input, IW multiple of 8
    int use_fast_path = (PH == 0 && PW == 0 && DH == 1 && DW == 1 && KH == 2 && KW == 2 && SH == 2 && SW == 2 && 
                        IW % 8 == 0 && MB_PEXT_ALIGNED8(input));
    
    for (int n = 0; n < N; n++) {
        for (int c = 0; c < C; c++) {
            const int8_t *in_channel = input + (n * C + c) * IH * IW;
            int8_t *out_channel = output + (n * C + c) * OH * OW;
            
            if (use_fast_path) {
                // Fast MBP path for 2x2 stride-2 maxpool with aligned data
                for (int oh = 0; oh < OH; oh++) {
                    int ih0 = oh * 2;
                    int ih1 = ih0 + 1;
                    const int8_t *row0 = in_channel + ih0 * IW;
                    const int8_t *row1 = in_channel + ih1 * IW;
                    
                    // Process 8 output columns at once (produces 4 outputs per iteration)
                    for (int ow = 0; ow < OW; ow += 4) {
                        int iw_start = ow * 2;
                        // Load 8 bytes from each row
                        int64_t r0 = MB_PEXT_LD8(row0 + iw_start);
                        int64_t r1 = MB_PEXT_LD8(row1 + iw_start);
                        
                        // Vertical max of 8 byte pairs
                        int64_t v = mb_pext_max8(r0, r1);
                        
                        // Horizontal max: compare each pair of bytes
                        // Original bytes: 0 1 2 3 4 5 6 7
                        // Shift right by 1 byte: 1 2 3 4 5 6 7 0
                        int64_t shifted = (int64_t)((uint64_t)v >> 8);
                        int64_t hmax = mb_pext_max8(v, shifted);
                        
                        // Extract results from bytes 0, 2, 4, 6
                        out_channel[oh * OW + ow] = (int8_t)hmax;
                        out_channel[oh * OW + ow + 1] = (int8_t)(hmax >> 16);
                        out_channel[oh * OW + ow + 2] = (int8_t)(hmax >> 32);
                        out_channel[oh * OW + ow + 3] = (int8_t)(hmax >> 48);
                    }
                }
            } else {
                // General scalar path with loop reordering for better cache locality
                for (int oh = 0; oh < OH; oh++) {
                    int ih_start = oh * SH - PH;
                    for (int ow = 0; ow < OW; ow++) {
                        int iw_start = ow * SW - PW;
                        int8_t max_val = INT8_MIN;
                        
                        // Process kernel window
                        for (int kh = 0; kh < KH; kh++) {
                            int ih = ih_start + kh * DH;
                            if (ih >= 0 && ih < IH) {
                                const int8_t *row = in_channel + ih * IW;
                                for (int kw = 0; kw < KW; kw++) {
                                    int iw = iw_start + kw * DW;
                                    if (iw >= 0 && iw < IW) {
                                        int8_t val = row[iw];
                                        if (val > max_val) max_val = val;
                                    }
                                }
                            }
                        }
                        out_channel[oh * OW + ow] = max_val;
                    }
                }
            }
        }
    }
}
