#!/usr/bin/env python3
"""Find Pathoplexus sequences that originate from GenBank *patent* records.

Background: Pathoplexus ingests public INSDC data as the ``insdc_ingest_user``
submitter.  Some of those GenBank records are not real virus isolates but
sequences filed in patents; GenBank marks them with the division ``PAT``
(pathoplexus/pathoplexus#967, loculus-project/loculus#6450).  This script
produces the per-organism list of Pathoplexus accessions to revoke.

It only *reads*: LAPIS for the ingested accessions, NCBI Entrez for the
division.  It never calls the Loculus backend's write endpoints.

How the division is determined
------------------------------
Entrez exposes the GenBank division as a searchable property, so the exact set
of accessions can be filtered server-side instead of downloading every record:

    epost   db=nuccore  id=<500 accessions>                     -> WebEnv + QueryKey k
    esearch db=nuccore  term="#k AND gbdiv_pat[PROP]"          -> the patent subset

Verified 2026-08-12: ``gbdiv_pat[PROP]`` agrees with ``GBSeq_division == PAT``
from ``efetch rettype=gb&retmode=xml`` (AX655367 -> PAT and matched;
MN566112/KY586362 -> VRL and not matched).

Because a false negative here means a patent sequence silently stays public,
every chunk is checked three ways:

* the posted accessions are read back (``efetch rettype=acc``) and any that
  Entrez failed to resolve are reported as ``UNRESOLVED``, never as "not a
  patent";
* everything that is neither PAT, SYN nor VRL is counted, and its real division
  is fetched, so an unexpected division cannot hide;
* ``--crosscheck`` re-derives the patent set by a completely different route
  (``txid<N>[Organism:exp] AND gbdiv_pat[PROP]`` per taxon, intersected
  locally) and diffs the two.

Synthetic-construct records (division ``SYN``) are reported separately: the ask
is patents, and folding constructs into the same list would silently widen a
revocation.

Usage
-----
    export NCBI_API_KEY=...            # optional, raises 3/s to 10/s
    ./find_patent_sequences.py --email you@example.org --outdir out
    ./find_patent_sequences.py --email you@example.org --outdir out --crosscheck
    ./find_patent_sequences.py --email you@example.org --outdir out --organism cchf

Results are cached under ``<outdir>``, so re-runs are cheap and a crash resumes
rather than restarting.
"""

from __future__ import annotations

import argparse
import csv
import http.client
import io
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

BACKEND_URL = "https://backend.pathoplexus.org"
LAPIS_URL = "https://lapis.pathoplexus.org"
EUTILS_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL_NAME = "ppx-patent-audit"

# Divisions we expect for virus records; anything else gets looked up explicitly.
EXPECTED_DIVISIONS = ("pat", "syn", "vrl")
CLASSIFIED_DIVISIONS = ("pat", "syn")

MAX_RETRIES = 6
MAX_RETRY_DELAY_SECONDS = 60

csv.field_size_limit(1 << 30)  # comment/authorAffiliations fields are large


# --------------------------------------------------------------------------- #
# HTTP plumbing
# --------------------------------------------------------------------------- #


def _sleep_backoff(attempt: int, retry_after: float | None, what: str) -> None:
    delay = max(retry_after or 0.0, min(2**attempt, MAX_RETRY_DELAY_SECONDS))
    delay = min(delay + random.uniform(0, 1), MAX_RETRY_DELAY_SECONDS)
    print(f"  {what}; retrying in {delay:.1f}s ({attempt + 1}/{MAX_RETRIES})", file=sys.stderr)
    time.sleep(delay)


def _retry_after(error: urllib.error.HTTPError) -> float | None:
    value = error.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def request(url: str, *, data: bytes | None = None, timeout: int = 300) -> bytes:
    """GET (or POST when ``data`` is given) with retries on 429/5xx/truncation."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == MAX_RETRIES:
                raise
            _sleep_backoff(attempt, _retry_after(error), f"HTTP {error.code}")
        except (urllib.error.URLError, http.client.IncompleteRead, TimeoutError) as error:
            if attempt == MAX_RETRIES:
                raise
            _sleep_backoff(attempt, None, f"{type(error).__name__}")
    raise RuntimeError("unreachable")


class Entrez:
    """Rate-limited Entrez client. NCBI allows 3 req/s, or 10 with an API key."""

    def __init__(self, email: str, api_key: str | None) -> None:
        self.email = email
        self.api_key = api_key
        self.min_interval = 0.12 if api_key else 0.40
        self._last_call = 0.0

    def _common(self) -> dict[str, str]:
        params = {"tool": TOOL_NAME, "email": self.email}
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def get(self, endpoint: str, params: dict[str, str]) -> bytes:
        self._throttle()
        query = urllib.parse.urlencode({**self._common(), **params})
        return request(f"{EUTILS_URL}/{endpoint}?{query}")

    def post(self, endpoint: str, params: dict[str, str]) -> bytes:
        self._throttle()
        body = urllib.parse.urlencode({**self._common(), **params}).encode()
        return request(f"{EUTILS_URL}/{endpoint}", data=body)

    def get_json(self, endpoint: str, params: dict[str, str]) -> dict:
        """GET a JSON reply. Entrez occasionally answers HTTP 200 with an HTML
        error page, which must be retried rather than crashing a long run."""
        for attempt in range(MAX_RETRIES + 1):
            raw = self.get(endpoint, params)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                if attempt == MAX_RETRIES:
                    raise RuntimeError(
                        f"{endpoint} kept returning non-JSON: {raw[:200]!r}"
                    ) from None
                _sleep_backoff(attempt, None, f"{endpoint} returned non-JSON")
        raise RuntimeError("unreachable")

    def post_xml(self, endpoint: str, params: dict[str, str]) -> ET.Element:
        """POST expecting XML, retrying the same HTML-instead-of-XML case."""
        for attempt in range(MAX_RETRIES + 1):
            raw = self.post(endpoint, params)
            try:
                return ET.fromstring(raw)
            except ET.ParseError:
                if attempt == MAX_RETRIES:
                    raise RuntimeError(f"{endpoint} kept returning non-XML: {raw[:200]!r}") from None
                _sleep_backoff(attempt, None, f"{endpoint} returned non-XML")
        raise RuntimeError("unreachable")

    # -- higher-level helpers ------------------------------------------------

    def epost(self, accessions: list[str]) -> tuple[str, str, list[str]]:
        """Upload an accession list. Returns (WebEnv, QueryKey, invalid ids)."""
        root = self.post_xml("epost.fcgi", {"db": "nuccore", "id": ",".join(accessions)})
        error = root.findtext("ERROR")
        web_env = root.findtext("WebEnv")
        query_key = root.findtext("QueryKey")
        if not web_env or not query_key:
            raise RuntimeError(f"epost returned no history handle: {error!r}")
        invalid = [node.text or "" for node in root.findall("InvalidIdList/Id")]
        return web_env, query_key, invalid

    def esearch_history(self, term: str, web_env: str) -> tuple[str, int]:
        """Run a search against an existing history. Returns (new key, count)."""
        result = self.get_json(
            "esearch.fcgi",
            {
                "db": "nuccore",
                "term": term,
                "WebEnv": web_env,
                "usehistory": "y",
                "retmax": "0",
                "retmode": "json",
            },
        )["esearchresult"]
        if "ERROR" in result:
            raise RuntimeError(f"esearch failed for {term!r}: {result['ERROR']}")
        return result["querykey"], int(result["count"])

    def accessions_of(self, web_env: str, query_key: str, count: int) -> list[str]:
        """List the accession.version of every record in a history set."""
        found: list[str] = []
        page = 5000
        for start in range(0, count, page):
            raw = self.get(
                "efetch.fcgi",
                {
                    "db": "nuccore",
                    "WebEnv": web_env,
                    "query_key": query_key,
                    "rettype": "acc",
                    "retmode": "text",
                    "retstart": str(start),
                    "retmax": str(min(page, count - start)),
                },
            )
            found.extend(line.strip() for line in raw.decode().splitlines() if line.strip())
        return found

    def divisions_of(self, accessions: list[str]) -> dict[str, str]:
        """Read GBSeq_division directly. Only for small sets — fetches records.

        ``seq_start``/``seq_stop`` trim the sequence to a single base so the
        response stays small; the annotation we want is unaffected.
        """
        divisions: dict[str, str] = {}
        for start in range(0, len(accessions), 100):
            batch = accessions[start : start + 100]
            raw = self.get(
                "efetch.fcgi",
                {
                    "db": "nuccore",
                    "id": ",".join(batch),
                    "rettype": "gb",
                    "retmode": "xml",
                    "seq_start": "1",
                    "seq_stop": "1",
                },
            )
            root = ET.fromstring(raw)
            for node in list(root.findall("GBSeq")) + list(root.findall("INSDSeq")):
                accession = node.findtext("GBSeq_primary-accession") or node.findtext(
                    "INSDSeq_primary-accession"
                )
                division = node.findtext("GBSeq_division") or node.findtext("INSDSeq_division")
                if accession and division:
                    divisions[base_accession(accession)] = division.upper()
        return divisions

    def titles_of(self, accessions: list[str]) -> dict[str, str]:
        """Fetch record definitions, for eyeballing the resulting list."""
        titles: dict[str, str] = {}
        for start in range(0, len(accessions), 200):
            batch = accessions[start : start + 200]
            result = self.get_json(
                "esummary.fcgi",
                {"db": "nuccore", "id": ",".join(batch), "retmode": "json"},
            ).get("result", {})
            for uid in result.get("uids", []):
                doc = result[uid]
                accession = doc.get("accessionversion") or doc.get("caption")
                if accession:
                    titles[base_accession(accession)] = doc.get("title", "")
        return titles


# --------------------------------------------------------------------------- #
# Pathoplexus side
# --------------------------------------------------------------------------- #


def base_accession(accession: str) -> str:
    """``AX655367.1`` -> ``AX655367``. Divisions are per record, not per version."""
    return accession.split(".", 1)[0].strip()


def discover_organisms() -> list[str]:
    """The backend OpenAPI doc has the organism list as a closed enum."""
    raw = request(f"{BACKEND_URL}/api-docs")
    schema = json.loads(raw)["components"]["schemas"]["Organism"]
    return list(schema["enum"])


def accession_fields(organism: str) -> list[str]:
    """Segmented organisms carry ``insdcAccessionBase_L/_M/_S``, others the bare name.

    Field sets genuinely differ per organism and LAPIS 400s on an unknown
    field, so this must be read per organism rather than hardcoded.
    """
    raw = request(f"{LAPIS_URL}/{organism}/sample/databaseConfig")
    metadata = json.loads(raw)["schema"]["metadata"]
    names = [field["name"] for field in metadata]
    fields = sorted(name for name in names if name.startswith("insdcAccessionBase"))
    if not fields:
        raise RuntimeError(f"{organism}: no insdcAccessionBase* field in databaseConfig")
    return fields


def lapis_filters(scope: tuple[str, str]) -> list[tuple[str, str]]:
    """``scope`` is the (field, value) selecting the records of interest.

    ``submitter=insdc_ingest_user`` is the obvious choice, but ``submitter`` is
    per *version*: a record ingested from GenBank and later revised by a curator
    has a latest version submitted by that curator, so the submitter filter
    silently drops it (58 such records as of 2026-08-12).  Filtering on
    ``ncbiSourceDb=GenBank`` instead covers those.
    """
    return [scope, ("versionStatus", "LATEST_VERSION"), ("isRevocation", "false")]


def lapis_count(organism: str, scope: tuple[str, str]) -> int:
    query = urllib.parse.urlencode(lapis_filters(scope))
    raw = request(f"{LAPIS_URL}/{organism}/sample/aggregated?{query}")
    return int(json.loads(raw)["data"][0]["count"])


def fetch_lapis_records(organism: str, scope: tuple[str, str], cache: Path) -> list[dict[str, str]]:
    """Pull the ingested records for one organism, with a row-count assertion.

    A truncated download and a wrong filter both fail silently, so the count is
    always checked against /aggregated using the same filters.
    """
    fields = ["accession", "version", *accession_fields(organism), "ncbiVirusTaxId"]
    if not cache.exists():
        params = [
            *lapis_filters(scope),
            ("fields", ",".join(fields)),
            ("dataFormat", "tsv"),
        ]
        raw = request(f"{LAPIS_URL}/{organism}/sample/details?{urllib.parse.urlencode(params)}")
        cache.write_bytes(raw)
    rows = list(csv.DictReader(io.StringIO(cache.read_text()), delimiter="\t"))
    expected = lapis_count(organism, scope)
    if len(rows) != expected:
        cache.unlink(missing_ok=True)
        raise RuntimeError(
            f"{organism}: got {len(rows)} rows but /aggregated says {expected}; "
            "cache dropped, re-run"
        )
    return rows


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


def classify_chunk(entrez: Entrez, chunk: list[str]) -> tuple[dict[str, str], dict[str, int]]:
    """Return {accession: division} for one chunk, plus per-chunk statistics."""
    web_env, query_key, invalid = entrez.epost(chunk)

    _, resolved_count = entrez.esearch_history(f"#{query_key}", web_env)
    resolved = {
        base_accession(accession)
        for accession in entrez.accessions_of(web_env, query_key, resolved_count)
    }

    divisions: dict[str, str] = {}
    # Anything Entrez could not resolve is recorded as such: treating it as
    # "not a patent" would be a silent false negative.
    for accession in chunk:
        if base_accession(accession) not in resolved:
            divisions[base_accession(accession)] = "UNRESOLVED"

    stats = {
        "posted": len(chunk),
        "resolved": len(resolved),
        "invalid": len(invalid),
        "unresolved": len(divisions),
    }

    for division in CLASSIFIED_DIVISIONS:
        key, count = entrez.esearch_history(f"#{query_key} AND gbdiv_{division}[PROP]", web_env)
        stats[division] = count
        for accession in entrez.accessions_of(web_env, key, count):
            divisions[base_accession(accession)] = division.upper()

    # Whatever is left over gets its true division looked up, so an unexpected
    # value shows up in the report instead of being assumed to be VRL.
    negations = " OR ".join(f"gbdiv_{division}[PROP]" for division in EXPECTED_DIVISIONS)
    key, count = entrez.esearch_history(f"#{query_key} NOT ({negations})", web_env)
    stats["unexpected"] = count
    if count:
        others = [base_accession(a) for a in entrez.accessions_of(web_env, key, count)]
        divisions.update(entrez.divisions_of(others))

    for accession in resolved:
        divisions.setdefault(accession, "VRL")
    return divisions, stats


def classify(entrez: Entrez, accessions: list[str], cache: Path, chunk_size: int) -> dict[str, str]:
    """Classify every accession, caching results so a re-run resumes.

    ``epost`` silently TRUNCATES a large id list: it keeps a prefix and discards
    the tail, with HTTP 200, an empty ``InvalidIdList`` and no error.  Measured
    2026-08-12 on nuccore, posting 2000 accessions in three identical trials:
    1743 / 1604 / 1508 stored -- so the cutoff is not even deterministic, which
    points at a server-side timeout in the accession->UID conversion step that
    NBK25499 warns about.  500 and 1000 were exact.

    A truncated-away accession comes back from the division search as a
    non-match, indistinguishable from a genuine negative.  So the posted set is
    always read back, the difference is recorded as UNRESOLVED rather than
    assumed patent-free, and those are retried in small chunks; only what
    survives that is genuinely unresolvable.
    """
    known: dict[str, str] = {}
    if cache.exists():
        for row in csv.DictReader(io.StringIO(cache.read_text()), delimiter="\t"):
            known[row["insdc_accession"]] = row["division"]
    todo = [a for a in accessions if a not in known or known[a] == "UNRESOLVED"]
    print(f"Classifying {len(todo)} accessions ({len(known)} cached)", file=sys.stderr)

    new_file = not cache.exists()
    with cache.open("a", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        if new_file:
            writer.writerow(["insdc_accession", "division"])

        def run_pass(items: list[str], size: int, label: str) -> list[str]:
            unresolved: list[str] = []
            for start in range(0, len(items), size):
                chunk = items[start : start + size]
                divisions, stats = classify_chunk(entrez, chunk)
                print(
                    f"  {label} {start + len(chunk)}/{len(items)}: "
                    + " ".join(f"{k}={v}" for k, v in stats.items()),
                    file=sys.stderr,
                )
                wanted = {base_accession(a) for a in chunk}
                for accession, division in divisions.items():
                    if accession not in wanted:
                        continue
                    known[accession] = division
                    writer.writerow([accession, division])
                    if division == "UNRESOLVED":
                        unresolved.append(accession)
                handle.flush()
            return unresolved

        stragglers = run_pass(todo, chunk_size, "main")
        if stragglers:
            print(
                f"Retrying {len(stragglers)} unresolved accessions in small chunks",
                file=sys.stderr,
            )
            still = run_pass(stragglers, 50, "retry")
            if still:
                print(
                    f"WARNING: {len(still)} accessions remain unresolved and are NOT "
                    "classified; see unresolved.txt",
                    file=sys.stderr,
                )
    return known


def crosscheck(entrez: Entrez, taxids: set[str], ours: set[str]) -> set[str]:
    """Independent derivation: all PAT records for these taxa, intersected locally.

    Uses a different Entrez path than classify() (taxon search rather than an
    epost'd id list), so a dropped id or a stale history key in one route shows
    up as a disagreement rather than as a shorter list.
    """
    found: set[str] = set()
    for taxid in sorted(taxids):
        if not taxid:
            continue
        result = entrez.get_json(
            "esearch.fcgi",
            {
                "db": "nuccore",
                "term": f"txid{taxid}[Organism:exp] AND gbdiv_pat[PROP]",
                "usehistory": "y",
                "retmax": "0",
                "retmode": "json",
            },
        )["esearchresult"]
        count = int(result["count"])
        if not count:
            continue
        accessions = entrez.accessions_of(result["webenv"], result["querykey"], count)
        found.update(base_accession(a) for a in accessions)
    return found & ours


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #


def write_organism_report(
    outdir: Path,
    organism: str,
    division: str,
    rows: list[dict[str, str]],
    fields: list[str],
    divisions: dict[str, str],
    titles: dict[str, str],
) -> list[str]:
    """Write the per-organism hit table and the bare accession list.

    Rule for segmented organisms (cchf, andv): a Pathoplexus record is flagged
    if *any* of its segments is a hit — revocation is per Pathoplexus accession,
    so a record with a patent segment cannot be partly revoked.  Every segment's
    division is emitted so disagreements are visible.
    """
    detail = outdir / f"{division.lower()}_{organism}.tsv"
    hits: list[str] = []
    with detail.open("w", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(
            ["ppx_accession", "ppx_accession_version", "organism", "insdc_accessions",
             "segment_divisions", "ncbi_titles"]
        )
        for row in rows:
            per_segment = []
            for field in fields:
                accession = base_accession(row.get(field) or "")
                if accession:
                    segment = field.removeprefix("insdcAccessionBase").lstrip("_") or "-"
                    per_segment.append((segment, accession, divisions.get(accession, "?")))
            if not any(state == division for _, _, state in per_segment):
                continue
            hits.append(row["accession"])
            writer.writerow(
                [
                    row["accession"],
                    f"{row['accession']}.{row['version']}",
                    organism,
                    ",".join(accession for _, accession, _ in per_segment),
                    ",".join(f"{segment}:{state}" for segment, _, state in per_segment),
                    " | ".join(
                        titles.get(accession, "") for _, accession, _ in per_segment
                    ).strip(" |"),
                ]
            )
    if hits:
        (outdir / f"revoke_{division.lower()}_{organism}.txt").write_text(
            "\n".join(hits) + "\n"
        )
    else:
        detail.unlink()
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Contact address for Entrez (required by NCBI)")
    parser.add_argument("--api-key", default=os.environ.get("NCBI_API_KEY"))
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--organism", action="append", help="Restrict to these organisms")
    parser.add_argument("--submitter", default="insdc_ingest_user")
    parser.add_argument(
        "--source-db",
        help="Filter on ncbiSourceDb (e.g. GenBank) instead of submitter. Catches "
        "GenBank-derived records whose latest version was revised by a curator.",
    )
    # NCBI recommends ~500 UIDs per request; larger accession lists can time out
    # in epost's internal accession->UID conversion step (NBK25499).
    parser.add_argument("--chunk-size", type=int, default=500, help="Accessions per epost")
    parser.add_argument("--crosscheck", action="store_true", help="Re-derive PAT set per taxon")
    args = parser.parse_args()

    outdir = args.outdir
    (outdir / "lapis").mkdir(parents=True, exist_ok=True)
    entrez = Entrez(args.email, args.api_key)
    if not args.api_key:
        print("No NCBI_API_KEY set: limited to 3 requests/s", file=sys.stderr)

    scope = ("ncbiSourceDb", args.source_db) if args.source_db else ("submitter", args.submitter)
    print(f"Scope: {scope[0]}={scope[1]}", file=sys.stderr)
    organisms = args.organism or discover_organisms()
    print(f"{len(organisms)} organisms: {' '.join(organisms)}", file=sys.stderr)

    records: dict[str, list[dict[str, str]]] = {}
    fields_by_organism: dict[str, list[str]] = {}
    taxids: dict[str, set[str]] = defaultdict(set)
    accession_to_ppx: dict[str, set[str]] = defaultdict(set)

    for organism in organisms:
        fields_by_organism[organism] = accession_fields(organism)
        rows = fetch_lapis_records(organism, scope, outdir / "lapis" / f"{organism}.tsv")
        records[organism] = rows
        for row in rows:
            taxids[organism].add((row.get("ncbiVirusTaxId") or "").strip())
            for field in fields_by_organism[organism]:
                accession = base_accession(row.get(field) or "")
                if accession:
                    accession_to_ppx[accession].add(f"{organism}/{row['accession']}")
        print(f"  {organism}: {len(rows)} records", file=sys.stderr)

    # One accession can back several Pathoplexus records, so classify the
    # deduplicated set and fan the answer back out.
    unique = sorted(accession_to_ppx)
    print(f"{len(unique)} unique INSDC accessions", file=sys.stderr)

    divisions = classify(entrez, unique, outdir / "divisions.tsv", args.chunk_size)

    hit_accessions = {
        division: sorted(a for a in unique if divisions.get(a) == division)
        for division in (d.upper() for d in CLASSIFIED_DIVISIONS)
    }
    titles = entrez.titles_of(sorted(set().union(*hit_accessions.values()))) if any(
        hit_accessions.values()
    ) else {}

    summary: dict[str, dict[str, list[str]]] = defaultdict(dict)
    for division, accessions in hit_accessions.items():
        for organism in organisms:
            summary[division][organism] = write_organism_report(
                outdir,
                organism,
                division,
                records[organism],
                fields_by_organism[organism],
                divisions,
                titles,
            )

    unresolved = sorted(a for a in unique if divisions.get(a) == "UNRESOLVED")
    unexpected = sorted(
        a for a in unique if divisions.get(a) not in {"VRL", "PAT", "SYN", "UNRESOLVED"}
    )

    lines = [
        "# GenBank patent sequences in Pathoplexus",
        "",
        f"Scope: `{scope[0]}={scope[1]}`, latest non-revoked versions.",
        f"{sum(len(r) for r in records.values())} records, {len(unique)} unique INSDC accessions.",
        "",
        "| organism | ingested | PAT records | SYN records |",
        "|---|---|---|---|",
    ]
    for organism in organisms:
        lines.append(
            f"| {organism} | {len(records[organism])} | "
            f"{len(summary['PAT'].get(organism, []))} | {len(summary['SYN'].get(organism, []))} |"
        )
    lines += [
        f"| **total** | {sum(len(r) for r in records.values())} | "
        f"{sum(len(v) for v in summary['PAT'].values())} | "
        f"{sum(len(v) for v in summary['SYN'].values())} |",
        "",
        f"Unresolved accessions (NOT classified, need a look): {len(unresolved)}",
        f"Unexpected divisions: {len(unexpected)}"
        + (f" -> {', '.join(f'{a}={divisions[a]}' for a in unexpected[:20])}" if unexpected else ""),
    ]
    if unresolved:
        (outdir / "unresolved.txt").write_text("\n".join(unresolved) + "\n")

    if args.crosscheck:
        lines += ["", "## Cross-check (per-taxon esearch, independent route)", ""]
        for organism in organisms:
            ours = {
                base_accession(row.get(field) or "")
                for row in records[organism]
                for field in fields_by_organism[organism]
            } - {""}
            independent = crosscheck(entrez, taxids[organism], ours)
            primary = {a for a in ours if divisions.get(a) == "PAT"}
            verdict = "agree" if independent == primary else "DISAGREE"
            lines.append(
                f"- {organism}: epost route {len(primary)}, taxon route {len(independent)} "
                f"-> {verdict}"
                + (
                    f" (only-taxon: {sorted(independent - primary)}; "
                    f"only-epost: {sorted(primary - independent)})"
                    if independent != primary
                    else ""
                )
            )

    report = outdir / "SUMMARY.md"
    report.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nWritten to {outdir}/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
