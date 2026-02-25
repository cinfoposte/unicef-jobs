#!/usr/bin/env python3
"""
UNICEF Job Vacancies – Filtered RSS Generator

Downloads the UNICEF careers RSS feed, applies grade/keyword filters,
and writes a filtered RSS 2.0 file (unicef_jobs.xml) with the newest 50 items.
"""

import hashlib
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FEED_URLS = [
    "https://careers.pageuppeople.com/671/cw/en/rss",
    "http://careers.pageuppeople.com/671/cw/en/rss",
    "https://careers.pageuppeople.com/671/cw/en-us/rss",
    "http://careers.pageuppeople.com/671/cw/en-us/rss",
]

OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unicef_jobs.xml")
MAX_ITEMS = 50

CHANNEL_TITLE = "UNICEF Job Vacancies (Filtered)"
CHANNEL_LINK = "https://jobs.unicef.org/"
SELF_LINK = "https://cinfoposte.github.io/unicef-jobs/unicef_jobs.xml"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}
REQUEST_TIMEOUT = 30

# --- Filter keywords (upper-case) ---

CONSULTANCY_KEYWORDS = [
    "CONSULTANT",
    "CONSULTANCY",
    "INDIVIDUAL CONTRACTOR",
    "CONTRACTOR AGREEMENT",
    "ICA ",       # space-padded to avoid false positives
    " ICA",
    " IICA ",
    " IICA",
    "IICA ",
]

INTERNSHIP_KEYWORDS = [
    "INTERN ",
    " INTERN",
    "INTERNSHIP",
    "FELLOWSHIP",
    "FELLOW ",
    " FELLOW",
    "PASANTÍA",
    "PASANTIA",
    "PRÁCTICA",
    "PRACTICA",
]

INCLUDED_GRADES = {"P-1", "P-2", "P-3", "P-4", "P-5", "D-1", "D-2"}

EXCLUDED_GRADE_PREFIXES = ("G-", "GS-", "NO-", "SB-", "LSC-")

# NO-A … NO-D  and  NOA … NOD  (national officer sub-grades)
EXCLUDED_NO_VARIANTS = {
    "NOA", "NOB", "NOC", "NOD",
    "NO-A", "NO-B", "NO-C", "NO-D",
}

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Upper-case, normalise dashes, compress whitespace."""
    text = text.upper()
    # Normalise all unicode dashes to ASCII hyphen
    text = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uFE58\uFE63\uFF0D]", "-", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_grade(text: str) -> str:
    """
    Insert a hyphen between grade-letter prefix and digit when missing.
    P4 -> P-4, D1 -> D-1, GS6 -> GS-6, NO1 -> NO-1, NOA -> NOA (kept).
    """
    # Handle two-letter prefixes first (GS, NO, SB, LS, etc.)
    text = re.sub(r"\b(GS|NO|SB|LS|LSC)(\d)\b", r"\1-\2", text)
    # Single-letter grades: P, D, G
    text = re.sub(r"\b([PDG])(\d)\b", r"\1-\2", text)
    return text


def strip_html(html: str) -> str:
    """Return plain text from HTML."""
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)


def stable_guid(link: str) -> str:
    """MD5 of link -> 16-digit numeric string."""
    digest = hashlib.md5(link.encode("utf-8")).hexdigest()
    numeric = str(int(digest, 16))
    return numeric[:16].ljust(16, "0")


def extract_grades(text: str) -> list[str]:
    """Return a list of grade tokens found in normalized text."""
    grades = re.findall(r"\b(?:P|D|G|GS|NO|SB|LSC|LS)-\d+\b", text)
    # Also catch NOA-NOD style
    grades += re.findall(r"\b(?:NO-[A-D]|NO[A-D])\b", text)
    return grades


def has_keyword(text: str, keywords: list[str]) -> bool:
    """Check if any keyword appears in the text."""
    for kw in keywords:
        if kw in text:
            return True
    return False


def classify_item(searchable_text: str) -> tuple[str, str]:
    """
    Classify an item.
    Returns (action, reason) where action is 'include' or 'exclude'.
    """
    norm = normalize(searchable_text)
    norm_graded = normalize_grade(norm)

    # 1) Consultancy -> EXCLUDE
    if has_keyword(norm, CONSULTANCY_KEYWORDS):
        return ("exclude", "consultancy")

    # 2) Check for excluded grades
    grades = extract_grades(norm_graded)
    for g in grades:
        if g in EXCLUDED_NO_VARIANTS:
            return ("exclude", "excluded-grade")
        if any(g.startswith(prefix) for prefix in EXCLUDED_GRADE_PREFIXES):
            return ("exclude", "excluded-grade")

    # 3) Check for included grades
    for g in grades:
        if g in INCLUDED_GRADES:
            return ("include", "included-grade")

    # 4) Internship / fellowship -> INCLUDE
    if has_keyword(norm, INTERNSHIP_KEYWORDS):
        return ("include", "internship")

    # 5) Default -> EXCLUDE
    return ("exclude", "no-grade-match")


def parse_pub_date(date_str: str | None) -> datetime:
    """Try to parse an RFC-2822 or common date string."""
    if date_str:
        try:
            return parsedate_to_datetime(date_str)
        except Exception:
            pass
        # Try ISO-8601 style
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# RSS parsing with ElementTree (namespace-safe)
# ---------------------------------------------------------------------------


def get_item_text(item_el: ET.Element, tag: str) -> str:
    """Get text from a child element, trying with and without namespace."""
    el = item_el.find(tag)
    if el is not None and el.text:
        return el.text
    # Try common RSS namespaces
    for ns in ["{http://purl.org/rss/1.0/}", "{http://purl.org/dc/elements/1.1/}"]:
        el = item_el.find(ns + tag)
        if el is not None and el.text:
            return el.text
    return ""


def parse_rss_items(xml_bytes: bytes) -> list[dict]:
    """Parse RSS XML and return a list of raw item dicts."""
    root = ET.fromstring(xml_bytes)
    items = []

    # Find all <item> elements anywhere in the tree
    for item_el in root.iter("item"):
        title = get_item_text(item_el, "title")
        link = get_item_text(item_el, "link")
        description = get_item_text(item_el, "description")
        pub_date = get_item_text(item_el, "pubDate")
        if not pub_date:
            pub_date = get_item_text(item_el, "date")  # dc:date

        # Collect categories
        categories = []
        for cat_el in item_el.findall("category"):
            if cat_el.text:
                categories.append(cat_el.text)

        items.append({
            "title": title,
            "link": link,
            "description": description,
            "pub_date_str": pub_date,
            "categories": categories,
        })

    return items


def build_searchable_text(item: dict) -> str:
    """Build a single searchable string from all useful item fields."""
    parts = [
        item["title"],
        strip_html(item["description"]),
    ]
    parts.extend(item["categories"])
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Feed download
# ---------------------------------------------------------------------------


def download_feed() -> tuple[bytes, str]:
    """Try each FEED_URL until one returns valid RSS. Returns (content_bytes, url)."""
    for url in FEED_URLS:
        try:
            print(f"Trying {url} …")
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                print(f"  HTTP {resp.status_code}, skipping")
                continue
            ct = resp.headers.get("Content-Type", "")
            text_preview = resp.text[:500].lower()
            if "xml" not in ct and "rss" not in ct and "<rss" not in text_preview:
                print(f"  Content-Type={ct!r}, doesn't look like XML, skipping")
                continue
            # Quick sanity check
            if "<item" not in resp.text[:5000].lower() and "<item" not in resp.text.lower():
                print("  No <item> elements found, trying next URL")
                continue
            print(f"  OK – received {len(resp.content)} bytes")
            return resp.content, url
        except requests.RequestException as exc:
            print(f"  Request error: {exc}")
    raise RuntimeError("Could not download a valid RSS feed from any URL")


# ---------------------------------------------------------------------------
# RSS output
# ---------------------------------------------------------------------------


def build_output_rss(items: list[dict]) -> ET.ElementTree:
    """Build an RSS 2.0 ElementTree from a list of item dicts."""
    rss = ET.Element("rss", version="2.0")
    rss.set("xmlns:atom", "http://www.w3.org/2005/Atom")

    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = CHANNEL_TITLE
    ET.SubElement(channel, "link").text = CHANNEL_LINK
    ET.SubElement(channel, "description").text = (
        "Filtered UNICEF job vacancies: Professional (P-1 to P-5), "
        "Director (D-1/D-2), internships and fellowships."
    )
    ET.SubElement(channel, "language").text = "en"
    ET.SubElement(channel, "pubDate").text = format_datetime(datetime.now(timezone.utc))

    atom_link = ET.SubElement(channel, "atom:link")
    atom_link.set("href", SELF_LINK)
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    for item_data in items:
        item_el = ET.SubElement(channel, "item")
        ET.SubElement(item_el, "title").text = item_data["title"]
        ET.SubElement(item_el, "link").text = item_data["link"]
        ET.SubElement(item_el, "description").text = item_data["description"]
        ET.SubElement(item_el, "guid", isPermaLink="false").text = item_data["guid"]
        ET.SubElement(item_el, "pubDate").text = item_data["pubDate"]
        if item_data.get("source_url"):
            src = ET.SubElement(item_el, "source", url=item_data["source_url"])
            src.text = item_data.get("source_name", "UNICEF Careers")

    return ET.ElementTree(rss)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    xml_bytes, source_url = download_feed()
    raw_items = parse_rss_items(xml_bytes)
    total = len(raw_items)
    print(f"Parsed {total} items from feed")

    now_str = format_datetime(datetime.now(timezone.utc))

    included_items = []
    exclusion_reasons = Counter()

    for item in raw_items:
        searchable = build_searchable_text(item)
        action, reason = classify_item(searchable)

        if action == "exclude":
            exclusion_reasons[reason] += 1
            continue

        pub_dt = parse_pub_date(item["pub_date_str"])
        included_items.append({
            "title": item["title"],
            "link": item["link"],
            "description": strip_html(item["description"]),
            "guid": stable_guid(item["link"]),
            "pubDate": format_datetime(pub_dt),
            "pub_dt": pub_dt,
            "source_url": source_url,
            "source_name": "UNICEF Careers RSS",
        })

    # Sort newest first, keep top N
    included_items.sort(key=lambda x: x["pub_dt"], reverse=True)
    included_items = included_items[:MAX_ITEMS]

    # Remove helper field before output
    for it in included_items:
        del it["pub_dt"]

    tree = build_output_rss(included_items)
    ET.indent(tree, space="  ")
    tree.write(OUTPUT_FILE, encoding="unicode", xml_declaration=True)

    # --- Log summary ---
    included_count = len(included_items)
    excluded_count = sum(exclusion_reasons.values())
    print()
    print("=" * 60)
    print("UNICEF Jobs – Filter Summary")
    print("=" * 60)
    print(f"  Source items:   {total}")
    print(f"  Included:       {included_count}")
    print(f"  Excluded:       {excluded_count}")
    if exclusion_reasons:
        print("  Exclusion reasons:")
        for reason, count in exclusion_reasons.most_common():
            print(f"    {reason:25s} {count}")
    print(f"  Output file:    {OUTPUT_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()
