#include <stddef.h>
#include <stdint.h>
#include "pext.h"

void kernel_linear_s8(const int8_t *input, const int8_t *weight, const int32_t *bias, int8_t *output, int M, int K, int N, int input_offset, int filter_offset, int output_offset, int output_multiplier, int output_shift, int activation_min, int activation_max)
{
    /* Only use MBP when both offsets are zero and weight rows are 8-byte aligned */
    if (input_offset == 0 && filter_offset == 0 && MB_PEXT_ALIGNED8(weight) && (K % 8) == 0) {
        static int8_t scratch[4096] __attribute__((aligned(8)));
        if (K > 0 && K + 15 <= 4096) {
            /* Precompute requantization constants outside all loops */
            int32_t round_shift = 0;
            if (output_shift > 0) {
                round_shift = 1 << (output_shift - 1);
            }
            int32_t shift_left = 0;
            if (output_shift < 0) {
                shift_left = -output_shift;
            }
            
            /* Compute alignment offset once per weight matrix */
            int r = (uintptr_t)(weight) & 7;
            int L = (r + K + 7) & ~7;
            
            /* Process multiple outputs per input row to reuse scratch */
            for (int m = 0; m < M; ++m) {
                const int8_t *in_row = input + m * K;
                
                /* Copy input row into aligned scratch with leading/trailing zeros */
                for (int i = 0; i < r; ++i) scratch[i] = 0;
                for (int i = 0; i < K; ++i) scratch[r + i] = in_row[i];
                for (int i = r + K; i < L; ++i) scratch[i] = 0;
                
                /* Process 4 outputs at a time to reuse scratch loads */
                int n = 0;
                for (; n + 4 <= N; n += 4) {
                    int32_t acc0 = bias ? bias[n + 0] : 0;
                    int32_t acc1 = bias ? bias[n + 1] : 0;
                    int32_t acc2 = bias ? bias[n + 2] : 0;
                    int32_t acc3 = bias ? bias[n + 3] : 0;
                    
                    const int8_t *w_row0 = weight + (n + 0) * K;
                    const int8_t *w_row1 = weight + (n + 1) * K;
                    const int8_t *w_row2 = weight + (n + 2) * K;
                    const int8_t *w_row3 = weight + (n + 3) * K;
                    
                    const int8_t *a0 = w_row0 - r;
                    const int8_t *a1 = w_row1 - r;
                    const int8_t *a2 = w_row2 - r;
                    const int8_t *a3 = w_row3 - r;
                    
                    /* Unroll dot product loop for better pipelining */
                    int g = 0;
                    for (; g + 32 <= L; g += 32) {
                        int64_t xs0 = MB_PEXT_LD8(scratch + g);
                        int64_t xs1 = MB_PEXT_LD8(scratch + g + 8);
                        int64_t xs2 = MB_PEXT_LD8(scratch + g + 16);
                        int64_t xs3 = MB_PEXT_LD8(scratch + g + 24);
                        
                        int64_t w00 = MB_PEXT_LD8(a0 + g);
                        int64_t w01 = MB_PEXT_LD8(a0 + g + 8);
                        int64_t w02 = MB_PEXT_LD8(a0 + g + 16);
                        int64_t w03 = MB_PEXT_LD8(a0 + g + 24);
                        
                        int64_t w10 = MB_PEXT_LD8(a1 + g);
                        int64_t w11 = MB_PEXT_LD8(a1 + g + 8);
                        int64_t w12 = MB_PEXT_LD8(a1 + g + 16);
                        int64_t w13 = MB_PEXT_LD8(a1 + g + 24);
                        
                        int64_t w20 = MB_PEXT_LD8(a2 + g);
                        int64_t w21 = MB_PEXT_LD8(a2 + g + 8);
                        int64_t w22 = MB_PEXT_LD8(a2 + g + 16);
                        int64_t w23 = MB_PEXT_LD8(a2 + g + 24);
                        
                        int64_t w30 = MB_PEXT_LD8(a3 + g);
                        int64_t w31 = MB_PEXT_LD8(a3 + g + 8);
                        int64_t w32 = MB_PEXT_LD8(a3 + g + 16);
                        int64_t w33 = MB_PEXT_LD8(a3 + g + 24);
                        
                        acc0 += mb_pext_dot8(xs0, w00) + mb_pext_dot8(xs1, w01) + mb_pext_dot8(xs2, w02) + mb_pext_dot8(xs3, w03);
                        acc1 += mb_pext_dot8(xs0, w10) + mb_pext_dot8(xs1, w11) + mb_pext_dot8(xs2, w12) + mb_pext_dot8(xs3, w13);
                        acc2 += mb_pext_dot8(xs0, w20) + mb_pext_dot8(xs1, w21) + mb_pext_dot8(xs2, w22) + mb_pext_dot8(xs3, w23);
                        acc3 += mb_pext_dot8(xs0, w30) + mb_pext_dot8(xs1, w31) + mb_pext_dot8(xs2, w32) + mb_pext_dot8(xs3, w33);
                    }
                    
                    for (; g < L; g += 8) {
                        int64_t xs = MB_PEXT_LD8(scratch + g);
                        int64_t w0 = MB_PEXT_LD8(a0 + g);
                        int64_t w1 = MB_PEXT_LD8(a1 + g);
                        int64_t w2 = MB_PEXT_LD8(a2 + g);
                        int64_t w3 = MB_PEXT_LD8(a3 + g);
                        
                        acc0 += mb_pext_dot8(xs, w0);
                        acc1 += mb_pext_dot8(xs, w1);
                        acc2 += mb_pext_dot8(xs, w2);
                        acc3 += mb_pext_dot8(xs, w3);
                    }
                    
                    /* Requantize 4 outputs */
                    int64_t prod0 = mb_pext_qmul(acc0, output_multiplier);
                    int64_t prod1 = mb_pext_qmul(acc1, output_multiplier);
                    int64_t prod2 = mb_pext_qmul(acc2, output_multiplier);
                    int64_t prod3 = mb_pext_qmul(acc3, output_multiplier);
                    
                    int32_t scaled0 = (int32_t)prod0;
                    int32_t scaled1 = (int32_t)prod1;
                    int32_t scaled2 = (int32_t)prod2;
                    int32_t scaled3 = (int32_t)prod3;
                    
                    if (output_shift > 0) {
                        scaled0 = ((int64_t)scaled0 + round_shift) >> output_shift;
                        scaled1 = ((int64_t)scaled1 + round_shift) >> output_shift;
                        scaled2 = ((int64_t)scaled2 + round_shift) >> output_shift;
                        scaled3 = ((int64_t)scaled3 + round_shift) >> output_shift;
                    } else if (shift_left > 0) {
                        scaled0 = scaled0 << shift_left;
                        scaled1 = scaled1 << shift_left;
                        scaled2 = scaled2 << shift_left;
                        scaled3 = scaled3 << shift_left;
                    }
                    
                    scaled0 += output_offset;
                    scaled1 += output_offset;
                    scaled2 += output_offset;
                    scaled3 += output_offset;
                    
                    /* Use fast clip8 when activation range matches int8 */
                    if (activation_min == -128 && activation_max == 127) {
                        output[m * N + n + 0] = (int8_t)mb_pext_clip8(scaled0);
                        output[m * N + n + 1] = (int8_t)mb_pext_clip8(scaled1);
                        output[m * N + n + 2] = (int8_t)mb_pext_clip8(scaled2);
                        output[m * N + n + 3] = (int8_t)mb_pext_clip8(scaled3);
                    } else {
                        if (scaled0 < activation_min) scaled0 = activation_min;
                        if (scaled0 > activation_max) scaled0 = activation_max;
                        if (scaled1 < activation_min) scaled1 = activation_min;
                        if (scaled1 > activation_max) scaled1 = activation_max;
                        if (scaled2 < activation_min) scaled2 = activation_min;
                        if (scaled2 > activation_max) scaled2 = activation_max;
                        if (scaled3 < activation_min) scaled3 = activation_min;
                        if (scaled3 > activation_max) scaled3 = activation_max;
                        
                        output[m * N + n + 0] = (int8_t)scaled0;
                        output[m * N + n + 1] = (int8_t)scaled1;
                        output[m * N + n + 2] = (int8_t)scaled2;
                        output[m * N + n + 3] = (int8_t)scaled3;
                    }
                }
                
                /* Handle remaining outputs */
                for (; n < N; ++n) {
                    int32_t acc = bias ? bias[n] : 0;
                    const int8_t *w_row = weight + n * K;
                    const int8_t *a = w_row - r;
                    
                    for (int g = 0; g < L; g += 8) {
                        int64_t xs = MB_PEXT_LD8(scratch + g);
                        int64_t ws = MB_PEXT_LD8(a + g);
                        acc += mb_pext_dot8(xs, ws);
                    }
                    
                    int64_t prod = mb_pext_qmul(acc, output_multiplier);
                    int32_t scaled = (int32_t)prod;
                    
                    if (output_shift > 0) {
                        scaled = ((int64_t)scaled + round_shift) >> output_shift;
                    } else if (shift_left > 0) {
                        scaled = scaled << shift_left;
                    }
                    
                    scaled += output_offset;
                    
                    if (activation_min == -128 && activation_max == 127) {
                        output[m * N + n] = (int8_t)mb_pext_clip8(scaled);
                    } else {
                        if (scaled < activation_min) scaled = activation_min;
                        if (scaled > activation_max) scaled = activation_max;
                        output[m * N + n] = (int8_t)scaled;
                    }
                }
            }
            return;
        }
    }

    /* Fallback scalar reference with multiple accumulators */
    int32_t round_shift = 0;
    if (output_shift > 0) {
        round_shift = 1 << (output_shift - 1);
    }
    int32_t shift_left = 0;
    if (output_shift < 0) {
        shift_left = -output_shift;
    }
    
    for (int m = 0; m < M; ++m) {
        const int8_t *in_row = input + m * K;
        for (int n = 0; n < N; ++n) {
            int32_t acc = bias ? bias[n] : 0;
            
            /* Use multiple accumulators for the dot product */
            int32_t acc0 = 0, acc1 = 0, acc2 = 0, acc3 = 0;
            int k = 0;
            const int8_t *w_row = weight + n * K;
            for (; k + 4 <= K; k += 4) {
                int32_t in_val0 = in_row[k + 0] + input_offset;
                int32_t in_val1 = in_row[k + 1] + input_offset;
                int32_t in_val2 = in_row[k + 2] + input_offset;
                int32_t in_val3 = in_row[k + 3] + input_offset;
                
                int32_t w_val0 = w_row[k + 0] + filter_offset;
                int32_t w_val1 = w_row[k + 1] + filter_offset;
                int32_t w_val2 = w_row[k + 2] + filter_offset;
                int32_t w_val3 = w_row[k + 3] + filter_offset;
                
                acc0 += in_val0 * w_val0;
                acc1 += in_val1 * w_val1;
                acc2 += in_val2 * w_val2;
                acc3 += in_val3 * w_val3;
            }
            acc += (acc0 + acc1) + (acc2 + acc3);
            
            for (; k < K; ++k) {
                int32_t in_val = in_row[k] + input_offset;
                int32_t w_val = w_row[k] + filter_offset;
                acc += in_val * w_val;
            }
            
            int64_t prod = (int64_t)acc * (int64_t)output_multiplier;
            prod = (prod + (1LL << 30)) >> 31;
            int32_t scaled = (int32_t)prod;
            
            if (output_shift > 0) {
                scaled = ((int64_t)scaled + round_shift) >> output_shift;
            } else if (shift_left > 0) {
                scaled = scaled << shift_left;
            }
            
            scaled += output_offset;
            
            if (scaled < activation_min) scaled = activation_min;
            if (scaled > activation_max) scaled = activation_max;
            
            output[m * N + n] = (int8_t)scaled;
        }
    }
}