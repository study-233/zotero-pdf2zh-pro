"""Atomic admission gate for local, authenticated runtime replacement."""
from __future__ import annotations

import hmac
import secrets
import threading
import time
from pathlib import Path


def control_token(directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".control-token"
    try:
        with path.open("x", encoding="ascii") as stream:
            stream.write(secrets.token_hex(32))
        path.chmod(0o600)
    except FileExistsError:
        pass
    value = path.read_text(encoding="ascii").strip()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("Invalid local control credential")
    return value


class MaintenanceGate:
    def __init__(self, busy, clock=time.monotonic):
        self._lock = threading.RLock()
        self._busy = busy
        self._clock = clock
        self._active = 0
        self._last_active = clock()
        self._held_until = 0.0
        self._last_busy = False

    def enter(self) -> bool:
        with self._lock:
            if self._held_until > self._clock():
                return False
            self._active += 1
            self._last_active = self._clock()
            self._last_busy = bool(self._busy())
            return True

    def leave(self):
        with self._lock:
            self._active = max(0, self._active - 1)
            self._last_active = self._clock()
            self._last_busy = bool(self._busy())

    def snapshot(self):
        with self._lock:
            busy = self._active > 0 or self._busy()
            if busy or self._last_busy:
                self._last_active = self._clock()
            self._last_busy = bool(busy)
            return {"activeRequests": self._active,
                    "busy": bool(busy),
                    "idleSeconds": max(0, int(self._clock() - self._last_active)),
                    "held": self._held_until > self._clock()}

    def acquire(self, idle_seconds=60):
        with self._lock:
            state = self.snapshot()
            if state["held"] or state["busy"] or state["idleSeconds"] < idle_seconds:
                return False
            # A crashed updater cannot leave the running server permanently closed.
            self._held_until = self._clock() + 120
            return True

    def release(self):
        with self._lock:
            self._held_until = 0
            self._last_active = self._clock()


def install_routes(app, busy, directory):
    from flask import request, jsonify, g

    gate = MaintenanceGate(busy)
    app.extensions["update_maintenance"] = gate

    @app.route("/internal/maintenance", methods=["POST", "DELETE"])
    def maintenance():
        if (request.remote_addr not in {"127.0.0.1", "::1"}
                or request.headers.get("Origin")
                or not hmac.compare_digest(request.headers.get("X-PDF2ZH-Control-Token", ""),
                                           control_token(directory()))):
            return jsonify({"status": "error", "code": "control_forbidden"}), 403
        if request.method == "DELETE":
            gate.release()
            return jsonify({"status": "ok"})
        data = request.get_json(silent=True) or {}
        idle = data.get("idleSeconds", 60)
        if type(idle) is not int or not 0 <= idle <= 300:
            return jsonify({"status": "error", "code": "invalid_idle_seconds"}), 400
        if not gate.acquire(idle):
            return jsonify({"status": "busy", **gate.snapshot()}), 409
        return jsonify({"status": "ok", **gate.snapshot()})

    @app.before_request
    def admission():
        exempt = (request.path in {"/health", "/internal/maintenance"}
                  or request.method == "GET" and request.path.startswith("/tasks")
                  and not request.path.endswith(("/result", "/diagnostics")))
        if exempt:
            return None
        if not gate.enter():
            response = jsonify({"status": "error", "code": "server_maintenance",
                                "accepted": False, "message": "服务正在更新，请稍后重试。"})
            response.status_code = 503
            response.headers["Retry-After"] = "3"
            response.headers["X-PDF2ZH-Maintenance"] = "not-accepted"
            return response
        g.maintenance_admitted = True

    @app.after_request
    def complete(response):
        if not g.pop("maintenance_admitted", False):
            return response
        if not response.is_streamed:
            gate.leave()
            return response
        original = response.response
        completed = False

        def finish():
            nonlocal completed
            if not completed:
                completed = True
                gate.leave()

        def tracked():
            try:
                yield from original
            finally:
                close = getattr(original, "close", None)
                try:
                    if close:
                        close()
                finally:
                    finish()

        response.response = tracked()
        response.call_on_close(finish)
        return response

    @app.teardown_request
    def failed(_error):
        if g.pop("maintenance_admitted", False):
            gate.leave()

    return gate
