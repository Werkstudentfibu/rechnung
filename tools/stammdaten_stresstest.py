"""Stresstest fuer den Excel-Stammdaten-Import (rein SYNTHETISCH, keine echten Daten).

Erzeugt eine Kreditorenliste (DE + CH) im Layout von Aarons echten Kopfzeilen
(Konto/Bezeichnung 1/Name1/SammelKt/Name2/Straße/Land/PlzOrt/Ortsbezeichnung)
mit typischem Excel-Schmutz sowie eine Mandanten- und Postfachliste, schreibt
sie als xlsx und laesst sie durch den echten Weg laufen (read_workbook ->
header_and_rows -> vorschau). Ausgabe: Fragen gesamt, je Gruppe, Laufzeit.

    python tools/stammdaten_stresstest.py [--zeilen 3000] [--seed 1] [--xlsx-out datei.xlsx]
"""

from __future__ import annotations

import argparse
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from _xlsx_helpers import Zahl, write_workbook  # noqa: E402

from rechnung import stammdaten_import as si  # noqa: E402
from rechnung.store import Store  # noqa: E402
from rechnung.xlsx import header_and_rows, read_workbook  # noqa: E402

KOPF = ["Konto", "Bezeichnung 1", "Name1", "SammelKt", "Name2", "Straße", "Land",
        "PlzOrt", "Ortsbezeichnung"]
MAND_KOPF = ["Mandanten Nr.", "Bezeichnung", "WJ", "LM"]
POST_KOPF = ["MDT", "E-Mail", "Passwort", "Bezeichnung", "Name"]

_STAMM = ["Muster", "Beispiel", "Probe", "Test", "Demo", "Platzhalter", "Alpha", "Beta",
          "Gamma", "Delta", "Nord", "Süd", "Ost", "West", "Rhein", "Elbe", "Alpen"]
_BRANCHE = ["Hotelbedarf", "Treuhand", "Getränke", "Reinigung", "Elektro", "Bau",
            "Druck", "Logistik", "Software", "Catering", "Wäscherei", "Technik"]
_FORM = ["GmbH", "AG", "KG", "GmbH & Co. KG", "e.K.", "UG"]
_ORTE_DE = [("50667", "Köln"), ("10115", "Berlin"), ("80331", "München"),
            ("20095", "Hamburg"), ("01067", "Dresden"), ("04109", "Leipzig"),
            ("06108", "Halle"), ("01309", "Dresden")]   # einige mit fuehrender Null
_ORTE_CH = [("8001", "Zürich"), ("3011", "Bern"), ("4001", "Basel"), ("6003", "Luzern")]


def _name(i: int) -> str:
    r = random.Random(i * 7919)
    return (f"{r.choice(_STAMM)} {r.choice(_BRANCHE)} {i} {r.choice(_FORM)}")


def _zeile(i: int, land: str, konto: int) -> list:
    name = _name(i if land == "DE" else i + 100000)
    ort = random.Random(i).choice(_ORTE_DE if land == "DE" else _ORTE_CH)
    n1, n2 = (name[:25], name[25:].strip()) if len(name) > 30 else (name, "")
    return [str(konto), name[:40], n1, "1604" if land == "DE" else "1605", n2,
            f"Musterstraße {i % 97 + 1}", land, ort[0], ort[1]]


def erzeuge(zeilen: int = 3000, seed: int = 1) -> dict[str, list[list]]:
    """-> {Blattname: Zeilen} (Zellen: str oder ``Zahl``). Rund ``zeilen``
    Datenzeilen insgesamt ueber DE + CH, davon etwa ein Viertel Schmutz."""
    rnd = random.Random(seed)
    n_de, n_ch = int(zeilen * 0.72), int(zeilen * 0.18)
    de = [_zeile(i, "DE", 70000 + i) for i in range(n_de)]
    ch = [_zeile(i, "CH", 80000 + i) for i in range(n_ch)]

    def schmutz(basis: list[list], land: str, anzahl: int) -> list[list]:
        out: list[list] = []
        for k in range(anzahl):
            src = rnd.choice(basis)
            z = list(src)
            art = k % 7
            if art == 0:                      # exakt doppelt
                pass
            elif art == 1:                    # Leerzeichen/Gross-Klein
                z[2] = "  " + z[2].upper() + "  "
                z[4] = " " + z[4]
            elif art == 2:                    # nur leer vs. gefuellt (Strasse/PLZ fehlen)
                z[5] = z[7] = z[8] = ""
            elif art == 3:                    # gleicher Name, andere Kontonummer
                z[0] = str(int(src[0]) + 500000)
            elif art == 4:                    # gleicher Name + Konto, andere Anschrift
                z[5] = "Ganz andere Straße 99"
                z[7], z[8] = rnd.choice(_ORTE_DE if land == "DE" else _ORTE_CH)
            elif art == 5:                    # Konto ohne Namen
                z = [str(int(src[0]) + 900000)] + [""] * 8
            else:                             # geschuetzte Leerzeichen, Tab, Umbruch
                z[2] = z[2].replace(" ", " ", 1)
                z[5] = z[5].replace(" ", "\t") + "\n"
            out.append(z)
        return out

    de += schmutz(de, "DE", int(zeilen * 0.07))
    ch += schmutz(ch, "CH", int(zeilen * 0.03))

    for z in de:                              # Zahlzellen wie aus Excel
        r = rnd.random()
        if r < 0.30:
            z[0] = Zahl(z[0] + ".0")
        elif r < 0.33 and len(z[0]) == 5 and z[0].isdigit():
            z[0] = Zahl(f"{int(z[0]) / 10000:.4f}E+4")   # 70001 -> 7.0001E+4
        if z[7] and len(z[7]) == 5 and z[7].startswith("0") and rnd.random() < 0.8:
            z[7] = Zahl(z[7].lstrip("0"))     # Excel hat die fuehrende Null gefressen
        elif z[7] and z[7].isdigit():
            z[7] = Zahl(z[7] + ".0") if rnd.random() < 0.2 else z[7]
    rnd.shuffle(de)
    rnd.shuffle(ch)

    def mit_kopfzeilen(rows: list[list]) -> list[list]:
        out = [list(KOPF)]
        for n, r in enumerate(rows, start=1):
            out.append(r)
            if n % 400 == 0:
                out.append(list(KOPF))        # wiederholte Kopfzeile (Seitenumbruch)
        out += [["", "", "", "Summe: %d" % len(rows)] + [""] * 5, ["-"] * 9]
        return out

    mandanten = [MAND_KOPF] + [[f"{m:03d}", f"M{m:02d} - Beispiel-Mandant {m}", "2026", "12"]
                               for m in range(1, 51)]
    mandanten += [[f"{m:03d}", f"M{m:02d} - Beispiel-Mandant {m}", "2026", "12"]
                  for m in (3, 7)]            # doppelt
    post = [POST_KOPF]
    for m in range(1, 51):
        post.append([f"{m:03d}" if m % 5 else f"{m + 100:03d}",   # jede 5.: MDT fehlt
                     f"postfach{m}@example.com", f"pw-{m}", f"M{m:02d} - Beispiel-Mandant {m}",
                     f"Beispiel-Mandant {m} GmbH"])
    post += [list(post[1]), list(post[2])]    # exakt doppelte Postfaecher
    return {"Kreditoren Deutschland": mit_kopfzeilen(de),
            "Kreditoren Schweiz": mit_kopfzeilen(ch),
            "Mandanten": mandanten, "Postfächer": post}


def sheets_aus_xlsx(daten: bytes) -> dict:
    out = {}
    for name, rows in read_workbook(daten).items():
        headers, rest = header_and_rows(rows)
        mapping = si.spalten_vorschlag(headers)
        out[name] = {"headers": headers, "rows": rest, "mapping": mapping,
                     "art": si.art_vorschlag(name, mapping)}
    return out


def messen(zeilen: int = 3000, seed: int = 1, xlsx_out: str | None = None,
           bibliothek: int = 0, ausgabe=print) -> dict:
    """``bibliothek``: so viele (nicht zur Liste passende) Lieferanten vorab in
    die Bibliothek legen -- misst die Pruefung gegen einen gefuellten Bestand."""
    daten = write_workbook(erzeuge(zeilen, seed))
    if xlsx_out:
        Path(xlsx_out).write_bytes(daten)
    sheets = sheets_aus_xlsx(daten)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "data")
        if bibliothek:
            from rechnung.identify import Supplier
            for i in range(bibliothek):
                store.save_supplier(Supplier(
                    id=f"alt-{i}", name=f"Altbestand {i} GmbH",
                    ustids=[f"DE{300000000 + i}"], ibans=[f"DE{i:020d}"]))
        t0 = time.perf_counter()
        vor = si.vorschau(store, sheets)
        dauer = time.perf_counter() - t0
        gruppen = vor["gruppen"]
        t1 = time.perf_counter()
        if gruppen:
            si.sammelantwort(store, sheets, {}, gruppen[0]["id"], gruppen[0]["sammelfaehig"][0])
        dauer_gruppe = time.perf_counter() - t1
    zeilen_gesamt = sum(len(b["zeilen"]) for b in vor["blaetter"].values())
    fragen = sum(g["anzahl"] for g in gruppen)
    ergebnis = {"zeilen": zeilen_gesamt, "fragen": fragen, "gruppen": gruppen,
                "zusammenfassung": vor["zusammenfassung"], "vorschau_s": dauer,
                "gruppen_antwort_s": dauer_gruppe}
    ausgabe(f"Zeilen gesamt: {zeilen_gesamt}")
    ausgabe(f"Fragen gesamt: {fragen}   (Gruppen: {len(gruppen)})")
    for g in gruppen[:10]:
        ausgabe(f"  {g['anzahl']:6d}  {g['art']:12s} {g['feld']:14s} {g['muster'][:70]}")
    ausgabe("Zusammenfassung: " + ", ".join(f"{k}={v}" for k, v in vor["zusammenfassung"].items()
                                           if not isinstance(v, (list, dict))))
    ausgabe(f"Laufzeit vorschau(): {dauer:.2f} s   Sammel-Antwort: {dauer_gruppe:.2f} s")
    return ergebnis


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--zeilen", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--xlsx-out")
    ap.add_argument("--bibliothek", type=int, default=0)
    a = ap.parse_args()
    messen(a.zeilen, a.seed, a.xlsx_out, a.bibliothek)
