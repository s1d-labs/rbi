# rbi

This repository is one **family** of the s1d-labs scrape pool. The family scrapes named-entity regulatory data from the
Reserve Bank of India (www.rbi.org.in). The family does not scrape macro statistics. It scrapes lists of named companies,
named banks and named regulated entities.

## Projects

| Project | Task | Source | Record |
|---|---|---|---|
| `rbi-enforcement` | `enforcement` | Press-release archive (per month) at `BS_PressReleaseDisplay.aspx` | one monetary penalty on a named bank or NBFC |
| `rbi-authorised-pso` | `authorised` | `PublicationsView.aspx?id=12043` (one inline HTML list) | one authorised or cancelled Payment System Operator |
| `rbi-nbfc-registered` | `nbfc` | `List_of_NBFCs_and_ARCs_registered_with_the_RBI.XLSX` | one registered NBFC or ARC |
| `rbi-nbfc-cancelled` | `nbfc` | `List_of_NBFCs_and_ARCs_whose_CoR_has_been_cancelled_by_the_RBI.XLSX` | one NBFC or ARC with a cancelled CoR |

All work runs on GitHub runners. The family uses no `geo` and no proxy. www.rbi.org.in answers from outside India.

## How each task works

- **enforcement**: RBI announces every monetary penalty as a press release. The press-release archive is an ASP.NET page.
  An archive postback (hidden fields `hdnYear` and `hdnMonth`, with `__VIEWSTATE`) returns the list of all press releases
  for one month. The task posts one month, keeps only the penalty press releases, fetches each detail page, and stores the
  entity, the amount, the order date, the law sections and the reason. The full text is inline, so no document download is
  needed. One queue item is one month.
- **authorised**: RBI publishes the authorised Payment System Operators as one inline HTML table. The table has two
  sections: current Certificates of Authorisation, and cancelled, returned or surrendered ones. The task stores one record
  per named entity from both sections.
- **nbfc**: RBI serves the NBFC and ARC lists as files on its document host (`rbidocs.rbi.org.in`). The file name ends with
  `.XLSX`, but the content is sometimes a true XLSX workbook and sometimes an HTML table. `parse_nbfc` handles both and maps
  the columns by header name, so it keeps working when RBI renames a column. The task stores one record per company and
  keeps the raw file in the family bucket.

## Known limit: the document host can serve a bot-challenge page

From some networks, `rbidocs.rbi.org.in` answers a non-browser client with a JavaScript bot-challenge page instead of the
file. The `nbfc` task detects this page (`rbicommon.is_challenge`) and reports a `bot_challenge` failure to the hub. It does
not store an empty record. The www.rbi.org.in pages (the `enforcement` and `authorised` tasks) do not have this challenge.

## Develop and test

- Parsing tests on saved pages and files: `python -m pytest tests -q`.
- One source without the hub:
  - `python enforcement.py --local 2023 2`
  - `python authorised.py --local`
  - `python nbfc.py --local <path-or-url> [registered|cancelled]`
- End to end against a local stand-in hub: `scrape-check --project rbi-authorised-pso --items 1`.
- The **check** workflow runs the parsing tests and `scrape-check` on every push. The **setup** workflow registers the
  projects, puts them in the pool and registers the health checks.

The full contract for a family is in `CLAUDE.md`.
