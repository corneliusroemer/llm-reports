## Script to Find GenBank Patent Sequences in Pathoplexus

Some sequences ingested from GenBank are not real virus isolates but sequences filed in patents.
GenBank marks those records with the **division `PAT`**. This script lists, per organism, the
Pathoplexus accessions that came from such records, so they can be revoked
(pathoplexus/pathoplexus#967).

It is **read-only** — it queries LAPIS and NCBI Entrez and writes files. It never calls the backend's
write endpoints, so running it cannot change any data. Revoking is a separate, manual step (below).

Long-term prevention — not ingesting patent records in the first place — is tracked in
loculus-project/loculus#6450 and is not what this does. Until that lands, revoked records will be
re-ingested on the next ingest run.

`FINDINGS.md` has the 2026-08-12 analysis, the validation evidence, and an appendix on the one
Entrez failure mode that can silently shorten the list. Read it before trusting a re-run.

### Requirements

Python 3.9+, tested on 3.12 — the script uses nothing outside the standard library, so the `pp-integrity`
environment is not needed (though it works fine):

```bash
micromamba create -f ../environment.yaml && micromamba activate pp-integrity   # optional
```

An [NCBI API key](https://support.nlm.nih.gov/kbArticle/?pn=KA-05317) is optional but raises the
rate limit from 3 to 10 requests/s; a full run takes ~90 min without one, ~30 with.

### Running it

```bash
export NCBI_API_KEY=...        # optional
python3 find_patent_sequences.py \
  --email you@pathoplexus.org \
  --outdir out \
  --crosscheck
```

`--email` is required by NCBI's usage policy. `--crosscheck` re-derives the patent set by a second,
independent route and reports whether the two agree — it costs ~14 extra requests and is strongly
recommended, since it is the check that does not depend on `epost` behaving.

Useful variants:

```bash
# one organism (fast, good for a spot check)
python3 find_patent_sequences.py --email you@pathoplexus.org --outdir out --organism cchf

# broader scope: every GenBank-derived record, including ones a curator later revised.
# `submitter` is per-version, so curator-revised records fall outside the default filter.
python3 find_patent_sequences.py --email you@pathoplexus.org --outdir out2 --source-db GenBank
```

Results are cached in `<outdir>/divisions.tsv`, so a re-run only classifies accessions it has not
seen and an interrupted run resumes rather than restarting. Delete that file to force a full
re-classification (e.g. to pick up upstream division changes).

### Output

| file | contents |
|---|---|
| `revoke_pat_<organism>.txt` | bare Pathoplexus accessions — **the revocation input** |
| `pat_<organism>.tsv` | same hits with INSDC accession, per-segment division, and NCBI title |
| `syn_<organism>.tsv`, `revoke_syn_<organism>.txt` | division `SYN` (synthetic constructs) — a **separate** category, deliberately not merged into the patent list |
| `SUMMARY.md` | per-organism counts and the cross-check verdict |
| `divisions.tsv` | accession → division cache |
| `lapis/<organism>.tsv` | the raw LAPIS pull |
| `unresolved.txt` | accessions Entrez could not classify — **only written if any; if it exists, read it** |

`results/2026-08-12/` holds the output of the first full run: 2260 `PAT` records across 14 organisms,
10 `SYN`, and `env_accessions.txt` (135 `ENV` records — an incidental finding, not patents).

### Checking a run went well

The script is built so that a missed patent shows up as a loud anomaly rather than a quietly shorter
list. After a run, confirm all three:

1. `SUMMARY.md` says **`Unresolved accessions: 0`**. Anything else means some accessions were not
   classified and are listed in `unresolved.txt` — they are *not* known to be patent-free.
2. Every organism's cross-check line says **`agree`**. A `DISAGREE` line names the differing
   accessions and must be understood before acting.
3. `Unexpected divisions` are reviewed. These are neither `PAT`/`SYN`/`VRL` — on 2026-08-12 all 135
   were `ENV`. A new value appearing here is information, not an error.

Do not raise `--chunk-size` to speed things up. Entrez `epost` silently truncates larger id lists
(measured: 2000 posted → 1743/1604/1508 stored across three identical trials, HTTP 200, no error),
which reads as "not a patent" for everything lost. 500 was exact; the script also reads the posted
set back and retries the difference. See `FINDINGS.md`.

### Revoking (a separate, deliberate step)

The lists are the input to a revocation; this script does not perform one. The mechanics are the same
as `../revoke-suppressed-sequences/revoke_suppressed.py`, which is the template to adapt — it already
does Keycloak auth, the revoke call, and the confirmation step. Per accession list:

1. `POST {backend}/{organism}/revoke` with
   `{"accessions": [...], "versionComment": "Patent-derived sequence (GenBank division PAT), revoked per pathoplexus#967"}`.
   This only *stages* a revocation version. Requires curator rights on the entries; retry on HTTP 423.
2. `POST {backend}/{organism}/approve-processed-data` with
   `{"scope": "ALL", "submitterNamesFilter": ["insdc_ingest_user"]}` to confirm it.

Test against staging first by swapping the URLs, as that script's README describes.
