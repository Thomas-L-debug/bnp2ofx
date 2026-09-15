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


def install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME


def desktop_dir() -> Path:
    home = Path.home()
    for candidate in (home / "Desktop", home / "Bureau"):
        if candidate.is_dir():
            return candidate
    public = Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop"
    return public


def start_menu_dir() -> Path:
    programs = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    return programs / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def create_shortcut(lnk: Path, target: Path) -> None:
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
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def main() -> int:
    src = resource_dir() / EXE_NAME
    if not src.exists():
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            APP_NAME,
            f"Fichier introuvable : {src}\nReconstruisez l'installeur.",
        )
        return 1

    dest_dir = install_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / EXE_NAME
    dest.write_bytes(src.read_bytes())

    create_shortcut(desktop_dir() / f"{APP_NAME}.lnk", dest)
    create_shortcut(start_menu_dir() / f"{APP_NAME}.lnk", dest)

    root = tk.Tk()
    root.withdraw()
    launch = messagebox.askyesno(
        APP_NAME,
        f"{APP_NAME} a été installé dans :\n{dest_dir}\n\n"
        "Un raccourci a été ajouté sur le Bureau et dans le menu Démarrer.\n\n"
        "Lancer le logiciel maintenant ?",
    )
    if launch:
        os.startfile(str(dest))  # type: ignore[attr-defined]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
