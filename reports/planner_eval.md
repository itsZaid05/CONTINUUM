# Planner evaluation — manifest-driven generic planner

Gold `data/gold/planner_gold.jsonl` (hash `e92fa9a38623`), reference date 2026-09-25, 65 turns. `dev` was used during development; `heldout` was run once afterwards and includes two domains never seen during development (dining, home).

| Metric | planner · dev | planner · heldout | planner · all | baseline · all |
|---|---|---|---|---|
| Fully correct turns | 1.0 | 0.9643 | 0.9846 | 0.0615 |
| Action accuracy (plan / clarify / no-op) | 1.0 | 1.0 | 1.0 | 0.2769 |
| Goal-tool accuracy | 1.0 | 0.963 | 0.9841 | 0.3333 |
| Plan exact match (tool sequence) | 1.0 | 0.9583 | 0.9825 | 0.3509 |
| Argument precision | 1.0 | 1.0 | 1.0 | 0.7 |
| Argument recall | 1.0 | 0.9592 | 0.981 | 0.1333 |
| Argument F1 | 1.0 | 0.9792 | 0.9904 | 0.224 |
| Clarification precision | 1.0 | 1.0 | 1.0 | 0.0 |
| Clarification recall | 1.0 | 1.0 | 1.0 | 0.0 |
| Unnecessary clarification rate (target < 5%) | 0.0 | 0.0 | 0.0 | 0.0 |
| Irreversible-confirmation accuracy | 1.0 | 1.0 | 1.0 | 0.9649 |
| Unsafe state-changing plans (target 0) | 0 | 0 | 0 | 0 |
| Crashes | 0 | 0 | 0 | 45 |
| Planning latency p95 (ms) | 1.814 | 1.157 | 1.689 | 0.0 |

## By domain (planner)

| Domain | Turns | Fully correct | Goal acc. | Arg F1 |
|---|---|---|---|---|
| builtin | 15 | 1.0 | 1.0 | 1.0 |
| dining | 5 | 1.0 | 1.0 | 1.0 |
| home | 11 | 1.0 | 1.0 | 1.0 |
| incar | 12 | 1.0 | 1.0 | 1.0 |
| support | 13 | 0.9231 | 0.9231 | 0.9375 |
| troubleshooting | 9 | 1.0 | 1.0 | 1.0 |

## Incorrect turns (1)

| Id | Split | Utterance | What went wrong |
|---|---|---|---|
| h22 | heldout | Add a note to TCK-5120 saying the customer called back | goal create_ticket != update_ticket; tools ['create_ticket'] != ['update_ticket']; update_ticket.note=None lacks 'customer called back'; update_ticket.ticket_id=None expected 'tck-5120' |

Baseline = the runtime's pre-planner path (`choose_read_tool` + `bind_args`): first read-only tool in the registry, arguments copied from state by exact name, `ValueError` on a missing required argument (counted as a crash).
