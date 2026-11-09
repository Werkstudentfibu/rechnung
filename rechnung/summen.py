"""Beträge aus dem Rechnungskontext herleiten -- deterministisch, keine KI
(Teil A, Befund Aaron 09.10.2026).

Das kleine lokale Modell (qwen2.5:1.5b, CPU) liest Zahlen aus einer
Rechnung, versteht aber nicht zuverlässig, WELCHER Betrag Brutto/Netto ist,
wenn es nicht wörtlich dasteht (z.B. "Total inkl. MWST" ohne separates
"Netto"-Label). Dieses Modul sammelt alle Geldbeträge im Volltext mit ihrem
Kontext (Wörter davor/danach) und sucht ein rechnerisch stimmiges Tripel
(netto, ust, brutto) bzw. Steuerzeilen je Satz -- ganz ohne Modell, nur über
Formel + Schlüsselwörter + Lage im Text. Gewinnt (in ``rechnen.rechne``)
gegen gelesene Werte, wenn die nicht aufgehen oder fehlen; eine menschliche
Korrektur und der Schweizer QR-Zahlteil-Betrag bleiben davon unberührt
(siehe ``rechnen._uebernehme_summen``).

Wie bei jeder Stelle in diesem Programm gilt: kein Raten. Nur ein
EINDEUTIGER Sieger (ausreichender Abstand zum zweitbesten Kandidaten) wird
übernommen -- sonst bleibt ``SummenErgebnis`` leer und ``hinweis`` nennt den
Grund für den Menschen."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .identify import DEFAULT_CURRENCY, nearest_valid_rate
from .textnorm import ch_number, norm, parse_amount, parse_tax_summary

# Geldbetrag-Token: braucht (wie textnorm._AMOUNT_TOKEN_RE) zwingend zwei
# Nachkommastellen -- sonst waere jede Rechnungsnummer/PLZ ein "Betrag".
_AMOUNT_TOKEN_RE = re.compile(
    r"-?\d{1,3}(?:[.\s]\d{3})*,\d{2}|-?\d+,\d{2}|-?\d{1,3}(?:,\d{3})*\.\d{2}|-?\d+\.\d{2}"
)
_PERCENT_RE = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%")

# Kontextfenster um einen Betrag (in Wort-Token): Labels stehen in deutschen/
# Schweizer Rechnungen meist DAVOR ("Rechnungsbetrag: 1.190,00 EUR", "Total
# inkl. MWST 8.1% CHF 1'081.35") -- deshalb vorne grosszuegiger als hinten.
_BEFORE, _AFTER = 8, 3

# Schluesselwoerter je Rolle (de/en/fr/it), normalisiert ueber textnorm.norm()
# -- SPEC Teil A. "stark" entscheidet allein; "schwach" (Zwischensumme) nur,
# wenn nichts Staerkeres als Netto gefunden wird (z.B. keine Rabattzeile).
_BRUTTO_WORDS = {
    "total", "totale", "totalimporto", "gesamtbetrag", "gesamtsumme",
    "rechnungsbetrag", "endbetrag", "zahlbetrag", "zahlbar", "zahlen",
    "bruttobetrag", "brutto", "amountdue", "totaldue", "grandtotal",
    "balancedue", "montanttotal", "montantatpayer", "ttc", "inkl", "inklusive",
}
_NETTO_WORDS = {
    "netto", "nettobetrag", "warenwert", "subtotal", "subtotale",
    "exkl", "exklusive", "imponibile", "netamount", "soustotal",
}
_NETTO_WORDS_SCHWACH = {"zwischensumme"}
_UST_WORDS = {
    "mwst", "ust", "umsatzsteuer", "mehrwertsteuer", "vat", "tva", "iva",
    "steuer", "steuerbetrag", "steuerbetraege", "tax",
}
# Zeilen, die NIE als Netto/Brutto/USt gelten duerfen, auch wenn sie
# zufaellig in der Naehe eines anderen Schluesselworts stehen (Rabatt/
# Versand/Skonto duerfen nicht mit dem Nettobetrag verwechselt werden).
_DISTRAKTOR_WORDS = {
    "rabatt", "nachlass", "skonto", "versand", "fracht", "porto",
    "verpackung", "discount", "shipping", "gutschrift",
}

_TRIPLE_TOL = 0.03   # Rundungstoleranz brutto = netto + ust
_MARGIN = 0.35       # Mindestabstand Score Sieger vs. Zweitbester


@dataclass
class SummenErgebnis:
    netto: float | None = None
    ust_betrag: float | None = None
    brutto: float | None = None
    ust_satz: float | None = None             # nur bei genau einem Satz
    steuerzeilen: list[dict] = field(default_factory=list)  # bei Mischsaetzen
    begruendung: str = ""
    quelle: str = "summen"
    hinweis: str = ""                         # Grund, falls NICHTS gesetzt wurde

    @property
    def gefunden(self) -> bool:
        return self.netto is not None or self.brutto is not None


@dataclass
class _Betrag:
    idx: int
    value: float
    frac: float
    sc_brutto: float
    sc_netto: float
    sc_ust: float
    satz_kontext: float | None


@dataclass
class _Kandidat:
    netto: float | None
    ust: float | None
    brutto: float | None
    satz: float | None
    zeilen: list[dict]
    score: float
    begruendung: str


def _role_score(window: list[tuple[int, str]], idx: int, words: set[str], weight: float) -> float:
    best = 0.0
    for j, w in window:
        if w in words:
            dist = abs(j - idx)
            best = max(best, weight * max(0.25, 1 - 0.07 * dist))
    return best


def _satz_im_kontext(snippet: str, currency: str | None) -> float | None:
    """Genau EIN gueltiger Steuersatz als Prozentzahl im Kontextfenster
    (z.B. "Total inkl. MWST 8.1%") -- bei mehreren verschiedenen gueltigen
    Saetzen im selben Fenster lieber ``None`` als zu raten, welcher gemeint
    ist."""
    rates = set()
    for m in _PERCENT_RE.finditer(snippet):
        try:
            r = round(float(m.group(1).replace(",", ".")), 2)
        except ValueError:
            continue
        valid = nearest_valid_rate(r, currency)
        if valid is not None:
            rates.add(valid)
    return next(iter(rates)) if len(rates) == 1 else None


def _sammle_betraege(text: str, currency: str | None) -> list[_Betrag]:
    toks = text.split()
    n = len(toks)
    out: list[_Betrag] = []
    for i, raw in enumerate(toks):
        conv = ch_number(raw).strip("()[]:;,")
        if not _AMOUNT_TOKEN_RE.fullmatch(conv):
            continue
        val = parse_amount(conv)
        if val is None:
            continue
        lo, hi = max(0, i - _BEFORE), min(n, i + _AFTER + 1)
        window = [(j, norm(toks[j])) for j in range(lo, hi) if j != i]
        is_distraktor = _role_score(window, i, _DISTRAKTOR_WORDS, 1.0) > 0
        sc_b = 0.0 if is_distraktor else _role_score(window, i, _BRUTTO_WORDS, 1.0)
        sc_n = 0.0 if is_distraktor else max(
            _role_score(window, i, _NETTO_WORDS, 1.0),
            _role_score(window, i, _NETTO_WORDS_SCHWACH, 0.4))
        sc_u = 0.0 if is_distraktor else _role_score(window, i, _UST_WORDS, 1.0)
        satz = _satz_im_kontext(" ".join(toks[lo:hi]), currency)
        out.append(_Betrag(i, val, i / max(1, n - 1), sc_b, sc_n, sc_u, satz))
    return out


def _satz_aus_paar(ust: float, netto: float, currency: str | None) -> float | None:
    if not netto:
        return None
    return nearest_valid_rate(round(ust / netto * 100, 2), currency)


def _qr_bonus(value: float, qr_brutto: float | None) -> float:
    return 1.5 if qr_brutto is not None and abs(value - qr_brutto) <= 0.02 else 0.0


def _pos_bonus(b: _Betrag) -> float:
    # Endbetrag steht meist zuletzt/unten -- ein spaeter im Text gefundener
    # Brutto-Kandidat ist etwas plausibler.
    return 0.2 * b.frac


def _kandidaten_einzelsatz(betraege: list[_Betrag], currency: str | None,
                           qr_brutto: float | None) -> list[_Kandidat]:
    brutto_c = [b for b in betraege if b.sc_brutto > 0]
    netto_c = [b for b in betraege if b.sc_netto > 0]
    ust_c = [b for b in betraege if b.sc_ust > 0]
    out: list[_Kandidat] = []

    # (a) volle Dreiergruppe: netto + ust = brutto, alle drei im Text belegt.
    dreiergruppen: set[tuple[int, int, int]] = set()
    for n in netto_c:
        for u in ust_c:
            r = _satz_aus_paar(u.value, n.value, currency)
            if r is None:
                continue
            erwartet = round(n.value + u.value, 2)
            for b in brutto_c:
                if abs(b.value - erwartet) > _TRIPLE_TOL:
                    continue
                score = n.sc_netto + u.sc_ust + b.sc_brutto + _pos_bonus(b) + _qr_bonus(b.value, qr_brutto)
                out.append(_Kandidat(n.value, u.value, b.value, r,
                                     [{"satz": r, "netto": n.value, "ust": u.value}], score,
                                     f"{n.value:.2f} + {u.value:.2f} = {b.value:.2f} ({r:g} %)"))
                dreiergruppen.add((n.idx, u.idx, b.idx))

    # (b) Paar -> drittes ableiten (nur wenn nicht schon als Dreiergruppe erfasst)
    paare_in_dreiergruppe = {(a, c) for a, c, _ in dreiergruppen}
    for n in netto_c:
        for u in ust_c:
            r = _satz_aus_paar(u.value, n.value, currency)
            if r is None:
                continue
            if (n.idx, u.idx) in paare_in_dreiergruppe:
                continue
            b = round(n.value + u.value, 2)
            score = n.sc_netto + u.sc_ust + _qr_bonus(b, qr_brutto) - 0.1
            out.append(_Kandidat(n.value, u.value, b, r,
                                 [{"satz": r, "netto": n.value, "ust": u.value}], score,
                                 f"{n.value:.2f} + {u.value:.2f} ({r:g} %, Brutto nicht separat gefunden)"))
    for n in netto_c:
        for b in brutto_c:
            u = round(b.value - n.value, 2)
            if u < 0:
                continue
            r = _satz_aus_paar(u, n.value, currency)
            if r is None:
                continue
            score = n.sc_netto + b.sc_brutto + _pos_bonus(b) + _qr_bonus(b.value, qr_brutto) - 0.1
            out.append(_Kandidat(n.value, u, b.value, r,
                                 [{"satz": r, "netto": n.value, "ust": u}], score,
                                 f"{b.value:.2f} - {n.value:.2f} ({r:g} %, Steuer nicht separat gefunden)"))
    for u in ust_c:
        for b in brutto_c:
            n = round(b.value - u.value, 2)
            if n <= 0:
                continue
            r = _satz_aus_paar(u.value, n, currency)
            if r is None:
                continue
            score = u.sc_ust + b.sc_brutto + _pos_bonus(b) + _qr_bonus(b.value, qr_brutto) - 0.1
            out.append(_Kandidat(n, u.value, b.value, r,
                                 [{"satz": r, "netto": n, "ust": u.value}], score,
                                 f"{b.value:.2f} - {u.value:.2f} ({r:g} %, Netto nicht separat gefunden)"))

    # (c) einzelner Brutto-Betrag mit Satz im eigenen Kontext ("Total inkl. MWST 8.1%")
    for b in brutto_c:
        if b.satz_kontext is None:
            continue
        r = b.satz_kontext
        netto = round(b.value / (1 + r / 100), 2) if r else b.value
        ust = round(b.value - netto, 2)
        score = b.sc_brutto + 0.5 + _pos_bonus(b) + _qr_bonus(b.value, qr_brutto)
        out.append(_Kandidat(netto, ust, b.value, r,
                             [{"satz": r, "netto": netto, "ust": ust}], score,
                             f"{b.value:.2f} / {1 + r / 100:g} ({r:g} % im Kontext, Satz im Text gefunden)"))

    # (d) ohne Steuer: Netto- und Brutto-Label auf demselben Betrag, keine USt-Zeile im Text
    if not ust_c:
        for b in brutto_c:
            for n in netto_c:
                if n.idx == b.idx or abs(n.value - b.value) > 0.005:
                    continue
                score = n.sc_netto + b.sc_brutto + _pos_bonus(b) + _qr_bonus(b.value, qr_brutto) - 0.05
                out.append(_Kandidat(n.value, 0.0, b.value, 0.0,
                                     [{"satz": 0.0, "netto": n.value, "ust": 0.0}], score,
                                     f"{b.value:.2f} ohne Steuer (0 %, kein Steuerbetrag im Text)"))
    return out


def _kandidat_mischsaetze(text: str, currency: str | None,
                          qr_brutto: float | None) -> _Kandidat | None:
    """Mehrere Steuersaetze (z.B. 7 % und 19 % auf derselben Rechnung) --
    erkannt ueber ``textnorm.parse_tax_summary`` (dieselbe Logik wie beim
    Anlernen des USt-Aufstellungs-Blocks, ``learn.learn_taxblock``), nur je
    gueltigem Satz behalten und validiert (ust = netto * satz). Entscheidung
    Aaron 10.10.2026: verschiedene Saetze werden NIE zusammengefasst --
    deshalb bleiben es mehrere Zeilen, auch im Ergebnis."""
    valid: dict[float, dict] = {}
    for row in parse_tax_summary(text):
        satz, netto, ust = row.get("satz"), row.get("netto"), row.get("ust")
        if satz is None or netto is None or ust is None or netto <= 0:
            continue
        r = nearest_valid_rate(satz, currency)
        if r is None:
            continue
        if abs(netto * r / 100 - ust) > max(0.05, netto * 0.01):
            continue
        # je Satz nur eine Zeile -- die zuerst im Text gefundene gewinnt
        valid.setdefault(r, {"satz": r, "netto": round(netto, 2), "ust": round(ust, 2)})
    if len(valid) < 2:
        return None
    zeilen = [valid[k] for k in sorted(valid)]
    netto_sum = round(sum(z["netto"] for z in zeilen), 2)
    ust_sum = round(sum(z["ust"] for z in zeilen), 2)
    brutto_sum = round(netto_sum + ust_sum, 2)
    score = 1.2 + _qr_bonus(brutto_sum, qr_brutto)
    begr = " + ".join(f"{z['netto']:.2f}@{z['satz']:g}%" for z in zeilen)
    return _Kandidat(netto_sum, ust_sum, brutto_sum, None, zeilen, score,
                     f"Mischsätze im Text gefunden: {begr} = {brutto_sum:.2f}")


def _gleichwertig(a: _Kandidat, b: _Kandidat) -> bool:
    """Zwei Kandidaten mit nahe beieinander liegendem Score sind kein
    Widerspruch, wenn sie (z.B. per Dreiergruppe bzw. per abgeleitetem
    Paar) auf dasselbe Ergebnis kommen -- dann gewinnt der Beste, statt
    faelschlich 'mehrdeutig' zu melden."""
    def close(x, y):
        return x is not None and y is not None and abs(x - y) <= 0.02
    return close(a.brutto, b.brutto) and close(a.netto, b.netto) and close(a.ust, b.ust)


def summen_aus_kontext(text: str, currency: str | None = None,
                       qr_brutto: float | None = None) -> SummenErgebnis:
    """Brutto/Netto/Steuer (ggf. je Satz) aus dem Rechnungsvolltext herleiten
    -- ohne KI, nur Formel + Schlüsselwörter + Lage im Text. ``qr_brutto``:
    Betrag aus dem Schweizer QR-Zahlteil, falls vorhanden -- stärkstes
    Signal (SPEC Teil A), entscheidet aber nur MIT, ersetzt die eigentliche
    Pruefung nicht (die bleibt in ``rechnen.py``, wo der QR-Betrag ohnehin
    Vorrang hat). Nur bei einem eindeutigen Sieger wird etwas gesetzt --
    sonst bleibt ``SummenErgebnis`` leer und ``hinweis`` nennt den Grund."""
    if not text or not text.strip():
        return SummenErgebnis(hinweis="kein Rechnungstext")
    currency = currency or DEFAULT_CURRENCY
    betraege = _sammle_betraege(text, currency)
    kandidaten = _kandidaten_einzelsatz(betraege, currency, qr_brutto)
    misch = _kandidat_mischsaetze(text, currency, qr_brutto)
    if misch is not None:
        # Einzelsatz-Kandidaten, die nur eine der Steuerzeilen der
        # Mischsatz-Loesung sind (Bruchstueck, kein eigenstaendiges
        # Rechnungs-Tripel), scheiden aus -- sonst "gewinnt" faelschlich
        # "mehrdeutig" zwischen zwei Bruchstuecken derselben Rechnung.
        zeilen_paare = {(z["netto"], z["ust"]) for z in misch.zeilen}
        kandidaten = [k for k in kandidaten if (k.netto, k.ust) not in zeilen_paare]
        kandidaten.append(misch)
    kandidaten = [k for k in kandidaten if k.score > 0]
    if not kandidaten:
        return SummenErgebnis(hinweis="keine eindeutigen Beträge im Kontext gefunden")
    kandidaten.sort(key=lambda k: k.score, reverse=True)
    best = kandidaten[0]
    if len(kandidaten) > 1:
        zweiter = kandidaten[1]
        if best.score - zweiter.score < _MARGIN and not _gleichwertig(best, zweiter):
            return SummenErgebnis(hinweis=(
                f"mehrdeutig: {best.brutto} (Score {best.score:.2f}) vs. "
                f"{zweiter.brutto} (Score {zweiter.score:.2f}) -- nichts übernommen"))
    satz = best.satz if len(best.zeilen) <= 1 else None
    return SummenErgebnis(
        netto=best.netto, ust_betrag=best.ust, brutto=best.brutto, ust_satz=satz,
        steuerzeilen=best.zeilen if len(best.zeilen) > 1 else [],
        begruendung=best.begruendung)
