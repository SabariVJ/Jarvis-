"""Resolve common Windows apps from installed paths before Start search."""
import os
from pathlib import Path
import shutil


def executable(name):
    folded = "".join(c for c in name.casefold() if c.isalnum())
    aliases = {"googlechrome": "chrome", "visualstudiocode": "code", "vscode": "code",
               "microsoftedge": "msedge", "edge": "msedge"}
    key = aliases.get(folded, folded.removesuffix("exe"))
    direct = Path(name)
    if direct.is_absolute() and direct.suffix.lower() == ".exe" and direct.is_file():
        return str(direct)
    found = shutil.which(key + ".exe")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", "."))
    roots = [Path(os.environ.get(env, ".")) for env in ("ProgramFiles", "ProgramFiles(x86)")]
    relative = {"chrome": "Google/Chrome/Application/chrome.exe",
                "msedge": "Microsoft/Edge/Application/msedge.exe",
                "code": "Microsoft VS Code/Code.exe"}
    candidates = []
    if key in relative:
        candidates.extend(root / relative[key] for root in roots)
        candidates.extend([local / relative[key], local / "Programs" / relative[key]])
    if key == "discord":
        candidates.extend(sorted((local / "Discord").glob("app-*/Discord.exe"),
                                 key=lambda p: p.stat().st_mtime, reverse=True))
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{key}.exe") as entry:
                    candidates.append(Path(winreg.QueryValue(entry, None).strip('"')))
            except OSError:
                pass
    except ImportError:
        pass
    return next((str(p) for p in candidates if p.is_file()), None)
