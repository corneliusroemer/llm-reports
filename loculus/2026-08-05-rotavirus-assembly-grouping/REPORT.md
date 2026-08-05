# 2026-08-05 — Rotavirus A: grouping segments into assemblies for tanglegrams

**Repo/commit:** `scratch/rotavirus` @ `f471b54` (parent of `6117d6e`)
**Goal:** link the 11 per-segment INSDC accessions of one clinical sample back
together, emit one-row-per-assembly metadata, and let the phylogenetic workflow
build per-segment trees that Auspice can tangle.

## TL;DR

- Assembly grouping works: **precision 0.9997, recall 0.9959, 98.4% of
  assemblies (2474/2514) recovered exactly** against NCBI GCA membership as
  ground truth, with the GCA groups withheld from the grouper.
- The binding constraint on complete assemblies was **not** grouping — it was
  NSP1 segment *assignment*. Fixing nextclade's seed parameters took complete
  11-segment assemblies from **606 → 5,095** (8.4×).
- Tanglegram support verified end-to-end: identical tip-name sets across the
  vp7/vp4/nsp1 alignments.

## Ground truth: NCBI GCA assemblies

NCBI GenBank assemblies give an authoritative "these accessions are one
assembly" answer. Obtained via `datasets download genome taxon 28875
--assembly-source GenBank --include seq-report` (2.0 MB zip, 2,514
`sequence_report.jsonl` files).

| property | value |
| --- | --- |
| assemblies | 2,514 |
| member accessions | 27,654 (= 2,514 × 11 exactly) |
| member-count distribution | `{11: 2514}` — a pure spike, zero exceptions |
| accessions in >1 assembly | 0 |
| join rate vs `results/metadata.tsv` | 27,654 / 27,654 = **100%** |

Cross-validated against the independent `datasets summary genome --report
sequence` stream: zero set difference in either direction.

**Two limits that stop GCA being used alone:**

1. **Coverage is ~20%** — 27,654 of 135,961 records.
2. **The newest GCA release date is 2021-07-03.** NCBI appears to have stopped
   minting virus GCAs, so there is *zero* GCA coverage for 2021+ sequences.

Also worth knowing: 268 of the 2,514 are labelled "Complete Genome" but total
<15 kb (a full RVA genome is ~18.5 kb), so the assembly-level label is not a
sequence-quality guarantee. Grouping is still trustworthy.

Note `biosample_accession` and `sra_accession` are each populated on only **524**
of 135,961 records, so neither is usable as a grouping key.

## The heuristic

Mirrors the two-tier split in `loculus-project/loculus`
(`ingest/scripts/calculate_assembly_groups.py` authoritative,
`heuristic_group_segments.py` fallback), but replaces the flat composite key with
blocking + compatibility clustering.

**Why not loculus's flat key.** It requires exact equality on every shared field.
On NCBI Virus data that only ever *splits*: any field NCBI fills in
inconsistently across a sample's segments tears the assembly apart. Measured
split causes on the 120 truth assemblies a flat key breaks:

| varying field(s) | assemblies split |
| --- | --- |
| authors | 66 |
| host + authors | 24 |
| date + host + authors | 9 |
| country | 7 |
| date | 4 |
| other combinations | 10 |

**`authors` is implicated in ~107 of 120.** Author *membership* (not just order,
which loculus already sorts for) differs between segments of one submission.

**Algorithm shipped:** block on normalised strain name, then cluster records that
are pairwise compatible on collection year / country / host / author set, where
compatible means equal-or-either-empty, and authors match on **set overlap**. A
cluster can never hold two copies of the same segment; ties broken by accession
proximity, since a sample's segments are deposited as a near-contiguous block.

Strain normalisation: strip segment tokens (`RVA/Human-wt/VNM/12070_94/VP2` →
…/12070_94), drop genotype brackets (`G1P[8]` → `G1P8`), unify host shorthand
(`Hu-wt` ↔ `Human-wt`), collapse separators/case.

### Key-set sweep (all scored against GCA truth)

Flat composite key, exact equality:

| key | precision | recall | exact % | complete 11-seg |
| --- | --- | --- | --- | --- |
| strain+date+country+host+authors | 1.0000 | 0.9775 | 94.91 | 563 |
| strain+date+country+host | 1.0000 | 0.9898 | 97.45 | 585 |
| strain+date+country | 1.0000 | 0.9931 | 98.45 | 589 |
| strain only | 1.0000 | 0.9964 | 99.40 | 593 |

Blocked + compatibility clustering (**shipped: first row**):

| soft fields | precision | recall | exact % | complete 11-seg | total assemblies |
| --- | --- | --- | --- | --- | --- |
| date+country+host+authors | 0.9997 | 0.9959 | 98.41 | **605** | 45,045 |
| authors only | 0.9997 | 0.9971 | 98.69 | 607 | 44,903 |
| none | 0.9997 | 0.9971 | 98.69 | 608 | 44,778 |

Blocked yields **more complete assemblies and fewer total assemblies** (better
merging) than any flat key. The soft guards cost ~0.3% of exact recovery but are
retained because they protect the ~80% of records that have **no ground truth**
against merging same-named samples from unrelated submissions.

### What it gets wrong

- **2 false merges out of 45,045 clusters**, both lab reassortants
  (`DxUK reassortant (UKg9D)`, `AU32xUK reassortant (UKg9AU32)`) whose metadata is
  byte-identical across two GCA records. Unseparable from metadata alone; they
  are constructs, not clinical samples.
- **Genuinely unrecoverable class:** submissions whose strain names carry a
  per-segment serial rather than a sample id — `KM-VP4-135` / `KM-VP7-110`.
  Stripping the segment token leaves `KM--135` vs `KM--110`. Nothing in the
  metadata pairs these, and fuzzy matching on the serial would be actively
  dangerous (`KM-VP4-135` and `KM-VP4-136` are different samples).

## The real blocker was NSP1 segment assignment

Grouping looked fine but only 606 assemblies came out complete, with a
suspicious bulge of 5,053 at exactly 10 segments — **4,992 of them missing
nsp1**, and 4,980 of those had an `unassigned` member sitting in the slot.

Cleanest possible evidence, because grouping is not in question: of the **2,514
GCA assemblies — 11 accessions each by NCBI's own assertion — only 255 got all 11
segments assigned; 2,154 got exactly 10.**

Diagnosis:

- nsp1 had only **818** assignments vs ~7,400–7,600 for every other internal gene.
- **Bimodal, not marginal:** when nextclade aligned nsp1 it got median coverage
  **0.996**; when it failed, exactly **0.0**. So `min_coverage: 0.2` was never the
  problem.
- Verified the sequences are real NSP1: `PV693988.1` (1461 nt, clean ATG, no
  ambiguity codes) translates to `MATFKDACYQYKKLNKLNNAVLKLGA` — canonical RVA NSP1.
- Nextclade's own error: `seed alignment was unable to find any matches that are
  long enough. Only matches of at least 40 nucleotides long are considered.`

NSP1 is the most divergent RVA gene (genotypes A1–A32 differ ~50% at nt level)
and shares no 40 nt exact window with the simian SA11 reference
(`NC_011500.2`). `minSeedCover` was already loosened to 0.01 in the dataset.

### Fix: seed parameters, not new references

| params | coverage on `PV693988.1` |
| --- | --- |
| default | **fails** |
| `--alignment-preset high-diversity` | 0.992 |
| `--alignment-preset short-sequences` | 0.992 |
| `--kmer-length 8 --min-match-length 15` | **0.996** |
| `--kmer-length 6 --min-match-length 10 --allowed-mismatches 5` | 0.996 |
| `--kmer-length` alone, or `--min-match-length` alone | **fails** |

Both knobs are required: default 10-mers cannot match, and a 40 nt window cannot
be reached. Explicit params were chosen over the presets because
`nextclade run --help` documents presets as "EXPERIMENTAL feature subject to
adjustments".

Effect at full scale (re-aligning all 14,423 unassigned records against all 11
references):

| metric | before | after |
| --- | --- | --- |
| nsp1 assignments | 818 | **6,421** |
| `unassigned` records | 14,423 | 5,647 |
| complete 11-segment assemblies | 606 | **5,095** |

8,776 of 14,423 unassigned were rescued (61%), 5,603 of them nsp1. The
segments-per-assembly histogram became properly bimodal (spikes at 1 and 11);
the 10-segment bulge collapsed from 5,258 → 1,305.

**Both workflows must carry the same params** — `ingest`:
`segment_assignment.alignment_args`, `phylogenetic`: `align.alignment_args`. A
sequence that ingest assigns to a segment but that fails to align downstream
silently vanishes from that tree, taking its assembly's tip with it.

## Tanglegram support

Two things decide whether a tanglegram works, and both are settled once for all
builds rather than per build:

1. **Shared tip set** — `phylogenetic/rules/select_assemblies.smk` picks the
   assembly set once, *after* per-segment QC, and subsamples at **assembly**
   level. Independently subsampling 1000 sequences per segment out of ~45k
   assemblies would leave the trees with almost no overlap.
2. **Identical tip names** — `scripts/rename_fasta_ids.py` renames tips from
   accession to `assembly_id`. Unique per build by construction (one accession
   per segment per assembly); a collision is a hard error, never a dropped tip.

Verified: vp7/vp4/nsp1 alignments have **identical tip-name sets**, equal to the
selected `assembly_id` set, with zero overlap with the accession set.

## Two bugs worth remembering

- **pandas coerces lowercase `true`/`false` to bool dtype.** A `tangle_selected`
  column written as `true`/`false` made `augur filter --query
  "tangle_selected == 'true'"` compare bool to string and drop **all 135,961
  records** with a cheerful "filtered out by the query" message. Now `yes`/`no`.
- **A YAML scalar `group_by: country year` passed with `{...:q}`** becomes one
  argument, so it was looked up as a single nonexistent column and collapsed
  497 country/year groups into 1. Must be a YAML list.

Also: DuckDB reads empty TSV fields as NULL, and `||` with NULL yields NULL — so
a naive concatenated grouping key silently collapses all rows with any missing
field into one group. This made a stricter key appear to produce *fewer* groups
than a looser one until it was fixed with `coalesce`.

## Files

Ingest:
- `bin/group_assemblies.py` — the grouper, with `--eval-against` scoring mode
- `bin/parse-gca-seq-report.py` — GCA membership from a Datasets seq-report zip
- `rules/group_assemblies.smk` — fetch / group / evaluate rules
- `defaults/config.yaml` — `assembly_grouping`, `segment_assignment.alignment_args`

Phylogenetic:
- `scripts/select_assemblies.py` — QC + assembly-coherent selection
- `scripts/rename_fasta_ids.py` — accession → assembly_id tip rename
- `rules/select_assemblies.smk`, `defaults/config.yaml` (`tangle`, `align`)

## Follow-ups not done

- The full ingest has **not** been re-run with the new alignment params; the
  committed DAG will re-run all 11 nextclade classifications on next invocation.
  All numbers above for the rescued state come from re-aligning the previously
  `unassigned` records and re-grouping, not from a clean full run.
- `nextclade sort` (minimizer-based, divergence-tolerant, one pass instead of 11)
  is worth evaluating as a replacement for argmax-over-11-alignments; it is what
  loculus uses (`ingest/scripts/parse_nextclade_sort_output.py`).
- The remaining 5,647 `unassigned` records were not investigated.
