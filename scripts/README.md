# Scripts

Use this directory for optional helper scripts such as local automation,
dataset preparation, or one-off maintenance tasks.

## Evaluation Batch

Run `light` and `full` evaluation runs for every media file in the sample input
folder:

```bash
cd /Users/matteopogetta/Documents/ExerPlazaProject
python scripts/run_evaluation_batch.py
```

Defaults:

- input folder: `/Users/matteopogetta/Documents/ExerPlazaSample/input`
- output sessions: `/Users/matteopogetta/Documents/ExerPlazaSample/output/evaluation_batch_<timestamp>/`
- evaluation history: `evaluations/`
- profiles: `light full`
- segmentation mode: `structural`

To compare semantic QA against the cheaper guarded local QA profile:

```bash
python scripts/run_evaluation_batch.py --resume --profiles quality quality_local --segmentation-mode structural
```

For cold benchmark runs, add:

```bash
python scripts/run_evaluation_batch.py --force-recompute
```

Resume an interrupted batch without repeating file/profile pairs that already
have a valid evaluation run from the same code snapshot:

```bash
python scripts/run_evaluation_batch.py --resume
```

If the Git commit or dirty working-tree state changed, `--resume` will run the
pair again instead of reusing an older evaluation. This avoids comparing a new
pipeline change against stale QA/C outputs.

When `full` is requested, the script checks that optional full-profile imports
work before launching long runs. This prevents saving degraded full evaluations
when alignment or diarization cannot start. To intentionally bypass this check:

```bash
python scripts/run_evaluation_batch.py --resume --skip-full-preflight
```

Preview commands without running the pipeline:

```bash
python scripts/run_evaluation_batch.py --dry-run
```
