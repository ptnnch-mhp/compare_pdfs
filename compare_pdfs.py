#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compare_pdfs_extended.py

Erweiterte Version des PDF-Dokumentenvergleichs mit vollständigem UTF‑8‑Support.
Features:
- PDF-Extraktion via PyPDF2
- Struktur- & Inhaltsvergleich (Abschnitt → Absatz → Satz)
- Ähnlichkeitsmetriken (Token‑Jaccard + SequenceMatcher)
- Kopierte Passagen (Rolling Window, ≥ N Wörter)
- CSV- und JSON-Exports
- Markdown- UND HTML-Bericht (mit Inline-Highlighting für Änderungen)
- Optional: Heatmap/Matrix der Similarities (matplotlib)
- Optional: Regex-Highlighting für Schlüsselbegriffe

Nutzung (Beispiel):
    python compare_pdfs_extended.py \
        --template file_1.pdf \
        --changed file_2.pdf \
        --segmentation auto \
        --min_copy_words 50 \
        --near_ratio 0.92 \
        --regex "CISO" --regex "ACSMS" \
        --heatmap \
        --out_md diff_report.md \
        --out_html diff_report.html \
        --out_json diff_report.json

"""
from __future__ import annotations

import argparse
import csv
import json
import re
import hashlib
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List, Tuple, Dict, Iterable, Optional

import PyPDF2

# Matplotlib ist optional (nur wenn --heatmap gesetzt)
try:
    import matplotlib.pyplot as plt
    import numpy as np
except Exception:  # pragma: no cover
    plt = None
    np = None

# ------------------------------
# UTF-8 Normalisierung & Tokenizer
# ------------------------------

def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = s.replace("\xad", "")             # Soft hyphen
    s = s.replace("\u00A0", " ")           # NBSP
    s = s.replace("\u2009", " ")            # Thin space
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def tokens(s: str, stopwords: Optional[set] = None) -> List[str]:
    s = normalize_text(s.lower())
    s = re.sub(r"[^\wäöüÄÖÜß\-]+", " ", s)
    tok = [t for t in s.split() if t]
    if stopwords:
        tok = [t for t in tok if t not in stopwords]
    return tok


DE_STOPWORDS = {
    "der","die","das","und","oder","aber","dass","daß","ist","sind","war","waren","ein","eine","einer","eines",
    "im","in","zu","vom","von","am","an","auf","für","mit","ohne","aus","als","auch","es","den","dem","des","sowie",
    "so","bei","durch","werden","wird","sich","nicht","nur","mehr","weniger"
}


# ------------------------------
# PDF Lesehilfe
# ------------------------------

def extract_pdf_text(path: str) -> List[str]:
    pages: List[str] = []
    with open(path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for p in reader.pages:
            try:
                t = p.extract_text() or ""
            except Exception:
                t = ""
            t = t.replace("\r\n", "\n").replace("\r", "\n")
            pages.append(t)
    return pages


# ------------------------------
# Struktur-Erkennung
# ------------------------------
@dataclass
class Header:
    level: int
    title: str
    page_idx: int
    line_idx: int
    anchor: str


HEADER_PATTERNS = [
    re.compile(r"^\s*(\d+(?:\.\d+){0,3})\s+([^\n]{3,})$"),   # 1.2.3 Titel
    re.compile(r"^\s*#{1,6}\s+(.+)$"),                         # Markdown-Header
    re.compile(r"^\s*(Anhang|Annex)\s+[A-Z]\b.*$")            # Annex
]


def detect_headers(pages: List[str]) -> List[Header]:
    headers: List[Header] = []
    for pi, page in enumerate(pages):
        for li, line in enumerate(page.split("\n")):
            stripped = line.strip()
            if not stripped:
                continue
            m = HEADER_PATTERNS[0].match(stripped)
            if m:
                num = m.group(1)
                title = m.group(2).strip()
                level = num.count(".") + 1
                anchor = f"{num} {title}"
                headers.append(Header(level, anchor, pi, li, anchor))
                continue
            m = HEADER_PATTERNS[1].match(stripped)
            if m:
                title = m.group(1).strip()
                headers.append(Header(1, title, pi, li, title))
                continue
            m = HEADER_PATTERNS[2].match(stripped)
            if m:
                headers.append(Header(1, stripped, pi, li, stripped))
                continue
    return headers


# ------------------------------
# Segmentierung (Abschnitt -> Absatz -> Satz)
# ------------------------------
@dataclass
class Segment:
    section_path: str
    paragraph_idx: int
    sentence_idx: int
    text: str
    page_span: Tuple[int, int]


def split_sentences(text: str) -> List[str]:
    text = text.replace("…", ".")
    parts = re.split(r"(?<=[^\d][\.!\?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def segment_document(pages: List[str], mode: str = "auto") -> List[Segment]:
    headers = detect_headers(pages)
    header_map = {(h.page_idx, h.line_idx): h for h in headers}

    lines_by_page = [pg.split("\n") for pg in pages]
    segments: List[Segment] = []
    current_path: List[str] = []

    def current_section_path() -> str:
        return " / ".join(current_path) if current_path else "ROOT"

    def flush_buffer(buf: List[str], page_ix: int):
        if not buf:
            return
        block = "\n".join(buf).strip()
        if not block:
            return
        paras = [p for p in re.split(r"\n\s*\n", block) if p.strip()]
        para_idx = 0
        for p in paras:
            if mode == "section":
                segments.append(Segment(current_section_path(), para_idx, -1, normalize_text(p), (page_ix, page_ix)))
                para_idx += 1
                continue
            if mode == "paragraph":
                segments.append(Segment(current_section_path(), para_idx, -1, normalize_text(p), (page_ix, page_ix)))
                para_idx += 1
                continue
            # sentence/auto
            sents = split_sentences(p)
            for si, s in enumerate(sents):
                segments.append(Segment(current_section_path(), para_idx, si, normalize_text(s), (page_ix, page_ix)))
            para_idx += 1

    for pi, lines in enumerate(lines_by_page):
        buf: List[str] = []
        for li, ln in enumerate(lines):
            if (pi, li) in header_map:
                flush_buffer(buf, pi)
                buf = []
                h = header_map[(pi, li)]
                while len(current_path) >= h.level:
                    current_path.pop()
                current_path.append(h.title)
                continue
            buf.append(ln)
        flush_buffer(buf, pi)

    return segments


# ------------------------------
# Ähnlichkeitsmetriken
# ------------------------------

def jaccard_similarity(a: Iterable[str], b: Iterable[str]) -> float:
    A = set(a); B = set(b)
    if not A and not B:
        return 1.0
    return len(A & B) / max(1, len(A | B))


def seq_ratio(a: str, b: str) -> float:
    return SequenceMatcher(a=a, b=b).ratio()


def composite_similarity(a_text: str, b_text: str, stopwords: Optional[set]) -> float:
    a_toks = tokens(a_text, stopwords)
    b_toks = tokens(b_text, stopwords)
    jac = jaccard_similarity(a_toks, b_toks)
    sm = seq_ratio(a_text, b_text)
    return 0.5 * jac + 0.5 * sm


# ------------------------------
# Segment-Matching (greedy)
# ------------------------------

def match_segments(template: List[Segment], changed: List[Segment], stopwords: Optional[set], min_sim: float = 0.35) -> Tuple[List[Tuple[Segment, Segment, float]], List[Segment], List[Segment]]:
    matched_pairs: List[Tuple[Segment, Segment, float]] = []
    unmatched_template: List[Segment] = []
    unmatched_changed = set(range(len(changed)))

    idx_by_section: Dict[str, List[int]] = {}
    for i, seg in enumerate(changed):
        idx_by_section.setdefault(seg.section_path, []).append(i)

    for tseg in template:
        cand_idx = idx_by_section.get(tseg.section_path, list(range(len(changed))))
        best_i = None
        best_sim = -1.0
        for i in cand_idx:
            if i not in unmatched_changed:
                continue
            sim = composite_similarity(tseg.text, changed[i].text, stopwords)
            if sim > best_sim:
                best_sim = sim
                best_i = i
        if best_i is not None and best_sim >= min_sim:
            matched_pairs.append((tseg, changed[best_i], best_sim))
            unmatched_changed.remove(best_i)
        else:
            unmatched_template.append(tseg)

    unmatched_changed_list = [changed[i] for i in sorted(unmatched_changed)]
    return matched_pairs, unmatched_template, unmatched_changed_list


# ------------------------------
# Kopierte Passagen (Rolling Window)
# ------------------------------

def find_copied_passages(template: List[Segment], changed: List[Segment], min_words: int = 50, near_ratio: float = 0.92) -> List[Dict]:
    def flatten(seg_list: List[Segment]) -> List[Tuple[str, str]]:
        out = []
        for s in seg_list:
            locator = f"{s.section_path} :: Abs.{s.paragraph_idx}{'' if s.sentence_idx<0 else f'/Satz {s.sentence_idx+1}'}"
            out.append((s.text, locator))
        return out

    A = flatten(template)
    B = flatten(changed)

    def word_list(t: str) -> List[str]:
        return tokens(t, stopwords=None)  # keine Stopwörter bei Kopien

    A_stream: List[str] = []
    A_index_meta: List[Tuple[int, str]] = []  # (end_offset, locator)
    for (t, loc) in A:
        w = word_list(t)
        A_stream.extend(w)
        A_index_meta.append((len(A_stream), loc))

    B_stream: List[str] = []
    B_index_meta: List[Tuple[int, str]] = []
    for (t, loc) in B:
        w = word_list(t)
        B_stream.extend(w)
        B_index_meta.append((len(B_stream), loc))

    results: List[Dict] = []
    win = min_words
    if len(A_stream) < win or len(B_stream) < win:
        return results

    def windows(words: List[str], size: int) -> Dict[str, List[int]]:
        mp: Dict[str, List[int]] = {}
        for i in range(0, len(words) - size + 1):
            chunk = " ".join(words[i:i+size])
            h = hashlib.sha1(chunk.encode("utf-8")).hexdigest()
            mp.setdefault(h, []).append(i)
        return mp

    A_wins = windows(A_stream, win)

    # Helper zur Lokalisierung
    def loc_from_offset(meta_index: List[Tuple[int, str]], offset: int) -> str:
        for end_off, loc in meta_index:
            if offset <= end_off:
                return loc
        return meta_index[-1][1]

    for j in range(0, len(B_stream) - win + 1):
        chunk = " ".join(B_stream[j:j+win])
        h = hashlib.sha1(chunk.encode("utf-8")).hexdigest()
        if h not in A_wins:
            continue
        for i in A_wins[h]:
            left = 0
            while i-1-left >= 0 and j-1-left >= 0 and A_stream[i-1-left] == B_stream[j-1-left]:
                left += 1
            right = 0
            while (i+win+right < len(A_stream) and j+win+right < len(B_stream)
                   and A_stream[i+win+right] == B_stream[j+win+right]):
                right += 1
            length = win + left + right
            A_text = " ".join(A_stream[i-left:i+win+right])
            B_text = " ".join(B_stream[j-left:j+win+right])
            r = seq_ratio(A_text, B_text)
            if r >= near_ratio:
                results.append({
                    "template_locator": loc_from_offset(A_index_meta, i),
                    "changed_locator": loc_from_offset(B_index_meta, j),
                    "words": length,
                    "similarity": round(r, 4),
                    "excerpt_template": A_text[:800] + ("…" if len(A_text) > 800 else ""),
                    "excerpt_changed": B_text[:800] + ("…" if len(B_text) > 800 else "")
                })

    # Dedup sortiert nach Länge/Similarität
    seen = set()
    dedup: List[Dict] = []
    for r in sorted(results, key=lambda x: (-x["words"], -x["similarity"])):
        key = (r["template_locator"], r["changed_locator"], r["words"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(r)
    return dedup


# ------------------------------
# HTML-Diff Utilities
# ------------------------------

def highlight_ops(a: str, b: str) -> Tuple[str, float]:
    """Markiert Wort-Änderungen zwischen a und b mit <span>-Tags.
    Rückgabe: (html_string, similarity_ratio)
    """
    a_t = tokens(a, stopwords=None)
    b_t = tokens(b, stopwords=None)
    sm = SequenceMatcher(a=a_t, b=b_t)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            out.append(' '.join(b_t[j1:j2]))
        elif tag == 'insert':
            out.append('<span class="ins">' + ' '.join(b_t[j1:j2]) + '</span>')
        elif tag == 'delete':
            out.append('<span class="del">' + ' '.join(a_t[i1:i2]) + '</span>')
        elif tag == 'replace':
            out.append('<span class="rep">' + ' '.join(b_t[j1:j2]) + '</span>')
    ratio = sm.ratio()
    return ' '.join(out), ratio


# ------------------------------
# Reporting
# ------------------------------

def write_csv(path: str, rows: List[Dict], fieldnames: List[str]):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_json(path: str, data: Dict):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_markdown(global_sim_pct: int, jac: float, avg_pair_sim: float, struct_overlap: float,
                   copies: List[Dict], deleted: List[Segment], added: List[Segment], modified: List[Tuple[Segment, Segment, float]],
                   min_copy_words: int) -> str:
    md = []
    md.append('# Diff‑Bericht (Erweiterte Version)')
    md.append('')
    md.append('## 1) Ähnlichkeitsgrad')
    md.append(f'- **Gesamte Übereinstimmung**: **{global_sim_pct}%**')
    md.append(f'    - Token‑Jaccard: {jac:.3f} | Ø‑Match‑Ähnlichkeit: {avg_pair_sim:.3f} | Struktur‑Overlap: {struct_overlap:.3f}')

    md.append('\n## 2) Kopierte Textabschnitte (≥ Mindestlänge)')
    if copies:
        md.append('| # | Locator (Template) | Locator (Geändert) | Wörter | Ähnlichkeit |')
        md.append('|---:|---|---|---:|---:|')
        for i, r in enumerate(copies, 1):
            md.append(f"| {i} | {r['template_locator']} | {r['changed_locator']} | {r['words']} | {r['similarity']:.3f} |")
    else:
        md.append('> Keine kopierten Abschnitte gemäß Schwellwert gefunden.')

    md.append('\n## 3) Änderungen')
    md.append('### 3.1 Gelöschter Text aus Template (Auszug)')
    if deleted:
        md.append('| # | Position | Auszug |')
        md.append('|---:|---|---|')
        for i, s in enumerate(deleted[:50], 1):
            excerpt = (s.text[:240] + '…') if len(s.text) > 240 else s.text
            pos = f"{s.section_path} :: Abs.{s.paragraph_idx}{'' if s.sentence_idx<0 else f'/Satz {s.sentence_idx+1}'}"
            md.append(f"| {i} | {pos} | {excerpt} |")
        if len(deleted) > 50:
            md.append(f"\n> **{len(deleted)-50} weitere…** siehe CSV.")
    else:
        md.append('> Nichts ausschließlich im Template gefunden.')

    md.append('\n### 3.2 Neu hinzugefügter Text im geänderten Dokument (Auszug)')
    if added:
        md.append('| # | Position | Auszug |')
        md.append('|---:|---|---|')
        for i, s in enumerate(added[:50], 1):
            excerpt = (s.text[:240] + '…') if len(s.text) > 240 else s.text
            pos = f"{s.section_path} :: Abs.{s.paragraph_idx}{'' if s.sentence_idx<0 else f'/Satz {s.sentence_idx+1}'}"
            md.append(f"| {i} | {pos} | {excerpt} |")
        if len(added) > 50:
            md.append(f"\n> **{len(added)-50} weitere…** siehe CSV.")
    else:
        md.append('> Keine exklusiven Ergänzungen gefunden.')

    md.append('\n### 3.3 Geänderte Segmente (Vorher ↔ Nachher, Auszug)')
    if modified:
        md.append('| # | Position (Template) | Position (Geändert) | Ähnlichkeit | Vorher (Auszug) | Nachher (Auszug) |')
        md.append('|---:|---|---|---:|---|---|')
        for i, (tseg, cseg, sim) in enumerate(modified[:50], 1):
            before = (tseg.text[:160] + '…') if len(tseg.text) > 160 else tseg.text
            after  = (cseg.text[:160] + '…') if len(cseg.text) > 160 else cseg.text
            pos_t  = f"{tseg.section_path} :: Abs.{tseg.paragraph_idx}{'' if tseg.sentence_idx<0 else f'/Satz {tseg.sentence_idx+1}'}"
            pos_c  = f"{cseg.section_path} :: Abs.{cseg.paragraph_idx}{'' if cseg.sentence_idx<0 else f'/Satz {cseg.sentence_idx+1}'}"
            md.append(f"| {i} | {pos_t} | {pos_c} | {sim:.3f} | {before} | {after} |")
        if len(modified) > 50:
            md.append(f"\n> **{len(modified)-50} weitere…** siehe CSV.")
    else:
        md.append('> Keine geänderten Segmente.')

    md.append('\n## 4) Zusammenfassung')
    md.append(f"- **Gesamte Übereinstimmung**: **{global_sim_pct}%**")
    md.append(f"- **Kopierte Abschnitte**: {len(copies)} (≥ {min_copy_words} Wörter)")
    md.append(f"- **Gelöscht**: {len(deleted)} | **Hinzugefügt**: {len(added)} | **Geändert**: {len(modified)}")

    return "\n".join(md)


def build_html(title: str, modified: List[Tuple[Segment, Segment, float]], regexes: List[re.Pattern]) -> str:
    css = """
    <style>
    body {font-family: system-ui, -apple-system, Segoe UI, Arial, sans-serif; line-height: 1.45;}
    h1,h2,h3 {margin-top: 1.2em}
    table {border-collapse: collapse; width: 100%;}
    td,th {border: 1px solid #ddd; padding: 6px; vertical-align: top;}
    tr:nth-child(even){background:#fafafa}
    .ins {background: #e6ffed;}
    .del {background: #ffeef0; text-decoration: line-through;}
    .rep {background: #fff5b1;}
    .hlre {background: #dbeafe; border-radius: 3px; padding: 0 2px;}
    .sim {font-variant-numeric: tabular-nums;}
    </style>
    """
    html = ["<html><head><meta charset='utf-8'>", f"<title>{title}</title>", css, "</head><body>"]
    html.append(f"<h1>{title}</h1>")
    html.append("<h2>Geänderte Segmente (inline Diff)</h2>")
    html.append("<table><thead><tr><th>#</th><th>Vorher (Template)</th><th>Nachher (Geändert)</th><th>Ähnlichkeit</th></tr></thead><tbody>")

    def apply_regex_highlights(text: str) -> str:
        out = text
        for rx in regexes:
            out = rx.sub(lambda m: f"<span class='hlre'>{m.group(0)}</span>", out)
        return out

    for i, (tseg, cseg, sim) in enumerate(modified, 1):
        t_html, _ = highlight_ops(tseg.text, tseg.text)   # identisch, nur Regex-Highlighting möglich
        c_html, _ = highlight_ops(tseg.text, cseg.text)
        t_html = apply_regex_highlights(t_html)
        c_html = apply_regex_highlights(c_html)
        html.append(
            f"<tr><td>{i}</td><td>{t_html}</td><td>{c_html}</td><td class='sim'>{sim:.3f}</td></tr>"
        )
    html.append("</tbody></table>")
    html.append("</body></html>")
    return "".join(html)


# ------------------------------
# Hauptworkflow
# ------------------------------

def build_reports(template_pdf: str, changed_pdf: str,
                  segmentation: str,
                  min_copy_words: int,
                  near_ratio: float,
                  use_stopwords: bool,
                  regex_list: List[str],
                  want_heatmap: bool,
                  out_md: str,
                  out_html: str,
                  out_json: str,
                  report_title: str):

    # 1) PDF lesen
    t_pages = extract_pdf_text(template_pdf)
    c_pages = extract_pdf_text(changed_pdf)

    # 2) Segmente bilden
    stopwords = DE_STOPWORDS if use_stopwords else None
    t_segs = segment_document(t_pages, mode=segmentation)
    c_segs = segment_document(c_pages, mode=segmentation)

    # 3) Matching
    pairs, t_only, c_only = match_segments(t_segs, c_segs, stopwords)
    modified = [(t, c, s) for (t, c, s) in pairs if s < near_ratio]

    # 4) Kopien
    copies = find_copied_passages(t_segs, c_segs, min_words=min_copy_words, near_ratio=near_ratio)

    # 5) Ähnlichkeit (global)
    t_all = "\n".join(s.text for s in t_segs)
    c_all = "\n".join(s.text for s in c_segs)
    jac = jaccard_similarity(tokens(t_all, stopwords), tokens(c_all, stopwords))
    avg_pair_sim = (sum(s for (_, _, s) in pairs) / len(pairs)) if pairs else 0.0
    t_sections = set(s.section_path for s in t_segs)
    c_sections = set(s.section_path for s in c_segs)
    struct_overlap = len(t_sections & c_sections) / max(1, len(t_sections | c_sections))
    global_sim = 0.4*jac + 0.4*avg_pair_sim + 0.2*struct_overlap
    global_sim_pct = int(round(100*global_sim))

    # 6) CSV Exports
    write_csv("diffs_deleted.csv", [
        {"Position": f"{s.section_path} :: Abs.{s.paragraph_idx}{'' if s.sentence_idx<0 else f'/Satz {s.sentence_idx+1}'}",
         "Text": s.text}
        for s in t_only
    ], ["Position","Text"])

    write_csv("diffs_added.csv", [
        {"Position": f"{s.section_path} :: Abs.{s.paragraph_idx}{'' if s.sentence_idx<0 else f'/Satz {s.sentence_idx+1}'}",
         "Text": s.text}
        for s in c_only
    ], ["Position","Text"])

    write_csv("diffs_modified.csv", [
        {"Template": f"{t.section_path} :: Abs.{t.paragraph_idx}{'' if t.sentence_idx<0 else f'/Satz {t.sentence_idx+1}'}",
         "Geändert": f"{c.section_path} :: Abs.{c.paragraph_idx}{'' if c.sentence_idx<0 else f'/Satz {c.sentence_idx+1}'}",
         "Ähnlichkeit(0-1)": f"{sim:.4f}",
         "Vorher": t.text,
         "Nachher": c.text}
        for (t, c, sim) in modified
    ], ["Template","Geändert","Ähnlichkeit(0-1)","Vorher","Nachher"])

    write_csv("copies_geN.csv", [
        {"Locator(Template)": r["template_locator"],
         "Locator(Geändert)": r["changed_locator"],
         "Wörter": r["words"],
         "Ähnlichkeit(0-1)": r["similarity"],
         "Zitat(Template,gekürzt)": r["excerpt_template"],
         "Zitat(Geändert,gekürzt)": r["excerpt_changed"]}
        for r in copies
    ], ["Locator(Template)","Locator(Geändert)","Wörter","Ähnlichkeit(0-1)","Zitat(Template,gekürzt)","Zitat(Geändert,gekürzt)"])

    # 7) Markdown
    md = build_markdown(global_sim_pct, jac, avg_pair_sim, struct_overlap, copies, t_only, c_only, modified, min_copy_words)
    with open(out_md, 'w', encoding='utf-8') as f:
        f.write(md)

    # 8) HTML (mit Regex-Highlighting)
    compiled_regexes = []
    for r in regex_list:
        try:
            compiled_regexes.append(re.compile(r, re.IGNORECASE))
        except re.error:
            pass
    html = build_html(report_title, modified, compiled_regexes)
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(html)

    # 9) JSON
    json_obj = {
        "similarity": {
            "global_pct": global_sim_pct,
            "token_jaccard": round(jac, 4),
            "avg_pair": round(avg_pair_sim, 4),
            "struct_overlap": round(struct_overlap, 4)
        },
        "counts": {
            "deleted": len(t_only),
            "added": len(c_only),
            "modified": len(modified),
            "copied": len(copies)
        }
    }
    write_json(out_json, json_obj)

    # 10) Heatmap (optional, einfache Similarity-Matrix der ersten N Segmente)
    if want_heatmap and plt is not None and np is not None:
        N = min(80, max(1, len(t_segs)))
        M = min(80, max(1, len(c_segs)))
        # Stichprobe / Truncation, um Laufzeit zu begrenzen
        sim_mat = np.zeros((N, M), dtype=float)
        for i in range(N):
            for j in range(M):
                sim_mat[i, j] = composite_similarity(t_segs[i].text, c_segs[j].text, stopwords)
        fig, ax = plt.subplots(figsize=(8, 6))
        im = ax.imshow(sim_mat, cmap='viridis', aspect='auto', vmin=0, vmax=1)
        ax.set_title('Similarity Heatmap (Teilmatrix)')
        ax.set_xlabel('Geänderte Segmente (Index)')
        ax.set_ylabel('Template Segmente (Index)')
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        fig.tight_layout()
        plt.savefig('diff_heatmap.png', dpi=150)
        plt.close(fig)

    print(f"[OK] Reports erzeugt: {out_md}, {out_html}, {out_json}")
    print("     CSVs: diffs_added.csv, diffs_deleted.csv, diffs_modified.csv, copies_geN.csv")
    if want_heatmap:
        if plt is None:
            print("[HINWEIS] Heatmap angefordert, aber matplotlib/numpy nicht verfügbar.")
        else:
            print("     Heatmap: diff_heatmap.png")


# ------------------------------
# CLI
# ------------------------------

def main():
    ap = argparse.ArgumentParser(description='Erweiterter PDF-Vergleich (UTF-8, HTML, CSV/JSON, Heatmap, Regex-Highlight).')
    ap.add_argument('--template', required=True, help='Template PDF (ältere Version)')
    ap.add_argument('--changed', required=True, help='Geändertes PDF (neue Version)')
    ap.add_argument('--segmentation', choices=['auto','section','paragraph','sentence'], default='auto', help='Segmentierungsmodus (default: auto)')
    ap.add_argument('--min_copy_words', type=int, default=50, help='Mindestlänge kopierter Passagen in Wörtern (default: 50)')
    ap.add_argument('--near_ratio', type=float, default=0.92, help='Schwelle für "nahezu identisch" (default: 0.92)')
    ap.add_argument('--stopwords', choices=['none','de'], default='de', help='Stopwort-Set (default: de)')
    ap.add_argument('--regex', action='append', default=[], help='Regex-Begriff(e) zum Hervorheben (mehrfach nutzbar)')
    ap.add_argument('--heatmap', action='store_true', help='Erzeuge zusätzlich eine Similarity-Heatmap (diff_heatmap.png)')
    ap.add_argument('--out_md', default='diff_report.md', help='Pfad zur Markdown-Ausgabe')
    ap.add_argument('--out_html', default='diff_report.html', help='Pfad zur HTML-Ausgabe')
    ap.add_argument('--out_json', default='diff_report.json', help='Pfad zur JSON-Zusammenfassung')
    ap.add_argument('--title', default='PDF‑Diff Bericht (Erweitert)', help='Titel für HTML-Bericht')
    args = ap.parse_args()

    use_stop = (args.stopwords == 'de')

    build_reports(
        template_pdf=args.template,
        changed_pdf=args.changed,
        segmentation=args.segmentation,
        min_copy_words=args.min_copy_words,
        near_ratio=args.near_ratio,
        use_stopwords=use_stop,
        regex_list=args.regex,
        want_heatmap=args.heatmap,
        out_md=args.out_md,
        out_html=args.out_html,
        out_json=args.out_json,
        report_title=args.title
    )

if __name__ == '__main__':
    main()
