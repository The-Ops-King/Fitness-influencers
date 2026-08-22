"""Small shared HTML helpers."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup


def soup_of(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # lxml missing or malformed markup
        return BeautifulSoup(html, "html.parser")


def visible_text(html: str, limit: int = 40_000) -> str:
    soup = soup_of(html)
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)[:limit]


def all_links(html: str, base_url: str | None = None) -> list[str]:
    soup = soup_of(html)
    out: dict[str, None] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("javascript:", "#")):
            continue
        if href.startswith("mailto:") or href.startswith("tel:"):
            out.setdefault(href, None)
            continue
        out.setdefault(urljoin(base_url, href) if base_url else href, None)
    # Booking widgets are often embedded rather than linked.
    for frame in soup.find_all("iframe", src=True):
        src = frame["src"].strip()
        if src:
            out.setdefault(urljoin(base_url, src) if base_url else src, None)
    return list(out)


def meta_content(html: str, *names: str) -> str | None:
    soup = soup_of(html)
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def page_title(html: str) -> str | None:
    soup = soup_of(html)
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return None
