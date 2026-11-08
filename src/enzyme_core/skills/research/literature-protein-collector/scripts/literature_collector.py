#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Literature Protein Information Collector

Automates multi-source literature search and open-access full-text retrieval for a target
protein, so that an OpenCode/Claude agent can deep-read the full text and mine mutant /
mechanism information. Mirrors the architecture of the sibling `patent-protein-collector`
skill: this script ONLY does deterministic, reproducible work (querying APIs, downloading
open-access full text, caching, sequence alignment, wild-type self-verification, CSV/report
assembly). It never calls an LLM and never fabricates data on failure -- a source that errors
out is skipped with a warning, and papers whose full text cannot be legally obtained are
labeled "abstract_only" rather than silently dropped or faked.

Relevance judgment, tiered analysis of full text / partial HTML / abstracts / metadata,
mutant coreference resolution and experimental-data mining, and the theoretical/mechanistic
synthesis are all performed by the calling agent, which feeds its structured findings back into this script via `from-analysis`
to produce the final mutant CSV and Markdown report (reusing the same self-verification and
alignment logic used for --from-analysis in the patent skill).

Pipeline:
  1. search           -- query multiple literature databases, dedupe, write candidates.json
     (agent reviews candidates.json by title/abstract only and writes selected.json)
  2. fetch-fulltext    -- for the agent-selected subset, resolve + download legal open-access
                          full text (PDF or plain text), write raw_data.json
     (agent deep-reads the downloaded files + raw_data.json, writes analysis.json)
  3. from-analysis     -- validates the agent's analysis.json, aligns each paper's reported
                          sequence against the user's target sequence, self-verifies wild-type
                          residues, and writes the final mutants.csv + report.md
  4. align             -- standalone utility to map positions between two sequences
"""

import argparse
import csv
import glob
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

SCRIPT_VERSION = "1.1"
EXPECTED_IO_ERRORS = (OSError, json.JSONDecodeError, UnicodeDecodeError)
EXPECTED_NETWORK_ERRORS = (RuntimeError, TimeoutError, urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError)
EXPECTED_SOURCE_ERRORS = EXPECTED_NETWORK_ERRORS + (ValueError, KeyError, ET.ParseError)

# =====================================================================
# 0. HTTP layer: per-host rate limiting, retries, User-Agent
# =====================================================================

CONTACT_EMAIL = os.environ.get("LIT_COLLECTOR_EMAIL") or os.environ.get("USER_EMAIL") or "anonymous@example.com"
USER_AGENT = f"literature-protein-collector/{SCRIPT_VERSION} (mailto:{CONTACT_EMAIL})"


def set_contact_email(email):
    """Updates the module-level CONTACT_EMAIL/USER_AGENT used by every source client. Called
    once per command after `load_config()` resolves the effective contact email (env var >
    config.json > built-in default), so a config.json-only setting (no env var set) still
    takes effect without needing every call site to thread an email parameter through."""
    global CONTACT_EMAIL, USER_AGENT
    if email:
        CONTACT_EMAIL = email
        USER_AGENT = f"literature-protein-collector/{SCRIPT_VERSION} (mailto:{CONTACT_EMAIL})"

# Seconds to wait between consecutive requests to the same host (politeness / published limits).
RATE_LIMITS = {
    "www.ebi.ac.uk": 0.34,              # Europe PMC: ~3 req/s
    "api.openalex.org": 0.15,           # OpenAlex polite pool: ~10 req/s w/ key, be conservative w/o
    "eutils.ncbi.nlm.nih.gov": 0.34,    # NCBI E-utilities: 3 req/s w/o key
    "api.semanticscholar.org": 1.05,    # Semantic Scholar public rate limit: ~1 req/s
    "api.crossref.org": 0.2,
    "api.unpaywall.org": 0.2,
    "api.core.ac.uk": 0.5,
    "export.arxiv.org": 3.1,            # arXiv Terms of Use: max 1 req / 3s
    "arxiv.org": 3.1,
}

_last_call = {}
_rate_lock = threading.Lock()


def _throttle(host):
    with _rate_lock:
        now = time.time()
        wait = RATE_LIMITS.get(host, 0.25) - (now - _last_call.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.time()


def http_request(url, headers=None, max_retries=4, timeout=40):
    """Fetches raw bytes with per-host throttling and exponential-backoff retries on
    429/5xx/network errors. Raises RuntimeError on non-retryable HTTP errors or exhausted
    retries -- callers decide whether that failure should abort the whole run (a single
    authoritative source) or just be skipped (one of several redundant sources)."""
    host = urllib.parse.urlparse(url).netloc
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    last_err = None
    for attempt in range(max_retries):
        _throttle(host)
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read(), dict(resp.headers), resp.status
        except urllib.error.HTTPError as e:
            body = e.read()[:500]
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                backoff = min(30, 2 ** attempt + 1)
                print(f"[Warning] HTTP {e.code} from {host} (attempt {attempt+1}/{max_retries}), "
                      f"retrying in {backoff}s...", file=sys.stderr)
                time.sleep(backoff)
                last_err = f"HTTP {e.code}: {body}"
                continue
            raise RuntimeError(f"HTTP {e.code} error fetching {url}: {body}")
        except urllib.error.URLError as e:
            if attempt < max_retries - 1:
                backoff = min(30, 2 ** attempt + 1)
                print(f"[Warning] Network error ({e.reason}) fetching {host} "
                      f"(attempt {attempt+1}/{max_retries}), retrying in {backoff}s...", file=sys.stderr)
                time.sleep(backoff)
                last_err = str(e.reason)
                continue
            raise RuntimeError(f"Network error fetching {url}: {e.reason}")
    raise RuntimeError(f"Failed to fetch {url} after {max_retries} attempts: {last_err}")


def http_get_json(url, headers=None):
    body, _, _ = http_request(url, headers=headers)
    return json.loads(body.decode("utf-8", errors="replace"))


class ResponseCache:
    """Persists successful search-API responses to local disk keyed by URL, so re-running the
    same search (e.g. after tweaking a downstream step) does not re-hit rate-limited APIs."""

    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def _path(self, key):
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, f"{digest}.json")

    def get(self, key):
        p = self._path(key)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except EXPECTED_IO_ERRORS:
                return None
        return None

    def set(self, key, value):
        with open(self._path(key), "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)


def cached_get_json(cache, url, headers=None):
    if cache is not None:
        hit = cache.get(url)
        if hit is not None:
            return hit
    data = http_get_json(url, headers=headers)
    if cache is not None:
        cache.set(url, data)
    return data


# =====================================================================
# 0b. Configuration loader (config.json + environment variable overrides)
# =====================================================================

# Maps config.json / effective-config keys to their environment variable equivalents.
CONFIG_ENV_MAP = {
    "contact_email": "LIT_COLLECTOR_EMAIL",
    "unpaywall_email": "UNPAYWALL_EMAIL",
    "ncbi_api_key": "NCBI_API_KEY",
    "openalex_api_key": "OPENALEX_API_KEY",
    "semantic_scholar_api_key": "SEMANTIC_SCHOLAR_API_KEY",
    "core_api_key": "CORE_API_KEY",
    "paddleocr_vl_token": "PADDLEOCR_VL_TOKEN",
}
# Fields worth warning about if they're only found in a plaintext config.json (real API
# keys/tokens); plain contact/email fields aren't secrets so we don't nag about those.
CONFIG_SECRET_FIELDS = {"ncbi_api_key", "openalex_api_key", "semantic_scholar_api_key",
                         "core_api_key", "paddleocr_vl_token"}


def load_config(config_path=None):
    """
    Loads all of this skill's credentials (API keys + contact emails) from config.json and/or
    environment variables, using the same precedence rule as the sibling patent-protein-collector
    skill: environment variables take priority over config.json. This lets you keep config.json
    free of plaintext secrets (or check it in without them) and inject credentials via the
    shell/session environment instead. If a secret is only found in config.json, a warning is
    printed to remind you not to commit a filled-in config.json to version control.

    Search order for the config file: an explicit `config_path` argument, then `./config.json`
    (relative to the current working directory), then `config.json` next to this script (the
    skill root, since `scripts/` sits directly under it).
    """
    search_paths = []
    if config_path:
        search_paths.append(config_path)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    search_paths.extend([
        "./config.json",
        os.path.join(script_dir, "..", "config.json"),
        os.path.join(script_dir, "config.json"),
    ])

    file_config = {}
    for path in search_paths:
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    file_config = json.load(f)
                print(f"[Info] Loaded configuration from {path}")
                break
            except EXPECTED_IO_ERRORS as e:
                print(f"[Warning] Failed to parse config file {path}: {e}", file=sys.stderr)

    config = {}
    secrets_from_file = []
    for key, env_var in CONFIG_ENV_MAP.items():
        env_val = os.environ.get(env_var, "")
        file_val = file_config.get(key, "") or ""
        if env_val:
            config[key] = env_val
        elif file_val:
            config[key] = file_val
            if key in CONFIG_SECRET_FIELDS:
                secrets_from_file.append(key)
        else:
            config[key] = ""

    if secrets_from_file:
        print(
            f"[Warning] {', '.join(secrets_from_file)} loaded from config.json in plaintext. "
            f"Prefer setting the corresponding environment variable instead "
            f"({', '.join(CONFIG_ENV_MAP[k] for k in secrets_from_file)}), and never commit a "
            f"filled-in config.json to version control.", file=sys.stderr
        )

    return config


# =====================================================================
# 1. Pure-Python sequence alignment (Needleman-Wunsch) + self-verification
#    (same algorithm as the patent-protein-collector skill, so mutant positions reported
#    against a paper's own sequence can be remapped onto the user's target sequence and
#    checked for consistency, exactly like the patent pipeline does for hit_sequence.)
# =====================================================================

def needleman_wunsch(seq1, seq2, match=2, mismatch=-1, gap=-1):
    n, m = len(seq1), len(seq2)
    score = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        score[i][0] = i * gap
    for j in range(m + 1):
        score[0][j] = j * gap
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            s_match = score[i - 1][j - 1] + (match if seq1[i - 1] == seq2[j - 1] else mismatch)
            s_delete = score[i - 1][j] + gap
            s_insert = score[i][j - 1] + gap
            score[i][j] = max(s_match, s_delete, s_insert)

    align1, align2 = [], []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and score[i][j] == score[i - 1][j - 1] + (match if seq1[i - 1] == seq2[j - 1] else mismatch):
            align1.append(seq1[i - 1]); align2.append(seq2[j - 1]); i -= 1; j -= 1
        elif i > 0 and (j == 0 or score[i][j] == score[i - 1][j] + gap):
            align1.append(seq1[i - 1]); align2.append('-'); i -= 1
        else:
            align1.append('-'); align2.append(seq2[j - 1]); j -= 1
    return "".join(reversed(align1)), "".join(reversed(align2))


def map_position_from_alignment(align1, align2, pos_1idx, seq1_len):
    try:
        pos_1idx = int(pos_1idx)
    except (TypeError, ValueError):
        return "Invalid Position", None, None
    if pos_1idx < 1 or pos_1idx > seq1_len:
        return "Out of Range", None, None

    idx1 = idx2 = 0
    for a1, a2 in zip(align1, align2):
        if a1 != '-':
            idx1 += 1
        if a2 != '-':
            idx2 += 1
        if a1 != '-' and idx1 == pos_1idx:
            if a2 != '-':
                return idx2, a1, a2
            return "Gap (Insertion)", a1, "-"
    return "Not Found", None, None


def map_multiple_positions(paper_seq, target_seq, positions):
    """Aligns paper_seq against target_seq ONCE, then maps every position in `positions`
    against that single alignment (avoids re-running O(n*m) alignment per mutant)."""
    if not paper_seq or not target_seq:
        return {p: (None, None, None) for p in positions}
    align1, align2 = needleman_wunsch(paper_seq, target_seq)
    return {p: map_position_from_alignment(align1, align2, p, len(paper_seq)) for p in positions}


def verify_mutation(paper_seq, paper_pos, wild_type):
    """Cross-checks an extracted mutant's wild-type residue against the actual residue at that
    position in the paper's own reported sequence, to catch LLM extraction hallucinations,
    off-by-one numbering (e.g. mature-protein vs precursor numbering), or OCR/typo noise in the
    source PDF."""
    if not paper_pos or not paper_seq:
        return "N/A", "No paper position or paper sequence available for verification.", wild_type
    try:
        pos_idx = int(paper_pos) - 1
    except (TypeError, ValueError):
        return "INVALID_POSITION", f"Invalid position value: {paper_pos}.", wild_type
    if not (0 <= pos_idx < len(paper_seq)):
        return "OUT_OF_RANGE", f"Position {paper_pos} is out of range for sequence length {len(paper_seq)}.", wild_type
    actual = paper_seq[pos_idx]
    if wild_type:
        if actual == wild_type:
            return "PASSED", f"Verified: found {wild_type} at position {paper_pos}.", wild_type
        return "FAILED", f"Discrepancy: expected {wild_type} at position {paper_pos}, but sequence has {actual}.", wild_type
    return "AUTO-FILLED", f"Auto-filled wild-type residue {actual} at position {paper_pos}.", actual


VALID_AMINO_ACIDS = set("ACDEFGHIKLMNPQRSTVWYXBZJUO")


def clean_and_validate_sequence(seq, label, allow_empty=False):
    if not seq:
        if allow_empty:
            return ""
        raise ValueError(f"{label} is required but was empty or not provided.")
    stripped = re.sub(r"\s", "", seq).upper()
    if any(ch.isdigit() for ch in stripped):
        print(f"[Warning] {label} contained digits (likely line numbers from a pasted sequence) - stripping them out.")
        stripped = re.sub(r"[0-9]", "", stripped)
    if not stripped:
        if allow_empty:
            return ""
        raise ValueError(f"{label} is empty after stripping whitespace/digits.")
    invalid = sorted(set(ch for ch in stripped if ch not in VALID_AMINO_ACIDS))
    if invalid:
        raise ValueError(
            f"{label} contains character(s) not in the standard/extended amino-acid alphabet: {invalid}. "
            f"Valid letters are ACDEFGHIKLMNPQRSTVWY plus ambiguity codes B/Z/X/J/U/O."
        )
    return stripped


# =====================================================================
# 2. Source clients -- each returns a list of normalized candidate dicts.
#    A source that errors is caught by the caller and skipped with a warning; it never
#    silently returns fabricated results.
# =====================================================================

def make_candidate(source, **kwargs):
    base = {
        "title": None, "abstract": None, "authors": [], "year": None, "journal": None,
        "doi": None, "pmid": None, "pmcid": None, "arxiv_id": None, "url": None,
        "citation_count": None, "is_oa": None, "oa_pdf_url": None, "categories": [],
    }
    base.update({k: v for k, v in kwargs.items() if v is not None})
    base["sources"] = [source]
    return base


def _safe_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _strip_tags(text):
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&(?:apos|#39|#x27);", "'", text)
    text = re.sub(r"&#x?[0-9a-fA-F]+;", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _get_header(headers, name):
    """Case-insensitive header lookup -- servers are inconsistent about Content-Type casing."""
    name = name.lower()
    for k, v in (headers or {}).items():
        if k.lower() == name:
            return v
    return None


DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")


def _extract_doi(text):
    """Best-effort DOI extraction from arbitrary text/URLs (e.g. a landing-page URL that embeds
    a DOI but whose source API didn't return a dedicated DOI field). Used to backfill missing
    DOIs before deduping, so the same paper found via two sources still merges into one
    candidate instead of appearing twice just because one source's DOI field was empty."""
    if not text:
        return None
    m = DOI_RE.search(text)
    return m.group(0).rstrip(".,;)") if m else None


def search_europepmc(query, max_results, cache=None):
    """Europe PMC covers PubMed, PMC full text, and preprints (bioRxiv/medRxiv/ChemRxiv) in one
    index -- add `AND SRC:PPR` to `query` to specifically target preprints instead of running a
    separate bioRxiv date-range crawl (bioRxiv's own API has no server-side keyword search)."""
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode({
        "query": query, "format": "json", "pageSize": min(max_results, 100), "resultType": "core",
    })
    data = cached_get_json(cache, url)
    out = []
    for hit in data.get("resultList", {}).get("result", [])[:max_results]:
        out.append(make_candidate(
            "europepmc",
            title=hit.get("title"), abstract=hit.get("abstractText"),
            authors=[a.strip() for a in (hit.get("authorString") or "").split(",") if a.strip()],
            year=_safe_int(hit.get("pubYear")), journal=hit.get("journalTitle"),
            doi=hit.get("doi"), pmid=hit.get("pmid"), pmcid=hit.get("pmcid"),
            is_oa=(hit.get("isOpenAccess") == "Y"),
            citation_count=_safe_int(hit.get("citedByCount")),
            url=f"https://europepmc.org/article/{hit.get('source', 'MED')}/{hit.get('id', '')}",
        ))
    return out


def _reconstruct_abstract(inverted_index):
    if not inverted_index:
        return None
    positions = {}
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions[i] = word
    return " ".join(positions[i] for i in sorted(positions))


def search_openalex(query, max_results, api_key=None, cache=None):
    params = {"search": query, "per-page": min(max_results, 100),
              "select": "id,doi,title,abstract_inverted_index,publication_year,primary_location,"
                        "authorships,cited_by_count,open_access,type"}
    if api_key:
        params["api_key"] = api_key
    params["mailto"] = CONTACT_EMAIL
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    data = cached_get_json(cache, url)
    out = []
    for w in data.get("results", [])[:max_results]:
        loc = w.get("primary_location") or {}
        source = loc.get("source") or {}
        oa = w.get("open_access") or {}
        doi = (w.get("doi") or "").replace("https://doi.org/", "") if w.get("doi") else None
        out.append(make_candidate(
            "openalex",
            title=w.get("title"), abstract=_reconstruct_abstract(w.get("abstract_inverted_index")),
            authors=[a.get("author", {}).get("display_name") for a in w.get("authorships", [])
                     if a.get("author", {}).get("display_name")],
            year=w.get("publication_year"), journal=source.get("display_name"),
            doi=doi, is_oa=oa.get("is_oa"), oa_pdf_url=oa.get("oa_url") or loc.get("pdf_url"),
            citation_count=w.get("cited_by_count"), url=w.get("id"),
        ))
    return out


def _parse_pubmed_xml(xml_bytes):
    out = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        print(f"[Warning] Failed to parse PubMed XML: {e}", file=sys.stderr)
        return out
    for art in root.findall(".//PubmedArticle"):
        medline = art.find("MedlineCitation")
        if medline is None:
            continue
        pmid_el = medline.find("PMID")
        pmid = pmid_el.text if pmid_el is not None else None
        article = medline.find("Article")
        if article is None:
            continue
        title_el = article.find("ArticleTitle")
        title = "".join(title_el.itertext()) if title_el is not None else None
        abstract_parts = [
            "".join(ab.itertext()) for ab in article.findall(".//AbstractText")
        ]
        abstract = " ".join(abstract_parts) if abstract_parts else None
        journal_el = article.find("Journal/Title")
        journal = journal_el.text if journal_el is not None else None
        year = None
        year_el = article.find("Journal/JournalIssue/PubDate/Year")
        if year_el is not None:
            year = _safe_int(year_el.text)
        else:
            medline_date = article.find("Journal/JournalIssue/PubDate/MedlineDate")
            if medline_date is not None and medline_date.text:
                m = re.search(r"\d{4}", medline_date.text)
                if m:
                    year = int(m.group())
        authors = []
        for a in article.findall("AuthorList/Author"):
            last = a.find("LastName")
            fore = a.find("ForeName")
            if last is not None:
                name = last.text + ((" " + fore.text) if fore is not None else "")
                authors.append(name)
        doi = None
        pmcid = None
        for aid in art.findall(".//ArticleId"):
            if aid.get("IdType") == "doi":
                doi = aid.text
            elif aid.get("IdType") == "pmc":
                pmcid = aid.text
        out.append(make_candidate(
            "pubmed", title=title, abstract=abstract, authors=authors, year=year,
            journal=journal, doi=doi, pmid=pmid, pmcid=pmcid,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
        ))
    return out


def search_pubmed(query, max_results, api_key=None, cache=None):
    params = {"db": "pubmed", "term": query, "retmax": max_results, "retmode": "json", "sort": "relevance"}
    if api_key:
        params["api_key"] = api_key
    params["email"] = CONTACT_EMAIL
    esearch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode(params)
    data = cached_get_json(cache, esearch_url)
    pmids = data.get("esearchresult", {}).get("idlist", [])
    if not pmids:
        return []
    fetch_params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml", "rettype": "abstract",
                     "email": CONTACT_EMAIL}
    if api_key:
        fetch_params["api_key"] = api_key
    efetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode(fetch_params)
    cache_hit = cache.get(efetch_url) if cache else None
    if cache_hit is not None:
        return cache_hit if isinstance(cache_hit, list) else []
    body, _, _ = http_request(efetch_url, headers={"Accept": "application/xml"})
    parsed = _parse_pubmed_xml(body)
    if cache is not None:
        cache.set(efetch_url, parsed)
    return parsed


def search_semanticscholar(query, max_results, api_key=None, cache=None):
    params = {"query": query, "limit": min(max_results, 100),
              "fields": "title,abstract,year,venue,externalIds,authors,citationCount,"
                        "isOpenAccess,openAccessPdf,url"}
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params)
    headers = {"x-api-key": api_key} if api_key else None
    try:
        data = cached_get_json(cache, url, headers=headers)
    except RuntimeError as e:
        # A rejected/invalid key (401/403) shouldn't take down this whole source when the
        # anonymous tier would have worked fine -- degrade to keyless access once instead of
        # failing the entire search just because one optional credential was bad.
        if api_key and ("HTTP 401" in str(e) or "HTTP 403" in str(e)):
            print(f"[Warning] Semantic Scholar rejected the configured API key ({e}); "
                  f"retrying without it (anonymous, rate-limited).", file=sys.stderr)
            data = cached_get_json(cache, url, headers=None)
        else:
            raise
    out = []
    for p in data.get("data", [])[:max_results]:
        ext = p.get("externalIds") or {}
        oa_pdf = p.get("openAccessPdf") or {}
        out.append(make_candidate(
            "semanticscholar",
            title=p.get("title"), abstract=p.get("abstract"),
            authors=[a.get("name") for a in (p.get("authors") or []) if a.get("name")],
            year=p.get("year"), journal=p.get("venue"),
            doi=ext.get("DOI"), pmid=ext.get("PubMed"), arxiv_id=ext.get("ArXiv"),
            is_oa=p.get("isOpenAccess"), oa_pdf_url=oa_pdf.get("url"),
            citation_count=p.get("citationCount"), url=p.get("url"),
        ))
    return out


def search_crossref(query, max_results, cache=None):
    """CrossRef is primarily useful for DOI resolution and citation counts; abstracts (when
    present) are JATS-tagged and are stripped of markup here."""
    params = {"query.bibliographic": query, "rows": min(max_results, 50),
              "select": "DOI,title,abstract,author,published-print,published-online,"
                        "container-title,is-referenced-by-count,link"}
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    data = cached_get_json(cache, url, headers={"mailto": CONTACT_EMAIL})
    out = []
    for w in data.get("message", {}).get("items", [])[:max_results]:
        date_parts = ((w.get("published-print") or w.get("published-online") or {})
                      .get("date-parts", [[None]]))
        year = date_parts[0][0] if date_parts and date_parts[0] else None
        authors = [f"{a.get('given', '')} {a.get('family', '')}".strip()
                   for a in w.get("author", []) if a.get("family")]
        oa_pdf_url = None
        for l in (w.get("link") or []):
            if l.get("content-type") == "application/pdf":
                oa_pdf_url = l.get("URL")
                break
        titles = w.get("title") or []
        out.append(make_candidate(
            "crossref",
            title=titles[0] if titles else None,
            abstract=_strip_tags(w["abstract"]) if w.get("abstract") else None,
            authors=authors, year=year,
            journal=(w.get("container-title") or [None])[0],
            doi=w.get("DOI"), oa_pdf_url=oa_pdf_url,
            citation_count=w.get("is-referenced-by-count"),
            url=f"https://doi.org/{w.get('DOI')}" if w.get("DOI") else None,
        ))
    return out


ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def search_arxiv(query, max_results, cache=None):
    params = {"search_query": query, "start": 0, "max_results": min(max_results, 50)}
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    cache_hit = cache.get(url) if cache else None
    if cache_hit is not None:
        return cache_hit if isinstance(cache_hit, list) else []
    body, _, _ = http_request(url, headers={"Accept": "application/atom+xml"})
    root = ET.fromstring(body)
    out = []
    for entry in root.findall("atom:entry", ARXIV_NS):
        id_url = entry.findtext("atom:id", default="", namespaces=ARXIV_NS)
        arxiv_id = id_url.rsplit("/", 1)[-1] if id_url else None
        published = entry.findtext("atom:published", default="", namespaces=ARXIV_NS)
        year = _safe_int(published[:4]) if published else None
        doi = entry.findtext("arxiv:doi", default=None, namespaces=ARXIV_NS)
        authors = [a.findtext("atom:name", default="", namespaces=ARXIV_NS)
                   for a in entry.findall("atom:author", ARXIV_NS)]
        categories = [c.get("term") for c in entry.findall("atom:category", ARXIV_NS) if c.get("term")]
        out.append(make_candidate(
            "arxiv",
            title=re.sub(r"\s+", " ", entry.findtext("atom:title", default="", namespaces=ARXIV_NS)).strip(),
            abstract=re.sub(r"\s+", " ", entry.findtext("atom:summary", default="", namespaces=ARXIV_NS)).strip(),
            authors=[a for a in authors if a], year=year, journal="arXiv preprint",
            doi=doi, arxiv_id=arxiv_id, is_oa=True, categories=categories,
            oa_pdf_url=f"https://arxiv.org/pdf/{arxiv_id}.pdf" if arxiv_id else None,
            url=id_url or None,
        ))
    if cache is not None:
        cache.set(url, out)
    return out


def search_core(query, max_results, api_key, cache=None):
    if not api_key:
        print("[Warning] CORE_API_KEY not set - skipping CORE search. Register a free key at "
              "https://core.ac.uk/services/api and set CORE_API_KEY to include this source.",
              file=sys.stderr)
        return []
    url = "https://api.core.ac.uk/v3/search/works/?" + urllib.parse.urlencode(
        {"q": query, "limit": min(max_results, 100)})
    data = cached_get_json(cache, url, headers={"Authorization": f"Bearer {api_key}"})
    out = []
    for w in data.get("results", [])[:max_results]:
        out.append(make_candidate(
            "core",
            title=w.get("title"), abstract=w.get("abstract"),
            authors=[a.get("name") for a in (w.get("authors") or []) if a.get("name")],
            year=w.get("yearPublished"), journal=(w.get("journals") or [{}])[0].get("title")
            if w.get("journals") else None,
            doi=w.get("doi"), is_oa=True, oa_pdf_url=w.get("downloadUrl"),
            citation_count=w.get("citationCount"), url=w.get("sourceFulltextUrls", [None])[0] or w.get("id"),
        ))
    return out


def search_doaj(query, max_results, cache=None):
    """DOAJ (Directory of Open Access Journals) indexes articles from journals that are
    peer-reviewed AND fully open access by the journal's own policy -- everything it returns is
    is_oa=True by construction, unlike aggregators that mix OA and paywalled content. No API key
    is required for search. Its `fulltext` link is often the publisher's HTML landing page
    rather than a direct PDF -- resolve_and_fetch_fulltext() already only accepts a response
    that actually starts with %PDF, so a landing-page link here just fails closed rather than
    silently saving an HTML page as if it were the paper."""
    url = ("https://doaj.org/api/search/articles/" + urllib.parse.quote(query, safe="") + "?"
           + urllib.parse.urlencode({"pageSize": min(max_results, 100)}))
    data = cached_get_json(cache, url)
    out = []
    for item in data.get("results", [])[:max_results]:
        bib = item.get("bibjson", {})
        doi = None
        for ident in bib.get("identifier", []):
            if ident.get("type", "").lower() == "doi":
                doi = ident.get("id")
                break
        fulltext_url = None
        for link in bib.get("link", []):
            if link.get("type") == "fulltext":
                fulltext_url = link.get("url")
                break
        journal = bib.get("journal", {})
        year = _safe_int(bib.get("year"))
        out.append(make_candidate(
            "doaj",
            title=bib.get("title"), abstract=bib.get("abstract"),
            authors=[a.get("name") for a in (bib.get("author") or []) if a.get("name")],
            year=year, journal=journal.get("title"),
            doi=doi, is_oa=True, oa_pdf_url=fulltext_url,
            url=fulltext_url or (f"https://doi.org/{doi}" if doi else None),
        ))
    return out


SOURCE_FUNCS = {
    "europepmc": lambda q, n, ctx: search_europepmc(q, n, cache=ctx["cache"]),
    "openalex": lambda q, n, ctx: search_openalex(q, n, api_key=ctx.get("openalex_api_key"), cache=ctx["cache"]),
    "pubmed": lambda q, n, ctx: search_pubmed(q, n, api_key=ctx.get("ncbi_api_key"), cache=ctx["cache"]),
    "semanticscholar": lambda q, n, ctx: search_semanticscholar(q, n, api_key=ctx.get("s2_api_key"), cache=ctx["cache"]),
    "crossref": lambda q, n, ctx: search_crossref(q, n, cache=ctx["cache"]),
    "arxiv": lambda q, n, ctx: search_arxiv(q, n, cache=ctx["cache"]),
    "core": lambda q, n, ctx: search_core(q, n, ctx.get("core_api_key"), cache=ctx["cache"]),
    "doaj": lambda q, n, ctx: search_doaj(q, n, cache=ctx["cache"]),
}

DEFAULT_SOURCES = ["europepmc", "openalex", "pubmed", "semanticscholar", "crossref", "arxiv"]
# Deliberately NOT included even as opt-in sources (see reference.md "为什么不做" section):
#   - Google Scholar / SSRN: no stable public API, scraping trips bot-detection (Cloudflare/
#     CAPTCHA) and risks violating ToS -- exactly the fragility the sibling paper-search-mcp
#     project's own docs report.
#   - Sci-Hub: bypasses publisher paywalls without authorization, incompatible with this
#     skill's "legal open access only" principle regardless of jurisdiction-dependent legality.


# =====================================================================
# 3. Dedup + candidate assembly
# =====================================================================

def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def _merge_candidate(existing, new):
    existing["sources"] = sorted(set(existing.get("sources", []) + new.get("sources", [])))
    for k in ("pmid", "pmcid", "arxiv_id", "doi", "oa_pdf_url", "abstract", "journal", "year", "url"):
        if not existing.get(k) and new.get(k):
            existing[k] = new[k]
    if new.get("is_oa"):
        existing["is_oa"] = True
    if (new.get("citation_count") or 0) > (existing.get("citation_count") or 0):
        existing["citation_count"] = new.get("citation_count")
    existing.setdefault("authors", [])
    if not existing["authors"] and new.get("authors"):
        existing["authors"] = new["authors"]


def dedupe_candidates(candidates):
    by_doi, no_doi_title, out = {}, {}, []
    for c in candidates:
        doi = (c.get("doi") or "").lower().strip().rstrip(".")
        if doi:
            if doi in by_doi:
                _merge_candidate(by_doi[doi], c)
            else:
                by_doi[doi] = c
                out.append(c)
            continue
        key = _norm_title(c.get("title"))
        if key and key in no_doi_title:
            _merge_candidate(no_doi_title[key], c)
        else:
            if key:
                no_doi_title[key] = c
            out.append(c)
    return out


def cmd_search(args):
    config = load_config(args.config)
    set_contact_email(config.get("contact_email"))
    cache = ResponseCache(args.cache_dir) if not args.no_cache else None
    ctx = {"cache": cache, "openalex_api_key": config.get("openalex_api_key") or None,
           "ncbi_api_key": config.get("ncbi_api_key") or None,
           "s2_api_key": config.get("semantic_scholar_api_key") or None,
           "core_api_key": config.get("core_api_key") or None}
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = [s for s in sources if s not in SOURCE_FUNCS]
    if unknown:
        print(f"[Error] Unknown source(s): {unknown}. Valid sources: {sorted(SOURCE_FUNCS)}", file=sys.stderr)
        sys.exit(1)

    queries = args.query  # argparse action="append" -- always a list, even for one --query
    all_candidates, errors = [], []
    tasks = [(q, s) for q in queries for s in sources]

    def _run_one(query, name):
        try:
            return query, name, SOURCE_FUNCS[name](query, args.max_results, ctx)
        except EXPECTED_SOURCE_ERRORS as e:
            return query, name, e

    # All (query, source) combinations run in ONE process, sharing the same in-memory per-host
    # rate limiter (_last_call/_rate_lock) -- this is the reason multiple query phrasings are
    # supported here as repeated --query flags rather than by telling the agent to launch several
    # separate `search` processes in parallel: separate processes would each track their own
    # rate-limit state and could collectively exceed a host's actual published limit, which is
    # both impolite and more likely to trigger 429s that slow the whole thing down anyway. A
    # generous worker cap is safe because the bottleneck is per-host politeness sleeps, not CPU or
    # connection-count -- more threads mostly just means less idle waiting, not more host load.
    with ThreadPoolExecutor(max_workers=min(16, len(tasks))) as pool:
        futures = [pool.submit(_run_one, q, s) for q, s in tasks]
        for fut in as_completed(futures):
            query, name, result = fut.result()
            label = f"{name}" if len(queries) == 1 else f"{name} [{query!r}]"
            if isinstance(result, Exception):
                print(f"[Warning] Source '{label}' failed and was skipped: {result}", file=sys.stderr)
                errors.append({"source": name, "query": query, "error": str(result)})
            else:
                print(f"[Info] {label}: {len(result)} result(s)")
                all_candidates.extend(result)

    if errors and len(errors) == len(tasks):
        print(f"[Error] All {len(tasks)} (query, source) combination(s) failed - no candidates "
              f"were retrieved. This is a network/API problem, not \"no results found\": do not "
              f"report an empty literature landscape to the user without investigating the "
              f"errors above first.", file=sys.stderr)
        sys.exit(1)

    # Backfill missing DOIs from the paper's own URL (e.g. a landing page containing
    # "doi.org/10.xxxx/yyy") before deduping, so the same paper found via two sources without a
    # dedicated DOI field on one of them still merges into a single candidate.
    for c in all_candidates:
        if not c.get("doi"):
            found = _extract_doi(c.get("url") or "") or _extract_doi(c.get("oa_pdf_url") or "")
            if found:
                c["doi"] = found

    # dedupe_candidates() also transparently merges the same paper found under different query
    # phrasings (it merges by DOI/title regardless of which query surfaced it), so running
    # several phrasings for broader recall never produces duplicate rows in the output.
    deduped = dedupe_candidates(all_candidates)
    deduped.sort(key=lambda c: (c.get("citation_count") or 0), reverse=True)
    for i, c in enumerate(deduped, 1):
        c["candidate_id"] = f"c{i}"

    if not deduped:
        print(f"[Warning] 0 candidates found for {queries!r} across "
              f"{len(sources)} source(s). Try broader/alternate keywords "
              f"(synonyms, gene name instead of common name, organism name) before concluding "
              f"no relevant literature exists.", file=sys.stderr)

    out = {
        "queries": queries,
        "sources_requested": sources,
        "source_errors": errors,
        "total_raw_results": len(all_candidates),
        "total_unique_candidates": len(deduped),
        "candidates": deduped,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[Success] {len(deduped)} unique candidate(s) (from {len(all_candidates)} raw hits "
          f"across {len(tasks) - len(errors)}/{len(tasks)} query×source combination(s)) "
          f"written to {args.output}")


# =====================================================================
# 4. Full-text resolution + download
# =====================================================================

def _write_bytes(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def _write_text(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _download_pdf(url, path, min_bytes=2000):
    """Downloads a PDF and verifies it is not empty/corrupted (starts with %PDF, reasonable
    size). Returns True on success, False on any failure -- callers use this to fall through
    to the next candidate open-access source rather than treating it as fatal."""
    try:
        body, headers, status = http_request(url, headers={"Accept": "application/pdf,*/*"}, timeout=90)
    except EXPECTED_NETWORK_ERRORS as e:
        print(f"[Info] PDF download failed for {url}: {e}", file=sys.stderr)
        return False
    if not body.startswith(b"%PDF") or len(body) < min_bytes:
        print(f"[Info] Response from {url} does not look like a valid PDF (size={len(body)}), skipping.",
              file=sys.stderr)
        return False
    _write_bytes(path, body)
    return True


META_TAG_RE = re.compile(r"<meta\s+([^>]*)>", re.IGNORECASE)
META_ATTR_RE = re.compile(r'([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*"([^"]*)"'
                           r"|([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*'([^']*)'")
ARTICLE_TAG_RE = re.compile(r"<article\b[^>]*>(.*?)</article>", re.IGNORECASE | re.DOTALL)
P_TAG_RE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
NON_CONTENT_BLOCK_RE = re.compile(
    r"<(script|style|nav|header|footer|aside|form)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def _parse_meta_tags(html_text):
    """Parses every <meta ...> tag into a dict of its attributes, tolerant of attribute order
    and quote style (single vs. double)."""
    metas = []
    for tag_match in META_TAG_RE.finditer(html_text):
        attrs = {}
        for m in META_ATTR_RE.finditer(tag_match.group(1)):
            if m.group(1):
                attrs[m.group(1).lower()] = m.group(2)
            else:
                attrs[m.group(3).lower()] = m.group(4)
        if attrs:
            metas.append(attrs)
    return metas


def _extract_citation_meta(html_text):
    """Extracts Highwire Press / Google Scholar style `<meta name="citation_*">` tags. Academic
    publishers (PMC, PLOS, Frontiers, MDPI, BMC, Wiley, Springer, ...) embed these on article
    landing pages specifically so that reference managers and indexers can programmatically find
    the real PDF URL and bibliographic metadata -- this is far more reliable than generically
    scraping the visible page, and is the reason a "landing page" URL is worth a second look
    instead of being treated as a dead end whenever it doesn't respond with a raw PDF."""
    result = {"citation_authors": []}
    for attrs in _parse_meta_tags(html_text):
        name = (attrs.get("name") or attrs.get("property") or "").lower()
        content = attrs.get("content")
        if not name or content is None:
            continue
        if name == "citation_pdf_url" and "citation_pdf_url" not in result:
            result["citation_pdf_url"] = content
        elif name == "citation_fulltext_html_url" and "citation_fulltext_html_url" not in result:
            result["citation_fulltext_html_url"] = content
        elif name == "citation_title" and "citation_title" not in result:
            result["citation_title"] = _strip_tags(content)
        elif name == "citation_doi" and "citation_doi" not in result:
            result["citation_doi"] = content
        elif name == "citation_author":
            result["citation_authors"].append(_strip_tags(content))
    return result


FULLTEXT_SECTION_MARKER_RE = re.compile(
    r"(materials and methods|methods and materials|results and discussion|"
    r"\bdiscussion\b|\bconclusions?\b|\backnowledge?ments?\b|\breferences\b)",
    re.IGNORECASE)


def _extract_article_text_from_html(html_text, min_len=1500, confident_len=6000):
    """Best-effort readable-text extraction from an HTML page, for OA links that turn out to be
    landing/full-text-HTML pages rather than a raw PDF. Not a full Readability port (this skill
    stays dependency-free) -- just the two heuristics that cover most scholarly publishing
    templates: prefer the semantic <article> container if present (used by PMC/PLOS/Frontiers/
    MDPI/BMC/Wiley/Springer), else fall back to concatenating <p> tag text (avoids pulling in
    nav/menu/button text that isn't wrapped in a paragraph tag). Script/style/nav/header/footer/
    aside/form blocks are stripped first regardless.

    Length alone is a weak signal for "this is real full text, not just an abstract" -- a
    paywalled landing page padded with "Cite this article"/"Save article"/journal navigation
    chrome can easily clear a few thousand characters while containing nothing but the abstract
    (observed in practice against a Springer landing page: 3.3k characters of pure chrome +
    abstract). So below `confident_len`, the extracted text must also contain a recognizable
    full-paper section marker (Methods/Results/Discussion/References/Acknowledgments) to be
    trusted; above `confident_len` it's accepted regardless, since abstract-only pages essentially
    never reach that size. Returns None when neither condition is met, so callers can honestly
    fall through rather than saving an abstract-dressed-as-fulltext scrap."""
    cleaned = NON_CONTENT_BLOCK_RE.sub(" ", html_text)
    article_match = ARTICLE_TAG_RE.search(cleaned)
    if article_match:
        text = _strip_tags(article_match.group(1))
    else:
        paragraphs = P_TAG_RE.findall(cleaned)
        text = "\n\n".join(_strip_tags(p) for p in paragraphs if _strip_tags(p))

    if len(text) < min_len:
        return None
    if len(text) < confident_len and not FULLTEXT_SECTION_MARKER_RE.search(text):
        return None
    return text


def _fetch_fulltext_from_url(url, out_dir, cid, _depth=0):
    """Attempts to obtain full text from a URL that may be a real PDF OR an HTML page. Tries, in
    order: (1) direct PDF download (the common case), (2) if the response is HTML instead, follow
    its `citation_pdf_url` meta tag to the real PDF -- many OA aggregator links (DOAJ, CrossRef,
    Unpaywall) resolve to a landing page rather than the PDF itself, (3) its
    `citation_fulltext_html_url` meta tag if the publisher only offers an HTML full-text view
    (common for e.g. PLOS/BMC), (4) best-effort readable-text extraction from the landing page
    itself as a last resort. Returns a dict with fulltext_status/local_path/extraction_method on
    success, or None if nothing usable was found -- callers fall through to the next OA source
    rather than treating this as fatal.

    `_depth` bounds how far citation-meta links are chased (max 2 hops: landing page ->
    citation_pdf_url, or landing page -> citation_fulltext_html_url) so a misconfigured page
    can't cause unbounded recursion."""
    pdf_path = os.path.join(out_dir, f"{cid}.pdf")
    if _download_pdf(url, pdf_path):
        return {"fulltext_status": "pdf", "local_path": pdf_path, "extraction_method": "direct_pdf"}

    if _depth > 1:
        return None

    try:
        body, headers, _ = http_request(url, headers={"Accept": "text/html,application/pdf,*/*"})
    except EXPECTED_NETWORK_ERRORS as e:
        print(f"[Info] Landing-page fetch failed for {url}: {e}", file=sys.stderr)
        return None

    if body.startswith(b"%PDF"):
        return None  # _download_pdf already tried this exact URL; it just failed size/validity

    content_type = (_get_header(headers, "Content-Type") or "").lower()
    looks_like_html = "html" in content_type or b"<html" in body[:2000].lower()
    if not looks_like_html:
        return None

    html_text = body.decode("utf-8", errors="replace")
    meta = _extract_citation_meta(html_text)

    pdf_url = meta.get("citation_pdf_url")
    if pdf_url and pdf_url != url:
        result = _fetch_fulltext_from_url(pdf_url, out_dir, cid, _depth=_depth + 1)
        if result:
            result["extraction_method"] = "citation_pdf_url"
            return result

    html_url = meta.get("citation_fulltext_html_url")
    if html_url and html_url != url:
        try:
            html_body, _, _ = http_request(html_url, headers={"Accept": "text/html"})
            text = _extract_article_text_from_html(html_body.decode("utf-8", errors="replace"))
            if text:
                path = os.path.join(out_dir, f"{cid}.txt")
                _write_text(path, text)
                return {"fulltext_status": "text", "local_path": path,
                        "extraction_method": "citation_fulltext_html_url"}
        except EXPECTED_NETWORK_ERRORS as e:
            print(f"[Info] citation_fulltext_html_url fetch failed for {html_url}: {e}", file=sys.stderr)

    text = _extract_article_text_from_html(html_text)
    if text:
        path = os.path.join(out_dir, f"{cid}.txt")
        _write_text(path, text)
        return {"fulltext_status": "text", "local_path": path, "extraction_method": "landing_page_readable_text"}

    return None


def _lookup_pmcid_by_doi(doi, cache=None):
    try:
        results = search_europepmc(f"DOI:{doi}", 1, cache=cache)
    except EXPECTED_SOURCE_ERRORS:
        return None
    return results[0].get("pmcid") if results else None


def _europepmc_fulltext_text(pmcid):
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    try:
        body, _, _ = http_request(url, headers={"Accept": "application/xml"})
    except EXPECTED_NETWORK_ERRORS as e:
        print(f"[Info] Europe PMC full text not available for {pmcid}: {e}", file=sys.stderr)
        return None
    text = _strip_tags(body.decode("utf-8", errors="replace"))
    return text if len(text) > 500 else None


def _unpaywall_pdf_url(doi, email):
    url = f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?" + urllib.parse.urlencode({"email": email})
    try:
        data = http_get_json(url)
    except EXPECTED_NETWORK_ERRORS as e:
        print(f"[Info] Unpaywall lookup failed for {doi}: {e}", file=sys.stderr)
        return None
    best = data.get("best_oa_location") or {}
    return best.get("url_for_pdf") or best.get("url")


def _core_pdf_url(doi, api_key):
    url = "https://api.core.ac.uk/v3/search/works/?" + urllib.parse.urlencode({"q": f'doi:"{doi}"', "limit": 1})
    try:
        data = http_get_json(url, headers={"Authorization": f"Bearer {api_key}"})
    except EXPECTED_NETWORK_ERRORS as e:
        print(f"[Info] CORE lookup failed for {doi}: {e}", file=sys.stderr)
        return None
    results = data.get("results", [])
    return results[0].get("downloadUrl") if results else None


def _doaj_fulltext_url(doi):
    try:
        results = search_doaj(f"doi:{doi}", 1)
    except EXPECTED_SOURCE_ERRORS as e:
        print(f"[Info] DOAJ lookup failed for {doi}: {e}", file=sys.stderr)
        return None
    return results[0].get("oa_pdf_url") if results else None


def resolve_and_fetch_fulltext(candidate, out_dir, cache, unpaywall_email=None, core_api_key=None):
    """Tries, in order, the sources most likely to hold a *legal open-access* copy: Europe PMC
    full text (best -- clean text, no PDF parsing needed), the paper's own OA PDF link (from
    OpenAlex/Semantic Scholar/CrossRef/arXiv/DOAJ), Unpaywall (aggregates OA locations across
    thousands of repositories by DOI), CORE (a large OA aggregator), then DOAJ by DOI as a last
    resort. Each of these four URL-based rungs is resolved through `_fetch_fulltext_from_url()`,
    which doesn't give up just because the URL serves an HTML landing page instead of a raw PDF
    -- it follows the page's `citation_pdf_url`/`citation_fulltext_html_url` meta tags (the
    standard mechanism scholarly publishers use for reference managers to find the real PDF) or,
    failing that, extracts readable article text from the landing page itself. The first rung
    that yields a real file wins; if none do, the paper is honestly marked abstract_only rather
    than silently skipped or faked.

    Idempotent / resumable: if `{cid}.txt` or `{cid}.pdf` already exists on disk from a previous
    run (e.g. the run was interrupted, or fetch-fulltext is being re-run after adding a paper),
    the existing file is reused without re-hitting any network API. Delete the file under
    `out_dir` first if you deliberately want to force a re-fetch for one paper."""
    cid = candidate["candidate_id"]
    doi = candidate.get("doi")

    existing_text = os.path.join(out_dir, f"{cid}.txt")
    if os.path.exists(existing_text) and os.path.getsize(existing_text) > 0:
        return {"fulltext_status": "text", "fulltext_source": candidate.get("fulltext_source", "cached_local_file"),
                "local_path": existing_text}
    existing_pdf = os.path.join(out_dir, f"{cid}.pdf")
    if os.path.exists(existing_pdf) and os.path.getsize(existing_pdf) > 0:
        return {"fulltext_status": "pdf", "fulltext_source": candidate.get("fulltext_source", "cached_local_file"),
                "local_path": existing_pdf}

    pmcid = candidate.get("pmcid")
    if not pmcid and doi:
        pmcid = _lookup_pmcid_by_doi(doi, cache=cache)
    if pmcid:
        text = _europepmc_fulltext_text(pmcid)
        if text:
            path = os.path.join(out_dir, f"{cid}.txt")
            _write_text(path, text)
            return {"fulltext_status": "text", "fulltext_source": "europepmc", "fulltext_pmcid": pmcid,
                    "local_path": path}

    if candidate.get("oa_pdf_url"):
        result = _fetch_fulltext_from_url(candidate["oa_pdf_url"], out_dir, cid)
        if result:
            result["fulltext_source"] = "direct_oa_link"
            return result

    if doi and unpaywall_email:
        pdf_url = _unpaywall_pdf_url(doi, unpaywall_email)
        if pdf_url:
            result = _fetch_fulltext_from_url(pdf_url, out_dir, cid)
            if result:
                result["fulltext_source"] = "unpaywall"
                return result

    if doi and core_api_key:
        pdf_url = _core_pdf_url(doi, core_api_key)
        if pdf_url:
            result = _fetch_fulltext_from_url(pdf_url, out_dir, cid)
            if result:
                result["fulltext_source"] = "core"
                return result

    if doi:
        pdf_url = _doaj_fulltext_url(doi)
        if pdf_url:
            result = _fetch_fulltext_from_url(pdf_url, out_dir, cid)
            if result:
                result["fulltext_source"] = "doaj"
                return result

    return {"fulltext_status": "abstract_only", "fulltext_source": None, "local_path": None}


def cmd_fetch_fulltext(args):
    if not os.path.exists(args.selected):
        print(f"[Error] Selected-candidates file not found: {args.selected}", file=sys.stderr)
        sys.exit(1)
    with open(args.selected, "r", encoding="utf-8") as f:
        data = json.load(f)
    candidates = data.get("candidates", [])
    if not candidates:
        print("[Warning] No candidates in the selected file - nothing to fetch.", file=sys.stderr)

    config = load_config(args.config)
    set_contact_email(config.get("contact_email"))
    cache = ResponseCache(args.cache_dir) if not args.no_cache else None
    unpaywall_email = config.get("unpaywall_email") or CONTACT_EMAIL
    core_api_key = config.get("core_api_key") or None
    out_dir = args.fulltext_dir
    os.makedirs(out_dir, exist_ok=True)

    def _run_one(c):
        result = resolve_and_fetch_fulltext(c, out_dir, cache, unpaywall_email, core_api_key)
        merged = dict(c)
        merged.update(result)
        return merged

    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(_run_one, c): c for c in candidates}
        for fut in as_completed(futures):
            r = fut.result()
            status = r["fulltext_status"]
            detail = r.get("fulltext_source") or ""
            if r.get("extraction_method") and r["extraction_method"] != "direct_pdf":
                detail += f" [{r['extraction_method']}]"
            print(f"[Info] {r['candidate_id']}: {status}" + (f" via {detail}" if detail else ""))
            results.append(r)

    results.sort(key=lambda r: r.get("candidate_id", ""))
    counts = {}
    for r in results:
        counts[r["fulltext_status"]] = counts.get(r["fulltext_status"], 0) + 1

    out = {
        "queries": data.get("queries"),
        "sources_requested": data.get("sources_requested"),
        "source_errors": data.get("source_errors", []),
        "total_raw_results": data.get("total_raw_results"),
        "total_unique_candidates": data.get("total_unique_candidates"),
        "fulltext_summary": counts,
        "candidates": results,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[Success] Full-text resolution done: {counts}. Written to {args.output}")
    print(f"[Notice] Only legally open-access full text is downloaded. Papers marked "
          f"'abstract_only' must be analyzed from title/abstract/metadata only -- do not "
          f"fabricate their full-text content.")


# =====================================================================
# 5. --from-analysis: agent's structured findings -> mutants.csv + report.md
# =====================================================================

CSV_FIELDS = [
    "paper_citation", "doi", "url", "candidate_id", "fulltext_status", "analysis_basis",
    "paper_sequence_length", "paper_sequence", "sequence_source_note",
    "raw_mutation", "mutation_position_in_paper_seq", "mapped_position_in_target_seq",
    "wild_type_residue", "mutant_residue", "property_tested",
    "quantitative_result", "qualitative_result", "assay_method",
    "source_locator", "evidence_type", "confidence", "limitations",
    "verification_status", "verification_details", "has_uncertain_data", "notes",
]

ANALYSIS_BASES = {"full_text", "partial_html", "abstract_only", "metadata_only"}
EVIDENCE_TYPES = {"direct_observation", "author_interpretation", "agent_inference"}
CONFIDENCE_LEVELS = {"high", "medium", "low"}

# Convention borrowed from structured-research workflows: rather than silently guessing when
# extraction confidence is low (e.g. an ambiguous table cell, a data point that might belong to
# a different mutant), the agent is expected to prefix that specific field's text with this
# literal marker. The mutant is still kept in the CSV/report (dropping it would lose track of it
# entirely) but flagged so a reader can tell "extracted with the same confidence as everything
# else" apart from "extracted, but the agent itself wasn't sure."
UNCERTAIN_TAG = "[uncertain]"
UNCERTAIN_MUTANT_FIELDS = ("raw_description", "property_tested", "quantitative_result",
                            "qualitative_result", "assay_method", "notes")


def _mutant_has_uncertain_data(mutant):
    return any(UNCERTAIN_TAG in str(mutant.get(f, "")) for f in UNCERTAIN_MUTANT_FIELDS)


def validate_and_clean_analysis(data):
    errors, warnings = [], []
    if not isinstance(data, dict):
        return data, ["Top-level JSON must be an object."], warnings

    try:
        data["target_sequence"] = clean_and_validate_sequence(
            data.get("target_sequence", ""), "target_sequence", allow_empty=True)
    except ValueError as e:
        errors.append(str(e))
    if not data.get("target_sequence"):
        warnings.append("target_sequence is empty - mutation positions will NOT be remapped "
                         "onto a target coordinate system (mapped_position_in_target_seq will be N/A).")

    papers = data.get("papers", [])
    if not isinstance(papers, list):
        errors.append("'papers' must be a list.")
        papers = []
    elif not papers:
        warnings.append("'papers' is empty - the mutant CSV will have no rows.")

    for i, p in enumerate(papers):
        label = f"papers[{i}]"
        if not isinstance(p, dict):
            errors.append(f"{label} must be an object.")
            continue
        if not p.get("citation") and not p.get("doi"):
            errors.append(f"{label} must have at least 'citation' or 'doi' to identify the source paper.")

        basis = p.get("analysis_basis", "")
        if basis not in ANALYSIS_BASES:
            errors.append(f"{label}.analysis_basis must be one of {sorted(ANALYSIS_BASES)}, got: {basis!r}")
        if p.get("fulltext_status") == "abstract_only" and basis not in {"abstract_only", "metadata_only"}:
            errors.append(f"{label}.analysis_basis cannot be {basis!r} when fulltext_status is 'abstract_only'.")
        if not str(p.get("accessible_content_summary", "")).strip():
            errors.append(f"{label}.accessible_content_summary is required, including when no relevant finding was found.")

        findings = p.get("findings")
        if not isinstance(findings, list):
            errors.append(f"{label}.findings must be a list (use [] when the accessible content has no relevant finding).")
            findings = []
        for j, finding in enumerate(findings):
            flabel = f"{label}.findings[{j}]"
            if not isinstance(finding, dict):
                errors.append(f"{flabel} must be an object.")
                continue
            for required in ("claim", "source_locator", "limitations"):
                if not str(finding.get(required, "")).strip():
                    errors.append(f"{flabel}.{required} is required.")
            if finding.get("evidence_type") not in EVIDENCE_TYPES:
                errors.append(f"{flabel}.evidence_type must be one of {sorted(EVIDENCE_TYPES)}.")
            if finding.get("confidence") not in CONFIDENCE_LEVELS:
                errors.append(f"{flabel}.confidence must be one of {sorted(CONFIDENCE_LEVELS)}.")

        seq = p.get("paper_sequence", "")
        if seq:
            try:
                p["paper_sequence"] = clean_and_validate_sequence(seq, f"{label}.paper_sequence")
            except ValueError as e:
                errors.append(str(e))
        else:
            warnings.append(f"{label} has no paper_sequence - position remapping and "
                             f"self-verification will be skipped for its mutants.")

        mutants = p.get("mutants", [])
        if not isinstance(mutants, list):
            errors.append(f"{label}.mutants must be a list.")
            mutants = []
        for j, m in enumerate(mutants):
            mlabel = f"{label}.mutants[{j}]"
            if not isinstance(m, dict):
                errors.append(f"{mlabel} must be an object.")
                continue
            if not m.get("raw_description"):
                warnings.append(f"{mlabel} has no raw_description.")
            if not str(m.get("source_locator", "")).strip():
                warnings.append(f"{mlabel}.source_locator is missing - the mutant claim is less traceable.")
            if m.get("evidence_type") and m.get("evidence_type") not in EVIDENCE_TYPES:
                errors.append(f"{mlabel}.evidence_type must be one of {sorted(EVIDENCE_TYPES)}.")
            if m.get("confidence") and m.get("confidence") not in CONFIDENCE_LEVELS:
                errors.append(f"{mlabel}.confidence must be one of {sorted(CONFIDENCE_LEVELS)}.")
            pos = m.get("mutation_position")
            if pos is not None:
                try:
                    int(pos)
                except (TypeError, ValueError):
                    errors.append(f"{mlabel}.mutation_position must be an integer, got: {pos!r}")
            for res_key in ("wild_type_residue", "mutant_residue"):
                res = m.get(res_key, "")
                if res and (len(res) != 1 or res.upper() not in VALID_AMINO_ACIDS):
                    errors.append(f"{mlabel}.{res_key} must be a single valid amino-acid letter, got: {res!r}")
    return data, errors, warnings


def cmd_merge_candidates(args):
    """Combines several `search` output files (e.g. from separate query rounds run at different
    times, or candidate lists gathered in different sessions) into one deduped candidates.json --
    complementary to `search --query` (repeated) which handles the common case of running
    several phrasings *concurrently in one process*. Use this one when the searches already
    happened separately (different turns, different --sources, or you simply forgot a keyword
    the first time and don't want to redo the whole search)."""
    all_candidates, sources_requested, source_errors, queries = [], [], [], []
    total_raw = 0
    for path in args.inputs:
        if not os.path.exists(path):
            print(f"[Error] Input file not found: {path}", file=sys.stderr)
            sys.exit(1)
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        all_candidates.extend(d.get("candidates", []))
        sources_requested.extend(d.get("sources_requested", []))
        source_errors.extend(d.get("source_errors", []))
        queries.extend(d.get("queries") or ([d["query"]] if d.get("query") else []))
        total_raw += d.get("total_raw_results", len(d.get("candidates", [])))

    for c in all_candidates:
        if not c.get("doi"):
            found = _extract_doi(c.get("url") or "") or _extract_doi(c.get("oa_pdf_url") or "")
            if found:
                c["doi"] = found

    deduped = dedupe_candidates(all_candidates)
    deduped.sort(key=lambda c: (c.get("citation_count") or 0), reverse=True)
    for i, c in enumerate(deduped, 1):
        c["candidate_id"] = f"c{i}"

    out = {
        "queries": sorted(set(queries)),
        "sources_requested": sorted(set(sources_requested)),
        "source_errors": source_errors,
        "total_raw_results": total_raw,
        "total_unique_candidates": len(deduped),
        "candidates": deduped,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[Success] Merged {len(args.inputs)} input file(s) ({total_raw} raw candidate "
          f"record(s)) into {len(deduped)} unique candidate(s), written to {args.output}")


def cmd_merge_analysis(args):
    """Combines many small per-paper JSON fragments (one per candidate_id, produced by
    independent sub-agents deep-reading papers in parallel) plus a separate top-level synthesis
    JSON (target sequence + theoretical write-up) into the single analysis.json that
    `from-analysis` expects.

    Why this exists: deep-reading N papers in a single agent turn doesn't scale -- for a large
    literature set it risks running out of context or producing lower-quality extraction on
    papers read later in the turn. Splitting deep-reading into one Task/sub-agent per paper (or
    small batch), each independently writing its own fragment file, lets that work run in
    parallel AND be resumed: if a batch is interrupted or one paper's sub-agent fails, only the
    missing fragments need to be regenerated, not the whole set. This mirrors the resumable
    per-item JSON pattern used by structured multi-agent research workflows for the same reason.
    """
    if not os.path.isdir(args.fragments_dir):
        print(f"[Error] Fragments directory not found: {args.fragments_dir}", file=sys.stderr)
        sys.exit(1)

    fragment_paths = sorted(glob.glob(os.path.join(args.fragments_dir, "*.json")))
    if not fragment_paths:
        print(f"[Error] No *.json fragment files found in {args.fragments_dir}", file=sys.stderr)
        sys.exit(1)

    papers, parse_errors = [], []
    for path in fragment_paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                papers.append(json.load(f))
        except EXPECTED_IO_ERRORS as e:
            parse_errors.append(f"{path}: {e}")
    if parse_errors:
        print(f"[Error] Failed to parse {len(parse_errors)} fragment(s):", file=sys.stderr)
        for e in parse_errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    synthesis = {}
    if args.synthesis:
        if not os.path.exists(args.synthesis):
            print(f"[Error] Synthesis file not found: {args.synthesis}", file=sys.stderr)
            sys.exit(1)
        with open(args.synthesis, "r", encoding="utf-8") as f:
            synthesis = json.load(f)
    else:
        print("[Warning] No --synthesis file given - target_protein_name/target_sequence/"
              "theoretical_analysis_markdown/design_implications_summary will all be empty in "
              "the merged output. Pass --synthesis once the theoretical write-up is ready.",
              file=sys.stderr)

    if args.selected:
        with open(args.selected, "r", encoding="utf-8") as f:
            selected = json.load(f)
        expected_ids = {c["candidate_id"] for c in selected.get("candidates", []) if c.get("candidate_id")}
        got_ids = {p.get("candidate_id") for p in papers if p.get("candidate_id")}
        missing = sorted(expected_ids - got_ids)
        if missing:
            print(f"[Warning] {len(missing)}/{len(expected_ids)} selected candidate(s) have no "
                  f"fragment yet in {args.fragments_dir} - deep-reading is not complete: {missing}",
                  file=sys.stderr)
        else:
            print(f"[Info] All {len(expected_ids)} selected candidate(s) have a fragment.")

    merged = dict(synthesis)
    merged["papers"] = papers
    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"[Success] Merged {len(papers)} paper fragment(s) from {args.fragments_dir} "
          f"into {args.output}")


def run_from_analysis(args):
    if not os.path.exists(args.analysis):
        print(f"[Error] Analysis JSON not found: {args.analysis}", file=sys.stderr)
        sys.exit(1)
    with open(args.analysis, "r", encoding="utf-8") as f:
        data = json.load(f)

    data, errors, warnings = validate_and_clean_analysis(data)
    for w in warnings:
        print(f"[Warning] {w}")
    if errors:
        print(f"[Error] --from-analysis input failed validation ({len(errors)} issue(s)):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    target_seq = data.get("target_sequence", "")
    target_name = data.get("target_protein_name", "")
    rows = []
    hotspot_counts = {}

    for p in data.get("papers", []):
        paper_seq = p.get("paper_sequence", "")
        mutants = p.get("mutants", [])
        positions = [m.get("mutation_position") for m in mutants if m.get("mutation_position")]
        pos_map = (map_multiple_positions(paper_seq, target_seq, positions)
                   if paper_seq and target_seq and positions else {})

        for m in mutants:
            pos = m.get("mutation_position")
            wt = m.get("wild_type_residue", "")
            status, details, wt = verify_mutation(paper_seq, pos, wt)
            if pos in pos_map:
                mapped_pos, _, _ = pos_map[pos]
            else:
                mapped_pos = "N/A"
            if isinstance(mapped_pos, int):
                hotspot_counts[mapped_pos] = hotspot_counts.get(mapped_pos, 0) + 1

            rows.append({
                "paper_citation": p.get("citation", ""),
                "doi": p.get("doi", ""),
                "url": p.get("url", ""),
                "candidate_id": p.get("candidate_id", ""),
                "fulltext_status": p.get("fulltext_status", ""),
                "analysis_basis": p.get("analysis_basis", ""),
                "paper_sequence_length": len(paper_seq) if paper_seq else "",
                "paper_sequence": paper_seq,
                "sequence_source_note": p.get("sequence_source_note", ""),
                "raw_mutation": m.get("raw_description", ""),
                "mutation_position_in_paper_seq": pos,
                "mapped_position_in_target_seq": mapped_pos,
                "wild_type_residue": wt,
                "mutant_residue": m.get("mutant_residue", ""),
                "property_tested": m.get("property_tested", ""),
                "quantitative_result": m.get("quantitative_result", ""),
                "qualitative_result": m.get("qualitative_result", ""),
                "assay_method": m.get("assay_method", ""),
                "source_locator": m.get("source_locator", ""),
                "evidence_type": m.get("evidence_type", ""),
                "confidence": m.get("confidence", ""),
                "limitations": m.get("limitations", ""),
                "verification_status": status,
                "verification_details": details,
                "has_uncertain_data": "Yes" if _mutant_has_uncertain_data(m) else "",
                "notes": m.get("notes", ""),
            })

    os.makedirs(os.path.dirname(os.path.abspath(args.output_csv)) or ".", exist_ok=True)
    with open(args.output_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[Success] Mutant table with {len(rows)} row(s) written to {args.output_csv}")

    svg_path = args.output_svg or os.path.join(
        os.path.dirname(os.path.abspath(args.output_report)) or ".", "mutation_hotspots.svg")
    svg_written = generate_hotspot_svg(data.get("target_sequence", ""), rows, svg_path)

    _generate_report(data, rows, hotspot_counts, args.output_report, args.search_summary,
                      svg_path if svg_written else None)


def _seq_preview(seq, head=40, tail=20):
    """Shortens a sequence for display inside report.md (which embeds every paper's sequence
    once per mutant-bearing paper): full sequences already live in mutants.csv, so the report
    only needs enough of a preview to sanity-check at a glance, keeping the report from growing
    linearly with paper count times sequence length for literature reviews spanning many papers."""
    if len(seq) <= head + tail + 10:
        return seq
    return f"{seq[:head]}...({len(seq) - head - tail} aa omitted, see mutants.csv)...{seq[-tail:]}"


def generate_hotspot_svg(target_seq, rows, output_path):
    """Generates an SVG visualization of mutation hotspots along the target sequence, in the
    same spirit as the sibling patent-protein-collector skill's mutation-hotspot map: bar
    height/opacity encodes how many literature-reported mutants map to each target position,
    so overlaps and gaps in coverage are visible at a glance. Returns True if a file was
    written, False if there was nothing to plot (no target sequence, or no mapped positions)."""
    if not target_seq:
        print("[Info] No target_sequence provided - skipping mutation hotspot SVG.")
        return False

    seq_len = len(target_seq)
    hotspots = {}
    for r in rows:
        pos = r.get("mapped_position_in_target_seq")
        if isinstance(pos, int):
            hotspots.setdefault(pos, []).append(
                (r.get("raw_mutation", ""), r.get("paper_citation", "")))

    if not hotspots:
        print("[Info] No mutant positions could be mapped onto the target sequence - skipping SVG.")
        return False

    width, height = 900, 280
    margin_x, margin_y = 60, 140
    plot_width = width - 2 * margin_x

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" height="100%">',
        f'  <rect width="{width}" height="{height}" fill="#f8fafc" rx="10"/>',
        '  <text x="40" y="32" font-family="system-ui, sans-serif" font-size="16" font-weight="bold" '
        'fill="#1e293b">目标蛋白突变热点分布图（文献来源）</text>',
        '  <text x="40" y="52" font-family="system-ui, sans-serif" font-size="12" fill="#64748b">'
        '柱高代表该位点被文献报道的突变体数量，颜色越深代表被越多不同文献研究过</text>',
    ]

    seq_y = margin_y + 50
    svg.append(f'  <rect x="{margin_x}" y="{seq_y}" width="{plot_width}" height="14" fill="#e2e8f0" rx="7"/>')
    svg.append(f'  <text x="{margin_x}" y="{seq_y + 32}" font-family="system-ui, sans-serif" '
               f'font-size="11" fill="#94a3b8" text-anchor="middle">1</text>')
    svg.append(f'  <text x="{margin_x + plot_width}" y="{seq_y + 32}" font-family="system-ui, sans-serif" '
               f'font-size="11" fill="#94a3b8" text-anchor="middle">{seq_len} aa</text>')

    max_freq = max(len({citation for _, citation in muts}) or 1 for muts in hotspots.values())

    for pos in sorted(hotspots):
        muts = hotspots[pos]
        n_papers = len({citation for _, citation in muts if citation}) or 1
        x = margin_x + (pos - 1) * (plot_width / max(1, seq_len - 1))
        bar_height = 15 + (n_papers / max_freq) * 60
        bar_y = seq_y - bar_height
        opacity = 0.55 + 0.45 * (n_papers / max_freq)

        svg.append(f'  <rect x="{x - 4:.1f}" y="{bar_y:.1f}" width="8" height="{bar_height:.1f}" '
                   f'fill="#2563eb" rx="4" opacity="{opacity:.2f}"/>')
        svg.append(f'  <circle cx="{x:.1f}" cy="{bar_y:.1f}" r="5" fill="#1d4ed8"/>')

        label = muts[0][0] or "?"
        if len(muts) > 1:
            label += f" (+{len(muts) - 1})"
        svg.append(f'  <text x="{x:.1f}" y="{bar_y - 10:.1f}" font-family="system-ui, sans-serif" '
                   f'font-size="10" font-weight="bold" fill="#1e293b" text-anchor="middle">{label}</text>')
        svg.append(f'  <text x="{x:.1f}" y="{seq_y + 25:.1f}" font-family="system-ui, sans-serif" '
                   f'font-size="10" fill="#475569" text-anchor="middle">{pos}</text>')

    leg_x, leg_y = width - 200, 30
    svg.append(f'  <rect x="{leg_x}" y="{leg_y}" width="160" height="40" fill="#ffffff" stroke="#e2e8f0" rx="6"/>')
    svg.append(f'  <circle cx="{leg_x + 20}" cy="{leg_y + 20}" r="6" fill="#2563eb"/>')
    svg.append(f'  <text x="{leg_x + 35}" y="{leg_y + 24}" font-family="system-ui, sans-serif" '
               f'font-size="11" fill="#334155">文献报道突变体</text>')
    svg.append('</svg>')

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(svg))
    print(f"[Success] Mutation hotspot SVG written to {output_path}")
    return True


def _gfm_anchor(text):
    """Approximates GitHub-Flavored-Markdown's automatic heading-anchor slugification (lowercase,
    strip punctuation, spaces -> hyphens) well enough to link a summary-table row to its later
    `### {citation}` section. Not a perfect match for every Markdown renderer's algorithm, and
    two papers with an identical citation string will collide (the renderer disambiguates with a
    `-1`/`-2` suffix that this function can't predict) -- acceptable for a convenience link, not
    load-bearing for correctness."""
    slug = re.sub(r"[^\w\- ]", "", text.lower())
    return re.sub(r"\s+", "-", slug.strip())


def _load_search_summary(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    return {
        "queries": d.get("queries"),
        "sources_requested": d.get("sources_requested"),
        "source_errors": d.get("source_errors", []),
        "total_raw_results": d.get("total_raw_results"),
        "total_unique_candidates": d.get("total_unique_candidates"),
        "fulltext_summary": d.get("fulltext_summary"),
    }


def _generate_report(data, rows, hotspot_counts, output_path, search_summary_path, svg_path=None):
    target_name = data.get("target_protein_name", "(未命名 / unnamed)")
    target_seq = data.get("target_sequence", "")
    theory_md = data.get("theoretical_analysis_markdown", "").strip()
    design_summary = data.get("design_implications_summary", "").strip()
    papers = data.get("papers", [])
    search_summary = _load_search_summary(search_summary_path)

    lines = [f"# {target_name} 文献信息挖掘与突变体设计参考报告", ""]
    lines.append("## 1. 检索与分析概述")
    lines.append(f"- **目标蛋白**: {target_name}")
    if target_seq:
        lines.append(f"- **目标序列长度**: {len(target_seq)} aa")
        lines.append(f"- **目标序列**（完整）:")
        lines.append("```")
        lines.append(target_seq)
        lines.append("```")
    else:
        lines.append("- **目标序列**: 未提供 -- 本报告中的突变位点仅按文献自身序列坐标呈现，未换算到目标序列坐标系。")
    lines.append(f"- **报告生成时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    if search_summary:
        queries_used = search_summary.get("queries") or []
        lines.append(f"- **检索查询语句**: " + "、".join(f"`{q}`" for q in queries_used))
        lines.append(f"- **检索来源**: {', '.join(search_summary.get('sources_requested') or [])}"
                      + (f"（失败: {[e['source'] for e in search_summary.get('source_errors', [])]}）"
                         if search_summary.get("source_errors") else ""))
        lines.append(f"- **原始命中 / 去重后候选**: {search_summary.get('total_raw_results')} / "
                      f"{search_summary.get('total_unique_candidates')}")
        if search_summary.get("fulltext_summary"):
            lines.append(f"- **全文获取情况**: {search_summary.get('fulltext_summary')}"
                          " （仅统计合法开放获取全文；abstract_only 表示该文献未能合法获取全文，"
                          "相关信息仅来自标题/摘要/元数据）")
    total_mutants = len(rows)
    total_uncertain = sum(1 for r in rows if r.get("has_uncertain_data") == "Yes")
    if total_mutants:
        lines.append(f"- **突变体记录总数 / 标记为不确定**: {total_mutants} / {total_uncertain}"
                      + ("（智能体对提取内容信心不足时会打上 `[uncertain]` 标记，详见 mutants.csv 的 "
                         "`has_uncertain_data` 列，深读设计方案前建议单独核实这些记录）" if total_uncertain else ""))
    lines.append("")

    lines.append("## 2. 文献筛选结果汇总")
    lines.append("| 文献 | DOI | 获取状态 | 实际分析依据 | 报道序列长度 | 结构化发现数 | 突变体数 | 不确定标记数 |")
    lines.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |")
    for p in papers:
        citation = p.get("citation", p.get("doi", "(未命名文献)"))
        mutants = p.get("mutants", [])
        n_mut = len(mutants)
        n_uncertain = sum(1 for m in mutants if _mutant_has_uncertain_data(m))
        seq_len = len(p.get("paper_sequence", "") or "")
        n_findings = len(p.get("findings", []) or [])
        citation_cell = f"[{citation}](#{_gfm_anchor(citation)})"
        lines.append(f"| {citation_cell} | {p.get('doi', '')} | "
                      f"{p.get('fulltext_status', '')} | {p.get('analysis_basis', '')} | "
                      f"{seq_len or 'N/A'} | {n_findings} | {n_mut} | "
                      f"{n_uncertain if n_uncertain else ''} |")
    lines.append("")

    lines.append("## 3. 蛋白质理论解析（催化机制与结构-功能关系）")
    lines.append(theory_md if theory_md else "*(智能体未提供理论解析内容 / not provided)*")
    lines.append("")

    lines.append("## 4. 逐篇结构化发现与突变实验数据")
    for p in papers:
        lines.append(f"### {p.get('citation', p.get('doi', '(未命名文献)'))}")
        if p.get("doi"):
            lines.append(f"- DOI: {p['doi']}")
        if p.get("url"):
            lines.append(f"- 链接: {p['url']}")
        lines.append(f"- 全文获取状态: {p.get('fulltext_status', 'N/A')}")
        lines.append(f"- 实际分析依据: {p.get('analysis_basis', 'N/A')}")
        lines.append(f"- 可访问内容摘要: {p.get('accessible_content_summary', '')}")
        if p.get("content_access_note"):
            lines.append(f"- 内容可得性与缺失说明: {p['content_access_note']}")
        if p.get("sequence_source_note"):
            lines.append(f"- 序列来源说明: {p['sequence_source_note']}")
        seq = p.get("paper_sequence", "")
        if seq:
            lines.append(f"- 文献报道序列（{len(seq)} aa，完整序列见 mutants.csv 对应行的 "
                         f"`paper_sequence` 列，此处仅预览首尾片段以控制报告体积）:")
            lines.append("```")
            lines.append(_seq_preview(seq))
            lines.append("```")
        findings = p.get("findings", []) or []
        lines.append("")
        lines.append("#### 从实际可访问内容提取的结构化发现")
        if findings:
            lines.append("| 类别 | 结论 | 证据类型 | 来源定位 | 置信度 | 局限性 |")
            lines.append("| :--- | :--- | :---: | :--- | :---: | :--- |")
            for finding in findings:
                lines.append(f"| {finding.get('category', 'other')} | {finding.get('claim', '')} | "
                             f"{finding.get('evidence_type', '')} | {finding.get('source_locator', '')} | "
                             f"{finding.get('confidence', '')} | {finding.get('limitations', '')} |")
        else:
            lines.append("*(已分析可访问内容，但未发现与目标蛋白机制、结构功能或突变设计直接相关的信息。)*")
        mutants = p.get("mutants", [])
        if mutants:
            lines.append("")
            lines.append("| | 原始突变描述 | 文献序列位点 | 目标序列对应位点 | 突变 | 检测性质 | 定量结果 | 定性结果 | 来源定位 | 置信度 | 自校验 |")
            lines.append("| :---: | :--- | :---: | :---: | :---: | :--- | :--- | :--- | :--- | :---: | :---: |")
            positions = [m.get("mutation_position") for m in mutants if m.get("mutation_position")]
            pos_map = (map_multiple_positions(seq, target_seq, positions)
                       if seq and target_seq and positions else {})
            for m in mutants:
                pos = m.get("mutation_position")
                mapped_pos = pos_map.get(pos, (None,))[0] if pos in pos_map else "N/A"
                status, _, wt = verify_mutation(seq, pos, m.get("wild_type_residue", ""))
                flag = "⚠️" if _mutant_has_uncertain_data(m) else ""
                lines.append(f"| {flag} | {m.get('raw_description', '')} | {pos} | {mapped_pos} | "
                              f"{wt}->{m.get('mutant_residue', '')} | {m.get('property_tested', '')} | "
                              f"{m.get('quantitative_result', '')} | {m.get('qualitative_result', '')} | "
                              f"{m.get('source_locator', '')} | {m.get('confidence', '')} | {status} |")
        lines.append("")

    lines.append("## 5. 跨文献位点汇总（同一目标序列位点在不同文献中的突变与效果对比）")
    lines.append("按目标序列坐标排序，方便一眼看出哪些位点被多篇文献重复研究、结论是否一致（若同一"
                  "位点在不同文献中效果相反或矛盾，通常意味着背景突变、检测条件或序列编号方式不同，"
                  "值得在设计前进一步核实）。")
    site_groups = {}
    for r in rows:
        pos = r.get("mapped_position_in_target_seq")
        if isinstance(pos, int):
            site_groups.setdefault(pos, []).append(r)
    if site_groups:
        lines.append("")
        lines.append("| | 目标序列位点 | 野生型->突变 | 检测性质 | 定量结果 | 定性结果 | 来源文献 | 自校验 |")
        lines.append("| :---: | :---: | :---: | :--- | :--- | :--- | :--- | :---: |")
        for pos in sorted(site_groups):
            for r in site_groups[pos]:
                flag = "⚠️" if r.get("has_uncertain_data") == "Yes" else ""
                lines.append(f"| {flag} | {pos} | {r.get('wild_type_residue', '')}->{r.get('mutant_residue', '')} | "
                              f"{r.get('property_tested', '')} | {r.get('quantitative_result', '')} | "
                              f"{r.get('qualitative_result', '')} | {r.get('paper_citation', '')} | "
                              f"{r.get('verification_status', '')} |")
    else:
        lines.append("*(暂无可换算到目标序列坐标系的突变位点——通常是因为未提供目标序列，"
                      "或相关文献均缺少 paper_sequence)*")
    lines.append("")

    lines.append("## 6. 突变位点频次分布（按目标序列坐标）")
    if svg_path:
        lines.append(f"![突变热点分布图]({os.path.basename(svg_path)})")
        lines.append("")
    if hotspot_counts:
        lines.append("| 目标序列位点 | 被研究文献数 |")
        lines.append("| :---: | :---: |")
        for pos in sorted(hotspot_counts):
            lines.append(f"| {pos} | {hotspot_counts[pos]} |")
    else:
        lines.append("*(未提供目标序列，或所有位点均未成功换算，无法生成频次分布)*")
    lines.append("")

    lines.append("## 7. 蛋白质突变体设计启示总结")
    lines.append(design_summary if design_summary else "*(智能体未提供设计启示总结 / not provided)*")
    lines.append("")

    lines.append("## 8. 数据来源与引用")
    lines.append("本报告基于以下公开数据库检索得到的文献（详见 mutants.csv 中每行的 doi/url 字段）：")
    for p in papers:
        lines.append(f"- {p.get('citation', p.get('doi', ''))}"
                      + (f" (DOI: {p['doi']})" if p.get("doi") else "")
                      + (f" — {p['url']}" if p.get("url") else ""))
    lines.append("")
    lines.append("> 请务必核实所引用文献的版权与使用条款；本报告仅提取事实性/实验性信息用于科研参考，"
                  "非开放获取全文的分析仅基于标题与摘要，可能不完整。")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[Success] Markdown report written to {output_path}")


# =====================================================================
# 6. Standalone alignment utility
# =====================================================================

def cmd_align(args):
    positions = [p.strip() for p in args.pos.split(",") if p.strip()]
    result = []
    pos_map = map_multiple_positions(args.paper_seq, args.target_seq, positions)
    for p in positions:
        mapped, paper_res, target_res = pos_map[p]
        result.append({"position": p, "mapped_position": mapped,
                        "aligned_paper_residue": paper_res, "aligned_target_residue": target_res})
    print(json.dumps(result, ensure_ascii=False, indent=2))


# =====================================================================
# 6b. check-sources: live diagnostic, not a research step
# =====================================================================

def cmd_check_sources(args):
    """Runs a trivial 1-result query against every source and reports pass/fail + latency, plus
    which optional credentials are configured. This is a diagnostic to run ONCE when setting up
    (or when a search comes back suspiciously empty) -- it is not part of the research pipeline
    itself and its output should not be cited as literature."""
    config = load_config(args.config)
    set_contact_email(config.get("contact_email"))
    ctx = {"cache": None, "openalex_api_key": config.get("openalex_api_key") or None,
           "ncbi_api_key": config.get("ncbi_api_key") or None,
           "s2_api_key": config.get("semantic_scholar_api_key") or None,
           "core_api_key": config.get("core_api_key") or None}

    print(f"[Info] Checking {len(SOURCE_FUNCS)} literature source(s) with a trivial query "
          f"({args.probe_query!r}, 1 result each)...\n")

    def _probe(name):
        start = time.time()
        try:
            results = SOURCE_FUNCS[name](args.probe_query, 1, ctx)
            elapsed = time.time() - start
            status = "OK" if results else "OK (0 results)"
            return (name, status, f"{elapsed:.2f}s", "")
        except EXPECTED_SOURCE_ERRORS as e:
            elapsed = time.time() - start
            return (name, "FAIL", f"{elapsed:.2f}s", str(e)[:120])

    to_probe = []
    rows = []
    for name in sorted(SOURCE_FUNCS):
        if name == "core" and not ctx.get("core_api_key"):
            rows.append((name, "SKIPPED (no API key)", "-", ""))
        else:
            to_probe.append(name)

    # Probe sources concurrently -- this is a diagnostic meant to be fast, and each source
    # already has its own per-host rate limiter, so parallelizing across DIFFERENT hosts is
    # safe and turns a ~40s worst-case sequential run (several sources retrying 429/503 with
    # backoff) into whatever the single slowest source takes.
    with ThreadPoolExecutor(max_workers=len(to_probe) or 1) as pool:
        rows.extend(pool.map(_probe, to_probe))
    rows.sort(key=lambda r: r[0])

    name_w = max(len(r[0]) for r in rows) + 2
    for name, status, elapsed, err in rows:
        line = f"  {name:<{name_w}} {status:<14} {elapsed:>7}"
        if err:
            line += f"   {err}"
        print(line)

    print("\n[Info] Optional credential status (via config.json / environment variables):")
    for field, env_var in CONFIG_ENV_MAP.items():
        state = "set" if config.get(field) else "not set"
        print(f"  {field:<24} ({env_var:<22}): {state}")

    print("\n[Info] Fulltext-only checks not run above (Unpaywall/CORE/DOAJ-by-DOI, Europe PMC "
          "full text) require a real DOI/PMCID -- use `fetch-fulltext` on an actual candidate to "
          "exercise those paths.")

    failed = [r for r in rows if r[1] == "FAIL"]
    if failed:
        print(f"\n[Warning] {len(failed)}/{len(rows)} source(s) failed. This usually means a "
              f"transient outage or a bad API key, not that the skill is broken -- rerun later "
              f"or check the credential status above.", file=sys.stderr)


# =====================================================================
# 7. CLI
# =====================================================================

def build_parser():
    parser = argparse.ArgumentParser(description="Literature protein information collector")
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="Search multiple literature databases for candidate papers")
    p_search.add_argument("--query", required=True, action="append",
                           help="Search query (protein/gene name + keywords, e.g. "
                                "'GH11 xylanase thermostability mutant Bacillus'). Repeat this "
                                "flag to run several phrasings concurrently in one process "
                                "(shares rate limiters, auto-dedupes) for broader recall without "
                                "extra wall-clock cost, e.g. --query 'GH11 xylanase mutant' "
                                "--query 'endo-1,4-beta-xylanase directed evolution'")
    p_search.add_argument("--sources", default=",".join(DEFAULT_SOURCES),
                           help=f"Comma-separated source list. Available: {sorted(SOURCE_FUNCS)}")
    p_search.add_argument("--max-results", type=int, default=20)
    p_search.add_argument("--output", required=True)
    p_search.add_argument("--cache-dir", default="cache")
    p_search.add_argument("--no-cache", action="store_true")
    p_search.add_argument("--config", default=None,
                           help="Path to config.json (default: auto-discover ./config.json or "
                                "the one next to this script). Env vars always override it.")
    p_search.set_defaults(func=cmd_search)

    p_merge_cand = sub.add_parser("merge-candidates", help="Merge+dedupe multiple search output "
                                                            "files gathered separately over time")
    p_merge_cand.add_argument("--inputs", required=True, nargs="+",
                               help="Two or more candidates.json files to merge")
    p_merge_cand.add_argument("--output", required=True)
    p_merge_cand.set_defaults(func=cmd_merge_candidates)

    p_fetch = sub.add_parser("fetch-fulltext", help="Resolve and download open-access full text for selected papers")
    p_fetch.add_argument("--selected", required=True, help="Path to agent-filtered candidates JSON "
                                                            "(same schema as search --output)")
    p_fetch.add_argument("--output", required=True)
    p_fetch.add_argument("--fulltext-dir", default="output/fulltext")
    p_fetch.add_argument("--cache-dir", default="cache")
    p_fetch.add_argument("--no-cache", action="store_true")
    p_fetch.add_argument("--concurrency", type=int, default=10,
                          help="Parallel candidates in flight (default 10). Safe to raise further "
                               "for large candidate sets that span many different journals/hosts "
                               "-- the actual request rate to any single host is still governed "
                               "by that host's own throttle (see RATE_LIMITS) regardless of this "
                               "number, so raising it mostly reduces idle waiting rather than "
                               "increasing load on any one source.")
    p_fetch.add_argument("--config", default=None,
                          help="Path to config.json (default: auto-discover ./config.json or "
                               "the one next to this script). Env vars always override it.")
    p_fetch.set_defaults(func=cmd_fetch_fulltext)

    p_analysis = sub.add_parser("from-analysis", help="Turn agent-produced structured findings into CSV + report")
    p_analysis.add_argument("--analysis", required=True, help="Path to agent-authored analysis JSON")
    p_analysis.add_argument("--output-csv", required=True)
    p_analysis.add_argument("--output-report", required=True)
    p_analysis.add_argument("--search-summary", default=None,
                             help="Optional path to the search or fetch-fulltext output JSON, used to "
                                  "auto-fill the report's search-methodology section")
    p_analysis.add_argument("--output-svg", default=None,
                             help="Optional path for the mutation-hotspot SVG (default: "
                                  "mutation_hotspots.svg next to --output-report). Skipped if "
                                  "target_sequence is empty or no mutant positions could be mapped.")
    p_analysis.set_defaults(func=run_from_analysis)

    p_merge = sub.add_parser("merge-analysis", help="Combine per-paper JSON fragments (from "
                                                     "parallel deep-reading sub-agents) + a "
                                                     "synthesis JSON into one analysis.json")
    p_merge.add_argument("--fragments-dir", required=True,
                          help="Directory of *.json files, one per paper (each matching a single "
                               "'papers[]' entry's schema)")
    p_merge.add_argument("--synthesis", default=None,
                          help="Path to a JSON with target_protein_name/target_sequence/"
                               "theoretical_analysis_markdown/design_implications_summary")
    p_merge.add_argument("--selected", default=None,
                          help="Optional path to selected.json, to report which candidates are "
                               "still missing a fragment (resume check)")
    p_merge.add_argument("--output", required=True)
    p_merge.set_defaults(func=cmd_merge_analysis)

    p_align = sub.add_parser("align", help="Standalone utility: map positions between two sequences")
    p_align.add_argument("--paper-seq", required=True)
    p_align.add_argument("--target-seq", required=True)
    p_align.add_argument("--pos", required=True, help="Comma-separated 1-indexed positions in paper-seq")
    p_align.set_defaults(func=cmd_align)

    p_check = sub.add_parser("check-sources", help="Diagnostic: probe every source with a "
                                                    "trivial query and report configured credentials")
    p_check.add_argument("--probe-query", default="protein")
    p_check.add_argument("--config", default=None,
                          help="Path to config.json (default: auto-discover ./config.json or "
                               "the one next to this script). Env vars always override it.")
    p_check.set_defaults(func=cmd_check_sources)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
