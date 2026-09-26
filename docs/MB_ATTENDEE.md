# ModelBlaster and an LLM put a kernel on the FPGA's accelerator (up to about 20×, 5 to 8 minutes)

This lab needs the tutorial setup: a PYNQ board with the tutorial's SD card image on the WiFi, and your
AWS seat (see `docs/MB_INSTRUCTOR.md`).

## 1. Open your seat's JupyterLab

Open JupyterLab in your browser, using the address and passphrase the instructors gave you, and go to the folder `modelblaster-llm-lab`. Its `README.md` says where to start. `mb_lab.ipynb` walks
through everything on this page one cell at a time and leaves a few blanks for you to fill in.
`mb_lab_solved.ipynb` is a recorded run of the same lab on a real board, with every chart and result in
it, so you can read it without a board. Open it if something does not work, or to compare it with your
own run. `mb_by_hand.ipynb` runs the same steps as `lab.go()` one ModelBlaster command at a time, if you
want to see how the tools are used directly.

You can also open a terminal (File → New → Terminal) and use the commands below. Your board keeps a
tunnel open to this seat, so the commands run on the seat and drive your FPGA from there.

## 2. Check that everything is ready

    mb doctor

## 3. Run it

Start `mb` and pick `1` (maxpool2d_s8).

    mb

ModelBlaster's LLM backend has DeepSeek rewrite a 2×2 max pool over several rounds so that it uses the
board's custom SIMD instruction MBP.MAX8, which does eight int8 compares in one instruction. The LLM
learns about MBP.MAX8 from the MBP instruction guide that the lab adds to its prompt. Your FPGA is part
of the loop. After each round the board runs the round's best kernel, and its cycle count goes into the
next round's prompt as hardware in the loop feedback. The kernel that is kept is the one that ran
fastest on the FPGA. The terminal draws each kernel the LLM tried as a bar, with a line saying what it
tried. At the end the board runs the old kernel, the new one, and the new one with the accelerator
switched off, and prints the verdict.

In 8 test runs the new kernel was 3.6 to 19.7× faster (6 of the 8 above 14×) and matched the reference
bit for bit every time, and the accelerator itself accounted for 2.9 to 11.7× of that. The LLM writes a
different kernel each time, so if yours is slow, run it again.

## 4. Look closer

`mb report <run>` prints the verdict again, and `mb calls <run>` shows every prompt and answer.
`mb commands <run>` lists the ModelBlaster, `west` and spike commands the run executed, which you can
paste to run yourself. The walkthrough explains the op, the MAX8 instruction, what the LLM did with it,
and how we show that the kernel really runs on the accelerator.

    mb show docs/MB_MAXPOOL_WALKTHROUGH.md

## 5. Write it yourself

Every try of your kernel runs on the FPGA and takes about 2 minutes.

    mb start maxpool2d_s8
    mb edit maxpool2d_s8
    mb try maxpool2d_s8

You start from the unoptimized kernel, whose header gives the rules and four hints. The solution is in
`fpga/pynq-z2/modelblaster/mb_ops/exercises/maxpool2d_s8_solution.c` (read it with `mb show <path>`).

## Things to try next

- `mb go maxpool2d_s8 --guide modelblaster` leaves out the MBP instruction guide, so the LLM is not
  told about the accelerator. Does it still use it?
- `mb go linear_s8` is an int8 matrix multiply on MBP.DOT8 (eight multiply and add operations per
  instruction).
- `mb go gelu_s8` comes out 43 to 49× faster with no accelerator at all. What did the LLM do instead?
  `mb show docs/MB_GELU_WALKTHROUGH.md` explains it and ends with an exercise in finding a bug.

## If something goes wrong

| if… | do this |
|---|---|
| something isn't working | `mb doctor` checks your board, your seat and the LLM key, and tells you what to do |
| the LLM is slow, unreachable or out of credit | `mb go maxpool2d_s8 --replay` replays a verified kernel that the LLM wrote in a recorded run; everything else still runs for real |
| you want to see your runs | `mb list` |
| the board step fails, or you got interrupted | `mb doctor`, then run it again (`mb go …` or `mb try …`) |
| it says *your board is busy with another run* | Wait. Two runs at once (another tab, or a notebook and a terminal) take turns on the board, and this one continues when the other is done. |

## Working on the board instead (no tunnel)

You can also run `mb` on the board itself. Log in with `ssh xilinx@<board-ip>` (the IP is shown on the
board's OLED), fetch `mb` from your seat, and run it:

    /opt/iiswc/host/aws_pull.sh $(/opt/iiswc/host/aws_whoami.sh --name) pub/mb/mb ~/mb && chmod +x ~/mb
    ~/mb

The board then reaches your seat, and every command above works as `~/mb …`.
