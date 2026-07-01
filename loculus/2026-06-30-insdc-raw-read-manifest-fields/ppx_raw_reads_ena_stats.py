#!/usr/bin/env python3
"""
For a given Pathoplexus organism, find all sequences with linked INSDC raw-read
accessions via the LAPIS API, look up those runs on the ENA Portal API, and emit
a joined TSV (Pathoplexus sample metadata x ENA read_run metadata).

Usage:
    python3 ppx_raw_reads_ena_stats.py mpox > mpox_raw_reads.tsv
    python3 ppx_raw_reads_ena_stats.py west-nile -o west_nile.tsv

List available organism slugs (best effort): see https://pathoplexus.org
Common ones: mpox, west-nile, rsv-a, rsv-b, dengue, measles, cchf
"""
import argparse
import csv
import json
import subprocess
import sys
import urllib.parse

LAPIS_BASE = "https://lapis.pathoplexus.org"
ENA_SEARCH = "https://www.ebi.ac.uk/ena/portal/api/search"

# Pathoplexus sample metadata fields worth carrying through to the join.
# (Full field list varies slightly by organism; missing fields are silently
# dropped by LAPIS rather than erroring.)
PPX_FIELDS = [
    "accession",
    "accessionVersion",
    "insdcRawReadsAccession",
    "authors",
    "authorAffiliations",
    "submitter",
    "groupName",
    "geoLocCountry",
    "geoLocAdmin1",
    "sampleCollectionDate",
    "releasedDate",
    "ncbiVirusName",
    "sequencingInstrument",
    "sequencingAssayType",
    "sequencingProtocol",
    "sequencedByOrganization",
    "bioprojectAccession",
    "biosampleAccession",
]

ENA_FIELDS = [
    "run_accession",
    "library_source",
    "library_strategy",
    "library_selection",
    "instrument_platform",
    "instrument_model",
    "read_count",
    "base_count",
    "first_public",
]

ENA_BATCH_SIZE = 50  # accessions per ENA "OR" query


def curl_get(url: str) -> str:
    """Fetch a URL via curl subprocess (stdlib's urllib needs http.client, which
    is unavailable in this minimal Python install)."""
    proc = subprocess.run(
        ["curl", "-s", "--fail", "--max-time", "60", url],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"curl failed (exit {proc.returncode}) for {url}: {proc.stderr}")
    return proc.stdout


def fetch_json(url: str) -> dict:
    return json.loads(curl_get(url))


def fetch_ppx_samples(organism: str) -> list[dict]:
    """Pull latest-version samples that have a non-null raw-reads accession."""
    params = {
        "fields": ",".join(PPX_FIELDS),
        "versionStatus": "LATEST_VERSION",
        "dataFormat": "json",
        "limit": 1000000,
    }
    url = f"{LAPIS_BASE}/{organism}/sample/details?{urllib.parse.urlencode(params)}"
    data = fetch_json(url)["data"]
    return [row for row in data if row.get("insdcRawReadsAccession")]


def expand_raw_read_rows(samples: list[dict]) -> list[dict]:
    """insdcRawReadsAccession can be a comma-separated list; emit one row per run accession."""
    rows = []
    for s in samples:
        accs = [a.strip() for a in s["insdcRawReadsAccession"].split(",") if a.strip()]
        for acc in accs:
            row = dict(s)
            row["run_accession"] = acc
            rows.append(row)
    return rows


def fetch_ena_metadata(run_accessions: list[str]) -> dict[str, dict]:
    """Batch-query the ENA Portal API and return run_accession -> metadata dict."""
    result: dict[str, dict] = {}
    unique = sorted(set(run_accessions))
    for i in range(0, len(unique), ENA_BATCH_SIZE):
        batch = unique[i : i + ENA_BATCH_SIZE]
        query = " OR ".join(f"run_accession={acc}" for acc in batch)
        params = {
            "result": "read_run",
            "query": query,
            "fields": ",".join(ENA_FIELDS),
            "format": "tsv",
        }
        url = f"{ENA_SEARCH}?{urllib.parse.urlencode(params)}"
        text = curl_get(url)
        reader = csv.DictReader(text.splitlines(), delimiter="\t")
        n_before = len(result)
        for row in reader:
            result[row["run_accession"]] = row
        print(f"  ENA batch {i // ENA_BATCH_SIZE + 1}: {len(batch)} accessions queried, "
              f"{len(result) - n_before} matched, total so far={len(result)}", file=sys.stderr)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("organism", help="Pathoplexus organism slug, e.g. mpox, west-nile, rsv-a")
    ap.add_argument("-o", "--output", default="-", help="Output TSV path (default: stdout)")
    args = ap.parse_args()

    print(f"Fetching Pathoplexus sample metadata for organism '{args.organism}'...", file=sys.stderr)
    samples = fetch_ppx_samples(args.organism)
    print(f"  {len(samples)} samples have a raw-reads accession", file=sys.stderr)

    rows = expand_raw_read_rows(samples)
    print(f"  {len(rows)} individual run accessions after expanding comma-separated lists", file=sys.stderr)

    print("Querying ENA Portal API for run-level metadata...", file=sys.stderr)
    ena_meta = fetch_ena_metadata([r["run_accession"] for r in rows])
    print(f"  ENA metadata found for {len(ena_meta)}/{len(set(r['run_accession'] for r in rows))} unique run accessions",
          file=sys.stderr)

    out_fields = PPX_FIELDS + [f for f in ENA_FIELDS if f != "run_accession"]
    out_fields = ["run_accession"] + [f for f in out_fields if f != "insdcRawReadsAccession"] + ["insdcRawReadsAccession_raw"]

    out = sys.stdout if args.output == "-" else open(args.output, "w", newline="")
    writer = csv.DictWriter(out, fieldnames=out_fields, delimiter="\t", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        run_acc = row["run_accession"]
        merged = dict(row)
        merged["insdcRawReadsAccession_raw"] = row["insdcRawReadsAccession"]
        merged.update(ena_meta.get(run_acc, {}))
        merged["run_accession"] = run_acc
        writer.writerow(merged)
    if out is not sys.stdout:
        out.close()
    print(f"Wrote {len(rows)} rows to {'stdout' if args.output == '-' else args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
