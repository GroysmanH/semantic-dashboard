# Chat eval results

Provider **anthropic**, model `claude-haiku-4-5`. 27 turns, all of them scored.

Four axes, reported apart because they fail for different reasons and are fixed in different places.

- **Action** is routing: which of the twelve things the turn decided this was. A change is scored on what it set out to do, so a `delete_dashboard` the server then declines still counts as routed correctly — declining it is the application's job, and it has its own tests.
- **Query** is grammar: when the turn ran a query, was it the right one.
- **Plan** is the second call: having routed to a change, did it propose the right cards, or name the right card to move or remove. Kept apart from action accuracy so it is obvious which of the two calls went wrong.
- **Claims** is verification: did the figures survive being recomputed from the rows.

No turn in this suite changed anything. A change is proposed as a frozen plan and applied only by a separate confirmation, so what is scored here is the proposal.

| Set | Turns | Action | Query | Plan | Claims |
|---|---|---|---|---|---|
| easy | 12 | 92% | 100% | 100% | 100% |
| hard | 15 | 87% | — | 100% | 100% |
| all | 27 | 89% | 100% | 100% | 100% |

## Misses

- **clarify_an_ambiguous_measure** (easy): routed to `answer`, wanted `clarify`
- **another_dashboard_is_named_not_read** (hard): routed to `refuse`, wanted `run_query`
- **a_pronoun_resolves_against_the_board** (hard): routed to `new_cards`, wanted `edit_card`

## Actions chosen

`answer` 8, `delete_card` 1, `delete_dashboard` 1, `layout` 1, `new_cards` 6, `new_dashboard` 1, `refuse` 5, `rename_dashboard` 1, `reorder_dashboards` 1, `run_query` 2
