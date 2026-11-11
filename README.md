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

A graph in a file that nobody opens is not watched, and a break arrives one week at a time.
`--dashboard` turns the graph into one static page, and `--compare` puts what changed since the
earlier sweep on it. A scheduled sweep that replaces the file keeps a page on a screen current.

A page on one workstation is still not where a team looks. `--publish` puts the graph in the
portal as one item, and a scheduled sweep with `--publish <item id>` replaces that item's
file each week, so its contents move when the dependencies do. It is the
only flag in this tool that writes to the organization, it needs `--apply`, and it refuses to
overwrite any item that this tool did not make for the same target. The next sweep does not
count that item as a reference: it recognises a graph this tool published, for this target or
for another, leaves it out and says so.

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
PASS  an organization in which nothing references the target exits 0
...
PASS  no credential reaches the file on disk  <-- pinned defect
...
PASS  a body the connection cut short is a problem, because HTTPResponse.read does not raise and the half was searched as if it were the whole  <-- pinned defect
...
PASS  a token whose user is not an administrator cannot search another user's private items, so a clean sweep is exit 2  <-- pinned defect
PASS  and the graph file carries the caveat next to complete  <-- pinned defect
PASS  and the page does not show the green banner, nor claim that nothing loads the target  <-- pinned defect
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
PASS  --dashboard without --apply writes nothing  <-- pinned defect
...
PASS  no credential reaches the page  <-- pinned defect
...
PASS  a sweep with unread items says NOT COMPLETE on the page and lists them, because a page that looked clean would be read as permission to delete  <-- pinned defect
...
PASS  --compare with two files and --dashboard writes nothing without --apply  <-- pinned defect
...
PASS  a page that would replace the graph the run compares with is refused  <-- pinned defect
...
PASS  a page that cannot be written ends the run on exit 2 and a message  <-- pinned defect
...
PASS  a write that fails at the rename leaves the old page and no partial file  <-- pinned defect
...
PASS  a title the console cannot encode is printed with a ? for each character, and the sweep still exits 1, because UnicodeEncodeError is a ValueError and used to make it exit 2  <-- pinned defect
...
PASS  and an independent reader gets every value and the file bytes back, including a carriage return and a near boundary inside the file
PASS  another user's item is refused as a place to put the graph  <-- pinned defect
PASS  --publish without --apply sends no POST and adds no item  <-- pinned defect
PASS  no request shares the item: this tool never shares it  <-- pinned defect
PASS  no credential reaches the console, the item's file, its title or its description  <-- pinned defect
PASS  a portal that answers corrupt is a failure, not a published graph  <-- pinned defect
...
PASS  a title, an owner and an excerpt from a swept item reach the csv guarded, from the sweep through build_document  <-- pinned defect
...
PASS  a user name with a # and an @ is quoted into the path, or the # ends the url there  <-- pinned defect
PASS  a unique prefix of --publish is refused, because it names a write to the organization  <-- pinned defect
...
PASS  a title, a type, a target and a url that hold markup are escaped, so an item owner cannot put script on the page  <-- pinned defect
...
PASS  a diff on the page marks an edge of an unread item unknown, and says why  <-- pinned defect
...
PASS  a unique prefix of --apply is refused, because abbreviations would turn a typo into a write  <-- pinned defect
PASS  a prefix of --dashboard is refused, because it names a file the run will write  <-- pinned defect
...
PASS  importing the tool runs no sweep and prints nothing, so it can be used as a library
--------------------------------------------------------------------
783 assertions, 0 failed
```

The full run prints all 783 assertions. It printed the same 783 lines on Windows under
Python 3.13, 3.12 and 3.9, and on Linux under Python 3.12. The `...` lines above are where this
block is cut.

On the Linux host, about one run in thirty failed before this change and still does: a connection
from the tool to the loopback stand-in did not complete, `faulthandler` showed the client stuck in
`socket.create_connection` with the stand-in idle, and the run ended with a handful of failed
assertions, never a wrong answer. The original code showed the same rate, 1 in 25. The cause was
not found. A rerun was green each time. It was never seen on Windows.

Measured with `python -m coverage run --branch whobreaks.py --self-test`, the self-test covers
100% of the statements and 100% of the branches of the file. No line is excluded, and no line
needs a portal that the stand-in cannot play.

## Requirements

Python 3.9 or newer. Standard library only: `urllib`, `json`, `csv`, `re`, `ssl`, `os`, `io`,
`argparse`, `datetime`, `getpass`, `html`, `time`, and `http.server`, `threading`, `socket`, `tempfile`,
`shutil`, `email` and `importlib` inside the self-test. It runs on ArcGIS Pro's Python and on a plain `python3`.
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
end to end. That includes the graph files and the diff between two of them, and the two calls that
`--publish` makes, `addItem` and `update`, with a multipart upload that the stand-in reads back
with the `email` package.

## Quick start

```
export WHOBREAKS_PASSWORD='...'
python whobreaks.py --url https://yourorg.maps.arcgis.com --target a1b2c3d4e5f60718293a4b5c6d7e8f90 \
    --username pubadmin
```

A scheduled run that already holds a token sets `WHOBREAKS_TOKEN` instead of passing `--token`,
because other processes on the machine can read the command line.

This is the output of a command that the self-test runs against its first stand-in organization,
on the loopback port that run happened to get, and on a console with the Windows code page. The
self-test itself prints PASS lines only. This text was captured by wrapping `main()` in a copy of
the file while the self-test ran, so the commands are the self-test's own but the capture
script is not in this repository. Ports, timestamps and byte counts change on every run:

```
$ python whobreaks.py --url http://127.0.0.1:62206 --target a1b2c3d4e5f60718293a4b5c6d7e8f90 --token ...
reading http://127.0.0.1:62206
target: a1b2c3d4e5f60718293a4b5c6d7e8f90
query: orgid:REALORG
service url: 127.0.0.1:62206/arcgis/rest/services/sites/featureserver
  11 item(s) from (orgid:REALORG) AND created:[0 TO 1790550925170]
searching the configuration of 11 item(s)
  HARD BREAK 0b000000000000000000000000000000 (Nightly refresh)
  HARD BREAK 5a000000000000000000000000000000 (How sites work)
  MENTION ONLY 7e000000000000000000000000000000 (refresh.py)
  SOFT BREAK ac000000000000000000000000000000 (Site map)
  HARD BREAK da000000000000000000000000000000 (Site board ??)
  MENTION ONLY dc000000000000000000000000000000 (Archive notes)
  HARD BREAK eb000000000000000000000000000000 (Site finder)

7 item(s) reference a1b2c3d4e5f60718293a4b5c6d7e8f90

HARD BREAK   0b000000000000000000000000000000  Nightly refresh [Notebook]
             data: cells/code/source (x1)
             lyr = gis.content.get('a1b2c3d4e5f60718293a4b5c6d7e8f90')
             data: cells/markdown/source (x1)
             The source layer used to be a1b2c3d4e5f60718293a4b5c6d7e8f90.
HARD BREAK   5a000000000000000000000000000000  How sites work [StoryMap]
             resources/draft_x.json: nodes/n-x1/data/itemId (x1)
             a1b2c3d4e5f60718293a4b5c6d7e8f90
             resources/draft_x.json: resources/r-1/data/itemId (x1)
             a1b2c3d4e5f60718293a4b5c6d7e8f90
HARD BREAK   da000000000000000000000000000000  Site board ?? [Dashboard]
             data: datasets/dataSource/itemId (x1)
             a1b2c3d4e5f60718293a4b5c6d7e8f90
HARD BREAK   eb000000000000000000000000000000  Site finder [Web Experience]
             resources/config.json: dataSources/dataSource_1/itemId (x1)
             a1b2c3d4e5f60718293a4b5c6d7e8f90
             resources/config.json: widgets/widget_1/useDataSources/dataSourceId (x1)
             dataSource_1-a1b2c3d4e5f60718293a4b5c6d7e8f90-0
SOFT BREAK   ac000000000000000000000000000000  Site map [Web Map]
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
  b1000000000000000000000000000000 Plot delivery: 33554433 byte item was not fetched, over the 33554432 byte limit, so its configuration was never searched
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

`--depth` follows hard breaks past the first hop. This is the captured output of a command the
self-test runs against its second stand-in organization, on the loopback port that run
happened to get:

```
$ python whobreaks.py --url http://127.0.0.1:62206/wide --target a1b2c3d4e5f60718293a4b5c6d7e8f90 \
    --token ... --depth 3
reading http://127.0.0.1:62206/wide
target: a1b2c3d4e5f60718293a4b5c6d7e8f90
query: orgid:REALORG
service url: 127.0.0.1:62206/arcgis/rest/services/sites/featureserver
  8 item(s) from (orgid:REALORG) AND created:[0 TO 1790550924637]
searching the configuration of 8 item(s)
  HARD BREAK f1000000000000000000000000000000 (Site layers)
  MENTION ONLY f5000000000000000000000000000000 (Site handbook)
  SOFT BREAK f7000000000000000000000000000000 (Site filter board)

3 item(s) reference a1b2c3d4e5f60718293a4b5c6d7e8f90

HARD BREAK   f1000000000000000000000000000000  Site layers [Web Map]
             data: operationalLayers/url (x1)
             http://127.0.0.1:62206/arcgis/rest/services/Sites/FeatureServer/0
SOFT BREAK   f7000000000000000000000000000000  Site filter board [Dashboard]
             data: datasets/filter (x1)
             SITE_SRC = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'
MENTION ONLY f5000000000000000000000000000000  Site handbook [StoryMap]
             metadata: description (x1)
             explains a1b2c3d4e5f60718293a4b5c6d7e8f90

1 hard, 1 soft, 1 mention only, over 7 item(s) searched

3 more item(s) break through a hard break, to depth 3:
HOP 2        f2000000000000000000000000000000  Site viewer [Web Mapping Application]
             loads f1000000000000000000000000000000 (Site layers), first hop f1000000000000000000000000000000
HOP 2        f7000000000000000000000000000000  Site filter board [Dashboard]
             loads f1000000000000000000000000000000 (Site layers), first hop f1000000000000000000000000000000
HOP 3        f3000000000000000000000000000000  Home site [Hub Site Application]
             loads f2000000000000000000000000000000 (Site viewer), first hop f1000000000000000000000000000000
```

The web map names the target only by its service url, and it is still a hard break. Its second
layer points at another url on the same server. The stand-in portal logged no request for
either url.

The filter board only filters on the target, which is a soft break. It also loads the web map,
so it breaks at hop 2 all the same. Two items are left out of the hop list on purpose. Site
notes names the web map in its description, and Handbook board embeds a story that only names
the target. A search that followed every edge would list both of them.

With `--depth 2` the same run lists the two hop 2 items and ends on
`1 more item(s) load one at hop 3. Raise --depth to follow them.` A depth that stops short says
so. Without `--depth` the report is the one above, line for line, with no hop section.

### Snapshots and the diff

`--apply --graph week2.json` writes every edge of the sweep, with the flags that say how far to
trust it. `--compare` prints what changed since an earlier graph. With one file, it compares
that file with the sweep it runs. With two files, it runs offline and makes no request at all.
This is the captured output of the self-test's runs over two graphs of its second organization:

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

## Watching the graph

`--dashboard` writes the graph as one page. It is made from the same edges that `--graph` writes,
and it is written only under `--apply`, like every other file this tool makes.

```
python whobreaks.py --url https://yourorg.maps.arcgis.com --target a1b2c3d4e5f60718293a4b5c6d7e8f90 \
    --depth 3 --graph week2.json --dashboard board.html --compare week1.json --apply
```

This is the text of the page that `render_dashboard` makes for a graph of the second stand-in
organization, with its tags removed, and with a graph of the week before for `--compare`. The ids
are the self-test's stand-ins:

```
Who breaks if a1b2c3d4e5f60718293a4b5c6d7e8f90 goes
Complete: every item was enumerated and read.
edges | 8: 5 hard, 1 soft, 2 mention
Breaks if the target is deleted, to depth 3
hop | item | title | type | loads
1 | f1000000000000000000000000000000 | Site layers | Web Map | a1b2c3d4e5f60718293a4b5c6d7e8f90
2 | f2000000000000000000000000000000 | Site viewer | Web Mapping Application | f1000000000000000000000000000000
2 | f7000000000000000000000000000000 | Site filter board | Dashboard | f1000000000000000000000000000000
3 | f3000000000000000000000000000000 | Home site | Hub Site Application | f2000000000000000000000000000000
Reference the target without breaking
SOFT BREAK | f7000000000000000000000000000000 | Site filter board | Dashboard
MENTION ONLY | f5000000000000000000000000000000 | Site handbook | StoryMap
Changed since the graph taken 2026-09-20T14:00:00Z
added | HARD BREAK | f3000000000000000000000000000000 | f2000000000000000000000000000000
removed | HARD BREAK | f4000000000000000000000000000000 | f2000000000000000000000000000000
Items that could not be read
Every item was read.
```

The page also lists every edge of the sweep, worst class first. It follows the same rules as the
console report. A break travels along hard edges only, so the filter board is listed under
`Reference the target without breaking` and also at hop 2, because it loads the web map. A graph
that is not complete, or that carries a caveat such as a token that is not an administrator's,
puts `NOT COMPLETE` and every reason in a box at the top, and the unread items in their own
table. The empty table under the hard breaks then says that no hard reference was found and that
the sweep is not an all clear. The green banner is for a graph with neither.

With two graph files, the page is made offline from the second one and the first one is the
earlier graph. This makes no request to the portal. A graph file holds item ids and no titles, so
this page lists ids only. A page made from a sweep also carries the title and type of each item.

```
python whobreaks.py --compare week1.json week2.json --dashboard board.html --apply
```

What the page is, and what the self-test checked about it:

- **One file with no script and no outside resource.** The page has an inline style and nothing
  else. A `Content-Security-Policy` element says `default-src 'none'` and allows inline style, so
  a browser that honours it loads nothing the file did not carry. The self-test looks for these
  elements in the text. It did not open the page in a browser.
- **Every value is escaped.** A title is typed by whoever owns the item. The self-test puts
  `<script>alert(1)</script>`, a quote-and-tag type, a markup target and a markup url on the
  page, and finds each of them as text. A control character becomes `?`, as on the console, and a
  letter outside ASCII becomes a numeric reference, so the file is pure ASCII and reads the same
  on every host.
- **It reloads itself.** A `refresh` element asks the browser to load the file again every 300
  seconds (`DASH_REFRESH_S`). A page left open then shows the next sweep without a key press. The
  self-test checks that the element is there. A browser reloading was not exercised.
- **It is replaced whole.** The page is written to `board.html.part` and renamed over the old
  one. The self-test makes the rename fail, and the old page is still there and the `.part` file
  is gone. Python documents `os.replace` as atomic on POSIX and makes no such promise for
  Windows. The self-test did not race a reader against the rename on either host.
- **It cannot overwrite what it reads.** `--dashboard` naming the same file as `--out`, as
  `--graph` or as a `--compare` input is refused with exit 64, whatever the case of the name,
  before the first request.
- **It cannot be abbreviated.** `--dash` is refused, as `--ap` and `--app` are.

Sources for what the page relies on:

- Python `os.replace`: "If successful, the renaming will be an atomic operation (this is a POSIX
  requirement)." https://docs.python.org/3/library/os.html#os.replace
- A Content-Security-Policy in a `<meta http-equiv>` element, `default-src` as the fallback for the
  other fetch directives, and `style-src` governing inline `<style>`.
  https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy
- `<meta http-equiv="refresh" content="300">` reloads the page after that many seconds, and a
  short interval is an accessibility risk. https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/meta/http-equiv

## Publishing the graph

`--publish` puts the graph of the sweep in the portal as one item. It is the same document that
`--graph` writes. It is sent only under `--apply`.

```
python whobreaks.py --url https://yourorg.maps.arcgis.com --target a1b2c3d4e5f60718293a4b5c6d7e8f90 \
    --depth 3 --publish --apply
```

Run bare, it adds a new item and prints the id. Pass that id to the next run, and the run
replaces the file of that item instead of adding another one. This is the captured output of the
self-test's own runs against its two stand-in organizations. Only the end of each report is shown:

```
$ python whobreaks.py --url http://127.0.0.1:63274 --target a1b2c3d4e5f60718293a4b5c6d7e8f90 --token ... --publish
...
Do not read this run as permission to delete. It searched what it could read.

Check only. Nothing was published. Re-run with --apply.

$ python whobreaks.py --url http://127.0.0.1:63274 --target a1b2c3d4e5f60718293a4b5c6d7e8f90 --token ... --publish --apply
...
added item ab000000000000000000000000000001, 1639 bytes. Pass --publish ab000000000000000000000000000001 next time to replace it.

$ python whobreaks.py --url http://127.0.0.1:63274/wide --target a1b2c3d4e5f60718293a4b5c6d7e8f90 --username pubadmin --publish ab000000000000000000000000000001 --apply
...
replaced item ab000000000000000000000000000001, 1570 bytes.

$ python whobreaks.py --url http://127.0.0.1:63274/wide --target a1b2c3d4e5f60718293a4b5c6d7e8f90 --token ... --publish a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2 --apply
...
error: item a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2 was not overwritten: its type is Web Map, not Code Attachment
```

The first two runs exit 2 and the third exits 1. The third signs in with `--username`, and the
password comes from `WHOBREAKS_PASSWORD`. Publishing does not change the exit code of the
sweep. The next sweep of the same organization finds the published graph in the search. Its
title, description and file all name the target, so it would be read as a mention and a
scheduled run would exit 1 for ever. The sweep leaves it out instead, when four things hold:
its type is `Code Attachment`, it carries the `whobreaks` tag and a `whobreaks-<target>` tag, it was read without a problem, and its file
is a graph of the target its second tag names. It then prints `not searched, because each is a
graph this tool published` and the id, so the omission is visible. The graph of another target
lists every item of its sweep, this sweep's target among them, so it is left out as well. An item that only borrows the
tag and the type is still searched. The fourth run exits 2, because a failed publish is an
incomplete job. The item that the first `--apply` run made was this, on the stand-in: owned by
the signed-in user, type `Code Attachment`, type keyword `Desktop`, title
`whobreaks graph a1b2...8f90` (ids shortened here), tags `whobreaks` and `whobreaks-a1b2...8f90`,
and a file named `whobreaks-graph-a1b2...8f90.json`.

What it does, and what the self-test checked about it:

- **Nothing is written without `--apply`.** Without it the run prints the line above, sends no POST
  and adds no item. The sweep itself still sends read requests (`portals/self`, the search, the
  target item, and each item's `data` and `resources`), and `--username` still posts the password
  to `generateToken`. A prefix of the flag, such as `--pub`, is refused, as `--ap` is.
- **It asks the portal who the token belongs to.** `community/self` gives the user name, which
  goes into the `addItem` path, quoted. A name with a `#` in it would otherwise end the url there.
  A token that names no user is refused. `--publish` with no token, no `--username` and no
  `WHOBREAKS_TOKEN` is a usage error before any request.
- **It never overwrites an item that is not its own.** Before an `update` it reads the item, and
  it replaces the file only when the item is the signed-in user's, is a `Code Attachment`, and
  carries both tags, `whobreaks` and `whobreaks-<target>`. The self-test points `--publish` at
  another user's item, at a web map, and at a graph tagged for another target. It checks that each
  is refused with its reason, that no update request was sent, and that the item still holds its
  own file.
- **It reads the item back.** The portal answers HTTP 200 with an error object, and a file item
  can be written and still hold other bytes. After the call the tool reads the item's data and
  compares it with what it sent. The self-test has the stand-in answer with an error, with
  `success: false`, with no id and with a stored file that differs by one byte, and each is a
  failure with exit 2. In the error case the portal echoes the token in its message and the
  tool's message does not.
- **It never shares the item.** `addItem` is sent with no sharing parameter, and the tool calls no
  share operation. The self-test checks that no request path contains `share`. The Add Item page
  says: "The item is flagged as private by default, and will not be shared with any group. This
  can be changed using the Share Item operation." So a new item is private to its owner until the
  owner shares it, and that is the owner's decision. The stand-in records `access` as
  `private`, as that page says, but it was not asked what a real portal does.
- **It sends the type keyword its item type needs.** The Items and item types page lists
  `Code Attachment` with a required keyword: one of Web Mapping Application, Explorer, Desktop,
  Mobile or iPhone, and for a Web Mapping Application one more of JavaScript, Flex or Silverlight.
  `addItem` sends `typeKeywords=Desktop`, the one that asks for nothing further, and the stand-in
  refuses a `Code Attachment` without one of the five, with exit 2 from the tool. The `update`
  call sends neither the type nor the keywords, because an update must not change them. No real
  portal has accepted this item, so whether `Desktop` is the keyword a portal expects for a JSON
  file is not proven.
- **The file carries no credential and no item title.** It is the graph file: ids, edge classes,
  the portal url, the query, the target, and the flags and reasons that say how far to trust it. The description
  of the item says `NOT COMPLETE` when the sweep was not.
- **A failed publish does not lose the local files.** `--out`, `--graph` and `--dashboard` are
  written first. If the publish then fails, the run exits 2 with the message.

The page that `--dashboard` makes is not published. The Esri list of file types that can be added
as items names no HTML file type, so the page stays a local file. A web server or a file share is
the place for it.

Sources for what `--publish` relies on. All of these pages were read for this change. The item type is
the nearest documented one, and Limits says what is not proven:

- Add Item: `POST .../content/users/<user>/addItem`, only `type` required, a `file` must be sent
  as a multipart request, the response has `success`, `id` and `folder`, a file name must be
  unique in the user's folder, and the new item is flagged private by default. https://developers.arcgis.com/rest/users-groups-and-items/add-item/
- Update Item: `POST .../content/users/<user>/items/<id>/update`, parameters not given are
  unaffected, and the response has `success` and `id`.
  https://developers.arcgis.com/rest/users-groups-and-items/update-item/
- Self: `GET .../community/self` describes the user the token identifies, with its `username`.
  https://developers.arcgis.com/rest/users-groups-and-items/self/
- Item: `owner`, `type`, `tags` (an array of strings) and `access`, and Item Data, which streams
  a file item's file with a content type derived from its MIME type.
  https://developers.arcgis.com/rest/users-groups-and-items/item/ and
  https://developers.arcgis.com/rest/users-groups-and-items/item-data/
- Items and item types: "Code Attachment" is a file item, described as "the sample code
  associated with a code sample application", and its keywords are required, as listed above. A
  generic JSON file is not an item type there.
  https://developers.arcgis.com/rest/users-groups-and-items/items-and-item-types/
- Supported items: lists no `.html` file type. https://doc.arcgis.com/en/arcgis-online/reference/supported-items.htm

## Usage

| Flag | Default | What it does |
|---|---|---|
| `--url` | none | Portal url. Required. Must start with `https://` or `http://`, and must carry no user name, password, query string or fragment. |
| `--target` | none | The item id you are about to delete. Required, and 32 hexadecimal characters. |
| `--query` | the signed-in org | Search query to sweep. The created-date slices are ANDed onto it. A sweep with a `--query` that finds nothing exits 2, not 0, because the tool cannot tell a query that covers the organization from one that leaves the referencing item out. |
| `--token` | none | An existing portal token. Other processes can read it on the command line, so a scheduled run sets `WHOBREAKS_TOKEN` instead. |
| `--username` | none | Sign in as this user. The password comes from `WHOBREAKS_PASSWORD` or an unechoed prompt. Refused with an `http://` url, except on this machine's loopback address. |
| `--out` | none | File to write the report to. |
| `--format` | `json` | `json` or `csv`. |
| `--num` | `100` | Rows per search page to ask for. Clamped to 100, which is all the server gives. |
| `--max-slices` | `4096` | Give up after this many created-date slices rather than splitting for ever. |
| `--depth` | `1` | Follow hard breaks this many hops from the target. 1 lists the direct references only. |
| `--graph` | none | File to write the edge list of the sweep to, as JSON. Needs `--apply`. |
| `--dashboard` | none | File to write the dependency graph to as one static HTML page. Needs `--apply`. Made offline from the second file when `--compare` has two. Refused when it names the same file as `--out`, `--graph` or a `--compare` file. |
| `--publish` | none | Put the graph in the portal as one `Code Attachment` item. Needs `--apply`, and a token or `--username`. With no value it adds an item and prints its id. With an item id it replaces the file of that item, and refuses an item that is not this user's, not a `Code Attachment`, or not tagged for this target. Not allowed with two `--compare` files. |
| `--compare` | none | One graph file: print the edges added and removed since it. Two graph files: compare them offline, with no `--url`. |
| `--insecure` | off | Skip TLS verification, for an Enterprise portal behind an internal CA. Refused together with `--username`. |
| `--apply` | off | Write the `--out` report, the `--graph` file and the `--dashboard` page, and make the `--publish` calls. Without it nothing is written. The sweep still sends its read requests, and `--username` still posts the password to `generateToken`. |
| `--self-test` | off | Run the offline assertions and exit. |

`--apply` writes the report, the edge list and the page to local files. With `--publish` it also adds
one item to the organization, or replaces the file of an item that this tool made. There
is no flag that deletes, moves or shares anything. Every parser option is spelled out in full,
because `allow_abbrev` is off: a prefix such as `--ap`, `--dash` or `--pub` is refused and exits 64. The password is never a flag either: `argv` is readable by every process on the
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
  url shape, and reports none of them. The url shape counts only on a portal item url, one with
  `/content/items/` in it, so a service that is itself called `Thumbnail` is still a reference.
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
- **A body that the connection cut short is not a clean item.** Python's `HTTPResponse.read(n)`
  does not raise when the connection closes before `Content-Length` bytes arrive, so half a
  configuration was searched as if it were the whole of it. The tool now compares the bytes it
  got with `Content-Length`, and the item is unread. The self-test drives this over a real
  socket, with a stand-in that promises 5,000 bytes, sends 35 and closes.
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
| 0 | nothing references the target, every item was enumerated and read, an administrator's token was sent, no `--query` was given, and the target itself was among the items searched. The last three are the conditions under which an empty result means an empty organization |
| 1 | something references it, listed worst first |
| 2 | the sweep could not be completed, so nothing can be concluded. Also an empty result with no token, with a token whose user is not an administrator, with a `--query`, or with a target that was not among the items searched. An item was unreadable or read in part, the target item itself was unreadable, a search slice sat on the 10,000 ceiling, or a search page held a result with no usable id. Also when `--apply` could not write a file it was asked for, the page included, when `--publish` was refused or could not be confirmed by reading the item back, and when the tool met a failure it did not foresee |
| 64 | usage error, including a flag that is unknown or has a bad value, a graph file that is missing, is not a graph, or covers a different scope, and an `--out`, `--graph` or `--dashboard` that is a folder or that names one file twice, and a `--dashboard` that names a graph file the run reads. Also a `--publish` with no token, with a value that is not an item id, or with two `--compare` files |

`--depth` and a one-file `--compare` do not change the exit code of the sweep. A two-file
`--compare` exits 0 when no edge changed and 1 when an edge did. It exits 2 when either graph is
incomplete or carries a caveat, whatever the diff says, because an edge missing from that graph is not proof. It also
exits 2 when an edge is unknown. That is what a deleted map leaves behind in the app that still
loads it.

Exit 2 beats exit 1 on purpose. Somebody who sees 1 goes and looks at the items. Somebody who
sees 0 deletes the item, so 0 has to mean the sweep was complete, not that it finished.

Four more conditions block exit 0 and nothing else. A sweep with no token sees only public
items, so a private app that uses a public service would not be seen. A sweep whose token
belongs to a user who is not an administrator cannot search another user's private items, and
looks exactly like a clean sweep. A sweep with a `--query`
covers only what the query matches, and the tool cannot tell a query that names the whole
organization from one that leaves the referencing item out. A sweep whose items do not include
the target itself has been pointed at the wrong place or cannot see where the target lives. In
each case an empty result exits 2 and the output lists the reason. A reference that was found
still exits 1, because it is true whatever else was missed. The same reasons are in the `caveats`
list of the `--out` document and of the `--graph` file, next to `complete`. `complete` says every
item was enumerated and read. A caveat says the sweep could not see everything anyway. The
dashboard shows its green banner only when `complete` is true and the list is empty, and a
graph with a caveat gets the `NOT COMPLETE` box with the reasons in it. A two-file `--compare`
exits 2 for such a graph, and the description of a published item says `NOT COMPLETE`. A graph
file from before this field has no `caveats` and is read as having none. Leave out `--query` and
sign in as an administrator to get exit 0.

The role comes from `portals/self`. Esri documents `user.role` there as `org_admin`,
`org_publisher` or `org_user`, and says the user object is returned only when a token is passed
in. Any role but `org_admin` is a caveat, and so is a portal that reports none. A custom role is
not in that list, so it is a caveat too, even if the portal gives it administrator privileges.
https://developers.arcgis.com/rest/users-groups-and-items/portal-self/

## Output

`--format json` writes the findings, the unread items and the reasons the enumeration might be
incomplete into one document, because those three have to be read together. `--format csv`
writes the nine finding columns: `id`, `title`, `type`, `owner`, `kind`, `source`, `path`,
`count`, `excerpt`.

A spreadsheet runs a cell that starts with `=`, `+`, `-`, `@`, a tab or a carriage return as a
formula, and an item owner writes the title and the text an excerpt is cut from. `--format csv`
puts one apostrophe in front of any text cell that starts with one of those six characters, in
every column but `count`, which stays a number. OWASP lists the same characters and names the
single-quote prefix as the mitigation:
https://owasp.org/www-community/attacks/CSV_Injection. Microsoft documents that an apostrophe
typed before an entry makes Excel store it as text:
https://support.microsoft.com/en-us/office/keeping-leading-zeros-and-large-numbers-1bf7b935-36e1-4985-842f-5dfa51f85fe7.
The JSON report keeps the text as the portal holds it. The tool never reads a CSV back:
`--compare` takes graph files only, and the self-test asserts that a CSV given to it is refused
with exit 64. A program that reads the CSV must strip the one leading apostrophe itself. A title
that began with an apostrophe in the portal cannot then be told from a guarded one.

Excerpts are scrubbed on the way out. A secured feature service url inside a web map carries a
token in its query string, and that excerpt is the most useful line in the report, so it is
written with the token replaced rather than dropped. The text is scrubbed before the excerpt is
cut from it, so a token whose key is outside the excerpt is still replaced. `access_token=` is
replaced as well as `token=`, and so are `client_secret=`, `secret=` and an Azure `sig=`, and so is a value written as `token="..."`, `password = '...'` or
`"token": "..."`. The self-test asserts that no credential reaches stdout or either file.

The console replaces every control character in a line with `?`. That includes the title, the
excerpt and an error message that quotes the portal. An item owner can put `ESC [2K` and a
carriage return in a title, and on the console that rewrote the report line above it. The JSON
and the JSON file keeps the title as the portal holds it. The CSV file guards it as above.

With `--depth` above 1, the JSON report also carries `depth`, `beyond` and a `transitive` list.
Each row has the item's `id`, `title` and `type`, its `hop`, the first-hop item as `via`, and
the item it `loads`. Every JSON report names the `service_url` it searched for, or null.

The `--dashboard` page holds the same edges as the `--graph` file, with the title and type of each
item when it comes from a sweep. See "Watching the graph" above.

The item that `--publish` makes holds the same document as the `--graph` file, with its keys in
order. See "Publishing the graph" above.

The `--graph` file holds `edges`, each with `from`, `to` and `kind`, sorted by `from` and then
`to`. The flags are next to them: `complete`, `caveats` (the reasons an empty sweep is not an all
clear), `unread` (the ids of the items that could not be
read), `items` (the ids of every item searched) and `unproven`. The file also records `url`, `query`, `target`, `service_url`, `scanned`
and `taken`. The self-test asserts that no credential reaches this file either.

## Limits

- It searches the organization you can see. An anonymous sweep covers public items, and a sweep
  as a user who is not an administrator covers what that user can search. Both exit 2 when they
  find nothing. The answer is honest about the query, not about the organization.
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
- The dashboard is a set of tables, not a drawing of the graph. Each table is sorted and the full
  edge list is on the page, so a sweep with tens of thousands of edges makes a long page. It was
  not timed on one.
- The dashboard has no history of its own. It shows the graph and the diff against one earlier
  graph. A series of weeks needs a series of graph files, and the diff is against the one you pass.
- A page made from graph files has ids and no titles, because the graph file stores none. Run a
  sweep with `--dashboard` to get titles.
- A `refresh` element reloads the file, not the portal. The page is as current as the last sweep
  that wrote it. MDN warns that a short refresh interval is an accessibility problem, so the
  interval is 300 seconds and it is a constant in the file, not a flag.
- Python gives no atomic promise for `os.replace` on Windows. If the replace fails there for any
  reason, the run exits 2 with a message and the old page stays. The self-test makes the rename
  fail with an error it raises itself, not with a real viewer holding the file.
- It does not delete, re-point or unshare anything. Repointing what it finds is `agol-relink`'s
  job.
- `--publish` was run only against the stand-in portal in the self-test. The REST pages above
  describe the calls and the response shape, and the stand-in answers in that shape. No real
  ArcGIS Online or Enterprise portal was asked to take the file.
- The type `Code Attachment` is the nearest documented file item, and its page names no file
  extension. A portal may refuse a `.json` file for it, or hold it under another type. If it does,
  the response is an error, the run exits 2 and the item is not made. If a portal accepts the
  upload and serves other bytes, the read-back catches it and the run exits 2, but the item it
  made stays in the portal, and the message names its id.
- The Update Item page does not say in words that a new file replaces the old one. It says that
  parameters not given are unaffected. The read-back is what checks it, and it was only checked
  against the stand-in.
- The read-back reads at most the size of the file, plus one byte. A portal that serves
  different bytes for any reason, such as a re-encoded file, is a failure here, not a pass.
- The item is a point-in-time copy. It does not hold the diff that `--compare` prints, and the
  tool keeps no series of weeks.
- The graph holds item ids and the portal url, and no titles. It still shows which items load
  which. Whoever can open the item sees which items load which, so share it as carefully as the
  content map it is made from.
- The publish uses the token's own user. An administrator who wants the item under another
  account signs in as that account.
- Group membership, folder structure and sharing are out of scope. This tool answers one
  question: what would break.
- A sweep covers items created up to the clock of the machine that runs it, so an item created
  after that moment, or dated in the future, is not searched. The count still matches, so the run
  can exit 0.
- Only `--dashboard` is checked against the `--compare` file. `--out` or `--graph` naming it
  replaces the baseline with a report or a new graph, and the next run exits 64 with `is not a
  whobreaks graph file`.
- `--out` and `--graph` are written in place, not by rename like the dashboard. A write that fails
  midway, such as a full disk, leaves a truncated file, and a truncated `--graph` baseline makes
  the next `--compare` exit 64 until the file is written again.
- A scheduled `--graph X --compare X` overwrites the last complete baseline with the graph of a
  sweep that could not read everything, and the next diff then shows mostly unknown edges. Keep
  the last complete graph under another name.
- `diff_graphs` checks that the item an edge names was in both sweeps, but not that the item
  holding the reference was. An app that drops out of the sweep without being deleted, because the
  sweeping account lost sight of it, prints as a removed reference.
- The CSV guard covers the six characters above and nothing else. OWASP also lists a line feed
  and the full-width `=`, `+`, `-` and `@` as leading characters. The guard does not prefix those.
  Whether Excel shows the apostrophe after it opens a CSV was not tested; no spreadsheet runs
  in the self-test. The self-test checks the bytes of the file with the `csv` module.
- `--apply` with a two-file `--compare` and no `--dashboard` is accepted and writes nothing, with
  no message.
- `--out`, `--graph` and `--dashboard` are compared without regard to case on every host. On Linux
  `report.json` and `Report.json` are two files, and the run refuses them as one with exit 64.
- A published graph is recognised by its type, its tags and its file, and not by its owner. Anyone
  who can add an item can copy those three markers and hide that item, and every reference in its
  other resources, from the sweep. Another user's copy of a graph for the same target is left out
  as well.
- `json.loads` keeps the last of two duplicate keys in a body, so a reference under the first copy
  is not seen.
- The dashboard has no age marker. If the scheduled sweep starts to fail with exit 2, the page
  keeps showing the last good graph, with the graph-taken date as the only clue.
- The published item is the graph as a JSON file, so watching it in the portal means opening that
  file. The HTML page is a local file only, and `--publish` does not upload it.
- Only resources whose names end in `.json` are read. Text resources such as `readme.txt`,
  `app.js` or `metadata/metadata.xml` are never searched, and the item is not marked unread for
  them.
- The scrubber does not match a percent-encoded key such as `token%3D`, so an encoded token inside
  the 1 KB window around the target id can reach an excerpt.
- Only a value after a key name the scrubber knows is replaced. A bare `key=VALUE`, an Esri API
  key that starts `AAPK`, `credentials:` and `sessionid=` values next to the target id are printed
  and written as they are.
- Item titles are not scrubbed, so a title that holds a credential-shaped string such as
  `access_token=...` is printed and written as it is.
- `--username` sends the password to `generateToken` before the `--apply` check. That call signs
  in and writes nothing to the organization, but it is a POST.
- `--token` with an `http://` url on a host that is not loopback sends the token in the query
  string in clear. Only `--username` is refused on `http://`.
- `urllib` follows a redirect from the portal to another host with the token still in the query
  string, and the tool does not stop it. Trust the portal url and any proxy in front of it.
- A search-result id and a resource name from an item are put in the request path as they are. A
  resource name that holds `../` can make a read of another endpoint on the same portal host with
  your token. It is a GET, and never another host.
- With `--username` and no `WHOBREAKS_PASSWORD`, `getpass` reads the console and not stdin, so on
  Windows a piped or scheduled run with no console waits for a password that never comes. Set
  `WHOBREAKS_PASSWORD` for unattended runs.
- A token made by `--username` lasts 60 minutes. A sweep that runs longer reads every later item
  as unread and exits 2 instead of finishing. Pass a longer-lived token in `WHOBREAKS_TOKEN` for a
  big organization.
- Esri's Item Data page does not say what `/data` returns for an item with no data file, such as a
  feature service. If a real portal answers those with an error, every such item is unread and
  every sweep exits 2. That is a safe failure, and it was not tried on a real portal.
- The description of `f1729/whobreaks` in "Related" was checked against that repository's GitHub
  description only, not against its code.

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
- [deadwidget](https://github.com/uhsear/deadwidget) - the finer grain inside one Experience Builder app: widgets bound to a layer or sublayer id that no longer exists

`phaakma/arcgis_item_graph` is described on GitHub as "ArcGIS Item Dependency Graphs". It was
found by `gh search repos` for this change and was not run or read, so nothing here compares
against it. Esri's own Item Graph is named at the top of this file.

A different project of the same name, `f1729/whobreaks`, does impact analysis on source code for AI
coding tools. It does not read ArcGIS items.
