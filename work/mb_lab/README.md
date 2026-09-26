# The ModelBlaster + LLM lab, as notebooks

An LLM (DeepSeek on AWS Bedrock) rewrites `maxpool2d_s8` to use the board's MBP.MAX8 instruction, with the
board in the loop, and the attendee sees how much of the speedup comes from the accelerator. The attendee's
one-page card is `docs/MB_ATTENDEE.md`; setting it up for a room is `docs/MB_INSTRUCTOR.md`.

| file | what it is |
|---|---|
| `mb_lab.ipynb` | the notebook an attendee completes. **Generated, do not hand-edit.** |
| `mb_lab_solved.ipynb` | the same lab, redrawn from complete runs in `assets/solved_runs.tar.gz`: no board or LLM needed. **Generated.** |
| `mb_lab.py` | the helpers both notebooks import (`import mb_lab as lab`). Every call runs the same `mb` command a terminal would. |
| `tools/build_notebooks.py` | the source of both notebooks. Edit it, re-run it, commit the results. |
| `tools/pack_solved_runs.py` | packs finished runs into `assets/solved_runs.tar.gz`, trimmed and without the instance's hostname. |
| `seat/` | the READMEs of the folder an attendee sees in JupyterLab (below). |

Like `../iiswc_tutorial.ipynb`, both notebooks are committed **without outputs**. What a cell should print is
described beside it instead.

## What an attendee sees

`scripts/96_seat_mb_setup.sh` puts one folder in each seat's JupyterLab (`~/work`), built from `seat/`, the
notebooks above and the walkthroughs in `docs/`:

    modelblaster-llm-lab/
      README.md                  start here
      mb_lab.ipynb               the lab (never overwritten once it is there)
      mb_lab_solved.ipynb        the same lab, already run
      walkthroughs/              how maxpool runs on the accelerator; the gelu contrast
      your-kernel/               where lab.start() and `mb start` put the kernel to edit

## Regenerating

    python3 notebooks/mb_lab/tools/build_notebooks.py
    # to refresh the stored runs, from a seat with a board: run the lab once (a live LLM run and a
    # `try` with the solution), then, with those two run directories:
    python3 notebooks/mb_lab/tools/pack_solved_runs.py <llm run> <try run>
    python3 notebooks/mb_lab/tools/build_notebooks.py
