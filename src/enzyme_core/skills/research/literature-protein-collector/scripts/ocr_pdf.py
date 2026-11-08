#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Optional OCR / layout-aware document parsing for downloaded PDFs, via the PaddleOCR-VL cloud
API. This is the ONE piece of the literature-protein-collector skill that needs a third-party
package (`requests`) and a paid/quota-limited API token -- everything else in this skill
(literature_collector.py) is stdlib-only and free.

Why this exists: an OpenCode/Claude agent CAN read a PDF directly with its built-in Read tool,
which renders pages and interprets them multimodally. That works fine for straightforward
born-digital text. But enzyme/protein literature full text usually lives or dies on DENSE DATA
TABLES (mutant lists, kinetic parameters, activity-fold-change tables, multi-column SI tables)
and sometimes scanned/low-quality pages -- exactly the content most likely to be misread by
generic vision-based PDF reading. Running a dedicated document-parsing OCR pass first and
handing the agent clean, layout-aware Markdown (with tables preserved as Markdown tables)
measurably reduces the risk of misreading a mutant's numeric result.

This script does NOT call any LLM itself -- it only talks to the PaddleOCR-VL job API and
writes plain files to disk. Relevance judgment and data mining still happen in the calling
agent, same as the rest of this skill.

Usage:
    python3 scripts/ocr_pdf.py --file output/fulltext/c3.pdf --output output/fulltext/c3.ocr.md
    python3 scripts/ocr_pdf.py --dir output/fulltext --output-dir output/fulltext_ocr

Auth: pass --token, set the PADDLEOCR_VL_TOKEN environment variable, or add "paddleocr_vl_token"
to config.json -- same precedence as the rest of this skill (env var > config.json > nothing).
Never hardcode a token in this file or commit one to version control -- request it from the user
via the `credentials` skill's safe-credentials protocol.
"""

import argparse
import glob
import json
import os
import sys
import time

try:
    import requests
    from requests import RequestException
except ImportError:
    print("[Error] This script needs the 'requests' package (the only third-party dependency "
          "in this skill). Install it with: pip install requests", file=sys.stderr)
    sys.exit(1)

# Reuse the config.json + env-var loader from the sibling script instead of duplicating it --
# both scripts live in the same scripts/ directory and share one config.json at the skill root.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from literature_collector import load_config
except ImportError:
    load_config = None

JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
DEFAULT_MODEL = "PaddleOCR-VL-1.6"
EXPECTED_OCR_ERRORS = (RuntimeError, TimeoutError, FileNotFoundError, OSError, ValueError, KeyError, json.JSONDecodeError, RequestException)


def submit_job(file_path, token, model, optional_payload):
    headers = {"Authorization": f"bearer {token}"}
    if file_path.startswith("http"):
        headers["Content-Type"] = "application/json"
        payload = {"fileUrl": file_path, "model": model, "optionalPayload": optional_payload}
        resp = requests.post(JOB_URL, json=payload, headers=headers, timeout=60)
    else:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        data = {"model": model, "optionalPayload": json.dumps(optional_payload)}
        with open(file_path, "rb") as f:
            resp = requests.post(JOB_URL, headers=headers, data=data, files={"file": f}, timeout=120)

    if resp.status_code != 200:
        raise RuntimeError(f"Job submission failed (HTTP {resp.status_code}): {resp.text[:500]}")
    return resp.json()["data"]["jobId"]


def poll_job(job_id, token, poll_interval=5, timeout=1800):
    headers = {"Authorization": f"bearer {token}"}
    deadline = time.time() + timeout
    while True:
        resp = requests.get(f"{JOB_URL}/{job_id}", headers=headers, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"Job status check failed (HTTP {resp.status_code}): {resp.text[:500]}")
        data = resp.json()["data"]
        state = data["state"]
        if state == "pending":
            print("[Info] OCR job pending...")
        elif state == "running":
            progress = data.get("extractProgress", {})
            print(f"[Info] OCR job running: {progress.get('extractedPages', '?')}/"
                  f"{progress.get('totalPages', '?')} pages")
        elif state == "done":
            progress = data.get("extractProgress", {})
            print(f"[Success] OCR job done: {progress.get('extractedPages', '?')} page(s) extracted.")
            return data["resultUrl"]["jsonUrl"]
        elif state == "failed":
            raise RuntimeError(f"OCR job failed: {data.get('errorMsg', 'unknown error')}")
        else:
            raise RuntimeError(f"Unexpected job state: {state}")

        if time.time() > deadline:
            raise TimeoutError(f"OCR job {job_id} did not finish within {timeout}s.")
        time.sleep(poll_interval)


def download_and_merge(jsonl_url, output_md_path, images_dir):
    resp = requests.get(jsonl_url, timeout=120)
    resp.raise_for_status()
    lines = [l for l in resp.text.strip().split("\n") if l.strip()]

    os.makedirs(os.path.dirname(os.path.abspath(output_md_path)) or ".", exist_ok=True)
    combined = []
    page_num = 0
    for line in lines:
        result = json.loads(line)["result"]
        for res in result["layoutParsingResults"]:
            combined.append(f"<!-- page {page_num} -->\n" + res["markdown"]["text"])
            for img_path, img_url in res["markdown"].get("images", {}).items():
                full_img_path = os.path.join(images_dir, img_path)
                os.makedirs(os.path.dirname(full_img_path), exist_ok=True)
                try:
                    img_bytes = requests.get(img_url, timeout=60).content
                    with open(full_img_path, "wb") as imf:
                        imf.write(img_bytes)
                except (RequestException, OSError) as e:
                    print(f"[Warning] Failed to download embedded image {img_path}: {e}", file=sys.stderr)
            page_num += 1

    with open(output_md_path, "w", encoding="utf-8") as f:
        f.write("\n\n---\n\n".join(combined))
    print(f"[Success] Merged {page_num} page(s) of OCR markdown written to {output_md_path}")


def ocr_one(file_path, output_md_path, token, model, poll_interval, timeout, optional_payload):
    if os.path.exists(output_md_path) and os.path.getsize(output_md_path) > 0:
        print(f"[Info] {output_md_path} already exists - skipping (delete it to force re-OCR).")
        return
    print(f"[Info] Submitting OCR job for {file_path}")
    job_id = submit_job(file_path, token, model, optional_payload)
    print(f"[Info] Job submitted: {job_id}")
    jsonl_url = poll_job(job_id, token, poll_interval, timeout)
    images_dir = os.path.splitext(output_md_path)[0] + "_images"
    download_and_merge(jsonl_url, output_md_path, images_dir)


def main():
    parser = argparse.ArgumentParser(description="OCR/layout-parse a PDF (or a directory of "
                                                  "PDFs) via the PaddleOCR-VL API into clean "
                                                  "Markdown, for reliable reading of dense data "
                                                  "tables in scientific PDFs.")
    parser.add_argument("--file", help="Single local PDF path or remote file URL")
    parser.add_argument("--dir", help="Directory of PDFs to OCR in batch (every *.pdf in it)")
    parser.add_argument("--output", help="Output Markdown path (required with --file)")
    parser.add_argument("--output-dir", help="Output directory for batch mode (required with --dir); "
                                             "each <name>.pdf becomes <name>.ocr.md")
    parser.add_argument("--token", default=None,
                         help="PaddleOCR-VL API bearer token. Defaults to the PADDLEOCR_VL_TOKEN "
                              "env var, falling back to \"paddleocr_vl_token\" in config.json. "
                              "NEVER hardcode this in a script or commit it to version control.")
    parser.add_argument("--config", default=None,
                         help="Path to config.json (default: auto-discover ./config.json or the "
                              "one next to this script). Env vars always override it.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--poll-interval", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--use-doc-orientation-classify", action="store_true")
    parser.add_argument("--use-doc-unwarping", action="store_true")
    parser.add_argument("--use-chart-recognition", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config) if load_config else {}
    token = args.token or config.get("paddleocr_vl_token") or os.environ.get("PADDLEOCR_VL_TOKEN")
    if not token:
        print("[Error] No PaddleOCR-VL API token provided. Pass --token, set the "
              "PADDLEOCR_VL_TOKEN environment variable, or add \"paddleocr_vl_token\" to "
              "config.json. If this skill is being used, you MUST use the safe credentials "
              "protocol in the `credentials` skill to obtain this token from the user rather "
              "than guessing or hardcoding one.", file=sys.stderr)
        sys.exit(1)
    args.token = token

    optional_payload = {
        "useDocOrientationClassify": args.use_doc_orientation_classify,
        "useDocUnwarping": args.use_doc_unwarping,
        "useChartRecognition": args.use_chart_recognition,
    }

    if args.file:
        if not args.output:
            print("[Error] --output is required with --file", file=sys.stderr)
            sys.exit(1)
        try:
            ocr_one(args.file, args.output, args.token, args.model, args.poll_interval, args.timeout,
                    optional_payload)
        except EXPECTED_OCR_ERRORS as e:
            print(f"[Error] OCR failed for {args.file}: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.dir:
        if not args.output_dir:
            print("[Error] --output-dir is required with --dir", file=sys.stderr)
            sys.exit(1)
        pdfs = sorted(glob.glob(os.path.join(args.dir, "*.pdf")))
        if not pdfs:
            print(f"[Warning] No *.pdf files found in {args.dir}", file=sys.stderr)
        os.makedirs(args.output_dir, exist_ok=True)
        for pdf_path in pdfs:
            base = os.path.splitext(os.path.basename(pdf_path))[0]
            out_md = os.path.join(args.output_dir, f"{base}.ocr.md")
            try:
                ocr_one(pdf_path, out_md, args.token, args.model, args.poll_interval, args.timeout,
                        optional_payload)
            except EXPECTED_OCR_ERRORS as e:
                print(f"[Warning] OCR failed for {pdf_path}, skipping: {e}", file=sys.stderr)
    else:
        print("[Error] Provide either --file or --dir.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
