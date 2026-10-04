"""Installeur simple si Inno Setup n'est pas disponible.
Copie BNP2OFX.exe dans %LOCALAPPDATA%\\BNP2OFX et crée un raccourci Bureau.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox


APP_NAME = "BNP2OFX"
EXE_NAME = "BNP2OFX.exe"


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent / "dist"


def find_payload() -> Path | None:
    """Trouve BNP2OFX.exe dans le bundle PyInstaller (racine, _internal, nom packed)."""
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS"))
        roots.extend(
            [
                meipass,
                meipass / "_internal",
                meipass.parent,
                Path(sys.executable).resolve().parent,
            ]
        )
    else:
        roots.append(resource_dir())

    names = {EXE_NAME.lower(), "bnp2ofx-packed.exe"}
    seen: set[Path] = set()
    for root in roots:
        try:
            root = root.resolve()
        except OSError:
            continue
        if root in seen or not root.is_dir():
            continue
        seen.add(root)
        for p in (root / EXE_NAME, root / "BNP2OFX-packed.exe"):
            if p.is_file():
                return p
        try:
            for p in root.glob("*.exe"):
                if p.name.lower() in names:
                    return p
        except OSError:
            continue
    return None


def install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME


def _csidl_path(csidl: int) -> Path | None:
    """Dossier Windows réel (Bureau OneDrive, menu Démarrer…), pas Public\\Desktop."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
    hr = ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    if hr != 0:
        return None
    path = Path(buf.value)
    return path if path.is_dir() else None


def desktop_dir() -> Path | None:
    # 0x10 = CSIDL_DESKTOPDIRECTORY (suit la redirection OneDrive / « Bureau »).
    found = _csidl_path(0x10)
    if found:
        return found
    home = Path.home()
    for candidate in (
        home / "Desktop",
        home / "Bureau",
        home / "OneDrive" / "Desktop",
        home / "OneDrive" / "Bureau",
    ):
        if candidate.is_dir():
            return candidate
    return None


def start_menu_dir() -> Path | None:
    # 0x02 = CSIDL_PROGRAMS
    found = _csidl_path(0x02)
    if found:
        return found
    programs = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    path = programs / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return path if path.is_dir() else None


def create_shortcut(lnk: Path, target: Path) -> bool:
    """True si le .lnk a été écrit. Ne lève pas : un Bureau non inscriptible
    (ex. C:\\Users\\Public\\Desktop) ne doit pas faire échouer l'installation."""
    try:
        lnk.parent.mkdir(parents=True, exist_ok=True)
        ps = (
            "$s = (New-Object -COM WScript.Shell).CreateShortcut('"
            + str(lnk).replace("'", "''")
            + "'); "
            "$s.TargetPath = '"
            + str(target).replace("'", "''")
            + "'; "
            "$s.WorkingDirectory = '"
            + str(target.parent).replace("'", "''")
            + "'; "
            "$s.Save()"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            check=False,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0 and lnk.is_file()
    except OSError:
        return False


def _ui_root() -> tk.Tk:
    root = tk.Tk()
    root.withdraw()
    return root


def main() -> int:
    src = find_payload()
    if src is None:
        _ui_root()
        where = resource_dir()
        messagebox.showerror(
            APP_NAME,
            f"Fichier introuvable : {where / EXE_NAME}\nReconstruisez l'installeur.",
        )
        return 1

    dest_dir = install_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / EXE_NAME
    dest.write_bytes(src.read_bytes())

    shortcuts: list[str] = []
    desk = desktop_dir()
    if desk and create_shortcut(desk / f"{APP_NAME}.lnk", dest):
        shortcuts.append("Bureau")
    menu = start_menu_dir()
    if menu and create_shortcut(menu / f"{APP_NAME}.lnk", dest):
        shortcuts.append("menu Démarrer")

    _ui_root()
    if shortcuts:
        extra = "Un raccourci a été ajouté : " + ", ".join(shortcuts) + "."
    else:
        extra = (
            "Le raccourci n'a pas pu être créé (droits du Bureau).\n"
            f"Lancez le logiciel ici :\n{dest}"
        )
    launch = messagebox.askyesno(
        APP_NAME,
        f"{APP_NAME} a été installé dans :\n{dest_dir}\n\n"
        f"{extra}\n\n"
        "Lancer le logiciel maintenant ?",
    )
    if launch:
        os.startfile(str(dest))  # type: ignore[attr-defined]
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        try:
            _ui_root()
            messagebox.showerror(APP_NAME, f"L'installation a échoué :\n{exc}")
        except Exception:
            pass
        raise SystemExit(1)
