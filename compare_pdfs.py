#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Dokumentenvergleich (Option 2 – format-/struktur-sensitiv) für zwei PDFs:
- TEMPLATE (file_1.pdf)      -> Referenz
- GEÄNDERTES DOKUMENT (file_2.pdf) -> Vergleich

Ausgabe:
- diff_report.md (Hauptbericht, Markdown)
- diffs_added.csv / diffs_deleted.csv / diffs_modified.csv
- copies_ge50w.csv (kopierte Passagen >= N Wörter)

Ohne zusätzliche Internet-Abhängigkeiten. Benötigt: PyPDF2 (vorhanden).
"""

import argparse
import re
import math
import csv
import hashlib
import unicodedata
from collections import defaultdict, namedtuple
from difflib import SequenceMatcher
from typing import List, Tuple, Dict, Iterable, Optional

import PyPDF2

# ---------------------------
# Utility: kleine deutsche Stopwortliste (optional)
# ---------------------------
DE_STOPWORDS = {
    "der","die","das","und","oder","aber","dass","daß","ist","sind","war","waren","ein","eine","einer","eines",
    "im","in","zu","vom","von","am","an","auf","für","mit","ohne","aus","als","auch","es","den","dem","des","sowie",
    "so","bei","durch","werden","wird","sich","nicht","nur","mehr","weniger"
}

# ---------------------------
# PDF: Text extrahieren (pro Seite)
# ---------------------------
def extract_pdf_text(path: str) -> List[str]:
    pages = []
    with open(path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for p in reader.pages:
            try:
                t = p.extract_text() or ""
            except Exception:
                t = ""
            # Normalisiere Zeilenenden
            t = t.replace("\r\n", "\n").replace("\r", "\n")
            pages.append(t)
    return pages

# ---------------------------
# Struktur-Erkennung: Überschriften / Abschnittsnummern / Tabellenblöcke
# ---------------------------
Header = namedtuple("Header", ["level", "title", "page_idx", "line_idx", "anchor"])

HEADER_PATTERNS = [
    # z.B. "1", "1.1", "2.3.4"
    re.compile(r"^\s*(\d+(?:\.\d+){0,3})\s+([^\n]{3,})$"),
    # Markdown-ähnliche (# Titel) kommt aus Export selten vor, lassen optional drin
    re.compile(r"^\s*#{1,6}\s+(.+)$"),
    # "Anhang A", "Annex A", optional
    re.compile(r"^\s*(Anhang|Annex)\s+[A-Z]\b.*$")
]

def detect_headers(pages: List[str]) -> List[Header]:
    headers = []
    for pi, page in enumerate(pages):
        for li, line in enumerate(page.split("\n")):
            stripped = line.strip()
            if not stripped:
                continue
            matched = False
            # Abschnittsnummern
            m = HEADER_PATTERNS[0].match(stripped)
            if m:
                num = m.group(1)
                title = m.group(2).strip()
                level = num.count(".") + 1
                anchor = f"{num} {title}"
                headers.append(Header(level, anchor, pi, li, anchor))
                continue
            # Markdown-Header
            m = HEADER_PATTERNS[1].match(stripped)
            if m:
                title = m.group(1).strip()
                level = 1
                anchor = title
                headers.append(Header(level, anchor, pi, li, anchor))
                continue
            # Annex
            m = HEADER_PATTERNS[2].match(stripped)
            if m:
                level = 1
                anchor = stripped
                headers.append(Header(level, anchor, pi, li, anchor))
                continue
    return headers

# ---------------------------
# Tabellen-Erkennung (heuristisch): viele Spalten-Trenner, Bullet/Cell-Muster etc.
# ---------------------------
def is_table_like_block(lines: List[str], tolerance: float = 0.75) -> bool:
    """
    Sehr einfache Heuristik:
    - viele Zeilen mit mehreren "Spaltenmerkmalen" (z. B. 2+ Räume, |, Tabs)
    - Prozent der Zeilen, die "tabellarisch" wirken, muss >= tolerance
    """
    if not lines:
        return False
    tableish = 0
    for ln in lines:
        l = ln.strip()
        if not l:
            continue
        # Spaltenindikatoren: mehrere Spaces / Pipes / Tabs
        # oder häufige "Spalten-Header"-Wörter (Version, Datum, Beschreibung, ...)
        if (l.count("  ") >= 1) or ("\t" in l) or ("│" in l) or ("|" in l):
            tableish += 1
            continue
        if re.search(r"\bVersion\b|\bDatum\b|\bBeschreibung\b|\bBehandlungszeitraum\b", l, re.I):
            tableish += 1
    ratio = tableish / max(1, len(lines))
    return ratio >= tolerance

# ---------------------------
# Segmentierung: Abschnitt -> Absatz -> Satz (hierarchisch)
# ---------------------------
Segment = namedtuple("Segment", ["section_path", "paragraph_idx", "sentence_idx", "text", "page_span"])

def split_sentences(text: str) -> List[str]:
    # einfache Satzsegmentierung auf Deutsch (heuristisch)
    # Punkte in Abkürzungen werden grob ignoriert, dennoch robust
    text = text.replace("…", ".")
    # Trenne auf . ! ? gefolgt von Leerzeichen/Zeilenende, aber nicht bei Nummerierungen "1.2.3"
    # Grobe Heuristik:
    candidates = re.split(r"(?<=[^\d][\.\!\?])\s+", text.strip())
    # Cleanup
    out = []
    for c in candidates:
        s = c.strip()
        if s:
            out.append(s)
    return out

def segment_document(pages: List[str], mode: str = "auto", table_tol: float = 0.75) -> List[Segment]:
    """
    mode:
      - 'section'    : segmentiert nur nach Überschriften, innerhalb als ein Block
      - 'paragraph'  : pro Absatz
      - 'sentence'   : pro Satz
      - 'auto'       : Abschnitt -> Absatz -> Satz (liefert feinste Segmente mit Strukturpfad)
    """
    headers = detect_headers(pages)
    header_map = {(h.page_idx, h.line_idx): h for h in headers}

    # Dokument zu Zeilen mit (page_idx, line_idx)
    lines_by_page = [pg.split("\n") for pg in pages]

    # Abschnitts-Pfade bauen
    segments: List[Segment] = []
    current_path: List[str] = []

    def current_section_path() -> str:
        return " / ".join(current_path) if current_path else "ROOT"

    for pi, lines in enumerate(lines_by_page):
        buf: List[str] = []
        para_idx = 0
        sent_idx_global = 0

        def flush_buffer_as_segments():
            nonlocal buf, para_idx, sent_idx_global
            if not buf:
                return
            block = "\n".join(buf).strip()
            if not block:
                buf = []
                return
            # Absatzsplitting
            paragraphs = [p for p in re.split(r"\n\s*\n", block) if p.strip()]
            if mode == "section":
                # gesamter Block als 1 Segment
                seg = Segment(current_section_path(), para_idx, -1, block, (pi, pi))
                segments.append(seg)
                para_idx += 1
                buf = []
                return

            for p in paragraphs:
                p_clean = p.strip()
                if not p_clean:
                    continue
                if mode == "paragraph":
                    seg = Segment(current_section_path(), para_idx, -1, p_clean, (pi, pi))
                    segments.append(seg)
                    para_idx += 1
                    continue
                # sentence / auto
                sents = split_sentences(p_clean)
                for si, s in enumerate(sents):
                    seg = Segment(current_section_path(), para_idx, si, s, (pi, pi))
                    segments.append(seg)
                    sent_idx_global += 1
                para_idx += 1
            buf = []

        for li, ln in enumerate(lines):
            # Wechsel bei Überschrift?
            if (pi, li) in header_map:
                # bisherigen Puffer flushen
                flush_buffer_as_segments()
                # neue Überschrift setzen
                h = header_map[(pi, li)]
                # Level basierte Hierarchie
                while len(current_path) >= h.level:
                    current_path.pop()
                current_path.append(h.title)
                continue
            buf.append(ln)

        # Seitenende flush
        flush_buffer_as_segments()

    return segments

# ---------------------------
# Normalisierung / Tokenisierung
# ---------------------------
def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    s = s.replace("\xad", "")
    s = s.replace("\u2009", " ").replace("\u00A0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()

def tokens(s: str, stopwords: Optional[set] = None) -> List[str]:
    s = normalize_text(s.lower())
    # entferne einfache Interpunktion
    s = re.sub(r"[^\wäöüÄÖÜß\-]+", " ", s)
    toks = [t for t in s.split() if t]
    if stopwords:
        toks = [t for t in toks if t not in stopwords]
    return toks

# ---------------------------
# Ähnlichkeitsmetriken
# ---------------------------
def jaccard_similarity(a: Iterable[str], b: Iterable[str]) -> float:
    A = set(a); B = set(b)
    if not A and not B:
        return 1.0
    return len(A & B) / max(1, len(A | B))

def seq_ratio(a: str, b: str) -> float:
    return SequenceMatcher(a=a, b=b).ratio()

def composite_similarity(a_text: str, b_text: str, stopwords: Optional[set]) -> float:
    # Mischung: 50% Token-Jaccard, 50% SequenceMatcher
    a_toks = tokens(a_text, stopwords)
    b_toks = tokens(b_text, stopwords)
    jac = jaccard_similarity(a_toks, b_toks)
    sm = seq_ratio(normalize_text(a_text), normalize_text(b_text))
    return 0.5*jac + 0.5*sm

# ---------------------------
# Segment-Matching (Greedy Best Match pro Segment im Template)
# ---------------------------
def match_segments(template: List[Segment], changed: List[Segment],
                   stopwords: Optional[set], near_ratio: float = 0.92
                   ) -> Tuple[List[Tuple[Segment, Segment, float]],
                              List[Segment], List[Segment]]:
    matched_pairs = []
    unmatched_template = []
    unmatched_changed = set(range(len(changed)))

    # Index über „section_path“ für grobe Vorauswahl (gleiche Sektion bevorzugt)
    idx_by_section: Dict[str, List[int]] = defaultdict(list)
    for i, seg in enumerate(changed):
        idx_by_section[seg.section_path].append(i)

    for tseg in template:
        # Kandidaten: gleiche Sektion zuerst, sonst gesamte Menge
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
        if best_i is not None and best_sim >= 0.35:  # minimale Ähnlichkeit für Match
            matched_pairs.append((tseg, changed[best_i], best_sim))
            if best_i in unmatched_changed:
                unmatched_changed.remove(best_i)
        else:
            unmatched_template.append(tseg)

    unmatched_changed_list = [changed[i] for i in sorted(unmatched_changed)]
    return matched_pairs, unmatched_template, unmatched_changed_list

# ---------------------------
# Kopierte Passagen >= N Wörter (Rolling Hash über Wörter)
# ---------------------------
def find_copied_passages(template: List[Segment], changed: List[Segment],
                         min_words: int = 50, stopwords: Optional[set] = None,
                         near_ratio: float = 0.92) -> List[Dict]:
    """
    Sucht längere (nahezu) identische Passagen über Segmentgrenzen.
    Heuristik: baue Wortfenster über zusammenhängende Segmente.
    """
    def flatten(seg_list: List[Segment]) -> List[Tuple[str, Tuple[int,int], str]]:
        # Rückgabe: [(text, page_span, locator), ...]
        out = []
        for s in seg_list:
            locator = f"{s.section_path} :: Abs.{s.paragraph_idx}{'' if s.sentence_idx<0 else f'/Satz {s.sentence_idx+1}'}"
            out.append((s.text, s.page_span, locator))
        return out

    A = flatten(template)
    B = flatten(changed)

    # Erzeuge Wortlisten (ohne Stopwörter, aber mit Reihenfolge) je Eintrag
    def word_list(t: str) -> List[str]:
        return tokens(t, stopwords=None)  # Für Kopien Erkennung: keine Stopwort-Filterung

    # Rolling Matches
    results = []
    # Erzeuge „Dokument“-Wortströme (vermindertes Risiko von Satzgrenzenverlusten)
    A_stream = []
    A_index_to_meta = []
    for ai, (t, pg, loc) in enumerate(A):
        w = word_list(t)
        for wi, _ in enumerate(w):
            A_stream.append(w[wi])
        A_index_to_meta.append((len(A_stream), loc))  # Endoffset + locator

    B_stream = []
    B_index_to_meta = []
    for bi, (t, pg, loc) in enumerate(B):
        w = word_list(t)
        for wi, _ in enumerate(w):
            B_stream.append(w[wi])
        B_index_to_meta.append((len(B_stream), loc))

    # Hash Maps: Fenster aus A
    win = min_words
    if len(A_stream) < win or len(B_stream) < win:
        return results

    def windows(words: List[str], size: int) -> Dict[str, List[int]]:
        mp = defaultdict(list)
        for i in range(0, len(words) - size + 1):
            chunk = " ".join(words[i:i+size])
            h = hashlib.sha1(chunk.encode("utf-8")).hexdigest()
            mp[h].append(i)
        return mp

    A_wins = windows(A_stream, win)
    # Scanne B und prüfe Hashtreffer, erweitere anschließend nach links/rechts
    for j in range(0, len(B_stream) - win + 1):
        chunk = " ".join(B_stream[j:j+win])
        h = hashlib.sha1(chunk.encode("utf-8")).hexdigest()
        if h not in A_wins:
            continue
        for i in A_wins[h]:
            # expandiere Match
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
            # Ähnlichkeitsprüfung (leichte Toleranz)
            r = seq_ratio(A_text, B_text)
            if r >= near_ratio:
                # Lokalisierung grob anhand Endoffsets
                # (Für Bericht: nur Locator-Strings aus Segmenten)
                # Finde ungefähre Abschnitt/Absatz anhand nächster Marker
                def loc_from_offset(meta_index, offset):
                    for end_off, loc in meta_index:
                        if offset <= end_off:
                            return loc
                    return meta_index[-1][1]

                a_loc = loc_from_offset(A_index_to_meta, i)
                b_loc = loc_from_offset(B_index_to_meta, j)
                results.append({
                    "template_locator": a_loc,
                    "changed_locator": b_loc,
                    "words": length,
                    "similarity": round(r, 4),
                    "excerpt_template": A_text[:800] + ("…" if len(A_text) > 800 else ""),
                    "excerpt_changed": B_text[:800] + ("…" if len(B_text) > 800 else "")
                })
    # Zusammenführen sehr nahe beieinander liegende Treffer könnte ergänzt werden
    # (hier: direkt Roh-Ergebnisse)
    # Dedup nach template_locator + changed_locator + words
    seen = set()
    dedup = []
    for r in sorted(results, key=lambda x: (-x["words"], -x["similarity"])):
        key = (r["template_locator"], r["changed_locator"], r["words"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(r)
    return dedup

# ---------------------------
# Haupt-Workflow
# ---------------------------
def build_report(template_pdf: str,
                 changed_pdf: str,
                 segmentation: str = "auto",
                 min_copy_words: int = 50,
                 stop_lang: Optional[str] = "de",
                 near_ratio: float = 0.92,
                 table_tol: float = 0.75,
                 out_md: str = "diff_report.md"):

    # 1) PDF lesen
    t_pages = extract_pdf_text(template_pdf)
    c_pages = extract_pdf_text(changed_pdf)

    # 2) Segmente bilden
    t_segs = segment_document(t_pages, mode=segmentation, table_tol=table_tol)
    c_segs = segment_document(c_pages, mode=segmentation, table_tol=table_tol)

    # 3) Stopwörter wählen
    stopwords = DE_STOPWORDS if (stop_lang == "de") else None

    # 4) Segment-Matching
    pairs, t_only, c_only = match_segments(t_segs, c_segs, stopwords, near_ratio=near_ratio)

    # 5) Einstufung: gelöscht / hinzugefügt / geändert
    deleted = []
    added = []
    modified = []

    for seg in t_only:
        deleted.append(seg)

    for seg in c_only:
        added.append(seg)

    for tseg, cseg, sim in pairs:
        # "Geändert", wenn Text nicht (nahezu) identisch
        if sim < near_ratio:
            modified.append((tseg, cseg, sim))

    # 6) Kopien >= min_words
    copies = find_copied_passages(t_segs, c_segs, min_words=min_copy_words,
                                  stopwords=None, near_ratio=near_ratio)

    # 7) Globaler Ähnlichkeitsgrad (gewichtete Mischung)
    #    - Wort-Jaccard gesamt
    #    - Satzähnlichkeit gemittelt über Matches
    #    - Struktur-Overlap (gleiche section_path Vorkommen)
    t_all = "\n".join([s.text for s in t_segs])
    c_all = "\n".join([s.text for s in c_segs])

    jac = jaccard_similarity(tokens(t_all, stopwords), tokens(c_all, stopwords))
    if pairs:
        avg_pair_sim = sum(sim for _, _, sim in pairs) / len(pairs)
    else:
        avg_pair_sim = 0.0
    t_sections = set(s.section_path for s in t_segs)
    c_sections = set(s.section_path for s in c_segs)
    struct_overlap = len(t_sections & c_sections) / max(1, len(t_sections | c_sections))

    # Gewichtung (kann bei Bedarf angepasst werden)
    global_sim = 0.4*jac + 0.4*avg_pair_sim + 0.2*struct_overlap
    global_sim_pct = int(round(100*global_sim))

    # 8) Exporte (CSV) + Markdown-Bericht
    def write_csv(path: str, rows: List[Dict[str, str]], fieldnames: List[str]):
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                w.writerow(r)

    # CSV: added / deleted / modified / copies
    write_csv("diffs_deleted.csv", [
        {
            "Position (Template)": f"{s.section_path} :: Abs.{s.paragraph_idx}{'' if s.sentence_idx<0 else f'/Satz {s.sentence_idx+1}'}",
            "Text (Template)": s.text
        } for s in deleted
    ], ["Position (Template)", "Text (Template)"])

    write_csv("diffs_added.csv", [
        {
            "Position (Geändert)": f"{s.section_path} :: Abs.{s.paragraph_idx}{'' if s.sentence_idx<0 else f'/Satz {s.sentence_idx+1}'}",
            "Text (Geändert)": s.text
        } for s in added
    ], ["Position (Geändert)", "Text (Geändert)"])

    write_csv("diffs_modified.csv", [
        {
            "Position (Template)": f"{t.section_path} :: Abs.{t.paragraph_idx}{'' if t.sentence_idx<0 else f'/Satz {t.sentence_idx+1}'}",
            "Position (Geändert)": f"{c.section_path} :: Abs.{c.paragraph_idx}{'' if c.sentence_idx<0 else f'/Satz {c.sentence_idx+1}'}",
            "Ähnlichkeit (0-1)": f"{sim:.4f}",
            "Vorher (Template)": t.text,
            "Nachher (Geändert)": c.text
        } for (t, c, sim) in modified
    ], ["Position (Template)", "Position (Geändert)", "Ähnlichkeit (0-1)", "Vorher (Template)", "Nachher (Geändert)"])

    write_csv("copies_ge50w.csv", [
        {
            "Locator (Template)": r["template_locator"],
            "Locator (Geändert)": r["changed_locator"],
            "Wortanzahl": r["words"],
            "Ähnlichkeit (0-1)": r["similarity"],
            "Zitat (Template, gekürzt)": r["excerpt_template"],
            "Zitat (Geändert, gekürzt)": r["excerpt_changed"]
        } for r in copies
    ], ["Locator (Template)", "Locator (Geändert)", "Wortanzahl", "Ähnlichkeit (0-1)",
        "Zitat (Template, gekürzt)", "Zitat (Geändert, gekürzt)"])

    # Markdown-Tabellen (kompakt, nicht alle Zeilen – für Details CSV öffnen)
    md = []
    md.append("# Diff‑Bericht (TEMPLATE = file_1.pdf, GEÄNDERT = file_2.pdf)\n")
    md.append("## 1) Ähnlichkeitsgrad\n")
    md.append(f"- **Gesamte Übereinstimmung**: **{global_sim_pct}%**\n")
    md.append(f"    - Token‑Jaccard: {jac:.3f} | Ø‑Satzähnlichkeit (Matches): {avg_pair_sim:.3f} | Struktur‑Overlap: {struct_overlap:.3f}\n")

    # Kopien
    md.append("\n## 2) Kopierte Textabschnitte (≥ Mindestlänge)\n")
    if copies:
        md.append("| # | Locator (Template) | Locator (Geändert) | Wörter | Ähnlichkeit |")
        md.append("|---:|---|---|---:|---:|")
        for i, r in enumerate(copies, 1):
            md.append(f"| {i} | {r['template_locator']} | {r['changed_locator']} | {r['words']} | {r['similarity']:.3f} |")
        md.append("\n> *Vollständige Zitate in* `copies_ge50w.csv`.\n")
    else:
        md.append("> Keine kopierten Abschnitte gemäß Schwellwert gefunden.\n")

    # Gelöscht / Hinzugefügt
    md.append("\n## 3) Änderungen\n")
    md.append("### 3.1 Gelöschter Text aus Template\n")
    if deleted:
        md.append("| # | Position (Template) | Auszug |")
        md.append("|---:|---|---|")
        for i, s in enumerate(deleted[:50], 1):
            excerpt = (s.text[:240] + "…") if len(s.text) > 240 else s.text
            md.append(f"| {i} | {s.section_path} :: Abs.{s.paragraph_idx}{'' if s.sentence_idx<0 else f'/Satz {s.sentence_idx+1}'} | {excerpt} |")
        if len(deleted) > 50:
            md.append(f"\n> **{len(deleted)-50} weitere…** Details in `diffs_deleted.csv`.")
    else:
        md.append("> Kein ausschließlich im Template vorhandener Text.\n")

    md.append("\n### 3.2 Neu hinzugefügter Text im geänderten Dokument\n")
    if added:
        md.append("| # | Position (Geändert) | Auszug |")
        md.append("|---:|---|---|")
        for i, s in enumerate(added[:50], 1):
            excerpt = (s.text[:240] + "…") if len(s.text) > 240 else s.text
            md.append(f"| {i} | {s.section_path} :: Abs.{s.paragraph_idx}{'' if s.sentence_idx<0 else f'/Satz {s.sentence_idx+1}'} | {excerpt} |")
        if len(added) > 50:
            md.append(f"\n> **{len(added)-50} weitere…** Details in `diffs_added.csv`.")
    else:
        md.append("> Kein ausschließlich im geänderten Dokument vorhandener Text.\n")

    md.append("\n### 3.3 Geänderte Sätze/Absätze (Vorher ↔ Nachher)\n")
    if modified:
        md.append("| # | Position (Template) | Position (Geändert) | Ähnlichkeit | Vorher (Template) | Nachher (Geändert) |")
        md.append("|---:|---|---|---:|---|---|")
        for i, (tseg, cseg, sim) in enumerate(modified[:50], 1):
            before = (tseg.text[:180] + "…") if len(tseg.text) > 180 else tseg.text
            after  = (cseg.text[:180] + "…") if len(cseg.text) > 180 else cseg.text
            pos_t  = f"{tseg.section_path} :: Abs.{tseg.paragraph_idx}{'' if tseg.sentence_idx<0 else f'/Satz {tseg.sentence_idx+1}'}"
            pos_c  = f"{cseg.section_path} :: Abs.{cseg.paragraph_idx}{'' if cseg.sentence_idx<0 else f'/Satz {cseg.sentence_idx+1}'}"
            md.append(f"| {i} | {pos_t} | {pos_c} | {sim:.3f} | {before} | {after} |")
        if len(modified) > 50:
            md.append(f"\n> **{len(modified)-50} weitere…** Details in `diffs_modified.csv`.")
    else:
        md.append("> Keine partiell geänderten Segmente (alle Matches nahezu identisch oder nur hinzugefügt/gelöscht).\n")

    # Zusammenfassung
    md.append("\n## 4) Zusammenfassung\n")
    md.append(f"- **Gesamte Übereinstimmung**: **{global_sim_pct}%**\n")
    md.append(f"- **Kopierte Abschnitte (≥ {min_copy_words} Wörter)**: {len(copies)}\n")
    md.append(f"- **Gelöscht**: {len(deleted)} | **Hinzugefügt**: {len(added)} | **Geändert**: {len(modified)}\n")
    md.append("\n**Hinweis:** Detaillierte Diffs und Zitate siehe die erzeugten CSV‑Dateien.\n")

    with open(path, "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)

    print(f"[OK] Bericht geschrieben: {out_md}")
    print(f"     CSVs: diffs_added.csv, diffs_deleted.csv, diffs_modified.csv, copies_ge50w.csv")
    print(f"     Übereinstimmung gesamt: {global_sim_pct}%")

# ---------------------------
# CLI
# ---------------------------
def main():
    ap = argparse.ArgumentParser(description="PDF-Dokumentenvergleich (Option 2, format-/struktur-sensitiv).")
    ap.add_argument("--template", required=True, help="Pfad zum Template-PDF (file_1.pdf).")
    ap.add_argument("--changed", required=True, help="Pfad zum geänderten PDF (file_2.pdf).")
    ap.add_argument("--segmentation", choices=["auto","section","paragraph","sentence"], default="auto",
                    help="Segmentierungsmodus (default: auto = Abschnitt→Absatz→Satz).")
    ap.add_argument("--min_copy_words", type=int, default=50,
                    help="Mindestlänge kopierter Passagen in Wörtern (default: 50).")
    ap.add_argument("--stopwords", choices=["none","de"], default="de",
                    help="Stopwort-Set für Metriken (default: de).")
    ap.add_argument("--near_ratio", type=float, default=0.92,
                    help="Schwelle für 'nahezu identisch' (default: 0.92).")
    ap.add_argument("--table_tolerance", type=float, default=0.75,
                    help="Empfindlichkeit der Tabellenheuristik (unused in report, reserviert).")
    ap.add_argument("--out", default="diff_report.md", help="Pfad für die Markdown-Ausgabe.")
    args = ap.parse_args()

    stop_lang = None if args.stopwords == "none" else "de"
    build_report(
        template_pdf=args.template,
        changed_pdf=args.changed,
        segmentation=args.segmentation,
        min_copy_words=args.min_copy_words,
        stop_lang=stop_lang,
        near_ratio=args.near_ratio,
        table_tol=args.table_tolerance,
        out_md=args.out
    )

if __name__ == "__main__":
    main()