"""Generate evaluation/usage_report.md from the model-call ledger of a run.

    python code/evaluation/usage.py            # writes usage_report.md (refuses to overwrite without --force)
    python code/evaluation/usage.py --print    # print only
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))
from llm.client import load_config  # noqa: E402

LEDGER = CODE / "llm_cache" / "usage_ledger.jsonl"
OUT = CODE / "evaluation" / "usage_report.md"


def build(requests: int = 518) -> str:
    cfg = load_config()
    rows = []
    if LEDGER.exists():
        with open(LEDGER, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    per = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0, "latency": 0, "purposes": defaultdict(int)})
    for r in rows:
        k = (r["provider"], r["model"])
        per[k]["calls"] += 1
        per[k]["in"] += r.get("input_tokens", 0)
        per[k]["out"] += r.get("output_tokens", 0)
        per[k]["latency"] += r.get("latency_ms", 0)
        per[k]["purposes"][r.get("purpose", "")] += 1

    def price(provider, model):
        table = cfg.get(provider, {}).get("prices_per_mtok", {})
        return table.get(model) or table.get("default") or [0.0, 0.0]

    lines = ["# Token usage and cost report", "",
             "Final full-dataset run that produced `output.csv` (518 requests). Every live model call is",
             "recorded in `code/llm_cache/usage_ledger.jsonl`; cached answers are replayed on later runs and",
             "do not appear here. The deterministic judgement layer makes no model calls.", "",
             "| Provider | Model | Purpose(s) | Calls | Input tokens | Output tokens | Avg latency (ms) | Price in/out ($/Mtok) | Est. cost ($) |",
             "|---|---|---|---|---|---|---|---|---|"]
    tot_calls = tot_in = tot_out = 0
    tot_cost = 0.0
    for (prov, model), v in sorted(per.items()):
        pin, pout = price(prov, model)
        cost = v["in"] / 1e6 * pin + v["out"] / 1e6 * pout
        tot_calls += v["calls"]; tot_in += v["in"]; tot_out += v["out"]; tot_cost += cost
        purposes = ", ".join(f"{k}:{n}" for k, n in sorted(v["purposes"].items()))
        lines.append(f"| {prov} | {model} | {purposes} | {v['calls']} | {v['in']} | {v['out']} | {v['latency'] // max(v['calls'], 1)} | {pin} / {pout} | {cost:.4f} |")
    lines += ["", "## Totals", "",
              f"- Model calls: **{tot_calls}**",
              f"- Input tokens: **{tot_in}**, output tokens: **{tot_out}**, total: **{tot_in + tot_out}**",
              f"- Average tokens per request (518 requests): **{(tot_in + tot_out) / requests:.1f}**",
              f"- Average model calls per request: **{tot_calls / requests:.2f}**",
              f"- Estimated total cost: **${tot_cost:.4f}**; per request: **${tot_cost / requests:.6f}**",
              "", "Prices are list prices per million tokens configured in `code/config.json`; free-tier OpenRouter models are priced at $0.",
              "", "## What the model is used for", "",
              "- `intent`: request texts the multilingual regex gate could not settle (hedged or mixed requests). One short call per distinct text.",
              "- `vision`: console screenshots (PNG) read into structured records.",
              "- `pdf`: PDF text layers read into structured records.",
              "", "All other work (identity resolution, evidence parsing, workflow reconstruction, policy, ordering) is deterministic Python."]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--print", action="store_true")
    a = ap.parse_args(argv)
    text = build()
    if a.print:
        print(text)
        return 0
    if OUT.exists() and not a.force:
        print(f"{OUT} exists; pass --force to overwrite (it documents the final online run)")
        return 1
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
