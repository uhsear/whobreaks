# whobreaks

Report every item that references the one you are about to delete, by reading the configuration
of every item in the organization, whatever its type.

You delete a service nobody appears to use. The portal asked for one confirmation, the
dependency graph was empty, and nothing in the item page said anybody depended on it. A week
later a public Experience Builder app shows an empty map. The link between the two was a line in
a widget config that no portal screen ever displayed, and the delete is not reversible.

`arcgis.apps.itemgraph` is the obvious tool, and it does a lot. In arcgis 2.4.2 it reads
`/data` for web maps, dashboards, Experience Builder apps, StoryMaps and Hub sites. It also reads
their known draft resources, such as `config/config.json` and the StoryMap draft, and
`ItemNode.required_by()` walks the graph in reverse. It parses the item types it knows, by the
keys it knows. To find the item behind a web map layer's url, it requests that url.

This tool takes a different approach. It reads every item of any type, and every JSON resource
of each one, and it searches notebooks and text bodies too. It matches the id as a whole token,
so an id inside a longer id is not a hit. It sorts each hit into a hard break, a soft break or
a mention, and leaves thumbnails out. It never opens a url that it finds inside an item.

The break does not stop at the first item. The web map that loads the service goes blank, and
so does every app and site that loads the web map. The same sweep that reads every item for
the target also records every reference between any two items, so `--depth 3` follows the
break three hops out without one more portal call. It follows hard breaks only. A map that
filters on the target still draws, and a story that names an item in a paragraph breaks
nothing, so neither passes a break on to whatever loads it.

A reference by service url is the other half. A dependency tool can resolve a url that it
finds inside an item by requesting that url with the admin session. Then any item author can
make the admin token call a server of their choosing. This tool reads the target item's own
`url` once, and searches every configuration for it as text. No url found inside an item is ever
opened, and the self-test proves it with a url that points back at its own stand-in portal.

```
$ python whobreaks.py --self-test
whobreaks self-test: no portal, no credentials, one loopback server
--------------------------------------------------------------------
...
PASS  a truncated target is REFUSED, not searched for, because a short string matches more and the answer would be about another item  <-- pinned defect
...
PASS  the id inside an Experience Builder data source id matches, because a dash ends an id  <-- pinned defect
...
PASS  the same 32 characters INSIDE a longer id do not match  <-- pinned defect
...
PASS  two overlapping occurrences are BOTH found, because the scan advances by one and not by the length of what it matched
...
PASS  forty of them before the id in a description are a mention with the id in its excerpt, not an IndexError that ended the sweep  <-- pinned defect
...
PASS  and so is the older /info/thumbnail.png, which has no directory after it for the other test to see  <-- pinned defect
...
PASS  so a thumbnail url is not a reference at all  <-- pinned defect
...
PASS  a url is a hard break under a widget key nothing here recognises, because url alone says the value is loaded  <-- pinned defect
...
PASS  a popup reference is a soft break, even under a layer, because the innermost key is what the value is for  <-- pinned defect
PASS  a filter on a layer is a soft break and not a hard one  <-- pinned defect
...
PASS  prose caps the class DOWNWARDS, so an id typed into a layer's description is a mention and not a hard break  <-- pinned defect
...
PASS  the same string in a MARKDOWN cell is a mention, because prose cannot break  <-- pinned defect
...
PASS  an Experience Builder config yields two hard breaks: the data source and the widget binding  <-- pinned defect
...
PASS  and both say which resource they came from, because the published app does not hold this config  <-- pinned defect
...
PASS  a StoryMap draft resource yields the node and the resource that load the item  <-- pinned defect
...
PASS  a notebook yields a hard break for the code cell and a mention for the markdown one  <-- pinned defect
...
PASS  a web map that filters on the item and mentions it in a popup is soft and mention, never hard  <-- pinned defect
...
PASS  an item whose only occurrence is the target's THUMBNAIL yields NOTHING, which is the 300 item false positive this tool exists to avoid  <-- pinned defect
...
PASS  an item referencing a LONGER id that contains the target is not a reference  <-- pinned defect
PASS  an id used as a dictionary KEY is found, which is how Experience Builder names a layer data source  <-- pinned defect
PASS  an item whose /data is not JSON is still searched, as prose  <-- pinned defect
...
PASS  and the token sitting beside the id in that very url is redacted out of the excerpt  <-- pinned defect
...
PASS  access_token is a token too, although an underscore is a word character and \b never matched after it  <-- pinned defect
...
PASS  a secret written as "token": "S3CR3T" is redacted too, not only one written as key=value  <-- pinned defect
...
PASS  a token whose key sits further before the id than the excerpt reaches is still redacted, because the text is scrubbed before it is cut  <-- pinned defect
...
PASS  the fold joins two findings only when the file, the path AND the class all match  <-- pinned defect
...
PASS  an app whose url names it in the QUERY STRING is left to the id search, because without that query it is every app of the same template in the org  <-- pinned defect
...
PASS  a url that is a bare server is refused as a needle, because it would match every service on that server  <-- pinned defect
...
PASS  https://maps.example.org/arcgis is refused the same way, because a web adaptor or a rest/services root matches every service under it  <-- pinned defect
...
PASS  a viewer url whose query string opens ANOTHER item is refused, because without that query it is every link through that viewer  <-- pinned defect
...
PASS  layer 1 does not match inside layer 12, because a digit continues the url  <-- pinned defect
PASS  the same path on a host whose name ENDS in the target's host is not the target  <-- pinned defect
...
PASS  a layer that loads the target's SERVICE URL is a hard break, and the same url in prose is a mention, by the same path rules as an id  <-- pinned defect
...
PASS  the clone with the thumbnail is not a reference at all  <-- pinned defect
PASS  the item whose fetch RAISED is counted as unread, not as clean  <-- pinned defect
...
PASS  which exits 2 even though references were found, because 0 and 1 are answers and this run has none  <-- pinned defect
...
PASS  the console reports items in id order however the search returned them, so two runs of the same sweep read the same  <-- pinned defect
...
PASS  and an incomplete run says so in words, not only in its exit code  <-- pinned defect
...
PASS  and the report lists what BREAKS before what only mentions, then settles ties by id, whatever order the findings arrived in  <-- pinned defect
...
PASS  a long unread list prints its first TWENTY rows and then says how many it held back, so the tail alone cannot carry it  <-- pinned defect
PASS  an item's own id in its search result is not an edge to itself  <-- pinned defect
...
PASS  another item's THUMBNAIL is no edge, exactly as it is no finding  <-- pinned defect
...
PASS  an item that only FILTERS on the target still breaks at hop 2, because it also loads the map that loads the target  <-- pinned defect
PASS  a search that follows EVERY edge reports the dashboard embedding a story that only names the target, and the map that only mentions a broken one
PASS  and along hard edges neither of them breaks, which is the wrong answer that search gives  <-- pinned defect
PASS  the target loading its own map is a cycle, not a break of the target  <-- pinned defect
...
PASS  transitive mode inherits the unread limit at EVERY hop: an unread item could load any broken item, so the run still exits 2  <-- pinned defect
...
PASS  an edge whose item could not be READ this time is unknown, not removed, because a 403 is not a fix  <-- pinned defect
...
PASS  an edge of an item the NEW sweep could not read is unknown even when it looks ADDED, because the metadata of an unread item is still searched and yields edges  <-- pinned defect
...
PASS  a map that left the sweep takes its own edge with it, and the edges TO it are unknown, not removed, because the items that load it did not change  <-- pinned defect
...
PASS  a page size of 1000 is clamped to 100 before it is sent, because the server clamps it silently  <-- pinned defect
...
PASS  exactly 10,000 is the CAP and never a total  <-- pinned defect
...
PASS  a short page advances by the ten rows it got, not by the hundred it asked for  <-- pinned defect
...
PASS  a slice that reported the ceiling on a LATER page is split, because the largest count any page gave is the only safe one to believe  <-- pinned defect
...
PASS  a search result whose id is a number raises the same ValueError, not a TypeError traceback, which exits 1  <-- pinned defect
PASS  a search result whose id is a list raises the same ValueError, not a TypeError traceback, which exits 1  <-- pinned defect
PASS  and so does a result that is not an object at all, as the same ValueError rather than an AttributeError traceback  <-- pinned defect
...
PASS  a slice still at the ceiling one day wide cannot be split again and is reported unproven  <-- pinned defect
PASS  its rows are KEPT all the same, because an item this tool can still search beats one it never sees  <-- pinned defect
...
PASS  the two halves meet at one millisecond past the midpoint, so no item falls in both and none falls in neither  <-- pinned defect
PASS  a portal that reports the ceiling for every query stops at the slice budget instead of splitting for ever  <-- pinned defect
...
PASS  an item with sixty json resources is read to the default fifty, so one StoryMap cannot turn a sweep into an afternoon
...
PASS  control characters in a title become ?, whatever the console's encoding, so an item owner cannot rewrite the lines above it on the operator's screen  <-- pinned defect
...
PASS  the default opener verifies the certificate  <-- pinned defect
...
PASS  and the password really did travel in the POST body, not in the query string  <-- pinned defect
...
PASS  a url carrying a context path the portal does not serve is refused, not read as an empty org  <-- pinned defect
...
PASS  a resource that is gone by the time it is fetched raises, which the reader turns into one unread item  <-- pinned defect
...
PASS  a real HTTP 500 raises rather than returning an empty body that would read like a clean item  <-- pinned defect
PASS  and the token that call carried is not in the message  <-- pinned defect
...
PASS  and the half that arrived is no longer JSON, so it comes back as text to be searched as prose  <-- pinned defect
...
PASS  its config comes from a RESOURCE, because its /data is empty and the draft is where the id lives  <-- pinned defect
...
PASS  an item too large to fetch is a problem too, so it is unread rather than clean  <-- pinned defect
...
PASS  a body nested too deep for json.loads is searched as text and its item is unread, not a RecursionError that ended the sweep  <-- pinned defect
PASS  so the real hard break in another item of the same sweep is still reported, and the run exits 2  <-- pinned defect
...
PASS  a StoryMap whose sixty photographs fill the first page of its resource listing still has its draft read, because the listing is paged to its end  <-- pinned defect
...
PASS  an item with more JSON resources than the limit reads that many and is UNREAD, not clean, because the rest were never searched  <-- pinned defect
PASS  a /data body over the byte cap is searched to the cap and the item is UNREAD, because the target id can sit past the cap  <-- pinned defect
...
PASS  a sweep with two unreadable items exits 2, whatever it found  <-- pinned defect
...
PASS  the cloned item carrying the target's thumbnail is not in the report at all  <-- pinned defect
...
PASS  the token never reaches stdout  <-- pinned defect
...
PASS  a complete sweep that found a reference exits 1  <-- pinned defect
...
PASS  a sweep that finds nothing, and read everything it found, is the only run that exits 0  <-- pinned defect
...
PASS  no credential reaches the file on disk  <-- pinned defect
PASS  and the json comes out with its keys in order, so two runs a week apart diff to the findings that changed  <-- pinned defect
...
PASS  with no blank line between rows: the defect DOUBLES the carriage return, it does not add a whole empty line, so that is the sequence asserted on  <-- pinned defect
...
PASS  --username with a plain http:// portal url is refused before any request, because the password would travel in clear  <-- pinned defect
...
PASS  the token can come from WHOBREAKS_TOKEN, so a scheduled run keeps it off argv, and it is sent on every call  <-- pinned defect
...
PASS  the web map that loads the target only by its SERVICE URL is a hard break, over the wire  <-- pinned defect
...
PASS  neither the target's service url nor the other url inside the web map was ever requested, although both point at this very server  <-- pinned defect
...
PASS  --depth 3 reports the app at hop 2, the dashboard that only filters on the target at hop 2, and the site at hop 3
PASS  and not the map that only mentions a broken map, nor the dashboard embedding a story that only names the target  <-- pinned defect
...
PASS  --compare with two files diffs them offline and exits 1 on a change
PASS  without one request to the portal  <-- pinned defect
...
PASS  so a sweep that found nothing exits 2, not 0, and prints no token  <-- pinned defect
...
PASS  an edge whose class is a list, which is unhashable, is refused as a bad graph rather than a TypeError  <-- pinned defect
...
PASS  an edge to an item that left the sweep is unknown and exits 2, not 0: the app still loads the map that was deleted  <-- pinned defect
...
PASS  --out and --graph that differ only in case are refused on every host, because Windows and a Mac both fold case and there the second write destroyed the first  <-- pinned defect
PASS  and so they are where os.path.normcase keeps case, as it does on a Mac, whose default volume folds it  <-- pinned defect
...
PASS  a file that cannot be written ends the run on exit 2 and a message, not on a traceback's exit 1, which reads as "something references it"  <-- pinned defect
PASS  a title the console cannot encode is printed with a ? for each character, and the sweep still exits 1, because UnicodeEncodeError is a ValueError and used to make it exit 2  <-- pinned defect
...
PASS  importing the tool runs no sweep and prints nothing, so it can be used as a library
--------------------------------------------------------------------
548 assertions, 0 failed
```

The full run prints all 548 assertions. It prints the same 548 lines on Windows under Python
3.13, 3.12 and 3.9, and on Linux under Python 3.12. The `...` lines above are where this block is
cut.

## Requirements

Python 3.9 or newer. Standard library only: `urllib`, `json`, `csv`, `re`, `ssl`, `os`, `io`,
`argparse`, `datetime`, `getpass`, `time`, and `http.server`, `threading`, `socket`, `tempfile`,
`shutil` and `importlib` inside the self-test. It runs on ArcGIS Pro's Python and on a plain `python3`.
`arcpy` is not used and the `arcgis` package is not needed.

```
git clone https://github.com/uhsear/whobreaks.git
python whobreaks.py --self-test
```

`--self-test` needs no portal and no credentials, so you can check the tool before you point it
at an organization. The matcher, the classifier and the traversal run over canned item data. The
HTTP layer runs against a stand-in portal this tool starts on `127.0.0.1`: a real socket, a real
sign-in POST, a real HTTP 500 on one item, real Experience Builder and StoryMap resources, a
second organization for the transitive search and the service url, and the whole command line
end to end. That includes the graph files and the diff between two of them.

## Quick start

```
export WHOBREAKS_PASSWORD='...'
python whobreaks.py --url https://yourorg.maps.arcgis.com --target a1b2c3d4e5f60718293a4b5c6d7e8f90 \
    --username gis_admin
```

A scheduled run that already holds a token sets `WHOBREAKS_TOKEN` instead of passing `--token`,
because other processes on the machine can read the command line.

This is the self-test's own run against its first stand-in organization, on the loopback port
that run happened to get, and on a console with the Windows code page:

```
$ python whobreaks.py --url http://127.0.0.1:62206 --target a1b2c3d4e5f60718293a4b5c6d7e8f90 --token ...
reading http://127.0.0.1:62206
target: a1b2c3d4e5f60718293a4b5c6d7e8f90
query: orgid:REALORG
service url: 127.0.0.1:62206/arcgis/rest/services/permits/featureserver
  11 item(s) from (orgid:REALORG) AND created:[0 TO 1790550925170]
searching the configuration of 11 item(s)
  HARD BREAK 0b000000000000000000000000000000 (Nightly refresh)
  HARD BREAK 5a000000000000000000000000000000 (How permits work)
  MENTION ONLY 7e000000000000000000000000000000 (refresh.py)
  SOFT BREAK ac000000000000000000000000000000 (Permit map)
  HARD BREAK da000000000000000000000000000000 (Permit board ??)
  MENTION ONLY dc000000000000000000000000000000 (Archive notes)
  HARD BREAK eb000000000000000000000000000000 (Permit finder)

7 item(s) reference a1b2c3d4e5f60718293a4b5c6d7e8f90

HARD BREAK   0b000000000000000000000000000000  Nightly refresh [Notebook]
             data: cells/code/source (x1)
             lyr = gis.content.get('a1b2c3d4e5f60718293a4b5c6d7e8f90')
             data: cells/markdown/source (x1)
             The source layer used to be a1b2c3d4e5f60718293a4b5c6d7e8f90.
HARD BREAK   5a000000000000000000000000000000  How permits work [StoryMap]
             resources/draft_x.json: nodes/n-x1/data/itemId (x1)
             a1b2c3d4e5f60718293a4b5c6d7e8f90
             resources/draft_x.json: resources/r-1/data/itemId (x1)
             a1b2c3d4e5f60718293a4b5c6d7e8f90
HARD BREAK   da000000000000000000000000000000  Permit board ?? [Dashboard]
             data: datasets/dataSource/itemId (x1)
             a1b2c3d4e5f60718293a4b5c6d7e8f90
HARD BREAK   eb000000000000000000000000000000  Permit finder [Web Experience]
             resources/config.json: dataSources/dataSource_1/itemId (x1)
             a1b2c3d4e5f60718293a4b5c6d7e8f90
             resources/config.json: widgets/widget_1/useDataSources/dataSourceId (x1)
             dataSource_1-a1b2c3d4e5f60718293a4b5c6d7e8f90-0
SOFT BREAK   ac000000000000000000000000000000  Permit map [Web Map]
             data: bookmarks/name (x1)
             site a1b2c3d4e5f60718293a4b5c6d7e8f90
             data: operationalLayers/layerDefinition/definitionExpression (x1)
             SOURCE_ID = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'
             data: operationalLayers/popupInfo/description (x1)
             joined to a1b2c3d4e5f60718293a4b5c6d7e8f90
MENTION ONLY 7e000000000000000000000000000000  refresh.py [Code Attachment]
             data: (not json) (x1)
             import arcgis lyr = gis.content.get('a1b2c3d4e5f60718293a4b5c6d7e8f90') # nightly refresh
MENTION ONLY dc000000000000000000000000000000  Archive notes [Document Link]
             metadata: description (x1)
             superseded by a1b2c3d4e5f60718293a4b5c6d7e8f90, keep for audit

4 hard, 1 soft, 2 mention only, over 10 item(s) searched

2 item(s) COULD NOT BE READ, so they are not clean, they are unknown:
  b1000000000000000000000000000000 Parcel delivery: 33554433 byte item was not fetched, over the 33554432 byte limit, so its configuration was never searched
  de000000000000000000000000000000 Locked report: data: http://127.0.0.1:62206/sharing/rest/content/items/de000000000000000000000000000000/data: HTTP Error 500: Internal Server Error

Do not read this run as permission to delete. It searched what it could read.
```

Eleven items, ten searched, seven of them referencing the target. The dashboard's title holds two
characters that the code page cannot show, so they print as `?`. One more item holds the id and is
not in the list. It is a cloned viewer that carries only the target's thumbnail url, and a
thumbnail is a picture of an item, not a use of it.

The run exits 2 rather than 1, because two items could not be read. One is too large to fetch
and one answered HTTP 500, and an item nobody read is not an item known to be clean.

## Following the break further

`--depth` follows hard breaks past the first hop. This is the self-test's own run against its
second stand-in organization, on the loopback port that run happened to get:

```
$ python whobreaks.py --url http://127.0.0.1:62206/wide --target a1b2c3d4e5f60718293a4b5c6d7e8f90 \
    --token ... --depth 3
reading http://127.0.0.1:62206/wide
target: a1b2c3d4e5f60718293a4b5c6d7e8f90
query: orgid:REALORG
service url: 127.0.0.1:62206/arcgis/rest/services/permits/featureserver
  8 item(s) from (orgid:REALORG) AND created:[0 TO 1790550924637]
searching the configuration of 8 item(s)
  HARD BREAK f1000000000000000000000000000000 (Permit layers)
  MENTION ONLY f5000000000000000000000000000000 (Permit handbook)
  SOFT BREAK f7000000000000000000000000000000 (Permit filter board)

3 item(s) reference a1b2c3d4e5f60718293a4b5c6d7e8f90

HARD BREAK   f1000000000000000000000000000000  Permit layers [Web Map]
             data: operationalLayers/url (x1)
             http://127.0.0.1:62206/arcgis/rest/services/Permits/FeatureServer/0
SOFT BREAK   f7000000000000000000000000000000  Permit filter board [Dashboard]
             data: datasets/filter (x1)
             PERMIT_SRC = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'
MENTION ONLY f5000000000000000000000000000000  Permit handbook [StoryMap]
             metadata: description (x1)
             explains a1b2c3d4e5f60718293a4b5c6d7e8f90

1 hard, 1 soft, 1 mention only, over 7 item(s) searched

3 more item(s) break through a hard break, to depth 3:
HOP 2        f2000000000000000000000000000000  Permit viewer [Web Mapping Application]
             loads f1000000000000000000000000000000 (Permit layers), first hop f1000000000000000000000000000000
HOP 2        f7000000000000000000000000000000  Permit filter board [Dashboard]
             loads f1000000000000000000000000000000 (Permit layers), first hop f1000000000000000000000000000000
HOP 3        f3000000000000000000000000000000  Permit site [Hub Site Application]
             loads f2000000000000000000000000000000 (Permit viewer), first hop f1000000000000000000000000000000
```

The web map names the target only by its service url, and it is still a hard break. Its second
layer points at another url on the same server. The stand-in portal logged no request for
either url.

The filter board only filters on the target, which is a soft break. It also loads the web map,
so it breaks at hop 2 all the same. Two items are left out of the hop list on purpose. Permit
notes names the web map in its description, and Handbook board embeds a story that only names
the target. A search that followed every edge would list both of them.

With `--depth 2` the same run lists the two hop 2 items and ends on
`1 more item(s) load one at hop 3. Raise --depth to follow them.` A depth that stops short says
so. Without `--depth` the report is the one above, line for line, with no hop section.

### Snapshots and the diff

`--apply --graph week2.json` writes every edge of the sweep, with the flags that say how far to
trust it. `--compare` prints what changed since an earlier graph. With one file, it compares
that file with the sweep it runs. With two files, it runs offline and makes no request at all.
This is the self-test's run over two graphs of its second organization:

```
$ python whobreaks.py --compare week0.json graphs/week1.json
compared with the graph taken 2026-09-20T14:00:00Z
+ HARD BREAK   f3000000000000000000000000000000 -> f2000000000000000000000000000000
- HARD BREAK   f4000000000000000000000000000000 -> f2000000000000000000000000000000
1 edge(s) added, 1 removed, 0 unknown
```

An edge is the item that holds the reference, the item it names, and the class. A reference
whose class changed prints as one edge removed and one edge added. An edge whose item could not
be read in one of the two sweeps prints with a `?` as unknown, because a 403 this week is not a
reference that somebody fixed. An edge that names an item one of the two sweeps did not hold is
unknown too. An app whose web map was unshared from the sweeping account still loads that map,
and its edge is missing only because the map was not in the sweep to be named. An item that
itself left the sweep takes its own edges with it, and those print as removed.

## Usage

| Flag | Default | What it does |
|---|---|---|
| `--url` | none | Portal url. Required. Must start with `https://` or `http://`, and must carry no user name, password, query string or fragment. |
| `--target` | none | The item id you are about to delete. Required, and 32 hexadecimal characters. |
| `--query` | the signed-in org | Search query to sweep. The created-date slices are ANDed onto it. |
| `--token` | none | An existing portal token. Other processes can read it on the command line, so a scheduled run sets `WHOBREAKS_TOKEN` instead. |
| `--username` | none | Sign in as this user. The password comes from `WHOBREAKS_PASSWORD` or an unechoed prompt. Refused with an `http://` url, except on this machine's loopback address. |
| `--out` | none | File to write the report to. |
| `--format` | `json` | `json` or `csv`. |
| `--num` | `100` | Rows per search page to ask for. Clamped to 100, which is all the server gives. |
| `--max-slices` | `4096` | Give up after this many created-date slices rather than splitting for ever. |
| `--depth` | `1` | Follow hard breaks this many hops from the target. 1 lists the direct references only. |
| `--graph` | none | File to write the edge list of the sweep to, as JSON. Needs `--apply`. |
| `--compare` | none | One graph file: print the edges added and removed since it. Two graph files: compare them offline, with no `--url`. |
| `--insecure` | off | Skip TLS verification, for an Enterprise portal behind an internal CA. Refused together with `--username`. |
| `--apply` | off | Write the `--out` report and the `--graph` file. Without it nothing is written. |
| `--self-test` | off | Run the offline assertions and exit. |

`--apply` writes local files only. It does not change the organization, and there is no flag
that does. The password is never a flag either: `argv` is readable by every process on the
machine, and the self-test asserts that no `--password` attribute exists at all. A password is
never posted over plain `http://` either. The one exception is the loopback address, which the
self-test uses.

## What it checks (or refuses)

For every item in the organization except the target itself, this tool reads three things and
searches all of them for the target id:

1. the search result, which already carries the title, the description and the snippet,
2. `/content/items/{id}/data`, which holds the web map, the dashboard and the notebook,
3. `/content/items/{id}/resources`, which holds the Experience Builder config and the StoryMap
   draft. Those two are where an id hides while the published app still points elsewhere.

It also searches them for the target's service url. That url is read once, from
`/content/items/{target}`. It is changed to lower case, and its scheme, query string, trailing
slash and any user and password are taken off. It must match as a whole url: `.../FeatureServer/1`
is not found inside `.../FeatureServer/12`, and `maps.example.org` is not found inside
`oldmaps.example.org`. A url that names a bare server, a web adaptor such as `/arcgis`, or a
`rest/services` root is not searched for, because it would match every service under it. A viewer
url whose query string opens another item, such as `index.html?webmap=<id>`, is not searched for,
because without its query it matches every link through that viewer. A url that carries the
target id anywhere, including in its query string, is not searched for either.
A service url that is percent-encoded inside another url, as in a Map Viewer link
`index.html?url=https%3A%2F%2F...`, is found too. The id search already finds it, and an app's url without its
`?id=` is every app of the same template in the organization.

`/content/items/{id}/resources` is listed to its last page. A StoryMap can list sixty photographs
before its draft, and a listing cut at the first page never reaches the draft.

The same read records every reference from one item to any other item of the sweep, by item id.
That is the edge list that `--depth` follows and `--graph` writes.

Each occurrence is classified by the path it sits at, not by the file it came from:

| Class | Means | Examples |
|---|---|---|
| `HARD BREAK` | the item loads the target at draw time | `operationalLayers/itemId`, `dataSources/.../itemId`, `datasets/dataSource/itemId`, a notebook code cell |
| `SOFT BREAK` | the item references the target without drawing it | `definitionExpression`, `bookmarks`, `popupInfo` |
| `MENTION ONLY` | somebody typed the id into prose | `description`, `snippet`, a markdown notebook cell |

The innermost key wins, so a `definitionExpression` inside `operationalLayers` is a filter and
not a layer. Prose caps the class downwards, so an id typed into a layer's description is a
mention whatever structure surrounds it.

It refuses, or reports as unknown, rather than guessing:

- **A thumbnail url is not a reference.** An item card, a gallery entry and a story cover all
  store another item's thumbnail url, and that url contains that item's id. A substring scan
  reported 300 of those as breaks on a real organization. This tool checks the key name and the
  url shape, and reports none of them.
- **An id inside a longer id is not a reference.** The match has to be a whole token: what
  precedes and follows it cannot be a letter or a digit.
- **A malformed `--target` is refused** before the first request. A truncated id is a shorter
  string, a shorter string matches more, and the report would be about a different item.
- **An item that could not be read is not a clean item.** Every fetch is isolated, so one 403
  costs one item instead of the sweep, and the unread items get their own section of the report
  with the reason attached.
- **An item that was read in part is not a clean item either.** A `/data` body or a resource
  over 4 MB is searched to 4 MB, and the item is unread, because the id can sit past the cap. An
  item with more than 50 JSON resources is read to 50 and is unread for the same reason.
- **A search that hits the 10,000 result ceiling is not a complete enumeration.** The sweep
  splits the created-date range and re-asks. When the pages of one query disagree about the
  total, the largest count any page reported is the one believed, so a later small number
  cannot cancel the split. When a single day is still on the ceiling it keeps the rows it did
  read, says which slice it could not prove, and refuses to exit 0.
- **A configuration nested too deep to parse is not a clean item.** JSON nested deeper than
  `json.loads` can recurse is searched as text, so an id in it is still found as a mention, and
  the item is unread. What parses is walked with a loop, not recursion, so a deep config that
  does parse is classified to its last level. One such item used to end the whole sweep on a
  `RecursionError`, with a real break in another item never reported.
- **A search page this tool cannot trust ends the run.** A result with no id, or with an id that
  is a number or a list, stops the sweep on exit 2 with a message. It used to stop on a
  traceback, which exits 1, and 1 reads as "something references it".
- **A target item that could not be read is a gap, not a pass.** Its url is then unknown, so no
  item was searched for it. The run says so and cannot exit 0.
- **A credential in `--url` is refused.** A `user:password@` or a `?token=` in the portal url was
  printed on the first line of the run and written into both files.
- **A url found inside an item is never opened.** If this tool resolved one, an item author
  could choose a server that the operator's token is sent to.
- **A break travels along hard references only.** Soft and mention edges are recorded and
  written to the graph, and the impact search never follows them.
- **Two graphs of different scopes are refused.** When the portal url, the query or the target
  differs, every item that one sweep left out would read as an edge removed.

## Exit codes

| Code | Means |
|---|---|
| 0 | nothing in the organization references the target, and every item was enumerated and read |
| 1 | something references it, listed worst first |
| 2 | the sweep could not be completed, so nothing can be concluded. An item was unreadable or read in part, the target item itself was unreadable, a search slice sat on the 10,000 ceiling, or a search page held a result with no usable id. Also when `--apply` could not write a file it was asked for, and when the tool met a failure it did not foresee |
| 64 | usage error, including a flag that is unknown or has a bad value, a graph file that is missing, is not a graph, or covers a different scope, and an `--out` or `--graph` that is a folder |

`--depth` and a one-file `--compare` do not change the exit code of the sweep. A two-file
`--compare` exits 0 when no edge changed and 1 when an edge did. It exits 2 when either graph is
incomplete, whatever the diff says, because an edge missing from that graph is not proof. It also
exits 2 when an edge is unknown. That is what a deleted map leaves behind in the app that still
loads it.

Exit 2 beats exit 1 on purpose. Somebody who sees 1 goes and looks at the items. Somebody who
sees 0 deletes the item, so 0 has to mean the sweep was complete, not that it finished.

## Output

`--format json` writes the findings, the unread items and the reasons the enumeration might be
incomplete into one document, because those three have to be read together. `--format csv`
writes the nine finding columns: `id`, `title`, `type`, `owner`, `kind`, `source`, `path`,
`count`, `excerpt`.

Excerpts are scrubbed on the way out. A secured feature service url inside a web map carries a
token in its query string, and that excerpt is the most useful line in the report, so it is
written with the token replaced rather than dropped. The text is scrubbed before the excerpt is
cut from it, so a token whose key is outside the excerpt is still replaced. `access_token=` is
replaced as well as `token=`, and so are `client_secret=`, `secret=` and an Azure `sig=`, and so is a value written as `token="..."`, `password = '...'` or
`"token": "..."`. The self-test asserts that no credential reaches stdout or either file.

The console replaces every control character in a line with `?`. That includes the title, the
excerpt and an error message that quotes the portal. An item owner can put `ESC [2K` and a
carriage return in a title, and on the console that rewrote the report line above it. The JSON
and CSV files keep the title as the portal holds it.

With `--depth` above 1, the JSON report also carries `depth`, `beyond` and a `transitive` list.
Each row has the item's `id`, `title` and `type`, its `hop`, the first-hop item as `via`, and
the item it `loads`. Every JSON report names the `service_url` it searched for, or null.

The `--graph` file holds `edges`, each with `from`, `to` and `kind`, sorted by `from` and then
`to`. The flags are next to them: `complete`, `unread` (the ids of the items that could not be
read), `items` (the ids of every item searched) and `unproven`. The file also records `url`, `query`, `target`, `service_url`, `scanned`
and `taken`. The self-test asserts that no credential reaches this file either.

## Limits

- It searches the organization you can see. An anonymous sweep covers public items, and a sweep
  as a non-administrator covers what that user can search. The answer is honest about the query,
  not about the organization.
- It reads JSON resources only, and at most 50 of them per item. An item id inside a PNG, a
  mobile map package or a file geodatabase in an item's resources is not found. An item with
  more than 50 JSON resources is reported as unread.
- `/data` and each resource are read to 4 MB. A body truncated there stops parsing as JSON and is
  searched as text, and the item is reported as unread, because an id past the cap was never
  looked at. A file item whose upload is between 4 MB and 32 MB therefore makes the sweep exit 2.
  Narrow the sweep with `--query` to leave such items out, and say so when you report the result.
  Items larger than 32 MB are not fetched at all, and are reported as unread.
- Two portal calls per item, plus one per JSON resource and one per further page of 50 rows in a
  long resource listing, plus one for the target item's url. A
  sweep of 9,000 items is a slow afternoon, not a quick check, and `--query` is how you narrow
  it. `--depth` and `--compare` add no call.
- The classes are a judgement about a key name, not proof. A widget nobody has heard of, holding
  the id under a key this tool does not know, is reported as a mention rather than missed.
- The depth at which JSON stops parsing depends on the Python that runs this tool. Python 3.9
  on Windows stops before 1,500 levels, 3.12 and 3.13 on Windows parse 1,500 and stop before
  5,000, and 3.12 on Linux parses 5,000. An item between those depths is unread on one host and
  read on another. The self-test uses 100,000 levels, which no host parses.
- Only a secret that follows a key name this tool knows is scrubbed: `token`, `password`,
  `apikey`, `api_key`, `code_verifier`, `signature`, `secret` and `sig`, with any prefix such as
  `access_`. A password passed as a bare positional argument, as in `GIS(url, "admin", "...")`,
  is not recognised and is printed in the excerpt.
- A string with no separator in it that holds the target id thousands of times, such as ids
  joined by dashes, is slow to search. Each match reads the whole string again, so 2,000
  matches in one string took about ten seconds.
- A portal that returns item ids in upper case is not supported. ArcGIS Online and Enterprise
  return lower case, and an upper-case copy of the target is searched as if it were another item.
- `--out` and `--graph` are compared by name, without regard to case. Two names for one file,
  such as a hard link, a symbolic link or an NTFS stream name like `r.json::$DATA`, are not
  detected, and the second write replaces the first.
- A graph file is trusted to be one this tool wrote. The file is checked for its shape and
  types, but a hand-edited file that says `"complete": true` next to a list of unread items is
  read as complete.
- It cannot see a reference that is neither the item id nor the target's own url. That includes
  a url through another host name, a proxy or an explicit port, a view layer's url, a layer
  named by title in a script, and an id assembled at run time from parts.
- The service url is matched for the target only, at the first hop. The edges between other
  items are by item id. An app loads a web map by id, so this matters mostly for a service that
  loads another service by url, which item configuration does not record.
- `--depth` inherits every limit above, at every hop. An unread item can load any broken item,
  so an incomplete sweep says so under the hop list as well, and still exits 2. An item outside
  the query is not in the sweep, so the chain stops at the edge of the query.
- It does not delete, re-point or unshare anything. Repointing what it finds is `agol-relink`'s
  job.
- Group membership, folder structure and sharing are out of scope. This tool answers one
  question: what would break.

## Contributing

Open an issue or pull request on GitHub.

## Author

Built by [Asir Khan](https://www.linkedin.com/in/asir-khan-310317264/).

## License

MIT.

## Related

Other single-file tools in this portfolio that pair with this one:

- [itemcensus](https://github.com/uhsear/itemcensus) - the enumeration underneath this, including the bisect past the 10,000 search ceiling
- [sightline](https://github.com/uhsear/sightline) - what a viewer can see among the items that survive
- [agol-relink](https://github.com/uhsear/agol-relink) - repoint the references this finds, instead of deleting
- [stalehost](https://github.com/uhsear/stalehost) - the whole-token rule that the service url match follows, applied to a retired host name on disk
