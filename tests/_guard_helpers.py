"""Ziel-Funktionen fuer den Arbeitsprozess in test_guard.py (muessen im
gespawnten Prozess importierbar sein, deshalb ein eigenes Modul)."""

import os
import time

from rechnung.pipeline import process


def maybe_hang(path, store, use_llm=True, create_draft_supplier=False):
    if "haengt" in str(path):
        time.sleep(60)
    if "absturz" in str(path):
        os._exit(3)
    if "kaputt" in str(path):
        raise ValueError("defektes PDF")
    return process(path, store, use_llm=use_llm, create_draft_supplier=create_draft_supplier)
