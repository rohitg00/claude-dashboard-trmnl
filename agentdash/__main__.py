import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime

from . import core
from .collectors import ALL

MAX_BYTES = 5000


def webhook_for(name):
    url = os.environ.get(f"TRMNL_WEBHOOK_URL_{name.upper()}", "")
    if not url and name == "claude":
        url = os.environ.get("TRMNL_WEBHOOK_URL", "")
    return url


def post(name, url, variables):
    body = json.dumps({"merge_variables": variables}).encode("utf-8")
    if len(body) > MAX_BYTES:
        print(f"[{name}] payload is {len(body)} bytes, over the {MAX_BYTES} byte TRMNL limit", file=sys.stderr)
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "agentdash-trmnl/2.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"[{name}] TRMNL responded {resp.status}")
            return True
    except urllib.error.HTTPError as err:
        print(f"[{name}] TRMNL responded {err.code}: {err.read().decode('utf-8', 'replace')[:200]}", file=sys.stderr)
    except urllib.error.URLError as err:
        print(f"[{name}] post failed: {err.reason}", file=sys.stderr)
    except OSError as err:
        print(f"[{name}] post failed: {type(err).__name__}: {err}", file=sys.stderr)
    return False


def run(names, now):
    results = {}
    for name in names:
        collector = ALL[name]
        if not collector.available():
            print(f"[{name}] no local data, skipped", file=sys.stderr)
            continue
        try:
            if hasattr(collector, "build"):
                results[name] = collector.build(now)
                continue
            data = collector.collect(now)
        except Exception as err:
            print(f"[{name}] collect failed: {type(err).__name__}: {err}", file=sys.stderr)
            continue
        results[name] = core.aggregate(
            name,
            collector.LABEL,
            data["records"],
            now,
            limits=data.get("limits"),
            footer=data.get("footer", ""),
            extra=data.get("extra"),
        )
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(prog="agentdash", description="Post coding-agent usage to TRMNL plugins")
    parser.add_argument("--agents", default=os.environ.get("AGENTDASH_AGENTS", ""), help="comma list, default all")
    parser.add_argument("--dry-run", action="store_true", help="print payloads, post nothing")
    parser.add_argument("--list", action="store_true", help="show which agents have local data")
    args = parser.parse_args(argv)

    if args.list:
        for name, collector in ALL.items():
            state = "data found" if collector.available() else "no data"
            hook = "webhook set" if webhook_for(name) else "no webhook"
            print(f"{name:10} {state:12} {hook}")
        return 0

    names = [n.strip() for n in args.agents.split(",") if n.strip()] or list(ALL)
    unknown = [n for n in names if n not in ALL]
    if unknown:
        parser.error(f"unknown agents: {', '.join(unknown)} (known: {', '.join(ALL)})")

    now = datetime.now()
    results = run(names, now)
    payloads = {name: variables for name, (variables, _) in results.items()}
    if results:
        payloads["fleet"] = core.fleet(list(results.values()), now)

    if args.dry_run:
        print(json.dumps(payloads, indent=2, ensure_ascii=False))
        return 0

    posted = failed = 0
    for name, variables in payloads.items():
        url = webhook_for(name)
        if not url:
            continue
        if post(name, url, variables):
            posted += 1
        else:
            failed += 1
    if not posted and not failed:
        print("No TRMNL_WEBHOOK_URL_<AGENT> variables set. Nothing posted. Use --dry-run to inspect.", file=sys.stderr)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
