"""Resident file-queue bridge; fixed export operation, no network or arbitrary code.

Register this folder in Fusion Scripts and Add-Ins. Run once per session or
enable Run on Startup. The worker only reads files; native API work runs in a
custom-event handler on Fusion's main thread.
"""
import importlib.util
import json
from pathlib import Path
import threading
import traceback

import adsk.core as ac

ROOT = Path(__file__).resolve().parents[3]
EVENT = "Tero.Publication.Export.v1"
_handlers = []
_stop = threading.Event()
_thread = None


class Handler(ac.CustomEventHandler):
    def notify(self, args):
        event_args = ac.CustomEventArgs.cast(args)
        path = Path(json.loads(event_args.additionalInfo)["request"])
        if path.parent.parent.resolve() != (ROOT / ".build/fusion-jobs").resolve():
            return
        if path.with_name("result.json").exists() or path.with_name("started.json").exists() or path.with_name("cancelled").exists():
            return
        try:
            spec = importlib.util.spec_from_file_location("tero_publication_exporter", Path(__file__).with_name("exporter.py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.export_job(path)
        except BaseException:
            path.with_name("result.json").write_text(json.dumps({"success":False,"error":traceback.format_exc()}))


def run(_context):
    global _thread
    if _thread and _thread.is_alive():
        return
    app = ac.Application.get()
    event = app.registerCustomEvent(EVENT)
    handler = Handler()
    event.add(handler)
    _handlers.append((event, handler))
    _stop.clear()

    def watch():
        submitted = set()
        while not _stop.wait(1):
            for path in sorted((ROOT / ".build/fusion-jobs").glob("*/request.json")):
                if path in submitted or path.with_name("result.json").exists() or path.with_name("cancelled").exists() or path.with_name("started.json").exists():
                    continue
                submitted.add(path)
                app.fireCustomEvent(EVENT, json.dumps({"request": str(path)}))
    _thread = threading.Thread(target=watch, daemon=True)
    _thread.start()


def stop(_context):
    _stop.set()
    if _thread:
        _thread.join(timeout=2)
    app = ac.Application.get()
    for event, handler in _handlers:
        event.remove(handler)
    _handlers.clear()
    app.unregisterCustomEvent(EVENT)
