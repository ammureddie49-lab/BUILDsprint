# Development evaluation — honest record

Official tool: `tools/run_days.py` and `tools/score.py`, supplied with BuildSprint-R3-candidate.zip.

## Final public benchmark (offline deterministic core)

| Category | Points |
|---|---:|
| Right briefs | 20 / 20 |
| Right answers | 15 / 15 |
| Honesty | 15 / 15 |
| Memory | 15 / 15 |
| Privacy | 10 / 10 |
| Cost | 10 / 10 |
| **Machine-scored public subset** | **85 / 85** |
| Getting better | N/A: needs at least six scored days |
| Design note | Human assessment, not scored by script |

The score is from the *five public days only*. The script cannot evaluate days 6–15. These results do not imply hidden-day generalization or perfect semantic correctness; the scorer checks specified fields, keywords, source IDs, and privacy rules.

## Three real failures from an earlier development run

The earliest complete replay scored **80.84 / 85** on the public machine-scored subset. We inspected the official scorer's reported failures, fixed the general logic, and replayed the full dataset.

1. **Day 1, question q01-01:** "Who handles billing for NB?" The first implementation abstained because it did not infer a short client code from structured source identifiers. Fix: derive abbreviations from ingested task IDs, not from a hard-coded client list. After the fix, the question was correctly answered using the launch-plan record.
2. **Day 2, brief ev-cl-ro-03:** incorrectly returned `nothing_new` because the code treated another *upcoming* meeting earlier that morning as if it had already happened. Fix: only use meetings in the observed past when establishing the prior-meeting boundary.
3. **Day 5, question q05-02:** selected the wrong person from two employees sharing a first name. Fix: use client-related evidence and contextual role compatibility, not numerical ID ordering.

These were observed development failures, not invented examples. The final public run has no scorer-reported failures. The tests suite also checks the corrected behaviors independently of answer files.

## Measured vs not measured

- **Measured:** actual outputs, official public scorer, 13 offline tests, JSON validity, source/privacy checks, CLI process behavior and wall time.
- **Not measured:** official hidden-day performance, model API accuracy (optional adapter not activated), cloud deployment, live model spend, human judge evaluation, or long-term production reliability.

## Commands to reproduce

```bash
python -m unittest discover -s tests -v
python tools/run_days.py --cmd "python tracemind.py" --days 1-5 --out runs/mine
python tools/score.py --run runs/mine --days 1-5
```
