# whobreaks

Report every item that references the one you are about to delete, including the Experience
Builder drafts the dependency graph never opens.

You delete a service nobody appears to use. The portal asked for one confirmation, the
dependency graph was empty, and nothing in the item page said anybody depended on it. A week
later a public Experience Builder app shows an empty map. The link between the two was a line in
a widget config that no portal screen ever displayed, and the delete is not reversible.

`arcgis.apps.itemgraph` is the obvious tool, and it does more than most people think.
`create_dependency_graph` accepts `include_reverse`, `ItemNode.contained_by()` and
`ItemNode.required_by()` both exist, and that traversal is far less code than this one. It is
built on `item.related_items(direction="reverse")`, which returns **the relationships the portal
has registered**. It never opens `/content/items/{id}/data` or `/content/items/{id}/resources`.
An item id that lives only inside an Experience Builder widget config, a dashboard dataset
binding, a StoryMap draft resource or a notebook string literal is invisible to it in both
directions, because no relationship was ever registered for it. That gap is the whole tool.

```
$ python whobreaks.py --self-test
whobreaks self-test: no portal, no credentials, one loopback server
--------------------------------------------------------------------
PASS  a truncated target is REFUSED, not searched for, because a short string matches more and the answer would be about another item  <-- pinned defect
PASS  the id inside an Experience Builder data source id matches, because a dash ends an id  <-- pinned defect
PASS  the same 32 characters INSIDE a longer id do not match  <-- pinned defect
PASS  two overlapping occurrences are BOTH found, because the scan advances by one and not by the length of what it matched
...
PASS  and so is the older /info/thumbnail.png, which has no directory after it for the other test to see  <-- pinned defect
PASS  so a thumbnail url is not a reference at all  <-- pinned defect
PASS  a url is a hard break under a widget key nothing here recognises, because url alone says the value is loaded  <-- pinned defect
PASS  a popup reference is a soft break, even under a layer, because the innermost key is what the value is for  <-- pinned defect
PASS  a filter on a layer is a soft break and not a hard one  <-- pinned defect
PASS  prose caps the class DOWNWARDS, so an id typed into a layer's description is a mention and not a hard break  <-- pinned defect
PASS  the same string in a MARKDOWN cell is a mention, because prose cannot break  <-- pinned defect
PASS  an Experience Builder config yields two hard breaks: the data source and the widget binding  <-- pinned defect
PASS  and both say which resource they came from, because the published app does not hold this config  <-- pinned defect
PASS  a StoryMap draft resource yields the node and the resource that load the item  <-- pinned defect
PASS  a notebook yields a hard break for the code cell and a mention for the markdown one  <-- pinned defect
PASS  a web map that filters on the item and mentions it in a popup is soft and mention, never hard  <-- pinned defect
PASS  an item whose only occurrence is the target's THUMBNAIL yields NOTHING, which is the 300 item false positive this tool exists to avoid  <-- pinned defect
PASS  an item referencing a LONGER id that contains the target is not a reference  <-- pinned defect
PASS  an id used as a dictionary KEY is found, which is how Experience Builder names a layer data source  <-- pinned defect
PASS  an item whose /data is not JSON is still searched, as prose  <-- pinned defect
PASS  and the token sitting beside the id in that very url is redacted out of the excerpt  <-- pinned defect
PASS  the fold joins two findings only when the file, the path AND the class all match  <-- pinned defect
...
PASS  the clone with the thumbnail is not a reference at all  <-- pinned defect
PASS  the item whose fetch RAISED is counted as unread, not as clean  <-- pinned defect
PASS  which exits 2 even though references were found, because 0 and 1 are answers and this run has none  <-- pinned defect
PASS  the console reports items in id order however the search returned them, so two runs of the same sweep read the same  <-- pinned defect
...
PASS  and an incomplete run says so in words, not only in its exit code  <-- pinned defect
PASS  and the report lists what BREAKS before what only mentions, then settles ties by id, whatever order the findings arrived in  <-- pinned defect
PASS  a long unread list prints its first TWENTY rows and then says how many it held back, so the tail alone cannot carry it  <-- pinned defect
PASS  a page size of 1000 is clamped to 100 before it is sent, because the server clamps it silently  <-- pinned defect
PASS  exactly 10,000 is the CAP and never a total  <-- pinned defect
PASS  a short page advances by the ten rows it got, not by the hundred it asked for  <-- pinned defect
...
PASS  a slice that reported the ceiling on a LATER page is split, because the largest count any page gave is the only safe one to believe  <-- pinned defect
PASS  a slice still at the ceiling one day wide cannot be split again and is reported unproven  <-- pinned defect
PASS  its rows are KEPT all the same, because an item this tool can still search beats one it never sees  <-- pinned defect
PASS  the two halves meet at one millisecond past the midpoint, so no item falls in both and none falls in neither  <-- pinned defect
PASS  a portal that reports the ceiling for every query stops at the slice budget instead of splitting for ever  <-- pinned defect
...
PASS  an item with sixty json resources is read to the default fifty, so one StoryMap cannot turn a sweep into an afternoon
PASS  the default opener verifies the certificate  <-- pinned defect
PASS  and the password really did travel in the POST body, not in the query string  <-- pinned defect
PASS  a url carrying a context path the portal does not serve is refused, not read as an empty org  <-- pinned defect
PASS  a resource that is gone by the time it is fetched raises, which the reader turns into one unread item  <-- pinned defect
PASS  a real HTTP 500 raises rather than returning an empty body that would read like a clean item  <-- pinned defect
PASS  and the token that call carried is not in the message  <-- pinned defect
PASS  and the half that arrived is no longer JSON, so it comes back as text to be searched as prose  <-- pinned defect
PASS  its config comes from a RESOURCE, which is the file the dependency graph never opens  <-- pinned defect
PASS  an item too large to fetch is a problem too, so it is unread rather than clean  <-- pinned defect
PASS  a sweep with two unreadable items exits 2, whatever it found  <-- pinned defect
PASS  the cloned item carrying the target's thumbnail is not in the report at all  <-- pinned defect
PASS  the token never reaches stdout  <-- pinned defect
PASS  a complete sweep that found a reference exits 1  <-- pinned defect
PASS  a sweep that finds nothing, and read everything it found, is the only run that exits 0  <-- pinned defect
...
PASS  no credential reaches the file on disk  <-- pinned defect
PASS  and the json comes out with its keys in order, so two runs a week apart diff to the findings that changed  <-- pinned defect
PASS  with no blank line between rows: the defect DOUBLES the carriage return, it does not add a whole empty line, so that is the sequence asserted on  <-- pinned defect
...
--------------------------------------------------------------------
348 assertions, 0 failed
```

## Requirements

Python 3.9 or newer. Standard library only: `urllib`, `json`, `csv`, `re`, `ssl`, `os`, `io`,
`argparse`, `datetime`, `getpass`, `time`, and `http.server`, `threading`, `socket`, `tempfile`
and `shutil` inside the self-test. It runs on ArcGIS Pro's Python and on a plain `python3`.
`arcpy` is not used and the `arcgis` package is not needed.

```
git clone https://github.com/uhsear/whobreaks.git
python whobreaks.py --self-test
```

`--self-test` needs no portal and no credentials, so you can check the tool before you point it
at an organization. The matcher, the classifier and the traversal run over canned item data. The
HTTP layer runs against a stand-in portal this tool starts on `127.0.0.1`: a real socket, a real
sign-in POST, a real HTTP 500 on one item, real Experience Builder and StoryMap resources, and
the whole command line end to end.

## Quick start

```
export WHOBREAKS_PASSWORD='...'
python whobreaks.py --url https://county.maps.arcgis.com --target a1b2c3d4e5f60718293a4b5c6d7e8f90 \
    --username gis_admin
```

```
reading https://county.maps.arcgis.com
target: a1b2c3d4e5f60718293a4b5c6d7e8f90
query: orgid:7c41f0a9b2e84d6390af5b1c8e2d7043
  13 item(s) from (orgid:7c41f0a9b2e84d6390af5b1c8e2d7043) AND created:[0 TO 1789599598647]
searching the configuration of 13 item(s)
  MENTION ONLY cln0000000000000000000000000000c (Unrelated map)
  HARD BREAK dsh00000000000000000000000000002 (Permit board)
  HARD BREAK exb00000000000000000000000000001 (Permit finder)
  SOFT BREAK map00000000000000000000000000005 (Permit viewer)
  HARD BREAK nbk00000000000000000000000000004 (Nightly refresh)
  HARD BREAK sec00000000000000000000000000008 (Secured viewer)
  HARD BREAK sto00000000000000000000000000003 (How permits work)
  MENTION ONLY txt00000000000000000000000000009 (Refresh script)

8 item(s) reference a1b2c3d4e5f60718293a4b5c6d7e8f90

HARD BREAK   dsh00000000000000000000000000002  Permit board [Dashboard]
             data: widgets/datasets/dataSource/itemId (x1)
             a1b2c3d4e5f60718293a4b5c6d7e8f90
             data: widgets/itemId (x1)
             a1b2c3d4e5f60718293a4b5c6d7e8f90
HARD BREAK   exb00000000000000000000000000001  Permit finder [Web Experience]
             resources/config.json: dataSources (x1)
             dataSource_1-a1b2c3d4e5f60718293a4b5c6d7e8f90-0
             resources/config.json: dataSources/dataSource_1-a1b2c3d4e5f60718293a4b5c6d7e8f90-0/itemId (x1)
             a1b2c3d4e5f60718293a4b5c6d7e8f90
             resources/config.json: widgets/widget_1/useDataSources/dataSourceId (x1)
             dataSource_1-a1b2c3d4e5f60718293a4b5c6d7e8f90-0
             resources/config.json: widgets/widget_1/config/initialMapDataSourceID (x1)
             a1b2c3d4e5f60718293a4b5c6d7e8f90
HARD BREAK   nbk00000000000000000000000000004  Nightly refresh [Notebook]
             data: cells/code/source (x1)
             gis.content.get('a1b2c3d4e5f60718293a4b5c6d7e8f90')
             data: cells/markdown/source (x1)
             This replaced a1b2c3d4e5f60718293a4b5c6d7e8f90 in 2021.
HARD BREAK   sec00000000000000000000000000008  Secured viewer [Web Map]
             data: operationalLayers/url (x1)
             ...ps://org.example/sharing/rest/content/items/a1b2c3d4e5f60718293a4b5c6d7e8f90/data?token=[redacted]&f=json
HARD BREAK   sto00000000000000000000000000003  How permits work [StoryMap]
             resources/draft_x.json: nodes/n-1/data/map/itemId (x1)
             a1b2c3d4e5f60718293a4b5c6d7e8f90
             resources/draft_x.json: resources/r-1/data/itemId (x1)
             a1b2c3d4e5f60718293a4b5c6d7e8f90
SOFT BREAK   map00000000000000000000000000005  Permit viewer [Web Map]
             data: operationalLayers/definitionExpression (x1)
             SRC_ITEM = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'
             data: operationalLayers/popupInfo/description (x1)
             source item a1b2c3d4e5f60718293a4b5c6d7e8f90
MENTION ONLY cln0000000000000000000000000000c  Unrelated map [Web Map]
             metadata: description (x1)
             Replaced a1b2c3d4e5f60718293a4b5c6d7e8f90 last year.
MENTION ONLY txt00000000000000000000000000009  Refresh script [Code Sample]
             data: (not json) (x1)
             portal.content.get('a1b2c3d4e5f60718293a4b5c6d7e8f90') # not json at all

5 hard, 1 soft, 2 mention only, over 12 item(s) searched

2 item(s) COULD NOT BE READ, so they are not clean, they are unknown:
  big0000000000000000000000000000b Parcel delivery: 67108864 byte item was not fetched, over the 33554432 byte limit, so its configuration was never searched
  prv0000000000000000000000000000a Locked report: data: 403 You do not have permissions to access this resource or perform this operation.; resources: https://county.maps.arcgis.com/sharing/rest/content/items/prv0000000000000000000000000000a/resources: 403 You do not have permissions to access this resource or perform this operation.

Do not read this run as permission to delete. It searched what it could read.
```

Thirteen items, twelve searched, eight of them holding the id somewhere. Two more items hold it
and are not in that list. One is a copy of the Permit finder that carries only the target's
thumbnail url, and a thumbnail is a picture of an item, not a use of it. The other names a
longer id that happens to contain these thirty-two characters, which is a different item.

The run exits 2 rather than 1, because two items could not be read. One is too large to fetch
and one refused the token, and an item nobody read is not an item known to be clean.

## Usage

| Flag | Default | What it does |
|---|---|---|
| `--url` | none | Portal url. Required. Must start with `https://` or `http://`. |
| `--target` | none | The item id you are about to delete. Required, and 32 hexadecimal characters. |
| `--query` | the signed-in org | Search query to sweep. The created-date slices are ANDed onto it. |
| `--token` | none | An existing portal token. |
| `--username` | none | Sign in as this user. The password comes from `WHOBREAKS_PASSWORD` or an unechoed prompt. |
| `--out` | none | File to write the report to. |
| `--format` | `json` | `json` or `csv`. |
| `--num` | `100` | Rows per search page to ask for. Clamped to 100, which is all the server gives. |
| `--max-slices` | `4096` | Give up after this many created-date slices rather than splitting for ever. |
| `--insecure` | off | Skip TLS verification, for an Enterprise portal behind an internal CA. Refused together with `--username`. |
| `--apply` | off | Write the report file. Without it nothing is written. |
| `--self-test` | off | Run the offline assertions and exit. |

`--apply` writes a report file. It does not change the organization, and there is no flag that
does. The password is never a flag either: `argv` is readable by every process on the machine,
and the self-test asserts that no `--password` attribute exists at all.

## What it checks (or refuses)

For every item in the organization except the target itself, this tool reads three things and
searches all of them for the target id:

1. the search result, which already carries the title, the description and the snippet,
2. `/content/items/{id}/data`, which holds the web map, the dashboard and the notebook,
3. `/content/items/{id}/resources`, which holds the Experience Builder config and the StoryMap
   draft. Those two are where an id hides while the published app still points elsewhere.

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
- **A search that hits the 10,000 result ceiling is not a complete enumeration.** The sweep
  splits the created-date range and re-asks. When the pages of one query disagree about the
  total, the largest count any page reported is the one believed, so a later small number
  cannot cancel the split. When a single day is still on the ceiling it keeps the rows it did
  read, says which slice it could not prove, and refuses to exit 0.

## Exit codes

| Code | Means |
|---|---|
| 0 | nothing in the organization references the target, and every item was enumerated and read |
| 1 | something references it, listed worst first |
| 2 | the sweep could not be completed, so nothing can be concluded. An item was unreadable, or a search slice sat on the 10,000 ceiling |
| 64 | usage error |

Exit 2 beats exit 1 on purpose. Somebody who sees 1 goes and looks at the items. Somebody who
sees 0 deletes the item, so 0 has to mean the sweep was complete, not that it finished.

## Output

`--format json` writes the findings, the unread items and the reasons the enumeration might be
incomplete into one document, because those three have to be read together. `--format csv`
writes the nine finding columns: `id`, `title`, `type`, `owner`, `kind`, `source`, `path`,
`count`, `excerpt`.

Excerpts are scrubbed on the way out. A secured feature service url inside a web map carries a
token in its query string, and that excerpt is the most useful line in the report, so it is
written with the token replaced rather than dropped. The self-test asserts that no credential
reaches stdout or either file.

## Limits

- It searches the organization you can see. An anonymous sweep covers public items, and a sweep
  as a non-administrator covers what that user can search. The answer is honest about the query,
  not about the organization.
- It reads JSON resources only. An item id inside a PNG, a mobile map package or a file
  geodatabase in an item's resources is not found.
- `/data` is read to 4 MB per item. A configuration truncated there stops parsing as JSON and is
  searched as text, so the cap costs the path of a finding, never the finding. Items larger than
  32 MB are not fetched at all, and are reported as unread.
- Two portal calls per item, plus one per JSON resource. A sweep of 9,000 items is a slow
  afternoon, not a quick check, and `--query` is how you narrow it.
- The classes are a judgement about a key name, not proof. A widget nobody has heard of, holding
  the id under a key this tool does not know, is reported as a mention rather than missed.
- It cannot see a reference that is not the item id: a hard-coded service url that does not
  contain the id, a layer named by title in a script, or an id assembled at run time from parts.
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
