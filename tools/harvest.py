"""Refill content/louvre/candidates.json with vetted Wikimedia Commons pictures for the nightly Louvre batch.

Runs on GitHub Actions before the nightly routine (see .github/workflows/louvre-candidates.yml), because
Wikimedia throttles the cloud sandbox the routine runs in. Everything slow happens here, in batched requests:
category sampling, licence checks, image URLs, the matching English Wikipedia article and its intro, and a
check that each image URL answers 200. The routine then only picks, looks, writes and publishes.

Usage: python3 tools/harvest.py [--places N] [--photographs N]   (pool sizes to keep unused; defaults 120 / 60)
Standard library only.
"""
import argparse
import html
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOUVRE = ROOT / "content" / "louvre"
UA = "LouvreFeed/0.1 (https://github.com/chunnsshing/commute-content)"
LICENSE = re.compile(r"^(CC0|Public domain|CC BY(-SA)? \d\.\d)$", re.I)
SKIP_ARTICLE = re.compile(r"^(List of|Lists of|Gallery|Timeline of|Index of|Outline of|Glossary)", re.I)


def get(base, params, tries=5):
    url = base + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == tries - 1:
                raise
            time.sleep(int(e.headers.get("Retry-After", "10")) + 2)
        except urllib.error.URLError:
            if attempt == tries - 1:
                raise
            time.sleep(5)
        finally:
            time.sleep(0.5)  # be polite even when it works


def commons(params):
    return get("https://commons.wikimedia.org/w/api.php", params)


def enwiki(params):
    return get("https://en.wikipedia.org/w/api.php", params)


def status(url):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status
        except urllib.error.HTTPError as e:
            if e.code != 429:
                return e.code
            time.sleep(10 * (attempt + 1))
        except urllib.error.URLError:
            return 0
        finally:
            time.sleep(0.3)
    return 429


def plain(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def sample_category(cat, k, exclude):
    try:
        d = commons({"action": "query", "list": "categorymembers", "cmtitle": "Category:" + cat,
                     "cmtype": "file", "cmlimit": "500"})
    except Exception as e:  # a missing or broken category should not stop the run
        print(f"  skip category {cat!r}: {e}", file=sys.stderr)
        return []
    files = [m["title"] for m in d.get("query", {}).get("categorymembers", [])
             if re.search(r"\.(jpe?g|png|tiff?)$", m["title"], re.I) and m["title"] not in exclude]
    random.shuffle(files)
    return files[:k]


def file_info(titles):
    """imageinfo + English Wikipedia usage for up to 50 files per request."""
    out = {}
    for batch in chunks(titles, 50):
        d = commons({"action": "query", "titles": "|".join(batch), "prop": "imageinfo|globalusage",
                     "iiprop": "url|size|mime|extmetadata", "iiurlwidth": "1280",
                     "iiextmetadatafilter": "LicenseShortName|Artist|Credit|DateTimeOriginal|ImageDescription",
                     "gunamespace": "0", "gulimit": "500"})
        for p in d.get("query", {}).get("pages", []):
            if "imageinfo" not in p:
                continue
            ii = p["imageinfo"][0]
            meta = {k: plain(v.get("value", "")) for k, v in ii.get("extmetadata", {}).items()}
            usage = [g["title"] for g in p.get("globalusage", []) if g.get("wiki") == "en.wikipedia.org"]
            out[p["title"]] = {"ii": ii, "meta": meta, "enwiki": usage}
    return out


def articles(titles):
    """Intro text, coordinates and canonical URL for up to 20 English Wikipedia articles per request."""
    out = {}
    for batch in chunks(sorted(set(titles)), 20):
        d = enwiki({"action": "query", "titles": "|".join(batch), "prop": "extracts|coordinates|info",
                    "exintro": "1", "explaintext": "1", "exlimit": "20", "inprop": "url", "redirects": "1"})
        q = d.get("query", {})
        alias = {r["from"]: r["to"] for r in q.get("redirects", []) + q.get("normalized", [])}
        for p in q.get("pages", []):
            if p.get("missing"):
                continue
            c = (p.get("coordinates") or [{}])[0]
            out[p["title"]] = {"title": p["title"], "url": p.get("fullurl"), "extract": p.get("extract", ""),
                               "lat": c.get("lat"), "lon": c.get("lon")}
        for src, dst in alias.items():
            if dst in out:
                out[src] = out[dst]
    return out


def pick_article(file_title, usage, arts):
    """The English article this picture illustrates: not a list or gallery, with coordinates, and sharing at
    least one word with the file name (which filters out portraits and details filed under a country)."""
    words = set(re.findall(r"[a-z]{4,}", file_title.lower()))
    best, best_score = None, 0
    for t in usage:
        a = arts.get(t)
        if not a or SKIP_ARTICLE.match(t) or not a["extract"]:
            continue
        if a["lat"] is None:  # places only: an article without coordinates is rarely about a place
            continue
        score = len(words & set(re.findall(r"[a-z]{4,}", t.lower())))
        if score > best_score:
            best, best_score = a, score
    return best


def build(group, title, info, article):
    ii, meta = info["ii"], info["meta"]
    url = ii.get("thumburl") or ii["url"]
    view = url.replace("/1280px-", "/500px-") if "/1280px-" in url else url
    return {
        "group": group,
        "commonsFile": title,
        "filePage": "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
        "license": meta.get("LicenseShortName", ""),
        "artist": meta.get("Artist", ""),
        "credit": meta.get("Credit", ""),
        "dateTaken": meta.get("DateTimeOriginal", ""),
        "description": meta.get("ImageDescription", "")[:600],
        "image": {"url": url, "width": ii.get("thumbwidth") or ii["width"],
                  "height": ii.get("thumbheight") or ii["height"], "viewUrl": view},
        "article": article,
    }


def harvest_places(categories, want, exclude):
    """Up to `want` new vetted place candidates: allowed licence, decent size, used in an English article
    that has coordinates, image URLs answering 200."""
    if want <= 0:
        return []
    # About one featured picture in five is a place with its own article, so oversample.
    per_cat = max(5, (want * 6) // max(1, len(categories)) + 1)
    titles = []
    for cat in random.sample(categories, len(categories)):
        titles += sample_category(cat, per_cat, exclude | set(titles))
    print(f"places: sampled {len(titles)} files", file=sys.stderr)

    infos = file_info(titles)
    ok = {t: i for t, i in infos.items() if usable(i)}
    print(f"places: {len(ok)} with an allowed licence and size", file=sys.stderr)

    arts = articles([u for i in ok.values() for u in i["enwiki"] if not SKIP_ARTICLE.match(u)])
    out = []
    for t, i in ok.items():
        if len(out) >= want:
            break
        art = pick_article(t, i["enwiki"], arts)
        if art and verified(cand := build("place", t, i, art)):
            out.append(cand)
    return out


def harvest_photographs(categories, want, exclude):
    """Up to `want` new photograph candidates, spread across "Photographs by <name>" categories. The article
    attached is the photographer's, since the story is about who made the picture and why."""
    out = []
    if want <= 0:
        return out
    names = {cat: re.sub(r"^Photographs by ", "", cat) for cat in categories}
    people = articles(list(names.values()))
    per = max(3, want // max(1, len(names)) + 1)
    for cat, person in random.sample(list(names.items()), len(names)):
        if len(out) >= want:
            break
        taken = 0
        for t, i in file_info(sample_category(cat, per * 3, exclude)).items():
            if len(out) >= want or taken >= per:
                break
            if not usable(i):
                continue
            cand = build("photograph", t, i, people.get(person))
            cand["photographer"] = person
            if verified(cand):
                out.append(cand)
                taken += 1
    return out


def usable(info):
    return (LICENSE.match(info["meta"].get("LicenseShortName", ""))
            and info["ii"].get("mime") in ("image/jpeg", "image/png", "image/tiff")
            and min(info["ii"]["width"], info["ii"]["height"]) >= 800)


def verified(cand):
    """The published image URL must answer 200; fall back to it for viewing if the small one does not."""
    if status(cand["image"]["url"]) != 200:
        return False
    if cand["image"]["viewUrl"] != cand["image"]["url"] and status(cand["image"]["viewUrl"]) != 200:
        cand["image"]["viewUrl"] = cand["image"]["url"]
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--places", type=int, default=120)
    ap.add_argument("--photographs", type=int, default=60)
    args = ap.parse_args()

    sources = json.loads((LOUVRE / "sources.json").read_text())
    history = json.loads((LOUVRE / "history.json").read_text())
    path = LOUVRE / "candidates.json"
    pool = json.loads(path.read_text()) if path.exists() else []

    used = {h.get("commonsFile", "").replace("_", " ") for h in history}
    used_articles = {(h.get("title") or "").lower() for h in history}
    pool = [c for c in pool if c["commonsFile"].replace("_", " ") not in used]
    exclude = used | {c["commonsFile"] for c in pool}

    have = {g: sum(1 for c in pool if c["group"] == g) for g in ("place", "photograph")}
    new = harvest_places(sources["places"], args.places - have["place"], exclude)
    new = [c for c in new if c["article"]["title"].lower() not in used_articles]
    photo_new = harvest_photographs(sources["photographs"], args.photographs - have["photograph"], exclude)

    pool += new + photo_new
    path.write_text(json.dumps(pool, ensure_ascii=False, indent=1) + "\n")
    counts = {g: sum(1 for c in pool if c["group"] == g) for g in ("place", "photograph")}
    print(f"added {len(new)} places, {len(photo_new)} photographs; pool now {counts}")


if __name__ == "__main__":
    main()
