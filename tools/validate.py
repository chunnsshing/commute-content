"""Validate content/louvre/feed.json against what the Louvre app decodes, plus the authoring rules.

Usage:
  python3 tools/validate.py content/louvre/feed.json [--history content/louvre/history.json] [--net-batch YYYY-MM-DD]

--net-batch checks that every image in that batch answers HTTP 200.
Exits 1 on any error.
"""
import argparse
import json
import re
import subprocess
import sys

ap = argparse.ArgumentParser()
ap.add_argument("feed")
ap.add_argument("--history")
ap.add_argument("--net-batch")
args = ap.parse_args()

feed = json.load(open(args.feed))
errors = []
if feed.get("schemaVersion") != 3:
    errors.append("schemaVersion must be 3")
items = feed.get("feed", [])

ids = [i.get("id") for i in items]
dupes = sorted({x for x in ids if ids.count(x) > 1})
if dupes:
    errors.append(f"duplicate ids in feed: {dupes}")

batches = sorted({i.get("batch") for i in items})
if len(batches) > 10:
    errors.append(f"feed holds {len(batches)} batches; keep only the newest 10")

LICENSE = re.compile(r"^(CC0|Public domain|CC BY(-SA)? \d\.\d)$", re.I)
LENSES = {"Light", "Composition", "Moment", "Process", "Context", "Design"}
HOSTS = ("https://upload.wikimedia.org/", "https://thumb.wikimedia.org/")


def is_str(v):
    return isinstance(v, str) and v.strip() != ""


for n, it in enumerate(items):
    tag = it.get("id") or f"#{n}"
    for k in ("id", "kind", "title", "maker", "year", "collection", "batch"):
        if not is_str(it.get(k)):
            errors.append(f"{tag}: missing {k}")
    if it.get("kind") not in ("place", "photograph"):
        errors.append(f"{tag}: kind must be place or photograph")
    if not isinstance(it.get("order"), int):
        errors.append(f"{tag}: order must be an integer")
    if not isinstance(it.get("hasStory"), bool):
        errors.append(f"{tag}: hasStory must be true/false")
    if not (isinstance(it.get("sources"), list) and it["sources"] and all(is_str(s) for s in it["sources"])):
        errors.append(f"{tag}: sources must be a non-empty list of URLs")

    cap = it.get("caption") or {}
    for k in ("kind", "title", "line2"):
        if not is_str(cap.get(k)):
            errors.append(f"{tag}: caption.{k} missing")
    if is_str(cap.get("kind")) and cap["kind"] != cap["kind"].upper():
        errors.append(f"{tag}: caption.kind must be uppercase")

    img = it.get("image")
    if not isinstance(img, dict):
        errors.append(f"{tag}: image missing")
    else:
        for k in ("url", "license", "credit"):
            if not is_str(img.get(k)):
                errors.append(f"{tag}: image.{k} missing")
        for k in ("width", "height"):
            if not isinstance(img.get(k), (int, float)) or img[k] <= 0:
                errors.append(f"{tag}: image.{k} must be a positive number")
        if not LICENSE.match(img.get("license") or ""):
            errors.append(f"{tag}: licence not allowed: {img.get('license')!r}")
        if not (img.get("url") or "").startswith(HOSTS):
            errors.append(f"{tag}: image must be served from Wikimedia")

    if it.get("kind") == "place":
        pl = it.get("place") or {}
        for k in ("country", "region"):
            if not is_str(pl.get(k)):
                errors.append(f"{tag}: place.{k} missing")
        for k in ("lat", "lon"):
            if not isinstance(pl.get(k), (int, float)):
                errors.append(f"{tag}: place.{k} must be a number")
        facts = pl.get("facts")
        if not (isinstance(facts, list) and len(facts) == 4
                and all(isinstance(f, list) and len(f) == 2 and all(is_str(x) for x in f) for f in facts)):
            errors.append(f"{tag}: place.facts must be exactly 4 [label, value] pairs")
        if is_str(cap.get("kind")) and not cap["kind"].startswith("PLACE · "):
            errors.append(f"{tag}: caption.kind must start with 'PLACE · '")
    elif "place" in it:
        errors.append(f"{tag}: only places carry a place block")

    if it.get("hasStory"):
        sw = len((it.get("story") or "").split())
        lw = len((it.get("lookAt") or "").split())
        if not 90 <= sw <= 130:
            errors.append(f"{tag}: story is {sw} words (90-130)")
        if not 35 <= lw <= 60:
            errors.append(f"{tag}: lookAt is {lw} words (35-60)")
        if it.get("lens") not in LENSES:
            errors.append(f"{tag}: lens {it.get('lens')!r} not in {sorted(LENSES)}")

    if args.net_batch and it.get("batch") == args.net_batch and isinstance(img, dict) and is_str(img.get("url")):
        code = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "30",
             "-A", "Louvre/0.1 (personal app)", img["url"]],
            capture_output=True, text=True).stdout
        if code != "200":
            errors.append(f"{tag}: image answered HTTP {code}")

if args.history:
    history = json.load(open(args.history))
    hids = [h.get("id") for h in history]
    hd = sorted({x for x in hids if hids.count(x) > 1})
    if hd:
        errors.append(f"history repeats ids: {hd}")
    files = [h.get("commonsFile") for h in history if h.get("commonsFile")]
    fd = sorted({x for x in files if files.count(x) > 1})
    if fd:
        errors.append(f"history repeats Commons files: {fd}")
    missing = sorted(set(ids) - set(hids))
    if missing:
        errors.append(f"feed ids missing from history: {missing}")

kinds = {}
for it in items:
    kinds[it.get("kind")] = kinds.get(it.get("kind"), 0) + 1
print(f"{len(items)} items in {len(batches)} batch(es): {kinds}")
for e in errors:
    print("ERROR:", e)
sys.exit(1 if errors else 0)
