#!/usr/bin/env python3
"""
BNP2OFX — mini convertisseur local PDF/TXT → OFX
Pensé pour les relevés BNP Paribas (compte chèque) au format texte.
Aucune donnée n'est envoyée sur Internet.
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
from xml.sax.saxutils import escape

APP_NAME = "BNP2OFX"
APP_VERSION = "1.1"
SUPPORTED_SUFFIXES = {".pdf", ".txt"}
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
# Modèle
# ---------------------------------------------------------------------------

TWOPLACES = Decimal("0.01")


@dataclass
class Transaction:
    date: datetime
    date_valeur: datetime | None
    label: str
    amount: Decimal  # négatif = débit
    raw_lines: list[str] = field(default_factory=list)


@dataclass
class Statement:
    account_id: str = "00000000000"
    bank_id: str = "30004"  # code banque BNP (indicatif)
    currency: str = "EUR"
    date_start: datetime | None = None
    date_end: datetime | None = None
    opening: Decimal | None = None
    closing: Decimal | None = None
    transactions: list[Transaction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class FileResult:
    source: Path
    dest: Path | None = None
    n_ops: int = 0
    account_id: str = ""
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and self.dest is not None


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

DATE_RE = re.compile(r"\b(\d{2})[./-](\d{2})[./-](\d{2,4})\b")
AMOUNT_RE = re.compile(
    r"(?<![\d])(-?\d{1,3}(?:[ \u00a0]\d{3})*(?:,\d{2})|-?\d+(?:,\d{2}))\b"
)

SKIP_LINE_RE = re.compile(
    r"(page\s+\d+|bnp\s*paribas|www\.bnp|iban\s*:|bic\s*:|"
    r"relev[eé]\s+de\s+compte|nature des op[eé]rations|"
    r"ancien solde|nouveau solde|date\s+valeur|"
    r"conditions et tarifs|votre conseiller|identifiant|"
    r"autorisation de d[eé]couvert|message important)",
    re.IGNORECASE,
)

BALANCE_RE = re.compile(
    r"(ancien solde|solde (?:pr[eé]c[eé]dent|initial|au)|"
    r"nouveau solde|solde (?:final|au \d{2}))",
    re.IGNORECASE,
)


CREDIT_HINT = re.compile(
    r"virement en votre faveur|en votre faveur|salaire|"
    r"remboursement|avoir|remise|d[eé]p[oô]t",
    re.I,
)
DEBIT_HINT = re.compile(
    r"pr[eé]l[eè]vement|paiement par carte|carte|ch[eè]que|"
    r"retrait|frais|commission|virement sepa emis|virement [eé]mis",
    re.I,
)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def signed_amount(label: str, value: Decimal) -> Decimal:
    """Décide le signe d'un montant unique à partir du libellé."""
    if CREDIT_HINT.search(label):
        return abs(value)
    if DEBIT_HINT.search(label):
        return -abs(value)
    return -abs(value)


def parse_fr_amount(text: str) -> Decimal | None:
    text = text.strip().replace("\u00a0", " ").replace(" ", "")
    text = text.replace(",", ".")
    text = text.replace("€", "").replace("EUR", "").strip()
    if not text or text in {"-", "—"}:
        return None
    try:
        return Decimal(text).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def parse_date(text: str) -> datetime | None:
    m = DATE_RE.search(text)
    if not m:
        return None
    d, mo, y = m.groups()
    year = int(y)
    if year < 100:
        year += 2000 if year < 80 else 1900
    try:
        return datetime(year, int(mo), int(d))
    except ValueError:
        return None


def extract_text(path: Path) -> str:
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
            t = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
            parts.append(t)
    if not any(p.strip() for p in parts):
        raise ValueError(
            "Aucun texte extrait. Ce PDF est peut-être un scan (image). "
            "Ce mini-logiciel ne fait pas d'OCR."
        )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Parseur heuristique type relevé BNP
# ---------------------------------------------------------------------------

def parse_statement(text: str) -> Statement:
    stmt = Statement()
    lines = [ln.rstrip() for ln in text.splitlines()]

    # Compte / IBAN
    iban = re.search(r"\bFR\s*\d{2}(?:\s*\d{4}){5}\s*\d{3}\b", text, re.I)
    if iban:
        digits = re.sub(r"\D", "", iban.group(0))
        # FR + 2 clés + 5 banque + 5 guichet + 11 compte + 2 clé RIB
        if len(digits) >= 21:
            stmt.bank_id = digits[2:7]  # 30004 pour BNP
            stmt.account_id = digits[12:23]

    acct = re.search(r"\b(\d{11})\b", text)
    if acct and stmt.account_id == "00000000000":
        stmt.account_id = acct.group(1)

    # Soldes
    for ln in lines:
        if not BALANCE_RE.search(ln):
            continue
        amounts = [parse_fr_amount(a) for a in AMOUNT_RE.findall(ln)]
        amounts = [a for a in amounts if a is not None]
        if not amounts:
            continue
        low = ln.lower()
        if "ancien" in low or "pr" in low and "cédent" in low or "initial" in low:
            stmt.opening = amounts[-1]
        elif "nouveau" in low or "final" in low:
            stmt.closing = amounts[-1]

    # Fallback soldes : première / dernière occurrence « solde »
    if stmt.opening is None or stmt.closing is None:
        found: list[Decimal] = []
        for ln in lines:
            if re.search(r"\bsolde\b", ln, re.IGNORECASE):
                amts = [parse_fr_amount(a) for a in AMOUNT_RE.findall(ln)]
                amts = [a for a in amts if a is not None]
                if amts:
                    found.append(amts[-1])
        if found:
            if stmt.opening is None:
                stmt.opening = found[0]
            if stmt.closing is None:
                stmt.closing = found[-1]

    current: Transaction | None = None

    def flush():
        nonlocal current
        if current and current.amount != 0:
            stmt.transactions.append(current)
        current = None

    for raw in lines:
        ln = raw.strip()
        if not ln:
            continue
        if SKIP_LINE_RE.search(ln) and not DATE_RE.match(ln[:12] if len(ln) >= 10 else ln):
            continue

        starts_with_date = bool(DATE_RE.match(ln[:12])) if len(ln) >= 8 else False

        if starts_with_date:
            flush()
            dates = DATE_RE.findall(ln)
            d0 = parse_date("/".join(dates[0])) if dates else None
            d1 = parse_date("/".join(dates[1])) if len(dates) > 1 else d0
            if d0 is None:
                continue

            # Enlever les dates du début pour isoler libellé + montants
            rest = DATE_RE.sub(" ", ln, count=2 if len(dates) > 1 else 1)
            amounts_txt = AMOUNT_RE.findall(rest)
            amounts = [parse_fr_amount(a) for a in amounts_txt]
            amounts = [a for a in amounts if a is not None]

            # Retirer les montants du libellé
            label = rest
            for a in amounts_txt:
                label = label.replace(a, " ")
            label = re.sub(r"\s+", " ", label).strip(" -–|")

            amount = Decimal("0.00")
            if len(amounts) >= 2:
                # Deux colonnes débit / crédit : en général une seule est remplie
                debit, credit = amounts[-2], amounts[-1]
                # Si les deux sont non nuls et différents, on privilégie
                # la convention : avant-dernier = débit, dernier = crédit
                if credit != 0 and debit == 0:
                    amount = credit
                elif debit != 0 and credit == 0:
                    amount = -abs(debit)
                elif credit != 0 and debit != 0:
                    # Les deux remplis : rare. On prend le plus à droite comme crédit
                    # seulement si le libellé suggère une entrée.
                    if re.search(r"virement en votre faveur|salaire|remboursement|avoir", label, re.I):
                        amount = abs(credit)
                    else:
                        amount = -abs(debit)
                else:
                    amount = Decimal("0.00")
            elif len(amounts) == 1:
                amount = signed_amount(label, amounts[0])
            else:
                amount = Decimal("0.00")

            current = Transaction(
                date=d0,
                date_valeur=d1,
                label=label or "Opération",
                amount=amount,
                raw_lines=[ln],
            )
        elif current is not None:
            # Continuation de libellé
            if SKIP_LINE_RE.search(ln):
                continue
            extra_amts = AMOUNT_RE.findall(ln)
            # Montant reporté à la ligne suivante (cas fréquent BNP)
            if extra_amts and current.amount == 0:
                parsed = [parse_fr_amount(a) for a in extra_amts]
                parsed = [a for a in parsed if a is not None]
                if len(parsed) >= 2:
                    debit, credit = parsed[-2], parsed[-1]
                    if credit != 0 and debit == 0:
                        current.amount = signed_amount(current.label, credit)
                    elif debit != 0 and credit == 0:
                        current.amount = -abs(debit)
                    elif CREDIT_HINT.search(current.label):
                        current.amount = abs(credit)
                    else:
                        current.amount = -abs(debit)
                elif parsed:
                    current.amount = signed_amount(current.label, parsed[-1])
                for a in extra_amts:
                    ln = ln.replace(a, " ")
            cleaned = re.sub(r"\s+", " ", ln).strip()
            if cleaned:
                current.label = (current.label + " " + cleaned).strip()
                current.raw_lines.append(raw)

    flush()

    if stmt.transactions:
        dates = [t.date for t in stmt.transactions]
        stmt.date_start = min(dates)
        stmt.date_end = max(dates)

    # Contrôle des soldes
    if stmt.opening is not None and stmt.closing is not None and stmt.transactions:
        computed = stmt.opening + sum((t.amount for t in stmt.transactions), Decimal("0"))
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

def ofx_date(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


def fitid(t: Transaction, idx: int) -> str:
    basis = f"{ofx_date(t.date)}|{t.amount}|{t.label}|{idx}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:22]


def generate_ofx(stmt: Statement) -> str:
    now = datetime.now()
    dtserver = now.strftime("%Y%m%d%H%M%S")
    start = stmt.date_start or now
    end = stmt.date_end or now

    trn_xml = []
    for i, t in enumerate(stmt.transactions, start=1):
        trntype = "CREDIT" if t.amount >= 0 else "DEBIT"
        name = t.label[:32]
        memo = t.label[:255]
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

    balamt = stmt.closing if stmt.closing is not None else Decimal("0.00")
    ledger = f"""
    <LEDGERBAL>
      <BALAMT>{balamt:.2f}</BALAMT>
      <DTASOF>{ofx_date(end)}</DTASOF>
    </LEDGERBAL>"""

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
# Conversion d'un fichier ou d'un dossier
# ---------------------------------------------------------------------------

def convert_file(src: Path, dst: Path) -> FileResult:
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
        dst.parent.mkdir(parents=True, exist_ok=True)
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
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIR_NAMES]
        for name in files:
            path = Path(root) / name
            if path.suffix.lower() in SUPPORTED_SUFFIXES:
                found.append(path)
    found.sort(key=lambda p: str(p).lower())
    return found


def default_ofx_dir(folder: Path) -> Path:
    return folder / "OFX"


def dest_for_source(src: Path, folder: Path, out_dir: Path) -> Path:
    try:
        rel = src.relative_to(folder)
    except ValueError:
        rel = Path(src.name)
    return (out_dir / rel).with_suffix(".ofx")


def convert_folder(folder: Path, out_dir: Path | None = None) -> list[FileResult]:
    files = iter_statement_files(folder)
    target = out_dir or default_ofx_dir(folder)
    return [convert_file(src, dest_for_source(src, folder, target)) for src in files]


def summarize_results(results: list[FileResult]) -> str:
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
# Interface graphique (tkinter = inclus avec Python)
# ---------------------------------------------------------------------------

def _enable_windows_dpi() -> None:
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
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    _enable_windows_dpi()

    root = tk.Tk()
    root.title(f"{APP_NAME} — relevé PDF vers OFX")
    root.minsize(640, 520)
    root.geometry("720x560")

    selected: dict[str, Path | None] = {"path": None}
    last_out: dict[str, Path | None] = {"path": None}

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
        info.configure(state="normal")
        info.delete("1.0", "end")
        info.insert("1.0", text)
        info.configure(state="disabled")

    def set_selection(path: Path) -> None:
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
        path = filedialog.askdirectory(title="Choisir le dossier qui contient les relevés PDF")
        if not path:
            return
        set_selection(Path(path))

    def recap_statement(stmt: Statement) -> str:
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
            root.update_idletasks()
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
        path = selected["path"]
        if path is None:
            messagebox.showwarning(
                "Choix manquant",
                "Choisissez d'abord un relevé ou un dossier de relevés.",
            )
            return
        for btn in (btn_file, btn_folder, btn_convert, btn_open):
            btn.configure(state="disabled")
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
        set_selection(preselect)

    root.mainloop()


def _ensure_console() -> None:
    """Affiche une console si l'exe a été lancé en mode fenêtre avec des arguments."""
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

    if src.is_dir():
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
    raise SystemExit(main())
