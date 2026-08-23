#!/usr/bin/env python3
"""
Daily Current Affairs harvester for prayagiasacademy.com.

Pulls today's items from authentic public RSS feeds (PIB, The Hindu,
InsightsIAS, Drishti IAS), ranks them by source priority, and writes:
  - data/YYYY-MM-DD.json  (dated archive)
  - data/latest.json      (pointer consumed by the website)

Runs on Python stdlib only. Scheduled via GitHub Actions at 18:30 UTC
(00:00 IST) every day.
"""

import json
import re
import sys
import html as htmllib
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
DATA_DIR = Path(__file__).parent / "data"
MAX_ITEMS = 10
DETAIL_LIMIT = 320

FEEDS = [
    {"name": "PIB", "lang": "en", "priority": 1,
     "url": "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3"},
    {"name": "PIB Hindi", "lang": "hi", "priority": 2,
     "url": "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=2&Regid=3"},
    {"name": "Drishti IAS", "lang": "en", "priority": 3,
     "url": "https://www.drishtiias.com/rss"},
    {"name": "InsightsIAS", "lang": "en", "priority": 4,
     "url": "https://insightsonindia.com/feed/"},
    {"name": "The Hindu", "lang": "en", "priority": 5,
     "url": "https://www.thehindu.com/news/national/feeder/default.rss"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PrayagIASFeedBot/1.0; +https://prayagiasacademy.com)"
}


def fetch_url(url, timeout=30):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def strip_html(raw):
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = htmllib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def truncate(text, limit=DETAIL_LIMIT):
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:-") + "..."


def parse_rss(xml_bytes):
    items = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return items
    for node in root.iter():
        if node.tag.endswith("item") or node.tag.endswith("entry"):
            entry = {"title": "", "link": "", "description": "", "published": None}
            for child in node:
                tag = child.tag.split("}")[-1].lower()
                if tag == "title":
                    entry["title"] = strip_html(child.text)
                elif tag == "link":
                    entry["link"] = (child.text or "").strip() or child.get("href", "")
                elif tag in ("description", "summary", "content"):
                    text = strip_html(child.text or "")
                    if len(text) > len(entry["description"]):
                        entry["description"] = text
                elif tag in ("pubdate", "published", "updated", "date"):
                    try:
                        entry["published"] = parsedate_to_datetime((child.text or "").strip())
                    except (TypeError, ValueError):
                        pass
            if entry["title"]:
                items.append(entry)
    return items


def is_today(pub, today_ist):
    if pub is None:
        return False
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    local = pub.astimezone(IST)
    return local.date() == today_ist.date() or local.date() == (today_ist - timedelta(days=1)).date()


def collect(today_ist):
    pool_en, pool_hi = [], []
    for feed in FEEDS:
        try:
            raw = fetch_url(feed["url"])
            entries = [e for e in parse_rss(raw) if e["title"]]
            fresh = [e for e in entries if is_today(e.get("published"), today_ist)]
            chosen = fresh if fresh else entries[:15]
            bucket = {"en": pool_en, "hi": pool_hi}[feed["lang"]]
            for e in chosen:
                bucket.append({
                    "source": feed["name"],
                    "priority": feed["priority"],
                    "title": e["title"],
                    "detail": truncate(e["description"] or e["title"]),
                    "url": e["link"],
                })
            print(f"[ok]   {feed['name']}: {len(chosen)} usable item(s)")
        except Exception as exc:
            print(f"[skip] {feed['name']}: {exc}", file=sys.stderr)

    pool_en.sort(key=lambda x: x["priority"])
    seen, selected = set(), []
    for item in pool_en:
        key = re.sub(r"\W+", "", item["title"].lower())[:60]
        if key in seen:
            continue
        seen.add(key)
        selected.append(item)
        if len(selected) >= MAX_ITEMS:
            break
    return selected, pool_hi


def build_payload(items_en, items_hi, today_ist):
    out = []
    for idx, item in enumerate(items_en):
        hi_title, hi_detail = "", ""
        if idx < len(items_hi):
            hi_title = items_hi[idx]["title"]
            hi_detail = items_hi[idx]["detail"]
        out.append({
            "title_en": item["title"],
            "title_hi": hi_title or item["title"],
            "detail_en": item["detail"],
            "detail_hi": hi_detail,
            "source": item["source"],
            "url": item["url"],
        })
    return {
        "updated_at": datetime.now(IST).isoformat(timespec="seconds"),
        "date": today_ist.strftime("%Y-%m-%d"),
        "sample": False,
        "items": out,
    }


def write_outputs(payload):
    DATA_DIR.mkdir(exist_ok=True)
    dated = DATA_DIR / f"{payload['date']}.json"
    latest = DATA_DIR / "latest.json"
    dated.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] wrote {dated.name} and latest.json ({len(payload['items'])} items)")


def main():
    today_ist = datetime.now(IST)
    print(f"Fetching current affairs for {today_ist.strftime('%Y-%m-%d')} IST")
    items_en, items_hi = collect(today_ist)

    if not items_en:
        latest = DATA_DIR / "latest.json"
        if latest.exists():
            print("[warn] no items fetched; keeping existing latest.json", file=sys.stderr)
            return 0
        print("[error] no sources reachable and no fallback file present", file=sys.stderr)
        return 1

    write_outputs(build_payload(items_en, items_hi, today_ist))
    return 0


if __name__ == "__main__":
    sys.exit(main())
