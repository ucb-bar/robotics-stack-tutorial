# ModelBlaster + LLM lab: start here

Your board is a PYNQ-Z1 FPGA running a RISC-V core with a small accelerator, **MBP**: four instructions that
each work on eight int8 values at once. In this lab an LLM rewrites a kernel to use it, your FPGA runs every
round, and you find out how much of the speedup is the accelerator itself. Then you write the kernel yourself.

1. Open **`mb_lab.ipynb`** and run it top to bottom with **Shift-Enter**. The first cell checks that your seat,
   your board and the LLM are ready.
2. Stuck, or something not working? **`mb_lab_solved.ipynb`** is the same lab already run on a real board:
   **Run → Run All Cells** redraws everything, with no board needed.
3. **`walkthroughs/`** explains how it works: the op, the MAX8 trick, how it's proven to run on the
   accelerator, and what the LLM was told.
4. **`your-kernel/`** is where your own kernel goes, when the notebook gets there.

Prefer a terminal? **File → New → Terminal**, then `mb doctor` and `mb`.
