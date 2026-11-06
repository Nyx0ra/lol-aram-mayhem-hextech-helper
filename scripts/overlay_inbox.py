"""Wake the Tk thread when an in-process overlay command is queued."""

import queue
import threading
import tkinter as tk


_WAKE = "<<OverlayCommandsReady>>"


class OverlayInbox:
    def __init__(self, root, consume):
        self._root = root
        self._consume = consume
        self._queue = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._pending = False
        self._closed = False
        self._binding = root.bind(_WAKE, self._dispatch, add="+")

    def put(self, message):
        with self._lock:
            if self._closed:
                return
            self._queue.put(message)
            if self._pending:
                return
            self._pending = True

        # Threaded Tcl marshals this call to the Tk thread. Queue the event
        # at the tail so the producer does not wait for the actual redraw.
        # The lock must be released before calling into Tk.
        try:
            self._root.event_generate(_WAKE, when="tail")
        except (tk.TclError, RuntimeError) as exc:
            with self._lock:
                self._pending = False
                closed = self._closed
            if not closed:
                print(f"悬浮窗通知失败: {exc}")

    def get_nowait(self):
        return self._queue.get_nowait()

    def _dispatch(self, _event):
        with self._lock:
            if self._closed:
                return
            self._pending = False
        self._consume()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._queue = queue.SimpleQueue()
        self._root.unbind(_WAKE, self._binding)
        self._consume = None
