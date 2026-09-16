#!/usr/bin/env python
"""Report every item that references the one you are about to delete, including the Experience Builder drafts the dependency graph never opens.

A delete in an ArcGIS organization is not reversible and the portal asks for no
more than a confirmation. This tool reads the CONFIGURATION of every item in
the organization, finds the ones that name the target item id, and sorts them
into a hard break (a data source or a layer reference), a soft break (a filter,
a bookmark, a popup reference) and a mention in free text. It is read-only:
there is no flag that deletes, moves or edits anything.

arcgis.apps.itemgraph is the obvious tool and it does more than people expect.
create_dependency_graph accepts include_reverse, ItemNode.contained_by() and
ItemNode.required_by() both exist, and that traversal is far less code than
this. It is built on item.related_items(direction="reverse"), which returns the
relationships the PORTAL HAS REGISTERED. It never opens
/content/items/{id}/data or /content/items/{id}/resources. An item id that
lives only inside an Experience Builder widget config, a dashboard dataset
binding, a StoryMap draft resource or a notebook string literal is invisible to
it in both directions, because no relationship was ever registered for it. That
gap is the whole tool.

    python whobreaks.py --self-test
    python whobreaks.py --url https://county.maps.arcgis.com --target ITEMID --username gis_admin
    python whobreaks.py --url https://county.maps.arcgis.com --target ITEMID --token TOKEN --out breaks.csv --format csv --apply

Exit codes: 0 nothing in the organization references the target, 1 something
does, 2 the sweep could not be completed so nothing can be concluded, 64 usage
error.
"""

from __future__ import print_function

import argparse
import csv
import datetime
import getpass
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# =============================================================================
# CONFIGURATION. Deliberately not constants at the call site.
# =============================================================================

# /sharing/rest/search clamps num to 100 whatever you ask for, and says nothing
# about it. A loop that advances by its own num walks past most of an org.
SEARCH_MAX_NUM = 100

# search counts accurately only to 10,000. At or beyond that the number IS
# 10,000 and the rows past it cannot be paged to. A slice that reaches this has
# to be split, and a slice that cannot be split any further is not proven.
SEARCH_CEILING = 10000

# Narrowest created-date slice the sweep will produce. Below one day the search
# API offers nothing this tool can split on, so the slice is reported as
# unproven instead.
DAY_MS = 86400000

# No ArcGIS item predates the epoch, so the first slice starts here rather than
# at a date somebody has to maintain.
EPOCH_START_MS = 0

# Ceiling on how many date slices one sweep may visit, so that a portal that
# reports the cap for every query stops rather than splitting for ever.
MAX_PARTITIONS = 4096

# Largest body this tool will read from /data. An Experience Builder config is
# tens of kilobytes; a file geodatabase item's /data is the whole upload. A
# truncated body no longer parses as JSON and is searched as text instead, so
# the cap costs structure, never the match.
MAX_BODY_BYTES = 4 * 1024 * 1024

# Items whose reported size is larger than this are not fetched at all, and are
# counted as UNREAD rather than as clean. An unread item is not evidence of
# anything, which is why it never disappears quietly.
MAX_ITEM_SIZE = 32 * 1024 * 1024

# Most JSON resources this tool reads per item. A StoryMap with hundreds of
# draft resources would otherwise turn one sweep into an afternoon.
MAX_RESOURCES = 50

# Seconds before a portal call is abandoned.
HTTP_TIMEOUT = 60

# What replaces a secret anywhere it could otherwise be printed or written.
REDACTED = "[redacted]"

# Environment variable the password is read from. It is never a command line
# flag: argv is readable by every process on the box, it lands in shell history
# and in scheduler logs, and urllib quotes a failing url back into its error.
SECRET_ENV = "WHOBREAKS_PASSWORD"

# =============================================================================
# End of CONFIGURATION.
# =============================================================================

HARD = "hard"
SOFT = "soft"
MENTION = "mention"

# How bad each class is, and what the report calls it.
RANK = {MENTION: 1, SOFT: 2, HARD: 3}
LABEL = {HARD: "HARD BREAK", SOFT: "SOFT BREAK", MENTION: "MENTION ONLY"}

# Keys whose value IS the thing that draws the map. The id under one of these
# is loaded at draw time, so deleting the target empties the app.
HARD_KEYS = frozenset((
    "itemid", "itemids", "serviceitemid", "sourceitemid", "layeritemid",
    "mapitemid", "webmap", "webscene", "map", "portalitem", "portalitems",
    "url", "itemurl", "layerurl", "sourceurl", "serviceurl",
    "operationallayers", "baselayers", "baselayer", "basemap", "basemaps",
    "layers", "layer", "tables", "featurecollection",
    "datasource", "datasources", "datasourceid", "datasourceids",
    "maindatasourceid", "rootdatasourceid", "usedatasources", "usedatasource",
    "dataset", "datasets", "source", "sources",
))

# Keys that reference the target without drawing it. Deleting the target leaves
# the app standing and the widget broken: an empty filter list, a bookmark that
# goes nowhere, a popup that cannot resolve the layer it points at.
SOFT_KEYS = frozenset((
    "filter", "filters", "definitionexpression", "where", "whereclause",
    "bookmark", "bookmarks", "popup", "popupinfo", "popuptemplate",
    "expressioninfos", "expression", "relatedrecords", "relationship",
    "relationships", "searchlayers", "applicationproperties",
))

# Prose. An id inside one of these is something a person typed, so it caps the
# class at a mention however the surrounding structure is named: a description
# that quotes an item id breaks nothing when that item goes away.
TEXT_KEYS = frozenset((
    "description", "snippet", "summary", "text", "markdown", "notes",
    "licenseinfo", "accessinformation", "caption", "comment", "title",
    "tags", "credits", "attribution",
))

# Keys that carry a PICTURE of another item, not a dependency on it. An item
# card, a gallery entry or a story cover stores the other item's thumbnail url,
# and that url contains that item's id. Deleting the target loses the picture.
# A naive substring scan reports every one of these as a break.
THUMB_KEYS = frozenset((
    "thumbnail", "thumbnailurl", "thumbnail_url", "thumb", "thumburl",
    "largethumbnail", "previewimage", "previewurl",
))

# Characters that end an item id. The boundary test is the difference between
# this tool and a substring scan: an id is a token, not a run of characters
# that happens to appear inside a longer one.
ALNUM = frozenset("0123456789abcdefghijklmnopqrstuvwxyz")
ID_CHARS = frozenset("0123456789abcdef")
ID_LENGTH = 32

# Anything shaped like a credential in a url or a config value. Excerpts of
# item configuration go into the report and onto the screen, and a secured
# service url carries a token in its query string.
SECRET_RE = re.compile(
    r"(?i)\b(token|password|apikey|api_key|code_verifier|signature)="
    r"[^&\s\"'<>]+")

# Columns of the CSV report, in order. Fixed, because a spreadsheet somebody
# filtered on must not gain a column between runs.
CSV_FIELDS = ("id", "title", "type", "owner", "kind", "source", "path",
              "count", "excerpt")


# ----------------------------------------------------------------- the matcher

def scrub(text):
    """Remove anything shaped like a credential from text about to be shown.

    Applied to every excerpt and every error message, not only to the ones that
    look risky. A secured feature service url inside a web map carries a token
    in its query string, and that excerpt is the most useful thing in the
    report, so it is printed with the token taken out rather than dropped.
    """
    return SECRET_RE.sub(lambda m: "%s=%s" % (m.group(1), REDACTED),
                         "%s" % (text,))


def redact(text, *secrets):
    """Remove the caller's own secrets from text, on top of the shaped ones."""
    out = scrub(text)
    for secret in secrets:
        if secret:
            # Coerced because this runs inside an exception handler, where a
            # secret that arrived as something other than a string used to
            # raise TypeError and throw the redaction away with it.
            out = out.replace("%s" % (secret,), REDACTED)
    return out


def looks_like_item_id(text):
    """True when text is exactly one ArcGIS item id: 32 hexadecimal digits."""
    body = ("%s" % (text,)).strip().lower()
    return len(body) == ID_LENGTH and all(c in ID_CHARS for c in body)


def normalise_target(target):
    """Validate the target id and return it in lower case.

    Refused rather than searched for. A truncated or mistyped id is a shorter
    string, a shorter string matches more, and what comes back is a list of
    items that reference something else entirely. Ids are compared without case
    because configurations written by hand and by older tools use both.
    """
    body = ("%s" % (target,)).strip()
    if not looks_like_item_id(body):
        raise ValueError("%r is not an ArcGIS item id. An item id is %d "
                         "hexadecimal characters." % (target, ID_LENGTH))
    return body.lower()


def id_positions(text, target):
    """Every offset in text where target appears AS AN ITEM ID.

    The boundary test is the whole difference between this and str.find. An id
    is a token: what precedes and follows it has to be something other than a
    letter or a digit. Without this the target matches inside a longer
    identifier that merely contains it, and every item carrying that longer
    identifier is reported as a break.
    """
    body = ("%s" % (text,)).lower()
    needle = ("%s" % (target,)).lower()
    if not needle:
        raise ValueError("an empty target would match everything")
    found = []
    at = body.find(needle)
    while at >= 0:
        before = body[at - 1] if at > 0 else ""
        end = at + len(needle)
        after = body[end] if end < len(body) else ""
        if before not in ALNUM and after not in ALNUM:
            found.append(at)
        # Advance by one, not by the length of the needle. Two occurrences can
        # overlap in a mangled string, and skipping the whole match would drop
        # the second one.
        at = body.find(needle, at + 1)
    return found


def token_at(text, pos):
    """The delimited token containing the character at pos.

    Used to see what the id is part of. A url, a quoted string and a word in a
    sentence end at different characters, so the separators are the union:
    whitespace, quotes, brackets and the punctuation JSON puts between values.
    """
    seps = " \t\r\n\f\v\"'<>(){}[],;|\\"
    lo = pos
    while lo > 0 and text[lo - 1] not in seps:
        lo -= 1
    hi = pos
    while hi < len(text) and text[hi] not in seps:
        hi += 1
    return text[lo:hi]


def is_thumbnail_context(path, token):
    """True when this occurrence is a PICTURE of the target, not a use of it.

    Two tests, because either one alone misses cases. The key names the field,
    which catches a thumbnail url wherever it is nested. The token names the
    url, which catches a thumbnail url stored under a key this tool has never
    heard of, because every portal thumbnail url carries the item's own
    /info/thumbnail path.
    """
    for key in path:
        if ("%s" % (key,)).lower() in THUMB_KEYS:
            return True
    low = ("%s" % (token,)).lower()
    return "/info/thumbnail" in low or "/thumbnail/" in low


def classify(path, token):
    """HARD, SOFT, MENTION, or None when this occurrence is not a reference.

    Deepest key wins, then prose caps it. Deepest wins because the innermost
    key is the most specific description of what the value is for: a
    definitionExpression inside operationalLayers is a filter, not a layer. The
    prose cap is applied afterwards and only downwards, so an id typed into the
    description of a layer is a mention and not a break, however hard the
    structure around it looks.
    """
    if is_thumbnail_context(path, token):
        return None
    kind = MENTION
    for key in path:
        low = ("%s" % (key,)).lower()
        if low in HARD_KEYS:
            kind = HARD
        elif low in SOFT_KEYS:
            kind = SOFT
    for key in path:
        if ("%s" % (key,)).lower() in TEXT_KEYS:
            kind = MENTION
    return kind


def walk(node, path=()):
    """Yield (path, text) for every string in a parsed configuration.

    Dictionary KEYS are yielded as well as values, and with the path of the
    dictionary rather than of the key. Experience Builder names its data
    sources after the item they load, so the id is in the key and the class has
    to come from the surrounding dataSources key.

    A dictionary carrying a cell_type puts that type into the path of
    everything under it. That is a notebook cell, and the difference between
    code and markdown is the difference between an item that stops working and
    one that mentions an id in a paragraph.

    List indices are left out of the path on purpose. A finding that says
    widgets/widget_1/useDataSources is readable; one that says
    widgets/widget_1/useDataSources/0/mainDataSourceId is not, and the index
    changes the moment somebody reorders the widget.
    """
    if isinstance(node, dict):
        marker = node.get("cell_type")
        base = path + (marker,) if isinstance(marker, str) and marker else path
        for key, value in node.items():
            if isinstance(key, str):
                yield base, key
                for hit in walk(value, base + (key,)):
                    yield hit
            else:
                # A JSON object cannot have a non-string key, but a caller can
                # hand this function a dict that came from somewhere else.
                for hit in walk(value, base):
                    yield hit
    elif isinstance(node, (list, tuple)):
        for value in node:
            for hit in walk(value, path):
                yield hit
    elif isinstance(node, str):
        yield path, node
    # Numbers, booleans and null cannot hold an item id, so they end the walk.


def excerpt(text, pos, width=44):
    """A one line, credential-free window onto the match.

    The excerpt is what makes a finding actionable: the difference between
    "dashboard X references the item" and "dashboard X binds dataset ds1 to
    it". Whitespace is collapsed so that a pretty printed config does not put
    one finding across nine lines of a CSV cell.
    """
    lo = max(0, pos - width)
    hi = min(len(text), pos + ID_LENGTH + width)
    body = " ".join(text[lo:hi].split())
    if lo > 0:
        body = "..." + body
    if hi < len(text):
        body = body + "..."
    return scrub(body)


def path_label(path):
    """Render a walk path for the report. The empty path is the document root."""
    return "/".join("%s" % (part,) for part in path) or "(root)"


def scan_text(target, text, path, source):
    """Findings for one string. Empty when the id is not in it AS an id."""
    out = []
    for pos in id_positions(text, target):
        kind = classify(path, token_at(text, pos))
        if kind is None:
            # A thumbnail url: a picture of the target, not a use of it.
            # Deleting the target loses the image and nothing else.
            continue
        out.append({"kind": kind, "source": source, "path": path_label(path),
                    "count": 1, "excerpt": excerpt(text, pos)})
    return out


def _collect(hits):
    """Fold repeated findings at one path into one, keeping the first excerpt.

    A web map that names the target in twelve places at the same path is one
    finding with a count of twelve. Twelve identical rows make a person scroll
    rather than read.
    """
    seen = {}
    order = []
    for hit in hits:
        key = (hit["source"], hit["path"], hit["kind"])
        if key in seen:
            seen[key]["count"] += hit["count"]
        else:
            seen[key] = hit
            order.append(key)
    return [seen[key] for key in order]


def scan_payload(target, payload, source):
    """Findings in one fetched body, whether or not it parsed as JSON.

    A body that is not JSON is still searched, as prose. A code attachment, a
    truncated Experience Builder config and a python file all arrive here, and
    an id found in one of them is real. It cannot be classified by structure,
    so it is a mention, which is the honest floor rather than a guess.
    """
    if isinstance(payload, (dict, list)):
        return _collect([hit for path, text in walk(payload)
                         for hit in scan_text(target, text, path, source)])
    if isinstance(payload, str):
        return _collect(scan_text(target, payload, ("(not json)",), source))
    if payload is None:
        return []
    raise ValueError("%s is a %s, which is not a fetched body"
                     % (source, type(payload).__name__))


def scan_item(target, meta, payloads):
    """Every finding for one item, worst class first.

    meta is the search result, which is scanned too: an id in a description or
    a snippet is a mention worth reporting. payloads is whatever could be read
    for the item, as (source, body) pairs.
    """
    hits = scan_payload(target, meta or {}, "metadata")
    for source, payload in payloads or ():
        hits.extend(scan_payload(target, payload, source))
    hits.sort(key=lambda hit: (-RANK[hit["kind"]], hit["source"], hit["path"]))
    return hits


def worst_kind(hits):
    """The most serious class among these findings, None when there are none."""
    if not hits:
        return None
    return max((hit["kind"] for hit in hits), key=lambda kind: RANK[kind])


# ------------------------------------------------------- enumerating the org
#
# The pager is itemcensus's, including how it behaves at the 10,000 ceiling.
# This tool needs the same guarantee for the opposite reason: there, a short
# count is a wrong number; here, an item that never came back is an item whose
# configuration was never searched, and the report would call the target safe
# to delete.

def clamp_num(num):
    """Clamp a page size to what the server will actually honour.

    Clamped before the request rather than after the response, so that the
    paging loop and the server agree about the page size from the first call.
    """
    if not isinstance(num, int) or isinstance(num, bool):
        raise ValueError("page size must be an integer, got %r" % (num,))
    if num < 1:
        raise ValueError("page size must be at least 1, got %r" % (num,))
    return min(num, SEARCH_MAX_NUM)


def is_capped(total):
    """True when a reported total has reached the ceiling and means nothing.

    The comparison is >=, not >. Exactly 10,000 is the cap value itself: an org
    with 10,000 items and an org with 400,000 both report 10,000, and there is
    no way to tell them apart from the number.
    """
    return int(total) >= SEARCH_CEILING


def next_start(page, start, returned):
    """Where the next page begins, or None when this query is finished.

    The server's own nextStart is authoritative and -1 is its end marker. When
    the response omits it, the next page begins after the rows that ACTUALLY
    came back, never after the page size that was asked for: the server clamps
    num without saying so, and a loop that advanced by its own num skipped
    everything in between.
    """
    if start < 1:
        raise ValueError("start is 1-based, got %r" % (start,))
    if returned < 0:
        raise ValueError("a page cannot return %r rows" % (returned,))
    if returned == 0:
        # No rows means no next page, whatever nextStart claims. A start past
        # the end of the result set lands here, and a server that returns
        # nothing while still pointing forward would otherwise page for ever.
        return None
    nxt = page.get("nextStart")
    if nxt is None:
        nxt = start + returned
    nxt = int(nxt)
    if nxt <= 0:
        return None
    if nxt <= start:
        raise ValueError("nextStart %d does not advance past start %d, which "
                         "would page the same rows for ever" % (nxt, start))
    if nxt > SEARCH_CEILING:
        # search refuses a start beyond the ceiling. Stopping here rather than
        # asking is the difference between a slice that gets split and a sweep
        # that dies on the portal's own error.
        return None
    return nxt


def slice_query(base, lo, hi):
    """Render one created-date slice as the q parameter search is given.

    The base query is parenthesised. An operator passing 'type:Web Map OR
    owner:jsmith' would otherwise have the date range AND itself onto the last
    term only, and the sweep would cover a different org than the one asked for.
    """
    if int(lo) > int(hi):
        raise ValueError("slice range is backwards: %d > %d" % (lo, hi))
    query = "created:[%d TO %d]" % (int(lo), int(hi))
    return "(%s) AND %s" % (base, query) if base else query


def crawl(fetch, query, num=SEARCH_MAX_NUM):
    """Page one query to its end. Returns (items by id, totals seen, pages).

    fetch is a callable taking (query, start, num) and returning one parsed
    search response. Everything this tool needs from a network lives behind it,
    which is why the paging and the splitting can be driven by canned pages.
    """
    size = clamp_num(num)
    items = {}
    totals = []
    pages = 0
    start = 1
    while start is not None:
        page = fetch(query, start, size)
        if not isinstance(page, dict):
            raise ValueError("fetch returned %r, which is not a search response"
                             % (page,))
        pages += 1
        totals.append(int(page.get("total") or 0))
        results = page.get("results") or []
        for raw in results:
            iid = raw.get("id")
            if not iid:
                raise ValueError("a search result has no id: %r" % (raw,))
            # Union by id. Two slices that overlap, because an item was edited
            # between them or because the created filter is fuzzy at the
            # boundary, contribute that item once.
            items[iid] = raw
        start = next_start(page, start, len(results))
    return items, totals, pages


def sweep(fetch, base="", now_ms=None, num=SEARCH_MAX_NUM,
          max_partitions=MAX_PARTITIONS, echo=None):
    """Enumerate the org as created-date slices. Returns (items, unproven).

    unproven is a list of human readable reasons the enumeration is not known
    to be complete. It is never discarded: the caller turns a non-empty list
    into exit code 2, because a reference search over an incomplete list of
    items cannot say that nothing references the target.

    A slice that reports the ceiling is split in two and its rows are thrown
    away, since the two halves cover it exactly. A slice that still reports the
    ceiling at one day wide cannot be split any further, and there its rows are
    KEPT: the first 10,000 items of that day are still items whose config can
    be searched, and a partial search that says so beats no search at all.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if max_partitions < 1:
        raise ValueError("--max-slices must be at least 1")
    pending = [(EPOCH_START_MS, int(now_ms))]
    items = {}
    unproven = []
    visited = 0
    pages = 0
    splits = 0
    while pending:
        lo, hi = pending.pop()
        visited += 1
        if visited > max_partitions:
            unproven.append(
                "gave up after %d date slice(s), still at %s. Either the "
                "portal reports the %d result ceiling for every query, or the "
                "sweep needs a narrower --query."
                % (max_partitions, slice_query(base, lo, hi), SEARCH_CEILING))
            break
        query = slice_query(base, lo, hi)
        found, totals, page_count = crawl(fetch, query, num)
        pages += page_count
        # The LARGEST total any page reported, not the last one. A query
        # whose first page says 10,000 and whose last page says 40 has an
        # index that moved underneath it, and believing the smaller number
        # skips the split that would have found the rest.
        total = max(totals) if totals else 0
        if is_capped(total) and hi - lo >= DAY_MS:
            mid = lo + (hi - lo) // 2
            splits += 1
            if echo:
                echo("  at the %d result ceiling, splitting %s"
                     % (SEARCH_CEILING, query))
            # The second half starts one millisecond after the first ends, so
            # no item falls in both and none falls in neither.
            pending.append((mid + 1, hi))
            pending.append((lo, mid))
            continue
        if is_capped(total):
            unproven.append(
                "%s still reports the %d result ceiling at one day wide, so "
                "the organization holds more items created that day than "
                "search will page to. The %d read from it were searched; the "
                "rest were never seen."
                % (query, SEARCH_CEILING, len(found)))
        elif len(found) != total:
            unproven.append("%s reported %d item(s) and returned %d"
                            % (query, total, len(found)))
        items.update(found)
        if echo and found:
            echo("  %d item(s) from %s" % (len(found), query))
    return items, unproven


# ------------------------------------------------------------- the traversal

class Report(object):
    """What the sweep found, and every reason it might not be the whole story."""

    def __init__(self, target, findings, scanned, unread, unproven):
        self.target = target
        self.findings = list(findings)
        self.scanned = int(scanned)
        self.unread = list(unread)
        self.unproven = list(unproven)
        # Folded once, here, rather than per item at every question. items,
        # kind_of, counted and describe each used to rescan the whole finding
        # list for one item, which is quadratic in an org where a lot of
        # things reference one service: 8,000 findings over 4,000 items spent
        # a second doing nothing but filtering.
        self._hits = {}
        self._worst = {}
        for hit in self.findings:
            iid = hit["id"]
            self._hits.setdefault(iid, []).append(hit)
            if iid not in self._worst or RANK[hit["kind"]] > RANK[self._worst[iid]]:
                self._worst[iid] = hit["kind"]

    @property
    def items(self):
        """Ids of the referencing items, worst first, then by id."""
        return sorted(self._worst,
                      key=lambda iid: (-RANK[self._worst[iid]], iid))

    def kind_of(self, iid):
        """The worst class of reference one item makes to the target."""
        return self._worst.get(iid)

    def hits_for(self, iid):
        """Every finding against one item, in the order the report holds them."""
        return self._hits.get(iid, [])

    def counted(self, kind):
        """How many items reference the target at exactly this class."""
        return sum(1 for worst in self._worst.values() if worst == kind)

    @property
    def complete(self):
        """True when every item in the org was enumerated AND read."""
        return not self.unproven and not self.unread

    def __repr__(self):
        return "Report(target=%r, items=%d, scanned=%d, unread=%d)" % (
            self.target, len(self.items), self.scanned, len(self.unread))


def inspect(target, items, reader, echo=None, unproven=()):
    """Search every item's configuration for the target. Returns a Report.

    reader takes (item id, reported size) and returns (payloads, problem).
    Every call is isolated: an item that raises, or that comes back with a
    problem, is recorded as unread and the traversal carries on. One item with
    a 403 on /data used to end the sweep at whatever it had found, which reads
    exactly like an organization where nothing references the target.

    An unread item is still scanned for what the search result already gave us,
    because a description is metadata and arrives with the enumeration. It
    stays in the unread list all the same: a mention found in its metadata says
    nothing about the configuration that could not be read.
    """
    target = normalise_target(target)
    findings = []
    unread = []
    scanned = 0
    for iid in sorted(items):
        meta = items[iid] or {}
        if iid == target:
            # The target references itself everywhere. Skipping it here rather
            # than filtering it out of the report keeps it out of the item
            # count as well.
            continue
        scanned += 1
        payloads = []
        try:
            payloads, problem = reader(iid, int(meta.get("size") or 0))
        except Exception as exc:
            # Deliberately broad. Anything a reader raises for one item is that
            # item's problem: a socket timeout, a portal error, a body that is
            # not what it claimed. None of them are a reason to stop reading
            # the other 9,000 items, and all of them make this item unread.
            problem = scrub("%s" % (exc,))
        if problem:
            unread.append({"id": iid, "title": meta.get("title") or "",
                           "problem": scrub(problem)})
        hits = scan_item(target, meta, payloads)
        for hit in hits:
            row = dict(hit)
            row.update({"id": iid, "title": meta.get("title") or "",
                        "type": meta.get("type") or "",
                        "owner": meta.get("owner") or ""})
            findings.append(row)
        if echo and hits:
            echo("  %s %s (%s)" % (LABEL[worst_kind(hits)], iid,
                                   meta.get("title") or "untitled"))
    findings.sort(key=lambda hit: (-RANK[hit["kind"]], hit["id"],
                                   hit["source"], hit["path"]))
    return Report(target, findings, scanned, unread, unproven)


def describe(report):
    """Render a report as the lines the command line prints."""
    out = []
    if report.findings:
        out.append("%d item(s) reference %s"
                   % (len(report.items), report.target))
        out.append("")
        for iid in report.items:
            hits = report.hits_for(iid)
            head = hits[0]
            out.append("%-12s %s  %s [%s]"
                       % (LABEL[report.kind_of(iid)], iid,
                          head["title"] or "untitled", head["type"] or "?"))
            for hit in hits:
                out.append("             %s: %s (x%d)"
                           % (hit["source"], hit["path"], hit["count"]))
                out.append("             %s" % hit["excerpt"])
        out.append("")
        out.append("%d hard, %d soft, %d mention only, over %d item(s) searched"
                   % (report.counted(HARD), report.counted(SOFT),
                      report.counted(MENTION), report.scanned))
    else:
        out.append("no item in the %d searched references %s"
                   % (report.scanned, report.target))
    if report.unread:
        out.append("")
        out.append("%d item(s) COULD NOT BE READ, so they are not clean, they "
                   "are unknown:" % len(report.unread))
        for row in report.unread[:20]:
            out.append("  %s %s: %s" % (row["id"], row["title"] or "untitled",
                                        row["problem"]))
        if len(report.unread) > 20:
            out.append("  ... and %d more" % (len(report.unread) - 20))
    if report.unproven:
        out.append("")
        out.append("the enumeration is NOT known to be complete:")
        for reason in report.unproven:
            out.append("  - %s" % reason)
    if not report.complete:
        out.append("")
        out.append("Do not read this run as permission to delete. It searched "
                   "what it could read.")
    return out


def exit_code(report):
    """0 nothing references the target, 1 something does, 2 nothing is proven.

    Incomplete beats found, because the two answers are used differently. A
    person who sees 1 goes and looks at the items. A person who sees 0 deletes
    the item, and 0 has to mean the sweep was complete, not that it finished.
    """
    if not report.complete:
        return 2
    return 1 if report.findings else 0


def build_document(url, query, target, taken, report):
    """Assemble the report document. No credential ever enters it."""
    return {
        "whobreaks": 1,
        "url": url,
        "query": query,
        "target": target,
        "taken": taken,
        "complete": report.complete,
        "scanned": report.scanned,
        "items": len(report.items),
        "hard": report.counted(HARD),
        "soft": report.counted(SOFT),
        "mention": report.counted(MENTION),
        "unread": list(report.unread),
        "unproven": list(report.unproven),
        "findings": [{field: hit.get(field, "") for field in CSV_FIELDS}
                     for hit in report.findings],
    }


def is_http_url(url):
    """True when urllib will actually open this url.

    A url typed without its scheme is the common mistake, and urllib answers it
    by raising an exception that quotes the whole url back, query string and
    token included. Refusing the url up front is what stops a token reaching a
    scheduler log.
    """
    return bool(url) and ("%s" % (url,)).lower().startswith(("http://",
                                                             "https://"))


def printable(text, encoding):
    """Make one line safe for a console that cannot encode it.

    ArcGIS Pro runs on Windows, where stdout is cp1252 unless somebody changed
    it, and one item whose title held a character that code page has no room
    for aborted the whole report with UnicodeEncodeError. A replaced character
    loses a letter; the crash lost the sweep.
    """
    if not encoding:
        return text
    try:
        text.encode(encoding)
    except UnicodeEncodeError:
        return text.encode(encoding, "replace").decode(encoding, "replace")
    except LookupError:
        return text
    return text


def utcnow():
    """UTC now, without datetime.utcnow().

    utcnow() is deprecated from 3.12 and datetime.UTC does not exist before
    3.11, so timezone.utc is the spelling that works on ArcGIS Pro's Python and
    on a current python3 alike.
    """
    return datetime.datetime.now(datetime.timezone.utc)


# ---------------------------------------------------------------- portal i/o

def _opener(insecure):
    """Build a urllib opener, optionally without certificate verification.

    Enterprise portals behind an internal CA are the reason --insecure exists.
    It is off by default and refused together with a password, because posting
    credentials down an unverified connection is the failure it would cause.
    """
    if not insecure:
        return urllib.request.build_opener()
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context))


def _open(url, path, params, insecure=False, post=False, secret=None,
          limit=MAX_BODY_BYTES):
    """One REST call, returning the raw body, capped at limit bytes.

    Capped here rather than in the caller, because /data is whatever the owner
    uploaded. A file geodatabase item's /data is the entire upload, and a
    reference search has no reason to pull a gigabyte through a VPN. A config
    truncated at the cap stops being JSON and is searched as text instead.
    """
    params = dict(params)
    params["f"] = "json"
    endpoint = "%s/sharing/rest/%s" % (url.rstrip("/"), path.lstrip("/"))
    data = urllib.parse.urlencode(params).encode("utf-8")
    opener = _opener(insecure)
    try:
        if post:
            response = opener.open(endpoint, data, timeout=HTTP_TIMEOUT)
        else:
            response = opener.open("%s?%s" % (endpoint, data.decode("utf-8")),
                                   timeout=HTTP_TIMEOUT)
        return response.read(limit)
    except Exception as exc:
        # Every credential this request carried, not only the caller's secret.
        # urllib quotes the full url back into its own error for a scheme-less
        # --url, and that url carries the token.
        raise RuntimeError(redact("%s: %s" % (endpoint, exc), secret,
                                  params.get("token"),
                                  params.get("password")))


def _call(url, path, params, insecure=False, post=False, secret=None):
    """One REST call returning parsed JSON, with the portal's errors raised.

    The portal answers HTTP 200 with an error object in the body, so the status
    code proves nothing and the body has to be read every time.
    """
    raw = _open(url, path, params, insecure=insecure, post=post, secret=secret)
    endpoint = "%s/sharing/rest/%s" % (url.rstrip("/"), path.lstrip("/"))
    try:
        body = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise RuntimeError("%s: the portal did not answer with JSON (%s)"
                           % (endpoint, exc))
    if not isinstance(body, dict):
        # Guarded here rather than in each caller. A captive portal login page,
        # a proxy error page and a load balancer's maintenance notice can all
        # parse as valid JSON without being a portal response, and every caller
        # goes straight to body.get(), which is an AttributeError and a
        # traceback instead of "the portal did not answer".
        raise RuntimeError("%s: the portal answered with a JSON %s, not an "
                           "object, so this is not a portal response"
                           % (endpoint, type(body).__name__))
    problem = portal_error(body)
    if problem:
        raise RuntimeError(redact("%s: %s" % (endpoint, problem), secret,
                                  params.get("token"),
                                  params.get("password")))
    return body


def portal_error(payload):
    """The portal's own error message, or None when this is a normal body.

    The portal returns HTTP 200 with {"error": {...}} for a permission denial,
    an expired token and a missing item alike, so this is what tells an
    unreadable item from an empty one.
    """
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        err = payload["error"]
        return "%s %s" % (err.get("code"), err.get("message"))
    return None


def decode_body(raw):
    """Parse a fetched body as JSON, or hand it back as text when it is not.

    None for an empty body, which is what most items return from /data. A body
    that is not JSON is not an error: a code attachment is python, a report is
    a PDF, and a config truncated at the byte cap is half an object. All three
    are searched as text by the caller, which is why this never raises.
    """
    if raw is None:
        return None
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    if not text.strip():
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return text
    if isinstance(parsed, (dict, list)):
        return parsed
    # A bare number or a bare string is valid JSON and no configuration. Kept
    # as text so that an id inside it is still found.
    return text


def portal_fetch(url, token, insecure=False):
    """Build the search callable the enumeration drives.

    Results are sorted by created ascending, because the sweep splits on
    created date. A stable sort on the field being split is what keeps an item
    from sliding between two pages while the sweep runs.
    """
    def fetch(query, start, num):
        params = {"q": query, "start": start, "num": num,
                  "sortField": "created", "sortOrder": "asc"}
        if token:
            params["token"] = token
        return _call(url, "search", params, insecure=insecure, secret=token)
    return fetch


def portal_reader(url, token, insecure=False, max_size=MAX_ITEM_SIZE,
                  max_resources=MAX_RESOURCES):
    """Build the reader inspect() drives: (item id, size) -> (payloads, problem).

    Two endpoints per item, because the dependency graph reads neither.
    /data holds the web map, the dashboard and the notebook. /resources holds
    the Experience Builder draft and the StoryMap draft, which is where an id
    hides while the published app still points somewhere else.

    A problem is returned, not raised. The caller records it against this item
    and reads the next one, so one 403 costs one item instead of the sweep.
    """
    def read(iid, size=0):
        payloads = []
        problems = []
        if max_size and int(size or 0) > max_size:
            return payloads, ("%d byte item was not fetched, over the %d byte "
                              "limit, so its configuration was never searched"
                              % (int(size), max_size))
        params = {"token": token} if token else {}
        try:
            body = decode_body(_open(url, "content/items/%s/data" % iid,
                                     params, insecure, secret=token))
        except RuntimeError as exc:
            problems.append("data: %s" % exc)
        else:
            problem = portal_error(body)
            if problem:
                problems.append("data: %s" % problem)
            elif body is not None:
                payloads.append(("data", body))
        try:
            listing = _call(url, "content/items/%s/resources" % iid,
                            dict(params, num=max_resources), insecure=insecure,
                            secret=token)
        except RuntimeError as exc:
            problems.append("resources: %s" % exc)
        else:
            for name in resource_names(listing, max_resources):
                path = "content/items/%s/resources/%s" % (
                    iid, urllib.parse.quote(name))
                try:
                    body = decode_body(_open(url, path, params, insecure,
                                             secret=token))
                except RuntimeError as exc:
                    problems.append("resources/%s: %s" % (name, exc))
                    continue
                problem = portal_error(body)
                if problem:
                    problems.append("resources/%s: %s" % (name, problem))
                elif body is not None:
                    payloads.append(("resources/%s" % name, body))
        return payloads, "; ".join(problems) or None
    return read


def resource_names(listing, limit=MAX_RESOURCES):
    """The JSON resources of one item, at most limit of them.

    Only JSON. A StoryMap's resources are mostly photographs, and an item id
    cannot be inside a JPEG in a form this tool could classify. The draft that
    matters, draft_<id>.json, is JSON, and so is an Experience Builder config.
    """
    names = []
    for row in (listing.get("resources") or ()):
        name = row.get("resource") if isinstance(row, dict) else row
        if isinstance(name, str) and name.lower().endswith(".json"):
            names.append(name)
    return names[:limit]


def read_secret(username):
    """Get the password from the environment, or prompt for it.

    Never from argv. getpass keeps it off the screen, and it is passed on to
    redact() so that nothing downstream can print it back out.
    """
    secret = os.environ.get(SECRET_ENV)
    if secret:
        return secret
    return getpass.getpass("password for %s (not echoed): " % username)


def generate_token(url, username, secret, insecure=False):
    """Exchange a username and password for a short-lived token."""
    body = _call(url, "generateToken", {
        "username": username,
        "password": secret,
        "client": "referer",
        "referer": url,
        "expiration": 60,
    }, insecure=insecure, post=True, secret=secret)
    token = body.get("token")
    if not token:
        raise RuntimeError("generateToken returned no token")
    return token


def org_id(url, token, insecure=False):
    """The signed-in organization's id, or None when the portal will not say."""
    params = {"token": token} if token else {}
    body = _call(url, "portals/self", params, insecure=insecure, secret=token)
    return body.get("id")


def _make_parent(path):
    """Create the directory the output file goes in, when it is missing."""
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)


def write_json(document, path):
    """Write the report as JSON and return the path."""
    _make_parent(path)
    with io.open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=1, sort_keys=True)
    return path


def write_csv(document, path):
    """Write the findings as CSV and return the path.

    newline="" is not decoration. Without it the csv module's own carriage
    return meets the one the text layer adds on Windows, and every other line
    of the file is blank, which Excel reads as an empty row between findings.
    """
    _make_parent(path)
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_FIELDS)
        for row in document["findings"]:
            writer.writerow([row[field] for field in CSV_FIELDS])
    return path


# ------------------------------------------------------------------ self-test

def self_test():
    """Assertions over the matcher, the classifier and the traversal.

    No portal and no credentials. The only socket is a stand-in portal this
    function starts on 127.0.0.1, so that the HTTP layer underneath the pure
    core is exercised by the same run rather than described.
    """
    import http.server
    import shutil
    import socket
    import tempfile
    import threading

    passed = [0]
    failed = []

    def check(cond, label):
        if cond:
            passed[0] += 1
            print("PASS  %s" % label)
        else:
            failed.append(label)
            print("FAIL  %s" % label)

    def raises(fn, label):
        try:
            fn()
        except ValueError:
            check(True, label)
        except Exception as exc:
            check(False, "%s (wrong exception %r)" % (label, exc))
        else:
            check(False, "%s (no error raised)" % label)

    def fails(fn, label):
        """A portal call that must raise. Returns the redacted message."""
        try:
            fn()
        except RuntimeError as exc:
            check(True, label)
            return "%s" % exc
        except Exception as exc:
            check(False, "%s (wrong exception %r)" % (label, exc))
        else:
            check(False, "%s (no error raised)" % label)
        return ""

    def refuses(argv, label):
        """argparse writes its usage text to stderr, which is swallowed here so
        that a passing self-test prints only PASS lines."""
        noise, sys.stderr = sys.stderr, io.StringIO()
        try:
            _parse(argv)
        except SystemExit:
            check(True, label)
        else:
            check(False, "%s (argparse accepted it)" % label)
        finally:
            sys.stderr = noise

    class Console(io.StringIO):
        """A stdout that reports an encoding, the way a real console does."""
        encoding = "utf-8"

    def captured(fn, encoding="utf-8"):
        """Run fn with stdout and stderr collected. Returns (result, text)."""
        console = Console()
        console.encoding = encoding
        saved = (sys.stdout, sys.stderr)
        sys.stdout, sys.stderr = console, console
        try:
            outcome = fn()
        finally:
            sys.stdout, sys.stderr = saved
        return outcome, console.getvalue()

    print("whobreaks self-test: no portal, no credentials, one loopback server")
    print("-" * 68)

    TARGET = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    OTHER = "0f9e8d7c6b5a49382716f5e4d3c2b1a0"
    # The same 32 characters living inside a longer hexadecimal identifier. A
    # substring scan reports this; an item id is a token, so this is not one.
    LONGER = "bb" + TARGET + "cc"
    THUMB = ("https://county.maps.arcgis.com/sharing/rest/content/items/%s"
             "/info/thumbnail/thumbnail.png" % TARGET)
    PORTAL = "https://county.maps.arcgis.com"
    DAY = DAY_MS
    BASE = 1577836800000                       # 2020-01-01T00:00:00Z, epoch ms
    NOW = BASE + 800 * DAY

    # ---- what counts as an item id at all
    check(looks_like_item_id(TARGET) is True, "a 32 character hex id is an id")
    check(looks_like_item_id(TARGET.upper()) is True,
          "an id in upper case is still an id")
    check(looks_like_item_id(TARGET[:31]) is False,
          "31 characters is not an id")
    check(looks_like_item_id(TARGET + "0") is False,
          "33 characters is not an id")
    check(looks_like_item_id("g" + TARGET[1:]) is False,
          "a non hexadecimal character makes it not an id")
    check(looks_like_item_id("") is False, "an empty string is not an id")
    check(normalise_target("  %s  " % TARGET.upper()) == TARGET,
          "a target is trimmed and lower cased, because configurations carry "
          "both cases  <-- pinned defect")
    raises(lambda: normalise_target(TARGET[:8]),
           "a truncated target is REFUSED, not searched for, because a short "
           "string matches more and the answer would be about another item  "
           "<-- pinned defect")
    raises(lambda: normalise_target(""), "an empty target is refused")
    raises(lambda: normalise_target(None), "a missing target is refused")
    raises(lambda: normalise_target("Parcels"),
           "an item title is refused, it is not an id")

    # ---- the matcher, which is a token search and not a substring scan
    check(id_positions(TARGET, TARGET) == [0],
          "the id on its own matches at the start")
    check(id_positions('{"itemId": "%s"}' % TARGET, TARGET) == [12],
          "the id inside a quoted json value matches")
    check(id_positions("dataSource_1-%s-0" % TARGET, TARGET) == [13],
          "the id inside an Experience Builder data source id matches, because "
          "a dash ends an id  <-- pinned defect")
    check(id_positions("item_%s.json" % TARGET, TARGET) == [5],
          "the id in a resource file name matches, because an underscore and a "
          "dot both end an id")
    check(id_positions(LONGER, TARGET) == [],
          "the same 32 characters INSIDE a longer id do not match  "
          "<-- pinned defect")
    check(id_positions(TARGET + "ff", TARGET) == [],
          "an id with hex digits after it is a different id")
    check(id_positions("ff" + TARGET, TARGET) == [],
          "an id with hex digits before it is a different id")
    check(id_positions(TARGET.upper(), TARGET) == [0],
          "the id in upper case matches, because configurations use both")
    check(len(id_positions("%s and %s" % (TARGET, TARGET), TARGET)) == 2,
          "two occurrences are two positions")
    check(id_positions(TARGET, TARGET.upper()) == [0],
          "a target handed over in upper case still matches, because both "
          "sides of the comparison are lowered  <-- pinned defect")
    check(id_positions("a-a-a", "a-a") == [0, 2],
          "two overlapping occurrences are BOTH found, because the scan "
          "advances by one and not by the length of what it matched")
    check(id_positions(OTHER, TARGET) == [], "another id does not match")
    check(id_positions("", TARGET) == [], "an empty string holds no id")
    check(id_positions("the parcels layer", TARGET) == [],
          "ordinary prose holds no id")
    raises(lambda: id_positions(TARGET, ""),
           "an empty target raises rather than matching everything")

    # ---- the token around a match, which is how the url cases are seen
    check(token_at('"%s"' % TARGET, 1) == TARGET,
          "a quoted value is one token")
    check(token_at("see %s now" % TARGET, 4) == TARGET,
          "a word in a sentence is one token")
    check(token_at(THUMB, THUMB.index(TARGET)) == THUMB,
          "a url is one token, however many slashes it has")
    check(token_at(TARGET, 0) == TARGET, "a bare id is the whole token")
    check(token_at("a,%s,b" % TARGET, 2) == TARGET,
          "a comma separated list splits into tokens")

    # ---- the thumbnail false positive, which is the pinned defect
    check(portal_error({"error": {"code": 403, "message": "no"}})
          == "403 no", "a portal error object is read as code and message")
    check(portal_error({"error": "the field is called error"}) is None,
          "but a config whose own key is called error is not a portal "
          "refusal, it is a body to search  <-- pinned defect")
    check(portal_error({"total": 3}) is None, "a normal body has no error")
    check(is_thumbnail_context(("thumbnail",), TARGET) is True,
          "a thumbnail key is a picture of the item, not a use of it")
    check(is_thumbnail_context(("item", "thumbnailUrl"), TARGET) is True,
          "and so is thumbnailUrl, at any depth")
    check(is_thumbnail_context(("card", "imageUrl"), THUMB) is True,
          "a thumbnail url under a key this tool never heard of is still a "
          "thumbnail, because the url says so  <-- pinned defect")
    check(is_thumbnail_context(
          ("card",), "https://county.maps.arcgis.com/sharing/rest/content/"
          "items/%s/info/thumbnail.png" % TARGET) is True,
          "and so is the older /info/thumbnail.png, which has no directory "
          "after it for the other test to see  <-- pinned defect")
    check(is_thumbnail_context(
          ("card",), "https://county.maps.arcgis.com/sharing/rest/content/"
          "items/%s/info/Thumbnail/Thumbnail.png" % TARGET) is True,
          "and one typed with capitals, because the url is lowered before it "
          "is looked at  <-- pinned defect")
    check(is_thumbnail_context(("url",), THUMB) is True,
          "even under url, which is otherwise a hard reference  "
          "<-- pinned defect")
    check(is_thumbnail_context(("url",), "https://services/x/FeatureServer/0")
          is False, "an ordinary service url is not a thumbnail")
    check(classify(("url",), THUMB) is None,
          "so a thumbnail url is not a reference at all  <-- pinned defect")
    check(classify(("operationalLayers", "itemId"), TARGET) == HARD,
          "an itemId under operationalLayers is a hard break")
    check(classify(("widgets", "widget_1", "url"),
                   "https://services/x/FeatureServer/0") == HARD,
          "a url is a hard break under a widget key nothing here recognises, "
          "because url alone says the value is loaded  <-- pinned defect")

    # ---- the classifier
    check(classify(("dataSources",), TARGET) == HARD,
          "a data source is a hard break")
    check(classify(("widgets", "useDataSources", "mainDataSourceId"), TARGET)
          == HARD, "an Experience Builder data source binding is a hard break")
    check(classify(("datasets", "dataSource", "itemId"), TARGET) == HARD,
          "a dashboard dataset binding is a hard break")
    check(classify(("layers", "url"), TARGET) == HARD,
          "a layer url is a hard break")
    check(classify(("operationalLayers", "popupInfo", "fieldInfos"), TARGET)
          == SOFT, "a popup reference is a soft break, even under a layer, "
                   "because the innermost key is what the value is for  "
                   "<-- pinned defect")
    check(classify(("operationalLayers", "layerDefinition",
                    "definitionExpression"), TARGET) == SOFT,
          "a filter on a layer is a soft break and not a hard one  "
          "<-- pinned defect")
    check(classify(("bookmarks", "name"), TARGET) == SOFT,
          "a bookmark is a soft break")
    check(classify(("description",), TARGET) == MENTION,
          "an id in a description is a mention")
    check(classify(("operationalLayers", "description"), TARGET) == MENTION,
          "prose caps the class DOWNWARDS, so an id typed into a layer's "
          "description is a mention and not a hard break  <-- pinned defect")
    check(classify(("snippet",), TARGET) == MENTION, "so is a snippet")
    check(classify((), TARGET) == MENTION,
          "an id at the root of a document, with no key over it, is a mention")
    check(classify(("somethingNobodyHasHeardOf",), TARGET) == MENTION,
          "an unknown key is a mention, which is the honest floor")
    check(classify(("cells", "code", "source"), TARGET) == HARD,
          "a notebook CODE cell that names the item is a hard break, because "
          "the notebook stops running")
    check(classify(("cells", "markdown", "source"), TARGET) == MENTION,
          "the same string in a MARKDOWN cell is a mention, because prose "
          "cannot break  <-- pinned defect")
    check(RANK[HARD] > RANK[SOFT] > RANK[MENTION],
          "hard outranks soft outranks mention")
    check(worst_kind([{"kind": MENTION}, {"kind": HARD}, {"kind": SOFT}])
          == HARD, "the worst class of a set of findings is the hard one")
    check(worst_kind([]) is None, "no findings have no class")

    # ---- the walk over a parsed configuration
    walked = [(path_label(path), text) for path, text in walk(
        {"a": {"b": "value"}, "n": 7, "ok": True, "gone": None})]
    check(("a/b", "value") in walked, "a nested value carries its key path")
    check(("(root)", "a") in walked and ("a", "b") in walked,
          "keys are walked as well as values, and with the path of the "
          "dictionary they are in, because Experience Builder names a data "
          "source after the item it loads  <-- pinned defect")
    check(not any(text in ("7", "True", "None") for _path, text in walked),
          "numbers, booleans and null end the walk, they cannot hold an id")
    listed = [path_label(path) for path, _text in walk(
        {"layers": [{"url": "x"}, {"url": "y"}]})]
    check(listed.count("layers/url") == 2,
          "list indices are left out of the path, so two layers report the "
          "same readable path  <-- pinned defect")
    celled = dict((path_label(path), text) for path, text in walk(
        {"cells": [{"cell_type": "code", "source": "one"},
                   {"cell_type": "markdown", "source": "two"}]}))
    check(celled.get("cells/code/source") == "one",
          "a notebook code cell puts its type into the path")
    check(celled.get("cells/markdown/source") == "two",
          "and so does a markdown cell, which is what tells them apart")
    check(path_label(()) == "(root)", "the empty path is the document root")
    check([text for _path, text in walk({7: "value"})] == ["value"],
          "a dictionary key that is not a string is skipped and its value is "
          "still walked, because this function is handed dicts that did not "
          "come from json.loads")

    # ---- the fixtures, which are real configuration shapes
    EXB = {
        "_ssr": True,
        "dataSources": {
            "dataSource_1": {"itemId": TARGET, "type": "WEB_MAP",
                             "label": "Permits"},
            "dataSource_2": {"itemId": OTHER, "type": "FEATURE_LAYER"},
        },
        "widgets": {
            "widget_1": {
                "uri": "widgets/arcgis/arcgis-map/",
                "useDataSources": [
                    {"dataSourceId": "dataSource_1-%s-0" % TARGET,
                     "mainDataSourceId": "dataSource_1",
                     "rootDataSourceId": "dataSource_1"}],
            },
            "widget_2": {
                "uri": "widgets/common/card/",
                "config": {"cardConfig": {"imageUrl": THUMB}},
            },
        },
    }
    DASHBOARD = {
        "widgets": [{"type": "mapWidget", "itemId": OTHER}],
        "datasets": [{"id": "ds1", "type": "serviceDataset",
                      "dataSource": {"itemId": TARGET, "layerId": 0}}],
    }
    STORY_DRAFT = {
        "nodes": {"n-x1": {"type": "webmap",
                           "data": {"itemId": TARGET, "itemType": "Web Map"}},
                  "n-x2": {"type": "text",
                           "data": {"text": "replaces map %s" % OTHER}}},
        "resources": {"r-1": {"type": "webmap", "data": {"itemId": TARGET}}},
    }
    NOTEBOOK = {
        "cells": [
            {"cell_type": "markdown",
             "source": ["The source layer used to be %s." % TARGET]},
            {"cell_type": "code",
             "source": ["gis = GIS('home')\n",
                        "lyr = gis.content.get('%s')\n" % TARGET]},
        ],
    }
    WEBMAP = {
        "operationalLayers": [{
            "id": "lyr0",
            "url": "https://services.arcgis.com/abc/FeatureServer/0",
            "layerDefinition": {"definitionExpression":
                                "SOURCE_ID = '%s'" % TARGET},
            "popupInfo": {"description": "joined to %s" % TARGET},
        }],
        "bookmarks": [{"name": "site %s" % TARGET}],
    }
    CLONE = {"title": "Parcel Viewer copy",
             "thumbnail": THUMB,
             "card": {"imageUrl": THUMB},
             "operationalLayers": [{"url": "https://services/x/FeatureServer"}]}

    exb_hits = scan_payload(TARGET, EXB, "resources/config.json")
    check([hit["kind"] for hit in exb_hits] == [HARD, HARD],
          "an Experience Builder config yields two hard breaks: the data "
          "source and the widget binding  <-- pinned defect")
    check(any(hit["path"] == "dataSources/dataSource_1/itemId"
              for hit in exb_hits), "one of them names the data source path")
    check(any("useDataSources" in hit["path"] for hit in exb_hits),
          "the other names the widget that uses it")
    check(all(hit["source"] == "resources/config.json" for hit in exb_hits),
          "and both say which resource they came from, because the published "
          "app does not hold this config  <-- pinned defect")
    check(not any(THUMB in hit["excerpt"] for hit in exb_hits),
          "the card thumbnail in the same config is NOT one of them  "
          "<-- pinned defect")

    dash_hits = scan_payload(TARGET, DASHBOARD, "data")
    check([hit["kind"] for hit in dash_hits] == [HARD],
          "a dashboard dataset binding is one hard break")
    check(dash_hits[0]["path"] == "datasets/dataSource/itemId",
          "at the dataset's own path")
    check("layerId" in dash_hits[0]["excerpt"] or TARGET in
          dash_hits[0]["excerpt"], "with an excerpt a person can act on")

    story_hits = scan_payload(TARGET, STORY_DRAFT, "resources/draft_x.json")
    check([hit["kind"] for hit in story_hits] == [HARD, HARD],
          "a StoryMap draft resource yields the node and the resource that "
          "load the item  <-- pinned defect")
    check(all(hit["source"].startswith("resources/") for hit in story_hits),
          "from the draft, which is a resource and not the item data")

    note_hits = scan_payload(TARGET, NOTEBOOK, "data")
    check(sorted(hit["kind"] for hit in note_hits) == [HARD, MENTION],
          "a notebook yields a hard break for the code cell and a mention for "
          "the markdown one  <-- pinned defect")
    check(any(hit["kind"] == HARD and "content.get" in hit["excerpt"]
              for hit in note_hits),
          "the hard one is the line that fetches the item")

    map_hits = scan_payload(TARGET, WEBMAP, "data")
    check(sorted(set(hit["kind"] for hit in map_hits)) == [MENTION, SOFT],
          "a web map that filters on the item and mentions it in a popup is "
          "soft and mention, never hard  <-- pinned defect")
    check(any(hit["path"].endswith("definitionExpression") for hit in map_hits),
          "the filter is one of them")
    check(any(hit["path"] == "bookmarks/name" for hit in map_hits),
          "the bookmark is another")
    check(scan_payload(TARGET, CLONE, "data") == [],
          "an item whose only occurrence is the target's THUMBNAIL yields "
          "NOTHING, which is the 300 item false positive this tool exists to "
          "avoid  <-- pinned defect")
    clones = [{"id": "c%03d" % n, "thumbnail": THUMB,
               "card": {"imageUrl": THUMB}} for n in range(300)]
    check(sum(len(scan_payload(TARGET, one, "data")) for one in clones) == 0,
          "300 items cloned from the target report zero findings between them")
    check(scan_payload(TARGET, {"layers": [{"itemId": LONGER}]}, "data") == [],
          "an item referencing a LONGER id that contains the target is not a "
          "reference  <-- pinned defect")
    check(scan_payload(TARGET, {"dataSources": {"%s-layer0" % TARGET: {}}},
                       "data")[0]["kind"] == HARD,
          "an id used as a dictionary KEY is found, which is how Experience "
          "Builder names a layer data source  <-- pinned defect")

    # ---- bodies that are not JSON at all
    raw_hits = scan_payload(TARGET, "gis.content.get('%s')  # nightly"
                            % TARGET, "data (not json)")
    check([hit["kind"] for hit in raw_hits] == [MENTION],
          "an item whose /data is not JSON is still searched, as prose  "
          "<-- pinned defect")
    check(raw_hits[0]["path"] == "(not json)",
          "and says so instead of inventing a path")
    check(scan_payload(TARGET, "no id in here", "data") == [],
          "text without the id yields nothing")
    check(scan_payload(TARGET, None, "data") == [],
          "an empty body yields nothing")
    check(scan_payload(TARGET, [], "data") == [],
          "an empty json array yields nothing")
    raises(lambda: scan_payload(TARGET, 17, "data"),
           "a body that is neither text nor json raises rather than being "
           "silently skipped")
    truncated = json.dumps(EXB)[:len(json.dumps(EXB)) // 2]
    check(len(scan_payload(TARGET, truncated, "data")) >= 1,
          "a config truncated at the byte cap no longer parses, and the id is "
          "still found in the text  <-- pinned defect")

    # ---- excerpts, counting and ordering
    # The id and the token have to be in the SAME string. A token in a
    # neighbouring value is never inside this excerpt to begin with, so an
    # assertion written that way passes whatever excerpt() does with it.
    secret_url = ("https://county.maps.arcgis.com/sharing/rest/content/items/"
                  "%s/data?token=SECRET123&f=json" % TARGET)
    secret_map = {"operationalLayers": [{"url": secret_url}]}
    secret_hits = scan_payload(TARGET, secret_map, "data")
    check(len(secret_hits) == 1 and secret_hits[0]["kind"] == HARD,
          "a layer whose url IS the item is one hard break")
    check("SECRET123" in secret_url
          and "SECRET123" not in secret_hits[0]["excerpt"]
          and REDACTED in secret_hits[0]["excerpt"],
          "and the token sitting beside the id in that very url is redacted "
          "out of the excerpt  <-- pinned defect")
    check(REDACTED in scrub("url?token=SECRET123&f=json"),
          "scrub replaces the token value and keeps the rest readable")
    check(scrub("password=hunter2") == "password=[redacted]",
          "a password in a config value is replaced by that exact word, not "
          "by whatever REDACTED happens to hold  <-- pinned defect")
    check(scrub("no secrets here") == "no secrets here",
          "and ordinary text is left alone")
    check(redact("the token is TKN", "TKN") == "the token is %s" % REDACTED,
          "a caller's own token is redacted wherever it appears")
    check(redact("nothing", None, "") == "nothing",
          "a missing secret redacts nothing")
    folded = _collect([
        {"kind": HARD, "source": "data", "path": "layers", "count": 1,
         "excerpt": "first"},
        {"kind": HARD, "source": "data", "path": "layers", "count": 1,
         "excerpt": "second"},
        {"kind": SOFT, "source": "data", "path": "layers", "count": 1,
         "excerpt": "soft"},
        {"kind": HARD, "source": "resources/config.json", "path": "layers",
         "count": 1, "excerpt": "other file"},
    ])
    check(len(folded) == 3,
          "the fold joins two findings only when the file, the path AND the "
          "class all match  <-- pinned defect")
    check(folded[0]["count"] == 2 and folded[0]["excerpt"] == "first",
          "the two that matched become one with a count of two, keeping the "
          "first excerpt")
    check([hit["source"] for hit in folded][-1] == "resources/config.json",
          "and the same path in a second file stays its own finding, because "
          "that is a different file to go and fix  <-- pinned defect")
    repeated = scan_payload(TARGET, {"layers": [{"itemId": TARGET},
                                                {"itemId": TARGET},
                                                {"itemId": TARGET}]}, "data")
    check(len(repeated) == 1 and repeated[0]["count"] == 3,
          "three references at one path are one finding with a count of three")
    mixed = scan_item(TARGET, {"description": "replaces %s" % TARGET},
                      [("data", DASHBOARD)])
    check([hit["kind"] for hit in mixed] == [HARD, MENTION],
          "findings come back worst first, so the top line of a report is the "
          "thing that breaks")
    check(mixed[1]["source"] == "metadata",
          "the mention came from the search result, which needs no extra call")
    check(scan_item(TARGET, None, None) == [],
          "an item with no metadata and nothing readable yields nothing")
    check(excerpt("x" * 500 + TARGET + "y" * 500, 500).startswith("..."),
          "a long excerpt is elided at the front")
    check(excerpt("x" * 500 + TARGET + "y" * 500, 500).endswith("..."),
          "and at the back")
    check("\n" not in excerpt("a\n  b %s\n c" % TARGET, 6),
          "an excerpt from a pretty printed config is one line")

    # ---- the traversal, and what happens when one item cannot be read
    def catalog(*rows):
        return dict((row["id"], row) for row in rows)

    def reader_for(table, raisers=(), problems=None):
        def read(iid, size=0):
            if iid in raisers:
                raise RuntimeError("403 You do not have permissions to access "
                                   "this resource, token=LIVE123")
            return (table.get(iid, []), (problems or {}).get(iid))
        return read

    ITEMS = catalog(
        {"id": TARGET, "title": "Permits", "type": "Feature Service",
         "owner": "gis_admin", "size": 4096},
        {"id": "exb0000000000000000000000000000a", "title": "Permit finder",
         "type": "Web Experience", "owner": "ann", "size": 20},
        {"id": "dsh0000000000000000000000000000b", "title": "Permit board",
         "type": "Dashboard", "owner": "bob", "size": 20},
        {"id": "cln0000000000000000000000000000c", "title": "Cloned viewer",
         "type": "Web Mapping Application", "owner": "ann", "size": 20},
        {"id": "pdf0000000000000000000000000000d", "title": "Locked report",
         "type": "PDF", "owner": "cal", "size": 20},
    )
    TABLE = {
        "exb0000000000000000000000000000a": [("resources/config.json", EXB)],
        "dsh0000000000000000000000000000b": [("data", DASHBOARD)],
        "cln0000000000000000000000000000c": [("data", CLONE)],
    }
    report = inspect(TARGET, ITEMS, reader_for(
        TABLE, raisers=("pdf0000000000000000000000000000d",)))
    check(report.scanned == 4,
          "the target itself is not scanned, an item does not reference itself")
    check(report.items == ["dsh0000000000000000000000000000b",
                           "exb0000000000000000000000000000a"],
          "two of the four items reference the target, and two that break it "
          "equally are listed by id, not by whichever the search returned "
          "first  <-- pinned defect")
    check(report.kind_of("exb0000000000000000000000000000a") == HARD,
          "the Experience Builder app is a hard break")
    check(report.kind_of("cln0000000000000000000000000000c") is None,
          "the clone with the thumbnail is not a reference at all  "
          "<-- pinned defect")
    check(len(report.unread) == 1,
          "the item whose fetch RAISED is counted as unread, not as clean  "
          "<-- pinned defect")
    check(report.unread[0]["id"] == "pdf0000000000000000000000000000d",
          "and is named, so somebody can go and look at it")
    check("LIVE123" not in json.dumps(report.unread),
          "with the token stripped out of the error it raised  "
          "<-- pinned defect")
    check(report.complete is False,
          "one unread item makes the whole run incomplete")
    check(exit_code(report) == 2,
          "which exits 2 even though references were found, because 0 and 1 "
          "are answers and this run has none  <-- pinned defect")
    check(report.counted(HARD) == 2 and report.counted(SOFT) == 0,
          "the counts are per item and not per finding")

    ORDERED = catalog(
        {"id": "dsh0000000000000000000000000000b", "title": "Permit board",
         "type": "Dashboard", "owner": "bob", "size": 20},
        {"id": "aaa0000000000000000000000000000e", "title": "Old notes",
         "type": "Web Map", "owner": "dee", "size": 20,
         "description": "replaced %s last year" % TARGET},
    )
    walked = []
    ordered = inspect(TARGET, ORDERED, reader_for(
        {"dsh0000000000000000000000000000b": [("data", DASHBOARD)]}),
        echo=walked.append)
    check([line.split()[2] for line in walked]
          == ["aaa0000000000000000000000000000e",
              "dsh0000000000000000000000000000b"],
          "the console reports items in id order however the search returned "
          "them, so two runs of the same sweep read the same  "
          "<-- pinned defect")
    check([hit["kind"] for hit in ordered.findings] == [HARD, MENTION],
          "the report opens on the thing that BREAKS, even though the item "
          "that only mentions the target sorts first by id  <-- pinned defect")
    check(ordered.items == ["dsh0000000000000000000000000000b",
                            "aaa0000000000000000000000000000e"],
          "and the item list is in that same order, worst first, id second  "
          "<-- pinned defect")

    clean = inspect(TARGET, ITEMS, reader_for({}))
    check(clean.findings == [] and clean.complete is True,
          "an org where nothing references the target is clean and complete")
    check(exit_code(clean) == 0, "which is the only way to exit 0")
    check("no item in the 4 searched" in "\n".join(describe(clean)),
          "and says how many items it searched to say so")
    empty = inspect(TARGET, {}, reader_for({}))
    check(empty.scanned == 0 and exit_code(empty) == 0,
          "an empty organization is searched and exits 0")
    check(repr(empty).startswith("Report("), "a report renders for a debugger")

    problem_report = inspect(TARGET, ITEMS, reader_for(
        TABLE, problems={"dsh0000000000000000000000000000b":
                         "resources: 500 Internal Server Error"}))
    check(len(problem_report.unread) == 1,
          "an item whose reader REPORTS a problem is unread as well, even "
          "though half of it was read  <-- pinned defect")
    check(problem_report.kind_of("dsh0000000000000000000000000000b") == HARD,
          "and the half that was read is still reported")
    check(exit_code(problem_report) == 2, "so that run cannot exit 1 either")

    lost = dict(ITEMS)
    lost["pdf0000000000000000000000000000d"] = dict(
        lost["pdf0000000000000000000000000000d"],
        description="superseded by %s" % TARGET)
    lost_report = inspect(TARGET, lost, reader_for(
        TABLE, raisers=("pdf0000000000000000000000000000d",)))
    check(lost_report.kind_of("pdf0000000000000000000000000000d") == MENTION,
          "an unread item is still searched in the metadata the enumeration "
          "already returned")
    check(len(lost_report.unread) == 1,
          "and stays unread, because a mention in a description says nothing "
          "about the configuration nobody could read  <-- pinned defect")

    echoed = []
    inspect(TARGET, ITEMS, reader_for(TABLE), echo=echoed.append)
    check(len(echoed) == 2 and "HARD BREAK" in echoed[0],
          "each referencing item is echoed as it is found, because a sweep of "
          "9,000 items takes a while")
    raises(lambda: inspect("not-an-id", ITEMS, reader_for(TABLE)),
           "a traversal for a malformed target refuses before the first fetch")

    # ---- what the report says and what it exits
    lines = "\n".join(describe(report))
    check("2 item(s) reference" in lines, "the report leads with the count")
    check("HARD BREAK" in lines and "resources/config.json" in lines,
          "and names the class and the source of every finding")
    check("COULD NOT BE READ" in lines,
          "the unread items get their own section, in capitals")
    check("Do not read this run as permission to delete" in lines,
          "and an incomplete run says so in words, not only in its exit code  "
          "<-- pinned defect")
    jumbled = Report(TARGET, [
        {"id": "ccc", "kind": HARD, "source": "data", "path": "layers/itemId",
         "count": 1, "excerpt": ""},
        {"id": "aaa", "kind": MENTION, "source": "metadata", "path": "snippet",
         "count": 1, "excerpt": ""},
        {"id": "bbb", "kind": MENTION, "source": "metadata", "path": "snippet",
         "count": 1, "excerpt": ""},
        {"id": "bbb", "kind": HARD, "source": "data", "path": "layers/itemId",
         "count": 1, "excerpt": ""},
    ], 4, [], [])
    check(jumbled.kind_of("bbb") == HARD,
          "an item that both names the target in prose and loads it is a HARD "
          "break, not whichever finding was stored first  <-- pinned defect")
    check(jumbled.items == ["bbb", "ccc", "aaa"],
          "and the report lists what BREAKS before what only mentions, then "
          "settles ties by id, whatever order the findings arrived in  "
          "<-- pinned defect")
    check([hit["path"] for hit in jumbled.hits_for("bbb")]
          == ["snippet", "layers/itemId"],
          "every finding against one item comes back together, in the order "
          "the report holds them, so the console prints each item once  "
          "<-- pinned defect")
    check(jumbled.hits_for("no-such-item") == [],
          "and an item with no findings has none, rather than raising")
    check(jumbled.counted(HARD) == 2 and jumbled.counted(MENTION) == 1,
          "each item is counted once, at its worst class, so four findings "
          "over three items are not four items  <-- pinned defect")
    unproven_report = Report(TARGET, [], 12, [],
                             ["created:[0 TO 1] still reports the ceiling"])
    check(exit_code(unproven_report) == 2,
          "an enumeration that could not be proven complete exits 2, however "
          "empty its finding list is  <-- pinned defect")
    check("NOT known to be complete" in "\n".join(describe(unproven_report)),
          "and says which part of the org it never saw")
    many_unread = Report(TARGET, [], 99,
                         [{"id": "i%02d" % n, "title": "", "problem": "403"}
                          for n in range(25)], [])
    unread_text = "\n".join(describe(many_unread))
    check("and 5 more" in unread_text
          and len([ln for ln in unread_text.splitlines()
                   if ln.startswith("  i")]) == 20,
          "a long unread list prints its first TWENTY rows and then says how "
          "many it held back, so the tail alone cannot carry it  "
          "<-- pinned defect")

    # ---- the pager, which is itemcensus's, including at the ceiling
    check(clamp_num(100) == 100, "a page size of 100 is passed through")
    check(clamp_num(1000) == 100,
          "a page size of 1000 is clamped to 100 before it is sent, because "
          "the server clamps it silently  <-- pinned defect")
    check(clamp_num(1) == 1, "a page size of 1 is honoured")
    raises(lambda: clamp_num(0), "a page size of zero raises")
    raises(lambda: clamp_num(-5), "a negative page size raises")
    raises(lambda: clamp_num("100"), "a page size that is a string raises")
    raises(lambda: clamp_num(True),
           "a page size of True raises, rather than paging one row at a time")
    check(is_capped(10000) is True,
          "exactly 10,000 is the CAP and never a total  <-- pinned defect")
    check(is_capped(9999) is False, "9,999 is a real total")
    check(is_capped(0) is False, "an empty result is not capped")
    check(next_start({"nextStart": 101}, 1, 100) == 101,
          "the server's nextStart is followed")
    check(next_start({"nextStart": -1}, 101, 100) is None,
          "a nextStart of -1 ends the crawl  <-- pinned defect")
    check(next_start({"nextStart": 0}, 1, 10) is None,
          "and so does a nextStart of 0, which ends the crawl rather than "
          "raising about a start that does not advance  <-- pinned defect")
    check(next_start({}, 1, 10) == 11,
          "a short page advances by the ten rows it got, not by the hundred it "
          "asked for  <-- pinned defect")
    check(next_start({}, 1, 0) is None, "a page with no rows ends the crawl")
    check(next_start({"nextStart": 10001}, 9951, 50) is None,
          "a next page starting past the 10,000 ceiling is not asked for  "
          "<-- pinned defect")
    raises(lambda: next_start({"nextStart": 1}, 101, 100),
           "a nextStart that does not advance raises, rather than paging the "
           "same rows for ever")
    raises(lambda: next_start({}, 0, 10), "a start of zero raises, start is 1-based")
    raises(lambda: next_start({}, 1, -1), "a negative row count raises")
    check(slice_query("orgid:ORG", 0, 10) == "(orgid:ORG) AND created:[0 TO 10]",
          "the base query is parenthesised, or the date range would AND onto "
          "its last term only  <-- pinned defect")
    check(slice_query("", 0, 10) == "created:[0 TO 10]",
          "with no base query it is the date range alone")
    raises(lambda: slice_query("", 10, 0), "a backwards date range raises")

    def pages(rows, total=None, size=100):
        """A fetch callable over a fixed list of search results."""
        def fetch(query, start, num):
            del query
            window = rows[start - 1:start - 1 + min(num, size)]
            return {"total": len(rows) if total is None else total,
                    "start": start, "num": len(window), "results": window}
        return fetch

    ROWS = [{"id": "id%028d" % n, "title": "item %d" % n,
             "created": BASE + n * 1000} for n in range(250)]
    crawled, totals, page_count = crawl(pages(ROWS), "created:[0 TO 1]")
    check(len(crawled) == 250,
          "paging reads all 250 items, not the 100 one page holds  "
          "<-- pinned defect")
    check(len(totals) == page_count and page_count == 4,
          "the total is read on every page, not only the first")
    # A query whose pages disagree about the total. The index moved while the
    # crawl was running, and there is no honest number to be had: only a
    # smaller one and a larger one. Whichever the sweep believes decides
    # whether it splits, and a sweep that does not split reports items it
    # never paged to as items that do not exist.
    def drifter(ceiling_page):
        """Reports the ceiling on one page of the first query and 250 else."""
        first = [None]

        def fetch(query, start, num):
            page = pages(ROWS)(query, start, num)
            if first[0] is None:
                first[0] = query
            capped = query == first[0] and start == ceiling_page
            page["total"] = SEARCH_CEILING if capped else 250
            return page
        return fetch

    _rows, early_totals, _n = crawl(drifter(1), "created:[0 TO 1]")
    check(early_totals[0] == SEARCH_CEILING and early_totals[-1] == 250,
          "a query can report the ceiling on its first page and a real count "
          "on its last, which is what a moving index looks like")
    for label, page_no in (("its FIRST page", 1), ("a LATER page", 201)):
        echoed = []
        drifted, drift_unproven = sweep(drifter(page_no), base="", now_ms=NOW,
                                        echo=echoed.append)
        check(any("splitting" in line for line in echoed),
              "a slice that reported the ceiling on %s is split, because the "
              "largest count any page gave is the only safe one to believe  "
              "<-- pinned defect" % label)
        check(len(drifted) == 250 and drift_unproven == [],
              "and every item still arrives from the halves, proven  "
              "<-- pinned defect")
    check(crawl(pages(ROWS[:0]), "q")[0] == {},
          "an empty result set reads nothing and does not loop")
    raises(lambda: crawl(pages([{"title": "no id"}]), "q"),
           "a search result with no id raises rather than being counted")
    raises(lambda: crawl(lambda q, s, n: ["not", "a", "page"], "q"),
           "a fetch that returns something that is not a search response raises")

    swept, unproven = sweep(pages(ROWS), base="orgid:ORG", now_ms=NOW)
    check(len(swept) == 250 and unproven == [],
          "an org under the ceiling is swept in one slice and proven complete")
    capped_query = [None]

    def capped_fetch(query, start, num):
        """Reports the ceiling for the first query and the truth after it.

        For every page of that query, not only its first page. A stand-in that
        capped one page and then told the truth would let the crawl finish
        with an honest total on the last page, and the split this asserts
        would never be reached.
        """
        page = pages(ROWS)(query, start, num)
        if capped_query[0] is None:
            capped_query[0] = query
        if query == capped_query[0]:
            page["total"] = SEARCH_CEILING
        return page

    split, split_unproven = sweep(capped_fetch, base="orgid:ORG", now_ms=NOW)
    check(split_unproven == [],
          "a slice at the ceiling is SPLIT and the two halves prove themselves")
    check(len(split) == 250,
          "and every item still arrives, because the halves cover the parent")
    echoes = []
    capped_query[0] = None
    sweep(capped_fetch, base="orgid:ORG", now_ms=NOW, echo=echoes.append)
    check(any("splitting" in line for line in echoes),
          "the split is echoed, so nobody wonders why the sweep slowed down")

    def always_capped(query, start, num):
        page = pages(ROWS[:100])(query, start, num)
        page["total"] = SEARCH_CEILING
        return page

    floor, floor_unproven = sweep(always_capped, base="", now_ms=DAY_MS // 2)
    check(len(floor_unproven) == 1 and "one day wide" in floor_unproven[0],
          "a slice still at the ceiling one day wide cannot be split again and "
          "is reported unproven  <-- pinned defect")
    check(len(floor) == 100,
          "its rows are KEPT all the same, because an item this tool can still "
          "search beats one it never sees  <-- pinned defect")
    check(exit_code(Report(TARGET, [], len(floor), [], floor_unproven)) == 2,
          "and the run that used them cannot exit 0")
    # 86400000 written out, not DAY_MS. Reading the floor from the
    # constant that defines it would make this pass for any value of it.
    day, day_unproven = sweep(always_capped, base="", now_ms=86400000)
    check(len(day_unproven) == 2,
          "a capped slice EXACTLY one day wide is still split, because the "
          "floor is the width below which a split buys nothing, and both "
          "halves then report unproven  <-- pinned defect")
    check(not [r for r in day_unproven if "created:[0 TO 86400000]" in r],
          "so the day itself is never the slice that gave up, its halves are")

    # What the split actually asked the portal for. The two halves have to
    # tile the parent exactly: an overlap counts an item twice and a gap of
    # one millisecond loses every item created in it.
    seen_slices = []

    def recording(query, start, num):
        if start == 1:
            span = query.split("created:[")[1].rstrip("]")
            lo_s, hi_s = span.split(" TO ")
            seen_slices.append((int(lo_s), int(hi_s)))
        return capped_fetch(query, start, num)

    capped_query[0] = None
    sweep(recording, base="", now_ms=NOW)
    parent = seen_slices[0]
    halves = sorted(seen_slices[1:])
    check(len(halves) == 2
          and halves[0] == (parent[0], (parent[0] + parent[1]) // 2)
          and halves[1] == (halves[0][1] + 1, parent[1]),
          "the two halves meet at one millisecond past the midpoint, so no "
          "item falls in both and none falls in neither  <-- pinned defect")

    budget, budget_unproven = sweep(always_capped, base="", now_ms=NOW,
                                    max_partitions=3)
    check(len(budget_unproven) == 1 and "gave up after 3" in budget_unproven[0],
          "a portal that reports the ceiling for every query stops at the slice "
          "budget instead of splitting for ever  <-- pinned defect")
    check(budget_unproven[0].count("created:[") == 1,
          "and names the slice it gave up on")
    budget_slices = []

    def counting(query, start, num):
        if start == 1:
            budget_slices.append(query)
        return always_capped(query, start, num)

    sweep(counting, base="", now_ms=NOW, max_partitions=3)
    check(len(budget_slices) == 3,
          "and it really stopped after three slices, counted at the fetch, "
          "not merely quoted the number it was given  <-- pinned defect")
    short, short_unproven = sweep(pages(ROWS[:10], total=40), base="",
                                  now_ms=NOW)
    check(len(short) == 10 and "reported 40 item(s) and returned 10"
          in short_unproven[0],
          "a slice that returns fewer rows than the server reported is "
          "unproven, not quietly short  <-- pinned defect")
    raises(lambda: sweep(pages(ROWS), max_partitions=0),
           "a slice budget of zero raises")

    # ---- the pieces of the portal layer that need no socket
    check(is_http_url(PORTAL) is True, "an https url is one urllib can open")
    check(is_http_url("county.maps.arcgis.com") is False,
          "a url with no scheme is refused, because urllib quotes the whole "
          "url back into its error and that url carries the token  "
          "<-- pinned defect")
    check(is_http_url("") is False, "an empty url is refused")
    check(decode_body(b'{"a": 1}') == {"a": 1}, "a json body is parsed")
    check(decode_body(b'[1, 2]') == [1, 2], "a json array body is parsed")
    check(decode_body(b"print('hi')") == "print('hi')",
          "a body that is not json comes back as text, not as an error  "
          "<-- pinned defect")
    check(decode_body(b"") is None, "an empty body is nothing at all")
    check(decode_body(b"   ") is None, "and so is a body of whitespace")
    check(decode_body(b"1234") == "1234",
          "a bare number is valid json and no configuration, so it stays text")
    check(decode_body(b"\xff\xfe binary " + TARGET.encode("ascii"))
          .endswith(TARGET),
          "a body that is not utf-8 is decoded with replacement, so an id "
          "inside a binary upload is still found  <-- pinned defect")
    check(decode_body(None) is None, "no body at all is nothing")
    check(portal_error({"error": {"code": 403, "message": "no"}})
          == "403 no", "a portal error envelope is recognised")
    check(portal_error({"id": "ORG"}) is None, "an ordinary body is not one")
    check(portal_error("text") is None, "and neither is a text body")
    check(resource_names({"resources": [{"resource": "draft_x.json"},
                                        {"resource": "cover.jpg"}]})
          == ["draft_x.json"],
          "only JSON resources are fetched, an id cannot be classified inside "
          "a photograph")
    check(resource_names({"resources": [{"resource": "a.json"}] * 9}, limit=4)
          == ["a.json"] * 4, "and no more than the resource limit")
    many = {"resources": [{"resource": "r%02d.json" % i} for i in range(60)]}
    check(len(resource_names(many)) == 50,
          "an item with sixty json resources is read to the default fifty, so "
          "one StoryMap cannot turn a sweep into an afternoon")
    check(resource_names(many)[-1] == "r49.json",
          "and it is the FIRST fifty, in the order the portal listed them")
    check(resource_names({}) == [], "an item with no resources reads none")
    check(printable("plain", "cp1252") == "plain",
          "a line the console can encode is printed as it is")
    check(printable("caf\u00e9 \u2764", "ascii") != "caf\u00e9 \u2764",
          "a line it cannot encode is replaced rather than crashing the run  "
          "<-- pinned defect")
    check(printable("caf\u00e9", None) == "caf\u00e9",
          "a stream with no encoding is left alone")
    check(printable("caf\u00e9", "not-a-codec") == "caf\u00e9",
          "and so is one whose encoding python has never heard of")
    def https_context(opener):
        """The ssl context the opener will use, None when it takes urllib's."""
        for handler in opener.handlers:
            if isinstance(handler, urllib.request.HTTPSHandler):
                # Before python 3.10 the handler keeps None when it was built
                # without a context, and urllib makes the verified default at
                # request time instead.
                return getattr(handler, "_context", None)
        return None

    default_context = https_context(_opener(False))
    check(default_context is None
          or default_context.verify_mode == ssl.CERT_REQUIRED,
          "the default opener verifies the certificate  <-- pinned defect")
    check(https_context(_opener(True)).verify_mode == ssl.CERT_NONE,
          "--insecure is the only opener that does not, which is why it is "
          "refused together with --username")
    check(https_context(_opener(True)).check_hostname is False,
          "and it does not check the hostname either")
    check(https_context(urllib.request.OpenerDirector()) is None,
          "an opener with no https handler at all reports no context, which "
          "is what the default check above reads as urllib's own")
    check(utcnow().tzinfo is not None,
          "the timestamp is timezone aware, because datetime.utcnow() is "
          "deprecated and datetime.UTC does not exist on ArcGIS Pro's python")

    typed = []
    saved_getpass = getpass.getpass
    getpass.getpass = lambda prompt="": typed.append(prompt) or "typed-secret"
    try:
        check(read_secret("gis_admin") == "typed-secret",
              "with nothing in the environment the password is PROMPTED for, "
              "never taken from argv  <-- pinned defect")
        check(typed and "not echoed" in typed[0],
              "and the prompt says it will not be echoed")
        # Set through the literal name, not through SECRET_ENV. Reading
        # the name from the constant would pass under any rename of it.
        os.environ["WHOBREAKS_PASSWORD"] = "from-the-environment"
        check(read_secret("gis_admin") == "from-the-environment",
              "WHOBREAKS_PASSWORD is the variable a scheduled run sets, and "
              "it is used when it is set  <-- pinned defect")
    finally:
        getpass.getpass = saved_getpass
        os.environ.pop("WHOBREAKS_PASSWORD", None)

    # ---- a stand-in portal on a real socket
    #
    # Everything above is a pure function over canned dictionaries. This
    # section starts an http.server on 127.0.0.1 and drives the whole command
    # line through urllib: real query strings, a real POST carrying a real
    # password, a real HTTP 500 on one item, a real body that is not JSON, and
    # real Experience Builder and StoryMap resources. A capability that is only
    # ever described is not tested.
    EXB_ID = "eb" + "0" * 30
    DASH_ID = "da" + "0" * 30
    STORY_ID = "5a" + "0" * 30
    NOTE_ID = "0b" + "0" * 30
    MAP_ID = "ac" + "0" * 30
    CLONE_ID = "c1" + "0" * 30
    DEAD_ID = "de" + "0" * 30
    TEXT_ID = "7e" + "0" * 30
    DESC_ID = "dc" + "0" * 30
    BIG_ID = "b1" + "0" * 30
    # Never in the catalogue: these two exist so that the per item failure
    # paths are driven over a real socket rather than described.
    PRIV_ID = "9d" + "0" * 30
    PART_ID = "9e" + "0" * 30
    PASSWORD = "hunter2-live"
    LIVE_TOKEN = "LIVE-TOKEN-0123456789"

    def row(iid, title, kind, owner="ann", created=BASE, size=2048, **extra):
        meta = {"id": iid, "title": title, "type": kind, "owner": owner,
                "created": created, "modified": created, "access": "org",
                "size": size, "numViews": 3}
        meta.update(extra)
        return meta

    CATALOG = [
        row(TARGET, "Permits", "Feature Service", owner="gis_admin"),
        row(EXB_ID, "Permit finder", "Web Experience"),
        row(DASH_ID, "Permit board", "Dashboard", owner="bob"),
        row(STORY_ID, "How permits work", "StoryMap"),
        row(NOTE_ID, "Nightly refresh", "Notebook", owner="cal"),
        row(MAP_ID, "Permit map", "Web Map"),
        row(CLONE_ID, "Cloned viewer", "Web Mapping Application"),
        row(DEAD_ID, "Locked report", "PDF", owner="cal"),
        row(TEXT_ID, "refresh.py", "Code Attachment"),
        row(DESC_ID, "Archive notes", "Document Link",
            description="superseded by %s, keep for audit" % TARGET),
        row(BIG_ID, "Parcel delivery", "File Geodatabase",
            size=MAX_ITEM_SIZE + 1),
    ]
    PYTEXT = ("import arcgis\n"
              "lyr = gis.content.get('%s')  # nightly refresh\n" % TARGET)
    DATA = {
        # No EXB_ID: its published /data is empty and the draft config lives
        # in a resource, which is the case this tool exists for.
        DASH_ID: DASHBOARD,
        STORY_ID: {"root": "draft_x.json"},
        NOTE_ID: NOTEBOOK,
        MAP_ID: WEBMAP,
        CLONE_ID: CLONE,
        TEXT_ID: PYTEXT,
    }
    RESOURCES = {
        EXB_ID: {"config.json": EXB, "cover.jpg": "not json"},
        STORY_ID: {"draft_x.json": STORY_DRAFT},
        PART_ID: {"gone.json": "BOOM", "bad.json": {"error": {
            "code": 403, "message": "Item resource does not exist or is "
            "inaccessible."}}, "empty.json": "", "text.json": "just text"},
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, fmt, *args):
            self.server.seen.append(self.path)

        def _send(self, raw, code=200, ctype="application/json"):
            if not isinstance(raw, bytes):
                raw = raw.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _json(self, body, code=200):
            self._send(json.dumps(body), code)

        def do_GET(self):
            path, _sep, query = self.path.partition("?")
            self._route(path, dict(urllib.parse.parse_qsl(query)))

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8")
            self.server.posted.append(body)
            self._route(self.path, dict(urllib.parse.parse_qsl(body)))

        def _route(self, path, params):
            rest = "/sharing/rest/"
            # A portal reached under /blind answers every call and refuses to
            # name its organization, which is a real Enterprise configuration.
            blind = path.startswith("/blind")
            if blind:
                path = path[len("/blind"):]
            if not path.startswith(rest):
                return self._json({"error": {"code": 404,
                                             "message": "not a portal"}})
            route = path[len(rest):]
            if route == "generateToken":
                if params.get("username") == "notoken":
                    # A portal behind a misconfigured SAML proxy answers 200
                    # with neither a token nor an error.
                    return self._json({"expires": 1})
                if params.get("password") != PASSWORD:
                    # A portal behind a chatty reverse proxy echoes the
                    # rejected submission back. That is what redact() is for.
                    return self._json({"error": {"code": 400, "message":
                                       "Invalid username or password. (sent "
                                       "%s/%s)" % (params.get("username"),
                                                   params.get("password"))}})
                return self._json({"token": LIVE_TOKEN, "expires": 1})
            if route == "portals/self":
                return self._json({} if blind else {"id": "REALORG"})
            if route == "notanobject":
                return self._json(["maintenance", "in progress"])
            if route == "search":
                return self._search(params)
            if route.startswith("content/items/"):
                return self._item(route[len("content/items/"):])
            return self._json({"error": {"code": 400,
                                         "message": "unhandled " + route}})

        def _search(self, params):
            query = params.get("q") or ""
            start = int(params.get("start") or 1)
            num = min(int(params.get("num") or 10), SEARCH_MAX_NUM)
            rows = [item for item in CATALOG if self._match(item, query)]
            window = rows[start - 1:start - 1 + num]
            total = len(rows)
            if self.server.cap_next:
                # The real server reports the ceiling and pages no further.
                # One query is enough to make the sweep split.
                self.server.cap_next = False
                total = SEARCH_CEILING
            nxt = start + len(window)
            return self._json({"total": total, "start": start,
                               "num": len(window),
                               "nextStart": -1 if nxt > len(rows) else nxt,
                               "results": window})

        def _match(self, item, query):
            for clause in query.split(" AND "):
                clause = clause.strip()
                if clause.startswith("(") and clause.endswith(")"):
                    clause = clause[1:-1]
                if clause.startswith("created:["):
                    lo, hi = clause[len("created:["):-1].split(" TO ")
                    if not int(lo) <= item["created"] <= int(hi):
                        return False
                    continue
                field, _sep, value = clause.partition(":")
                if field == "orgid":
                    continue
                if ("%s" % item.get(field, "")) != value:
                    return False
            return True

        def _item(self, rest):
            iid, _slash, tail = rest.partition("/")
            if iid == PRIV_ID:
                # HTTP 200 carrying an error object, which is how a portal
                # says no. The status code proves nothing.
                return self._json({"error": {"code": 403, "message":
                                   "You do not have permissions to access "
                                   "this resource or perform this operation."}})
            if tail == "data":
                if iid == DEAD_ID:
                    # A real HTTP failure, not an error envelope. urllib raises
                    # this one, which is the path that used to end the sweep.
                    return self._send(b"upstream failed", 500, "text/plain")
                if iid not in DATA:
                    return self._send(b"")
                body = DATA[iid]
                if isinstance(body, str):
                    return self._send(body, ctype="text/plain")
                return self._json(body)
            if tail == "resources":
                names = sorted(RESOURCES.get(iid, {}))
                return self._json({"total": len(names), "start": 1,
                                   "num": len(names),
                                   "nextStart": -1,
                                   "resources": [{"resource": name,
                                                  "created": 1, "size": 9}
                                                 for name in names]})
            if tail.startswith("resources/"):
                name = urllib.parse.unquote(tail[len("resources/"):])
                body = RESOURCES.get(iid, {}).get(name)
                if body is None:
                    return self._json({"error": {"code": 404,
                                                 "message": "no resource"}})
                if body == "BOOM":
                    return self._send(b"upstream failed", 500, "text/plain")
                if isinstance(body, str):
                    return self._send(body, ctype="text/plain")
                return self._json(body)
            return self._json({"error": {"code": 400,
                                         "message": "unhandled " + rest}})

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server.seen = []
    server.posted = []
    server.cap_next = False
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    workdir = tempfile.mkdtemp(prefix="whobreaks-selftest-")
    try:
        LIVE = "http://127.0.0.1:%d" % server.server_address[1]
        check(server.server_address[0] == "127.0.0.1",
              "the stand-in portal is bound to the loopback address and "
              "nothing else")
        check(server.socket.family == socket.AF_INET, "over ipv4")

        # the transport, one call at a time
        check(org_id(LIVE, None) == "REALORG",
              "portals/self answers over a real socket")
        check(generate_token(LIVE, "gis_admin", PASSWORD) == LIVE_TOKEN,
              "a real POST exchanges a real password for a token")
        check(any("password=%s" % PASSWORD in body for body in server.posted),
              "and the password really did travel in the POST body, not in "
              "the query string  <-- pinned defect")
        rejected = fails(lambda: generate_token(LIVE, "gis_admin", "wrong-pass"),
                         "a rejected sign-in raises")
        check("wrong-pass" not in rejected,
              "with the password stripped out of the portal's own echo of it  "
              "<-- pinned defect")
        empty_token = fails(lambda: generate_token(LIVE, "notoken", PASSWORD),
                            "a sign-in that comes back with no token raises "
                            "rather than sweeping anonymously and reporting "
                            "an org of public items  <-- pinned defect")
        check("no token" in empty_token, "and says what was missing")
        shaped = fails(lambda: _call(LIVE, "notanobject", {}),
                       "a portal answering with a JSON array is refused, not "
                       "fed to body.get() as a traceback  <-- pinned defect")
        check("list" in shaped, "and the refusal says what came back instead")
        unhandled = fails(lambda: _call(LIVE, "content/users/x", {}),
                          "a REST path the portal does not implement raises")
        check("unhandled" in unhandled, "and names the path it refused")
        stray = fails(lambda: _call(LIVE, "content/items/%s/info" % EXB_ID,
                                    {"token": LIVE_TOKEN}, secret=LIVE_TOKEN),
                      "and so does an item endpoint this tool never asks for, "
                      "rather than being read as an empty item")
        check("unhandled" in stray, "which is also named")
        wrong_root = fails(lambda: _call(LIVE + "/portal", "portals/self", {}),
                           "a url carrying a context path the portal does not "
                           "serve is refused, not read as an empty org  "
                           "<-- pinned defect")
        check("not a portal" in wrong_root,
              "and says that is what happened, because /portal is right on "
              "one Enterprise and wrong on the next")
        vanished = fails(lambda: _call(
            LIVE, "content/items/%s/resources/vanished.json" % EXB_ID,
            {"token": LIVE_TOKEN}, secret=LIVE_TOKEN),
            "a resource that is gone by the time it is fetched raises, which "
            "the reader turns into one unread item  <-- pinned defect")
        check("404" in vanished and LIVE_TOKEN not in vanished,
              "with the portal's own code and no token in the message")
        boom = fails(lambda: _open(LIVE, "content/items/%s/data" % DEAD_ID,
                                   {"token": LIVE_TOKEN}, secret=LIVE_TOKEN),
                     "a real HTTP 500 raises rather than returning an empty "
                     "body that would read like a clean item  "
                     "<-- pinned defect")
        check(LIVE_TOKEN not in boom,
              "and the token that call carried is not in the message  "
              "<-- pinned defect")
        notjson = fails(lambda: _call(LIVE, "content/items/%s/data" % TEXT_ID,
                                      {}),
                        "a body that is not JSON raises from _call, which is "
                        "why /data is read with _open instead")
        check("did not answer with JSON" in notjson,
              "and says so in words")

        whole = _open(LIVE, "content/items/%s/data" % STORY_ID,
                      {"token": LIVE_TOKEN}, secret=LIVE_TOKEN)
        clipped = _open(LIVE, "content/items/%s/data" % STORY_ID,
                        {"token": LIVE_TOKEN}, secret=LIVE_TOKEN,
                        limit=len(whole) // 2)
        check(len(clipped) == len(whole) // 2 < len(whole),
              "a body is read only to the byte cap, so one file geodatabase "
              "item cannot pull a gigabyte through a VPN  <-- pinned defect")
        check(isinstance(decode_body(whole), dict)
              and isinstance(decode_body(clipped), str),
              "and the half that arrived is no longer JSON, so it comes back "
              "as text to be searched as prose  <-- pinned defect")
        check(MAX_BODY_BYTES == 4 * 1024 * 1024,
              "the cap that applies when nobody passes one is four mebibytes")

        # the reader, per item, over the wire
        read = portal_reader(LIVE, LIVE_TOKEN)
        payloads, problem = read(EXB_ID, 20)
        check(problem is None, "the Experience Builder item reads cleanly")
        check([source for source, _body in payloads]
              == ["resources/config.json"],
              "its config comes from a RESOURCE, which is the file the "
              "dependency graph never opens  <-- pinned defect")
        check(payloads[0][1]["dataSources"]["dataSource_1"]["itemId"] == TARGET,
              "and it really is the widget config, parsed")
        payloads, problem = read(STORY_ID, 20)
        check(sorted(source for source, _body in payloads)
              == ["data", "resources/draft_x.json"],
              "a StoryMap reads both its data and its draft resource")
        payloads, problem = read(DEAD_ID, 20)
        check(payloads == [] and "500" in problem,
              "an item whose /data fails over HTTP returns a problem, not an "
              "exception that would end the sweep  <-- pinned defect")
        payloads, problem = read(BIG_ID, MAX_ITEM_SIZE + 1)
        check(payloads == [] and "never searched" in problem,
              "an item too large to fetch is a problem too, so it is unread "
              "rather than clean  <-- pinned defect")
        check(not any(("content/items/%s/data" % BIG_ID) in path
                      for path in server.seen),
              "and it was never asked for at all")
        payloads, problem = read(TEXT_ID, 20)
        check(problem is None and isinstance(payloads[0][1], str),
              "an item whose /data is python comes back as text  "
              "<-- pinned defect")
        payloads, problem = read(PRIV_ID, 20)
        check(payloads == [] and problem.count("403") == 2,
              "an item the token may not read reports BOTH endpoints as a "
              "problem, because a 403 arrives as HTTP 200 with an error "
              "object  <-- pinned defect")
        payloads, problem = read(PART_ID, 20)
        check([source for source, _body in payloads]
              == ["resources/text.json"],
              "an item with four resources yields the one that could be read")
        check("gone.json: " in problem and "500" in problem,
              "the resource that failed over HTTP is a problem against that "
              "item, and the other resources were still read  "
              "<-- pinned defect")
        check("bad.json" in problem and "403" in problem,
              "so is the one the portal refused with an error object")
        check("empty.json" not in problem,
              "an empty resource is not a problem, it is an empty resource")
        check(not any("cover.jpg" in path for path in server.seen),
              "the photograph next to the Experience Builder config was never "
              "fetched, because an item id cannot be read out of a JPEG")
        before = len(server.seen)
        anon = portal_reader(LIVE, None)
        anon(MAP_ID, 20)
        check(not any("token=" in path for path in server.seen[before:]),
              "an anonymous read sends no token parameter at all  "
              "<-- pinned defect")

        # the command line, end to end, against that portal
        code, seen = captured(lambda: main(
            ["--url", LIVE, "--target", TARGET, "--token", LIVE_TOKEN]))
        check(code == 2,
              "a sweep with two unreadable items exits 2, whatever it found  "
              "<-- pinned defect")
        check("7 item(s) reference %s" % TARGET in seen,
              "seven of the ten items reference the target")
        for name, iid in (("Experience Builder app", EXB_ID),
                          ("dashboard", DASH_ID),
                          ("StoryMap draft", STORY_ID),
                          ("notebook", NOTE_ID)):
            line = [one for one in seen.splitlines() if iid in one
                    and one.startswith("HARD BREAK")]
            check(bool(line), "the %s is a hard break over the wire" % name)
        check(any(line.startswith("SOFT BREAK") and MAP_ID in line
                  for line in seen.splitlines()),
              "the web map that only filters on it is a soft break")
        check(any(line.startswith("MENTION ONLY") and TEXT_ID in line
                  for line in seen.splitlines()),
              "the python attachment is a mention")
        check(any(line.startswith("MENTION ONLY") and DESC_ID in line
                  for line in seen.splitlines()),
              "and so is the item that names it in a description")
        check(CLONE_ID not in seen,
              "the cloned item carrying the target's thumbnail is not in the "
              "report at all  <-- pinned defect")
        check("COULD NOT BE READ" in seen and DEAD_ID in seen,
              "the item that answered 500 is reported as unread")
        check(BIG_ID in seen, "and so is the one too large to fetch")
        check(LIVE_TOKEN not in seen,
              "the token never reaches stdout  <-- pinned defect")
        check("4 hard, 1 soft, 2 mention only" in seen,
              "and the tail counts every class")

        code, seen = captured(lambda: main(
            ["--url", LIVE, "--target", TARGET, "--token", LIVE_TOKEN,
             "--query", "type:Web Map"]))
        check(code == 1,
              "a complete sweep that found a reference exits 1  "
              "<-- pinned defect")
        check("1 item(s) reference" in seen and "SOFT BREAK" in seen,
              "--query narrows the sweep to the one item it names")
        code, seen = captured(lambda: main(
            ["--url", LIVE, "--target", TARGET, "--token", LIVE_TOKEN,
             "--query", "type:Form"]))
        check(code == 0 and "no item in the 0 searched" in seen,
              "a sweep that finds nothing, and read everything it found, is "
              "the only run that exits 0  <-- pinned defect")

        before = len(server.seen)
        code, seen = captured(lambda: main(
            ["--url", LIVE, "--target", TARGET, "--query", "type:Web Map"]))
        check(code == 1 and MAP_ID in seen,
              "an anonymous sweep of the public items needs no credential at "
              "all")
        check(not any("token=" in path for path in server.seen[before:]),
              "and sends no token parameter on any call, not on search and not "
              "on /data  <-- pinned defect")

        server.cap_next = True
        code, seen = captured(lambda: main(
            ["--url", LIVE, "--target", TARGET, "--token", LIVE_TOKEN]))
        check("at the 10000 result ceiling, splitting" in seen,
              "a portal reporting the ceiling makes the sweep split by created "
              "date, over the wire  <-- pinned defect")
        check("7 item(s) reference %s" % TARGET in seen,
              "and every referencing item is still found after the split")

        os.environ[SECRET_ENV] = PASSWORD
        try:
            code, seen = captured(lambda: main(
                ["--url", LIVE, "--target", TARGET, "--username", "gis_admin",
                 "--query", "type:Dashboard"]))
        finally:
            del os.environ[SECRET_ENV]
        check(code == 1 and DASH_ID in seen,
              "--username signs in with the password from the environment and "
              "runs the sweep")
        check(PASSWORD not in seen and LIVE_TOKEN not in seen,
              "and neither the password nor the token it bought reaches "
              "stdout  <-- pinned defect")

        os.environ[SECRET_ENV] = "wrong-pass"
        try:
            code, seen = captured(lambda: main(
                ["--url", LIVE, "--target", TARGET, "--username", "gis_admin"]))
        finally:
            del os.environ[SECRET_ENV]
        check(code == 2, "a rejected sign-in exits 2")
        check("wrong-pass" not in seen,
              "and the rejected password is not printed  <-- pinned defect")

        # the report files
        out_json = os.path.join(workdir, "nested", "breaks.json")
        code, seen = captured(lambda: main(
            ["--url", LIVE, "--target", TARGET, "--token", LIVE_TOKEN,
             "--query", "type:Dashboard", "--out", out_json]))
        check(code == 1 and not os.path.exists(out_json),
              "--out without --apply writes nothing  <-- pinned defect")
        check("Check only" in seen, "and says the run was a check")
        code, seen = captured(lambda: main(
            ["--url", LIVE, "--target", TARGET, "--token", LIVE_TOKEN,
             "--query", "type:Dashboard", "--out", out_json, "--apply"]))
        check(code == 1 and os.path.isfile(out_json),
              "--apply writes the report, and creates the folder it lives in")
        with io.open(out_json, encoding="utf-8") as handle:
            written = handle.read()
        document = json.loads(written)
        check(document["target"] == TARGET and document["hard"] == 1,
              "the document names the target and counts the classes")
        check(document["findings"][0]["path"] == "datasets/dataSource/itemId",
              "and carries the path of every finding")
        check(LIVE_TOKEN not in written and PASSWORD not in written,
              "no credential reaches the file on disk  <-- pinned defect")
        top_keys = re.findall(r'^ "([a-z]+)"', written, re.M)
        check(top_keys == sorted(top_keys) and len(top_keys) > 5,
              "and the json comes out with its keys in order, so two runs a "
              "week apart diff to the findings that changed  <-- pinned "
              "defect")
        out_csv = os.path.join(workdir, "breaks.csv")
        code, seen = captured(lambda: main(
            ["--url", LIVE, "--target", TARGET, "--token", LIVE_TOKEN,
             "--query", "type:Dashboard", "--out", out_csv, "--format", "csv",
             "--apply"]))
        with io.open(out_csv, encoding="utf-8", newline="") as handle:
            csv_text = handle.read()
        check(csv_text.splitlines()[0] == ",".join(CSV_FIELDS),
              "the csv header is the fixed column list")
        check("\r\r\n" not in csv_text,
              "with no blank line between rows: the defect DOUBLES the "
              "carriage return, it does not add a whole empty line, so that "
              "is the sequence asserted on  <-- pinned defect")
        check(len(list(csv.reader(io.StringIO(csv_text)))) == 2,
              "and one row per finding")
        check(LIVE_TOKEN not in csv_text, "and no credential in it either")

        # usage errors, which never reach the portal
        code, seen = captured(lambda: main([]))
        check(code == 64 and "--url is required" in seen,
              "no --url at all is a usage error")
        code, seen = captured(lambda: main(["--url", LIVE]))
        check(code == 64 and "--target is required" in seen,
              "no --target is a usage error")
        code, seen = captured(lambda: main(
            ["--url", "county.maps.arcgis.com", "--target", TARGET]))
        check(code == 64 and "https://" in seen,
              "a scheme-less --url is refused before any request is built  "
              "<-- pinned defect")
        code, seen = captured(lambda: main(
            ["--url", LIVE, "--target", "Parcels"]))
        check(code == 64 and "not an ArcGIS item id" in seen,
              "a target that is not an item id is refused before the sweep  "
              "<-- pinned defect")
        code, seen = captured(lambda: main(
            ["--url", LIVE, "--target", TARGET, "--apply"]))
        check(code == 64 and "--apply needs --out" in seen,
              "--apply with nowhere to write is a usage error")
        code, seen = captured(lambda: main(
            ["--url", LIVE, "--target", TARGET, "--username", "u",
             "--insecure"]))
        check(code == 64 and "unverified connection" in seen,
              "--insecure with --username is refused, because that posts the "
              "password down an unverified connection  <-- pinned defect")
        code, seen = captured(lambda: main(
            ["--url", LIVE, "--target", TARGET, "--num", "0"]))
        check(code == 64 and "--num" in seen, "a --num of zero is a usage error")
        code, seen = captured(lambda: main(
            ["--url", LIVE, "--target", TARGET, "--max-slices", "0"]))
        check(code == 64 and "--max-slices" in seen,
              "a --max-slices of zero is a usage error")
        code, seen = captured(lambda: main(
            ["--url", LIVE + "/blind", "--target", TARGET]))
        check(code == 2 and "could not read the org id" in seen,
              "a portal that will not name its org asks for --query rather "
              "than sweeping the wrong thing  <-- pinned defect")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        shutil.rmtree(workdir, ignore_errors=True)

    # ---- the document, without a portal
    doc = build_document(PORTAL, "orgid:ORG", TARGET, "2026-09-16T00:00:00Z",
                         report)
    check(doc["whobreaks"] == 1 and doc["complete"] is False,
          "the document records whether the sweep was complete")
    check(len(doc["unread"]) == 1 and doc["items"] == 2,
          "and carries the unread list next to the findings, because the two "
          "have to be read together  <-- pinned defect")
    unproven_doc = build_document(PORTAL, "orgid:ORG", TARGET, "t",
                                  unproven_report)
    check(unproven_doc["unproven"] == unproven_report.unproven
          and unproven_doc["unproven"] != [],
          "the document carries the reasons the sweep was not proven, because "
          "a reader of the file cannot see the console  <-- pinned defect")
    check(sorted(doc["findings"][0]) == sorted(CSV_FIELDS),
          "every finding has exactly the csv columns, whatever the format")
    check("LIVE123" not in json.dumps(doc),
          "and no token from any error message is in it")

    # ---- argument handling
    args = _parse(["--url", PORTAL, "--target", TARGET])
    check(args.apply is False, "--apply defaults to OFF")
    check(args.insecure is False, "--insecure defaults to OFF")
    check(args.self_test is False, "--self-test defaults to OFF")
    check(args.token is None and args.username is None,
          "no credential is assumed")
    check(args.out is None, "nothing is written by default")
    check(args.format == "json", "the default format is json")
    check(args.query is None, "the default query is the whole org")
    check(args.num == SEARCH_MAX_NUM, "--num defaults to the server's cap")
    check(args.max_slices == MAX_PARTITIONS,
          "--max-slices defaults to the configured budget")
    check(_parse(["--self-test"]).self_test is True, "--self-test parses")
    check(_parse(["--url", PORTAL, "--apply"]).apply is True, "--apply is read")
    check(_parse(["--url", PORTAL, "--insecure"]).insecure is True,
          "--insecure is read")
    check(_parse(["--url", PORTAL, "--target", TARGET]).target == TARGET,
          "--target is read")
    check(_parse(["--url", PORTAL, "--token", "T"]).token == "T",
          "--token is read")
    check(_parse(["--url", PORTAL, "--username", "u"]).username == "u",
          "--username is read")
    check(_parse(["--url", PORTAL, "--query", "q"]).query == "q",
          "--query is read")
    check(_parse(["--url", PORTAL, "--out", "f.json"]).out == "f.json",
          "--out is read")
    check(_parse(["--url", PORTAL, "--format", "csv"]).format == "csv",
          "--format is read")
    check(_parse(["--url", PORTAL, "--num", "25"]).num == 25, "--num is read")
    check(_parse(["--url", PORTAL, "--max-slices", "9"]).max_slices == 9,
          "--max-slices is read")
    refuses(["--url", PORTAL, "--format", "xlsx"],
            "a format nothing can write is refused by argparse")
    refuses(["--url", PORTAL, "--num", "lots"], "a non-numeric --num is refused")
    refuses(["--target"], "a --target with no value is refused")
    check("password" not in _parse(["--url", PORTAL]).__dict__,
          "there is no --password attribute at all, because argv is readable "
          "by every process on the box  <-- pinned defect")
    refuses(["--url", PORTAL, "--password", "hunter2"],
            "a --password flag is refused, it does not exist")
    refuses(["--url", PORTAL, "--delete"],
            "there is no --delete flag either, this tool only reads  "
            "<-- pinned defect")

    # ---- the harness itself, which has to be able to report red
    #
    # A self-test whose failure path is never exercised is not a control: it
    # reports green because nothing ever calls the other branch. The harness is
    # run here against five deliberate failures, with its output swallowed and
    # its tally put back, so that a green run has still proven it can go red.
    def probe():
        check(False, "a false check must be recorded as a failure")
        raises(lambda: None, "a function that raises nothing must fail")
        raises(lambda: 1 / 0, "a function that raises the wrong thing must fail")
        refuses(["--self-test"], "an argv argparse accepts must fail")
        return [fails(lambda: None, "a call that does not raise must fail"),
                fails(lambda: 1 / 0,
                      "a call that raises the wrong thing must fail")]

    kept_passed, kept_failed = passed[0], list(failed)
    returned, noise = captured(probe)
    probe_passed, probe_failed = passed[0], list(failed)
    passed[0], failed[:] = kept_passed, kept_failed
    check(len(probe_failed) - len(kept_failed) == 6,
          "the harness records a false check, a missing exception, two wrong "
          "exceptions, an argv argparse accepted and a portal call that did "
          "not raise as six failures, so a broken tool turns this self-test "
          "red  <-- pinned defect")
    check(probe_passed == kept_passed,
          "not one of those six was counted as a pass")
    check(noise.count("FAIL  ") == 6,
          "every recorded failure prints a FAIL line an operator can see")
    check(returned == ["", ""],
          "a portal call that did not fail the right way yields no message to "
          "assert on")

    print("-" * 68)
    total = passed[0] + len(failed)
    if failed:
        print("%d assertions, %d failed" % (total, len(failed)))
        for item in failed:
            print("  FAILED: %s" % item)
        return 1
    print("%d assertions, 0 failed" % total)
    return 0


# ----------------------------------------------------------------------- cli

def _parse(argv):
    ap = argparse.ArgumentParser(
        prog="whobreaks.py",
        description="Report every item in an ArcGIS organization that "
                    "references the item you are about to delete.",
        epilog="Read-only. No flag deletes, moves or edits anything. The "
               "password is never a flag: export %s or answer the prompt."
               % SECRET_ENV,
    )
    ap.add_argument("--url",
                    help="portal url, e.g. https://county.maps.arcgis.com")
    ap.add_argument("--target",
                    help="item id whose references you want, 32 hex characters")
    ap.add_argument("--query",
                    help="search query to sweep (default: orgid of the "
                         "signed-in org)")
    ap.add_argument("--token", help="an existing portal token")
    ap.add_argument("--username",
                    help="generate a token for this user. The password comes "
                         "from %s or an unechoed prompt, never from argv."
                         % SECRET_ENV)
    ap.add_argument("--out", help="file to write the report to")
    ap.add_argument("--format", choices=["json", "csv"], default="json",
                    help="report format (default json)")
    ap.add_argument("--num", type=int, default=SEARCH_MAX_NUM,
                    help="rows per search page to ask for. Clamped to %d, "
                         "which is all the server gives (default %d)."
                         % (SEARCH_MAX_NUM, SEARCH_MAX_NUM))
    ap.add_argument("--max-slices", dest="max_slices", type=int,
                    default=MAX_PARTITIONS,
                    help="give up after this many created-date slices rather "
                         "than splitting for ever (default %d)"
                         % MAX_PARTITIONS)
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS certificate verification, for an Enterprise "
                         "portal behind an internal CA. Refused together with "
                         "--username.")
    ap.add_argument("--apply", action="store_true",
                    help="write the report file. Without this the sweep runs "
                         "and prints, and nothing is written.")
    ap.add_argument("--self-test", dest="self_test", action="store_true",
                    help="run the offline assertions and exit")
    return ap.parse_args(argv)


def _authenticate(args):
    """Return a token, or None for an anonymous read."""
    if args.token:
        return args.token
    if not args.username:
        return None
    secret = read_secret(args.username)
    return generate_token(args.url, args.username, secret, args.insecure)


def main(argv=None):
    args = _parse(sys.argv[1:] if argv is None else argv)

    if args.self_test:
        return self_test()

    if not args.url:
        print("error: --url is required. Use --self-test to verify the tool "
              "without a portal.", file=sys.stderr)
        return 64
    if not is_http_url(args.url):
        print("error: --url must start with https:// or http://, got %r. "
              "urllib quotes a url it cannot open back into its own error, "
              "and that url carries the token." % args.url, file=sys.stderr)
        return 64
    if not args.target:
        print("error: --target is required: the item id you are about to "
              "delete.", file=sys.stderr)
        return 64
    try:
        target = normalise_target(args.target)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 64
    if args.apply and not args.out:
        print("error: --apply needs --out, the file to write the report to.",
              file=sys.stderr)
        return 64
    if args.insecure and args.username:
        print("error: --insecure with --username would post your password "
              "down an unverified connection. Pass --token instead.",
              file=sys.stderr)
        return 64
    if args.num < 1:
        print("error: --num must be at least 1.", file=sys.stderr)
        return 64
    if args.max_slices < 1:
        print("error: --max-slices must be at least 1.", file=sys.stderr)
        return 64

    try:
        token = _authenticate(args)
        query = args.query
        if not query:
            oid = org_id(args.url, token, args.insecure)
            if not oid:
                raise RuntimeError("could not read the org id, pass --query")
            query = "orgid:%s" % oid
        print("reading %s" % args.url)
        print("target: %s" % target)
        print("query: %s" % query)
        items, unproven = sweep(portal_fetch(args.url, token, args.insecure),
                                base=query, num=args.num,
                                max_partitions=args.max_slices,
                                echo=lambda line: print(line))
        print("searching the configuration of %d item(s)" % len(items))
        report = inspect(target, items,
                         portal_reader(args.url, token, args.insecure),
                         echo=lambda line: print(line), unproven=unproven)
    except (RuntimeError, ValueError) as exc:
        # ValueError is how the enumeration rejects a page it cannot trust: a
        # result with no id, a nextStart that does not advance. Those come from
        # the portal, not from a bug here, so they are an exit code and a
        # message rather than a traceback.
        print("error: %s" % scrub(exc), file=sys.stderr)
        return 2

    print("")
    encoding = getattr(sys.stdout, "encoding", None)
    for line in describe(report):
        print(printable(line, encoding))

    if args.out:
        document = build_document(args.url, query, target,
                                  utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                                  report)
        if not args.apply:
            print("")
            print("Check only. No report was written. Re-run with --apply.")
        else:
            writer = write_csv if args.format == "csv" else write_json
            print("")
            print("wrote %s" % writer(document, args.out))

    return exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
