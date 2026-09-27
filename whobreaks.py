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
    python whobreaks.py --url https://county.maps.arcgis.com --target ITEMID --token TOKEN --depth 3 --graph week2.json --apply
    python whobreaks.py --compare week1.json week2.json

--depth follows HARD breaks past the first hop, offline, over the references
the same sweep recorded between every pair of items. The target's own service
url is read once and searched for as text; no url found inside an item is
ever opened. --graph writes the edge list and --compare diffs two of them.

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

# Largest body this tool will read from /data or one resource. An Experience
# Builder config is tens of kilobytes; a file geodatabase item's /data is the
# whole upload. A truncated body no longer parses as JSON and is searched as
# text, and its item is UNREAD: an id past the cap was never looked at.
MAX_BODY_BYTES = 4 * 1024 * 1024

# Items whose reported size is larger than this are not fetched at all, and are
# counted as UNREAD rather than as clean. An unread item is not evidence of
# anything, which is why it never disappears quietly.
MAX_ITEM_SIZE = 32 * 1024 * 1024

# Most JSON resources this tool reads per item, and the rows per page of the
# resource listing. A StoryMap with hundreds of draft resources would otherwise
# turn one sweep into an afternoon. An item with more is UNREAD.
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

# Every item id in a lowered string, with the same boundary as id_positions:
# 32 hexadecimal characters with no letter or digit on either side. This is
# what finds the references BETWEEN items, not only the ones to the target.
ID_TOKEN_RE = re.compile(r"(?<![0-9a-z])[0-9a-f]{32}(?![0-9a-z])")

# Lower case for A to Z only. str.lower() is not length preserving: a dotted
# capital I becomes two characters, every offset after it moves, and the token
# read at that offset is a different one, or past the end of the string. Ids
# and urls are ASCII, so nothing a match needs is lost.
ASCII_LOWER = dict((code, code + 32) for code in range(ord("A"), ord("Z") + 1))

# Characters that continue a service url, so a match that runs into one is part
# of a longer url and not the target's. The left side includes the dot, so a
# host is not found inside a longer host name. The right side includes the
# digits, so layer 1 is not found inside layer 12, and a slash, a quote or a
# question mark ends it, so a layer url under the target's service is found.
URL_LEFT = ALNUM | frozenset("-._")
URL_RIGHT = ALNUM | frozenset("-_")

# Anything shaped like a credential in a url or a config value. Excerpts of
# item configuration go into the report and onto the screen, and a secured
# service url carries a token in its query string. The key may follow an
# underscore: \b does not, and access_token= went out in clear. The value may
# follow a colon or spaces and may be quoted, because a notebook writes
# token="..." and a JSON body writes "token": "...", and both went out in
# clear when only key=value was recognised.
SECRET_RE = re.compile(
    r"(?i)(?<![a-z0-9])(token|password|apikey|api_key|code_verifier|"
    r"signature)([\"']?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|"
    r"[^&\s\"'<>,;(){}\[\]]+)")

# Characters that move a terminal's cursor or change what it shows. An item
# title is typed by whoever owns the item, and one holding ESC [2K and a
# carriage return rewrote the report line above it on the operator's screen.
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# A url path that ends here is a server, a web adaptor or the root of its
# services, not one service. Searched for, it matches every service under it.
SERVER_ROOTS = frozenset(("arcgis", "server", "portal", "rest", "services"))

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
    return SECRET_RE.sub(lambda m: m.group(1) + m.group(2) + REDACTED,
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
    needle = lower(target)
    if not needle:
        raise ValueError("an empty target would match everything")
    return _bounded(lower(text), needle, ALNUM, ALNUM)


def lower(text):
    """text in lower case, A to Z only, so every offset into it stays valid."""
    return ("%s" % (text,)).translate(ASCII_LOWER)


def _bounded(body, needle, left, right):
    """Offsets of needle in body where neither neighbour continues the token."""
    found = []
    at = body.find(needle)
    while at >= 0:
        before = body[at - 1] if at > 0 else ""
        end = at + len(needle)
        after = body[end] if end < len(body) else ""
        if before not in left and after not in right:
            found.append(at)
        # Advance by one, not by the length of the needle. Two occurrences can
        # overlap in a mangled string, and skipping the whole match would drop
        # the second one.
        at = body.find(needle, at + 1)
    return found


def url_needle(raw, target=None):
    """The target's service url as a search needle, or (None, why) when not.

    Lower case, with the scheme, the query string, the fragment and any
    trailing slash taken off, because a configuration writes the same service
    as http or https, with or without ?token=, with or without a slash. The
    url is only ever READ from the target's own item and searched for as text:
    nothing this tool finds inside an item is ever opened.

    Refused as a needle, with the reason, when it would match too much: a url
    that is a bare server, a web adaptor or its rest/services root matches
    every service on that server, and a viewer url whose query string opens
    another item matches every link through that viewer. A url that
    carries the target id is left to the id search, which already finds it,
    so the same occurrence is not counted twice. The id is looked for BEFORE
    the query string comes off: an app's url is index.html?id=<the app>, and
    without its query it is every app of that template in the org.
    """
    body = lower(raw or "").strip()
    if not body:
        return None, "the target item has no url"
    if target and id_positions(body, target):
        return None, "its url carries the item id, which is searched already"
    page = body.split("#", 1)[0].split("?", 1)[0]
    other = ID_TOKEN_RE.search(body[len(page):])
    if other:
        # A viewer url that opens ANOTHER item: index.html?webmap=<that map>.
        # Without its query it is every link through the same viewer.
        return None, ("its url opens another item (%s) through its query "
                      "string, and without that query it would match every "
                      "link through the same page" % other.group(0))
    body = page
    if "://" in body:
        body = body.split("://", 1)[1]
    body = body.strip("/")
    host = body.split("/", 1)[0]
    if "@" in host:
        # user:password@host. A config names the service without them, and
        # the needle is printed, so they are taken off before either.
        body = body[len(host.rsplit("@", 1)[0]) + 1:]
    if "/" not in body or body.rsplit("/", 1)[1] in SERVER_ROOTS:
        return None, ("its url names a whole server (%s), and a search for "
                      "that would match every service on it" % body)
    return body, None


def url_positions(text, needle):
    """Every offset in text where the service url needle appears as a whole url.

    The whole-token rule is the same idea as the id's: what precedes the match
    cannot continue a host name and what follows it cannot continue a path
    segment. Without it the target's layer 1 matches inside layer 12, and a
    service on maps.example.org matches the same path on oldmaps.example.org.
    """
    if not needle:
        return []
    return _bounded(lower(text), needle, URL_LEFT, URL_RIGHT)


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

    A loop over an explicit stack, not recursion. One item whose config was
    nested 1,500 deep ran into the recursion limit, and the RecursionError
    ended the whole sweep on exit 2 with a real break in another item unread.
    Children go onto the stack in reverse, so they come off in document order.
    """
    stack = [(node, path)]
    while stack:
        node, path = stack.pop()
        if isinstance(node, dict):
            marker = node.get("cell_type")
            base = (path + (marker,) if isinstance(marker, str) and marker
                    else path)
            for key, value in reversed(list(node.items())):
                if isinstance(key, str):
                    stack.append((value, base + (key,)))
                    # The key itself, as a string at the dictionary's path.
                    stack.append((key, base))
                else:
                    # A JSON object cannot have a non-string key, but a caller
                    # can hand this function a dict from somewhere else.
                    stack.append((value, base))
        elif isinstance(node, (list, tuple)):
            stack.extend((value, path) for value in reversed(node))
        elif isinstance(node, str):
            yield path, node
        # Numbers, booleans and null cannot hold an item id: they end here.


def excerpt(text, pos, width=44):
    """A one line, credential-free window onto the match.

    The excerpt is what makes a finding actionable: the difference between
    "dashboard X references the item" and "dashboard X binds dataset ds1 to
    it". Whitespace is collapsed so that a pretty printed config does not put
    one finding across nine lines of a CSV cell.

    Scrubbed BEFORE it is cut. Cut first, a window that starts inside
    "token=" keeps the value and loses the key that scrub() looks for.
    """
    # ponytail: scrubs 1 KB either side, not the whole string, so one 4 MB
    # body with a thousand matches is not scrubbed a thousand times. A secret
    # value longer than that is not a token this tool has ever seen.
    span_lo = max(0, pos - 1024)
    span_hi = min(len(text), pos + ID_LENGTH + 1024)
    clean = scrub(text[span_lo:span_hi])
    at = len(scrub(text[span_lo:pos]))
    lo = max(0, at - width)
    hi = min(len(clean), at + ID_LENGTH + width)
    body = " ".join(clean[lo:hi].split())
    if lo > 0 or span_lo > 0:
        body = "..." + body
    if hi < len(clean) or span_hi < len(text):
        body = body + "..."
    return body


def path_label(path):
    """Render a walk path for the report. The empty path is the document root."""
    return "/".join("%s" % (part,) for part in path) or "(root)"


def scan_text(target, text, path, source, url=None):
    """Findings for one string. Empty when the id is not in it AS an id.

    url is the target's service url needle from url_needle(), or None. A match
    on it is classified by the same path rules as a match on the id: the url
    under a layer loads the service, the url in a description mentions it.
    """
    positions = id_positions(text, target)
    if url:
        positions = sorted(set(positions) | set(url_positions(text, url)))
    out = []
    for pos in positions:
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


def strings_of(payload, source):
    """(path, text) for every string in one fetched body.

    A body that is not JSON is still searched, as prose. A code attachment, a
    truncated Experience Builder config and a python file all arrive here, and
    an id found in one of them is real. It cannot be classified by structure,
    so it is a mention, which is the honest floor rather than a guess.
    """
    if isinstance(payload, (dict, list)):
        return walk(payload)
    if isinstance(payload, str):
        return [(("(not json)",), payload)]
    if payload is None:
        return []
    raise ValueError("%s is a %s, which is not a fetched body"
                     % (source, type(payload).__name__))


def scan_payload(target, payload, source, url=None):
    """Findings in one fetched body, whether or not it parsed as JSON."""
    return _collect([hit for path, text in strings_of(payload, source)
                     for hit in scan_text(target, text, path, source, url)])


def scan_item(target, meta, payloads, url=None):
    """Every finding for one item, worst class first.

    meta is the search result, which is scanned too: an id in a description or
    a snippet is a mention worth reporting. payloads is whatever could be read
    for the item, as (source, body) pairs.
    """
    hits = scan_payload(target, meta or {}, "metadata", url)
    for source, payload in payloads or ():
        hits.extend(scan_payload(target, payload, source, url))
    hits.sort(key=lambda hit: (-RANK[hit["kind"]], hit["source"], hit["path"]))
    return hits


def item_refs(meta, payloads, known, skip=()):
    """{item id: worst class} for every OTHER item of the sweep this one names.

    known maps a lowered id to the id as the sweep holds it, so an id that is
    not an item of this sweep is not an edge. The class comes from classify(),
    exactly as it does for the target, so a thumbnail is no edge at all and an
    id in a description is a MENTION edge that the impact search never follows.
    skip holds the item itself and the target, whose edge comes from the
    findings instead, because only the findings also match the service url.
    """
    worst = {}
    bodies = [("metadata", meta or {})] + list(payloads or ())
    for source, body in bodies:
        for path, text in strings_of(body, source):
            for match in ID_TOKEN_RE.finditer(lower(text)):
                other = known.get(match.group(0))
                if other is None or other in skip:
                    continue
                kind = classify(path, token_at(text, match.start()))
                if kind is not None and RANK[kind] > RANK.get(worst.get(other),
                                                              0):
                    worst[other] = kind
    return worst


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
            # A row that is not an object is a page this tool cannot trust,
            # refused as ValueError like a row with no id, not a traceback.
            # An id that is not a string is refused the same way. A number
            # there stopped the sort of the item ids as a TypeError, and a
            # list stopped the union by id: both were tracebacks, which exit
            # 1, and 1 reads as "something references the target".
            iid = raw.get("id") if isinstance(raw, dict) else None
            if not iid or not isinstance(iid, str):
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

    def __init__(self, target, findings, scanned, unread, unproven, edges=(),
                 names=None):
        self.target = target
        self.findings = list(findings)
        self.scanned = int(scanned)
        self.unread = list(unread)
        self.unproven = list(unproven)
        # Every reference between two items of the sweep, as {from, to, kind}:
        # the item that holds the reference, the item it names, and the class.
        self.edges = sorted(edges, key=lambda edge: (edge["from"], edge["to"]))
        # Title and type of every item searched, for the transitive report,
        # whose items need not have a finding against the target at all.
        self.names = dict(names or {})
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


def inspect(target, items, reader, echo=None, unproven=(), url=None):
    """Search every item's configuration for the target. Returns a Report.

    url is the target's service url needle, or None. The same read of each item
    also records every reference it makes to any OTHER item of the sweep, so
    the transitive impact costs no extra portal call.

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
    known = dict((("%s" % (iid,)).lower(), iid) for iid in items)
    findings = []
    unread = []
    edges = []
    names = {}
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
        hits = scan_item(target, meta, payloads, url)
        names[iid] = {"title": meta.get("title") or "",
                      "type": meta.get("type") or ""}
        if hits:
            edges.append({"from": iid, "to": target, "kind": worst_kind(hits)})
        for other, kind in item_refs(meta, payloads, known,
                                     (iid, target)).items():
            edges.append({"from": iid, "to": other, "kind": kind})
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
    return Report(target, findings, scanned, unread, unproven, edges, names)


def impact(edges, target, depth):
    """Items that break when target goes, along HARD edges only, to depth hops.

    Returns ({item: (hop, via, loads)}, beyond). via is the first-hop item the
    break travels through and loads is the item one hop nearer the target.
    beyond is how many more items load one at the last hop, so a depth that
    was too small says so instead of reading as the end of the chain.

    HARD only, because a break has to travel. A map that filters on the target
    still draws; an app that loads that map is not broken by the filter. A
    story that names an item in its prose breaks nothing, and a dashboard that
    embeds the story is not broken by the prose either. A search that followed
    every edge would report both of them, and would report more of them the
    more people typed item ids into descriptions.

    Deterministic: each level is walked in id order and the first item to
    reach another wins, so two runs over the same org print the same chain.
    """
    if depth < 1:
        raise ValueError("depth must be at least 1, got %r" % (depth,))
    loaders = {}
    for edge in edges:
        if edge["kind"] == HARD:
            loaders.setdefault(edge["to"], set()).add(edge["from"])
    reached = {}
    frontier = [target]
    for hop in range(1, depth + 1):
        found = []
        for node in frontier:
            for src in sorted(loaders.get(node, ())):
                # A cycle comes back to the target or to an item already
                # placed. Neither is a new break, and skipping them is what
                # makes the search end.
                if src == target or src in reached:
                    continue
                via = src if hop == 1 else reached[node][1]
                reached[src] = (hop, via, node)
                found.append(src)
        frontier = sorted(found)
    beyond = set(src for node in frontier for src in loaders.get(node, ())
                 if src != target and src not in reached)
    return reached, len(beyond)


def transitive(report, depth):
    """The items beyond the first hop, as report rows, and the beyond count."""
    reached, beyond = impact(report.edges, report.target, depth)
    rows = []
    for iid in sorted(reached, key=lambda one: (reached[one][0], one)):
        hop, via, parent = reached[iid]
        if hop > 1:
            name = report.names.get(iid, {})
            rows.append({"id": iid, "title": name.get("title", ""),
                         "type": name.get("type", ""), "hop": hop,
                         "via": via, "loads": parent})
    return rows, beyond


def describe_transitive(report, depth):
    """The console section for --depth above 1."""
    rows, beyond = transitive(report, depth)
    out = [""]
    if rows:
        out.append("%d more item(s) break through a hard break, to depth %d:"
                   % (len(rows), depth))
        for row in rows:
            out.append("HOP %-8d %s  %s [%s]"
                       % (row["hop"], row["id"], row["title"] or "untitled",
                          row["type"] or "?"))
            out.append("             loads %s (%s), first hop %s"
                       % (row["loads"], report.names.get(row["loads"], {})
                          .get("title") or "untitled", row["via"]))
    else:
        out.append("nothing breaks beyond the first hop, to depth %d" % depth)
    if beyond:
        out.append("%d more item(s) load one at hop %d. Raise --depth to "
                   "follow them." % (beyond, depth + 1))
    if report.unread:
        out.append("%d unread item(s) may load any of these at any hop: their "
                   "configuration was never searched." % len(report.unread))
    return out


def describe(report, depth=1):
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
    if depth > 1:
        out.extend(describe_transitive(report, depth))
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


def build_document(url, query, target, taken, report, depth=1,
                   service_url=None):
    """Assemble the report document. No credential ever enters it."""
    document = {
        "whobreaks": 1,
        "url": url,
        "query": query,
        "target": target,
        "taken": taken,
        "service_url": service_url,
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
    if depth > 1:
        rows, beyond = transitive(report, depth)
        document.update({"depth": depth, "transitive": rows,
                         "beyond": beyond})
    return document


# ---------------------------------------------------------- snapshot and diff

def build_graph(url, query, target, taken, report, service_url=None):
    """The edge list of one sweep, with the flags that say how far to trust it.

    The flags travel with the edges because a diff has to read them. An edge
    that is missing from a sweep that could not read its item has not gone
    away, it was not looked for.
    """
    return {
        "whobreaks_graph": 1,
        "url": url,
        "query": query,
        "target": target,
        "taken": taken,
        "service_url": service_url,
        "complete": report.complete,
        "scanned": report.scanned,
        "unread": sorted(row["id"] for row in report.unread),
        "unproven": list(report.unproven),
        # Every item searched. An edge is only recorded to an item of the
        # sweep, so a diff has to know whether the item an edge names was
        # there to be named at all.
        "items": sorted(report.names),
        "edges": [dict(edge) for edge in report.edges],
    }


def load_graph(path):
    """Read a graph file this tool wrote, or raise ValueError saying why not."""
    with io.open(path, encoding="utf-8") as handle:
        body = json.load(handle)
    if (not isinstance(body, dict) or body.get("whobreaks_graph") != 1
            or not isinstance(body.get("edges"), list)):
        raise ValueError("%s is not a whobreaks graph file. Write one with "
                         "--apply --graph." % path)
    for edge in body["edges"]:
        # Every field a string before kind is looked up in RANK: a list there
        # is unhashable, and the diff builds sets of these fields.
        if (not isinstance(edge, dict)
                or not all(isinstance(edge.get(field), str) and edge[field]
                           for field in ("from", "to", "kind"))
                or edge["kind"] not in RANK):
            raise ValueError("%s holds an edge this tool did not write: %r"
                             % (path, edge))
    for field, why in (("unread", "tell a 403 from a fix"),
                       ("items", "tell an item that left the sweep from a "
                                 "reference that was removed")):
        ids = body.get(field)
        if (not isinstance(ids, list)
                or not all(isinstance(iid, str) for iid in ids)):
            raise ValueError("%s has no list of %s item ids, so a diff could "
                             "not %s" % (path, field, why))
    return body


def scope_mismatch(graph, url, query, target):
    """The fields on which a graph and a sweep disagree about what was swept.

    Two graphs of different queries differ by every item one of them left
    out, and each of those would print as an edge removed. Refusing the pair
    is the only honest answer.
    """
    wanted = {"url": ("%s" % (url,)).rstrip("/"), "query": query,
              "target": target}
    held = {"url": ("%s" % (graph.get("url"),)).rstrip("/"),
            "query": graph.get("query"), "target": graph.get("target")}
    return [field for field in ("url", "query", "target")
            if wanted[field] != held[field]]


def diff_graphs(old, new):
    """(added, removed, unknown) edges between two graphs, as sorted tuples.

    An edge is (from, to, kind), so a reference whose class changed is one
    removed and one added. A changed edge is UNKNOWN, not added or removed,
    when its item could not be read in EITHER sweep, or when the item it
    names was not in both sweeps. A 403 this week is not a reference that
    somebody fixed, and an unread item still yields the edges its metadata
    holds, so an edge of it can look new. An app whose map was unshared from
    the sweeping account still loads that map; the edge is gone only because
    the map was not there to be named.
    """
    before = set((e["from"], e["to"], e["kind"]) for e in old["edges"])
    after = set((e["from"], e["to"], e["kind"]) for e in new["edges"])
    unread = set(old.get("unread") or ()) | set(new.get("unread") or ())
    held = [set(graph["items"]) | set([graph["target"]])
            for graph in (old, new)]

    def unsure(e):
        return e[0] in unread or not all(e[1] in ids for ids in held)

    changed = (after - before) | (before - after)
    added = sorted(e for e in after - before if not unsure(e))
    removed = sorted(e for e in before - after if not unsure(e))
    unknown = sorted(e for e in changed if unsure(e))
    return added, removed, unknown


def describe_diff(old, new, added, removed, unknown):
    """Render a graph diff as the lines the command line prints."""
    out = ["compared with the graph taken %s" % old.get("taken")]
    for sign, rows in (("+", added), ("-", removed), ("?", unknown)):
        for src, dst, kind in rows:
            out.append("%s %-12s %s -> %s" % (sign, LABEL[kind], src, dst))
    out.append("%d edge(s) added, %d removed, %d unknown"
               % (len(added), len(removed), len(unknown)))
    if unknown:
        out.append("an unknown edge belongs to an item one of the two sweeps "
                   "could not read, or names an item one of them did not "
                   "hold, so it is neither added nor removed")
    for graph, name in ((old, "previous"), (new, "current")):
        if not graph.get("complete"):
            out.append("the %s graph is NOT complete, so an edge missing from "
                       "it is not proof that the reference is gone" % name)
    return out


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

    Control characters become ? first, whatever the encoding. A title is
    typed by whoever owns the item, and ESC [2K with a carriage return in one
    rewrote the line above it on the operator's screen.
    """
    text = CONTROL_RE.sub("?", text)
    if not encoding:
        return text
    try:
        text.encode(encoding)
    except UnicodeEncodeError:
        return text.encode(encoding, "replace").decode(encoding, "replace")
    except LookupError:
        return text
    return text


def complain(text):
    """Print one error line to stderr, through printable() like stdout.

    An error can quote a portal's own message or a path from a graph file,
    so it gets the same treatment as a title.
    """
    print(printable("error: %s" % text, getattr(sys.stderr, "encoding", None)),
          file=sys.stderr)


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
    are searched as text by the caller, which is why this never raises a
    ValueError. JSON nested deeper than the parser can recurse raises
    RecursionError, and the reader turns that into an unread item.
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
                  max_resources=MAX_RESOURCES, max_body=MAX_BODY_BYTES):
    """Build the reader inspect() drives: (item id, size) -> (payloads, problem).

    Two endpoints per item, because the dependency graph reads neither.
    /data holds the web map, the dashboard and the notebook. /resources holds
    the Experience Builder draft and the StoryMap draft, which is where an id
    hides while the published app still points somewhere else.

    A problem is returned, not raised. The caller records it against this item
    and reads the next one, so one 403 costs one item instead of the sweep.

    A part that was read only in part is a problem too. A body over max_body
    is searched to the cap, and the item is unread, because an id past the
    cap was never looked at. An item with more JSON resources than
    max_resources is read to that many and is unread for the same reason.
    """
    params = {"token": token} if token else {}

    def fetch(source, path, payloads, problems):
        try:
            raw = _open(url, path, params, insecure, secret=token,
                        limit=max_body + 1)
        except RuntimeError as exc:
            problems.append("%s: %s" % (source, exc))
            return
        if len(raw) > max_body:
            raw = raw[:max_body]
            problems.append("%s: over the %d byte limit, so only the first %d "
                            "bytes were searched" % (source, max_body,
                                                     max_body))
        try:
            body = decode_body(raw)
        except RecursionError:
            # JSON nested deeper than the parser can follow. It is searched
            # as text, so an id in it is still found, and the item is unread,
            # because a path this tool could not walk cannot be classified.
            problems.append("%s: nested too deep to parse as JSON, so it was "
                            "searched as text" % source)
            payloads.append((source, raw.decode("utf-8", "replace")))
            return
        problem = portal_error(body)
        if problem:
            problems.append("%s: %s" % (source, problem))
        elif body is not None:
            payloads.append((source, body))

    def listed(iid):
        """Every row of the resource listing, over as many pages as it has.

        One page of max_resources rows used to be all that was asked for, and
        a StoryMap's photographs filled it before its draft was listed.
        """
        rows = []
        start = 1
        while start is not None:
            page = _call(url, "content/items/%s/resources" % iid,
                         dict(params, start=start, num=max_resources),
                         insecure=insecure, secret=token)
            got = page.get("resources") or []
            rows.extend(got)
            start = next_start(page, start, len(got))
        return rows

    def read(iid, size=0):
        payloads = []
        problems = []
        if max_size and int(size or 0) > max_size:
            return payloads, ("%d byte item was not fetched, over the %d byte "
                              "limit, so its configuration was never searched"
                              % (int(size), max_size))
        fetch("data", "content/items/%s/data" % iid, payloads, problems)
        try:
            rows = listed(iid)
        except (RuntimeError, ValueError) as exc:
            problems.append("resources: %s" % exc)
        else:
            names = resource_names({"resources": rows}, len(rows))
            if len(names) > max_resources:
                problems.append("resources: %d JSON resources, and only the "
                                "first %d were read" % (len(names),
                                                        max_resources))
            for name in names[:max_resources]:
                fetch("resources/%s" % name, "content/items/%s/resources/%s"
                      % (iid, urllib.parse.quote(name)), payloads, problems)
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


def item_url(url, token, target, insecure=False):
    """The target item's own url, read once from the portal, or None.

    This is the only url this tool reads that is not a portal endpoint, and it
    is read, never opened. Resolving a service url found inside other items by
    fetching it would let any item author make this tool call a server of
    their choosing with the operator's token, so that is never done.
    """
    params = {"token": token} if token else {}
    body = _call(url, "content/items/%s" % target, params, insecure=insecure,
                 secret=token)
    return body.get("url")


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
    DOTTED = "\u0130" * 40
    check(len(DOTTED.lower()) == 80 and len(lower(DOTTED + "AB")) == 42
          and lower("AB") == "ab",
          "a dotted capital I is two characters once str.lower() is done with "
          "it, so the matcher lowers A to Z only and every offset stays put")
    dotted = scan_item(TARGET, {"description": DOTTED + " " + TARGET}, [])
    check([hit["kind"] for hit in dotted] == [MENTION]
          and dotted[0]["excerpt"].endswith(TARGET),
          "forty of them before the id in a description are a mention with "
          "the id in its excerpt, not an IndexError that ended the sweep  "
          "<-- pinned defect")
    thumb_after = ("# %s\nlyr = gis.content.get('%s')  %s"
                   % (DOTTED, TARGET, THUMB.replace(TARGET, OTHER)))
    check([hit["kind"] for hit in scan_payload(
              TARGET, {"cells": [{"cell_type": "code", "source": thumb_after}]},
              "data")] == [HARD],
          "and a code cell that loads the target next to another item's "
          "thumbnail is still a hard break, not the thumbnail the shifted "
          "offset used to land on  <-- pinned defect")

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
    check(is_thumbnail_context(
          ("card",), "https://org.example/sharing/rest/content/items/%s/"
          "resources/thumbnail/cover.png" % TARGET) is True,
          "and a url through any /thumbnail/ folder is a picture as well")
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
    check(classify(("filters", "dataSource", "itemId"), TARGET) == HARD,
          "and the innermost key wins upwards too: the data source a filter "
          "widget loads is a hard break, not a filter")
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
    check([text for _path, text in walk({"b": ["x", {"c": "y"}], "a": "z"})]
          == ["b", "x", "c", "y", "a", "z"],
          "the walk yields in document order, each key before its value, so "
          "the excerpt a fold keeps is the first one in the file")
    nested = {}
    inner = nested
    for _level in range(3000):
        inner["layers"] = {}
        inner = inner["layers"]
    inner["itemId"] = TARGET
    check([hit["kind"] for hit in scan_payload(TARGET, nested, "data")]
          == [HARD],
          "a config nested 3,000 deep is walked to the bottom and its id is a "
          "hard break, not a RecursionError that ended the whole sweep  "
          "<-- pinned defect")

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
    check(scrub("data?access_token=SECRET123") == "data?access_token=%s"
          % REDACTED,
          "access_token is a token too, although an underscore is a word "
          "character and \\b never matched after it  <-- pinned defect")
    check(scrub("mytoken=abc") == "mytoken=abc",
          "and a key that merely ends in token after a letter is left alone")
    for form in ('token="S3CR3T"', "token='S3CR3T'", 'password = "S3CR3T"',
                 '"token": "S3CR3T"', 'GIS(api_key="S3CR3T")'):
        check("S3CR3T" not in scrub(form) and REDACTED in scrub(form),
              "a secret written as %s is redacted too, not only one written "
              "as key=value  <-- pinned defect" % form)
    check(scrub('password = "S3CR3T two words"') == 'password = %s' % REDACTED,
          "a quoted value is redacted to its closing quote, so the words "
          "after a space in it do not survive")
    check(scrub('{"tokenExpiration": 60, "token": {"a": 1}}')
          == '{"tokenExpiration": 60, "token": {"a": 1}}',
          "and a key that only starts with token, or a token key holding an "
          "object, is left alone")
    kwarg = scan_payload(TARGET, {"cells": [{"cell_type": "code", "source": [
        'gis = GIS(api_key="S3CR3T"); lyr = gis.content.get("%s")' % TARGET]}]},
        "data")
    check(len(kwarg) == 1 and "S3CR3T" not in kwarg[0]["excerpt"]
          and "content.get" in kwarg[0]["excerpt"],
          "so a notebook cell that signs in with a keyword argument next to "
          "the id prints the call and not the key  <-- pinned defect")
    far = ("https://svc.example.org/arcgis/rest/services/x/FeatureServer?token="
           "SECRETSECRETSECRETSECRETSECRETSECRETSECRET&itemId=%s" % TARGET)
    far_excerpt = scan_payload(TARGET, {"layers": [{"url": far}]},
                               "data")[0]["excerpt"]
    check("SECRETSECRET" not in far_excerpt and REDACTED in far_excerpt,
          "a token whose key sits further before the id than the excerpt "
          "reaches is still redacted, because the text is scrubbed before it "
          "is cut  <-- pinned defect")
    wide_secret = ("x" * 2000 + " token=" + "s" * 1000 + "&id=" + TARGET
                   + "&token=" + "t" * 3000)
    cut = excerpt(wide_secret, wide_secret.index(TARGET))
    check(cut.startswith("...") and cut.endswith("...") and TARGET in cut
          and "sss" not in cut and "ttt" not in cut,
          "a long value on either side is redacted and the excerpt still says "
          "it was cut at both ends")
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

    # ---- the target's own service url, matched as text and never opened
    SVC_NEEDLE = "services.example.com/abc/arcgis/rest/services/permits/featureserver"
    check(url_needle("HTTPS://Services.Example.com/ABC/arcgis/rest/services/"
                     "Permits/FeatureServer/?f=json#top", TARGET)
          == (SVC_NEEDLE, None),
          "a service url loses its scheme, query string, fragment and trailing "
          "slash and is lowered, because configs write the same service in "
          "all those ways  <-- pinned defect")
    check(url_needle("//services.example.com/ABC/arcgis/rest/services/Permits/"
                     "FeatureServer")[0] == SVC_NEEDLE,
          "a scheme-relative url is read the same")
    check(url_needle(None) == (None, "the target item has no url"),
          "an item with no url has no needle, and says why")
    check(url_needle("   ")[0] is None, "and neither does a url of whitespace")
    check(url_needle("https://svc:Hunter2@Services.Example.com/ABC/arcgis/"
                     "rest/services/Permits/FeatureServer")[0] == SVC_NEEDLE,
          "a url carrying a user and password is searched for without them, "
          "because the needle is printed and a config names the service "
          "without them  <-- pinned defect")
    check(url_needle("https://svc:p@ss@Services.Example.com/ABC/arcgis/rest/"
                     "services/Permits/FeatureServer")[0] == SVC_NEEDLE,
          "and a password with an @ in it comes off whole, up to the LAST @")
    check(url_needle("https://Services.Example.com/ABC/arcgis/rest/services/"
                     "Permits/FeatureServer#layer0")[0] == SVC_NEEDLE,
          "a fragment with no query string before it comes off too")
    for app in ("https://org.example/apps/webappviewer/index.html?id=%s",
                "https://org.example/apps/instant/sidebar/index.html?appid=%s"):
        app_needle = url_needle(app % TARGET.upper(), TARGET)
        check(app_needle[0] is None and "item id" in app_needle[1],
              "an app whose url names it in the QUERY STRING is left to the id "
              "search, because without that query it is every app of the same "
              "template in the org  <-- pinned defect")
    check(scan_item(TARGET, {}, [("data", {"cards": [{"url": app % OTHER}]})],
                    url_needle(app % TARGET, TARGET)[0]) == [],
          "so a site linking to ANOTHER app of that template is no break")
    FOLDER = url_needle("https://maps.example.org/arcgis/rest/services/"
                        "Permits")[0]
    check(url_positions("https://maps.example.org/arcgis/rest/services/"
                        "Permits_old/FeatureServer", FOLDER) == []
          and url_positions("https://maps.example.org/arcgis/rest/services/"
                            "Permits-2024/FeatureServer", FOLDER) == [],
          "an underscore or a dash continues a service name, so Permits is not "
          "found inside Permits_old or Permits-2024")
    bare = url_needle("https://maps.example.org/", TARGET)
    check(bare[0] is None and "whole server" in bare[1],
          "a url that is a bare server is refused as a needle, because it would "
          "match every service on that server  <-- pinned defect")
    for root in ("https://maps.example.org/arcgis",
                 "https://maps.example.org/arcgis/rest/services/",
                 "https://services.example.com/ABC/arcgis/rest/services"):
        rooted = url_needle(root, TARGET)
        check(rooted[0] is None and "whole server" in rooted[1],
              "%s is refused the same way, because a web adaptor or a "
              "rest/services root matches every service under it  "
              "<-- pinned defect" % root)
    viewer = url_needle("https://www.arcgis.com/apps/mapviewer/index.html"
                        "?webmap=%s" % OTHER.upper(), TARGET)
    check(viewer[0] is None and OTHER in viewer[1],
          "a viewer url whose query string opens ANOTHER item is refused, "
          "because without that query it is every link through that viewer  "
          "<-- pinned defect")
    check(url_positions("https://h.example/arcgis/rest/services/P/"
                        "FeatureServer/0", url_needle(
                            "https://h.example/arcgis/rest/services/P/"
                            "FeatureServer\n", TARGET)[0]) == [8],
          "a target url that ends in a newline is trimmed before it becomes "
          "the needle, and still matches")
    carried = url_needle("https://org.example/sharing/rest/content/items/%s/"
                         "data" % TARGET, TARGET)
    check(carried[0] is None and "item id" in carried[1],
          "a url that carries the target id is left to the id search, so one "
          "occurrence is not counted twice  <-- pinned defect")
    LAYER1 = url_needle("https://maps.example.org/arcgis/rest/services/"
                        "Permits/FeatureServer/1")[0]
    check(url_positions("https://maps.example.org/arcgis/rest/services/Permits/"
                        "FeatureServer/1", LAYER1) == [8],
          "the target url matches itself")
    check(url_positions("http://MAPS.example.org/arcgis/rest/services/permits/"
                        "featureserver/1?token=x", LAYER1) == [7],
          "and matches over http, in capitals, with a query string after it")
    check(url_positions("https://maps.example.org/arcgis/rest/services/Permits/"
                        "FeatureServer/12", LAYER1) == [],
          "layer 1 does not match inside layer 12, because a digit continues "
          "the url  <-- pinned defect")
    check(url_positions("https://oldmaps.example.org/arcgis/rest/services/"
                        "Permits/FeatureServer/1", LAYER1) == [],
          "the same path on a host whose name ENDS in the target's host is not "
          "the target  <-- pinned defect")
    check(url_positions("https://gis.maps.example.org/arcgis/rest/services/"
                        "Permits/FeatureServer/1", LAYER1) == [],
          "and neither is the same path on a sub-host of it  <-- pinned defect")
    check(url_positions("https://services.example.com/ABC/arcgis/rest/services/"
                        "Permits/FeatureServer/0/query", SVC_NEEDLE) == [8],
          "a layer url under the target's service matches, because a slash "
          "ends the service  <-- pinned defect")
    check(url_positions("any text", None) == [], "no needle finds nothing")
    URL_MAP = {
        "operationalLayers": [{"title": "Permits", "url":
                               "https://services.example.com/ABC/arcgis/rest/"
                               "services/Permits/FeatureServer/0"}],
        "description": "copied from https://services.example.com/abc/arcgis/"
                       "rest/services/permits/featureserver last year",
    }
    url_hits = scan_payload(TARGET, URL_MAP, "data", SVC_NEEDLE)
    check(sorted(hit["kind"] for hit in url_hits) == [HARD, MENTION],
          "a layer that loads the target's SERVICE URL is a hard break, and the "
          "same url in prose is a mention, by the same path rules as an id  "
          "<-- pinned defect")
    check(any(hit["kind"] == HARD and "FeatureServer/0" in hit["excerpt"]
              for hit in url_hits), "and the excerpt shows the url it matched")
    check(scan_payload(TARGET, URL_MAP, "data") == [],
          "without the needle the same web map yields nothing, which is the gap "
          "the url search closes")
    check(scan_item(TARGET, {"url": "https://services.example.com/abc/arcgis/"
                                    "rest/services/Permits/FeatureServer"},
                    [], SVC_NEEDLE)[0]["kind"] == HARD,
          "a second item registered on the target's service url is a hard "
          "break, found in the search result alone")

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

    # ---- references between items, and the impact search over them
    W_MAP = "f1" + "0" * 30
    W_APP = "f2" + "0" * 30
    W_SITE = "f3" + "0" * 30
    W_NOTE = "f4" + "0" * 30
    W_DOC = "f5" + "0" * 30
    W_EMBED = "f6" + "0" * 30
    W_FILT = "f7" + "0" * 30
    known = dict((iid, iid) for iid in (TARGET, W_MAP, W_APP, W_DOC))
    check(item_refs({"id": W_MAP}, [], known, (W_MAP, TARGET)) == {},
          "an item's own id in its search result is not an edge to itself  "
          "<-- pinned defect")
    check(item_refs({}, [("data", {"layers": [{"itemId": TARGET}]})], known,
                    (W_MAP, TARGET)) == {},
          "the edge to the target is left to the findings, which also match "
          "the service url")
    check(item_refs({}, [("data", {"layers": [{"itemId": OTHER}]})], known)
          == {}, "an id that is not an item of this sweep is not an edge")
    check(item_refs({}, [("data", {"layers": [{"itemId": "bb%scc" % W_APP}]})],
                    known) == {},
          "a longer id that contains an item id is not an edge to it")
    check(item_refs({}, [("data", "bb%s cc%s" % (W_APP, W_DOC))], known) == {}
          and item_refs({}, [("data", "%sbb %scc" % (W_APP, W_DOC))], known)
          == {},
          "and neither is one with characters on its left only, or on its "
          "right only")
    check(item_refs({}, [("data", {"thumbnail": THUMB.replace(TARGET, W_APP)})],
                    known) == {},
          "another item's THUMBNAIL is no edge, exactly as it is no finding  "
          "<-- pinned defect")
    check(item_refs({"description": "replaces %s" % W_APP},
                    [("data", {"layers": [{"itemId": W_APP}]}), ("data", None)],
                    known) == {W_APP: HARD},
          "an item that loads another and names it in prose holds ONE edge to "
          "it, at the worse class, whichever it met first  <-- pinned defect")
    check(item_refs({"snippet": "see %s" % W_DOC,
                     "layers": [{"itemId": W_DOC}]}, [], known)
          == {W_DOC: HARD},
          "and the same holds when the hard reference comes second")
    check(item_refs({"layers": [{"itemId": W_DOC}],
                     "snippet": "see %s" % W_DOC}, [], known) == {W_DOC: HARD},
          "and when it comes first, so a later mention cannot downgrade it  "
          "<-- pinned defect")
    check(item_refs({}, [("data", {"cells": [{"cell_type": "code", "source":
                                              "# %s\nload('%s')"
                                              % (DOTTED, W_APP)}]})], known)
          == {W_APP: HARD},
          "an edge after forty dotted capital I is found at its own offset  "
          "<-- pinned defect")
    check(item_refs({}, [("data", "load('%s')" % W_DOC.upper())], known)
          == {W_DOC: MENTION},
          "an id in a body that is not json is a mention edge, found in upper "
          "case too")

    def edge(src, dst, kind):
        return {"from": src, "to": dst, "kind": kind}

    EDGE_ITEMS = catalog(
        {"id": TARGET, "title": "Permits", "type": "Feature Service"},
        {"id": W_MAP, "title": "Permit layers", "type": "Web Map"},
        {"id": W_APP, "title": "Permit viewer", "type": "Web Mapping "
         "Application"},
        {"id": W_NOTE, "title": "Permit notes", "type": "Web Map",
         "description": "an older copy of %s" % W_MAP})
    reads = []

    def counting_reader(iid, size=0):
        reads.append(iid)
        return reader_for({
            W_MAP: [("data", {"operationalLayers": [{"itemId": TARGET}]})],
            W_APP: [("data", {"values": {"webmap": W_MAP}})]})(iid, size)

    edged = inspect(TARGET, EDGE_ITEMS, counting_reader)
    check(edged.edges == [edge(W_MAP, TARGET, HARD), edge(W_APP, W_MAP, HARD),
                          edge(W_NOTE, W_MAP, MENTION)],
          "one read of each item records its reference to the target AND to "
          "every other item of the sweep")
    check(len(reads) == 3,
          "at no extra portal cost: three items searched, three reads")
    check(edged.names[W_APP] == {"title": "Permit viewer",
                                 "type": "Web Mapping Application"},
          "and the title of every item searched is kept for the report")
    both_ways = inspect(TARGET, catalog(
        {"id": W_MAP, "description": "replaces %s" % TARGET}, {"id": W_APP}),
        reader_for({W_MAP: [("data", {"operationalLayers": [
                                 {"itemId": TARGET}]})],
                    W_APP: [("data", {"values": {"webmap": W_MAP}})]}))
    check(transitive(both_ways, 2)[0] == [{"id": W_APP, "title": "",
                                           "type": "", "hop": 2,
                                           "via": W_MAP, "loads": W_MAP}],
          "a map that loads the target AND names it in its description holds "
          "a HARD edge to it, so the app that loads the map breaks at hop 2  "
          "<-- pinned defect")
    upper = inspect(TARGET, catalog({"id": W_MAP.upper()}, {"id": W_APP}),
                    reader_for({W_APP: [("data", {"values":
                                                  {"webmap": W_MAP}})]}))
    check(upper.edges == [edge(W_APP, W_MAP.upper(), HARD)],
          "an item the search returned in upper case is still the item "
          "another loads by its lower case id, and the edge names it as the "
          "sweep holds it  <-- pinned defect")

    E = [edge(W_MAP, TARGET, HARD), edge(W_APP, W_MAP, HARD),
         edge(W_SITE, W_APP, HARD), edge(W_APP, W_SITE, HARD),
         edge(W_NOTE, W_MAP, MENTION), edge(W_DOC, TARGET, MENTION),
         edge(W_EMBED, W_DOC, HARD), edge(W_FILT, TARGET, SOFT),
         edge(W_FILT, W_MAP, HARD), edge(TARGET, W_MAP, HARD)]
    reached, beyond = impact(E, TARGET, 5)
    check(sorted(reached) == sorted([W_MAP, W_APP, W_SITE, W_FILT]),
          "the impact search follows HARD edges only")
    check(reached[W_APP] == (2, W_MAP, W_MAP)
          and reached[W_SITE] == (3, W_MAP, W_APP),
          "and gives each item its hop count, the first-hop item the break "
          "travels through, and the item it loads")
    check(reached[W_FILT][0] == 2,
          "an item that only FILTERS on the target still breaks at hop 2, "
          "because it also loads the map that loads the target  "
          "<-- pinned defect")
    naive, _beyond = impact([dict(one, kind=HARD) for one in E], TARGET, 5)
    check(W_EMBED in naive and W_NOTE in naive and W_DOC in naive,
          "a search that follows EVERY edge reports the dashboard embedding a "
          "story that only names the target, and the map that only mentions a "
          "broken one")
    check(W_EMBED not in reached and W_NOTE not in reached
          and W_DOC not in reached,
          "and along hard edges neither of them breaks, which is the wrong "
          "answer that search gives  <-- pinned defect")
    check(TARGET not in reached,
          "the target loading its own map is a cycle, not a break of the "
          "target  <-- pinned defect")
    check(beyond == 0,
          "a cycle between two broken items ends the search instead of "
          "looping, with nothing counted beyond it  <-- pinned defect")
    check(impact(E, TARGET, 1) == ({W_MAP: (1, W_MAP, TARGET)}, 2),
          "depth 1 is the direct hard breaks, and it counts the two items that "
          "load one of them")
    check(impact(E, TARGET, 2)[1] == 1,
          "depth 2 counts the one item that loads a hop 2 item, so a short "
          "depth says it was short  <-- pinned defect")
    raises(lambda: impact(E, TARGET, 0), "a depth of zero raises")
    A1, A2, X1 = "a1" + "0" * 30, "a2" + "0" * 30, "e1" + "0" * 30
    check(impact([edge(A2, TARGET, HARD), edge(A1, TARGET, HARD),
                  edge(X1, A2, HARD), edge(X1, A1, HARD)], TARGET, 2)[0][X1]
          == (2, A1, A1),
          "an item two broken items both load is reached through the lower id, "
          "so two runs print the same chain  <-- pinned defect")
    check(impact([edge(A2, TARGET, HARD), edge(A1, TARGET, HARD),
                  edge(X1, A2, HARD), edge(X1, A1, HARD)], TARGET, 1)[1] == 1,
          "and at depth 1 it is ONE item beyond the depth, not one per broken "
          "item it loads  <-- pinned defect")
    B0, Z9, X3 = "b0" + "0" * 30, "c9" + "0" * 30, "e3" + "0" * 30
    check(impact([edge(A1, TARGET, HARD), edge(A2, TARGET, HARD),
                  edge(Z9, A1, HARD), edge(B0, A2, HARD),
                  edge(X3, Z9, HARD), edge(X3, B0, HARD)], TARGET, 3)[0][X3]
          == (3, A2, B0),
          "and so at every hop: each level is walked in id order, not in the "
          "order the level before it found them")

    def finding(iid, kind):
        return {"id": iid, "kind": kind, "source": "data", "path": "p",
                "count": 1, "excerpt": "", "title": "", "type": "", "owner": ""}

    NAMES = dict((iid, {"title": "t" + iid[:2], "type": "k"})
                 for iid in (W_MAP, W_APP, W_SITE, W_NOTE, W_DOC, W_EMBED,
                             W_FILT))
    chain = Report(TARGET, [finding(W_MAP, HARD), finding(W_DOC, MENTION),
                            finding(W_FILT, SOFT)], 7, [], [], E, NAMES)
    rows, beyond = transitive(chain, 3)
    check([row["id"] for row in rows] == [W_APP, W_FILT, W_SITE],
          "the transitive rows are hop 2 first, then by id, and leave out the "
          "first hop the report already lists")
    deep = "\n".join(describe(chain, 3))
    check("3 more item(s) break through a hard break, to depth 3" in deep
          and "HOP 3        %s" % W_SITE in deep
          and "loads %s (tf2), first hop %s" % (W_APP, W_MAP) in deep,
          "the console names each one with its hop, what it loads and the "
          "first hop")
    check("HOP" not in "\n".join(describe(chain)),
          "the default depth of 1 prints no transitive section, so the report "
          "reads exactly as it did before  <-- pinned defect")
    check("1 more item(s) load one at hop 3. Raise --depth"
          in "\n".join(describe(chain, 2)),
          "a depth that stops short says how many it left behind")
    check("nothing breaks beyond the first hop, to depth 2"
          in "\n".join(describe(Report(TARGET, [], 1, [], []), 2)),
          "and a depth with nothing past the first hop says that too")
    gappy = Report(TARGET, [finding(W_MAP, HARD)], 7,
                   [{"id": W_EMBED, "title": "", "problem": "403"}], [], E,
                   NAMES)
    gap_text = "\n".join(describe(gappy, 3))
    check("1 unread item(s) may load any of these at any hop" in gap_text
          and "Do not read this run" in gap_text and exit_code(gappy) == 2,
          "transitive mode inherits the unread limit at EVERY hop: an unread "
          "item could load any broken item, so the run still exits 2  "
          "<-- pinned defect")
    chain_doc = build_document(PORTAL, "q", TARGET, "t", chain, 3, "svc")
    check(chain_doc["depth"] == 3 and chain_doc["beyond"] == 0
          and [row["hop"] for row in chain_doc["transitive"]] == [2, 2, 3],
          "the report file carries the transitive rows with their hops")
    check(chain_doc["transitive"][0] == {
              "id": W_APP, "title": "tf2", "type": "k", "hop": 2,
              "via": W_MAP, "loads": W_MAP},
          "and each row names the first hop and the item it loads")
    check("transitive" not in build_document(PORTAL, "q", TARGET, "t", chain)
          and chain_doc["service_url"] == "svc",
          "a depth 1 file has no transitive key, and every file names the "
          "service url that was searched for")

    # ---- the snapshot, and the diff between two of them
    old_graph = build_graph(PORTAL, "orgid:ORG", TARGET, "t1", Report(
        TARGET, [], 5, [], [], [edge(W_MAP, TARGET, HARD),
                                edge(W_APP, W_MAP, HARD),
                                edge(W_NOTE, W_MAP, MENTION)], NAMES))
    new_graph = build_graph(PORTAL, "orgid:ORG", TARGET, "t2", Report(
        TARGET, [], 5, [], [], [edge(W_SITE, W_APP, HARD),
                                edge(W_MAP, TARGET, HARD),
                                edge(W_APP, W_MAP, SOFT)], NAMES))
    check([one["from"] for one in new_graph["edges"]] == [W_MAP, W_APP, W_SITE]
          and new_graph["complete"] is True and new_graph["unread"] == [],
          "a graph holds its edges in order, with its completeness flags")
    check(new_graph["items"] == sorted(NAMES),
          "and the id of every item the sweep searched")
    added, removed, unknown = diff_graphs(old_graph, new_graph)
    check(added == [(W_APP, W_MAP, SOFT), (W_SITE, W_APP, HARD)],
          "the diff lists the edges added")
    check(removed == [(W_APP, W_MAP, HARD), (W_NOTE, W_MAP, MENTION)],
          "and the edges removed, so a class that changed is one of each")
    check(diff_graphs(old_graph, old_graph) == ([], [], []),
          "a graph compared with itself changes nothing")
    gap_graph = build_graph(PORTAL, "orgid:ORG", TARGET, "t3", Report(
        TARGET, [], 5, [{"id": W_APP, "title": "", "problem": "403"}], [],
        [edge(W_MAP, TARGET, HARD)], NAMES))
    check(gap_graph["complete"] is False and gap_graph["unread"] == [W_APP],
          "a graph from a sweep with an unread item says so, by id")
    check(diff_graphs(old_graph, gap_graph)
          == ([], [(W_NOTE, W_MAP, MENTION)], [(W_APP, W_MAP, HARD)]),
          "an edge whose item could not be READ this time is unknown, not "
          "removed, because a 403 is not a fix  <-- pinned defect")
    check(diff_graphs(gap_graph, old_graph)
          == ([(W_NOTE, W_MAP, MENTION)], [], [(W_APP, W_MAP, HARD)]),
          "and an edge whose item was unread LAST time is unknown, not added")
    metadata_only = build_graph(PORTAL, "orgid:ORG", TARGET, "t4", Report(
        TARGET, [], 5, [{"id": W_NOTE, "title": "", "problem": "403"}], [],
        [edge(W_MAP, TARGET, HARD), edge(W_APP, W_MAP, HARD),
         edge(W_NOTE, W_APP, MENTION)], NAMES))
    check(diff_graphs(old_graph, metadata_only)
          == ([], [], sorted([(W_NOTE, W_MAP, MENTION),
                              (W_NOTE, W_APP, MENTION)])),
          "an edge of an item the NEW sweep could not read is unknown even "
          "when it looks ADDED, because the metadata of an unread item is "
          "still searched and yields edges  <-- pinned defect")
    left = dict(NAMES)
    del left[W_MAP]
    gone_graph = build_graph(PORTAL, "orgid:ORG", TARGET, "t5", Report(
        TARGET, [], 4, [], [], [], left))
    check(diff_graphs(old_graph, gone_graph)
          == ([], [(W_MAP, TARGET, HARD)],
              [(W_APP, W_MAP, HARD), (W_NOTE, W_MAP, MENTION)]),
          "a map that left the sweep takes its own edge with it, and the edges "
          "TO it are unknown, not removed, because the items that load it did "
          "not change  <-- pinned defect")
    check(diff_graphs(gone_graph, old_graph)
          == ([(W_MAP, TARGET, HARD)], [],
              [(W_APP, W_MAP, HARD), (W_NOTE, W_MAP, MENTION)]),
          "and when it comes back its own edge is added and the edges to it "
          "are unknown")
    check("or names an item one of them did not hold" in "\n".join(
              describe_diff(old_graph, gone_graph,
                            *diff_graphs(old_graph, gone_graph))),
          "and the printed diff says why an edge is unknown")
    diff_text = "\n".join(describe_diff(old_graph, gap_graph,
                                        *diff_graphs(old_graph, gap_graph)))
    check("- MENTION ONLY %s -> %s" % (W_NOTE, W_MAP) in diff_text
          and "? HARD BREAK   %s -> %s" % (W_APP, W_MAP) in diff_text
          and "0 edge(s) added, 1 removed, 1 unknown" in diff_text,
          "the printed diff marks each edge added, removed or unknown")
    check("neither added nor removed" in diff_text
          and "the current graph is NOT complete" in diff_text
          and "previous graph is NOT" not in diff_text,
          "and says which of the two graphs was incomplete")
    check("+ SOFT BREAK   %s -> %s" % (W_APP, W_MAP) in "\n".join(
              describe_diff(old_graph, new_graph, added, removed, unknown)),
          "an added edge prints with a plus")
    check(scope_mismatch(old_graph, PORTAL + "/", "orgid:ORG", TARGET) == [],
          "a trailing slash on the url is the same portal")
    check(scope_mismatch(dict(old_graph, url=PORTAL + "/"), PORTAL,
                         "orgid:ORG", TARGET) == [],
          "and so is a trailing slash on the url the graph recorded")
    check(scope_mismatch(old_graph, PORTAL, "type:Web Map", OTHER)
          == ["query", "target"],
          "a graph of another query or another target is named as a mismatch, "
          "because every item one sweep left out would read as removed  "
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
    for bad_id, shape in ((12345, "number"), (["x"], "list")):
        raises(lambda: crawl(pages([{"id": bad_id}]), "q"),
               "a search result whose id is a %s raises the same ValueError, "
               "not a TypeError traceback, which exits 1  <-- pinned defect"
               % shape)
    raises(lambda: crawl(pages(["notadict"]), "q"),
           "and so does a result that is not an object at all, as the same "
           "ValueError rather than an AttributeError traceback  "
           "<-- pinned defect")
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
    check(resource_names({"resources": [{"resource": "DRAFT.JSON"}]})
          == ["DRAFT.JSON"],
          "a JSON resource named in capitals is still read")
    check(printable("plain", "cp1252") == "plain",
          "a line the console can encode is printed as it is")
    check(printable("caf\u00e9 \u2764", "ascii") != "caf\u00e9 \u2764",
          "a line it cannot encode is replaced rather than crashing the run  "
          "<-- pinned defect")
    check(printable("caf\u00e9", None) == "caf\u00e9",
          "a stream with no encoding is left alone")
    check(printable("caf\u00e9", "not-a-codec") == "caf\u00e9",
          "and so is one whose encoding python has never heard of")
    hostile = "x\r\x1b[2K\x1b[1A\nno item references it\x9b"
    check(printable(hostile, "utf-8") == "x??[2K?[1A?no item references it?"
          and printable(hostile, None) == printable(hostile, "utf-8"),
          "control characters in a title become ?, whatever the console's "
          "encoding, so an item owner cannot rewrite the lines above it on "
          "the operator's screen  <-- pinned defect")
    _none, complained = captured(lambda: complain("portal said \x1b[2Kok"))
    check(complained == "error: portal said ?[2Kok\n",
          "and an error message goes through the same filter, because it can "
          "quote the portal's own words")
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
    PHOTO_ID = "9f" + "0" * 30
    DEEP_ID = "9c" + "0" * 30
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
        # A title no Windows code page holds, for the console test.
        row(DASH_ID, "Permit board \u8a31\u53ef", "Dashboard", owner="bob"),
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
    # A second organization, reached under /wide, for the transitive search
    # and the service url. Its own catalogue, so that every count asserted
    # against the first one above still holds.
    WIDE = [
        row(TARGET, "Permits", "Feature Service", owner="gis_admin"),
        row(W_MAP, "Permit layers", "Web Map"),
        row(W_APP, "Permit viewer", "Web Mapping Application"),
        row(W_SITE, "Permit site", "Hub Site Application"),
        row(W_NOTE, "Permit notes", "Web Map",
            description="an older copy of %s" % W_MAP),
        row(W_DOC, "Permit handbook", "StoryMap",
            description="explains %s" % TARGET),
        row(W_EMBED, "Handbook board", "Dashboard"),
        row(W_FILT, "Permit filter board", "Dashboard"),
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
        # Deeper than json.loads can recurse on any python this runs on.
        DEEP_ID: "[" * 100000 + '"%s"' % TARGET + "]" * 100000,
        W_APP: {"values": {"webmap": W_MAP}},
        W_SITE: {"widgets": [{"type": "app", "itemId": W_APP}]},
        W_EMBED: {"widgets": [{"type": "embed", "itemId": W_DOC}]},
        W_FILT: {"widgets": [{"type": "mapWidget", "itemId": W_MAP}],
                 "datasets": [{"filter": "PERMIT_SRC = '%s'" % TARGET}]},
        # W_MAP is added once the server is listening, because its layers
        # point back at that server, which is how a fetch would be seen.
    }
    RESOURCES = {
        EXB_ID: {"config.json": EXB, "cover.jpg": "not json"},
        STORY_ID: {"draft_x.json": STORY_DRAFT},
        PART_ID: {"gone.json": "BOOM", "bad.json": {"error": {
            "code": 403, "message": "Item resource does not exist or is "
            "inaccessible."}}, "empty.json": "", "text.json": "just text"},
    }
    # Sixty photographs, listed before the draft, which is JSON.
    RESOURCES[PHOTO_ID] = dict(("img%02d.jpg" % n, "not json")
                               for n in range(60))
    RESOURCES[PHOTO_ID]["story_draft.json"] = STORY_DRAFT

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
            # /wide serves the second catalogue, and /noinfo refuses to show
            # the target item itself, as a portal does to an anonymous caller
            # when the target is not public.
            variant = ""
            for prefix in ("/blind", "/wide", "/noinfo", "/badid"):
                if path.startswith(prefix + "/"):
                    variant, path = prefix, path[len(prefix):]
            blind = variant == "/blind"
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
            if route == "search" and variant == "/badid":
                # One row whose id is a number, as a broken proxy or a
                # hand-rolled portal sends it.
                return self._json({"total": 1, "nextStart": -1,
                                   "results": [{"id": 12345}]})
            if route == "search":
                return self._search(params, WIDE if variant == "/wide"
                                    else CATALOG)
            if route.startswith("content/items/"):
                return self._item(route[len("content/items/"):], variant,
                                  params)
            return self._json({"error": {"code": 400,
                                         "message": "unhandled " + route}})

        def _search(self, params, catalog_rows):
            query = params.get("q") or ""
            start = int(params.get("start") or 1)
            num = min(int(params.get("num") or 10), SEARCH_MAX_NUM)
            rows = [item for item in catalog_rows if self._match(item, query)]
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

        def _item(self, rest, variant, params):
            iid, _slash, tail = rest.partition("/")
            if iid == PRIV_ID or (not tail and variant == "/noinfo"):
                # HTTP 200 carrying an error object, which is how a portal
                # says no. The status code proves nothing.
                return self._json({"error": {"code": 403, "message":
                                   "You do not have permissions to access "
                                   "this resource or perform this operation."}})
            if not tail:
                # The item itself, which is where the target's service url is
                # read from. Its url points back at this server, so a tool
                # that opened it would show up in server.seen.
                return self._json({"id": iid, "type": "Feature Service",
                                   "url": self.server.service_url})
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
                # Paged as the portal pages it: start and num honoured, and
                # nextStart -1 on the last page.
                names = sorted(RESOURCES.get(iid, {}))
                start = int(params.get("start") or 1)
                window = names[start - 1:start - 1 + int(params.get("num")
                                                         or 10)]
                nxt = start + len(window)
                return self._json({"total": len(names), "start": start,
                                   "num": len(window),
                                   "nextStart": -1 if nxt > len(names)
                                   else nxt,
                                   "resources": [{"resource": name,
                                                  "created": 1, "size": 9}
                                                 for name in window]})
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
    server.service_url = None
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    workdir = tempfile.mkdtemp(prefix="whobreaks-selftest-")
    try:
        LIVE = "http://127.0.0.1:%d" % server.server_address[1]
        server.service_url = LIVE + "/arcgis/rest/services/Permits/FeatureServer"
        DATA[W_MAP] = {"operationalLayers": [
            {"title": "Permits", "url": server.service_url + "/0"},
            {"title": "Elsewhere",
             "url": LIVE + "/trap/rest/services/Other/FeatureServer/0"}]}
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
        payloads, problem = read(DEEP_ID, 20)
        check("nested too deep" in (problem or "")
              and [source for source, _body in payloads] == ["data"]
              and isinstance(payloads[0][1], str),
              "a body nested too deep for json.loads is searched as text and "
              "its item is unread, not a RecursionError that ended the sweep  "
              "<-- pinned defect")
        deep_report = inspect(TARGET, catalog({"id": DEEP_ID},
                                              {"id": DASH_ID}), read)
        check(deep_report.kind_of(DASH_ID) == HARD
              and deep_report.kind_of(DEEP_ID) == MENTION
              and [one["id"] for one in deep_report.unread] == [DEEP_ID]
              and exit_code(deep_report) == 2,
              "so the real hard break in another item of the same sweep is "
              "still reported, and the run exits 2  <-- pinned defect")
        before = len(server.seen)
        anon = portal_reader(LIVE, None)
        anon(MAP_ID, 20)
        check(not any("token=" in path for path in server.seen[before:]),
              "an anonymous read sends no token parameter at all  "
              "<-- pinned defect")
        before = len(server.seen)
        payloads, problem = read(PHOTO_ID, 20)
        check(problem is None and [source for source, _body in payloads]
              == ["resources/story_draft.json"],
              "a StoryMap whose sixty photographs fill the first page of its "
              "resource listing still has its draft read, because the listing "
              "is paged to its end  <-- pinned defect")
        check(len([path for path in server.seen[before:] if path.startswith(
                  "/sharing/rest/content/items/%s/resources?" % PHOTO_ID)])
              == 2, "sixty-one rows are two pages of fifty, two calls")
        payloads, problem = portal_reader(LIVE, LIVE_TOKEN,
                                          max_body=30)(STORY_ID, 20)
        check([source for source, _body in payloads]
              == ["data", "resources/draft_x.json"]
              and problem == "resources/draft_x.json: over the 30 byte limit, "
              "so only the first 30 bytes were searched",
              "a RESOURCE over the byte cap makes its item unread too, and the "
              "/data under the cap is no problem")
        before = len(server.seen)
        payloads, problem = portal_reader(LIVE, LIVE_TOKEN,
                                          max_resources=2)(PART_ID, 20)
        check(len([path for path in server.seen[before:]
                   if "/%s/resources/" % PART_ID in path]) == 2
              and "4 JSON resources, and only the first 2 were read" in problem,
              "an item with more JSON resources than the limit reads that many "
              "and is UNREAD, not clean, because the rest were never searched  "
              "<-- pinned defect")
        check("JSON resources" not in (portal_reader(
                  LIVE, LIVE_TOKEN, max_resources=4)(PART_ID, 20)[1] or ""),
              "and one with exactly the limit is read whole, with no problem "
              "about the limit")
        dash_size = len(json.dumps(DASHBOARD))
        payloads, problem = portal_reader(LIVE, LIVE_TOKEN,
                                          max_body=dash_size // 2)(DASH_ID, 20)
        check(payloads[0][0] == "data" and isinstance(payloads[0][1], str)
              and len(payloads[0][1]) == dash_size // 2
              and "data: over the %d byte limit" % (dash_size // 2) in problem,
              "a /data body over the byte cap is searched to the cap and the "
              "item is UNREAD, because the target id can sit past the cap  "
              "<-- pinned defect")
        check(portal_reader(LIVE, LIVE_TOKEN,
                            max_body=dash_size)(DASH_ID, 20)[1] is None,
              "and a body exactly at the cap is whole, and no problem")
        sized = portal_reader(LIVE, LIVE_TOKEN, max_size=20)
        check(sized(EXB_ID, 20)[1] is None and "never searched" in
              sized(EXB_ID, 21)[1],
              "an item exactly at the size limit is fetched, and one byte over "
              "it is not")

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
            ["--url", LIVE + "/badid", "--target", TARGET, "--query", "q"]))
        check(code == 2 and "has no id: {'id': 12345}" in seen
              and "Traceback" not in seen,
              "a search result whose id is a number ends the run on exit 2 "
              "and a message, not on a traceback's exit 1, which reads as "
              "\"something references it\"  <-- pinned defect")
        code, seen = captured(lambda: main(
            ["--url", LIVE + "/blind", "--target", TARGET]))
        check(code == 2 and "could not read the org id" in seen,
              "a portal that will not name its org asks for --query rather "
              "than sweeping the wrong thing  <-- pinned defect")

        # the service url, the transitive search and the graph, end to end
        WIDE_URL = LIVE + "/wide"
        check(item_url(LIVE, LIVE_TOKEN, TARGET) == server.service_url,
              "the target's own url is read from its item, over the wire")
        before = len(server.seen)
        code, seen = captured(lambda: main(
            ["--url", WIDE_URL, "--target", TARGET, "--token", LIVE_TOKEN,
             "--depth", "3"]))
        wide_seen = seen
        check(code == 1 and "3 item(s) reference %s" % TARGET in seen,
              "a sweep of the second org exits 1 with three direct references")
        check("service url: 127.0.0.1:%d/arcgis/rest/services/permits/"
              "featureserver" % server.server_address[1] in seen,
              "and says which service url it searched for")
        check(any(line.startswith("HARD BREAK") and W_MAP in line
                  for line in seen.splitlines())
              and "operationalLayers/url" in seen,
              "the web map that loads the target only by its SERVICE URL is a "
              "hard break, over the wire  <-- pinned defect")
        check("1 hard, 1 soft, 1 mention only, over 7 item(s) searched" in seen,
              "and the tail counts one of each class")
        asked = [path for path in server.seen[before:]]
        check(asked and not any(path.startswith(("/arcgis/", "/trap/"))
                                for path in asked),
              "neither the target's service url nor the other url inside the "
              "web map was ever requested, although both point at this very "
              "server  <-- pinned defect")
        check(sum(1 for path in asked if path.startswith(
                  "/wide/sharing/rest/content/items/%s?" % TARGET)) == 1,
              "and the target item itself was read exactly once")
        hops = [line for line in seen.splitlines() if line.startswith("HOP")]
        check([line.split()[1:3] for line in hops]
              == [["2", W_APP], ["2", W_FILT], ["3", W_SITE]],
              "--depth 3 reports the app at hop 2, the dashboard that only "
              "filters on the target at hop 2, and the site at hop 3")
        check(W_NOTE not in "\n".join(hops) and W_EMBED not in "\n".join(hops),
              "and not the map that only mentions a broken map, nor the "
              "dashboard embedding a story that only names the target  "
              "<-- pinned defect")
        check("loads %s (Permit viewer), first hop %s" % (W_APP, W_MAP) in seen,
              "the site's line names what it loads and the first hop")
        code, seen = captured(lambda: main(
            ["--url", WIDE_URL, "--target", TARGET, "--token", LIVE_TOKEN,
             "--depth", "2"]))
        check(code == 1 and "1 more item(s) load one at hop 3" in seen,
              "--depth 2 says how many items it did not follow")
        code, seen = captured(lambda: main(
            ["--url", WIDE_URL, "--target", TARGET, "--token", LIVE_TOKEN]))
        def report_part(text):
            return text.split("searching the configuration")[1]

        check(code == 1 and "HOP" not in seen
              and report_part(wide_seen).startswith(report_part(seen)),
              "without --depth the same sweep prints the same report, line for "
              "line, and no transitive section after it  <-- pinned defect")

        graph_one = os.path.join(workdir, "graphs", "week1.json")
        code, seen = captured(lambda: main(
            ["--url", WIDE_URL, "--target", TARGET, "--token", LIVE_TOKEN,
             "--graph", graph_one]))
        check(code == 1 and not os.path.exists(graph_one)
              and "Check only" in seen,
              "--graph without --apply writes nothing  <-- pinned defect")
        code, seen = captured(lambda: main(
            ["--url", WIDE_URL, "--target", TARGET, "--token", LIVE_TOKEN,
             "--graph", graph_one, "--apply"]))
        check(code == 1 and os.path.isfile(graph_one),
              "--apply --graph writes the edge list, and creates its folder")
        with io.open(graph_one, encoding="utf-8") as handle:
            graph_text = handle.read()
        wide_graph = load_graph(graph_one)
        check([(one["from"], one["to"], one["kind"])
               for one in wide_graph["edges"]]
              == [(W_MAP, TARGET, HARD), (W_APP, W_MAP, HARD),
                  (W_SITE, W_APP, HARD), (W_NOTE, W_MAP, MENTION),
                  (W_DOC, TARGET, MENTION), (W_EMBED, W_DOC, HARD),
                  (W_FILT, TARGET, SOFT), (W_FILT, W_MAP, HARD)],
              "the graph holds every reference between the items of the sweep, "
              "with its class, in order")
        check(wide_graph["complete"] is True and wide_graph["unread"] == []
              and wide_graph["unproven"] == [],
              "and the flags of a sweep that read everything")
        check(LIVE_TOKEN not in graph_text,
              "no credential reaches the graph file  <-- pinned defect")

        previous = dict(wide_graph, taken="last week")
        previous["edges"] = [one for one in wide_graph["edges"]
                             if one["from"] != W_SITE] + [
            {"from": W_NOTE, "to": W_APP, "kind": HARD}]
        graph_zero = os.path.join(workdir, "week0.json")
        write_json(previous, graph_zero)
        code, seen = captured(lambda: main(
            ["--url", WIDE_URL, "--target", TARGET, "--token", LIVE_TOKEN,
             "--compare", graph_zero]))
        check("compared with the graph taken last week" in seen
              and "+ HARD BREAK   %s -> %s" % (W_SITE, W_APP) in seen
              and "- HARD BREAK   %s -> %s" % (W_NOTE, W_APP) in seen
              and "1 edge(s) added, 1 removed, 0 unknown" in seen,
              "--compare with one file prints the edges this sweep added and "
              "removed since it")
        check(code == 1,
              "and leaves the sweep's own exit code alone  <-- pinned defect")

        before = len(server.seen)
        code, seen = captured(lambda: main(["--compare", graph_zero,
                                            graph_one]))
        check(code == 1 and "1 edge(s) added, 1 removed" in seen,
              "--compare with two files diffs them offline and exits 1 on a "
              "change")
        check(len(server.seen) == before,
              "without one request to the portal  <-- pinned defect")
        code, seen = captured(lambda: main(["--compare", graph_one,
                                            graph_one]))
        check(code == 0 and "0 edge(s) added, 0 removed, 0 unknown" in seen,
              "and exits 0 when nothing changed")

        graph_base = os.path.join(workdir, "base.json")
        code, seen = captured(lambda: main(
            ["--url", LIVE, "--target", TARGET, "--token", LIVE_TOKEN,
             "--graph", graph_base, "--apply"]))
        base_graph = load_graph(graph_base)
        check(code == 2 and base_graph["complete"] is False
              and base_graph["unread"] == sorted([BIG_ID, DEAD_ID]),
              "a graph from a sweep with unread items carries them, by id  "
              "<-- pinned defect")
        code, seen = captured(lambda: main(["--compare", graph_base,
                                            graph_base]))
        check(code == 2 and "NOT complete" in seen,
              "and a diff that involves it exits 2, even with nothing changed, "
              "because a missing edge there is not proof  <-- pinned defect")
        code, seen = captured(lambda: main(["--compare", graph_base,
                                            graph_one]))
        check(code == 64 and "different url" in seen,
              "two graphs of different portals are refused, not diffed  "
              "<-- pinned defect")
        before = len(server.seen)
        code, seen = captured(lambda: main(
            ["--url", WIDE_URL, "--target", TARGET, "--token", LIVE_TOKEN,
             "--query", "type:Web Map", "--compare", graph_one]))
        check(code == 64 and "different query" in seen,
              "a graph taken with another --query is refused before the sweep")
        check(not any("search" in path for path in server.seen[before:]),
              "so the refused run never searched")

        out_both = os.path.join(workdir, "both.json")
        code, seen = captured(lambda: main(
            ["--url", WIDE_URL, "--target", TARGET, "--token", LIVE_TOKEN,
             "--depth", "3", "--out", out_both, "--graph", graph_one,
             "--apply"]))
        with io.open(out_both, encoding="utf-8") as handle:
            both = json.load(handle)
        check(code == 1 and [row["id"] for row in both["transitive"]]
              == [W_APP, W_FILT, W_SITE] and both["depth"] == 3,
              "--out and --graph together write both files, and the report "
              "carries the transitive rows")
        check(both["service_url"] == url_needle(server.service_url)[0],
              "and names the service url it searched for")

        code, seen = captured(lambda: main(
            ["--url", LIVE + "/noinfo", "--target", TARGET, "--token",
             LIVE_TOKEN, "--query", "type:Form"]))
        check(code == 2 and "service url: not searched, because the target "
              "item could not be read" in seen,
              "a target item the token may not read leaves the url search "
              "undone and says so")
        check("NOT known to be complete" in seen and LIVE_TOKEN not in seen,
              "so a sweep that found nothing exits 2, not 0, and prints no "
              "token  <-- pinned defect")

        # usage errors of the new flags, which never reach the portal
        for argv, needle, label in (
                (["--url", LIVE, "--target", TARGET, "--depth", "0"],
                 "--depth", "a --depth of zero is a usage error"),
                (["--url", LIVE, "--target", TARGET, "--out", graph_one,
                  "--graph", graph_one, "--apply"], "same file",
                 "--out and --graph naming one file is a usage error, because "
                 "the second write would destroy the first  <-- pinned defect"),
                (["--compare", graph_one, graph_one, graph_one], "one graph",
                 "--compare with three files is a usage error"),
                (["--url", LIVE, "--compare", graph_one, graph_one], "offline",
                 "--compare with two files and a --url is a usage error"),
                (["--compare", out_both, graph_one], "not a whobreaks graph",
                 "a report file handed to --compare is refused, it is not a "
                 "graph  <-- pinned defect"),
                (["--compare", graph_one, os.path.join(workdir, "none.json")],
                 "none.json", "a graph file that does not exist is refused"),
                (["--url", LIVE, "--target", TARGET, "--compare",
                  os.path.join(workdir, "none.json")], "none.json",
                 "and so it is before a sweep, which then never starts")):
            code, seen = captured(lambda: main(argv))
            check(code == 64 and needle in seen, label)
        bad_edge = os.path.join(workdir, "bad-edge.json")
        write_json(dict(wide_graph, edges=[{"from": W_MAP, "to": TARGET,
                                            "kind": "fatal"}]), bad_edge)
        raises(lambda: load_graph(bad_edge),
               "a graph holding an edge class this tool never writes is "
               "refused")
        for label, broken in (
                ("an edge whose class is a list, which is unhashable, is "
                 "refused as a bad graph rather than a TypeError  "
                 "<-- pinned defect",
                 dict(wide_graph, edges=[{"from": W_MAP, "to": TARGET,
                                          "kind": [HARD]}])),
                ("and so is one whose item is a list",
                 dict(wide_graph, edges=[{"from": [W_MAP], "to": TARGET,
                                          "kind": HARD}])),
                ("and one with no item it points to",
                 dict(wide_graph, edges=[{"from": W_MAP, "kind": HARD}])),
                ("a graph whose unread list is not a list of ids is refused, "
                 "because the diff reads it to tell a 403 from a fix  "
                 "<-- pinned defect", dict(wide_graph, unread=7)),
                ("and so is one whose unread list holds a list  "
                 "<-- pinned defect", dict(wide_graph, unread=[[W_MAP]])),
                ("and one whose edge names the empty string as its item  "
                 "<-- pinned defect",
                 dict(wide_graph, edges=[{"from": "", "to": TARGET,
                                          "kind": HARD}])),
                ("a graph with no list of the items it searched is refused, "
                 "because the diff reads it to tell an item that left the "
                 "sweep from a reference that was removed  <-- pinned defect",
                 dict((key, value) for key, value in wide_graph.items()
                      if key != "items")),
                ("and so is a file with edges but without the mark this tool "
                 "writes", dict((key, value) for key, value
                                in wide_graph.items()
                                if key != "whobreaks_graph"))):
            write_json(broken, bad_edge)
            raises(lambda: load_graph(bad_edge), label)
        code, seen = captured(lambda: main(["--compare", bad_edge, graph_one]))
        check(code == 64 and "not a whobreaks graph" in seen,
              "--compare turns a refused graph into a usage error, not a "
              "traceback")

        # --compare exit codes, one side at a time
        def compared(old, new):
            paths = []
            for n, graph in enumerate((old, new)):
                paths.append(os.path.join(workdir, "cmp%d.json" % n))
                write_json(graph, paths[-1])
            return captured(lambda: main(["--compare"] + paths))

        extra_edge = dict(wide_graph, edges=wide_graph["edges"] + [
            {"from": W_NOTE, "to": W_APP, "kind": HARD}])
        check(compared(extra_edge, wide_graph)[0] == 1,
              "--compare exits 1 when an edge was only removed")
        check(compared(wide_graph, extra_edge)[0] == 1,
              "and when an edge was only added")
        half = dict(wide_graph, complete=False)
        code, seen = compared(half, wide_graph)
        check(code == 2 and "previous graph is NOT complete" in seen,
              "and exits 2 when only the PREVIOUS graph was incomplete")
        code, seen = compared(wide_graph, half)
        check(code == 2 and "current graph is NOT complete" in seen,
              "and when only the CURRENT one was")

        # writing the files, and failing to
        code, seen = captured(lambda: main(
            ["--url", WIDE_URL, "--target", TARGET, "--token", LIVE_TOKEN,
             "--out", os.path.join(workdir, "case.json"), "--graph",
             os.path.join(workdir, "CASE.json"), "--apply"]))
        check(code == 64 and "same file" in seen
              and not os.path.exists(os.path.join(workdir, "case.json")),
              "--out and --graph that differ only in case are refused on "
              "every host, because Windows and a Mac both fold case and there "
              "the second write destroyed the first  <-- pinned defect")
        # What a Mac runs: normcase is the identity there, and its default
        # volume still folds case. Simulated, so every host proves it.
        import posixpath
        kept_normcase, os.path.normcase = os.path.normcase, posixpath.normcase
        try:
            code, seen = captured(lambda: main(
                ["--url", WIDE_URL, "--target", TARGET, "--token",
                 LIVE_TOKEN, "--out", os.path.join(workdir, "mac.json"),
                 "--graph", os.path.join(workdir, "MAC.json"), "--apply"]))
        finally:
            os.path.normcase = kept_normcase
        check(code == 64 and "same file" in seen,
              "and so they are where os.path.normcase keeps case, as it does "
              "on a Mac, whose default volume folds it  <-- pinned defect")
        code, seen = captured(lambda: main(
            ["--url", WIDE_URL, "--target", TARGET, "--token", LIVE_TOKEN,
             "--out", workdir, "--apply"]))
        check(code == 64 and "is a folder" in seen,
              "an --out that is a folder is refused before the sweep")
        blocker = os.path.join(workdir, "blocker")
        write_json({}, blocker)
        code, seen = captured(lambda: main(
            ["--url", WIDE_URL, "--target", TARGET, "--token", LIVE_TOKEN,
             "--graph", os.path.join(blocker, "g.json"), "--apply"]))
        check(code == 2 and "error:" in seen and "Traceback" not in seen,
              "a file that cannot be written ends the run on exit 2 and a "
              "message, not on a traceback's exit 1, which reads as "
              "\"something references it\"  <-- pinned defect")
        code, seen = captured(lambda: main(
            ["--url", LIVE, "--target", TARGET, "--token", LIVE_TOKEN,
             "--query", "type:Dashboard"]), encoding="ascii")
        check(code == 1 and DASH_ID in seen and "Permit board ??" in seen,
              "a title the console cannot encode is printed with a ? for each "
              "character, and the sweep still exits 1, because "
              "UnicodeEncodeError is a ValueError and used to make it exit 2  "
              "<-- pinned defect")
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
    check("service_url" in doc and doc["service_url"] is None,
          "and a sweep that searched for no service url says null, rather "
          "than leaving the key out")
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

    check(tally(3, ["x", "y"]) == (["5 assertions, 2 failed", "  FAILED: x",
                                     "  FAILED: y"], 1),
          "a red run ends on the failure count and names every failure, and "
          "exits 1  <-- pinned defect")
    check(tally(3, []) == (["3 assertions, 0 failed"], 0),
          "and a green run ends on the footer the README quotes, and exits 0")

    # Imported under another name, so that the module's own last line is
    # seen to do nothing unless the file is run as a script.
    import importlib.util
    spec = importlib.util.spec_from_file_location("whobreaks_imported",
                                                  os.path.abspath(__file__))
    imported = importlib.util.module_from_spec(spec)
    _none, import_noise = captured(lambda: spec.loader.exec_module(imported))
    check(import_noise == "" and imported.main is not main
          and imported.RANK == RANK,
          "importing the tool runs no sweep and prints nothing, so it can be "
          "used as a library")

    print("-" * 68)
    lines, code = tally(passed[0], failed)
    for line in lines:
        print(line)
    return code


def tally(passed, failed):
    """The self-test's closing lines and its exit code."""
    total = passed + len(failed)
    if failed:
        return (["%d assertions, %d failed" % (total, len(failed))]
                + ["  FAILED: %s" % item for item in failed]), 1
    return ["%d assertions, 0 failed" % total], 0


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
    ap.add_argument("--depth", type=int, default=1,
                    help="follow hard breaks this many hops from the target "
                         "(default 1: the items that reference it directly)")
    ap.add_argument("--graph",
                    help="file to write the edge list of this sweep to, as "
                         "json, with --apply")
    ap.add_argument("--compare", nargs="+", metavar="GRAPH",
                    help="print the edges added and removed since a graph "
                         "file. With one file, against this sweep. With two, "
                         "offline, between the two files and with no --url.")
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

    if args.compare and len(args.compare) > 2:
        print("error: --compare takes one graph file, or two to compare "
              "offline.", file=sys.stderr)
        return 64
    if args.compare and len(args.compare) == 2:
        if args.url:
            print("error: two graph files are compared offline. Drop --url, "
                  "or pass one file to compare it with a new sweep.",
                  file=sys.stderr)
            return 64
        return compare_files(args.compare[0], args.compare[1])

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
    if args.apply and not (args.out or args.graph):
        print("error: --apply needs --out or --graph, the file to write the "
              "report or the edge list to.", file=sys.stderr)
        return 64
    # Compared without case on every host. On Windows and on macOS's default
    # volume rep.json and REP.json are one file, and os.path.normcase folds
    # case on Windows only, so on a Mac the graph overwrote the report.
    if (args.out and args.graph
            and os.path.normcase(os.path.abspath(args.out)).lower()
            == os.path.normcase(os.path.abspath(args.graph)).lower()):
        print("error: --out and --graph name the same file, and the second "
              "would overwrite the first.", file=sys.stderr)
        return 64
    for path in (args.out, args.graph):
        if path and os.path.isdir(path):
            print("error: %s is a folder. --out and --graph name the file to "
                  "write." % path, file=sys.stderr)
            return 64
    if args.depth < 1:
        print("error: --depth must be at least 1.", file=sys.stderr)
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
    encoding = getattr(sys.stdout, "encoding", None)

    def say(line):
        # Every line that can carry an item title or a query goes through
        # printable(). UnicodeEncodeError is a ValueError, so a title the
        # console cannot encode used to end a complete sweep as exit 2.
        print(printable(line, encoding))

    previous = None
    if args.compare:
        try:
            previous = load_graph(args.compare[0])
        except (ValueError, OSError) as exc:
            complain(scrub(exc))
            return 64

    try:
        token = _authenticate(args)
        query = args.query
        if not query:
            oid = org_id(args.url, token, args.insecure)
            if not oid:
                raise RuntimeError("could not read the org id, pass --query")
            query = "orgid:%s" % oid
        if previous is not None:
            wrong = scope_mismatch(previous, args.url, query, target)
            if wrong:
                print("error: %s was taken with a different %s, so every item "
                      "one sweep left out would read as a change."
                      % (args.compare[0], " and ".join(wrong)),
                      file=sys.stderr)
                return 64
        say("reading %s" % args.url)
        say("target: %s" % target)
        say("query: %s" % query)
        extra = []
        try:
            needle, why = url_needle(
                item_url(args.url, token, target, args.insecure), target)
        except RuntimeError as exc:
            needle, why = None, "the target item could not be read"
            extra.append("the target item could not be read (%s), so no item "
                         "was searched for its service url" % scrub(exc))
        say("service url: %s" % (needle or "not searched, because %s" % why))
        items, unproven = sweep(portal_fetch(args.url, token, args.insecure),
                                base=query, num=args.num,
                                max_partitions=args.max_slices,
                                echo=say)
        print("searching the configuration of %d item(s)" % len(items))
        report = inspect(target, items,
                         portal_reader(args.url, token, args.insecure),
                         echo=say,
                         unproven=unproven + extra, url=needle)
    except (RuntimeError, ValueError) as exc:
        # ValueError is how the enumeration rejects a page it cannot trust: a
        # result with no id, a nextStart that does not advance. Those come from
        # the portal, not from a bug here, so they are an exit code and a
        # message rather than a traceback.
        complain(scrub(exc))
        return 2

    print("")
    for line in describe(report, args.depth):
        say(line)

    taken = utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    graph = build_graph(args.url, query, target, taken, report, needle)
    if previous is not None:
        print("")
        for line in describe_diff(previous, graph,
                                  *diff_graphs(previous, graph)):
            say(line)

    if args.out or args.graph:
        if not args.apply:
            print("")
            print("Check only. No report was written. Re-run with --apply.")
        else:
            print("")
            try:
                if args.out:
                    document = build_document(args.url, query, target, taken,
                                              report, args.depth, needle)
                    writer = write_csv if args.format == "csv" else write_json
                    say("wrote %s" % writer(document, args.out))
                if args.graph:
                    say("wrote %s" % write_json(graph, args.graph))
            except OSError as exc:
                # Exit 2, not the traceback's 1: a scheduled run that reads
                # the file must not read "something references it" and then
                # find no file, or last week's.
                complain(scrub(exc))
                return 2

    return exit_code(report)


def compare_files(old_path, new_path):
    """--compare OLD NEW: diff two graph files. No portal, no network at all.

    Exits 0 when no edge changed, 1 when one did, and 2 when either graph is
    incomplete, whatever the diff says: an edge missing from a sweep that could
    not read everything is not proof that the reference is gone.
    """
    try:
        old = load_graph(old_path)
        new = load_graph(new_path)
    except (ValueError, OSError) as exc:
        complain(scrub(exc))
        return 64
    wrong = scope_mismatch(old, new.get("url"), new.get("query"),
                           new.get("target"))
    if wrong:
        print("error: the two graphs were taken with a different %s, so every "
              "item one sweep left out would read as a change."
              % " and ".join(wrong), file=sys.stderr)
        return 64
    added, removed, unknown = diff_graphs(old, new)
    encoding = getattr(sys.stdout, "encoding", None)
    for line in describe_diff(old, new, added, removed, unknown):
        print(printable(line, encoding))
    if not (old.get("complete") and new.get("complete")):
        return 2
    return 1 if (added or removed) else 0


if __name__ == "__main__":
    sys.exit(main())
