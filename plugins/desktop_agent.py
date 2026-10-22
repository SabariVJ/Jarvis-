"""Inspect Windows accessibility controls and act on a recent exact selection."""
import json
import time
import uuid
from core import appidentity, outcome
from core.automation_runtime import get_runtime
from plugins._automation_common import declaration, string, report

PLUGIN = declaration("desktop_agent",
    "Inspect a Windows app's accessible controls, focus or arrange its window. "
    "Before clicking or typing, inspect and use the returned snapshot and exact control ID. "
    "Use whatsapp_control for WhatsApp interactions.",
    {"action": string(choices=["windows", "inspect", "focus", "arrange", "click", "type"]),
     "app": string("Exact application name"), "window": {"type": "INTEGER"},
     "snapshot": string(), "control_id": {"type": "INTEGER"}, "text": string(),
     "position": string(choices=["left", "right", "maximize", "restore"])})
_snapshots = {}


def _windows(app):
    from pywinauto import Desktop
    pids = {m.pid for m in appidentity.running(app, include_self=True) if m.score >= 30}
    return [w for w in Desktop(backend="uia").windows()
            if w.process_id() in pids and w.is_visible()]


def _password(control):
    try:
        return bool(control.element_info.element.CurrentIsPassword)
    except Exception:
        return control.element_info.control_type in {"Edit", "ComboBox"}


def run(parameters, player=None, session_memory=None):
    try:
        with get_runtime().desktop(session_memory):
            return _run(parameters)
    except Exception as exc:
        return outcome.failed("desktop_agent", str(exc))


def _run(p):
    action = p.get("action")
    app = p.get("app", "").strip()
    if not app:
        raise ValueError("Provide the application name")
    windows = _windows(app)
    if action == "windows":
        return report("desktop_agent", [{"window": w.handle, "title": w.window_text(), "pid": w.process_id()} for w in windows])
    if p.get("window"):
        windows = [w for w in windows if w.handle == p["window"]]
    if len(windows) != 1:
        raise ValueError("No unique application window; use windows and specify its handle")
    window = windows[0]
    if action == "focus":
        window.set_focus()
        return report("desktop_agent", {"focused": window.has_focus()}) if window.has_focus() else outcome.unverified("desktop_agent", "Focus not confirmed")
    if action == "arrange":
        import win32api
        import win32gui
        position = p.get("position", "")
        if position in {"maximize", "restore"}:
            win32gui.ShowWindow(window.handle, 3 if position == "maximize" else 9)
        elif position in {"left", "right"}:
            rect = win32api.GetMonitorInfo(win32api.MonitorFromWindow(window.handle))["Work"]
            left, top, right, bottom = rect
            width = (right - left) // 2
            win32gui.ShowWindow(window.handle, 9)
            win32gui.MoveWindow(window.handle, left if position == "left" else left + width,
                                top, width, bottom - top, True)
        else:
            raise ValueError("Choose left, right, maximize or restore")
        return report("desktop_agent", {"rectangle": list(win32gui.GetWindowRect(window.handle))})
    controls = []
    for item in window.descendants():
        try:
            if item.is_visible() and not _password(item) and item.element_info.control_type in {
                "Button", "Edit", "ComboBox", "MenuItem", "ListItem", "TabItem", "Text", "CheckBox"}:
                controls.append(item)
        except Exception:
            continue
        if len(controls) >= 250:
            break
    if action == "inspect":
        now = time.monotonic()
        for token in list(_snapshots):
            if now - _snapshots[token]["at"] > 30:
                del _snapshots[token]
        token = uuid.uuid4().hex[:12]
        rows = [{"control_id": i, "name": c.element_info.name,
                 "type": c.element_info.control_type, "enabled": c.is_enabled()}
                for i, c in enumerate(controls)]
        _snapshots[token] = {"at": now, "window": window.handle, "pid": window.process_id(),
                             "controls": [{"runtime_id": c.element_info.runtime_id,
                                           "name": c.element_info.name,
                                           "type": c.element_info.control_type} for c in controls]}
        return report("desktop_agent", {"snapshot": token, "expires_seconds": 30}) + "\n" + outcome.untrusted("Windows accessibility", json.dumps(rows))
    if action not in {"click", "type"}:
        raise ValueError("Unknown desktop action")
    import psutil
    if psutil.Process(window.process_id()).name().casefold().startswith("whatsapp"):
        raise ValueError("Use whatsapp_control to preserve exact contact verification")
    saved = _snapshots.pop(p.get("snapshot", ""), None)
    if not saved or time.monotonic() - saved["at"] > 30 or saved["window"] != window.handle or saved["pid"] != window.process_id():
        raise ValueError("Snapshot missing, expired or belongs to another window; inspect again")
    index = p.get("control_id", -1)
    if not isinstance(index, int) or not 0 <= index < len(saved["controls"]):
        raise ValueError("Invalid control ID")
    identity = saved["controls"][index]
    if not identity["runtime_id"]:
        raise ValueError("Control has no stable accessibility identity")
    matching = [c for c in controls if c.element_info.runtime_id == identity["runtime_id"]
                and c.element_info.name == identity["name"]
                and c.element_info.control_type == identity["type"]]
    if len(matching) != 1:
        raise ValueError("Control changed; inspect again")
    item = matching[0]
    if not item.is_visible() or not item.is_enabled() or _password(item):
        raise ValueError("Control is no longer actionable")
    window.set_focus()
    if action == "type":
        if item.element_info.control_type not in {"Edit", "ComboBox"}:
            raise ValueError("Selected control is not an editable field")
        text = str(p.get("text", ""))
        item.set_edit_text(text)
        if item.get_value() == text:
            return report("desktop_agent", {"text_set": True})
    else:
        if item.element_info.control_type == "Text":
            raise ValueError("Select an actionable control, not a text label")
        item.click_input()
    return outcome.unverified("desktop_agent", "Input sent; inspect the resulting application state before continuing")
