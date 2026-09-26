# Your kernel

`lab.start()` in the notebook (or `mb start maxpool2d_s8` in a terminal) puts the unoptimized kernel here
as `maxpool2d_s8.c`. Its header has the rules and four hints, meant to be read one at a time.

Edit it here, save with Ctrl-S, then run `lab.try_kernel()` (or `mb try maxpool2d_s8`). A kernel that
doesn't compile or gives a wrong answer is stopped on spike before it reaches your board. The previous
version is kept as `maxpool2d_s8.c.bak`.

The goal is `ON THE ACCELERATOR` and under 10 cycles per output.
