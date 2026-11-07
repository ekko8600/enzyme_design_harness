from __future__ import annotations

import argparse
import html
import re
from pathlib import Path


HTML_RENDERER_MARKER = "opencode-html-report-renderer"


def report_css() -> str:
    return """
:root {
  color-scheme: light;
  --bg: #f5f7f8;
  --paper: #ffffff;
  --paper-2: #f9fbfb;
  --ink: #18212b;
  --muted: #64717f;
  --line: #d9e2e3;
  --accent: #216b6f;
  --accent-2: #d95d39;
  --ok: #147a55;
  --warn: #9a5b00;
  --danger: #b42318;
  --shadow: 0 18px 48px rgba(24, 33, 43, 0.08);
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  background:
    radial-gradient(circle at 12% 0%, rgba(33, 107, 111, 0.12), transparent 30rem),
    linear-gradient(180deg, #eef4f3 0%, var(--bg) 22rem);
  color: var(--ink);
  font-family: "Aptos", "Segoe UI", "Noto Sans", Arial, sans-serif;
  line-height: 1.58;
  -webkit-font-smoothing: antialiased;
}
main, .report-shell {
  width: min(1180px, calc(100% - 32px));
  margin: 0 auto;
  padding: 28px 0 44px;
}
.report-hero {
  position: relative;
  overflow: hidden;
  background: linear-gradient(135deg, #ffffff 0%, #f4faf9 58%, #edf3f0 100%);
  border: 1px solid rgba(33, 107, 111, 0.18);
  border-radius: 8px;
  box-shadow: var(--shadow);
  padding: clamp(22px, 4vw, 42px);
  margin: 0 0 18px;
}
.report-hero::after {
  content: "";
  position: absolute;
  inset: auto -90px -130px auto;
  width: 300px;
  height: 300px;
  background: radial-gradient(circle, rgba(217, 93, 57, 0.16), transparent 68%);
  pointer-events: none;
}
.report-kicker, .eyebrow {
  margin: 0 0 8px;
  color: var(--accent);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
h1 {
  margin: 0;
  max-width: 920px;
  color: #111a22;
  font-size: clamp(30px, 4.8vw, 54px);
  line-height: 1.04;
  text-wrap: balance;
}
.report-subtitle, .muted, .subtitle {
  color: var(--muted);
}
.report-subtitle {
  max-width: 76ch;
  margin: 14px 0 0;
  font-size: 16px;
}
.report-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 20px;
}
section, .section, .panel, .card {
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: 0 10px 34px rgba(24, 33, 43, 0.055);
  margin: 14px 0;
  padding: clamp(16px, 2.6vw, 24px);
}
.hero { background: transparent; border: 0; box-shadow: none; padding: 0; }
h2 {
  margin: 0 0 12px;
  color: #17232f;
  font-size: clamp(19px, 2vw, 24px);
  line-height: 1.18;
  text-wrap: balance;
}
h3 {
  margin: 18px 0 8px;
  color: #1d2a35;
  font-size: 17px;
  line-height: 1.25;
}
p { max-width: 80ch; }
a { color: var(--accent); text-underline-offset: 3px; }
table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  margin: 10px 0 2px;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 8px;
  font-size: 14px;
}
th, td {
  border-bottom: 1px solid var(--line);
  padding: 9px 11px;
  text-align: left;
  vertical-align: top;
}
th {
  background: #eef4f3;
  color: #33424d;
  font-weight: 700;
}
td {
  background: #fff;
  overflow-wrap: anywhere;
}
tr:last-child > th, tr:last-child > td { border-bottom: 0; }
tbody tr:hover td { background: #fbfdfc; }
code {
  background: #eef3f3;
  border: 1px solid #dbe5e5;
  border-radius: 5px;
  color: #164e53;
  font-family: "Cascadia Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 0.92em;
  padding: 1px 5px;
}
.grid, .metric-grid, .summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 10px;
}
.metric, .box {
  background: var(--paper-2);
  border: 1px solid #dfe8e8;
  border-radius: 8px;
  padding: 12px;
}
.metric span, .metric-label {
  display: block;
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
}
.metric strong, .metric-value {
  display: block;
  margin-top: 3px;
  color: #16262b;
  font-size: 22px;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.pill, .badge, .status, .status-pill, .env-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border-radius: 999px;
  padding: 4px 10px;
  background: #e7f3f2;
  color: #17575b;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}
.complete, .done, .badge-success, .ok { color: var(--ok); background: #ecf8f1; }
.needs-opencode-synthesis, .needs_user_review, .pending, .badge-warning { color: var(--warn); background: #fff4e5; }
.failed, .blocked, .missing, .badge-danger { color: var(--danger); background: #fff0ed; }
.callout, .caveat, .assessment, .review-note {
  border-left: 4px solid var(--accent-2);
  background: #fff8f4;
  border-radius: 6px;
  padding: 10px 12px;
}
details {
  border: 1px solid #dfe8e8;
  border-radius: 8px;
  background: #fff;
  margin: 10px 0;
  padding: 10px 12px;
}
summary {
  cursor: pointer;
  color: #243540;
  font-weight: 700;
}
.skill-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 10px;
}
.skill-card {
  margin: 0;
  min-width: 0;
}
footer {
  margin-top: 28px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 13px;
}
@media (max-width: 760px) {
  main, .report-shell { width: min(100% - 20px, 1180px); padding-top: 12px; }
  .report-hero, section, .section, .panel, .card { padding: 14px; }
  table { display: block; overflow-x: auto; white-space: nowrap; }
  td, th { white-space: normal; }
}
"""


def render_document(
    *,
    title: str,
    body_html: str,
    subtitle: str = "",
    kicker: str = "Enzyme Mutation Design",
    lang: str = "auto",
    extra_head: str = "",
    footer: str = "Rendered with OpenCode html-report-renderer.",
) -> str:
    if lang == 'auto':
        visible=re.sub(r'<[^>]+>', ' ', body_html)
        lang='zh-CN' if re.search(r'[\u3400-\u9fff]', title+' '+visible) else 'en'
    safe_title = html.escape(title)
    subtitle_html = f'<p class="report-subtitle">{html.escape(subtitle)}</p>' if subtitle else ""
    footer_html = f"<footer>{html.escape(footer)}</footer>" if footer else ""
    return "\n".join(
        [
            "<!doctype html>",
            f'<html lang="{html.escape(lang)}">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{safe_title}</title>",
            f'<meta name="generator" content="{HTML_RENDERER_MARKER}">',
            extra_head,
            f"<style>{report_css()}</style>",
            "</head>",
            "<body>",
            '<main class="report-shell">',
            '<header class="report-hero">',
            f'<p class="report-kicker">{html.escape(kicker)}</p>',
            f"<h1>{safe_title}</h1>",
            subtitle_html,
            "</header>",
            body_html,
            footer_html,
            "</main>",
            "</body>",
            "</html>",
        ]
    ) + "\n"


def polish_existing_html(document: str, *, title: str | None = None, subtitle: str = "", lang: str | None = None) -> str:
    body_match = re.search(r"<body[^>]*>(?P<body>.*?)</body>", document, flags=re.I | re.S)
    body = body_match.group("body").strip() if body_match else document.strip()
    if re.search(r"<main\b", body, flags=re.I):
        body_html = body
    else:
        body_html = f'<section class="section">{body}</section>'

    if title is None:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", document, flags=re.I | re.S)
        heading_match = re.search(r"<h1[^>]*>(.*?)</h1>", document, flags=re.I | re.S)
        raw_title = title_match.group(1) if title_match else (heading_match.group(1) if heading_match else "HTML report")
        title = re.sub(r"<[^>]+>", "", raw_title).strip() or "HTML report"
    if lang is None:
        original_lang=re.search(r'<html\b[^>]*\blang=["\']([^"\']+)["\']',document,flags=re.I)
        lang=original_lang.group(1) if original_lang else 'auto'
    return render_document(title=title, subtitle=subtitle, body_html=body_html,lang=lang)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render or polish project HTML reports with the OpenCode report style.")
    parser.add_argument("--input", type=Path, help="Existing HTML body or complete HTML file to polish.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--title", default=None)
    parser.add_argument("--subtitle", default="")
    parser.add_argument("--body", default=None, help="Inline HTML body. Used when --input is omitted.")
    parser.add_argument("--lang", default=None, help="Document language; default preserves existing language or detects Chinese/English.")
    args = parser.parse_args(argv)

    if args.input:
        source = args.input.read_text(encoding="utf-8")
    elif args.body is not None:
        source = args.body
    else:
        parser.error("provide --input or --body")

    args.output.write_text(polish_existing_html(source, title=args.title, subtitle=args.subtitle,lang=args.lang), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
