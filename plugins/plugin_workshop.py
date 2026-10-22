"""Prepare reviewable plugin drafts; never import generated Python."""
import ast
from pathlib import Path
import re
from core import outcome
from plugins._automation_common import declaration, string, report, new_output

PLUGIN = declaration("plugin_workshop",
    "Draft a new Python JARVIS plugin with the current AI provider or statically check a draft. "
    "Writes outside live plugins; never imports, installs or executes generated code.",
    {"action": string(choices=["draft", "check"]), "name": string(), "description": string(),
     "path": string("Draft to check"), "output": string("New .py draft path outside plugins")})


def check(source):
    tree = ast.parse(source)
    meta = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PLUGIN" for t in node.targets):
            meta = ast.literal_eval(node.value)
    if not isinstance(meta, dict) or not re.fullmatch(r"[A-Za-z_]\w{0,63}", str(meta.get("name", ""))):
        raise ValueError("Missing valid literal PLUGIN metadata")
    if not meta.get("description") or meta.get("parameters", {}).get("type") != "OBJECT":
        raise ValueError("PLUGIN needs a description and OBJECT parameters")
    if not any(isinstance(n, ast.FunctionDef) and n.name == "run" and n.args.args
               and n.args.args[0].arg == "parameters" for n in tree.body):
        raise ValueError("Missing def run(parameters, ...) entry point")
    compile(tree, "<plugin-draft>", "exec")
    return {"name": meta["name"], "syntax_valid": True, "metadata_valid": True,
            "security_reviewed": False, "executed": False}


def run(parameters, player=None, session_memory=None):
    try:
        action = parameters.get("action")
        if action == "check":
            source = Path(parameters.get("path", "")).read_text(encoding="utf-8")
            return report("plugin_workshop", check(source))
        if action != "draft":
            raise ValueError("Unknown workshop action")
        name = parameters.get("name", "")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
            raise ValueError("Use a short snake_case plugin name")
        target = new_output(parameters.get("output", ""))
        live = Path(__file__).resolve().parent
        if target.suffix != ".py" or target.is_relative_to(live):
            raise ValueError("Save a new .py draft outside the live plugins folder")
        from core import gemini
        source = gemini.text("Write a JARVIS Python plugin named " + name + ". Return only Python. "
            "Use a literal PLUGIN dict with name, description, parameters (type OBJECT). "
            "Entry point run(parameters, player=None, session_memory=None) returns a result string. "
            "Import optional dependencies lazily. Use core.gemini for AI calls, core.outcome for results. "
            "No network/GUI/file effects at import time. Requirement: " + parameters.get("description", ""),
            tier=gemini.SMART, timeout_ms=60000)
        source = re.sub(r"^```(?:python)?\s*|\s*```$", "", source.strip())
        result = check(source)
        with target.open("x", encoding="utf-8") as handle:
            handle.write(source + "\n")
        return report("plugin_workshop", {"draft": str(target), **result})
    except Exception as exc:
        return outcome.failed("plugin_workshop", str(exc))
