"""Small shared helpers; underscore files are not discovered as plugins."""
from pathlib import Path
import json
from core import outcome


def declaration(name, description, properties, required=("action",)):
    result = {"name": name, "description": description, "tier": "extra", "brief": description,
            "parameters": {"type": "OBJECT", "properties": properties, "required": list(required)}}
    if name in {"knowledge_library", "research_assistant", "document_studio", "media_assistant", "project_assistant"}:
        result["behavior"] = "NON_BLOCKING"
    return result


def string(description="", choices=None):
    prop = {"type": "STRING", "description": description}
    if choices:
        prop["enum"] = choices
    return prop


def report(name, data):
    return outcome.done(name, verified=True) + "\n" + json.dumps(data, ensure_ascii=False, default=str)


def new_output(path):
    target = Path(path).expanduser().resolve()
    if not path or target.exists():
        raise ValueError("Choose a new output filename; existing files are never overwritten")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def read_document(path, limit=150000):
    path = Path(path).resolve(strict=True)
    if path.stat().st_size > 25 * 1024 * 1024:
        raise ValueError("Document exceeds the 25 MB limit")
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages[:200])[:limit]
    if suffix == ".docx":
        from docx import Document
        return "\n".join(p.text for p in Document(path).paragraphs)[:limit]
    if suffix not in {".txt", ".md", ".csv", ".json", ".py", ".log", ".rst", ".ts", ".tsx", ".js",
                      ".jsx", ".html", ".css", ".toml", ".yaml", ".yml", ".go", ".rs", ".java", ".cpp", ".h", ".cs"}:
        raise ValueError("Supported: text, Markdown, CSV, JSON, Python, logs, PDF and DOCX")
    return path.read_text(encoding="utf-8", errors="replace")[:limit]
