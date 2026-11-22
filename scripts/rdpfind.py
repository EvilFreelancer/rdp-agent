#!/usr/bin/env python3
"""rdpfind.py - suggest RDP host addresses already present on this machine.

A new operator rarely remembers the address of the remote machine, but the client
they use has it saved. This script looks for it in the places that do not depend on
which client that is:

1. `.rdp` files in the usual folders, parsed for `full address:s:`;
2. saved connections of the clients that keep them on disk (Windows App,
   Microsoft Remote Desktop), read read-only.

It never invents an address: when nothing is found it says so and points the operator
to whoever issued the machine.
"""
import glob
import os
import plistlib
import re
import sqlite3
import sys

FOLDERS = ["Downloads", "Documents", "Desktop"]
CLIENT_DBS = [
    ("Windows App",
     "Library/Group Containers/UBF8T346G9.com.microsoft.rdc/Library/"
     "Application Support/com.microsoft.rdc.macos.spotlight-data.sqlite"),
    ("Windows App Beta",
     "Library/Application Support/com.microsoft.rdc.osx.beta/"
     "com.microsoft.rdc.application-data.sqlite"),
]
ADDRESS = re.compile(r"^\s*full address:s:(.+?)\s*$", re.M)


def from_rdp_files(home):
    found = []
    for folder in FOLDERS:
        for path in glob.glob(os.path.join(home, folder, "**", "*.rdp"), recursive=True):
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            m = ADDRESS.search(text)
            if m:
                found.append((m.group(1).strip(), path, "-"))
    return found


def hosts_in(path):
    """Any sqlite file whose tables expose a host column, whatever the client is."""
    out = []
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    except Exception:
        return out
    try:
        tables = [r[0] for r in con.execute(
            "select name from sqlite_master where type='table'")]
        for table in tables:
            cols = [r[1] for r in con.execute("pragma table_info(%s)" % table)]
            host_col = next((c for c in cols if c.upper().endswith("HOSTNAME")), None)
            if not host_col:
                continue
            count_col = next((c for c in cols if c.upper().endswith("CONNECTIONCOUNT")), None)
            select = "%s, %s" % (host_col, count_col or "0")
            for host, count in con.execute(
                    "select %s from %s where %s is not null" % (select, table, host_col)):
                if host and len(str(host)) > 3:
                    out.append((str(host), table, count))
    except Exception:
        pass
    finally:
        con.close()
    return out


def from_clients(home):
    """Only the Microsoft clients' own databases, never other apps' containers."""
    found = []
    for label, rel in CLIENT_DBS:
        path = os.path.join(home, rel)
        if not os.path.exists(path):
            continue
        for host, _table, count in hosts_in(path):
            found.append((host, label, count))
    return found


def main():
    home = os.path.expanduser("~")
    raw = from_clients(home) + from_rdp_files(home)
    best = {}
    for host, source, count in raw:
        key = host.strip().lower()
        cnt = count if isinstance(count, int) else 0
        if key not in best or cnt > (best[key][2] if isinstance(best[key][2], int) else 0):
            best[key] = (host.strip(), source, count)
    rows = sorted(best.values(), key=lambda r: -(r[2] if isinstance(r[2], int) else 0))
    if not rows:
        print("Готовых адресов на этой машине не найдено.")
        print("Спросите адрес у администратора или у того, кто выдал вам машину.")
        print("Обычно он выглядит так: 10.x.x.x или win-xxx.company.ru")
        return 1
    print("Найденные адреса — подтвердите нужный или введите свой:\n")
    for i, (host, source, count) in enumerate(rows, 1):
        mark = "" if count == "-" else "  (подключений: %s)" % count
        print("  %d. %-32s %s%s" % (i, host, source, mark))
    print("\nАдрес можно взять и вручную: в своём клиенте откройте свойства")
    print("сохранённого ПК — поле называется PC name / Компьютер.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
