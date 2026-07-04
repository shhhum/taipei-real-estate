# Claude Code routine — daily listing search

Copy the block below into the **prompt field** when creating a scheduled
Claude Code routine (claude.ai/code → Routines) pointed at this repository.
The remote environment must have `AIRTABLE_TOKEN`, `AIRTABLE_BASE_ID`, and
`AIRTABLE_TABLE_ID` configured as environment variables (they already are if
you reused the existing environment).

Suggested schedule: once daily, any time — inserts are deduped by listing URL,
so an extra or repeated run is harmless.

---

```
Run today's Taipei commercial-listing search and report the results.

1. Read CLAUDE.md at the repo root first — it explains the pipeline, the
   environment, and the run's quirks. The SessionStart hook has already
   installed dependencies; do not run playwright install.

2. Start the live pipeline in the background and wait for it to finish:

       uv run python -u -m src.main

   Expect the first 10–20 minutes to be completely silent (the 591 scraper
   prints nothing until it finishes). A quiet process is not a hung process —
   do not kill it or restart it early. A full run takes 15–40 minutes.

3. When it completes, read the final summary line
   (scraped=N accepted=N rejected=N inserted=N) and the per-site lines above
   it, then report:
   - scraped / accepted / inserted totals for today
   - per-site raw counts (591, sinyi, hbhousing, yungching)
   - any site that errored, with its error message

4. Error handling:
   - One site failing is a degraded-but-successful run: report the failure,
     keep the results from the other sites. Transient proxy 503s/TLS resets
     are known; if you have time, re-run just that site's fetch() and insert
     the extra listings (inserts are deduped, partial re-runs are safe).
   - All four sites returning 0 or erroring means something is broken
     (network egress, site layout change). Investigate briefly and report
     what you found — do not push code changes as part of this routine.

5. Do not commit or push anything. This routine only runs the pipeline and
   reports; code changes belong to interactive sessions.
```
