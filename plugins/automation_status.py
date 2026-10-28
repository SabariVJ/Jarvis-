"""Explain current system load, automation capabilities and measured latency."""
import importlib.util
import shutil
from core import outcome
from core.automation_runtime import get_runtime
from plugins._automation_common import declaration, string, report

PLUGIN = declaration("automation_status",
    "Show JARVIS task context, measured tool latency, available dependencies, or CPU/RAM/process load to investigate slowness.",
    {"action": string(choices=["status", "context", "performance", "capabilities"])})


def run(parameters, player=None, session_memory=None):
    try:
        rt = get_runtime()
        action = parameters.get("action", "status")
        if action == 'capabilities':
            catalog = rt.catalog_provider() if callable(getattr(rt, 'catalog_provider', None)) else getattr(rt, 'catalog', [])
            return report('automation_status', {'enabled_tools': [
                {'name': tool['name'], 'parameters': tool.get('parameters', {})} for tool in catalog],
                'note': 'Current enabled registry inventory; provider budgets may expose a subset.'})
        if action == "context":
            return report("automation_status", rt.context())
        if action == "performance":
            from core.performance_report import performance_report
            return report("automation_status", {"tool_timings": rt.stats(),
                'phases': performance_report(rt.path)})
        if action != "status":
            raise ValueError("Unknown status action")
        import psutil
        processes = []
        for proc in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                processes.append({"pid": proc.pid, "name": proc.info["name"],
                                  "ram_mb": round(proc.info["memory_info"].rss / 1048576, 1)})
            except (psutil.Error, AttributeError):
                pass
        return report("automation_status", {"cpu_percent": psutil.cpu_percent(interval=0.2),
            "ram_percent": psutil.virtual_memory().percent,
            "largest_processes": sorted(processes, key=lambda p: p["ram_mb"], reverse=True)[:10],
            "dependencies": {name: bool(importlib.util.find_spec(name)) for name in
                             ("pywinauto", "ddgs", "docx", "openpyxl", "pdfplumber", "mcp", "imageio_ffmpeg")},
            "ffmpeg_on_path": bool(shutil.which("ffmpeg")),
            "recipes": len(rt.records("recipe")), "triggers": len(rt.records("trigger"))})
    except Exception as exc:
        return outcome.failed("automation_status", str(exc))
