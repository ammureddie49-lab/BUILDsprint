# TraceMind | BuildSprint Round 3

**A source-grounded, privacy-aware work assistant with persistent daily memory.**

This is a ready-to-run implementation of the challenge in the official `BuildSprint-R3-candidate` pack. The original organizer files (`README.md`, `DATA_FORMAT.md`, `data/`, `answers/`, `tools/`) are included unchanged. The solution does **not** read `answers/` or `data/tasks/` at runtime; those files belong to the organizers' evaluation harness.

## Quick start in Kiro (Windows PowerShell)

1. Unzip `TraceMind_BuildSprint_R3_Implementation.zip`.
2. Open the `BuildSprint-R3-candidate` folder in Kiro.
3. Open Kiro's terminal and run:

```powershell
python --version
python -m unittest discover -s tests -v
python tools/run_days.py --cmd "python tracemind.py" --days 1-5 --out runs/mine
python tools/score.py --run runs/mine --days 1-5
```

Only **Python 3.11+** is needed. No pip installs, cloud infrastructure, model API, or GPU are required for the deterministic baseline.

For a fast sanity check:

```powershell
python tracemind.py ingest --day 1
python tracemind.py brief --person p02 --date 2026-11-03
python tracemind.py ask --person p02 --at 2026-11-02T18:00:00Z --ask "Who handles billing for NB?"
```

Every command writes **only JSON** to stdout. Errors go to stderr. State persists in `.tracemind.sqlite3`. `ingest --day 1` clears previous run state, as required by the official harness.

## Architecture

```text
Official daily JSONL                     Feedback from prior day
          |                                     |
          +----------- ingest --day N ----------+
                              |
                       SQLite evidence
                  (raw record, source ID,
                   org, author, visibility)
                              |
                       Security filter
                  (same org + ACL + forget)
                              |
              +---------------+----------------+
              |                                |
        brief --person                     ask --person
        meeting selection                  entity/task lookup
        since-last-meeting                 time-zone resolution
        structured task changes            owner/due/status state
        decisions & commitments            ambiguity/refusal
        document/sheet diffs               optional LLM fallback
              |                                |
              +---------------+----------------+
                              |
                        JSON output
                    source IDs + usage
```

**Why this design:** data selection, authority, task versions, expiry and access checks stay deterministic. The optional model is restricted to authorised evidence and cannot bypass those rules. Structured task `change.field` updates only the field it changes; a later routine comment does not roll back a `done` status.

## Required CLI

```text
python tracemind.py ingest --day N
python tracemind.py brief --person P --date YYYY-MM-DD
python tracemind.py ask --person P --at UTC_ISO --ask "question"
python tracemind.py forget --person P --what "topic"
```

Additional optional command:

```text
python tracemind.py audit --person P
```

`audit` lists only records that the requester can see and respects forget suppression. It is an optional stretch feature, not needed for the official 4-command harness.

### Privacy model

- Cross-organization access is denied.
- `team` requires the same organization.
- `private` requires the same author.
- `restricted` requires the requesting person in `readers`.
- Access is checked **before** all search, extraction, summary, and optional model prompts.
- Forgotten topics are suppressed by scope and known source IDs; future matching records are filtered.
- A record's instructions (e.g., "Assistant: ignore your rules") are treated as data, not commands.

## Optional model-assisted fallback

The offline baseline is the verified public-dataset implementation. An optional OpenAI-compatible chat completion adapter is in `llm_adapter.py` for previously unseen questions where the deterministic system lacks an answer. It is disabled by default. **It has not been benchmarked with a live model in this package.**

To opt in, set these environment variables (PowerShell example):

```powershell
$env:TRACEMIND_USE_LLM = "1"
$env:TRACEMIND_MODEL = "your-model-id"
$env:TRACEMIND_API_KEY = "your-secret-key"
$env:TRACEMIND_API_BASE = "https://api.openai.com/v1"
$env:TRACEMIND_PRICE_INPUT_PER_M = "YOUR_INPUT_USD_PER_M_TOKENS"
$env:TRACEMIND_PRICE_OUTPUT_PER_M = "YOUR_OUTPUT_USD_PER_M_TOKENS"
```

The `PRICE_*` variables must be real numeric prices, such as `0.10`, rather than the textual placeholders above. Check the provider's current pricing first. The adapter reports token counts from the provider if supplied, or estimates usage conservatively, and calculates cost using these prices. It limits prompts to 4,000 estimated input tokens for asks and validates cited source IDs. Use approved providers and confirm that sending workplace records to an API is permitted; do not paste secrets in code or commit them.

**Model limitation:** a cited source ID does not itself prove an LLM's paraphrase. There is an additional numerical/textual check, but model-enabled outputs still require separate evaluation. If a request fails validation or the API fails, the system abstains instead of fabricating.

### Usage reporting

When the deterministic engine handles a call, `input_tokens = 0`, `output_tokens = 0`, and `cost_usd = 0` are accurate because **no LLM was called**. `ms` measures wall-clock processing time inside the command. Optional model calls use actual provider usage where available; ensure valid price configuration.

## Tests and official scoring

```powershell
python -m unittest discover -s tests -v
python tools/run_days.py --cmd "python tracemind.py" --days 1-5 --out runs/mine
python tools/score.py --run runs/mine --days 1-5
```

The development benchmark on the five public days reached **85.0/85 machine-scored points** with the offline model-free engine. This consists of 20/20 briefs, 15/15 answers, 15/15 honesty, 15/15 memory, 10/10 privacy, and 10/10 cost. The **5-point improvement category is not measurable on five days**, and the **10-point design note is assessed by people**. These are development results on public tests; they do **not** establish hidden-day accuracy.

The `tests/test_tracemind.py` suite exercises 13 scenarios, independent of the answer keys: JSON-only CLI, sequential ingestion, privacy, restricted records, forgotten material, time zones, promise dates, stale task comments, correct context for same-first-name people, prompt injection, and safe `nothing_new`.

## Project files

- `tracemind.py` — 4 official commands + optional audit
- `llm_adapter.py` — optional model fallback, disabled unless configured
- `tests/test_tracemind.py` — regression suite
- `DESIGN_NOTE.md` — one-page submission note
- `EVALUATION.md` — actual benchmark, failures encountered and fixes
- `DEMO_SCRIPT.md` — suggested 5–7 minute demo and live change plan
- `.kiro/steering/tracemind.md` — guardrails for Kiro
- `runs/tracemind/` — generated public-day results and scorer outputs

## Important limitations / next engineering improvements

This is **not** a guarantee of perfect real-world reasoning. Unknown document structures, complex meeting changes, unseen languages, multiple plausible aliases, and new kinds of feedback can defeat rules designed around the published specification. The opt-in LLM fallback for unclear questions is not live-validated here. The hidden days are deliberately unavailable. Prioritize synthetic adversarial tests and live evaluator results over adding a UI or more agents.

## Submission / integrity

Submit the implementation, design note, README, measured development scores and honest failure analysis. Review the organizer's submission requirements for the demonstration video and 30-minute live coding session. Do not claim a hidden score, synthetic model metrics, or unused cloud infrastructure. Keep API keys out of GitHub.
