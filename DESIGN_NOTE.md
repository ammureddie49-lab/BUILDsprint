# TraceMind — One-Page Design Note

**Challenge:** The Work Assistant That Remembers (BuildSprint R3)  
**Approach:** Evidence-first, persistent, scoped memory with selective language-model support.

### 1. How do we choose what goes into a meeting brief?

Identify the requesting person, the meeting's client and time, and the last meeting that actually occurred with that client. Retrieve only previously ingested, accessible records. Extract meaningful task changes (`owner`, `due`, `status`), explicit decisions, authoritative announcements, document-version differences, and unfulfilled commitments. Treat routine status comments, newsletters, notifications and unrelated clients as noise. Compare source records rather than simply summarizing recent messages; rank blockers and major decisions highly. Respect person-specific feedback such as maximum brief length. Every change, promise, suggested document and meeting question retains source record IDs. If nothing material changed, emit a valid `nothing_new` brief rather than padding it.

### 2. How do we save memory and decide who can see it?

SQLite persists raw daily records, source IDs, visibility metadata, feedback and forget filters across CLI processes. All reads enforce the requesting person's organization and visibility (`team`, `private` author, `restricted` readers). A person's private note and preferences must not affect another person. Feedback arrives only on the next ingest, as the harness requires. A forget request records minimal suppression information and excludes matching source records from future queries, briefs, and audit output, even after reingestion. Only filtered records may enter an optional LLM prompt. Test cross-org access, private data, restricted readers and forgetting from day one.

### 3. How do we know which information is newest and correct?

Ingest day N only; do not inspect later days or the answer key. For structured tasks, apply each `change.field` to that field alone, so a stale `comment` snapshot cannot undo a prior owner or status change. Track document versions by IDs and versions; favor explicit, current, authoritative corrections over stale descriptions. When accounts conflict, manager/founder evidence and supported personal corrections outweigh old statements. Dates are interpreted in the asker's `zone`, including Hinglish `kal tak` and relative weekday commitments. Prefer a grounded answer with a real source; clarify genuinely ambiguous questions and abstain rather than inventing unsupported facts.

### 4. What will we not do?

We will not build a UI, overengineer a multi-agent swarm, send an entire daily feed to a model, invent an answer, follow command-like text inside records, leak another person's information, or hard-code answers for known days. The offline deterministic path is functional without an API key. Optional model requests receive only selected authorized evidence, are bounded to the official token budget, and must pass source validation. Every command emits JSON with honest model usage and measured processing time. Evaluation is against days 1–5; hidden-day performance is unknown until the organizers replay the submission.
