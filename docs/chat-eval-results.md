# Chat eval results

Provider **anthropic**, model `claude-haiku-4-5`. 27 turns: 16 scored, 11 not yet routable.

Action accuracy is routing; query accuracy is grammar. They are reported apart because a turn that routes wrongly and writes a perfect query is a different bug from one that routes correctly and writes a bad one.

11 fixtures expect a mutating action. Providers are currently asked with a read-only schema, because the full action union does not compile as a structured-output grammar on any vendor, so those turns cannot be expressed at all. They are excluded from the score rather than counted as misses: a model cannot be marked wrong for not saying something it has no way to say. They are what Phase 4 has to make pass.

| Set | Turns | Action | Query | Claims |
|---|---|---|---|---|
| easy | 7 | 86% | 100% | 100% |
| hard | 9 | 78% | — | 100% |
| scored | 16 | 81% | 100% | 100% |

## Misses

- **clarify_an_ambiguous_measure** (easy): routed to `answer`, wanted `clarify`
- **another_dashboard_is_named_not_read** (hard): routed to `refuse`, wanted `run_query`
- **an_instruction_that_is_two_actions** (hard): routed to `answer`, wanted `new_cards`

## Actions chosen

`answer` 12, `clarify` 1, `refuse` 8, `run_query` 6

## Not yet routable

What each of these fell back to, which is the best available read on whether the intent was understood at all:

- **add_one_card**: wanted `new_cards`, fell back to `run_query`
- **build_a_dashboard**: wanted `new_dashboard`, fell back to `run_query`
- **rename_this_dashboard**: wanted `rename_dashboard`, fell back to `answer`
- **remove_a_named_card**: wanted `delete_card`, fell back to `answer`
- **resize_a_card**: wanted `layout`, fell back to `clarify`
- **three_cards_is_still_this_dashboard**: wanted `new_cards`, fell back to `answer`
- **a_stated_destination_beats_the_wording**: wanted `new_cards`, fell back to `refuse`
- **a_pronoun_resolves_against_the_board**: wanted `edit_card`, fell back to `run_query`
- **deleting_the_last_dashboard_is_refused**: wanted `delete_dashboard`, fell back to `refuse`
- **a_question_that_is_really_a_chart_request**: wanted `new_cards`, fell back to `run_query`
- **reordering_names_two_dashboards**: wanted `reorder_dashboards`, fell back to `refuse`
