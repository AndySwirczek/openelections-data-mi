"""Submit a PDF to the PaddleOCR-VL AI Studio endpoint and cache per-page markdown.

Reuses the backend proven in openelections-data-al (convert_canvass_pdfs_paddleocr.py):
one async job per PDF, JSONL result with per-page markdown. Raw markdown is cached
per page so parser iterations never re-hit the API.

    .venv/bin/python src/fetch_paddleocr_md.py <pdf...> [--cache DIR]

Reads the AI Studio token from $PADDLEOCR_TOKEN or ~/.paddleocr_token.
"""
import argparse
import glob
import json
import os
import re
import sys
import time

JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
MODEL = "PaddleOCR-VL-1.6"
OPTIONAL_PAYLOAD = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useChartRecognition": False,
}
POLL_SECONDS = 5


def _token():
    tok = os.environ.get("PADDLEOCR_TOKEN")
    if tok:
        return tok.strip()
    for path in (".paddleocr_token", os.path.expanduser("~/.paddleocr_token")):
        if os.path.exists(path):
            return open(path).read().strip()
    raise SystemExit("No PaddleOCR token: set $PADDLEOCR_TOKEN or create ~/.paddleocr_token")


def _submit_job(pdf_path, token):
    import requests
    headers = {"Authorization": f"bearer {token}"}
    data = {"model": MODEL, "optionalPayload": json.dumps(OPTIONAL_PAYLOAD)}
    r = None
    for attempt in range(20):
        try:
            with open(pdf_path, "rb") as f:
                r = requests.post(JOB_URL, headers=headers, data=data,
                                  files={"file": f}, timeout=300)
            break
        except requests.RequestException as e:
            print(f"    submit error ({e.__class__.__name__}), retry {attempt + 1}/20",
                  file=sys.stderr, flush=True)
            time.sleep(POLL_SECONDS)
    if r is None:
        raise RuntimeError("failed to submit job")
    if r.status_code != 200:
        raise RuntimeError(f"PaddleOCR submit failed ({r.status_code}): {r.text[:400]}")
    return r.json()["data"]["jobId"]


def _poll_job(job_id, token):
    import requests
    headers = {"Authorization": f"bearer {token}"}
    misses = 0
    while True:
        try:
            r = requests.get(f"{JOB_URL}/{job_id}", headers=headers, timeout=120)
            r.raise_for_status()
            misses = 0
        except requests.RequestException as e:
            misses += 1
            if misses > 20:
                raise
            print(f"    poll error ({e.__class__.__name__}), retry {misses}/20",
                  file=sys.stderr, flush=True)
            time.sleep(POLL_SECONDS)
            continue
        d = r.json()["data"]
        state = d["state"]
        if state == "done":
            return d["resultUrl"]["jsonUrl"]
        if state == "failed":
            raise RuntimeError(f"PaddleOCR job failed: {d.get('errorMsg')}")
        prog = d.get("extractProgress") or {}
        if state == "running" and "totalPages" in prog:
            print(f"    running {prog.get('extractedPages')}/{prog['totalPages']} pages",
                  flush=True)
        time.sleep(POLL_SECONDS)


def _fetch_pages(jsonl_url):
    import requests
    r = None
    for attempt in range(20):
        try:
            r = requests.get(jsonl_url, timeout=120)
            r.raise_for_status()
            break
        except requests.RequestException as e:
            print(f"    fetch error ({e.__class__.__name__}), retry {attempt + 1}/20",
                  file=sys.stderr, flush=True)
            time.sleep(POLL_SECONDS)
    else:
        raise RuntimeError(f"failed to fetch {jsonl_url}")
    pages = []
    for line in r.text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        for res in json.loads(line)["result"]["layoutParsingResults"]:
            pages.append(res["markdown"]["text"])
    return pages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--cache", default="/tmp/paddleocr_md")
    args = ap.parse_args()

    token = _token()
    for pdf_path in args.pdfs:
        stem = re.sub(r"[^A-Za-z0-9]+", "_", os.path.splitext(os.path.basename(pdf_path))[0])
        cache = os.path.join(args.cache, stem)
        os.makedirs(cache, exist_ok=True)
        done_marker = os.path.join(cache, ".complete")
        if os.path.exists(done_marker):
            have = len(glob.glob(os.path.join(cache, "p*.md")))
            print(f"{os.path.basename(pdf_path)}: cached ({have} pages)", flush=True)
            continue
        print(f"submitting {os.path.basename(pdf_path)} ...", flush=True)
        job_id = _submit_job(pdf_path, token)
        print(f"    job {job_id}", flush=True)
        jsonl_url = _poll_job(job_id, token)
        pages = _fetch_pages(jsonl_url)
        for i, raw in enumerate(pages, start=1):
            open(os.path.join(cache, f"p{i:03d}.md"), "w").write(raw)
        open(done_marker, "w").write(str(len(pages)))
        print(f"got {len(pages)} pages -> {cache}", flush=True)


if __name__ == "__main__":
    main()