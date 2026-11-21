# TraceMind — Suggested 5–7 minute demo plan

**This is a recording outline, not a finished demo video.** The candidate must record and narrate their own implementation.

1. **0:00–0:50 — Problem and design:** one sentence on meeting briefs/questions; explain deterministic source selection and why privacy runs before retrieval.
2. **0:50–1:40 — Harness replay:** demonstrate `python tools/run_days.py --cmd "python tracemind.py" --days 1-5 --out runs/demo`, then `python tools/score.py --run runs/demo --days 1-5`. Explain the public-only score and human/hidden categories.
3. **1:40–2:35 — Real meeting brief:** show `runs/demo/day-03/brief-p02-2026-11-05.json`, pointing to records supporting the updated billing owner, shifted launch, and task deadlines.
4. **2:35–3:25 — Feedback persistence:** show that day N's feedback enters on the subsequent ingest. Check a corrected billing owner; show that person preferences are scoped.
5. **3:25–4:10 — Honest abstention:** demonstrate an Alderpoint `nothing_new` brief or an ambiguous-Friday clarification instead of a guess.
6. **4:10–5:05 — Privacy and forgetting:** compare private travel data for Meera vs another employee. Run `forget` and demonstrate that later asks stop returning the forgotten trip. The test suite additionally checks restricted records and cross-org isolation.
7. **5:05–6:10 — Engineering proof:** run `python -m unittest discover -s tests -v`; explain one regression test, e.g., a stale task comment cannot undo `done`.
8. **6:10–6:40 — Tradeoffs:** the offline core reports 0 model tokens truthfully; optional model fallback uses authorised context, token limits and source checks. Hidden-day performance remains unknown.

## Live coding preparation

Likely safe modifications: add an extra preference, recognize an additional commitment phrase, or introduce a new privacy test. For each change: write/update a test first, make the smallest fix, rerun tests, then rerun the official five-day scorer. Do not edit the supplied harness or answer keys.
