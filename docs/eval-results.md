# Eval results

4 answerable questions and 0 that must be refused, run against the semantic layer.

| Model | Exact | Relaxed | Execution | Correct refusals | Retries | Raw text-to-SQL |
|---|---|---|---|---|---|---|
| `claude-sonnet-5` | 25% | 25% | 100% | — | 0 | 75% |

## Where the raw text-to-SQL arm failed

The grammar makes these impossible rather than unlikely: there is no free-text table slot, and join paths are declared rather than generated.

| Model | Invalid SQL | Missing table or column | Unreachable |
|---|---|---|---|
| `claude-sonnet-5` | 0 | 0 | 0 |
