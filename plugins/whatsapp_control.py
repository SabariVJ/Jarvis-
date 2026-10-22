
"""
MARK LVI - WhatsApp Windows desktop plugin.

Features:
- Search saved WhatsApp contacts
- Open and verify a matching chat
- Initiate explicitly requested voice/video calls after contact verification

Dependencies: pywinauto, pyautogui, pyperclip
"""

import os
import time
from core import outcome
from core.automation_runtime import get_runtime
from core.automation_metrics import measure, timed


def _wait(probe, timeout, interval=0.15):
    deadline = time.monotonic() + timeout
    while True:
        get_runtime().check_cancelled(None)
        value = probe()
        if value:
            return value
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


PLUGIN = {
    "name": "whatsapp_control",
    "description": (
        "Control the Windows WhatsApp desktop application. "
        "Use when the user asks to search for a contact on WhatsApp, "
        "open a WhatsApp conversation, make a WhatsApp voice call, "
        "or start a WhatsApp video call. "
        "Actions: search, open_chat, voice_call, video_call. "
        "Require the saved contact name and verify the chat header "
        "before placing a requested call."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": [
                    "search",
                    "open_chat",
                    "voice_call",
                    "video_call",
                ],
                "description": "The requested WhatsApp action.",
            },
            "contact": {
                "type": "STRING",
                "description": "Exact saved WhatsApp contact name.",
            },
        },
        "required": ["action", "contact"],
    },
}


def _norm(value):
    return " ".join(str(value or "").casefold().split())


def _ui():
    from pywinauto import Desktop
    return Desktop(backend="uia")



def _app_windows():
    """Use native HWND/process discovery, then wrap only the actual app in UIA."""
    import psutil
    from core import appidentity
    desktop = None
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            name = (proc.info.get('name') or '').casefold()
            if not (name.startswith('whatsapp') or name == 'applicationframehost.exe'):
                continue
            for hwnd in appidentity._hwnds_for(proc.info['pid']):
                get_runtime().check_cancelled(None)
                desktop = desktop or _ui()
                window = desktop.window(handle=hwnd).wrapper_object()
                if not window.is_visible() or window.process_id() != proc.info['pid']:
                    continue
                if name == 'applicationframehost.exe' and not window.window_text().strip().casefold().startswith('whatsapp'):
                    continue
                yield window
        except InterruptedError:
            raise
        except Exception:
            continue


@timed('window_detection')
def _window():
    for window in _app_windows():
        if window.window_text().strip().casefold().startswith('whatsapp'):
            return window

    return None


def _ensure_window():
    """Open WhatsApp if it is not already visible."""
    found = _window()

    if found:
        return found

    import pyautogui as pg

    try:
        with get_runtime().side_effect('whatsapp_app_launch'):
            os.startfile("whatsapp:")

    except OSError:
        pg.press("win")
        time.sleep(0.6)
        pg.write("WhatsApp", interval=0.06)
        with get_runtime().side_effect('whatsapp_app_launch_fallback'):
            pg.press("enter")

    found = _wait(_window, 20, 0.25)
    if found:
        return found

    raise RuntimeError(
        "WhatsApp Desktop did not open. Install and "
        "sign in to WhatsApp, then try again."
    )


def _items(window, control_types=None):
    try:
        with measure('accessibility_scan', control_types=list(control_types or [])) as metrics:
            if control_types:
                items = [item for kind in control_types for item in window.descendants(control_type=kind)]
            else:
                items = window.descendants()
            metrics['controls_returned'] = len(items)
            return items
    except InterruptedError:
        raise
    except Exception:
        return []


def _props(control):
    try:
        return (
            (control.element_info.name or "").strip(),
            control.element_info.control_type or "",
            control.rectangle(),
        )
    except Exception:
        return "", "", None


@timed('search_field_detection')
def _search_input(window):
    """Find a search edit field in the left chat panel."""
    wr = window.rectangle()
    candidates = []

    for item in _items(window, ('Edit', 'ComboBox')):
        name, kind, rect = _props(item)

        if rect is None or kind not in (
            "Edit",
            "ComboBox",
        ):
            continue

        x = rect.mid_point().x
        y = rect.mid_point().y

        if (
            wr.left < x < wr.left + wr.width() * 0.60
            and wr.top < y < wr.top + wr.height() * 0.40
        ):
            rank = (
                0 if "search" in name.casefold() else 1,
                rect.top,
            )
            candidates.append((rank, item))

    if not candidates:
        raise RuntimeError(
            "WhatsApp search field was not accessible. "
            "Open the Chats screen and retry."
        )

    return sorted(
        candidates,
        key=lambda entry: entry[0],
    )[0][1]




@timed('contact_results_scan')
def _matches(window, contact):
    """
    Find WhatsApp chat results using accessible names.

    Example Windows UIA label:
        11 unread messages Appa Yesterday

    Match the contact's complete name, not a substring
    of another person's name.
    """
    import re

    target = _norm(contact)
    if not target:
        return []

    wr = window.rectangle()

    date_pattern = (
        r"(?:today|yesterday|monday|tuesday|"
        r"wednesday|thursday|friday|saturday|sunday|"
        r"\d{1,2}[:/.-]\d{1,2})"
    )

    pattern = re.compile(
        r"^(?:view status\s+)?"
        r"(?:\d+\s+unread messages?\s+)?"
        + re.escape(target)
        + rf"(?=\s+{date_pattern}\b|$)",
        re.IGNORECASE,
    )

    candidates = []

    for item in _items(window, ('DataItem',)):
        try:
            name, kind, rect = _props(item)

            if kind != "DataItem" or rect is None:
                continue

            label = _norm(name)

            if not pattern.match(label):
                continue

            x = rect.mid_point().x
            y = rect.mid_point().y

            # Only accept controls inside the chat panel.
            if not (
                wr.left < x <
                wr.left + wr.width() * 0.60
            ):
                continue

            if not (
                wr.top + 120 < y < wr.bottom - 70
            ):
                continue

            if not (0 < rect.height() <= 130):
                continue

            # Prefer the contact-name element rather than
            # the larger "View status" container.
            priority = (
                1 if label.startswith("view status ") else 0
            )

            candidates.append(
                (priority, rect.height(), y, item)
            )

        except Exception:
            continue

    candidates.sort(key=lambda row: row[:3])

    results = []
    used_y = []

    for _, _, y, item in candidates:
        if any(abs(y - old_y) < 50 for old_y in used_y):
            continue

        results.append(item)
        used_y.append(y)

    return results



@timed('contact_search')
def _search(window, contact):
    """Search using the desktop UI and clipboard paste."""
    import pyautogui as pg
    import pyperclip

    with get_runtime().side_effect('whatsapp_focus'):
        window.set_focus()

    def ready():
        try:
            field = _search_input(window)
            return field if field.is_visible() and field.is_enabled() else None
        except Exception:
            return None
    field = _wait(ready, 15)
    if field is None:
        raise RuntimeError("WhatsApp is loading, signed out or not on Chats. Search was not ready within 15 seconds.")
    with get_runtime().side_effect('whatsapp_search_focus'):
        field.click_input()

    previous_clipboard = pyperclip.paste()

    try:
        get_runtime().effect('whatsapp_search_input')
        pg.hotkey("ctrl", "a")
        pyperclip.copy(contact)
        pg.hotkey("ctrl", "v")
        time.sleep(0.35)

    finally:
        pyperclip.copy(previous_clipboard)

    # Give the filter a short settling period, then wait for actual results.
    time.sleep(0.4)
    results = _wait(lambda: _matches(window, contact), 4) or []
    if results:
        time.sleep(0.25)
        results = _matches(window, contact)
    return results


@timed('header_verification')
def _header_matches(window, contact):
    """Check that the opened chat header shows the contact."""
    wr = window.rectangle()
    try:
        panel_right = _search_input(window).rectangle().right
    except Exception:
        return False  # Without the chat-panel boundary the header is unverified.

    for item in _items(window, ('Text', 'Button')):
        name, kind, rect = _props(item)

        if rect is None or kind not in ("Text", "Button") or _norm(name) != _norm(contact):
            continue

        x = rect.mid_point().x
        y = rect.mid_point().y

        if (
            panel_right < x < wr.right
            and wr.top < y
            < wr.top + min(160, wr.height() * 0.25)
        ):
            return True

    return False


@timed('chat_selection')
def _open_chat(window, contact, results):
    if not results:
        return (
            f"No exact WhatsApp contact named "
            f"'{contact}' was found."
        )

    if len(results) > 1:
        return (
            f"Multiple results match '{contact}'. "
            "Please disambiguate the contact manually."
        )

    with get_runtime().side_effect('whatsapp_chat_selection'):
        results[0].click_input()
    if not _wait(lambda: _header_matches(window, contact), 5):
        return (
            f"I opened a search result but could not "
            f"verify the chat header for '{contact}'. "
            "No call was placed."
        )

    return None


@timed('call_control_detection')
def _call_button(window, video=False):
    """Find a labeled call control near the chat header."""
    wr = window.rectangle()

    direct = []
    generic = []

    for item in _items(window, ('Button',)):
        name, kind, rect = _props(item)

        if kind != "Button" or rect is None:
            continue

        x = rect.mid_point().x
        y = rect.mid_point().y

        if not (
            x > wr.left + wr.width() * 0.45
            and wr.top < y
            < wr.top + wr.height() * 0.27
        ):
            continue

        label = _norm(name)

        if any(
            text in label
            for text in ("end", "decline", "hang up")
        ):
            continue

        if video:
            if label in (
                "video",
                "video call",
                "start video call",
            ):
                direct.append(item)
        else:
            if label in (
                "voice call",
                "audio call",
                "start voice call",
                "start audio call",
                "phone call",
            ):
                direct.append(item)

        if label in ("call", "calls"):
            generic.append(item)

    if direct:
        return direct[0], False

    if generic:
        return generic[0], True

    return None, False


def _call_menu_option(window, video):
    """Find voice/video action in an opened call menu."""
    wr = window.rectangle()

    for item in _items(window, ('Button', 'MenuItem', 'ListItem', 'Text')):
        name, kind, rect = _props(item)

        if kind not in (
            "Button",
            "MenuItem",
            "ListItem",
            "Text",
        ) or rect is None:
            continue

        x = rect.mid_point().x
        y = rect.mid_point().y

        if not (
            x > wr.left + wr.width() * 0.45
            and wr.top < y
            < wr.top + wr.height() * 0.55
        ):
            continue

        label = _norm(name)

        if video and label in (
            "video",
            "video call",
        ):
            return item

        if not video and label in (
            "voice",
            "voice call",
            "audio",
            "audio call",
        ):
            return item

    return None


@timed('active_call_detection')
def _active_call():
    for window in _app_windows():
        try:
            for item in _items(window, ('Button',)):
                name, kind, _ = _props(item)
                if kind == "Button" and _norm(name) in {"end call", "hang up", "leave call"} and item.is_visible():
                    return True
        except Exception:
            continue
    return False


def run(parameters, player=None, session_memory=None):
    rt = get_runtime()
    try:
        with rt.bound_context(session_memory or rt.context()), rt.desktop(session_memory):
            if str(parameters.get('action', '')).endswith('_call'):
                rt.authorize('whatsapp_control', parameters, session_memory or rt.context())
            with measure('whatsapp_execution', session_memory):
                return _run(parameters, player, session_memory)
    except Exception as exc:
        return outcome.failed("whatsapp_control", str(exc))


def _run(parameters, player=None, session_memory=None):
    action = str(
        parameters.get("action", "")
    ).strip().lower()

    contact = str(
        parameters.get("contact", "")
    ).strip()

    valid_actions = (
        "search",
        "open_chat",
        "voice_call",
        "video_call",
    )

    if action not in valid_actions:
        return "Invalid WhatsApp action."

    if not contact or len(contact) > 120:
        return "Please provide the exact saved contact name."

    try:
        if action.endswith("_call") and _active_call():
            return outcome.failed("whatsapp_control", "A WhatsApp call is already active")
        window = _ensure_window()
        results = _search(window, contact)

        if action == "search":
            if not results:
                return (
                    f"No exact contact named '{contact}' "
                    "was visible in WhatsApp search."
                )

            if len(results) > 1:
                return (
                    f"Multiple results named '{contact}' "
                    "were found."
                )

            return f"Found '{contact}' in WhatsApp search."

        error = _open_chat(
            window,
            contact,
            results,
        )

        if error:
            return error

        if action == "open_chat":
            return outcome.done("whatsapp_control", contact=contact, chat_verified=True)

        video = action == "video_call"

        

        # Recheck target immediately before placing the call.
        if not _header_matches(window, contact):
            return (
                "The chat changed before the call. "
                "No call was placed."
            )

        button, opens_menu = _call_button(
            window,
            video,
        )

        if button is None:
            return (
                "The chat was verified, but the call "
                "button was not exposed to Windows "
                "accessibility. Please call manually."
            )

        rt = get_runtime()
        rt.check_cancelled(session_memory)
        if not _header_matches(window, contact):
            return outcome.failed("whatsapp_control", "Chat header changed before clicking")
        blocked = rt.reserve_call(_norm(contact), session_memory)
        if blocked:
            return outcome.failed("whatsapp_control", blocked)
        # The reservation is retained even if clicking raises: the outcome may
        # be uncertain. Never retry a call in an exception handler.
        with rt.side_effect('whatsapp_call_click', session_memory, contact_verified=True, video=video):
            button.click_input()

        if opens_menu:
            time.sleep(0.5)

            option = _call_menu_option(
                window,
                video,
            )

            if option is None:
                return (
                    "Call options were opened, but I "
                    "could not identify the correct "
                    "voice/video option. Select it manually."
                )

            rt.check_cancelled(session_memory)
            if not _header_matches(window, contact):
                return outcome.failed("whatsapp_control", "Chat changed while call menu was open; stopped")
            with rt.side_effect('whatsapp_call_menu_click', session_memory, contact_verified=True, video=video):
                option.click_input()

        kind = "video" if video else "voice"

        return outcome.unverified("whatsapp_control", "Pressed call control once; outgoing call not independently verified",
                                  contact=contact, kind=kind, retry_allowed=False)

    except Exception as exc:
        return (
            "WhatsApp automation stopped safely: "
            f"{type(exc).__name__}: {exc}"
        )
