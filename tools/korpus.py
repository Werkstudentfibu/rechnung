"""Baustein 1: Uebungssatz mit Soll-Werten aus frei lizenzierten E-Rechnungen.

Liest den geklonten ZUGFeRD-Korpus (https://github.com/ZUGFeRD/corpus,
Apache-2.0) ueber das vorhandene ``rechnung.einvoice`` (Stufe 0, CII/UBL) aus
und baut daraus Testfaelle im selben Format wie ``rechnung.testsatz``: je
Rechnung die Soll-Werte (Nr, Datum, Faelligkeit, Netto, USt, Brutto,
Waehrung, USt-IdNr, IBAN, Lieferantenname) und die Soll-Positionen.

Der Korpus selbst liegt NICHT im Repo (``RECHNUNG_KORPUS_ZUGFERD``, Standard
``~/korpus/zugferd``) -- nur dieses Werkzeug und seine Ausgabe
(``~/korpus/testsatz_zugferd.json`` + Manifest) sind versioniert.

Ausschluss:
- Der Korpus enthaelt absichtlich fehlerhafte Testdateien fuer Validatoren
  (Ordner ``fail/``, siehe corpus-README) -- die sind kein Uebungsmaterial
  fuer "richtig lesen" und werden uebersprungen.
- Dateien ohne auswertbares eingebettetes/eigenstaendiges XML (UBL/CII)
  liefern ``einvoice.load() -> None`` und werden ebenfalls uebersprungen
  (z.B. FatturaPA, PEPPOL-XML ohne PDF, reine Scans).

Dubletten: derselbe reale Rechnungsinhalt taucht im Korpus oft mehrfach auf
(verschiedene ZUGFeRD-/Factur-X-Profile/-Versionen derselben Beispielrechnung
-- Aaron 13.10.2026). Dateien mit gleicher Rechnungsnummer + Lieferant +
Betraegen werden zu einem "Szenario" gruppiert, damit die Messung nicht
denselben Fall mehrfach gewichtet.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rechnung import einvoice  # noqa: E402

KORPUS_ENV = "RECHNUNG_KORPUS_ZUGFERD"
DEFAULT_ROOT = Path.home() / "korpus" / "zugferd"
AUSGABE = Path.home() / "korpus" / "testsatz_zugferd.json"
MANIFEST = Path.home() / "korpus" / "MANIFEST_zugferd.md"

LIZENZ = "Apache License 2.0"
QUELLE = "https://github.com/ZUGFeRD/corpus"

# Ordner/Dateinamen-Anteile, die absichtlich fehlerhafte Testdateien
# markieren (ZUGFeRD-corpus-README: "partly with artificial errors").
_KAPUTT_MARKER = re.compile(r"(^|/)fail(/|$)", re.IGNORECASE)


def korpus_root() -> Path:
    return Path(os.environ.get(KORPUS_ENV, str(DEFAULT_ROOT))).expanduser()


def _ist_absichtlich_fehlerhaft(relpfad: str) -> bool:
    return bool(_KAPUTT_MARKER.search(relpfad.replace(os.sep, "/")))


def _positionen(inv: einvoice.EInvoice) -> list[dict]:
    out = []
    for p in inv.positionen:
        out.append({
            "pos": p.get("pos") or "",
            "beschreibung": p.get("bezeichnung") or "",
            "menge": p.get("menge"),
            "einzelpreis_netto": p.get("einzelpreis"),
            "gesamt_netto": p.get("netto"),
            "mwst_satz": p.get("ust_satz"),
        })
    return out


def _szenario_id(inv: einvoice.EInvoice) -> str:
    """Gruppenschluessel fuer "derselbe reale Rechnungsinhalt". Fehlen die
    Kernfelder (Nr/Betrag), bleibt jede Datei ihr eigenes Szenario -- besser
    ein Fall zu viel gezaehlt als zwei verschiedene faelschlich verschmolzen."""
    kern = (inv.rechnungsnummer or "", inv.verkaeufer_name or "",
            inv.rechnungsdatum or "", inv.brutto, inv.netto, inv.waehrung or "")
    if not inv.rechnungsnummer or inv.brutto is None:
        return "einzel:" + hashlib.sha1(repr(kern).encode()).hexdigest()
    return "szenario:" + hashlib.sha1(repr(kern).encode()).hexdigest()


def sammeln(root: Path | None = None) -> tuple[list[dict], list[dict]]:
    """-> (Testfaelle, Uebersprungene). Ein Testfall je lesbarer PDF/XML,
    mit Szenario-Gruppenschluessel fuer die Dubletten-Zusammenfassung."""
    root = root or korpus_root()
    faelle: list[dict] = []
    uebersprungen: list[dict] = []
    if not root.is_dir():
        return faelle, uebersprungen
    pfade = sorted(p for p in root.rglob("*")
                   if p.is_file() and p.suffix.lower() in (".pdf", ".xml")
                   and ".git" not in p.parts)
    for p in pfade:
        rel = str(p.relative_to(root))
        if _ist_absichtlich_fehlerhaft(rel):
            uebersprungen.append({"datei": rel, "grund": "absichtlich fehlerhaft (fail/)"})
            continue
        try:
            inv = einvoice.load(p)
        except Exception as e:  # noqa: BLE001 -- defekte Fremddatei darf den Lauf nie stoppen
            uebersprungen.append({"datei": rel, "grund": f"Fehler beim Lesen: {e}"})
            continue
        if inv is None:
            uebersprungen.append({"datei": rel, "grund": "kein auswertbares XML (CII/UBL)"})
            continue
        sha256 = hashlib.sha256(p.read_bytes()).hexdigest()
        felder = {
            "rechnungsnummer": inv.rechnungsnummer or None,
            "rechnungsdatum": inv.rechnungsdatum or None,
            "faelligkeit": inv.faelligkeit or None,
            "netto": inv.netto,
            "ust_betrag": inv.ust_betrag,
            "brutto": inv.brutto,
            "waehrung": inv.waehrung or None,
            "ustid": inv.verkaeufer_ustid or None,
            "iban": inv.verkaeufer_iban[0] if inv.verkaeufer_iban else None,
            "verkaeufer_name": inv.verkaeufer_name or None,
        }
        felder = {k: v for k, v in felder.items() if v not in (None, "")}
        faelle.append({
            "sha256": sha256,
            "datei": rel,
            "quelle": QUELLE,
            "lizenz": LIZENZ,
            "format": f"{inv.syntax}:{inv.profile}" if inv.profile else inv.syntax,
            "szenario_id": _szenario_id(inv),
            "felder": felder,
            "positionen": _positionen(inv),
            "ust_aufstellung": inv.steuern,
            "typ": "gutschrift_korrektur" if inv.is_correction else "rechnung",
        })
    return faelle, uebersprungen


def szenarien(faelle: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for f in faelle:
        out.setdefault(f["szenario_id"], []).append(f)
    return out


def _git_commit(root: Path) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=5, check=False)
        return r.stdout.strip() or None
    except Exception:
        return None


def manifest_text(root: Path, faelle: list[dict], uebersprungen: list[dict]) -> str:
    gruppen = szenarien(faelle)
    commit = _git_commit(root)
    lines = [
        "# Manifest: ZUGFeRD-Korpus (frei lizenzierte Testdaten)",
        "",
        f"Quelle: {QUELLE}",
        f"Lizenz: {LIZENZ} (siehe `{root}/LICENSE`)",
        f"Lokaler Klon: `{root}`" + (f" (Commit `{commit}`)" if commit else ""),
        "",
        "Verwendung: ausschliesslich zur Messung und Verbesserung des eigenen "
        "Lese-Programms (Uebungskorpus, kein echter Geschaeftsvorfall). "
        "Keine Weitergabe der Original-PDFs/-XML ausserhalb dieses Rechners.",
        "",
        f"Verwertbare Dateien: {len(faelle)} in {len(gruppen)} Szenarien "
        f"(Dubletten ueber Format-/Profilversionen zusammengefasst).",
        f"Uebersprungen: {len(uebersprungen)} "
        f"(davon absichtlich fehlerhaft: "
        f"{sum(1 for u in uebersprungen if 'fehlerhaft' in u['grund'])}).",
        "",
        "## Je Datei",
        "",
        "| Datei | Format | Szenario | Lizenz |",
        "|---|---|---|---|",
    ]
    for f in faelle:
        lines.append(f"| {f['datei']} | {f['format']} | {f['szenario_id'][:15]}... | {LIZENZ} |")
    if uebersprungen:
        lines += ["", "## Uebersprungen", "", "| Datei | Grund |", "|---|---|"]
        for u in uebersprungen:
            lines.append(f"| {u['datei']} | {u['grund']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    root = korpus_root()
    faelle, uebersprungen = sammeln(root)
    AUSGABE.parent.mkdir(parents=True, exist_ok=True)
    AUSGABE.write_text(json.dumps(
        {"quelle": QUELLE, "lizenz": LIZENZ, "root": str(root),
         "faelle": faelle, "uebersprungen": uebersprungen},
        indent=2, ensure_ascii=False), encoding="utf-8")
    MANIFEST.write_text(manifest_text(root, faelle, uebersprungen), encoding="utf-8")
    gruppen = szenarien(faelle)
    print(f"{len(faelle)} verwertbare Rechnungen in {len(gruppen)} Szenarien "
          f"aus {root} -> {AUSGABE}")
    print(f"{len(uebersprungen)} uebersprungen -> siehe {MANIFEST}")


if __name__ == "__main__":
    main()
