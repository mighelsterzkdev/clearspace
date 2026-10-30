"""PC maintenance tools beside cleanup: updates, network, repair, disks and startup apps.

Every tool runs a fixed Windows command or API. The renderer only chooses among
known actions and identifiers that this module listed itself; it never passes a
command line. Long tools run on background threads and report bounded state.
"""

from __future__ import annotations

from copy import deepcopy
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import re
import socket
import statistics
import subprocess
import threading
import time

TARGETS = {"cloudflare": "1.1.1.1", "google": "8.8.8.8"}
PING_SAMPLES = 60
PING_INTERVAL = 0.5
# These update themselves through Microsoft Edge Update; winget cannot replace the system copy.
SELF_UPDATING = frozenset(("Microsoft.Edge", "Microsoft.Edge.Beta", "Microsoft.Edge.Dev", "Microsoft.EdgeWebView2Runtime"))
_PACKAGE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}")
_HOST = re.compile(r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_PERCENT = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s?%")


class ToolError(ValueError):
    """A bounded message suitable for display."""


def _hidden() -> dict:
    if os.name != "nt":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {"startupinfo": startup, "creationflags": subprocess.CREATE_NO_WINDOW}


def _system32(name: str) -> str:
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / ("Sysnative" if _wow64() else "System32") / name)


def _wow64() -> bool:
    return os.name == "nt" and ctypes.sizeof(ctypes.c_void_p) == 4 and "PROGRAMFILES(X86)" in os.environ


# Program updates (winget) ------------------------------------------------------

def parse_winget_table(text: str) -> list[dict]:
    """Rows of the first table in `winget upgrade` output, in any display language.

    Columns are found from the header line above the dashed rule, so localized
    headings work. Only rows with an identifier and both versions are kept.
    """
    lines = [line.split("\r")[-1].rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    rule = next((index for index, line in enumerate(lines) if len(line) > 20 and set(line) == {"-"}), None)
    if rule is None or rule == 0:
        return []
    header = lines[rule - 1]
    starts = [match.start() for match in re.finditer(r"\S+", header)]
    if len(starts) < 4:
        return []
    packages = []
    for line in lines[rule + 1:]:
        if not line.strip() or len(line) < starts[-1] or line.startswith("-"):
            break
        cells = [line[start:end].strip() for start, end in zip(starts, [*starts[1:], len(line)])]
        name, identifier, version, available = cells[:4]
        if not (_PACKAGE_ID.fullmatch(identifier) and version and available) or identifier in SELF_UPDATING:
            continue
        packages.append({"id": identifier, "name": name, "version": version, "available": available,
                         "source": cells[4] if len(cells) > 4 else "", "status": "pending"})
    return packages


def _winget(*arguments: str, timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["winget", *arguments, "--accept-source-agreements", "--disable-interactivity"],
                              capture_output=True, timeout=timeout, **_hidden())
    except FileNotFoundError as error:
        raise ToolError("Windows Package Manager (winget) is not installed. Install App Installer from the Microsoft Store.") from error
    except subprocess.TimeoutExpired as error:
        raise ToolError("Windows Package Manager did not answer in time. Try again.") from error


# Connection test ---------------------------------------------------------------

def summarize(samples: list[float | None]) -> dict:
    received = [value for value in samples if value is not None]
    lost = len(samples) - len(received)
    loss = round(100 * lost / len(samples), 1) if samples else 0.0
    if not received:
        return {"average": None, "jitter": None, "best": None, "worst": None, "loss": loss, "verdict": "offline" if samples else None}
    jitter = statistics.fmean(abs(a - b) for a, b in zip(received, received[1:])) if len(received) > 1 else 0.0
    average = statistics.fmean(received)
    worst = max(received)
    if loss >= 5:
        verdict = "losing"
    elif jitter > 15 or (worst > 3 * average and worst - average > 80):
        verdict = "unstable"
    else:
        verdict = "stable"
    return {"average": round(average, 1), "jitter": round(jitter, 1), "best": round(min(received), 1),
            "worst": round(worst, 1), "loss": loss, "verdict": verdict}


class _Echo:
    """ICMP echo through iphlpapi; works without administrator permission."""

    def __init__(self):
        class Options(ctypes.Structure):
            _fields_ = [("Ttl", ctypes.c_ubyte), ("Tos", ctypes.c_ubyte), ("Flags", ctypes.c_ubyte),
                        ("OptionsSize", ctypes.c_ubyte), ("OptionsData", ctypes.c_void_p)]

        class Reply(ctypes.Structure):
            _fields_ = [("Address", ctypes.c_ulong), ("Status", ctypes.c_ulong), ("RoundTripTime", ctypes.c_ulong),
                        ("DataSize", ctypes.c_ushort), ("Reserved", ctypes.c_ushort), ("Data", ctypes.c_void_p),
                        ("Options", Options)]

        self._reply_type = Reply
        self._api = ctypes.WinDLL("iphlpapi", use_last_error=True)
        self._api.IcmpCreateFile.restype = ctypes.c_void_p
        self._api.IcmpCloseHandle.argtypes = [ctypes.c_void_p]
        self._api.IcmpSendEcho.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ushort,
                                           ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
        self._api.IcmpSendEcho.restype = ctypes.c_ulong
        self._handle = self._api.IcmpCreateFile()
        if not self._handle or self._handle == ctypes.c_void_p(-1).value:
            raise ToolError("Windows could not start the connection test.")

    def __call__(self, address: str, timeout_ms: int = 1000) -> float | None:
        destination = int.from_bytes(socket.inet_aton(address), "little")
        payload = b"clearspace-check-"
        buffer = ctypes.create_string_buffer(ctypes.sizeof(self._reply_type) + len(payload) + 8)
        started = time.perf_counter()
        count = self._api.IcmpSendEcho(self._handle, destination, payload, len(payload), None, buffer, len(buffer), timeout_ms)
        if not count:
            return None
        reply = self._reply_type.from_buffer(buffer)
        if reply.Status != 0:
            return None
        # RoundTripTime has whole milliseconds; keep sub-millisecond replies visible.
        return float(reply.RoundTripTime) if reply.RoundTripTime else round((time.perf_counter() - started) * 1000, 2)

    def close(self):
        if self._handle:
            self._api.IcmpCloseHandle(self._handle)
            self._handle = None


# Windows repair ----------------------------------------------------------------

def last_percent(text: str) -> float | None:
    found = _PERCENT.findall(text)
    if not found:
        return None
    value = float(found[-1].replace(",", "."))
    return value if 0 <= value <= 100 else None


def sfc_findings(log_lines, since: datetime) -> dict:
    """Read System File Checker results from CBS.log entries written since `since`.

    The [SR] entries are written in English on every display language.
    """
    repaired = unrepaired = 0
    verified = False
    for line in log_lines:
        if "[SR]" not in line:
            continue
        try:
            stamp = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if stamp < since:
            continue
        if "Repairing corrupted file" in line or "Repaired file" in line:
            repaired += 1
        elif "Cannot repair member file" in line:
            unrepaired += 1
        elif "Verify complete" in line:
            verified = True
    return {"repaired": repaired, "unrepaired": unrepaired, "verified": verified}


def _cbs_tail(limit: int = 8 * 1024 * 1024) -> list[str]:
    path = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Logs" / "CBS" / "CBS.log"
    try:
        with path.open("rb") as log:
            size = log.seek(0, os.SEEK_END)
            log.seek(max(0, size - limit))
            return log.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []


# Startup apps ------------------------------------------------------------------

RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN32 = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
# Store apps declare startup tasks; State 2 and 4 run at sign-in, 3 and 4 are fixed by policy.
APP_TASKS = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\SystemAppData"


def app_name(package: str) -> str:
    """A readable name from a package family name, e.g. MicrosoftTeams_8wekyb3d8bbwe -> Microsoft Teams."""
    name = package.split("_", 1)[0].rsplit(".", 1)[-1]
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name) or package


def approved_enabled(value: bytes | None) -> bool:
    """Task Manager's StartupApproved flag: missing or an even first byte means enabled."""
    return not value or not (value[0] & 1)


def approved_value(enabled: bool, now: float | None = None) -> bytes:
    if enabled:
        return bytes([2]) + bytes(11)
    filetime = int(((now if now is not None else time.time()) + 11644473600) * 10_000_000)
    return bytes([3, 0, 0, 0]) + filetime.to_bytes(8, "little")


def _program(command: str) -> str:
    command = os.path.expandvars(command.strip())
    if command.startswith('"'):
        target = command[1:].split('"', 1)[0]
    else:
        match = re.match(r"(.+?\.(?:exe|bat|cmd|com|lnk|vbs|ps1))(?:\s|$)", command, re.IGNORECASE)
        target = match[1] if match else command.split(" ", 1)[0]
    return Path(target).name or command[:60]


class StartupSources:
    """Registry Run keys and Startup folders, the same places Task Manager lists."""

    def __init__(self, user_root=None, machine_root=None, user_folder=None, machine_folder=None):
        import winreg
        self.winreg = winreg
        self.roots = {"user": user_root or (winreg.HKEY_CURRENT_USER, ""), "machine": machine_root or (winreg.HKEY_LOCAL_MACHINE, "")}
        self.folders = {
            "user": Path(user_folder) if user_folder else Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup",
            "machine": Path(machine_folder) if machine_folder else Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs\StartUp",
        }

    def _key(self, scope, path):
        hive, prefix = self.roots[scope]
        return hive, f"{prefix}\\{path}" if prefix else path

    def _values(self, scope, path):
        hive, name = self._key(scope, path)
        try:
            with self.winreg.OpenKey(hive, name) as key:
                index, values = 0, []
                while True:
                    try:
                        values.append(self.winreg.EnumValue(key, index))
                    except OSError:
                        return values
                    index += 1
        except OSError:
            return []

    def _subkeys(self, scope, path):
        hive, name = self._key(scope, path)
        try:
            with self.winreg.OpenKey(hive, name) as key:
                index, names = 0, []
                while True:
                    try:
                        names.append(self.winreg.EnumKey(key, index))
                    except OSError:
                        return names
                    index += 1
        except OSError:
            return []

    def _app_tasks(self):
        for package in self._subkeys("user", APP_TASKS)[:400]:
            for task in self._subkeys("user", f"{APP_TASKS}\\{package}")[:20]:
                state = dict((name, data) for name, data, _ in self._values("user", f"{APP_TASKS}\\{package}\\{task}")).get("State")
                if isinstance(state, int) and 0 <= state <= 5:
                    yield package, task, state

    def items(self) -> list[dict]:
        found = []
        for package, task, state in self._app_tasks():
            found.append({"id": f"user:App:{package}\\{task}", "name": app_name(package), "program": "Microsoft Store", "scope": "user",
                          "enabled": state in (2, 4), "fixed": state in (3, 4)})
        sources = [("user", RUN, "Run"), ("machine", RUN, "Run")]
        if os.name == "nt":
            sources.append(("machine", RUN32, "Run32"))
        for scope, path, approved in sources:
            flags = {name: data for name, data, _ in self._values(scope, f"{APPROVED}\\{approved}") if isinstance(data, bytes)}
            for name, data, kind in self._values(scope, path):
                if not name or not isinstance(data, str) or kind not in (self.winreg.REG_SZ, self.winreg.REG_EXPAND_SZ):
                    continue
                found.append({"id": f"{scope}:{approved}:{name}", "name": name, "program": _program(data), "scope": scope,
                              "enabled": approved_enabled(flags.get(name))})
        for scope, folder in self.folders.items():
            flags = {name: data for name, data, _ in self._values(scope, f"{APPROVED}\\StartupFolder") if isinstance(data, bytes)}
            try:
                entries = sorted(entry for entry in folder.iterdir() if entry.is_file() and entry.name.lower() != "desktop.ini")
            except OSError:
                entries = []
            for entry in entries:
                found.append({"id": f"{scope}:StartupFolder:{entry.name}", "name": entry.stem, "program": entry.name, "scope": scope,
                              "enabled": approved_enabled(flags.get(entry.name))})
        return sorted(found, key=lambda item: item["name"].casefold())

    def set_enabled(self, identifier: str, enabled: bool) -> None:
        listed = {item["id"]: item for item in self.items()}
        if identifier not in listed:
            raise ToolError("This startup item is no longer present. Refresh the list.")
        if listed[identifier].get("fixed"):
            raise ToolError("An administrator policy controls this startup item.")
        scope, approved, name = identifier.split(":", 2)
        app = approved == "App"
        hive, path = self._key(scope, f"{APP_TASKS}\\{name}" if app else f"{APPROVED}\\{approved}")
        try:
            with self.winreg.CreateKeyEx(hive, path, 0, self.winreg.KEY_SET_VALUE) as key:
                if app:
                    # The same values Task Manager writes: 2 enabled by the user, 1 disabled by the user.
                    self.winreg.SetValueEx(key, "State", 0, self.winreg.REG_DWORD, 2 if enabled else 1)
                else:
                    self.winreg.SetValueEx(key, name, 0, self.winreg.REG_BINARY, approved_value(enabled))
        except PermissionError as error:
            raise ToolError("Changing this item needs administrator permission.") from error
        except OSError as error:
            raise ToolError("Windows could not change this startup item.") from error


# Service -----------------------------------------------------------------------

class ToolsService:
    def __init__(self, administrator=None, echo=None, startup=None, winget=None, disks=None, flush=None, repair_runner=None):
        if administrator is None:
            from .maintenance import is_administrator as administrator
        self._administrator = administrator
        self._echo, self._startup = echo, startup
        self._winget = winget or _winget
        self._disks = disks or _read_disks
        self._flush = flush or _flush_dns
        self._repair_runner = repair_runner or _run_repair
        self._lock = threading.Lock()
        self._stop_ping = threading.Event()
        self._stop_updates = threading.Event()
        self._state = {
            "dns": {"status": "idle", "error": None},
            "connection": {"status": "idle", "target": "cloudflare", "samples": [], "summary": None, "error": None},
            "updates": {"status": "idle", "packages": [], "current": None, "error": None},
            "repair": {"status": "idle", "phase": None, "progress": None, "result": None, "error": None},
            "disks": {"status": "idle", "items": [], "error": None},
            "startup": {"status": "idle", "items": [], "error": None},
        }

    def snapshot(self) -> dict:
        with self._lock:
            state = deepcopy(self._state)
        state["administrator"] = bool(self._administrator())
        return state

    def _update(self, tool, **values):
        with self._lock:
            self._state[tool].update(values)

    def _start(self, tool, busy, target, *args):
        with self._lock:
            if self._state[tool]["status"] in busy:
                raise ToolError("This tool is already running.")
            self._state[tool].update(status=busy[0], error=None)
        threading.Thread(target=self._guard, args=(tool, target, *args), name=f"clearspace-{tool}", daemon=True).start()
        return self.snapshot()

    def _guard(self, tool, target, *args):
        try:
            target(*args)
        except ToolError as error:
            self._update(tool, status="failed", error=str(error))
        except Exception:
            self._update(tool, status="failed", error="The tool stopped unexpectedly. Try again.")

    def busy(self) -> bool:
        with self._lock:
            return (self._state["updates"]["status"] == "updating" or self._state["repair"]["status"] == "running")

    # DNS
    def flush_dns(self):
        return self._start("dns", ("running",), self._flush_dns)

    def _flush_dns(self):
        self._flush()
        self._update("dns", status="done")

    # Connection
    def test_connection(self, target):
        if target in TARGETS:
            address_name = target
        elif isinstance(target, str) and _HOST.fullmatch(target.strip()):
            address_name = target.strip().lower()
        else:
            raise ToolError("Enter a valid server name or IPv4 address.")
        self._stop_ping.clear()
        with self._lock:
            if self._state["connection"]["status"] == "running":
                raise ToolError("This tool is already running.")
            self._state["connection"].update(status="running", target=address_name, samples=[], summary=None, error=None)
        threading.Thread(target=self._guard, args=("connection", self._ping, address_name), name="clearspace-connection", daemon=True).start()
        return self.snapshot()

    def stop_connection(self):
        self._stop_ping.set()
        return self.snapshot()

    def _ping(self, target):
        try:
            address = TARGETS.get(target) or socket.gethostbyname(target)
        except OSError as error:
            raise ToolError("That server name could not be found. Check the name and your connection.") from error
        echo = self._echo or _Echo()
        samples = []
        try:
            for _ in range(PING_SAMPLES):
                if self._stop_ping.is_set():
                    break
                started = time.monotonic()
                samples.append(echo(address))
                self._update("connection", samples=list(samples), summary=summarize(samples))
                self._stop_ping.wait(max(0.0, PING_INTERVAL - (time.monotonic() - started)))
        finally:
            if self._echo is None:
                echo.close()
        self._update("connection", status="done", summary=summarize(samples))

    # Disks
    def check_disks(self):
        return self._start("disks", ("running",), self._check_disks)

    def _check_disks(self):
        self._update("disks", status="done", items=self._disks())

    # Startup
    def _sources(self):
        if self._startup is None:
            self._startup = StartupSources()
        return self._startup

    def list_startup(self):
        try:
            items = self._sources().items()
        except OSError as error:
            raise ToolError("Windows could not list startup apps.") from error
        self._update("startup", status="done", items=items, error=None)
        return self.snapshot()

    def set_startup(self, identifier, enabled):
        if not isinstance(identifier, str) or not isinstance(enabled, bool) or len(identifier) > 400:
            raise ToolError("Choose a startup item from the list.")
        self._sources().set_enabled(identifier, enabled)
        return self.list_startup()

    # Updates
    def check_updates(self):
        return self._start("updates", ("checking", "updating"), self._check_updates)

    def _check_updates(self):
        result = self._winget("upgrade", timeout=180)
        packages = parse_winget_table(result.stdout.decode("utf-8", "replace"))
        if result.returncode not in (0, 0x8A15002B - 2**32, 0x8A15002B) and not packages:
            raise ToolError("Windows Package Manager could not list updates. Try again.")
        self._update("updates", status="ready", packages=packages[:200], current=None)

    def install_updates(self, identifiers):
        with self._lock:
            listed = [package["id"] for package in self._state["updates"]["packages"]]
        if (not isinstance(identifiers, list) or not identifiers or len(identifiers) > 200
                or any(not isinstance(item, str) or item not in listed for item in identifiers) or len(set(identifiers)) != len(identifiers)):
            raise ToolError("Choose programs from the update list.")
        self._stop_updates.clear()
        return self._start("updates", ("updating", "checking"), self._install, list(identifiers))

    def stop_updates(self):
        self._stop_updates.set()
        return self.snapshot()

    def _set_package(self, identifier, status):
        with self._lock:
            for package in self._state["updates"]["packages"]:
                if package["id"] == identifier:
                    package["status"] = status

    def _install(self, identifiers):
        for identifier in identifiers:
            if self._stop_updates.is_set():
                break
            self._update("updates", current=identifier)
            self._set_package(identifier, "updating")
            try:
                result = self._winget("upgrade", "--id", identifier, "--exact", "--silent",
                                      "--accept-package-agreements", timeout=1800)
                self._set_package(identifier, "updated" if result.returncode == 0 else "failed")
            except ToolError:
                self._set_package(identifier, "failed")
        self._update("updates", status="done", current=None)

    # Repair
    def repair(self):
        if not self._administrator():
            raise ToolError("Repairing Windows needs administrator permission. Use Restart as administrator first.")
        with self._lock:
            if self._state["repair"]["status"] == "running":
                raise ToolError("This tool is already running.")
            self._state["repair"].update(status="running", phase="dism", progress=None, result=None, error=None)
        threading.Thread(target=self._guard, args=("repair", self._repair), name="clearspace-repair", daemon=True).start()
        return self.snapshot()

    def _repair(self):
        def progress(phase, value):
            self._update("repair", phase=phase, progress=value)
        result = self._repair_runner(progress)
        self._update("repair", status="done", phase=None, progress=None, result=result)


def _flush_dns():
    if os.name != "nt":
        raise ToolError("This tool works on Windows only.")
    dns = ctypes.WinDLL("dnsapi")
    dns.DnsFlushResolverCache.restype = ctypes.c_int
    if not dns.DnsFlushResolverCache():
        raise ToolError("Windows could not clear the DNS cache.")


_DISK_SCRIPT = (
    "Get-PhysicalDisk | ForEach-Object { $r = $_ | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue; "
    "[pscustomobject]@{Name=$_.FriendlyName; Media=[string]$_.MediaType; Bus=[string]$_.BusType; Health=[string]$_.HealthStatus; "
    "Size=$_.Size; Temperature=$r.Temperature; Wear=$r.Wear} } | ConvertTo-Json -Compress"
)


def parse_disks(text: str) -> list[dict]:
    try:
        data = json.loads(text) if text.strip() else []
    except ValueError:
        return []
    rows = data if isinstance(data, list) else [data]
    disks = []
    for row in rows[:16]:
        if not isinstance(row, dict):
            continue
        health = str(row.get("Health") or "").lower()
        number = lambda key: row[key] if isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool) else None
        disks.append({
            "name": str(row.get("Name") or "Disk")[:120], "media": str(row.get("Media") or "")[:20], "bus": str(row.get("Bus") or "")[:20],
            "health": health if health in ("healthy", "warning", "unhealthy") else "unknown",
            "size": number("Size"), "temperature": number("Temperature"), "wear": number("Wear"),
        })
    return disks


def _read_disks() -> list[dict]:
    try:
        result = subprocess.run([_system32(r"WindowsPowerShell\v1.0\powershell.exe"), "-NoProfile", "-NonInteractive", "-Command", _DISK_SCRIPT],
                                capture_output=True, timeout=60, **_hidden())
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ToolError("Windows could not read disk health.") from error
    disks = parse_disks(result.stdout.decode("utf-8", "replace"))
    if not disks:
        raise ToolError("Windows could not read disk health.")
    return disks


def _stream(command, phase, progress, decode):
    """Run a console tool and report the last percentage it printed."""
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **_hidden())
    tail = ""
    while chunk := child.stdout.read(256):
        tail = (tail + decode(chunk))[-400:]
        value = last_percent(tail)
        if value is not None:
            progress(phase, value)
    return child.wait()


def _run_repair(progress) -> dict:
    """DISM repairs the component store first, then System File Checker repairs system files."""
    dism = _stream([_system32("Dism.exe"), "/Online", "/Cleanup-Image", "/RestoreHealth", "/English"], "dism", progress,
                   lambda chunk: chunk.decode("utf-8", "replace"))
    since = datetime.now().replace(microsecond=0)
    # sfc writes UTF-16 when its output is redirected.
    sfc = _stream([_system32("sfc.exe"), "/scannow"], "sfc", progress, lambda chunk: chunk.replace(b"\x00", b"").decode("utf-8", "replace"))
    findings = sfc_findings(_cbs_tail(), since)
    if findings["unrepaired"]:
        verdict = "unrepaired"
    elif findings["repaired"]:
        verdict = "repaired"
    elif sfc == 0 or findings["verified"]:
        verdict = "clean"
    else:
        verdict = "failed"
    return {"verdict": verdict, "dism_code": dism, "sfc_code": sfc, **findings}
