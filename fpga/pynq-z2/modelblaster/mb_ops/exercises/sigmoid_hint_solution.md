## A faster algorithm for this op (a 256-entry table)

With per-tensor symmetric int8 quantisation this op maps 256 possible input bytes to 256 output
bytes for a given (scale_in, scale_out, activation_min, activation_max). So never evaluate the
float expression once per element. Instead: one pass over the input marking which of the 256 byte
values occur; evaluate the reference's own float expression -- same constants, same casts, same
expf, the same division by scale_out and the same roundf -- once per DISTINCT byte into an
int8_t table[256]; then one gather pass, output[i] = table[(uint8_t)input[i]]. Keep the exact
reference expression for each table entry (do not replace the division with a multiply by the
reciprocal, do not change the order of operations) so the result stays bit-identical. For small
n (n < 32) just run the reference loop.
