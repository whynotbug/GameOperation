from __future__ import annotations

import json
import time
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pynput import keyboard, mouse
try:
    import win32gui  # type: ignore
except Exception:  # pragma: no cover
    win32gui = None  # type: ignore


EventType = Literal[
    "key_press",
    "key_release",
    "mouse_move",
    "mouse_click",
    "mouse_scroll",
]


@dataclass
class RecordedEvent:
    t: EventType
    dt: float
    data: Dict[str, Any]


class ActionRecorder:
    def __init__(self, target_hwnd: Optional[int] = None) -> None:
        self._events: List[RecordedEvent] = []
        self._start_ts: Optional[float] = None
        self._target_hwnd = target_hwnd
        self._stop_event: threading.Event = threading.Event()
        self._kb_listener: Optional[keyboard.Listener] = None
        self._ms_listener: Optional[mouse.Listener] = None

    def _now_delta(self) -> float:
        assert self._start_ts is not None
        return time.perf_counter() - self._start_ts

    def record(self, output_file: Optional[Path] = None) -> List[Dict[str, Any]]:
        self._events.clear()
        self._start_ts = time.perf_counter()

        kb_listener = keyboard.Listener(
            on_press=self._on_key_press, on_release=self._on_key_release
        )
        ms_listener = mouse.Listener(
            on_move=self._on_move, on_click=self._on_click, on_scroll=self._on_scroll
        )

        self._kb_listener = kb_listener
        self._ms_listener = ms_listener
        kb_listener.start()
        ms_listener.start()

        try:
            while not self._stop_event.is_set():
                time.sleep(0.05)
        finally:
            kb_listener.stop()
            ms_listener.stop()

        payload = [asdict(e) for e in self._events]
        if output_file is not None:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return payload

    # Keyboard handlers
    def _on_key_press(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        if not self._accept_event():
            return
        self._events.append(
            RecordedEvent(
                t="key_press",
                dt=self._now_delta(),
                data={"key": _key_to_str(key)},
            )
        )

    def _on_key_release(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        # Stop recording if ESC is released
        if _key_to_str(key) == "Key.esc":
            self._events.append(
                RecordedEvent(
                    t="key_release",
                    dt=self._now_delta(),
                    data={"key": _key_to_str(key)},
                )
            )
            self._stop_event.set()
            return
        if not self._accept_event():
            return
        self._events.append(
            RecordedEvent(
                t="key_release",
                dt=self._now_delta(),
                data={"key": _key_to_str(key)},
            )
        )

    # Mouse handlers
    def _on_move(self, x: int, y: int) -> None:
        if not self._accept_event():
            return
        self._events.append(RecordedEvent(t="mouse_move", dt=self._now_delta(), data={"x": x, "y": y}))

    def _on_click(self, x: int, y: int, button: mouse.Button, pressed: bool) -> None:
        if not self._accept_event():
            return
        self._events.append(
            RecordedEvent(
                t="mouse_click",
                dt=self._now_delta(),
                data={"x": x, "y": y, "button": button.name, "pressed": pressed},
            )
        )

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        if not self._accept_event():
            return
        self._events.append(
            RecordedEvent(
                t="mouse_scroll",
                dt=self._now_delta(),
                data={"x": x, "y": y, "dx": dx, "dy": dy},
            )
        )

    def _accept_event(self) -> bool:
        if self._target_hwnd is None or win32gui is None:
            return True
        try:
            fg = win32gui.GetForegroundWindow()
            return int(fg) == int(self._target_hwnd)
        except Exception:
            return True

    def request_stop(self) -> None:
        self._stop_event.set()


class ActionReplayer:
    def __init__(self) -> None:
        self._keyboard = keyboard.Controller()
        self._mouse = mouse.Controller()

    def replay(self, input_file: Path) -> None:
        raw = json.loads(input_file.read_text(encoding="utf-8"))
        events = [RecordedEvent(**e) for e in raw]

        if not events:
            return

        start = time.perf_counter()
        for ev in events:
            target = start + ev.dt
            _sleep_until(target)
            self._dispatch(ev)

    def _dispatch(self, ev: RecordedEvent) -> None:
        if ev.t == "key_press":
            k = _str_to_key(ev.data["key"])
            self._keyboard.press(k)
        elif ev.t == "key_release":
            k = _str_to_key(ev.data["key"])
            self._keyboard.release(k)
        elif ev.t == "mouse_move":
            self._mouse.position = (ev.data["x"], ev.data["y"])
        elif ev.t == "mouse_click":
            btn = getattr(mouse.Button, ev.data["button"])  # left/right/middle
            if ev.data["pressed"]:
                self._mouse.press(btn)
            else:
                self._mouse.release(btn)
        elif ev.t == "mouse_scroll":
            self._mouse.scroll(ev.data["dx"], ev.data["dy"])


def _sleep_until(target_ts: float) -> None:
    while True:
        now = time.perf_counter()
        remaining = target_ts - now
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.01))


def _key_to_str(key: keyboard.Key | keyboard.KeyCode) -> str:
    try:
        if isinstance(key, keyboard.KeyCode) and key.char is not None:
            return key.char
        return str(key)
    except Exception:
        return str(key)


def _str_to_key(s: str) -> keyboard.Key | keyboard.KeyCode:
    # Map back common special keys, otherwise assume a single char
    if s.startswith("Key."):
        name = s.split(".", 1)[1]
        return getattr(keyboard.Key, name)
    if len(s) == 1:
        return keyboard.KeyCode.from_char(s)
    # Fallback to KeyCode for strings
    return keyboard.KeyCode.from_char(s)


