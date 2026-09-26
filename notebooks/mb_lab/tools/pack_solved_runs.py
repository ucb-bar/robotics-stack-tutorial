#!/usr/bin/env python3
"""Pack finished lab runs into notebooks/mb_lab/assets/solved_runs.tar.gz, which mb_lab_solved.ipynb redraws.

    python3 notebooks/mb_lab/tools/pack_solved_runs.py <run dir> [<run dir> ...]
    python3 notebooks/mb_lab/tools/build_notebooks.py      # then regenerate the notebooks

Each run is trimmed to the files the notebook reads, and the host and pid fields are removed
from status.json so the solved notebook does not contain them. The archive is deterministic
(sorted names, zero timestamps): packing the same runs twice gives the same bytes.
"""
import gzip
import io
import json
import sys
import tarfile
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "assets" / "solved_runs.tar.gz"
KEEP = ["run.json", "report.txt", "status.json", "board.json", "board_rounds.jsonl", "commands.txt",
        "commands.sh", "before/spike.json", "before/gen/kernels.c", "after/spike.json",
        "after/transcript.jsonl", "after/round*", "board/*/console.txt", "board/*/image.json",
        "kernels_replay/pext/*.c"]


def members(run: Path):
    seen = set()
    for pat in KEEP:
        for f in sorted(run.glob(pat)):
            if f.is_file() and f not in seen:
                seen.add(f)
                data = f.read_bytes()
                if f.name == "status.json":
                    d = json.loads(data)
                    for k in ("host", "pid"):
                        d.pop(k, None)
                    data = (json.dumps(d) + "\n").encode()
                yield f"{run.name}/{f.relative_to(run)}", data


def main() -> int:
    runs = [Path(a) for a in sys.argv[1:]]
    if not runs or not all((r / "run.json").exists() for r in runs):
        print(__doc__, file=sys.stderr)
        return 2
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for run in sorted(runs, key=lambda r: r.name):
            for name, data in members(run):
                ti = tarfile.TarInfo(name)
                ti.size, ti.mtime, ti.mode = len(data), 0, 0o644
                tar.addfile(ti, io.BytesIO(data))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", mtime=0, filename="") as gz:
        gz.write(raw.getvalue())
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes): {', '.join(r.name for r in runs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
