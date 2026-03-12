# Dokumentenvergleich – PDF Diff Tool (UTF‑8 Support)

Dieses Repository enthält ein leistungsfähiges Python‑Werkzeug zum **Vergleich zweier PDF‑Dokumente**.  
Es unterstützt:

- strukturellen Vergleich (Kapitel, Abschnitte, Tabellen)
- inhaltlichen Vergleich (Absätze, Sätze)
- Identifikation kopierter Passagen (≥ konfigurierbare Wortzahl)
- Erkennung von Änderungen: gelöscht, hinzugefügt, geändert
- UTF‑8‑vollständige Unterstützung (inkl. Umlaute)
- Markdown‑Bericht + CSV‑Diff‑Dateien

Ideal für Revision, Compliance, technische Dokumentation, Redlining und Versionierung.

---

## 🚀 Features

- **PDF‑Extraktion mit PyPDF2**
- **Hierarchische Segmentierung**
  - Abschnitt → Absatz → Satz
- **Ähnlichkeitsmetriken**
  - Token‑Jaccard
  - SequenceMatcher
  - Struktur-Overlap
- **Kopierte Textabschnitte ≥ N Wörter**
- **CSV‑Exports**:
  - `diffs_added.csv`
  - `diffs_deleted.csv`
  - `diffs_modified.csv`
  - `copies_ge50w.csv`
- **Diff‑Bericht als Markdown (`diff_report.md`)**
- **UTF‑8 Normalisierung**
  - Umlaute
  - Soft Hyphen
  - Unicode‑Bereinigung
- **HTML‑Bericht (--out_html) mit farbigen Highlights für Einfügungen (grün), Löschungen (rot, durchgestrichen) und Ersetzungen (gelb).**
- **Regex‑Highlighting: mit --regex "CISO" oder mehrfachen --regex‑Flags markierst du Schlüsselbegriffe in den Reports.**
- **Kopierte Passagen (Rolling‑Window‑Match) ab --min_copy_words N, inkl. CSV copies_geN.csv.**
- **Ähnlichkeits‑Heatmap (--heatmap) als diff_heatmap.png (benötigt matplotlib + numpy).**
- **JSON‑Summary (--out_json) für Pipeline/Automation.**

---

## 📦 Installation

```bash
pip install PyPDF2 matplotlib numpy
git clone https://github.com/ptnnch-mhp/compare_pdfs.git
cd compare_pdfs
```

---

## ▶️ Nutzung

```bash
python compare_pdfs.py \
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

```

### Parameter

| Parameter | Beschreibung | Standard |
| ---------- | ---------- | ---------- | 
| --template | Template‑PDF (ältere Version) | Pflicht |
| --changed | Geändertes Dokument | Pflicht |
| --segmentation | auto / section / paragraph / sentence | auto | 
| --min_copy_words | Mindestwortzahl kopierter Passagen | 50 | 
| --stopwords | none / de | de | 
| --near_ratio | Ähnlichkeitsschwelle | 0.92 | 
| --table_tolerance | Tabellenheuristik | 0.75 | 
| --out | Markdown‑Bericht | diff_report.md | 
| --out_html | HTML-Datei mit Änderungen | diff_report.html |
|  --out_json | JSON-Output | diff_report.json |

---

### 📁 Outputs

* diff_report.md – kompakte Übersicht
* diff_report.html – interaktiver HTML‑Diff mit Highlights
* diff_report.json – Kennzahlen (Ähnlichkeit, Counts)
* diffs_added.csv, diffs_deleted.csv, diffs_modified.csv, copies_geN.csv
* (optional) diff_heatmap.png – Visualisierung von Segment‑Similarities

---

### 🔠 UTF‑8 & Umlaute

Das Script verwendet:
* encoding="utf-8"
* unicodedata.normalize("NFC", ...)
* Entfernt Soft Hyphens und NBSP
