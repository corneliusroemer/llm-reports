# GenBank patent sequences in Pathoplexus — 2026-08-12 findings

Analysis behind `find_patent_sequences.py`; see `README.md` for how to run it. Addresses
pathoplexus/pathoplexus#967 (revoke the patent sequences already ingested). Loculus-side prevention
is loculus-project/loculus#6450 and is **not** what this does.

Lists from this run: `results/2026-08-12/`.

## How patents are detected

GenBank assigns every record a **division**; patent-derived sequences get `PAT`. Entrez exposes it
as a searchable property, so an exact accession set can be filtered server-side rather than
downloading ~200k full records:

```
epost   db=nuccore  id=<500 accessions>            -> WebEnv + QueryKey k
esearch db=nuccore  term="#k AND gbdiv_pat[PROP]"  -> the patent subset of exactly those
```

**The predicate was verified, not assumed** (2026-08-12): `gbdiv_pat[PROP]` agrees exactly with
`GBSeq_division` from `efetch rettype=gb&retmode=xml` — `AX655367` is `PAT` and matches;
`MN566112` / `KY586362` are `VRL` and do not.

Input set: LAPIS `/details` per organism, filtered `submitter=insdc_ingest_user`,
`versionStatus=LATEST_VERSION`, `isRevocation=false`. The INSDC accession field name differs per
organism (segmented organisms carry `insdcAccessionBase_L/_M/_S`), so it is read from each
organism's `databaseConfig` rather than hardcoded — LAPIS returns HTTP 400 for an unknown field.

### Why a false negative can't hide

A missed patent sequence silently stays public, so each chunk is checked three ways:

1. **Posted accessions are read back** (`efetch rettype=acc`) and any Entrez failed to resolve are
   recorded as `UNRESOLVED`, never as "not a patent".
2. **Nothing is assumed to be `VRL`**: everything that is neither `PAT`, `SYN` nor `VRL` is counted
   and its true division fetched, so an unexpected division surfaces in the report.
3. **`--crosscheck` re-derives the patent set by an unrelated route** —
   `txid<N>[Organism:exp] AND gbdiv_pat[PROP]` per taxon, intersected locally with the ingested
   accessions — and diffs it against the epost route. A dropped id or a stale history key in one
   route shows up as a disagreement rather than as a quietly shorter list.

Segmented organisms: a Pathoplexus record is flagged if **any** segment is `PAT`. Revocation is per
Pathoplexus accession, so a record with a patent segment cannot be partly revoked; every segment's
division is emitted in the TSV so disagreements stay visible.

## Validation against the manually curated list

pathoplexus/curation_reports#23 contains 18 accessions (1 mpox, 17 hantavirus) manually verified as
patent-derived by two people. The detector finds **all 18, with zero false negatives**, plus 9
additional mpox records whose GenBank titles are unambiguous — e.g. `GN049036` "Sequence 30 from
Patent EP2028270", `JB071962` "Sequence 2 from Patent EP2496699", `QP053878` "Patent application
sequence for JP 2025046038-A/1".

A third, fully independent route agrees as well: ENA's Portal API exposes the same flag as a data
class, and all 10 mpox hits appear in it —

```bash
curl -s --get 'https://www.ebi.ac.uk/ena/portal/api/search' \
  --data-urlencode 'result=sequence' \
  --data-urlencode 'query=dataclass="PAT" AND tax_tree(10244)' \
  --data-urlencode 'fields=accession,dataclass' --data-urlencode 'format=tsv'
```

ENA lists 28 `PAT` records under the mpox taxon; 10 of them were ingested into Pathoplexus.

## Results

Run 2026-08-12 over all 14 organisms: 196,597 ingested records, 199,036 unique INSDC accessions.
**2,260 records are GenBank division `PAT`.** Lists are in `results/2026-08-12/revoke_pat_<organism>.txt`.

| organism | ingested | **PAT** | SYN | cross-check |
|---|---|---|---|---|
| andv | 474 | 17 | 0 | agree |
| cchf | 3104 | 18 | 0 | agree |
| dengue | 57517 | 509 | 0 | agree |
| ebola-bdbv | 44 | 11 | 0 | agree |
| ebola-sudan | 161 | 9 | 0 | agree |
| ebola-zaire | 3725 | 92 | 0 | agree |
| hmpv | 15213 | 703 | 0 | agree |
| marburg | 411 | 21 | 0 | agree |
| measles | 29065 | 158 | 2 | agree |
| mpox | 10947 | 10 | 0 | agree |
| rsv-a | 37926 | 356 | 8 | agree |
| rsv-b | 27705 | 76 | 0 | agree |
| west-nile | 8015 | 167 | 0 | agree |
| yellow-fever | 2290 | 113 | 0 | agree |
| **total** | **196597** | **2260** | **10** | 14/14 agree |

Health of the run: **0 unresolved** accessions (52 were dropped by `epost` mid-run and all 52 were
recovered by the retry pass), and **all 14 organisms agree** between the epost route and the
independent per-taxon route. Division breakdown of the full set: 196,631 `VRL`, 2,260 `PAT`,
135 `ENV`, 10 `SYN`.

`ebola-bdbv` is the standout in proportion — 11 of its 44 ingested records (25%) are patent
sequences. `hmpv` (703) and `dengue` (509) dominate in absolute numbers.

Titles confirm the hits are what they claim, e.g. `CQ840375` "Sequence 60 from Patent WO2004057021",
`A75711` "Sequence 1 from Patent WO9322440". Note that some patent records carry innocuous-looking
titles (`A13666` "PUO-218 cDNA", `FV537349` "Modified Microbial Nucleic Acid") — which is exactly
why the division flag rather than the title is the detector.

### Coverage of the input set was verified too

The cross-check above validates the *division call*, not the *input set* — both routes start from
the accessions the LAPIS pull returned, so a record missing from the pull is missed identically by
both and still "agrees". The specific hole: `submitter` is per **version**, so a record ingested
from GenBank and later revised by a curator has a latest version whose submitter is that curator.
There are **58 such records** (e.g. andv `arthurshem_curator` 47, `annaparker_curator` 3;
ebola-sudan `michaelmartin_curator` 4).

So the whole thing was re-run with `--source-db GenBank`, which scopes on `ncbiSourceDb` instead of
`submitter` and therefore includes those records (not committed; regenerate with `--source-db GenBank`). Result: 148 additional accessions,
**0 additional patents**, and the two PAT accession sets are **identical** (2260 each, symmetric
difference 0). The list is complete with respect to both scopings — verified, not assumed.

### Incidental finding: 135 records are division `ENV`

The unexpected-division check surfaced 135 ingested records marked `ENV` (environmental /
metagenomic, source organism not directly identified) — e.g. `MN488638`–`MN488656`. These are not
patents and are **not** in the revocation lists. Whether environmental records belong in Pathoplexus
is a separate curation question worth raising on #967; they are listed in `results/2026-08-12/env_accessions.txt`.

## Limitations worth stating before revoking

- **`PAT` is a floor, not a ceiling.** Division `PAT` covers sequences submitted through the
  patent-office pipelines. Patent-derived sequences deposited by other routes are not marked, and
  NCBI excludes most pre-grant application sequences. So this list is sound but not exhaustive —
  it will not catch every patent sequence in Pathoplexus.
- **`SYN` is reported but kept separate.** Synthetic constructs are a different category from
  patents (the "constructs" half of #967). Folding them into the same list would silently widen the
  revocation, so that is left as an explicit decision.
- **Divisions can change upstream**, and records can be suppressed after the fact. The list is a
  snapshot; re-run before acting if it has aged.
- Without `NCBI_API_KEY` the run is capped at 3 req/s and took ~90 min for 199k accessions;
  HTTP 429/502 and the occasional HTML-body-under-HTTP-200 are retried with backoff, and the
  division cache makes a crash resume rather than restart. With a key it should be ~3x faster.
- **`epost` silently TRUNCATES large id lists** — characterised 2026-08-12, see the section below.
  Batch size stays at 500 and anything unresolved is retried in chunks of 50. Do not raise
  `--chunk-size` for speed without watching the `unresolved=` counter.


## Appendix: how `epost` loses accessions (measured)

This is the one failure mode that could have silently shortened the revocation list, so it was
characterised rather than just worked around. Posting 2000 nuccore accessions, three trials:

| posted | stored (`esearch '#k'`) | lost | `InvalidIdList` |
|---|---|---|---|
| 2000 | 1743 | 257 | **empty** |
| 2000 | 1604 | 396 | **empty** |
| 2000 | 1508 | 492 | **empty** |
| 1000 | 1000 | 0 | empty |
| 500 | 500 | 0 | empty |

Three properties make this dangerous:

1. **It is a contiguous tail truncation.** In every trial the retained ids were exactly the first
   N of the posted list and the lost ones exactly the last 2000−N — not a scattered dropout.
2. **The cutoff is non-deterministic** (1743 / 1604 / 1508 for the same input), so it looks like a
   server-side time or size limit hit during accession→UID conversion, not a fixed cap.
3. **Nothing reports it.** HTTP 200, `<InvalidIdList>` empty, no `ERROR` element. The only signal is
   that `esearch "#k"` returns a smaller count than you posted — which is precisely why the script
   reads the posted set back and flags the difference as `UNRESOLVED`.

Consequence: `esearch "#k AND gbdiv_pat[PROP]"` then searches only the retained prefix, and any
patent record in the truncated tail is reported as "not a patent". Nothing distinguishes that from a
genuine negative.

**Why it didn't corrupt this run**, and why that was partly luck: the script sorts accessions, and
patent-office prefixes (`A`, `AX`, `CQ`, `FV`, …) sort early, so the patents sat in the retained
prefix — the 396 and 492 lost accessions were 100% `VRL` in both trials. A different ordering (by
ingest date, or unsorted) would have put patents in the truncated tail and lost them. The
protections that make the final list trustworthy regardless are the readback check, the small-chunk
retry, and above all the per-taxon cross-check, which never goes through `epost` at all and agreed
exactly on all 14 organisms.

### Is it documented? Half of it

**The limit is documented; the failure mode is not.**

The E-utilities reference ([NBK25499](https://www.ncbi.nlm.nih.gov/books/NBK25499/)) names the exact
mechanism, and it matches what the measurements above imply:

> "When using accession.version identifiers, there is a conversion step that takes place that causes
> large lists of identifiers to time out, even when using POST. Therefore, we recommend batching
> these types of requests in sizes of about 500 UIDs or less."

So the ~500 batch size and the accession→UID conversion step as the cause are both official. What
that page does *not* say anywhere is what actually happens when you exceed it: it says the request
will "time out", which reads as an error you would catch. It does not say the call returns **HTTP 200
with a silently shortened set**, and it does not document `InvalidIdList` at all. A developer who
follows the recommendation is safe; one who exceeds it gets undescribed behaviour that looks like
success.

The word "truncation" only appears in the **EDirect client changelog**
([NBK564895](https://www.ncbi.nlm.nih.gov/books/NBK564895/)), where NCBI's own tool author works
around this class of bug repeatedly:

| version | date | entry |
|---|---|---|
| 14.9 | 2021-04-15 | **"Epost uses chunks of 1000 to avoid server truncation."** |
| 16.1 | 2021-10-13 | "Epost looks up all nucleotide accessions to **silently** skip replaced records." |
| 22.4 | 2024-07-17 | "Elink adjusts batch size to remain within history element limits." |
| 22.5 | 2024-08-29 | "Elink creates new web environment to avoid history overflow." |
| 23.1 | 2024-11-20 | "Elink into history splits input into chunks, but now **truncates each chunk to avoid backend server overflow**" |

The v14.9 entry is the direct confirmation: "server truncation" is NCBI's own name for it, and 1000
is the chunk size their client picked to dodge it — consistent with the measurements above, where
1000 was exact and 2000 truncated. The ELink entries show the same history-server overflow class of
failure being fought on another endpoint.

Note the v16.1 entry is a **second, independent id-loss path**: replaced records are silently
skipped. So "posted ≠ stored" has at least two causes, one of which is intended behaviour. Either
way the readback is the only way to know.

### Who is exposed (it is not a client-version question)

The truncation is **server-side and still live**: measured 2026-08-12, five years after the EDirect
v14.9 workaround. NCBI never fixed the server; they chunked around it in their client. So exposure
depends on *how you call the API*, not on what version you run:

| caller | exposed? | why |
|---|---|---|
| **EDirect CLI** (`epost`) | no | the installed v22.4 script contains `join-into-groups-of 1000` — the v14.9 fix. Protected by the client, not by a server fix. |
| **this script** | defended | it calls the HTTP API directly via `urllib`, so no client fix applies; it chunks at 500, reads the stored set back, and retries the difference. |
| **loculus PR 6981** `mirroring/enrich_genbank.py` | no | uses `efetch` with batches of 100 and never touches `epost`/the history server. It also marks per-accession misses (`not_found: True`), which is the same defence. |

Anything new that reaches for `epost` on raw HTTP is exposed by default, which is the reusable
lesson.
