"""Ghost Data Still Exists - deletion audit agent.

Usage (from the repository root or anywhere):
    python code/main.py                 # audit dataset/deletion_requests.csv -> output.csv
    python code/main.py --sample        # audit the 32 sample requests and score vs sample_output.csv
    python code/main.py --case case_33  # one case, prints the trace
    python code/main.py --offline       # never call a model (cache only; --strict fails on a miss)
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CODE_DIR.parent
sys.path.insert(0, str(CODE_DIR))

from audit.activity import ActivityIndex  # noqa: E402
from audit.attribution import assign_accounts, self_evaluate  # noqa: E402
from audit.data import DATASET_DIR, Request, load_dataset  # noqa: E402
from audit.engine import Engine, EvidenceStore  # noqa: E402
from audit.identity import IdentityResolver  # noqa: E402
from audit.policy import load_rules  # noqa: E402
from audit.steps import steps_to_json  # noqa: E402
from llm.intent import IntentGate  # noqa: E402
from parsers import UnknownFormat, parse_file  # noqa: E402

log = logging.getLogger("main")


def die(problem: str, cause: str, fix: str, code: int = 2):
    sys.stderr.write(f"error: {problem}\n  cause: {cause}\n  fix: {fix}\n")
    sys.exit(code)


def case_sort_key(case_id: str):
    head, _, num = case_id.rpartition("_")
    return (head, int(num)) if num.isdigit() else (case_id, 0)


def build_engine(ds, args, media_results=None, intent_model=None):
    resolver = IdentityResolver(ds.identities, ds.audit_log)
    activity = ActivityIndex(ds.activities)
    load_rules(ds.rules)
    parsed = {}
    coverage = []
    for ef in ds.evidence:
        try:
            pr = parse_file(ef)
        except UnknownFormat:
            pr = (media_results or {}).get(ef.file_id)
            if pr is None:
                coverage.append({"file_id": ef.file_id, "path": ef.file_path, "observations": 0, "note": "media not read"})
                continue
        except Exception as exc:  # per-file isolation: never abort the run
            log.warning("parse failed for %s (%s): %s", ef.file_id, ef.file_path, exc)
            coverage.append({"file_id": ef.file_id, "path": ef.file_path, "observations": 0, "note": f"parse failed: {exc}"})
            continue
        extra = (media_results or {}).get(ef.file_id)
        if extra is not None:  # model-read records supplement the deterministic parse
            known_ids = {o.resource_id for o in pr.observations}
            pr.observations.extend(o for o in extra.observations if o.resource_id not in known_ids)
        parsed[ef.file_id] = pr
        coverage.append({"file_id": ef.file_id, "path": ef.file_path, "observations": len(pr.observations),
                         "linked": sum(1 for o in pr.observations if o.subject_refs), "scopes": len(pr.scopes),
                         "unparsed_lines": len(pr.unparsed_lines), "notes": sorted(set(pr.notes))})
    store = EvidenceStore(ds.evidence, parsed)
    gate = IntentGate(model_fn=intent_model, strict_offline=bool(args.offline and args.strict))
    # account attribution: evidence-known accounts for every request (incl. samples) eliminate candidates
    seen_ids = set()
    all_requests = []
    for r in list(ds.requests) + list(ds.sample_requests):
        if r.case_id not in seen_ids:
            seen_ids.add(r.case_id)
            all_requests.append(r)
    known = {}
    probe = Engine(ds, store, resolver, activity, lambda t: ("DELETION", "probe"), {}, {})
    for r in all_requests:
        ids = resolver.resolve(r.email)
        if not ids:
            continue
        linked = probe.linked_observations(ids, r.audit_at, since=r.reported_at) or probe.linked_observations(ids, r.audit_at, since=r.requested_at)
        accs = sorted({o.resource_id for o in linked if o.resource_id.startswith("account_")} |
                      {o.flags["linked_account"] for o in linked if o.flags.get("linked_account")})
        import re
        for a in activity.links_for(ids.all_keys()):
            accs += re.findall(r"\baccount_\d+\b", a.details)
        if accs:
            known[r.case_id] = sorted(set(accs))[0]
    deletion_requests = [r for r in all_requests if gate(r.request_text)[0] == "DELETION"]
    gate.stats = {"regex": 0, "model": 0, "fallback": 0}
    attributed, candidates = assign_accounts(deletion_requests, known, activity)
    account_map = {**known, **attributed}
    # identity-less observed resources (anonymised analytics): cadence attribution
    from audit.attribution import assign_by_cadence
    from audit.observations import ANALYTICS_PROFILE
    linked_resources = set()
    has_category = {}
    owned_actions = {}
    for r in deletion_requests:
        ids = resolver.resolve(r.email)
        if not ids:
            continue
        linked = probe.linked_observations(ids, r.audit_at, since=r.reported_at) or probe.linked_observations(ids, r.audit_at, since=r.requested_at)
        has_category[r.case_id] = {o.category for o in linked}
        for o in linked:
            linked_resources.add(o.resource_id)
            if not o.resource_id.startswith("account_"):
                for a in activity.worker_actions(o.resource_id):
                    owned_actions[a.activity_id] = r.case_id
    cand = {}
    for o in store.observations():
        if o.category == ANALYTICS_PROFILE and o.present and not o.identifiers and not o.subject_refs and o.resource_id not in linked_resources:
            rows = [a for a in activity.worker_actions(o.resource_id) if a.event_type == "ANONYMIZE_RECORD"]
            if rows:
                cand.setdefault(ANALYTICS_PROFILE, {})[o.resource_id] = rows[-1].event_time
    cand = {k: list(v.items()) for k, v in cand.items()}
    extra = assign_by_cadence(deletion_requests, owned_actions, activity, cand, has_category)
    engine = Engine(ds, store, resolver, activity, gate, account_map, candidates, extra)
    engine.coverage = coverage
    engine.attribution_eval = self_evaluate(deletion_requests, known, activity) if args.attribution_eval else None
    engine.known_accounts = known
    return engine


def write_output(path: Path, rows):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for cid, status, steps in rows:
            w.writerow([cid, status, steps_to_json(steps)])
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ghost Data Still Exists - deletion audit agent")
    ap.add_argument("--dataset", default=str(DATASET_DIR), help="dataset directory (default: <repo>/dataset)")
    ap.add_argument("--requests", default="deletion_requests.csv", help="requests file inside the dataset dir")
    ap.add_argument("--out", default=str(REPO_ROOT / "output.csv"), help="output CSV path (default: <repo>/output.csv)")
    ap.add_argument("--sample", action="store_true", help="audit sample_deletion_requests.csv, write output_sample.csv and score it")
    ap.add_argument("--case", help="audit a single case id and print its trace")
    ap.add_argument("--limit", type=int, help="audit only the first N requests")
    ap.add_argument("--offline", action="store_true", help="never call a model; use the local cache only")
    ap.add_argument("--strict", action="store_true", help="with --offline: fail on any cache miss instead of falling back")
    ap.add_argument("--refresh", action="store_true", help="ignore cached model answers")
    ap.add_argument("--no-llm", action="store_true", help="deterministic only: regex intent, no vision")
    ap.add_argument("--trace-dir", default=str(CODE_DIR / "audit_trace"), help="where per-case trace JSON is written")
    ap.add_argument("--no-trace", action="store_true")
    ap.add_argument("--sort", choices=["numeric", "string"], default="numeric", help="output row order by case_id")
    ap.add_argument("--attribution-eval", action="store_true", help="measure account attribution on the evidence-known subset")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    t0 = time.time()
    try:
        ds = load_dataset(Path(args.dataset), "sample_deletion_requests.csv" if args.sample else args.requests)
    except FileNotFoundError as exc:
        die("dataset not found", str(exc).splitlines()[0], f"place the organizer dataset at {args.dataset} or pass --dataset <dir>", 3)

    intent_model = None
    media_results = None
    llm_client = None
    if not args.no_llm:
        try:
            from llm.client import make_client
            from llm.intent_model import make_intent_model
            from llm.vision import read_media
            llm_client = make_client(offline=args.offline, refresh=args.refresh, cache_dir=CODE_DIR / "llm_cache")
            intent_model = make_intent_model(llm_client)
            media_results = read_media(ds.evidence, llm_client)
        except ImportError:
            log.info("LLM layer not available; running deterministic only")
        except RuntimeError as exc:
            log.warning("LLM layer disabled: %s", exc)

    engine = build_engine(ds, args, media_results=media_results, intent_model=intent_model)
    requests = list(ds.requests)
    if args.case:
        requests = [r for r in requests if r.case_id == args.case]
        if not requests:
            die(f"case {args.case} not found", "not in the requests file", "check the case id")
    if args.limit:
        requests = requests[: args.limit]

    results = []
    trace_dir = Path(args.trace_dir)
    if not args.no_trace:
        trace_dir.mkdir(parents=True, exist_ok=True)
    review = []
    for r in requests:
        res = engine.audit_case(r)
        results.append(res)
        if res.trace.flags:
            review.append({"case_id": r.case_id, "flags": res.trace.flags, "candidates": res.trace.account_candidates})
        if not args.no_trace:
            with open(trace_dir / f"{r.case_id}.json", "w", encoding="utf-8") as fh:
                json.dump(res.trace.__dict__, fh, ensure_ascii=False, indent=1, default=str)
        if args.case:
            print(json.dumps(res.trace.__dict__, ensure_ascii=False, indent=1, default=str))
            print(steps_to_json(res.steps))

    rows = [(res.case_id, res.status, res.steps) for res in results]
    rows.sort(key=(lambda x: case_sort_key(x[0])) if args.sort == "numeric" else (lambda x: x[0]))
    out_path = Path(args.out) if not args.sample else REPO_ROOT / "output_sample.csv"
    if not args.case:
        if not args.limit and len(rows) != len(ds.requests):
            die("row count mismatch", f"{len(rows)} rows for {len(ds.requests)} requests", "report this as a bug", 4)
        write_output(out_path, rows)

    from collections import Counter
    st = Counter(res.status for res in results)
    print(f"audited {len(results)} cases in {time.time() - t0:.1f}s: {dict(st)}; intent sources {engine.intent_fn.stats}; "
          f"accounts known={len(engine.known_accounts)} attributed={len(engine.account_map) - len(engine.known_accounts)}; review items={len(review)}")
    if llm_client is not None:
        print("model calls:", llm_client.summary())
    if engine.attribution_eval:
        print("attribution self-eval:", engine.attribution_eval)
    if not args.case:
        print(f"wrote {out_path}")
        with open(CODE_DIR / "evaluation" / "coverage_report.json", "w", encoding="utf-8") as fh:
            json.dump({"files": engine.coverage, "review": review, "intent_stats": engine.intent_fn.stats}, fh, indent=1, ensure_ascii=False)
    if args.sample and ds.sample_output:
        from evaluation.scorer import format_report, load, score
        gold = {cid: (row["deletion_status"], json.loads(row["steps_json"])) for cid, row in ds.sample_output.items()}
        rep = score(load(out_path), gold)
        print(format_report(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
