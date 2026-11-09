"""Rechenstufe: nachrechnen UND ergaenzen -- deterministische Formeln, keine
KI (SPEC Baustein 2 "Selbstlernen 2").

Jede Zelle (Kopf-Betrag, Positionszeile, USt-Aufstellungszeile) bekommt eine
Herkunft:

- ``gelesen``    im PDF/XML belegt (Vorlage/KI/E-Rechnung/Volltext-Regel)
- ``errechnet``  aus GELESENEN Werten hergeleitet -- nie aus einem bereits
                 errechneten Wert (keine Kette errechnet -> errechnet)
- ``kopf``       aus dem Rechnungskopf auf jede Position uebertragen
                 (Waehrung, einziger Steuersatz)
- ``korrigiert`` vom Menschen eingetragen (``FieldResult.source == "manual"``)

Ablauf von ``rechne()``:

0. Teil A (Befund Aaron 09.10.2026, Modul ``summen.py``): gehen die
   GELESENEN Kopf-Betraege nicht auf (oder fehlen sie), wird ein
   eindeutiges Tripel aus dem Rechnungsvolltext hergeleitet (Formel +
   Schluesselwoerter + Lage im Text, keine KI) und uebernimmt die
   betroffenen Felder als ``errechnet`` -- eine menschliche Korrektur und
   der Schweizer QR-Zahlteil-Betrag bleiben davon unberuehrt.
1. Kopf ergaenzen (netto/ust_betrag/brutto/ust_satz aus je zwei der vier,
   nur wenn der Satz fuer die Waehrung gueltig ist).
2. USt-Aufstellung aus dem (jetzt vollstaendigeren) Kopf/den Positionen
   bauen (``tax_breakdown``, bisher ``pipeline._tax_breakdown``).
3. Positionen ergaenzen: Satz/Waehrung aus dem Kopf uebernehmen (``kopf``),
   Bruttopreis-Positionen entrechnen, Menge*Einzelpreis<->Zeilensumme.
4. USt-Aufstellung danach erneut bauen (jetzt mit vervollstaendigten
   Positionen) und je Zeile Netto/USt ergaenzen.

Nachgerechnet wird NICHT hier -- das macht ``validate.run_all``/
``rechen_pruefungen`` wie bisher (Baustein 9), nur auf dem jetzt
vollstaendigeren Stand. Ist ein Wert nicht eindeutig herleitbar (z.B.
Mischsaetze ohne Aufteilung), bleibt die Zelle leer und der Grund landet in
``RechenResult.gruende`` -- kein Raten."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import summen
from .identify import DEFAULT_CURRENCY, EXPECTED_RATES
from .identify import nearest_valid_rate as _identify_nearest_valid_rate
from .template import FieldResult
from .textnorm import coerce
from .validate import looks_like_brutto_prices

GELESEN = "gelesen"
ERRECHNET = "errechnet"
KOPF = "kopf"
KORRIGIERT = "korrigiert"

_RATE_TOL = 0.5  # Prozentpunkte -- Rundung beim Rueckrechnen eines Satzes


def herkunft(source: str) -> str:
    """Vierstufige Herkunft (siehe Modul-Docstring) zu einer Feldquelle
    (``FieldResult.source``) -- fuer die Anzeige (UI/CSV/JobRouter,
    Baustein 3). Alles, was nicht ``manual``/``errechnet``/``kopf`` ist
    (Vorlage, KI, E-Rechnung, Textregel, Stammdaten), gilt als ``gelesen``."""
    if source == "manual":
        return KORRIGIERT
    if source in (ERRECHNET, KOPF):
        return source
    return GELESEN


def _to_amount(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    return coerce(str(v or ""), "amount")[0]


def _num(it: dict, key: str) -> float | None:
    v = it.get(key)
    if v is None or v == "":
        return None
    return _to_amount(v)


def _nearest_valid_rate(satz: float, currency: str | None) -> float | None:
    """Naechster gueltiger Satz der Waehrung, wenn ``satz`` (z.B. aus
    USt/Netto zurueckgerechnet) innerhalb der Rundungstoleranz liegt --
    sonst ``None`` (kein gueltiger Satz, nicht raten). Gemeinsame Grundlage
    mit ``summen.py`` (Teil A) steht jetzt in ``identify.nearest_valid_rate``."""
    return _identify_nearest_valid_rate(satz, currency, _RATE_TOL)


def tax_breakdown(fields: dict, line_items: list[dict] | None) -> list[dict]:
    """USt-Aufstellung je Steuersatz. Prioritaet:
    1. explizit markierter ``ust_aufstellung``-Block
    2. aus den Positionen aufsummiert (Standardspalten ``mwst_satz``/
       ``gesamt_netto``, SPEC Baustein 1)
    3. Einzelsatz aus den Kopf-Feldern netto/ust_betrag/ust_satz
    """
    fr = fields.get("ust_aufstellung")
    if fr is not None and isinstance(fr.value, list) and fr.value:
        rows = []
        for r in fr.value:
            netto = _to_amount(r.get("netto"))
            ust = _to_amount(r.get("ust"))
            rows.append({"satz": r.get("satz"), "netto": netto, "ust": ust,
                         "brutto": round((netto or 0) + (ust or 0), 2)})
        return rows

    if line_items:
        agg: dict[float, dict] = {}
        for it in line_items:
            rate = _num(it, "mwst_satz")
            base = _num(it, "gesamt_netto")
            if rate is None or base is None:
                continue
            slot = agg.setdefault(rate, {"satz": rate, "netto": 0.0, "ust": 0.0})
            slot["netto"] = round(slot["netto"] + base, 2)
            slot["ust"] = round(slot["ust"] + base * rate / 100, 2)
        rows = [dict(v, brutto=round(v["netto"] + v["ust"], 2)) for v in agg.values()]
        if rows:
            return sorted(rows, key=lambda r: r["satz"])

    netto = _to_amount(fields["netto"].value) if "netto" in fields else None
    ust = _to_amount(fields["ust_betrag"].value) if "ust_betrag" in fields else None
    satz = _to_amount(fields["ust_satz"].value) if "ust_satz" in fields else None
    if netto is not None or ust is not None:
        return [{"satz": satz, "netto": netto, "ust": ust,
                 "brutto": round((netto or 0) + (ust or 0), 2)}]
    return []


@dataclass
class RechenResult:
    fields: dict[str, FieldResult]
    line_items: list[dict]
    tax_breakdown: list[dict]
    ergaenzt: list[str] = field(default_factory=list)   # "netto errechnet (...)" usw.
    gruende: list[str] = field(default_factory=list)    # nicht eindeutig -> Mensch


def _feld(fields: dict, name: str) -> FieldResult | None:
    fr = fields.get(name)
    if fr is None or fr.source == "missing" or fr.value in (None, ""):
        return None
    return fr


def _feldwert(fields: dict, name: str) -> tuple[FieldResult | None, float | None]:
    fr = _feld(fields, name)
    return fr, (_to_amount(fr.value) if fr else None)


def _ergaenze_kopf(fields: dict, currency: str | None, gruende: list[str],
                   ergaenzt: list[str]) -> None:
    """Netto/USt-Betrag/Brutto/USt-Satz aus je zwei der vier GELESENEN
    Kopf-Werte herleiten (SPEC 2b) -- nur wenn ein dabei verwendeter Satz
    fuer die Waehrung gueltig ist (DE 0/7/19, CH 0/2,6/3,8/8,1 inkl. der
    alten CHF-Saetze, siehe identify.EXPECTED_RATES)."""
    netto_fr, netto = _feldwert(fields, "netto")
    _, ust = _feldwert(fields, "ust_betrag")
    _, brutto = _feldwert(fields, "brutto")
    _, satz = _feldwert(fields, "ust_satz")

    def setze(name: str, value: float, formel: str) -> None:
        fields[name] = FieldResult(name, round(value, 2), formel, 1.0, ERRECHNET)
        ergaenzt.append(f"{name} errechnet ({formel})")

    # Netto = Brutto bei Steuer > 0 kann nicht stimmen (Befund Aaron 09.10.2026:
    # Schweizer Rechnung "Total inkl. MWST" -- die KI las den Endbetrag auch als
    # Netto). Der Endbetrag ist das Brutto (bei QR-Rechnungen sogar vom
    # Aussteller strukturiert); Netto wird verworfen und unten aus Brutto und
    # Steuer bzw. Satz neu errechnet. Eine Korrektur des Menschen bleibt.
    steuer = (ust is not None and abs(ust) > 0.005) or (satz is not None and satz > 0)
    if (netto is not None and brutto is not None and abs(netto - brutto) < 0.005
            and netto != 0 and steuer and netto_fr is not None and netto_fr.source != "manual"):
        fields.pop("netto", None)
        netto = None
        if ust is not None and abs(ust) <= 0.005:   # "0,00 Steuer" bei Satz > 0 ebenso falsch
            fields.pop("ust_betrag", None)
            ust = None
        gruende.append("Netto gleich Brutto trotz Steuer -- gelesenes Netto verworfen, "
                       "aus Brutto neu errechnet")
        if ust is not None and abs(ust) > 0.005:
            setze("netto", brutto - ust, f"{brutto:.2f} - {ust:.2f} (Brutto - Steuer)")
            netto = round(brutto - ust, 2)

    if brutto is not None and netto is None and ust is None:
        if satz is not None and _nearest_valid_rate(satz, currency) is not None:
            n = round(brutto / (1 + satz / 100), 2)
            setze("netto", n, f"{brutto:.2f} / {1 + satz / 100:g} (Brutto, {satz:g} %)")
            setze("ust_betrag", round(brutto - n, 2), f"{brutto:.2f} - {n:.2f}")
            netto, ust = n, round(brutto - n, 2)
        elif satz is not None:
            gruende.append(f"USt-Satz {satz:g} % ist in {currency or DEFAULT_CURRENCY} kein "
                           "gueltiger Satz -- Brutto nicht in Netto/USt aufgeteilt")
        else:
            gruende.append("Netto/USt aus Brutto nicht errechnet: kein (eindeutiger) "
                           "USt-Satz bekannt (z.B. Mischsaetze ohne Aufteilung)")

    if netto is not None and ust is not None and brutto is None:
        b = round(netto + ust, 2)
        setze("brutto", b, f"{netto:.2f} + {ust:.2f}")
        brutto = b

    if netto is not None and netto != 0 and ust is not None and satz is None:
        r = _nearest_valid_rate(round(ust / netto * 100, 2), currency)
        if r is not None:
            setze("ust_satz", r, f"{ust:.2f} / {netto:.2f} * 100")
            satz = r

    if netto is not None and satz is not None and ust is None:
        if _nearest_valid_rate(satz, currency) is not None:
            u = round(netto * satz / 100, 2)
            setze("ust_betrag", u, f"{netto:.2f} * {satz:g} %")
            ust = u
            if brutto is None:
                setze("brutto", round(netto + u, 2), f"{netto:.2f} + {u:.2f}")

    if ust is not None and satz is not None and satz != 0 and netto is None:
        if _nearest_valid_rate(satz, currency) is not None:
            n = round(ust / (satz / 100), 2)
            setze("netto", n, f"{ust:.2f} / {satz:g} %")
            netto = n
            if brutto is None:
                setze("brutto", round(n + ust, 2), f"{n:.2f} + {ust:.2f}")


def _item_herkunft(it: dict) -> dict:
    h = it.get("_herkunft")
    if not isinstance(h, dict):
        h = {}
        it["_herkunft"] = h
    return h


def _ergaenze_item(it: dict, currency: str | None, satz_einzig: float | None,
                   brutto_rechnung: bool, gruende: list[str]) -> None:
    h = _item_herkunft(it)

    # kopf: Satz/Waehrung aus dem Kopf uebernehmen, wenn die Zeile selbst
    # keinen hat -- Satz nur, wenn die GANZE Rechnung genau einen Satz kennt.
    if _num(it, "mwst_satz") is None and satz_einzig is not None:
        it["mwst_satz"] = satz_einzig
        h["mwst_satz"] = KOPF
    if not it.get("waehrung") and currency:
        it["waehrung"] = currency
        h["waehrung"] = KOPF

    satz = _num(it, "mwst_satz")

    # Bruttopreis-Zeile entrechnen: die bisher gelesenen Werte in
    # einzelpreis_netto/gesamt_netto sind tatsaechlich Bruttobetraege.
    if brutto_rechnung and satz is not None and _nearest_valid_rate(satz, currency) is not None:
        for key in ("einzelpreis_netto", "gesamt_netto"):
            if h.get(key) == ERRECHNET:
                continue
            brutto_val = _num(it, key)
            if brutto_val is None:
                continue
            it[key] = round(brutto_val / (1 + satz / 100), 2)
            h[key] = ERRECHNET

    menge, preis, zeile = _num(it, "menge"), _num(it, "einzelpreis_netto"), _num(it, "gesamt_netto")
    if menge and preis is not None and zeile is None:
        it["gesamt_netto"] = round(menge * preis, 2)
        h["gesamt_netto"] = ERRECHNET
    elif menge and zeile is not None and preis is None:
        it["einzelpreis_netto"] = round(zeile / menge, 2)
        h["einzelpreis_netto"] = ERRECHNET
    elif zeile is not None and menge is None and preis is None:
        pass  # Pauschale -- nichts zu ergaenzen, validate.py beanstandet das nicht

    if not h:
        it.pop("_herkunft", None)


def _single_rate(fields: dict, line_items: list[dict]) -> float | None:
    """Der EINE Steuersatz dieser Rechnung, wenn Kopf UND alle Positionen,
    die ueberhaupt einen Satz tragen, sich auf genau einen einigen --
    Mischsaetze (z.B. 7 % und 19 % in verschiedenen Zeilen) geben ``None``,
    damit nichts geraten wird."""
    rates: set[float] = set()
    _, head = _feldwert(fields, "ust_satz")
    if head is not None:
        rates.add(round(head, 2))
    for it in line_items:
        r = _num(it, "mwst_satz")
        if r is not None:
            rates.add(round(r, 2))
    return next(iter(rates)) if len(rates) == 1 else None


def _ergaenze_positionen(line_items: list[dict], fields: dict, currency: str | None,
                         text: str, gruende: list[str]) -> None:
    if not line_items:
        return
    satz_einzig = _single_rate(fields, line_items)
    brutto_rechnung = looks_like_brutto_prices(text)
    fehlt_satz = [i for i, it in enumerate(line_items, 1) if _num(it, "mwst_satz") is None]
    if fehlt_satz and satz_einzig is None:
        gruende.append("USt-Satz je Position nicht eindeutig (Mischsaetze ohne Aufteilung) "
                       f"-- Position(en) {', '.join(str(i) for i in fehlt_satz)} ohne Satz")
    for it in line_items:
        _ergaenze_item(it, currency, satz_einzig, brutto_rechnung, gruende)


def _ergaenze_aufstellung(rows: list[dict], currency: str | None, gruende: list[str]) -> None:
    for row in rows:
        if not isinstance(row, dict):
            continue
        h: dict = {}
        satz = row.get("satz")
        if satz is not None:
            satz = _to_amount(satz)
        if satz is not None and _nearest_valid_rate(satz, currency) is not None:
            netto, ust = row.get("netto"), row.get("ust")
            if netto is not None and ust is None:
                row["ust"] = round(netto * satz / 100, 2)
                row["brutto"] = round(netto + row["ust"], 2)
                h["ust"] = ERRECHNET
            elif ust is not None and netto is None and satz:
                row["netto"] = round(ust / (satz / 100), 2)
                row["brutto"] = round(row["netto"] + ust, 2)
                h["netto"] = ERRECHNET
        if h:
            row["_herkunft"] = h


# Quellen, die ein Summen-Tripel (Teil A) nie ueberschreiben darf: eine
# menschliche Korrektur gewinnt immer, der Schweizer QR-Zahlteil-Betrag
# bleibt maassgeblich (SPEC Teil A, Entscheidung Aaron 10.10.2026).
_SUMMEN_GESPERRTE_QUELLEN = ("manual", "qr")

# Konfidenz eines aus dem Kontext hergeleiteten Betrags -- wie ein normaler
# Vorschlag (gelb/"Vorschlag" im UI), nicht so hoch wie ein tatsaechlich
# GELESENER Wert.
_SUMMEN_KONFIDENZ = 0.75


def _kopf_konsistent(fields: dict, currency: str | None) -> bool:
    """Gehen die GELESENEN (bzw. manuell korrigierten) Kopf-Betraege schon
    in sich auf -- Brutto = Netto + Steuer, ein gueltiger Satz, nicht
    "Netto gleich Brutto trotz Steuer"? Nur wenn das NICHT der Fall ist,
    kommt ``summen.py`` (Teil A) zum Zug -- Vorlagen-/KI-Werte, die aufgehen,
    bleiben unangetastet.

    Reicht schon EIN Betrag (z.B. nur Brutto gelesen) zusammen mit einem
    gueltigen Satz, kann ``_ergaenze_kopf`` selbst das Fehlende errechnen
    (Formel, kein Kontext-Scan noetig) -- dann gilt der Kopf ebenfalls schon
    als konsistent, ``summen.py`` wird nicht erst bemueht (sonst wuerde ein
    per Formel bereits ableitbarer Wert zusaetzlich -- und u.U. abweichend
    -- aus dem Fliesstext gesucht, Befund 10.10.2026 beim Anlernen)."""
    _, netto = _feldwert(fields, "netto")
    _, ust = _feldwert(fields, "ust_betrag")
    _, brutto = _feldwert(fields, "brutto")
    _, satz = _feldwert(fields, "ust_satz")
    anzahl = sum(x is not None for x in (netto, ust, brutto))
    if satz is not None and _nearest_valid_rate(satz, currency) is None:
        return False
    satz_gueltig = satz is not None  # bereits oben auf Gueltigkeit geprueft
    if anzahl == 0:
        return False
    if anzahl == 1 and not satz_gueltig:
        return False
    if netto is not None and ust is not None and brutto is not None:
        if abs(netto + ust - brutto) > 0.02:
            return False
    steuer = (ust is not None and abs(ust) > 0.005) or (satz is not None and satz > 0)
    if (netto is not None and brutto is not None and abs(netto - brutto) < 0.005
            and netto != 0 and steuer):
        return False
    return True


def _qr_hinweis_betrag(fields: dict) -> float | None:
    """Brutto aus dem Schweizer QR-Zahlteil (swissqr.apply, Quelle "qr"),
    falls vorhanden -- staerkstes Signal fuer ``summen.summen_aus_kontext``
    (SPEC Teil A). Wird NIE ueberschrieben (siehe
    ``_SUMMEN_GESPERRTE_QUELLEN``), hilft aber, den richtigen Kandidaten im
    Fliesstext zu erkennen."""
    fr = fields.get("brutto")
    if fr is not None and fr.source == "qr" and isinstance(fr.value, (int, float)):
        return float(fr.value)
    return None


def _uebernehme_summen(fields: dict, erg: "summen.SummenErgebnis",
                       gruende: list[str], ergaenzt: list[str]) -> None:
    """Ein eindeutiges Summen-Tripel (Teil A) in die Kopf-Felder eintragen --
    nie eine gesperrte Quelle (manuell/QR) ueberschreiben, UND ein bereits
    gelesenes Feld nur ersetzen, wenn es nachweislich WIDERSPRUECHLICH ist
    (Netto=Brutto trotz Steuer, oder alle drei Betraege zusammen gehen
    rechnerisch nicht auf) -- fehlt nur ein GESCHWISTERFELD (z.B. kennt eine
    Vorlage bislang nur Netto), bleibt das vorhandene, korrekte Feld
    unangetastet und summen.py füllt ausschliesslich die Luecke (Befund
    10.10.2026 beim Anlernen: sonst verliert ein bereits korrektes Feld
    seine Herkunft "gelesen" an ein zufaellig textlich passendes
    "errechnet"). Mischsaetze gehen als ``ust_aufstellung``-Block ein
    (Prioritaet 1 in ``tax_breakdown``)."""
    _, netto = _feldwert(fields, "netto")
    _, ust = _feldwert(fields, "ust_betrag")
    _, brutto = _feldwert(fields, "brutto")
    _, satz = _feldwert(fields, "ust_satz")
    steuer = (ust is not None and abs(ust) > 0.005) or (satz is not None and satz > 0)
    widerspruch = (
        (netto is not None and brutto is not None and abs(netto - brutto) < 0.005
         and netto != 0 and steuer)
        or (netto is not None and ust is not None and brutto is not None
            and abs(netto + ust - brutto) > 0.02))

    def gesperrt(name: str) -> bool:
        fr = fields.get(name)
        return (fr is not None and fr.source in _SUMMEN_GESPERRTE_QUELLEN
                and fr.value not in (None, ""))

    def vorhanden(name: str) -> bool:
        fr = fields.get(name)
        return fr is not None and fr.source != "missing" and fr.value not in (None, "")

    def setze(name: str, value) -> None:
        if value is None or gesperrt(name):
            return
        if vorhanden(name) and not widerspruch:
            return
        fields[name] = FieldResult(name, value, erg.begruendung, _SUMMEN_KONFIDENZ, ERRECHNET)

    setze("netto", erg.netto)
    setze("ust_betrag", erg.ust_betrag)
    setze("brutto", erg.brutto)
    setze("ust_satz", erg.ust_satz)
    if erg.steuerzeilen:
        setze("ust_aufstellung", erg.steuerzeilen)
    ergaenzt.append(f"Beträge aus dem Rechnungskontext hergeleitet ({erg.begruendung}, Quelle: summen)")


def _summen_aus_kontext(fields: dict, currency: str | None, text: str,
                        gruende: list[str], ergaenzt: list[str]) -> None:
    """Teil A: gehen die gelesenen Kopf-Betraege nicht auf (oder fehlen sie),
    sucht ``summen.py`` ein eindeutiges Tripel im Rechnungstext und gewinnt
    gegen die gelesenen Werte -- siehe Modul-Docstring von ``summen.py`` und
    ``_kopf_konsistent``/``_uebernehme_summen`` oben."""
    if _kopf_konsistent(fields, currency) or not text:
        return
    erg = summen.summen_aus_kontext(text, currency, qr_brutto=_qr_hinweis_betrag(fields))
    if erg.gefunden:
        _uebernehme_summen(fields, erg, gruende, ergaenzt)
    elif erg.hinweis:
        gruende.append(erg.hinweis)


def rechne(fields: dict[str, FieldResult], line_items: list[dict] | None,
          text: str = "") -> RechenResult:
    """Fuehrt Baustein 2 komplett aus (Kopf ergaenzen -> USt-Aufstellung
    bauen -> Positionen ergaenzen -> USt-Aufstellung erneut bauen +
    ergaenzen). Nachrechnen (Checks) bleibt Sache von ``validate.py`` --
    ``fields``/``line_items`` werden hier nur vervollstaendigt, danach ganz
    normal wie bisher geprueft."""
    fields = dict(fields)
    line_items = [dict(it) for it in (line_items or [])]
    gruende: list[str] = []
    ergaenzt: list[str] = []

    waehrung_fr = _feld(fields, "waehrung")
    currency = waehrung_fr.value if waehrung_fr else None

    _summen_aus_kontext(fields, currency, text, gruende, ergaenzt)
    _ergaenze_kopf(fields, currency, gruende, ergaenzt)
    rows = tax_breakdown(fields, line_items)
    _ergaenze_positionen(line_items, fields, currency, text, gruende)
    rows = tax_breakdown(fields, line_items)
    _ergaenze_aufstellung(rows, currency, gruende)

    return RechenResult(fields=fields, line_items=line_items, tax_breakdown=rows,
                        ergaenzt=ergaenzt, gruende=gruende)


def nur_gelesene_werte(rows: list[dict] | None) -> list[dict]:
    """Zellen mit Herkunft ``errechnet``/``kopf`` entfernen, Rest unveraendert
    -- fuer das Anlernen (``learn.learn_line_items``/``learn_taxblock``):
    die Vorlage darf nur gelesene Werte als Anker wiederfinden, nie einen
    Wert, den die Rechenstufe selbst eingetragen hat (SPEC Baustein 2:
    "learn.py darf nur gelesene Werte wiederfinden")."""
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        h = row.get("_herkunft") or {}
        out.append({k: v for k, v in row.items()
                    if k != "_herkunft" and h.get(k) not in (ERRECHNET, KOPF)})
    return out
