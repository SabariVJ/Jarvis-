"""Opt-in, local full-text document index with source references."""
import json
from pathlib import Path
import re
import time
import stat
from core import outcome
from core.automation_runtime import get_runtime
from plugins._automation_common import declaration, string, report, read_document

PLUGIN = declaration("knowledge_library",
    "Index a user-selected file or folder locally and search its document contents with source paths. "
    "No automatic whole-disk indexing. Use forget to remove an indexed root.",
    {"action": string(choices=["index", "search", "sources", "forget"]),
     "path": string("Exact file or folder selected by the user"), "query": string()})
EXTENSIONS = {".txt", ".md", ".pdf", ".docx", ".csv", ".py", ".rst"}
EXCLUDE = {".git", ".venv", "venv", "node_modules", "__pycache__", "config", "memory", ".ssh", ".aws"}


def _reparse(path):
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) &
                                     getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024))


def _allowed(path):
    lower = path.name.lower()
    return (path.suffix.lower() in EXTENSIONS and not any(part.lower() in EXCLUDE for part in path.parts)
            and not any(word in lower for word in ("secret", "credential", "token", "password")))


def _schema(db):
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS knowledge USING fts5(root UNINDEXED, path UNINDEXED, section UNINDEXED, body)")


def run(parameters, player=None, session_memory=None):
    rt = get_runtime()
    action = parameters.get("action")
    try:
        with rt.db() as db:
            _schema(db)
        if action == "search":
            terms = re.findall(r"\w+", parameters.get("query", ""), flags=re.UNICODE)[:20]
            if not terms:
                raise ValueError("Provide search words")
            query = " OR ".join('"' + t + '"' for t in terms)
            with rt.db() as db:
                rows = db.execute("SELECT path,section,snippet(knowledge,3,'[',']','…',48) AS excerpt "
                                  "FROM knowledge WHERE knowledge MATCH ? ORDER BY rank LIMIT 8", (query,)).fetchall()
            return report("knowledge_library", {"matches": len(rows)}) + "\n" + outcome.untrusted("local documents", json.dumps([dict(r) for r in rows]))
        if action == "sources":
            return report("knowledge_library", rt.records("knowledge_root"))
        raw = parameters.get("path", "")
        if not raw:
            raise ValueError("Provide a specific file or folder")
        root = Path(raw).expanduser().resolve()
        if action == "forget":
            with rt.db() as db:
                db.execute("DELETE FROM knowledge WHERE root=?", (str(root),))
            rt.delete("knowledge_root", str(root))
            return report("knowledge_library", {"forgotten": str(root)})
        if action != "index" or not root.exists():
            raise ValueError("Choose index and an existing file or folder")
        if root == Path(root.anchor) or root == Path.home():
            raise ValueError("Select a specific folder, not an entire drive or home directory")
        import os
        paths = []
        if root.is_file():
            paths = [root]
        else:
            for directory, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = [d for d in dirs if d.lower() not in EXCLUDE and not _reparse(Path(directory, d))]
                paths.extend(Path(directory, f) for f in files if _allowed(Path(directory, f)))
                if len(paths) > 1000:
                    raise ValueError("Folder exceeds 1000 documents; choose a smaller folder")
        chunks, errors, count, characters = [], [], 0, 0
        for path in paths:
            rt.check_cancelled(session_memory)
            if not _allowed(path) or _reparse(path):
                continue
            try:
                body = read_document(path, 100000)
                characters += len(body)
                if characters > 12_000_000:
                    raise OverflowError("Index exceeds 12 million characters; choose a smaller folder")
                chunks.extend((str(root), str(path), str(i // 2500 + 1), body[i:i+3000])
                              for i in range(0, len(body), 2500))
                count += 1
            except OverflowError:
                raise
            except Exception as exc:
                errors.append({"path": str(path), "error": str(exc)})
        with rt.db() as db:
            db.execute("DELETE FROM knowledge WHERE root=?", (str(root),))
            db.executemany("INSERT INTO knowledge VALUES (?,?,?,?)", chunks)
        info = {"files": count, "chunks": len(chunks), "indexed_at": time.time(), "errors": errors[:30]}
        rt.put("knowledge_root", str(root), info)
        return report("knowledge_library", info)
    except Exception as exc:
        return outcome.failed("knowledge_library", str(exc))
