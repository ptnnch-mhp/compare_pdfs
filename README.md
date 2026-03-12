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

---

## 📦 Installation

```bash
pip install PyPDF2
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
  --stopwords de \
  --near_ratio 0.92 \
  --out diff_report.md
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

---

### 📁 Outputs

* diff_report.md – Übersicht
* diffs_added.csv
* diffs_deleted.csv
* diffs_modified.csv
* copies_ge50w.csv

---

### 🔠 UTF‑8 & Umlaute

Das Script verwendet:
* encoding="utf-8"
* unicodedata.normalize("NFC", ...)
* Entfernt Soft Hyphens und NBSP
