"""Reproducible read-only source benchmark; mutations only in a disposable snapshot.

Run from repository root with PYTHONPATH=. See docs/performance/translation-cards.md.
Output includes full executionStats, timings and exact functional comparisons.
"""
import argparse
import json
import statistics
import time
from datetime import UTC, datetime
from uuid import uuid4

from pymongo import MongoClient

from app.translation_repository import translation_cards_pipeline
from app.translation_schemas import TranslationCardsParams


def legacy_pipeline(params):
    """Original pipeline, kept here solely as a benchmark reference."""
    optimized = translation_cards_pipeline(params)
    match = optimized[0]
    lookup = next(stage for stage in optimized if "$lookup" in stage)
    pipeline = [match, lookup]
    if params.translation_status != "all":
        pipeline.append({"$match": {"_translation_records.0": {
            "$exists": params.translation_status == "present",
        }}})
    return pipeline + [
        {"$sort": {"name": 1, "oracle_id": 1}},
        {"$skip": params.offset}, {"$limit": params.limit + 1},
    ]


def run(db, pipeline):
    return list(db.oracle_cards.aggregate(pipeline, collation={"locale": "simple"}))


def measure(db, pipeline, repeats):
    run(db, pipeline)  # One excluded warm-up per variant/case; no cache flushing.
    timings = []
    for _ in range(repeats):
        start = time.perf_counter()
        documents = run(db, pipeline)  # Consume cursor, including full original documents.
        timings.append(round((time.perf_counter() - start) * 1000, 3))
    explain = db.command("explain", {
        "aggregate": "oracle_cards", "pipeline": pipeline, "cursor": {},
        "collation": {"locale": "simple"},
    }, verbosity="executionStats")
    return documents, {"ms": timings, "median_ms": statistics.median(timings),
                       "explain": explain}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default="mongodb://localhost:27017")
    parser.add_argument("--source", default="card_manager")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", required=True)
    parser.add_argument("--only", help="Run only a named scenario; skip ablations")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    client = MongoClient(args.uri)
    source = client[args.source]
    temporary = client[f"test_translation_benchmark_{uuid4().hex}"]
    result = {"started_utc": datetime.now(UTC).isoformat(), "repeats": args.repeats,
              "mongodb_version": client.server_info()["version"],
              "source_indexes": {name: source[name].index_information()
                                 for name in ("oracle_cards", "translations")}, "cases": []}
    try:
        for name in ("oracle_cards", "translations"):
            list(source[name].aggregate([{"$match": {}}, {"$out": {
                "db": temporary.name, "coll": name,
            }}]))
        temporary.oracle_cards.create_index("oracle_id", unique=True)
        temporary.translations.create_index(
            [("oracle_id", 1), ("lang", 1)], unique=True, name="uq_translation_oracle_lang",
        )
        result["counts"] = {name: temporary[name].count_documents({})
                            for name in ("oracle_cards", "translations")}
        # Ablation: separate benefits of changing order and adding the index.
        params = TranslationCardsParams(lang="pt", kind="cards", translation_status="missing",
                                        limit=24)
        result["ablations"] = {}
        for label, pipeline in ([] if args.only else [("legacy_no_index", legacy_pipeline(params)),
                                ("sort_without_index", translation_cards_pipeline(params))]):
            _, result["ablations"][label] = measure(temporary, pipeline, args.repeats)
        temporary.oracle_cards.create_index(
            [("name", 1), ("oracle_id", 1)], name="ix_oracle_cards_name_oracle_id",
            collation={"locale": "simple"},
        )
        if not args.only:
            _, result["ablations"]["index_without_sort_move"] = measure(
                temporary, legacy_pipeline(params), args.repeats,
            )
        # Sparse target language, isolated to this disposable snapshot.
        sparse = list(temporary.oracle_cards.find({}, {"oracle_id": 1}).limit(7))
        temporary.translations.insert_many([
            {"oracle_id": card["oracle_id"], "lang": "zz-ZZ", "name": ""} for card in sparse
        ])
        scenarios = [
            ("initial", {}), ("offset_10000", {"offset": 10000}),
            ("after_end", {"offset": 100000}),
            ("name", {"name": "dragon"}),
            ("no_name_match", {"name": "__no_matching_card_95731__"}),
            ("accessories", {"kind": "accessories"}),
            ("accessory_name", {"kind": "accessories", "name": "token"}),
            ("no_language_records", {"lang": "zz"}),
            ("few_language_records", {"lang": "zz-ZZ"}),
        ]
        for label, changes in scenarios:
            if args.only and label != args.only:
                continue
            for status in ("missing", "present", "all"):
                params = TranslationCardsParams(**{
                    "lang": "pt", "kind": "cards", "limit": 24,
                    "translation_status": status, **changes,
                })
                old, before = measure(temporary, legacy_pipeline(params), args.repeats)
                new, after = measure(temporary, translation_cards_pipeline(params), args.repeats)
                # Compare complete BSON documents and IDs, including the extra pagination item.
                assert old == new, (label, status)
                case = {"scenario": label, "params": params.model_dump(),
                        "identical_documents": True, "returned_with_sentinel": len(new),
                        "before": before, "after": after}
                result["cases"].append(case)
                print(label, status, before["median_ms"], after["median_ms"], flush=True)
                with open(args.output, "w") as output:
                    json.dump(result, output, indent=2, default=str)
    finally:
        client.drop_database(temporary.name)
        client.close()


if __name__ == "__main__":
    main()
