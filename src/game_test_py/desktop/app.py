from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from PySide6 import QtCore, QtGui, QtWidgets
import ctypes

try:
    import win32gui  # type: ignore
except Exception:  # pragma: no cover - non-windows env
    win32gui = None  # type: ignore
try:
    import win32con  # type: ignore
    import win32process  # type: ignore
    import win32api  # type: ignore
except Exception:  # pragma: no cover
    win32con = None  # type: ignore
    win32process = None  # type: ignore
    win32api = None  # type: ignore

from game_test_py.tools.recorder import ActionRecorder


@dataclass
class WindowInfo:
    hwnd: int
    title: str


def enumerate_windows() -> List[WindowInfo]:
    windows: List[WindowInfo] = []
    if win32gui is None:
        return windows

    def _enum_handler(hwnd: int, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title:
                windows.append(WindowInfo(hwnd=hwnd, title=title))

    win32gui.EnumWindows(_enum_handler, None)
    return windows


class BorderOverlay(QtWidgets.QWidget):
    """Single transparent overlay that draws a red rectangle (no fill)."""

    def __init__(self, color: str = "#ff3b30", width: int = 2) -> None:
        super().__init__(None, QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)
        self.setWindowFlag(QtCore.Qt.WindowDoesNotAcceptFocus, True)
        self._color = QtGui.QColor(color)
        self._width = width

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        pen = QtGui.QPen(self._color)
        pen.setWidth(self._width)
        pen.setCosmetic(True)  # keep width independent of scaling
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        r = self.rect().adjusted(1, 1, -1, -1)
        painter.drawRect(r)

    # helper to mirror previous API used by caller
    def set_geometry(self, x: int, y: int, w: int, h: int) -> None:
        self.setGeometry(x, y, w, h)


class StopPanel(QtWidgets.QWidget):
    stop_requested = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__(None, QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint)
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)
        self.setWindowFlag(QtCore.Qt.WindowDoesNotAcceptFocus, True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        btn = QtWidgets.QPushButton("停止录制")
        btn.clicked.connect(self.stop_requested.emit)
        layout.addWidget(btn)


class MainWindow(QtWidgets.QWidget):
    save_payload = QtCore.Signal(object)
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("操作录制器")
        self.resize(560, 420)

        self._rec_thread: Optional[threading.Thread] = None
        self._replay_thread: Optional[threading.Thread] = None
        self._recording: bool = False
        self._overlay: Optional[BorderOverlay] = None
        self._stop_panel: Optional[StopPanel] = None
        self._border_timer = QtCore.QTimer(self)
        self._border_timer.setInterval(80)
        self._border_timer.timeout.connect(self._tick_follow_window)
        self._target_hwnd: Optional[int] = None

        # 默认存储目录
        self.default_dir = Path("recordings").resolve()
        self.default_dir.mkdir(parents=True, exist_ok=True)
        self.path_edit = QtWidgets.QLineEdit(str(self.default_dir / "sample.json"))
        self.path_browse = QtWidgets.QPushButton("浏览…")
        self.refresh_btn = QtWidgets.QPushButton("刷新窗口")
        self.start_btn = QtWidgets.QPushButton("开始录制")
        self.stop_btn = QtWidgets.QPushButton("停止录制")
        self.stop_btn.setEnabled(False)
        self.list_widget = QtWidgets.QListWidget()
        self.status_label = QtWidgets.QLabel("")

        # 回放区域
        self.play_path = QtWidgets.QComboBox()
        self.play_path.setEditable(True)
        self.play_btn = QtWidgets.QPushButton("开始回放")

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("输出文件(JSON):"))
        path_row = QtWidgets.QHBoxLayout()
        path_row.addWidget(self.path_edit)
        path_row.addWidget(self.path_browse)
        layout.addLayout(path_row)
        layout.addWidget(QtWidgets.QLabel("选择窗口:"))
        layout.addWidget(self.list_widget)

        btns = QtWidgets.QHBoxLayout()
        btns.addWidget(self.refresh_btn)
        btns.addStretch(1)
        btns.addWidget(self.start_btn)
        btns.addWidget(self.stop_btn)
        layout.addLayout(btns)

        layout.addSpacing(12)
        layout.addWidget(QtWidgets.QLabel("回放文件(JSON):"))
        play_row = QtWidgets.QHBoxLayout()
        play_row.addWidget(self.play_path)
        play_row.addWidget(self.play_btn)
        layout.addLayout(play_row)
        layout.addWidget(self.status_label)

        self.path_browse.clicked.connect(self.on_browse_save)
        self.refresh_btn.clicked.connect(self.refresh_windows)
        self.start_btn.clicked.connect(self.on_start)
        self.stop_btn.clicked.connect(self.on_stop)

        self.play_btn.clicked.connect(self.on_play)
        self.refresh_windows()
        self.refresh_recordings()
        self.save_payload.connect(self._prompt_save_payload)

    def refresh_windows(self) -> None:
        self.list_widget.clear()
        for w in enumerate_windows():
            item = QtWidgets.QListWidgetItem(f"#{w.hwnd} - {w.title}")
            item.setData(QtCore.Qt.UserRole, w.hwnd)
            self.list_widget.addItem(item)

    def on_start(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            QtWidgets.QMessageBox.warning(self, "提示", "请先选择要录制的窗口")
            return
        self._target_hwnd = int(item.data(QtCore.Qt.UserRole))
        output = Path(self.path_edit.text())
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            QtWidgets.QMessageBox.critical(self, "错误", "无法创建保存目录")
            return
        self._start_recording(output, self._target_hwnd)

    def _start_recording(self, output: Path, hwnd: int) -> None:
        if self._recording:
            return
        self._recording = True
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_label.setText("正在录制… 按 ESC 或点击停止按钮结束")

        # 置顶目标窗口
        try:
            if win32gui is not None:
                # 尝试恢复并前置窗口
                win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
                if win32con and win32process and win32api:
                    # 通过附加输入队列提升前置成功率
                    fg = win32gui.GetForegroundWindow()
                    pid1, tid1 = win32process.GetWindowThreadProcessId(fg)
                    pid2, tid2 = win32process.GetWindowThreadProcessId(hwnd)
                    if tid1 and tid2 and tid1 != tid2:
                        win32api.AttachThreadInput(tid1, tid2, True)
                        win32gui.BringWindowToTop(hwnd)
                        win32gui.SetForegroundWindow(hwnd)
                        win32api.AttachThreadInput(tid1, tid2, False)
                        # 再次呼叫以确保激活
                        win32gui.ShowWindow(hwnd, 9)
                        win32gui.SetForegroundWindow(hwnd)
                else:
                    win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass

        # 弹出覆盖层（带停止按钮）
        self._overlay = BorderOverlay()
        qx = qy = qw = qh = 0
        if win32gui is not None:
            try:
                x, y, w, h = self._get_window_rect_precise(hwnd)
                qx, qy, qw, qh = self._to_qt_coords(hwnd, x, y, w, h)
                self._overlay.setGeometry(qx, qy, qw, qh)
            except Exception:
                qx, qy, qw, qh = 100, 100, 600, 300
                self._overlay.setGeometry(qx, qy, qw, qh)
        self._overlay.show()

        # 独立的小停靠面板，避免覆盖层遮挡导致黑块
        self._stop_panel = StopPanel()
        self._stop_panel.stop_requested.connect(self.on_stop)
        # 将停止按钮固定在红框内部右上角（与 overlay 同坐标系）
        try:
            self._stop_panel.setGeometry(qx + qw - 140, qy + 40, 120, 40)
        except Exception:
            self._stop_panel.setGeometry(200, 120, 120, 40)
        self._stop_panel.show()
        # 不再跟随窗口，避免任何闪烁；需要时再启用
        self._border_timer.stop()

        def _worker() -> None:
            try:
                # 先录内存，不立即落盘
                self._active_recorder = ActionRecorder(target_hwnd=hwnd)
                payload = self._active_recorder.record(None)
                # 通过信号在主线程弹出保存对话框
                self.save_payload.emit(payload)
            finally:
                self._active_recorder = None
                QtCore.QMetaObject.invokeMethod(self, "_recording_finished", QtCore.Qt.QueuedConnection)

        self._rec_thread = threading.Thread(target=_worker, daemon=True)
        self._rec_thread.start()

    @QtCore.Slot()
    def _recording_finished(self) -> None:
        self._recording = False
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if self._overlay is not None:
            self._overlay.close()
            self._overlay = None
        if self._stop_panel is not None:
            self._stop_panel.close()
            self._stop_panel = None
        self._border_timer.stop()
        # 保存提示在 _prompt_save_payload 中处理

    @QtCore.Slot()
    def _tick_follow_window(self) -> None:
        if self._overlay is None or self._target_hwnd is None or win32gui is None:
            return
        try:
            x, y, w, h = self._get_window_rect_precise(self._target_hwnd)
            qx, qy, qw, qh = self._to_qt_coords(self._target_hwnd, x, y, w, h)
            self._overlay.setGeometry(qx, qy, qw, qh)
            if self._stop_panel is not None:
                self._stop_panel.setGeometry(qx + qw - 140, qy + 40, 120, 40)
        except Exception:
            pass

    @QtCore.Slot(object)
    def _prompt_save_payload(self, payload_obj: object) -> None:
        import json as _json
        try:
            payload = list(payload_obj)  # type: ignore[assignment]
        except Exception:
            payload = []
        suggested = self.path_edit.text()
        fn, _ = QtWidgets.QFileDialog.getSaveFileName(self, "保存录制为", suggested, "JSON (*.json)")
        if not fn:
            self.status_label.setText("已取消保存（已丢弃本次录制）")
            return
        try:
            Path(fn).parent.mkdir(parents=True, exist_ok=True)
            Path(fn).write_text(_json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.path_edit.setText(fn)
            self.status_label.setText(f"已保存: {fn}")
            self.refresh_recordings()
            QtWidgets.QMessageBox.information(self, "完成", f"录制已保存\n{fn}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "错误", f"保存失败: {e}")

    def on_stop(self) -> None:
        if not self._recording:
            return
        self.status_label.setText("正在停止并保存…")
        self.stop_btn.setEnabled(False)
        # 优先请求录制器主动停止，避免向目标窗口发送 ESC 而触发关闭等行为
        try:
            if hasattr(self, "_active_recorder") and self._active_recorder is not None:
                self._active_recorder.request_stop()
                return
        except Exception:
            pass
        # 兜底：发送 ESC
        from pynput import keyboard
        ctrl = keyboard.Controller()
        ctrl.press(keyboard.Key.esc)
        ctrl.release(keyboard.Key.esc)

    def on_browse_save(self) -> None:
        fn, _ = QtWidgets.QFileDialog.getSaveFileName(self, "选择保存文件", self.path_edit.text(), "JSON (*.json)")
        if fn:
            self.path_edit.setText(fn)

    def refresh_recordings(self) -> None:
        # 列出默认目录下的所有 json 作为回放下拉
        self.play_path.clear()
        try:
            for p in sorted(self.default_dir.glob("*.json")):
                self.play_path.addItem(str(p))
        except Exception:
            pass

    def _get_window_rect_precise(self, hwnd: int) -> tuple[int, int, int, int]:
        """Use DWM extended frame bounds for accurate rect (no shadow), fallback to GetWindowRect."""
        # DWMWA_EXTENDED_FRAME_BOUNDS = 9
        try:
            DWMWA_EXTENDED_FRAME_BOUNDS = 9
            rect = ctypes.wintypes.RECT()  # type: ignore[attr-defined]
            dwmapi = ctypes.windll.dwmapi  # type: ignore[attr-defined]
            res = dwmapi.DwmGetWindowAttribute(ctypes.wintypes.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect))  # type: ignore[attr-defined]
            if res == 0:
                x, y, w, h = rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
                return x, y, w, h
        except Exception:
            pass
        # Fallback
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        return l, t, r - l, b - t

    def _to_qt_coords(self, hwnd: int, x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
        """Convert physical screen pixels (Win32) to Qt logical pixels under DPI scaling."""
        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            user32.SetProcessDPIAware()
            dpi = user32.GetDpiForWindow(ctypes.wintypes.HWND(hwnd))  # type: ignore[attr-defined]
            scale = dpi / 96.0 if dpi else 1.0
        except Exception:
            # Fallback to Qt screen ratio
            scr = QtGui.QGuiApplication.primaryScreen()
            scale = float(scr.devicePixelRatio()) if scr else 1.0
        if scale <= 0:
            scale = 1.0
        qx = int(round(x / scale))
        qy = int(round(y / scale))
        qw = int(round(w / scale))
        qh = int(round(h / scale))
        return qx, qy, qw, qh

    def on_play(self) -> None:
        from game_test_py.tools.recorder import ActionReplayer
        if self._replay_thread is not None and self._replay_thread.is_alive():
            return
        p = Path(self.play_path.currentText())
        if not p.exists():
            QtWidgets.QMessageBox.warning(self, "提示", "文件不存在")
            return
        self.play_btn.setEnabled(False)
        self.status_label.setText("回放中…")
        # 最小化主窗口，避免遮挡
        self.showMinimized()

        def _replay_worker() -> None:
            try:
                ActionReplayer().replay(p)
            except Exception as e:  # 回到 UI 线程提示
                QtCore.QMetaObject.invokeMethod(
                    self,
                    "_replay_failed",
                    QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, str(e)),
                )
            finally:
                QtCore.QMetaObject.invokeMethod(self, "_replay_finished", QtCore.Qt.QueuedConnection)

        self._replay_thread = threading.Thread(target=_replay_worker, daemon=True)
        self._replay_thread.start()

    @QtCore.Slot()
    def _replay_finished(self) -> None:
        self.play_btn.setEnabled(True)
        self.status_label.setText("回放完成")
        self.showNormal()

    @QtCore.Slot(str)
    def _replay_failed(self, msg: str) -> None:
        QtWidgets.QMessageBox.critical(self, "错误", msg)


def run() -> None:
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


