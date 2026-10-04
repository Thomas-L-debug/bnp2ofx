#!/usr/bin/env python3
"""
BNP2OFX — mini convertisseur local PDF/TXT → OFX
Pensé pour les relevés BNP Paribas (compte chèque) au format texte.
Aucune donnée n'est envoyée sur Internet.

Enchaînement d'un relevé :
  extract_text()     → texte brut (mémoire, pas de .txt écrit)
  parse_statement()  → opérations / soldes / compte
  generate_ofx()     → fichier .ofx
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from xml.sax.saxutils import escape  # échappe &, <, > dans les libellés OFX

APP_NAME = "BNP2OFX"
APP_VERSION = "1.2"
SUPPORTED_SUFFIXES = {".pdf", ".txt"}
# Sous-dossiers ignorés quand on parcourt un dossier de relevés (évite de
# reconvertir les OFX déjà produits, ou de scanner le build PyInstaller).
SKIP_DIR_NAMES = {
    "ofx",
    "dist",
    "build",
    "__pycache__",
    ".git",
    ".venv",
    "venv",
}

# ---------------------------------------------------------------------------
# Modèle : ce que le parseur construit, avant d'écrire l'OFX
# ---------------------------------------------------------------------------

# 1 centime — tous les montants sont arrondis à 2 décimales (pas de float).
TWOPLACES = Decimal("0.01")


@dataclass
class Transaction:
    """Une ligne d'opération du relevé."""
    date: datetime
    date_valeur: datetime | None
    label: str
    amount: Decimal  # négatif = débit (sortie), positif = crédit (entrée)
    raw_lines: list[str] = field(default_factory=list)  # lignes d'origine, pour debug


@dataclass
class Statement:
    """Un relevé entier une fois interprété."""
    account_id: str = "00000000000"
    bank_id: str = "30004"  # 30004 = code banque BNP, si l'IBAN n'est pas lu
    currency: str = "EUR"
    date_start: datetime | None = None  # plus petite date d'opération
    date_end: datetime | None = None    # plus grande date d'opération
    opening: Decimal | None = None      # ancien solde imprimé
    closing: Decimal | None = None      # nouveau solde imprimé
    transactions: list[Transaction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class FileResult:
    """Compte-rendu d'un fichier (mode dossier : un résultat par PDF)."""
    source: Path
    dest: Path | None = None
    n_ops: int = 0
    account_id: str = ""
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True seulement si un OFX a bien été écrit."""
        return self.error is None and self.dest is not None


# ---------------------------------------------------------------------------
# Utilitaires : reconnaître dates, montants, libellés dans le texte brut
# ---------------------------------------------------------------------------

# Date française avec année : 02/05/2026, 02.05.26, 02-05-2026.
DATE_RE = re.compile(r"\b(\d{2})[./-](\d{2})[./-](\d{2,4})\b")
# Date en tête de ligne, année optionnelle (pdftotext BNP : « 03.08 »).
LINE_START_DATE_RE = re.compile(r"^(\d{2})[./-](\d{2})(?:[./-](\d{2,4}))?(?!\d)")
# Toute date JJ.MM ou JJ.MM.AA(AA) dans une ligne.
ANY_DATE_RE = re.compile(r"\b(\d{2})[./-](\d{2})(?:[./-](\d{2,4}))?\b")

# Espaces fréquents dans les PDF FR (insécable, fine).
_FR_SPACE = r"[ \u00a0\u202f\u2009]"
# Montant FR : « 1 250,40 », « 86,30 », « 725 ,93 », « 4 000 , 00 », « 7 366, 88 ».
AMOUNT_RE = re.compile(
    rf"(?<![\d.,])(-?\d{{1,3}}(?:{_FR_SPACE}\d{{3}})*{_FR_SPACE}*,{_FR_SPACE}*\d{{2}})"
)

# Période imprimée : « du 31 juillet 2026 au 31 août 2026 ».
PERIOD_FR_RE = re.compile(
    r"du\s+(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})\s+au\s+(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})",
    re.IGNORECASE,
)
PERIOD_NUM_RE = re.compile(
    r"du\s+(\d{2}[./-]\d{2}[./-]\d{2,4})\s+au\s+(\d{2}[./-]\d{2}[./-]\d{2,4})",
    re.IGNORECASE,
)

MONTHS_FR = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}

# En-têtes, pub, mentions légales : on ne les prend pas pour des opérations.
# (Sauf si la ligne commence par une date : alors c'est peut-être une vraie opé.)
SKIP_LINE_RE = re.compile(
    r"(page\s+\d+|p\.\s*\d+\s*/\s*\d+|bnp\s*paribas|www\.bnp|iban\s*:|bic\s*:|"
    r"relev[eé]\s+de\s+(?:votre\s+)?compte|nature des op[eé]rations|"
    r"ancien solde|nouveau solde|date\s+valeur|"
    r"conditions et tarifs|votre conseiller|identifiant|"
    r"autorisation de d[eé](?:couvert|bit)|message important|"
    r"sous[- ]?total|relev[eé]\s+[eé]dit[eé]|"
    r"si[eè]ge social|rcs\s+paris|garantiedesdepots|^sorp\d)",
    re.IGNORECASE,
)

# Ligne d'en-tête de tableau ou total, sans opération.
TABLE_HEADER_RE = re.compile(
    r"^(date|valeur|d[eé]bit|cr[eé]dit|sorties\s*:?|entr[eé]es\s*:?|total)\s*$",
    re.IGNORECASE,
)

# Blocs du relevé BNP (pdftotext) : tout le bloc est crédit ou débit.
SECTION_CREDIT_RE = re.compile(
    r"^(?:virement[s]?\s+re[cç]u[s]?)\s*:?\s*$",
    re.IGNORECASE,
)
SECTION_DEBIT_RE = re.compile(
    r"^(?:virement[s]?\s+[eé]mis|pr[eé]l[eè]vement[s](?:\s*/\s*.+)?)\s*:?\s*$",
    re.IGNORECASE,
)


# Quand il n'y a qu'une colonne de montant, le libellé dit le sens.
CREDIT_HINT = re.compile(
    r"virement en votre faveur|en votre faveur|salaire|"
    r"remboursement|avoir|remise|d[eé]p[oô]t|"
    r"vir(?:ement)?s?\s+sepa\s+re[cç]u|vir(?:ement)?s?\s+re[cç]u",
    re.I,
)
DEBIT_HINT = re.compile(
    r"pr[eé]l[eè]vement|paiement par carte|carte|ch[eè]que|"
    r"retrait|frais|commission|virement sepa emis|virement [eé]mis|"
    r"faveur tiers|vr\.?\s*permanent",
    re.I,
)


def is_frozen() -> bool:
    """True si on tourne dans l'exe PyInstaller (pas `python bnp2ofx.py`)."""
    return bool(getattr(sys, "frozen", False))


def signed_amount(label: str, value: Decimal) -> Decimal:
    """Décide le signe d'un montant unique à partir du libellé."""
    if CREDIT_HINT.search(label):
        return abs(value)  # entrée d'argent
    if DEBIT_HINT.search(label):
        return -abs(value)  # sortie d'argent
    # Par défaut un débit : la majorité des lignes d'un compte chèque sont des sorties.
    return -abs(value)


def parse_fr_amount(text: str) -> Decimal | None:
    """'1 250,40 €' → Decimal('1250.40'). None si ce n'est pas un nombre."""
    text = text.strip().replace("\u00a0", " ").replace(" ", "")  # 1 250,40 → 1250,40
    text = text.replace(",", ".")  # virgule FR → point attendu par Decimal
    text = text.replace("€", "").replace("EUR", "").strip()
    if not text or text in {"-", "—"}:
        return None
    try:
        return Decimal(text).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def _fold_month_name(name: str) -> str:
    """août / février → aout / fevrier, pour la table MONTHS_FR."""
    return (
        name.lower()
        .replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("ë", "e")
        .replace("à", "a")
        .replace("â", "a")
        .replace("ô", "o")
        .replace("û", "u")
        .replace("ù", "u")
        .replace("ç", "c")
    )


def _year_from_token(y: str) -> int:
    """Année à 2 chiffres : 00–79 → 2000–2079, 80–99 → 1980–1999."""
    year = int(y)
    if year < 100:
        year += 2000 if year < 80 else 1900
    return year


def _datetime_dmy(day: int, month: int, year: int) -> datetime | None:
    try:
        return datetime(year, month, day)
    except ValueError:
        return None  # ex. 31/02/2026


def extract_period(text: str) -> tuple[datetime | None, datetime | None]:
    """Lit « du 31 juillet 2026 au 31 août 2026 » (ou JJ/MM/AAAA)."""
    m = PERIOD_FR_RE.search(text)
    if m:
        d1, n1, y1, d2, n2, y2 = m.groups()
        mo1 = MONTHS_FR.get(_fold_month_name(n1))
        mo2 = MONTHS_FR.get(_fold_month_name(n2))
        if mo1 and mo2:
            start = _datetime_dmy(int(d1), mo1, int(y1))
            end = _datetime_dmy(int(d2), mo2, int(y2))
            if start and end:
                return start, end
    m = PERIOD_NUM_RE.search(text)
    if m:
        start, end = parse_date(m.group(1)), parse_date(m.group(2))
        if start and end:
            return start, end
    return None, None


def year_for_month(month: int, start: datetime | None, end: datetime | None) -> int:
    """Année d'une date JJ.MM d'après la période du relevé (gère déc. → janv.)."""
    if start and end:
        if start.year == end.year:
            return start.year
        if month >= start.month:
            return start.year
        return end.year
    if end:
        return end.year
    if start:
        return start.year
    return datetime.now().year


def parse_date_groups(
    groups: tuple[str, ...] | list[str],
    period_start: datetime | None = None,
    period_end: datetime | None = None,
) -> datetime | None:
    """Groupes regex (jour, mois, année?) → datetime. Année déduite si absente."""
    day = int(groups[0])
    month = int(groups[1])
    year_tok = groups[2] if len(groups) > 2 else ""
    if not (1 <= month <= 12):
        return None
    if year_tok:
        year = _year_from_token(year_tok)
    else:
        year = year_for_month(month, period_start, period_end)
    return _datetime_dmy(day, month, year)


def parse_date(text: str) -> datetime | None:
    """Première date avec année trouvée dans la chaîne, ou None."""
    m = DATE_RE.search(text)
    if not m:
        return None
    return parse_date_groups(m.groups())


def line_starts_with_date(ln: str) -> bool:
    """True si la ligne commence par JJ.MM ou JJ.MM.AAAA (mois 1–12)."""
    m = LINE_START_DATE_RE.match(ln)
    if not m:
        return False
    month = int(m.group(2))
    return 1 <= month <= 12


def line_amounts(ln: str) -> list[Decimal]:
    """Tous les montants reconnus sur une ligne."""
    return [a for a in (parse_fr_amount(x) for x in AMOUNT_RE.findall(ln)) if a is not None]


def extract_text(path: Path) -> str:
    """PDF ou TXT → texte brut en mémoire. Pas d'OCR, pas d'écriture sur disque."""
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix != ".pdf":
        raise ValueError("Fichier non supporté. Choisissez un PDF ou un TXT.")
    try:
        import pdfplumber
    except ImportError as exc:
        if is_frozen():
            raise RuntimeError(
                "Le module de lecture PDF est manquant dans cette version. "
                "Réinstallez BNP2OFX."
            ) from exc
        raise RuntimeError(
            "Le module pdfplumber n'est pas installé.\n"
            "Dans un terminal : pip install pdfplumber"
        ) from exc
    parts: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            # x/y_tolerance : colle les morceaux de texte un peu écartés (colonnes BNP).
            t = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
            parts.append(t)
    if not any(p.strip() for p in parts):
        raise ValueError(
            "Aucun texte extrait. Ce PDF est peut-être un scan (image). "
            "Ce mini-logiciel ne fait pas d'OCR."
        )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Parseur heuristique type relevé BNP (compte chèque)
# ---------------------------------------------------------------------------
# On ne lit pas les colonnes par position X/Y. On lit le texte ligne à ligne :
#   - une ligne qui COMMENCE par une date = nouvelle opération
#   - les lignes suivantes sans date = suite du libellé (souvent le montant)
# Si BNP change la mise en page, c'est ici que ça casse — d'où le contrôle des soldes.

def _amounts_near(lines: list[str], index: int) -> list[Decimal]:
    """Montants sur la ligne, sinon ceux du bloc qui suit jusqu'à un en-tête.

    Sur un pdftotext sans colonnes, « Solde créditeur » peut être suivi du
    total des entrées puis du vrai solde : on garde toute la liste (le
    parseur prend le dernier).
    """
    found = line_amounts(lines[index])
    if found:
        return found
    collected: list[Decimal] = []
    for j in range(index + 1, min(index + 8, len(lines))):
        s = lines[j].strip()
        if not s:
            continue
        if line_starts_with_date(s) or re.search(r"\bsolde\b", s, re.I):
            break
        if SKIP_LINE_RE.search(s) or TABLE_HEADER_RE.match(s):
            break
        if SECTION_CREDIT_RE.match(s) or SECTION_DEBIT_RE.match(s):
            break
        amts = line_amounts(s)
        if amts:
            collected.extend(amts)
            continue
        break
    return collected


def _signed_from_columns(
    amounts: list[Decimal],
    label: str,
    section_sign: int | None,
) -> Decimal:
    """Choisit le signe : colonnes débit/crédit, sinon bloc du relevé, sinon libellé."""
    if len(amounts) >= 2:
        debit, credit = amounts[-2], amounts[-1]
        if credit != 0 and debit == 0:
            return credit
        if debit != 0 and credit == 0:
            return -abs(debit)
        if credit != 0 and debit != 0:
            if CREDIT_HINT.search(label):
                return abs(credit)
            return -abs(debit)
        return Decimal("0.00")
    if not amounts:
        return Decimal("0.00")
    value = amounts[-1]
    if section_sign is not None:
        return abs(value) * section_sign
    return signed_amount(label, value)


def parse_statement(text: str) -> Statement:
    stmt = Statement()
    lines = [ln.rstrip() for ln in text.splitlines()]
    period_start, period_end = extract_period(text)

    # --- Compte / IBAN -------------------------------------------------------
    # IBAN FR : FR + 2 clés + 5 banque + 5 guichet + 11 compte + 2 clé RIB.
    iban = re.search(r"\bFR\s*\d{2}(?:\s*\d{4}){5}\s*\d{3}\b", text, re.I)
    if iban:
        digits = re.sub(r"\D", "", iban.group(0))  # garde uniquement 0-9
        if len(digits) >= 21:
            stmt.bank_id = digits[2:7]      # ex. 30004 (BNP)
            stmt.account_id = digits[12:23]  # 11 chiffres du compte

    # RIB imprimé : 5 (banque) + 5 (guichet) + 11 (compte) + 2 (clé).
    rib = re.search(r"\b(\d{5})\s+(\d{5})\s+(\d{11})\s+(\d{2})\b", text)
    if rib and stmt.account_id == "00000000000":
        stmt.bank_id = rib.group(1)
        stmt.account_id = rib.group(3)

    # Si pas d'IBAN ni de RIB, on prend le premier bloc de 11 chiffres du document.
    acct = re.search(r"\b(\d{11})\b", text)
    if acct and stmt.account_id == "00000000000":
        stmt.account_id = acct.group(1)

    # --- Soldes imprimés (pour le contrôle de fin, pas pour inventer les opés)
    # Sur les relevés pdftotext, le montant est souvent la ligne sous « Solde au … ».
    labeled_opening: Decimal | None = None
    labeled_closing: Decimal | None = None
    found_soldes: list[Decimal] = []
    for i, ln in enumerate(lines):
        if not re.search(r"\bsolde\b", ln, re.IGNORECASE):
            continue
        amounts = _amounts_near(lines, i)
        if not amounts:
            continue
        value = amounts[-1]
        found_soldes.append(value)
        low = ln.lower()
        if "ancien" in low or "initial" in low or "pr" in low and "cédent" in low:
            labeled_opening = value
        elif "nouveau" in low or "final" in low:
            labeled_closing = value

    stmt.opening = labeled_opening
    stmt.closing = labeled_closing
    if stmt.opening is None and found_soldes:
        stmt.opening = found_soldes[0]
    if stmt.closing is None and found_soldes:
        stmt.closing = found_soldes[-1]

    # --- Opérations ----------------------------------------------------------
    current: Transaction | None = None  # opération en cours de construction
    section_sign: int | None = None  # +1 = bloc crédits, -1 = bloc débits

    def flush():
        """Range l'opération en cours si elle a un montant, puis l'oublie."""
        nonlocal current
        if current and current.amount != 0:
            if not current.label.strip():
                current.label = "Opération"
            stmt.transactions.append(current)
        current = None

    for raw in lines:
        ln = raw.strip()
        if not ln:
            continue
        # Identifiant de page (ex. 624333249473), pas une référence d'opération.
        if current is None and re.fullmatch(r"\d{10,}", ln):
            continue

        starts_with_date = line_starts_with_date(ln)

        if not starts_with_date and SECTION_CREDIT_RE.match(ln):
            flush()
            section_sign = 1
            continue
        if not starts_with_date and SECTION_DEBIT_RE.match(ln):
            flush()
            section_sign = -1
            continue

        if TABLE_HEADER_RE.match(ln):
            continue

        # Ignore en-têtes / pub, SAUF si la ligne commence par une date.
        if SKIP_LINE_RE.search(ln) and not starts_with_date:
            if re.search(
                r"sous[- ]?total|^\s*total\s*$|p\.\s*\d|page\s+\d|relev[eé]\s+de",
                ln,
                re.I,
            ):
                flush()
            continue

        if starts_with_date:
            dates = ANY_DATE_RE.findall(ln)
            d0 = parse_date_groups(dates[0], period_start, period_end) if dates else None
            d1 = (
                parse_date_groups(dates[1], period_start, period_end)
                if len(dates) > 1
                else d0
            )
            if d0 is None:
                continue

            # On retire les 1 ou 2 dates du début pour isoler libellé + montants.
            rest = ANY_DATE_RE.sub(" ", ln, count=2 if len(dates) > 1 else 1)
            amounts_txt = AMOUNT_RE.findall(rest)
            amounts = [a for a in (parse_fr_amount(x) for x in amounts_txt) if a is not None]
            label = rest
            for a in amounts_txt:
                label = label.replace(a, " ")
            label = re.sub(r"\s+", " ", label).strip(" -–|")

            # pdftotext empile souvent : date opé, date valeur, libellé, montant.
            date_only = not label and not amounts
            if (
                date_only
                and current is not None
                and current.amount == 0
                and not current.label.strip()
            ):
                current.date_valeur = d0
                current.raw_lines.append(ln)
                continue

            flush()
            current = Transaction(
                date=d0,
                date_valeur=d1,
                label=label,
                amount=_signed_from_columns(amounts, label, section_sign),
                raw_lines=[ln],
            )
        elif current is not None:
            extra_amts = AMOUNT_RE.findall(ln)
            parsed = [a for a in (parse_fr_amount(x) for x in extra_amts) if a is not None]
            amount_only = bool(parsed) and not re.sub(
                r"[\s\u00a0\u202f\u2009€EUR]", "", AMOUNT_RE.sub("", ln), flags=re.I
            )
            if parsed and current.amount == 0:
                current.amount = _signed_from_columns(
                    parsed, current.label, section_sign
                )
                for a in extra_amts:
                    ln = ln.replace(a, " ")
            elif amount_only:
                continue
            cleaned = re.sub(r"\s+", " ", ln).strip()
            if cleaned:
                current.label = (current.label + " " + cleaned).strip()
                current.raw_lines.append(raw)

    flush()  # n'oublie pas la dernière opération du relevé

    if stmt.transactions:
        dates = [t.date for t in stmt.transactions]
        stmt.date_start = min(dates)
        stmt.date_end = max(dates)

    # Filet de sécurité : ancien + somme(opés) doit retomber sur le nouveau solde.
    # Si le « solde » lu est un sous-total (entrées/sorties collés par pdftotext),
    # on reprend un montant « solde » du document qui tombe juste.
    if stmt.opening is not None and stmt.transactions:
        computed = stmt.opening + sum((t.amount for t in stmt.transactions), Decimal("0"))
        if stmt.closing is None or (computed - stmt.closing).copy_abs() > Decimal("0.05"):
            for cand in found_soldes:
                if (computed - cand).copy_abs() <= Decimal("0.05"):
                    stmt.closing = cand
                    break
        if stmt.closing is not None:
            delta = (computed - stmt.closing).copy_abs()
            if delta > Decimal("0.05"):
                stmt.warnings.append(
                    f"Les soldes ne tombent pas juste "
                    f"(calculé {computed} €, relevé {stmt.closing} €, écart {delta} €). "
                    "Vérifiez le fichier OFX avant import."
                )

    if not stmt.transactions:
        stmt.warnings.append(
            "Aucune opération détectée. Le format de ce relevé n'est peut-être pas reconnu."
        )

    return stmt


# ---------------------------------------------------------------------------
# Génération OFX 1.02 (SGML) — largement accepté en France
# ---------------------------------------------------------------------------
# OFX 1.02 n'est pas du XML strict (balises souvent non fermées). On écrit
# quand même des balises fermées : ça reste lu par Sage / Cegid / GnuCash.

def ofx_date(dt: datetime) -> str:
    """2026-05-02 → '20260502' (format OFX)."""
    return dt.strftime("%Y%m%d")


def fitid(t: Transaction, idx: int) -> str:
    """Identifiant stable d'une opération : même relevé réimporté = pas de doublon."""
    basis = f"{ofx_date(t.date)}|{t.amount}|{t.label}|{idx}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:22]


def generate_ofx(stmt: Statement) -> str:
    """Statement → contenu texte d'un fichier .ofx (pas encore écrit sur disque)."""
    now = datetime.now()
    dtserver = now.strftime("%Y%m%d%H%M%S")
    start = stmt.date_start or now
    end = stmt.date_end or now

    trn_xml = []
    for i, t in enumerate(stmt.transactions, start=1):
        trntype = "CREDIT" if t.amount >= 0 else "DEBIT"
        name = t.label[:32]   # champ NAME OFX : court (affiché en liste)
        memo = t.label[:255]  # MEMO : libellé complet
        trn_xml.append(
            f"""
    <STMTTRN>
      <TRNTYPE>{trntype}</TRNTYPE>
      <DTPOSTED>{ofx_date(t.date)}</DTPOSTED>
      <TRNAMT>{t.amount:.2f}</TRNAMT>
      <FITID>{fitid(t, i)}</FITID>
      <NAME>{escape(name)}</NAME>
      <MEMO>{escape(memo)}</MEMO>
    </STMTTRN>""".rstrip()
        )

    # Solde de clôture vu par le logiciel de compta après import.
    balamt = stmt.closing if stmt.closing is not None else Decimal("0.00")
    ledger = f"""
    <LEDGERBAL>
      <BALAMT>{balamt:.2f}</BALAMT>
      <DTASOF>{ofx_date(end)}</DTASOF>
    </LEDGERBAL>"""

    # En-tête SGML puis arbre : SIGNON (session) + BANKMSG (compte + liste d'opés).
    body = f"""OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:UTF-8
CHARSET:NONE
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
  <SIGNONMSGSRSV1>
    <SONRS>
      <STATUS>
        <CODE>0</CODE>
        <SEVERITY>INFO</SEVERITY>
      </STATUS>
      <DTSERVER>{dtserver}</DTSERVER>
      <LANGUAGE>FRA</LANGUAGE>
    </SONRS>
  </SIGNONMSGSRSV1>
  <BANKMSGSRSV1>
    <STMTTRNRS>
      <TRNUID>1</TRNUID>
      <STATUS>
        <CODE>0</CODE>
        <SEVERITY>INFO</SEVERITY>
      </STATUS>
      <STMTRS>
        <CURDEF>{stmt.currency}</CURDEF>
        <BANKACCTFROM>
          <BANKID>{escape(stmt.bank_id)}</BANKID>
          <ACCTID>{escape(stmt.account_id)}</ACCTID>
          <ACCTTYPE>CHECKING</ACCTTYPE>
        </BANKACCTFROM>
        <BANKTRANLIST>
          <DTSTART>{ofx_date(start)}</DTSTART>
          <DTEND>{ofx_date(end)}</DTEND>
{''.join(trn_xml)}
        </BANKTRANLIST>
{ledger}
      </STMTRS>
    </STMTTRNRS>
  </BANKMSGSRSV1>
</OFX>
"""
    return body


# ---------------------------------------------------------------------------
# Conversion d'un fichier ou d'un dossier (enchaîne extract → parse → OFX)
# ---------------------------------------------------------------------------

def convert_file(src: Path, dst: Path) -> FileResult:
    """Convertit un PDF/TXT. N'interrompt pas un lot : les erreurs vont dans FileResult."""
    try:
        text = extract_text(src)
        stmt = parse_statement(text)
        if not stmt.transactions:
            return FileResult(
                source=src,
                account_id=stmt.account_id,
                error="Aucune opération reconnue dans ce fichier.",
                warnings=list(stmt.warnings),
            )
        dst.parent.mkdir(parents=True, exist_ok=True)  # crée OFX/2026/ si besoin
        dst.write_text(generate_ofx(stmt), encoding="utf-8")
        return FileResult(
            source=src,
            dest=dst,
            n_ops=len(stmt.transactions),
            account_id=stmt.account_id,
            warnings=list(stmt.warnings),
        )
    except Exception as exc:
        return FileResult(source=src, error=str(exc))


def iter_statement_files(folder: Path) -> list[Path]:
    """PDF et TXT dans le dossier, y compris les sous-dossiers."""
    found: list[Path] = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIR_NAMES]  # ne pas descendre dans OFX/
        for name in files:
            path = Path(root) / name
            if path.suffix.lower() in SUPPORTED_SUFFIXES:
                found.append(path)
    found.sort(key=lambda p: str(p).lower())
    return found


def default_ofx_dir(folder: Path) -> Path:
    """Les OFX d'un lot vont dans un sous-dossier OFX, pas mélangés aux PDF."""
    return folder / "OFX"


def dest_for_source(src: Path, folder: Path, out_dir: Path) -> Path:
    """2026/mai.pdf dans le dossier source → OFX/2026/mai.ofx (même arborescence)."""
    try:
        rel = src.relative_to(folder)
    except ValueError:
        rel = Path(src.name)
    return (out_dir / rel).with_suffix(".ofx")


def convert_folder(folder: Path, out_dir: Path | None = None) -> list[FileResult]:
    """Convertit tous les relevés d'un dossier. out_dir=None → dossier/OFX."""
    files = iter_statement_files(folder)
    target = out_dir or default_ofx_dir(folder)
    return [convert_file(src, dest_for_source(src, folder, target)) for src in files]


def summarize_results(results: list[FileResult]) -> str:
    """Texte récapitulatif pour la fenêtre et la ligne de commande."""
    ok = [r for r in results if r.ok]
    warn = [r for r in ok if r.warnings]
    err = [r for r in results if not r.ok]
    lines = [
        f"{len(results)} fichier(s) traité(s) : "
        f"{len(ok)} converti(s), {len(warn)} avec avertissement, {len(err)} en erreur.",
        "",
    ]
    for r in results:
        name = r.source.name
        if r.ok:
            mark = "ATTENTION" if r.warnings else "OK"
            extra = f"  ({r.warnings[0]})" if r.warnings else ""
            dest = r.dest.name if r.dest else ""
            lines.append(f"{mark}  {name}  →  {r.n_ops} opération(s)  {dest}{extra}")
        else:
            lines.append(f"ERREUR  {name}  →  {r.error}")
    return "\n".join(lines)


def open_in_explorer(path: Path) -> None:
    """Ouvre le dossier dans l'explorateur de fichiers du système."""
    path = path if path.is_dir() else path.parent
    if not path.exists():
        return
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        os.system(f'open "{path}"')
    else:
        os.system(f'xdg-open "{path}"')


# ---------------------------------------------------------------------------
# Interface graphique (tkinter = inclus avec Python, pas de dépendance UI)
# ---------------------------------------------------------------------------

def _enable_windows_dpi() -> None:
    """Évite un texte flou sur un écran haute résolution Windows."""
    if sys.platform != "win32":
        return
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            from ctypes import windll

            windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def run_gui(preselect: Path | None = None) -> None:
    """Fenêtre : choisir un fichier ou un dossier, convertir, ouvrir le résultat.
    preselect : chemin déjà choisi (glisser-déposer un fichier sur l'exe)."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    _enable_windows_dpi()

    root = tk.Tk()
    root.title(f"{APP_NAME} — relevé PDF vers OFX")
    root.minsize(640, 520)
    root.geometry("720x560")

    selected: dict[str, Path | None] = {"path": None}   # fichier ou dossier choisi
    last_out: dict[str, Path | None] = {"path": None}   # dernier OFX / dossier OFX écrit

    header = tk.Label(
        root,
        text="Convertir des relevés BNP (PDF ou TXT) en fichiers OFX",
        font=("Segoe UI", 14, "bold"),
        pady=10,
    )
    header.pack()

    hint = tk.Label(
        root,
        text="Tout se passe sur cet ordinateur. Aucun fichier n'est envoyé en ligne.\n"
        "Vous pouvez convertir un relevé, ou tout un dossier d'un coup.",
        fg="#444",
        justify="center",
    )
    hint.pack()

    file_var = tk.StringVar(value="Aucun fichier ni dossier choisi")
    file_lbl = tk.Label(root, textvariable=file_var, wraplength=660, pady=8)
    file_lbl.pack()

    progress = ttk.Progressbar(root, mode="determinate")
    progress.pack(fill="x", padx=20, pady=(0, 4))

    status_var = tk.StringVar(value="")
    status_lbl = tk.Label(root, textvariable=status_var, fg="#333")
    status_lbl.pack()

    info = tk.Text(root, height=14, wrap="word", state="disabled")
    info.pack(fill="both", expand=True, padx=16, pady=8)

    def set_info(text: str) -> None:
        """Remplace le texte de la zone de résultats (widget en lecture seule)."""
        info.configure(state="normal")
        info.delete("1.0", "end")
        info.insert("1.0", text)
        info.configure(state="disabled")

    def set_selection(path: Path) -> None:
        """Mémorise le choix et affiche un aperçu (liste des PDF si c'est un dossier)."""
        selected["path"] = path
        last_out["path"] = None
        if path.is_dir():
            files = iter_statement_files(path)
            file_var.set(f"Dossier : {path}")
            if not files:
                set_info(
                    "Aucun PDF ou TXT trouvé dans ce dossier.\n"
                    "Placez-y vos relevés BNP (fichiers .pdf), puis choisissez-le à nouveau."
                )
            else:
                preview = "\n".join(f" • {p.relative_to(path)}" for p in files[:40])
                extra = ""
                if len(files) > 40:
                    extra = f"\n • … et {len(files) - 40} autres"
                set_info(
                    f"{len(files)} relevé(s) trouvé(s). Cliquez sur Convertir.\n"
                    f"Les fichiers OFX seront enregistrés dans :\n{default_ofx_dir(path)}\n\n"
                    f"{preview}{extra}"
                )
        else:
            file_var.set(f"Fichier : {path}")
            set_info("Relevé prêt. Cliquez sur Convertir, puis choisissez où enregistrer l'OFX.")

    def choose_file() -> None:
        """Bouton : dialogue 'ouvrir un fichier' PDF/TXT."""
        path = filedialog.askopenfilename(
            title="Choisir un relevé",
            filetypes=[
                ("Relevés", "*.pdf *.txt"),
                ("PDF", "*.pdf"),
                ("Texte", "*.txt"),
                ("Tous les fichiers", "*.*"),
            ],
        )
        if not path:
            return
        set_selection(Path(path))

    def choose_folder() -> None:
        """Bouton : dialogue 'ouvrir un dossier' (lot de relevés)."""
        path = filedialog.askdirectory(title="Choisir le dossier qui contient les relevés PDF")
        if not path:
            return
        set_selection(Path(path))

    def recap_statement(stmt: Statement) -> str:
        """Texte affiché après parsing d'un seul relevé (compte, soldes, aperçu)."""
        recap = [
            f"Compte : {stmt.account_id}",
            f"Opérations : {len(stmt.transactions)}",
        ]
        if stmt.date_start and stmt.date_end:
            recap.append(
                f"Période : {stmt.date_start:%d/%m/%Y} → {stmt.date_end:%d/%m/%Y}"
            )
        if stmt.opening is not None:
            recap.append(f"Ancien solde : {stmt.opening} €")
        if stmt.closing is not None:
            recap.append(f"Nouveau solde : {stmt.closing} €")
        if stmt.warnings:
            recap.append("")
            recap.append("Avertissements :")
            recap.extend(f" • {w}" for w in stmt.warnings)
        if stmt.transactions:
            recap.append("")
            recap.append("Aperçu :")
            for t in stmt.transactions[:12]:
                recap.append(f" {t.date:%d/%m/%Y}  {t.amount:>10} €  {t.label[:50]}")
            if len(stmt.transactions) > 12:
                recap.append(f" … et {len(stmt.transactions) - 12} autres")
        return "\n".join(recap)

    def convert_one_interactive(path: Path) -> None:
        """Un fichier : parse, montre l'aperçu, demande où sauver l'OFX."""
        try:
            text = extract_text(path)
            stmt = parse_statement(text)
        except Exception as exc:
            messagebox.showerror("Erreur de lecture", str(exc))
            return

        set_info(recap_statement(stmt))
        if not stmt.transactions:
            messagebox.showerror(
                "Conversion impossible",
                "Aucune opération n'a été reconnue dans ce fichier.",
            )
            return

        default_name = path.with_suffix(".ofx").name
        out = filedialog.asksaveasfilename(
            title="Enregistrer le fichier OFX",
            defaultextension=".ofx",
            initialfile=default_name,
            filetypes=[("OFX", "*.ofx"), ("Tous les fichiers", "*.*")],
        )
        if not out:
            return
        out_path = Path(out)
        out_path.write_text(generate_ofx(stmt), encoding="utf-8")
        last_out["path"] = out_path
        extra = ""
        if stmt.warnings:
            extra = "\n\nAttention : " + stmt.warnings[0]
        messagebox.showinfo(
            "Terminé",
            f"{len(stmt.transactions)} opération(s) enregistrées dans :\n{out_path}{extra}",
        )

    def convert_folder_interactive(folder: Path) -> None:
        """Un dossier : un OFX par PDF, barre de progression, récapitulatif."""
        files = iter_statement_files(folder)
        if not files:
            messagebox.showwarning(
                "Aucun relevé",
                "Aucun fichier PDF ou TXT n'a été trouvé dans ce dossier.",
            )
            return
        out_dir = default_ofx_dir(folder)
        progress["maximum"] = max(len(files), 1)
        progress["value"] = 0
        results: list[FileResult] = []
        for i, src in enumerate(files, start=1):
            status_var.set(f"Conversion {i} / {len(files)} : {src.name}")
            progress["value"] = i - 1
            root.update_idletasks()  # rafraîchit la fenêtre pendant la boucle (sinon elle fige)
            dest = dest_for_source(src, folder, out_dir)
            results.append(convert_file(src, dest))
            progress["value"] = i
            root.update_idletasks()

        last_out["path"] = out_dir
        summary = summarize_results(results)
        set_info(summary + f"\n\nDossier des OFX :\n{out_dir}")
        status_var.set("Terminé.")
        ok = sum(1 for r in results if r.ok)
        err = sum(1 for r in results if not r.ok)
        warn = sum(1 for r in results if r.ok and r.warnings)
        messagebox.showinfo(
            "Terminé",
            f"{ok} relevé(s) converti(s) dans :\n{out_dir}\n\n"
            f"Avertissements : {warn}\nErreurs : {err}",
        )

    def convert() -> None:
        """Bouton Convertir : fichier ou dossier selon ce qui a été choisi."""
        path = selected["path"]
        if path is None:
            messagebox.showwarning(
                "Choix manquant",
                "Choisissez d'abord un relevé ou un dossier de relevés.",
            )
            return
        for btn in (btn_file, btn_folder, btn_convert, btn_open):
            btn.configure(state="disabled")  # évite un second clic pendant le traitement
        try:
            if path.is_dir():
                convert_folder_interactive(path)
            else:
                convert_one_interactive(path)
        except Exception:
            messagebox.showerror(
                "Erreur inattendue",
                "La conversion a échoué.\n\n" + traceback.format_exc(limit=4),
            )
        finally:
            for btn in (btn_file, btn_folder, btn_convert, btn_open):
                btn.configure(state="normal")

    def open_output() -> None:
        """Bouton : ouvre le dossier des OFX (ou le dossier du fichier sauvé)."""
        target = last_out["path"] or selected["path"]
        if target is None:
            messagebox.showinfo(
                "Rien à ouvrir",
                "Convertissez d'abord un relevé ou un dossier.",
            )
            return
        open_in_explorer(target)

    btns = ttk.Frame(root)
    btns.pack(pady=10)
    btn_file = ttk.Button(btns, text="1. Choisir un relevé…", command=choose_file)
    btn_file.grid(row=0, column=0, padx=6)
    btn_folder = ttk.Button(
        btns, text="1. Choisir un dossier de relevés…", command=choose_folder
    )
    btn_folder.grid(row=0, column=1, padx=6)
    btn_convert = ttk.Button(btns, text="2. Convertir", command=convert)
    btn_convert.grid(row=0, column=2, padx=6)
    btn_open = ttk.Button(btns, text="Ouvrir le dossier des OFX", command=open_output)
    btn_open.grid(row=0, column=3, padx=6)

    footer = tk.Label(
        root,
        text=f"{APP_NAME} {APP_VERSION}  ·  hors ligne  ·  relevés BNP Paribas",
        fg="#666",
        pady=4,
    )
    footer.pack(side="bottom")

    if preselect is not None and preselect.exists():
        set_selection(preselect)  # glisser-déposer sur l'exe

    root.mainloop()  # boucle d'événements : la fenêtre reste ouverte jusqu'à fermeture


def _ensure_console() -> None:
    """L'exe est compilé sans console. Si on le lance en ligne de commande,
    il faut en recréer une, sinon print() n'affiche nulle part."""
    if not is_frozen() or sys.platform != "win32":
        return
    try:
        if sys.stdout is not None and sys.stdout.isatty():
            return
    except Exception:
        pass
    try:
        from ctypes import windll

        windll.kernel32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        sys.stderr = sys.stdout
        sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
    except Exception:
        pass


def _print_usage() -> None:
    print(
        f"{APP_NAME} {APP_VERSION}\n"
        "Usage :\n"
        "  bnp2ofx.py                         Fenêtre graphique\n"
        "  bnp2ofx.py relevé.pdf [sortie.ofx] Un fichier\n"
        "  bnp2ofx.py dossier\\ [dossier_ofx]  Tous les PDF/TXT du dossier\n"
    )


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée : GUI si pas d'argument, sinon conversion en ligne de commande.
    Codes de sortie : 0 = OK, 1 = conversion ratée, 2 = fichier introuvable."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        run_gui()
        return 0
    if argv[0] in {"-h", "--help", "/?"}:
        _ensure_console()
        _print_usage()
        return 0

    # Glisser-déposer un fichier ou un dossier sur l'exe → ouvrir la fenêtre
    if is_frozen() and len(argv) == 1:
        dropped = Path(argv[0])
        if dropped.exists():
            run_gui(preselect=dropped)
            return 0

    _ensure_console()
    src = Path(argv[0])
    if not src.exists():
        print("Introuvable :", src)
        return 2

    if src.is_dir():  # lot : tous les PDF/TXT, OFX dans dossier/OFX (ou argv[1])
        out_dir = Path(argv[1]) if len(argv) > 1 else default_ofx_dir(src)
        files = iter_statement_files(src)
        if not files:
            print("Aucun PDF ou TXT dans", src)
            return 1
        results = [
            convert_file(f, dest_for_source(f, src, out_dir)) for f in files
        ]
        print(summarize_results(results))
        print("Dossier des OFX :", out_dir)
        return 0 if all(r.ok for r in results) else 1

    # Un seul fichier : sortie.ofx fourni, ou même nom que le PDF.
    dst = Path(argv[1]) if len(argv) > 1 else src.with_suffix(".ofx")
    result = convert_file(src, dst)
    for w in result.warnings:
        print("AVERTISSEMENT:", w)
    if result.error:
        print("ERREUR:", result.error)
        return 1
    print(f"{result.n_ops} opération(s)")
    print("Écrit :", result.dest)
    return 0


if __name__ == "__main__":
    # Ne lance main() que si ce fichier est exécuté, pas s'il est importé (tests).
    raise SystemExit(main())
