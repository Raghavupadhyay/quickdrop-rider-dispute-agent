#!/usr/bin/env python3
"""Replay data/conversations.json against a running dispute desk and report.

    python evals/run.py http://localhost:8000 [--payswift http://localhost:8081]
                        [--conversations data/conversations.json] [--json out.json]

Uses only the public interface (POST /messages, GET /trace/{rider_id},
GET /ops/pending) and PaySwift (GET /v1/payouts), so it can run against any
implementation. Standard library only.

For each conversation it measures, as deltas around the conversation:
  payout      rupees that reached PaySwift for the rider
  approval    rupees newly waiting for ops approval (null if none)
  escalation  whether a new escalation appeared
  mentions    facts the rider should have been told (substring of any reply)
and checks the trace shape (at, type, name, input, output; known types).

The data README says to run each conversation against a fresh system. When the
rider already has PaySwift payouts before a conversation starts (a re-run on a
used system), a payout mismatch is reported as STALE instead of FAIL.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


STEP_TYPES = {"message_in", "tool_call", "decision", "reply", "error"}
FAILED_PAYOUT_STATUSES = {"failed", "rejected", "cancelled", "reversed"}


def http(method, url, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"null")
        except json.JSONDecodeError:
            payload = None
        return exc.code, payload


def payswift_total(payswift_url, rider_id):
    status, body = http("GET", f"{payswift_url}/v1/payouts?rider_id={rider_id}")
    if status != 200:
        raise RuntimeError(f"PaySwift list failed: {status} {body}")
    return sum(
        int(p.get("amount", 0))
        for p in body.get("data", [])
        if str(p.get("status", "")).lower() not in FAILED_PAYOUT_STATUSES
    )


def pending_for(service_url, rider_id):
    status, body = http("GET", f"{service_url}/ops/pending")
    if status != 200:
        raise RuntimeError(f"/ops/pending failed: {status} {body}")
    return [item for item in body if item.get("rider_id") == rider_id]


def check_trace(service_url, rider_id, message_ids):
    """Returns a list of problems with the trace (empty when fine)."""
    status, steps = http("GET", f"{service_url}/trace/{rider_id}")
    problems = []
    if status != 200 or not isinstance(steps, list):
        return [f"/trace returned {status}"]
    if not steps:
        return ["trace is empty"]
    for step in steps:
        missing = {"at", "type", "name", "input", "output"} - set(step)
        if missing:
            problems.append(f"step missing {sorted(missing)}")
            break
        if step["type"] not in STEP_TYPES:
            problems.append(f"unknown step type {step['type']!r}")
            break
    ats = [s.get("at", "") for s in steps]
    if ats != sorted(ats):
        problems.append("steps not in time order")
    types = [s.get("type") for s in steps]
    distinct = len(set(message_ids))  # a redelivered message is one message
    if types.count("message_in") < distinct:
        problems.append(f"{types.count('message_in')} message_in steps for {distinct} distinct messages")
    if "reply" not in types:
        problems.append("no reply step")
    return problems


def candidates(expected):
    """`one_of` lists alternative end states; everything else applies to all."""
    base = {k: v for k, v in expected.items() if k != "one_of"}
    if "one_of" in expected:
        return [{**base, **alt} for alt in expected["one_of"]]
    return [base]


def matches(expected, got):
    if expected.get("payout", 0) != got["payout"]:
        return False
    if (expected.get("approval") or None) != got["approval"]:
        return False
    escalation = expected.get("escalation", "optional")
    if escalation == "required" and not got["escalation"]:
        return False
    if escalation == "no" and got["escalation"]:
        return False
    return True


def run_conversation(service_url, payswift_url, conv, settle_seconds=12.0):
    rider_id = conv["rider_id"]
    turns = [t for t in conv["turns"] if t.get("from") == "rider"]

    payout_before = payswift_total(payswift_url, rider_id)
    pending_before = {item["id"] for item in pending_for(service_url, rider_id)}

    replies, latencies, errors = [], [], []
    for turn in turns:
        body = {
            "message_id": turn["message_id"],
            "rider_id": rider_id,
            "text": turn["text"],
            "received_at": turn["received_at"],
        }
        started = time.perf_counter()
        status, response = http("POST", f"{service_url}/messages", body)
        latencies.append(time.perf_counter() - started)
        if status != 200 or not isinstance(response, dict) or "reply" not in response:
            errors.append(f"POST /messages -> {status}: {response}")
            replies.append("")
        else:
            replies.append(str(response["reply"]))

    # PaySwift can take a few seconds to process a payout (its slow path is
    # ~8s), and the desk may answer before it lands. Give the ledger a moment
    # to settle, stopping as soon as the outcome matches an expected end state.
    wanted = candidates(conv["expected"])
    settle_deadline = time.monotonic() + settle_seconds
    while True:
        payout = payswift_total(payswift_url, rider_id) - payout_before
        new_items = [item for item in pending_for(service_url, rider_id) if item["id"] not in pending_before]
        approvals = [item for item in new_items if item.get("type") == "approval"]
        escalations = [item for item in new_items if item.get("type") == "escalation"]
        approval = sum(int(item.get("amount") or 0) for item in approvals) or None
        got = {"payout": payout, "approval": approval, "escalation": bool(escalations)}
        if any(matches(exp, got) for exp in wanted) or time.monotonic() >= settle_deadline:
            break
        time.sleep(0.5)
    all_replies = " ".join(replies).lower()
    missing_mentions = [m for m in conv["expected"].get("reply_mentions", []) if str(m).lower() not in all_replies]
    trace_problems = check_trace(service_url, rider_id, [t["message_id"] for t in turns])

    outcome_ok = any(matches(exp, got) for exp in candidates(conv["expected"]))
    stale = (not outcome_ok) and payout_before > 0 and got["payout"] == 0

    if errors:
        status = "ERROR"
    elif outcome_ok and not missing_mentions and not trace_problems:
        status = "PASS"
    elif stale:
        status = "STALE"
    else:
        status = "FAIL"

    return {
        "scenario": conv.get("scenario", ""),
        "rider_id": rider_id,
        "turns": len(turns),
        "expected": conv["expected"],
        "got": got,
        "missing_mentions": missing_mentions,
        "trace_problems": trace_problems,
        "errors": errors,
        "replies": replies,
        "max_latency_s": round(max(latencies), 2) if latencies else None,
        "status": status,
    }


def fmt_expected(expected):
    if "one_of" in expected:
        return " | ".join(f"pay {a.get('payout', 0)}/appr {a.get('approval')}" for a in expected["one_of"])
    return f"pay {expected.get('payout', 0)}/appr {expected.get('approval')}/esc {expected.get('escalation', '-')}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("service_url", help="e.g. http://localhost:8000")
    parser.add_argument("--payswift", default="http://localhost:8081")
    parser.add_argument("--conversations", default=str(Path(__file__).resolve().parent.parent / "data" / "conversations.json"))
    parser.add_argument("--json", help="also write the full report to this file")
    parser.add_argument("--only", type=int, help="run only conversation number N (1-based)")
    parser.add_argument("--settle", type=float, default=12.0,
                        help="seconds to give PaySwift to show a payout after the last turn (default 12)")
    args = parser.parse_args()

    service_url = args.service_url.rstrip("/")
    payswift_url = args.payswift.rstrip("/")

    status, _ = http("GET", f"{service_url}/health")
    if status != 200:
        print(f"service not healthy at {service_url} ({status})")
        return 2
    status, _ = http("GET", f"{payswift_url}/health")
    if status != 200:
        print(f"PaySwift not healthy at {payswift_url} ({status})")
        return 2

    conversations = json.loads(Path(args.conversations).read_text(encoding="utf-8"))
    if args.only:
        conversations = [conversations[args.only - 1]]

    results = []
    print(f"{'#':>2}  {'rider':5}  {'status':6}  {'expected':38}  {'got':30}  {'lat':>5}  scenario")
    for index, conv in enumerate(conversations, 1):
        result = run_conversation(service_url, payswift_url, conv, settle_seconds=args.settle)
        results.append(result)
        got = result["got"]
        got_text = f"pay {got['payout']}/appr {got['approval']}/esc {'yes' if got['escalation'] else 'no'}"
        print(
            f"{index:>2}  {result['rider_id']:5}  {result['status']:6}  {fmt_expected(result['expected'])[:38]:38}  "
            f"{got_text:30}  {result['max_latency_s'] or 0:>4.1f}s  {result['scenario'][:40]}"
        )
        for note in result["errors"]:
            print(f"      error: {note}")
        if result["missing_mentions"]:
            print(f"      reply should mention: {result['missing_mentions']}")
        for note in result["trace_problems"]:
            print(f"      trace: {note}")
        if result["status"] != "PASS":
            for reply in result["replies"]:
                print(f"      reply: {reply[:160]}")

    counts = {s: sum(1 for r in results if r["status"] == s) for s in ("PASS", "FAIL", "STALE", "ERROR")}
    total = len(results)
    print()
    print(
        f"{counts['PASS']}/{total} passed"
        + (f", {counts['FAIL']} failed" if counts["FAIL"] else "")
        + (f", {counts['STALE']} stale (rider already paid on this system; run on a fresh system)" if counts["STALE"] else "")
        + (f", {counts['ERROR']} errors" if counts["ERROR"] else "")
    )
    slowest = max((r["max_latency_s"] or 0) for r in results) if results else 0
    print(f"slowest reply: {slowest:.1f}s (vendor retries after ~10s)")

    if args.json:
        Path(args.json).write_text(json.dumps({"results": results, "summary": counts}, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"full report: {args.json}")

    return 1 if (counts["FAIL"] or counts["ERROR"]) else 0


if __name__ == "__main__":
    sys.exit(main())
