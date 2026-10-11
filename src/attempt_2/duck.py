"""One DuckDB connection factory for every heavy adapter (2026-10-11).

Why: on the v10 night run five sources died with DuckDB "Out of Memory Error:
Allocation failure" (servicing, MUE, sector schemes, billing LM) and one with a
pandas MemoryError (enrollment). A bare ``duckdb.connect()`` is an in-memory
database with NO temp directory, so DuckDB cannot spill a large hash aggregate
to disk and asks the OS for more RAM until the allocation fails; its default
memory ceiling (80 percent of physical RAM) also ignores the gigabytes pandas
already holds in the same process. This factory gives every connection a temp
directory (spilling on) and a memory ceiling sized for a process that shares
the box with pandas.

Environment overrides (the night-runner sets them):
  MEDICAID_DUCKDB_MEM   e.g. '6GB'  (default: 40 percent of physical RAM, at least 2GB)
  MEDICAID_DUCKDB_TMP   a directory on a disk with room (default: <system temp>/medicaid_duckdb)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def total_ram_bytes() -> int | None:
    """Physical RAM in bytes, or None when it cannot be read (no psutil needed)."""
    try:
        import psutil  # type: ignore
        return int(psutil.virtual_memory().total)
    except Exception:  # noqa: BLE001
        pass
    try:
        if os.name == "nt":
            import ctypes
            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = _MS()
            ms.dwLength = ctypes.sizeof(_MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):  # type: ignore[attr-defined]
                return int(ms.ullTotalPhys)
        else:
            return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except Exception:  # noqa: BLE001
        return None
    return None


def default_memory_limit() -> str:
    env = os.environ.get("MEDICAID_DUCKDB_MEM")
    if env:
        return env
    total = total_ram_bytes()
    if not total:
        return "4GB"
    gb = max(2, int(total * 0.4 / (1024 ** 3)))
    return f"{gb}GB"


def default_temp_dir() -> str:
    env = os.environ.get("MEDICAID_DUCKDB_TMP")
    d = Path(env) if env else Path(tempfile.gettempdir()) / "medicaid_duckdb"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def connect(memory_limit: str | None = None, temp_dir: str | None = None):
    """``duckdb.connect()`` with spilling on and a shared-process memory ceiling."""
    import duckdb
    cfg = {"temp_directory": temp_dir or default_temp_dir(),
           "memory_limit": memory_limit or default_memory_limit()}
    try:
        return duckdb.connect(config=cfg)
    except Exception:  # noqa: BLE001  (an old DuckDB without these config keys)
        con = duckdb.connect()
        for k, v in cfg.items():
            try:
                con.execute(f"SET {k}='{v}'")
            except Exception:  # noqa: BLE001
                pass
        return con
