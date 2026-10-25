"""Sequential HTTP timing, one warm-up then N measured full response reads."""
import argparse
import hashlib
import json
import statistics
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8002")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    paths = [
        "/cards/search?lang=en&kind=cards&format=standard,modern,pioneer,legacy,commander"
        "&legality=legal,restricted&limit=24&offset=0",
        *[f"/translations/cards?lang=pt&kind=cards&translation_status={status}&limit=24&offset=0"
          for status in ("missing", "present", "all")],
    ]
    results = []
    for path in paths:
        times = []
        for _ in range(args.repeats + 1):
            start = time.perf_counter()
            with urllib.request.urlopen(args.base_url + path, timeout=60) as response:
                payload = response.read()
                status = response.status
            times.append(round((time.perf_counter() - start) * 1000, 3))
        data = json.loads(payload)
        results.append({
            "path": path, "status": status, "warmup_ms": times[0], "ms": times[1:],
            "median_ms": statistics.median(times[1:]),
            "response_sha256": hashlib.sha256(
                json.dumps(data, sort_keys=True).encode(),
            ).hexdigest(),
        })
        print(path, times, flush=True)
    with open(args.output, "w") as output:
        json.dump({"base_url": args.base_url, "results": results}, output, indent=2)


if __name__ == "__main__":
    main()
