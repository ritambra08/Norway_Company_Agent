"""A small, tolerant HTML reader built on the standard library.

It produces just enough structure for deterministic extraction: title, meta tags, <link> tags,
anchors with their visible text and enclosing block, JSON-LD objects, <time> elements and the
visible page text. No network, no JavaScript.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
        "source", "track", "wbr"}
SKIP_TEXT = {"script", "style", "noscript", "template", "svg", "head"}
BLOCK = {"article", "li", "tr", "section", "div", "p", "td", "dd", "dt", "h1", "h2", "h3", "h4",
         "h5", "h6", "header", "footer", "main", "aside", "figure", "ul", "ol", "table", "body"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict, parent: "Node | None"):
        self.tag = tag
        self.attrs = attrs
        self.children: list = []
        self.parent = parent

    def text(self) -> str:
        out: list[str] = []

        def walk(n: Node):
            for c in n.children:
                if isinstance(c, str):
                    out.append(c)
                elif c.tag not in SKIP_TEXT:
                    walk(c)
        walk(self)
        return _ws(" ".join(out))

    def iter(self, tag: str | None = None):
        for c in self.children:
            if isinstance(c, Node):
                if tag is None or c.tag == tag:
                    yield c
                yield from c.iter(tag)

    def block(self) -> "Node":
        n = self.parent
        while n is not None and n.tag not in BLOCK:
            n = n.parent
        return n or self


def _ws(s: str) -> str:
    s = re.sub("[\u00ad\u200b\u200c\u200d\ufeff]", "", s)  # soft hyphens / zero-width marks
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class Anchor:
    href: str
    text: str
    node: Node
    title: str = ""


@dataclass
class Doc:
    root: Node
    title: str = ""
    lang: str = ""
    metas: dict = field(default_factory=dict)  # lower(name|property) -> content
    links: list = field(default_factory=list)  # dicts of <link> attributes
    anchors: list = field(default_factory=list)
    jsonld: list = field(default_factory=list)  # flattened JSON-LD objects
    times: list = field(default_factory=list)  # (datetime attr, text, node)
    text: str = ""
    base: str = ""


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.cur = self.root
        self.doc = Doc(self.root)
        self._script_type: str | None = None
        self._script_buf: list[str] = []
        self._in_title = False
        self._title_done = False
        self._title: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html" and a.get("lang"):
            self.doc.lang = a["lang"]
        if tag == "meta":
            key = (a.get("name") or a.get("property") or a.get("itemprop") or "").lower()
            if key and "content" in a and key not in self.doc.metas:
                self.doc.metas[key] = a["content"]
        elif tag == "link":
            self.doc.links.append(a)
        elif tag == "base" and a.get("href"):
            self.doc.base = a["href"]
        node = Node(tag, a, self.cur)
        self.cur.children.append(node)
        if tag == "title" and not self._title and not self._title_done:
            self._in_title = True
        if tag == "script":
            self._script_type = (a.get("type") or "").lower()
            self._script_buf = []
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.cur.tag == tag:
            self.cur = self.cur.parent or self.root

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self._in_title = False
            self._title_done = True
        if tag == "script" and self._script_type is not None:
            if "ld+json" in self._script_type:
                _collect_jsonld("".join(self._script_buf), self.doc.jsonld)
            self._script_type = None
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        if self._script_type is not None:
            self._script_buf.append(data)
            return
        if self._in_title:
            self._title.append(data)
        self.cur.children.append(data)


def _collect_jsonld(raw: str, out: list) -> None:
    raw = raw.strip()
    if not raw:
        return
    try:
        data = json.loads(raw)
    except ValueError:
        try:
            data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw))  # trailing commas are common
        except ValueError:
            return

    def walk(x):
        if isinstance(x, list):
            for i in x:
                walk(i)
        elif isinstance(x, dict):
            if "@graph" in x:
                walk(x["@graph"])
            if "@type" in x:
                out.append(x)
            for k, v in x.items():
                if k != "@graph" and isinstance(v, (dict, list)) and k not in ("address", "hiringOrganization",
                                                                                 "jobLocation", "publisher", "author"):
                    walk(v)
    walk(data)


def parse(html: str) -> Doc:
    b = _Builder()
    try:
        b.feed(html)
        b.close()
    except Exception:  # noqa: BLE001 - malformed markup must never crash a run
        pass
    doc = b.doc
    doc.title = _ws(unescape("".join(b._title)))
    for n in doc.root.iter("a"):
        href = (n.attrs.get("href") or "").strip()
        if href:
            doc.anchors.append(Anchor(href, n.text(), n, n.attrs.get("title", "")))
    for n in doc.root.iter("time"):
        doc.times.append((n.attrs.get("datetime", ""), n.text(), n))
    body = next(doc.root.iter("body"), None) or doc.root
    doc.text = body.text()
    return doc


def types_of(obj: dict) -> set[str]:
    t = obj.get("@type")
    if isinstance(t, list):
        return {str(x).lower() for x in t}
    return {str(t).lower()} if t else set()
