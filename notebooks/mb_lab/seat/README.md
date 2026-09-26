# ModelBlaster + LLM lab: start here

Your board is a PYNQ-Z1 FPGA running a RISC-V core with a small accelerator called MBP, which adds four
instructions that each work on eight int8 values at once. In this lab ModelBlaster has an LLM rewrite a
kernel to use it while your FPGA runs every round, and you find out how much of the speedup comes from
the accelerator itself. After that you write the kernel yourself.

1. Open `mb_lab.ipynb` and run it top to bottom with Shift-Enter. The first cell checks that your seat,
   your board and the LLM are ready.
2. If something is not working, open `mb_lab_solved.ipynb`. It is a recorded run of the same lab on a
   real board, with every chart and result in it, and it needs no board.
3. `mb_by_hand.ipynb` shows what `lab.go()` does, one ModelBlaster, `west` or `spike` command per cell:
   lower the op, generate the reference kernel, let the LLM optimize it, measure it on your FPGA, feed the
   measurement back, and switch the MBP off. `mb_by_hand_solved.ipynb` is that notebook already run.
4. `walkthroughs/` explains how it works: the op, how MAX8 is used, how we show that the kernel runs on
   the accelerator, and what the LLM was told.
5. `your-kernel/` is where your own kernel goes when the notebook gets to that part.

To use a terminal instead, open File → New → Terminal and run `mb doctor`, then `mb`.
