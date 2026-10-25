# Token usage and cost report

Final full-dataset run that produced `output.csv` (518 requests). Every live model call is
recorded in `code/llm_cache/usage_ledger.jsonl`; cached answers are replayed on later runs and
do not appear here. The deterministic judgement layer makes no model calls.

| Provider | Model | Purpose(s) | Calls | Input tokens | Output tokens | Avg latency (ms) | Price in/out ($/Mtok) | Est. cost ($) |
|---|---|---|---|---|---|---|---|---|
| openrouter | nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free | intent:1, vision:5 | 6 | 12788 | 11846 | 44087 | 0.0 / 0.0 | 0.0000 |
| openrouter | nvidia/nemotron-3.5-super-vl:free | pdf:1, vision:11 | 12 | 27373 | 16045 | 13112 | 0.0 / 0.0 | 0.0000 |

## Totals

- Model calls: **18**
- Input tokens: **40161**, output tokens: **27891**, total: **68052**
- Average tokens per request (518 requests): **131.4**
- Average model calls per request: **0.03**
- Estimated total cost: **$0.0000**; per request: **$0.000000**

Prices are list prices per million tokens configured in `code/config.json`; free-tier OpenRouter models are priced at $0.

## What the model is used for

- `intent`: request texts the multilingual regex gate could not settle (hedged or mixed requests). One short call per distinct text.
- `vision`: console screenshots (PNG) read into structured records.
- `pdf`: PDF text layers read into structured records.

All other work (identity resolution, evidence parsing, workflow reconstruction, policy, ordering) is deterministic Python.
