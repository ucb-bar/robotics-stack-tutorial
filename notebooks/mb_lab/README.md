# The ModelBlaster + LLM lab, as notebooks

Through ModelBlaster's LLM backend, DeepSeek on AWS Bedrock rewrites `maxpool2d_s8` to use the board's
MBP.MAX8 instruction, with the board in the loop, and the attendee sees how much of the speedup comes
from the accelerator. The attendee's card, a single page, is `docs/MB_ATTENDEE.md`, and
`docs/MB_INSTRUCTOR.md` covers setting the lab up for a room.

| file | what it is |
|---|---|
| `mb_lab.ipynb` | the notebook an attendee completes. Generated; do not edit it by hand. |
| `mb_lab_solved.ipynb` | the same lab, already drawn from complete recorded runs in `assets/solved_runs.tar.gz`. It can be read with no board or LLM. Generated. |
| `mb_by_hand.ipynb` | what `lab.go()` does, one command per cell: `extract_graph`, `generate_kernels` (reference, then `--backend llm --optimize` in two rounds, the second given the FPGA's cycle counts as hardware in the loop feedback), `west build`, spike, and `mb run-image` on the board, then the same kernel with the MBP off. No helper module. Generated. |
| `mb_by_hand_solved.ipynb` | `mb_by_hand.ipynb` run on a seat with a real board and the LLM, with its outputs stored (`tools/build_notebooks.py --store-outputs`). Generated. |
| `mb_lab.py` | the helpers the lab notebooks import (`import mb_lab as lab`). Every call runs the same `mb` command a terminal would. |
| `tools/build_notebooks.py` | the source of the notebooks. Edit it, rerun it, and commit the results. |
| `tools/pack_solved_runs.py` | packs finished runs into `assets/solved_runs.tar.gz`, trimmed and without the instance's hostname. |
| `seat/` | the READMEs of the folder an attendee sees in JupyterLab (below). |

`mb_lab.ipynb` and `mb_by_hand.ipynb` are committed without outputs, like `../iiswc_tutorial.ipynb`, and
each cell has a description of what it should print. The two solved notebooks are committed with their
outputs so they open fully drawn. `tools/build_notebooks.py` runs the cells of `mb_lab_solved.ipynb` against
the stored runs while it writes the notebook. Those runs carry no instance hostname because
`tools/pack_solved_runs.py` removes it, so the outputs don't either. The outputs of `mb_by_hand_solved.ipynb`
come from running it on a seat, and `--store-outputs` copies them in without hostnames or IP addresses.

## What an attendee sees

`scripts/96_seat_mb_setup.sh` puts one folder in each seat's JupyterLab (`~/work`), built from `seat/`, the
notebooks above and the walkthroughs in `docs/`:

    modelblaster-llm-lab/
      README.md                  start here
      mb_lab.ipynb               the lab (never overwritten once it is there)
      mb_lab_solved.ipynb        the same lab, already run
      mb_by_hand.ipynb           the same lab with ModelBlaster's own commands (never overwritten)
      mb_by_hand_solved.ipynb    that one, already run
      by-hand/                   made by mb_by_hand.ipynb: one folder per run of its setup cell
      walkthroughs/              how maxpool runs on the accelerator; the gelu contrast
      your-kernel/               where lab.start() and `mb start` put the kernel to edit

## Regenerating

    python3 notebooks/mb_lab/tools/build_notebooks.py
    # to refresh the stored runs, from a seat with a board: run the lab once (a live LLM run and a
    # `try` with the solution), then, with those two run directories:
    python3 notebooks/mb_lab/tools/pack_solved_runs.py <llm run> <try run>
    python3 notebooks/mb_lab/tools/build_notebooks.py
    # mb_by_hand_solved.ipynb: after build_notebooks.py, on a seat with a board, run it and store its outputs:
    jupyter nbconvert --to notebook --execute --ExecutePreprocessor.timeout=1800 --output ran.ipynb mb_by_hand_solved.ipynb
    python3 notebooks/mb_lab/tools/build_notebooks.py --store-outputs ran.ipynb
