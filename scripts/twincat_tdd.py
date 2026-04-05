"""
TwinCAT TDD Pipeline - UmRT + TcUnit-Runner orchestration.

Pipeline for local TDD loops and Claude Code / ECC integration:
    1. Ensure the Usermode Runtime is running (CONFIG state)
    2. Run TcUnit-Runner (builds, activates, runs PLC, collects xUnit results)
    3. Parse xUnit XML and emit a machine-readable JSON summary on stdout
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


TCUNIT_RUNNER_EXE = r"C:\Program Files (x86)\TcUnit-Runner\TcUnit-Runner.exe"
DEFAULT_UMRT_PATH = r"C:\ProgramData\Beckhoff\TwinCAT\3.1\Runtimes\UmRT_Default"
DEFAULT_ADS_DLL = r"C:\Program Files (x86)\Beckhoff\TwinCAT\3.1\Components\Base\v170\TwinCAT.Ads.dll"
UMRT_SYSTEM_PORT = 300
UMRT_SYSMANAGER_PORT = 10000
MAX_WATCHDOG_RESTARTS = 10


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PipelineConfig:
    solution_path: str
    tcunit_task_name: str = ""
    ams_net_id: str = ""
    tc_version: str = ""
    umrt_instance_path: str = DEFAULT_UMRT_PATH
    timeout_minutes: int = 5
    tcunit_runner_exe: str = TCUNIT_RUNNER_EXE
    ads_dll_path: str = DEFAULT_ADS_DLL
    variant_test: str = "Test"
    variant_release: str = "Release"
    leave_running: bool = True


@dataclass
class TestCaseResult:
    name: str
    classname: str
    status: str
    duration: float = 0.0
    failure_message: str = ""


@dataclass
class TestSuiteResult:
    name: str
    tests: int
    failures: int
    duration: float
    test_cases: list = field(default_factory=list)


@dataclass
class TestResults:
    total_tests: int = 0
    total_failures: int = 0
    total_duration: float = 0.0
    suites: list = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return self.total_failures == 0 and self.total_tests > 0


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(phase: str, message: str, error: bool = False):
    level = "ERROR" if error else "INFO"
    print(f"[{level}] [{phase}] {message}", file=sys.stderr)


def ads_set_state(ams_net_id: str, port: int, ads_dll: str, state: str) -> tuple[bool, str]:
    """Send an ADS state change command via PowerShell + TwinCAT.Ads.dll."""
    ps = (
        f"$ErrorActionPreference='Stop'; "
        f"Add-Type -Path '{ads_dll}'; "
        f"$c = New-Object TwinCAT.Ads.TcAdsClient; "
        f"try {{ "
        f"$c.Connect('{ams_net_id}', {port}); "
        f"$t = New-Object TwinCAT.Ads.StateInfo "
        f"([TwinCAT.Ads.AdsState]::{state}),([int16]0); "
        f"$c.WriteControl($t); Start-Sleep -Milliseconds 500; "
        f"$s=$c.ReadState(); "
        f"Write-Output ($s.AdsState.ToString() + '/' + $s.DeviceState.ToString()) "
        f"}} catch {{ Write-Output ('ERROR: ' + $_.Exception.InnerException.Message) }} "
        f"finally {{ $c.Dispose() }}"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=15,
    )
    out = (proc.stdout or "").strip()
    return proc.returncode == 0 and not out.startswith("ERROR"), out


def ads_read_state(ams_net_id: str, port: int, ads_dll: str) -> str:
    """Read ADS state via PowerShell. Returns 'Run/0', 'Config/1', etc. or 'ERROR:...'."""
    ps = (
        f"$ErrorActionPreference='Stop'; "
        f"Add-Type -Path '{ads_dll}'; "
        f"$c = New-Object TwinCAT.Ads.TcAdsClient; "
        f"try {{ "
        f"$c.Connect('{ams_net_id}', {port}); "
        f"$s=$c.ReadState(); "
        f"Write-Output ($s.AdsState.ToString() + '/' + $s.DeviceState.ToString()) "
        f"}} catch {{ Write-Output ('ERROR: ' + $_.Exception.Message) }} "
        f"finally {{ $c.Dispose() }}"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=10,
    )
    return (proc.stdout or "").strip()


# ---------------------------------------------------------------------------
# UmRT Manager
# ---------------------------------------------------------------------------

class UmRTManager:
    """Start / monitor / auto-restart the TwinCAT Usermode Runtime.

    TcUnit-Runner's ActivateConfiguration() kills the UmRT process.
    Unlike the TwinCAT Windows service, UmRT has no service manager to
    auto-restart it.  The watchdog thread detects the crash and respawns
    the process so that StartRestartTwinCAT() (called ~20 s later by
    TcUnit-Runner) finds a live runtime.
    """

    def __init__(self, instance_path: str):
        self.instance_path = Path(instance_path)
        self.tc_registry = self.instance_path / "3.1" / "TcRegistry.xml"
        self.instance_name = self.instance_path.name
        self.process: Optional[subprocess.Popen] = None
        self._output_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._state_lock = threading.Lock()
        self._last_state = ""
        self._restart_count = 0
        self._watchdog_active = False
        self._exe_path: Optional[Path] = None
        self._ams_net_id = ""
        self._ads_dll = DEFAULT_ADS_DLL

    def is_running(self) -> bool:
        result = subprocess.run(
            ["tasklist"], capture_output=True, text=True, timeout=10,
        )
        return "TcSystemServiceUm.exe" in result.stdout

    def get_ams_net_id(self) -> str:
        if not self.tc_registry.exists():
            return ""
        try:
            tree = ET.parse(str(self.tc_registry))
            for val in tree.iter("Value"):
                if (val.get("Name") == "AmsNetId"
                        and val.get("Type") == "BIN"
                        and val.text):
                    octets = bytes.fromhex(val.text.strip())
                    return ".".join(str(b) for b in octets)
        except Exception:
            pass
        return ""

    def start(self) -> bool:
        if self.is_running():
            log("UMRT", "Killing stale UmRT instance")
            subprocess.run(
                ["taskkill", "/IM", "TcSystemServiceUm.exe", "/F"],
                capture_output=True, text=True, timeout=15,
            )
            time.sleep(2)

        twincat_dir = os.environ.get("TWINCAT3DIR", "")
        if not twincat_dir:
            log("UMRT", "TWINCAT3DIR not set", error=True)
            return False

        self._exe_path = Path(twincat_dir) / "Runtimes" / "bin" / "TcSystemServiceUm.exe"
        if not self._exe_path.exists():
            log("UMRT", f"TcSystemServiceUm.exe not found: {self._exe_path}", error=True)
            return False

        return self._spawn_and_wait_config()

    def start_watchdog(self, ams_net_id: str = "", ads_dll: str = "") -> None:
        """Start the watchdog thread that auto-restarts UmRT if it dies."""
        self._ams_net_id = ams_net_id
        self._ads_dll = ads_dll or DEFAULT_ADS_DLL
        self._watchdog_active = True
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog_thread.start()
        log("UMRT", "Watchdog started")

    def stop_watchdog(self) -> None:
        self._watchdog_active = False

    def restart_to_run(self, ams_net_id: str = "", ads_dll: str = "") -> bool:
        """Restart UmRT and boot to RUN state via ADS Reset.

        Sequence: kill stale → start (CONFIG) → ADS Reset → (boot project loads) → RUN.
        """
        if not self.start():
            return False
        _ams = ams_net_id or self._ams_net_id or self.get_ams_net_id()
        _dll = ads_dll or self._ads_dll
        if not _ams:
            log("UMRT", "No AmsNetId for RUN transition", error=True)
            return False
        log("UMRT", "Sending ADS Reset to System Manager port")
        ok, result = ads_set_state(_ams, UMRT_SYSMANAGER_PORT, _dll, "Reset")
        if not ok:
            log("UMRT", f"ADS Reset failed: {result}", error=True)
            return False
        # Wait for boot sequence to complete then verify PLC port is alive
        log("UMRT", f"ADS Reset sent (initial: {result}), waiting for PLC boot...")
        for attempt in range(15):
            time.sleep(2)
            state = ads_read_state(_ams, 851, _dll)  # Check PLC port, not system manager
            if state.startswith("Run"):
                log("UMRT", f"PLC reached RUN state ({(attempt+1)*2}s)")
                return True
            log("UMRT", f"PLC state poll {attempt+1}: {state}")
        log("UMRT", f"PLC did not reach RUN within 30s (last: {state})", error=True)
        return False

    # -- internal helpers --------------------------------------------------

    def _spawn_and_wait_config(self) -> bool:
        assert self._exe_path is not None
        log("UMRT", f"Starting UmRT via {self._exe_path}")
        with self._state_lock:
            self._last_state = ""
        self.process = subprocess.Popen(
            [str(self._exe_path), "-t", "bin", "-i", "path",
             "-n", self.instance_name, "-c", ".\\3.1"],
            cwd=str(self.instance_path),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        self._start_output_monitor()

        if not self._wait_for_state("config", timeout_seconds=45):
            log("UMRT", "UmRT did not reach CONFIG state", error=True)
            return False

        log("UMRT", "UmRT reached CONFIG state")
        return True

    def _watchdog_loop(self) -> None:
        """Poll every 2 s; if the process died, respawn it.

        TcUnit-Runner's StartRestartTwinCAT() kills the UmRT process.
        Unlike the TwinCAT Windows service there is no service manager
        to auto-restart it — that is our job.

        After respawn, UmRT starts in CONFIG.  We send 'c' (Reconfig)
        via stdin to trigger the boot sequence:
            CONFIG → Reconfig → (load boot files) → RUN
        """
        while self._watchdog_active:
            time.sleep(2)
            if not self._watchdog_active:
                return
            if self.process is None:
                continue
            if self.process.poll() is not None:
                self._restart_count += 1
                log("UMRT", f"UmRT process died (exit={self.process.returncode}, restart #{self._restart_count})")
                if self._restart_count > MAX_WATCHDOG_RESTARTS:
                    log("UMRT", f"Exceeded max restarts ({MAX_WATCHDOG_RESTARTS}), stopping watchdog", error=True)
                    self._watchdog_active = False
                    return
                time.sleep(1)
                try:
                    ok = self._spawn_and_wait_config()
                    if not ok:
                        log("UMRT", "UmRT restart failed", error=True)
                        continue
                    log("UMRT", f"UmRT restarted to CONFIG (restart #{self._restart_count})")
                    self._send_reconfig_via_stdin()
                except Exception as exc:
                    log("UMRT", f"UmRT restart error: {exc}", error=True)

    def _send_reconfig_via_stdin(self) -> None:
        """Send 'c' to UmRT stdin to trigger Reconfig boot sequence.

        Equivalent to pressing 'c' in the UmRT console window.
        CONFIG → Reconfig → (reads CurrentConfig.xml + boot project) → RUN.
        """
        if self.process is None or self.process.stdin is None:
            log("UMRT", "Cannot send Reconfig: no stdin", error=True)
            return
        try:
            log("UMRT", "Sending 'c' (Reconfig) via stdin")
            self.process.stdin.write("c\n")
            self.process.stdin.flush()
        except Exception as exc:
            log("UMRT", f"stdin Reconfig failed: {exc}", error=True)

    def _send_reconfig_via_ads(self) -> None:
        """Send CONFIG→Reconfig→(auto)RUN via ADS to the system port.

        A plain CONFIG→RUN skips PLC loading.  Reconfig triggers the boot
        sequence which reads CurrentConfig.xml, loads Port_851.app, and
        transitions the PLC to RUN (because Port_851.autostart exists).
        """
        if not self._ams_net_id or not os.path.exists(self._ads_dll):
            log("UMRT", "Cannot send Reconfig: missing AmsNetId or ADS DLL", error=True)
            return
        log("UMRT", f"Sending Reconfig via ADS to {self._ams_net_id}:{UMRT_SYSTEM_PORT}")
        ok, result = ads_set_state(
            self._ams_net_id, UMRT_SYSTEM_PORT, self._ads_dll, "Reconfig",
        )
        if ok:
            log("UMRT", f"ADS Reconfig result: {result}")
        else:
            log("UMRT", f"ADS Reconfig failed: {result}", error=True)

    def _start_output_monitor(self) -> None:
        # Always start a new thread for the new process handle
        self._output_thread = threading.Thread(target=self._consume_output, daemon=True)
        self._output_thread.start()

    def _consume_output(self) -> None:
        proc = self.process
        if proc is None or proc.stdout is None:
            return
        while True:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    return
                time.sleep(0.1)
                continue
            line = line.strip()
            if not line:
                continue
            log("UMRT", line)
            lowered = line.lower()
            if "state:" in lowered:
                state_text = lowered.split("state:", 1)[1].strip().split()[0]
                with self._state_lock:
                    self._last_state = state_text

    def _wait_for_state(self, expected: str, timeout_seconds: int) -> bool:
        deadline = time.time() + timeout_seconds
        expected = expected.strip().lower()
        while time.time() < deadline:
            if self.process is not None and self.process.poll() is not None:
                return False
            with self._state_lock:
                if self._last_state == expected:
                    return True
            time.sleep(0.2)
        with self._state_lock:
            return self._last_state == expected


# ---------------------------------------------------------------------------
# TcUnit-Runner
# ---------------------------------------------------------------------------

def run_tcunit_runner(config: PipelineConfig) -> tuple[int, str, str]:
    """Run TcUnit-Runner and return (exit_code, log_output, xunit_xml_path)."""
    exe = config.tcunit_runner_exe
    if not os.path.exists(exe):
        raise FileNotFoundError(f"TcUnit-Runner not found: {exe}")

    solution = str(Path(config.solution_path).resolve())
    cmd = [exe, f"--VisualStudioSolutionFilePath={solution}"]
    if config.tcunit_task_name:
        cmd.append(f"--TcUnitTaskName={config.tcunit_task_name}")
    if config.ams_net_id:
        cmd.append(f"--AmsNetId={config.ams_net_id}")
    if config.tc_version:
        cmd.append(f"--TcVersion={config.tc_version}")
    if config.timeout_minutes:
        cmd.append(f"--Timeout={config.timeout_minutes}")

    log("TEST", f"Running: {' '.join(cmd)}")
    proc = subprocess.run(
        cmd,
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        cwd=str(Path(solution).parent),
        timeout=config.timeout_minutes * 60 + 120,
    )

    log_output = (proc.stdout or "") + (proc.stderr or "")
    log("TEST", f"TcUnit-Runner exit code: {proc.returncode}")
    log_path = str(Path(solution).parent / "tcunit_runner.log")
    Path(log_path).write_text(log_output, encoding="utf-8")
    for line in log_output.splitlines():
        if line.strip():
            log("TEST", line.strip())

    xunit_path = str(Path(solution).parent / "TcUnit_xUnit_results.xml")
    return proc.returncode, log_output, xunit_path


# ---------------------------------------------------------------------------
# xUnit XML parsing
# ---------------------------------------------------------------------------

def parse_xunit_xml(xml_path: str) -> Optional[TestResults]:
    if not os.path.exists(xml_path):
        return None
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError:
        return None

    root = tree.getroot()
    results = TestResults()

    suite_elements = root.findall(".//testsuite") if root.tag == "testsuites" else [root]
    if root.tag == "testsuite":
        suite_elements = [root]

    for suite_el in suite_elements:
        suite = TestSuiteResult(
            name=suite_el.get("name", ""),
            tests=int(suite_el.get("tests", 0)),
            failures=int(suite_el.get("failures", 0)),
            duration=float(suite_el.get("time", 0)),
        )
        for tc_el in suite_el.findall("testcase"):
            fail_el = tc_el.find("failure")
            suite.test_cases.append(TestCaseResult(
                name=tc_el.get("name", ""),
                classname=tc_el.get("classname", ""),
                status="FAIL" if fail_el is not None else "PASS",
                duration=float(tc_el.get("time", 0)),
                failure_message=fail_el.get("message", "") if fail_el is not None else "",
            ))
        results.suites.append(suite)

    results.total_tests = sum(s.tests for s in results.suites)
    results.total_failures = sum(s.failures for s in results.suites)
    results.total_duration = sum(s.duration for s in results.suites)
    return results


# ---------------------------------------------------------------------------
# JSON report
# ---------------------------------------------------------------------------

def build_report(
    *, status: str, phase: str, exit_code: int,
    message: str = "", results: Optional[TestResults] = None,
    artifacts: Optional[dict] = None,
) -> str:
    data: dict = {
        "status": status,
        "phase": phase,
        "exit_code": exit_code,
        "message": message,
        "artifacts": artifacts or {},
    }
    if results is not None:
        data["summary"] = {
            "total": results.total_tests,
            "passed": results.total_tests - results.total_failures,
            "failed": results.total_failures,
            "suites": len(results.suites),
            "duration": results.total_duration,
        }
        data["suites"] = [
            {
                "name": s.name,
                "tests": s.tests,
                "failures": s.failures,
                "duration": s.duration,
                "test_cases": [
                    {
                        "name": tc.name,
                        "classname": tc.classname,
                        "status": tc.status,
                        "duration": tc.duration,
                        "failure_message": tc.failure_message,
                    }
                    for tc in s.test_cases
                ],
            }
            for s in results.suites
        ]
    return json.dumps(data, indent=2, ensure_ascii=False)


def format_markdown(results: TestResults) -> str:
    lines = ["## TcUnit Test Results", ""]
    passed = results.total_tests - results.total_failures
    status = "PASS" if results.all_passed else "TEST_FAIL"
    lines.append(f"**Status**: {status} ({passed}/{results.total_tests} passed)")
    lines.append(f"**Duration**: {results.total_duration:.3f}s")
    lines.append("")
    if results.suites:
        lines.append("| Suite | Test | Result | Duration | Message |")
        lines.append("|-------|------|--------|----------|---------|")
        for s in results.suites:
            for tc in s.test_cases:
                lines.append(
                    f"| {s.name} | {tc.name} | {tc.status} "
                    f"| {tc.duration:.3f}s | {tc.failure_message} |"
                )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def emit(report: str) -> None:
    """Print JSON report to stdout (machine-readable channel)."""
    print(report)


def run_pipeline(config: PipelineConfig) -> int:
    solution = str(Path(config.solution_path).resolve())
    artifacts: dict = {"solution_path": solution}

    # -- 1. UmRT ----------------------------------------------------------
    umrt = UmRTManager(config.umrt_instance_path)

    detected_id = umrt.get_ams_net_id()
    if detected_id:
        config.ams_net_id = detected_id
        log("UMRT", f"Detected AmsNetId: {config.ams_net_id}")
    if config.ams_net_id:
        artifacts["ams_net_id"] = config.ams_net_id

    if not umrt.start():
        emit(build_report(
            status="UMRT_ERROR", phase="runtime", exit_code=1,
            message="Failed to start Usermode Runtime", artifacts=artifacts,
        ))
        return 1

    # -- 1.5. Activate Test variant ----------------------------------------
    from twincat_variant import activate_variant
    if config.variant_test:
        if not activate_variant(solution, config.variant_test):
            emit(build_report(
                status="ERROR", phase="variant", exit_code=1,
                message=f"Failed to activate Test variant '{config.variant_test}'",
                artifacts=artifacts,
            ))
            return 1

    # -- 2-4. TcUnit-Runner + parse + cleanup (try-finally protected) -----
    pipeline_rc = 1
    try:
        # Delete stale xUnit XML so build failures aren't masked by old results
        xunit_path = str(Path(solution).parent / "TcUnit_xUnit_results.xml")
        if os.path.exists(xunit_path):
            os.remove(xunit_path)
            log("TEST", "Deleted stale xUnit XML")

        # -- 2. TcUnit-Runner with UmRT watchdog --------------------------
        umrt.start_watchdog(ams_net_id=config.ams_net_id, ads_dll=config.ads_dll_path)
        try:
            exit_code, log_output, xunit_path = run_tcunit_runner(config)
        except FileNotFoundError as exc:
            emit(build_report(
                status="ERROR", phase="test", exit_code=1,
                message=str(exc), artifacts=artifacts,
            ))
            return 1
        except subprocess.TimeoutExpired:
            emit(build_report(
                status="TIMEOUT", phase="test", exit_code=1,
                message="TcUnit-Runner timed out", artifacts=artifacts,
            ))
            return 1
        finally:
            umrt.stop_watchdog()

        artifacts["tcunit_log"] = str(Path(solution).parent / "tcunit_runner.log")
        artifacts["xunit_xml"] = xunit_path

        # -- 3. Check for build errors before parsing XML -----------------
        build_error = any(
            marker in log_output
            for marker in ["ERROR: Build errors", "ERROR: Failed to build"]
        )
        if build_error:
            emit(build_report(
                status="BUILD_ERROR", phase="test", exit_code=exit_code,
                message="TcUnit-Runner reported build errors",
                artifacts=artifacts,
            ))
            return 1

        # -- 3b. Parse results --------------------------------------------
        results = parse_xunit_xml(xunit_path)

        if results is None:
            lowered = log_output.lower()
            if "error loading vs dte" in lowered:
                msg = "TcUnit-Runner could not load Visual Studio DTE"
            elif "timeout" in lowered:
                msg = "TcUnit-Runner timed out"
            else:
                msg = "xUnit XML was not produced"
            emit(build_report(
                status="ERROR", phase="test", exit_code=exit_code,
                message=msg, artifacts=artifacts,
            ))
            return 1

        status = "PASS" if results.all_passed else "TEST_FAIL"
        emit(build_report(
            status=status, phase="test", exit_code=exit_code,
            message="" if results.all_passed else "One or more TcUnit tests failed",
            results=results, artifacts=artifacts,
        ))
        log("TEST", "\n" + format_markdown(results))
        pipeline_rc = 0 if results.all_passed else 1

    finally:
        # -- 4. ALWAYS restore Release variant ----------------------------
        if config.variant_release:
            if activate_variant(solution, config.variant_release):
                log("VARIANT", f"Restored variant: {config.variant_release}")
            else:
                log("VARIANT", f"Warning: failed to restore '{config.variant_release}'", error=True)

        # -- 5. Leave UmRT in RUN state for post-pipeline use -------------
        if config.leave_running:
            log("UMRT", "Restarting UmRT to RUN state")
            if not umrt.restart_to_run(ams_net_id=config.ams_net_id, ads_dll=config.ads_dll_path):
                log("UMRT", "Warning: could not restart UmRT to RUN", error=True)

    return pipeline_rc


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(config_path: str, solution_override: str) -> PipelineConfig:
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        fields = PipelineConfig.__dataclass_fields__
        values = {k: v for k, v in raw.items() if k in fields}
        config = PipelineConfig(**values)
    elif solution_override:
        config = PipelineConfig(solution_path=solution_override)
    else:
        raise ValueError("No config file and no --solution provided")

    if solution_override:
        config.solution_path = solution_override
    return config


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="TwinCAT TDD Pipeline")
    parser.add_argument("--config", "-c", help="Config JSON path")
    parser.add_argument("--solution", "-s", help="Path to .sln file")
    parser.add_argument("--task", "-t", help="TcUnit task name")
    parser.add_argument("--ams-net-id", "-a", help="AMS NetId override")
    parser.add_argument("--tc-version", help="TwinCAT version")
    parser.add_argument("--timeout", "-u", type=int, help="Timeout in minutes")
    parser.add_argument("--leave-running", action="store_true", default=True,
                        help="Leave UmRT in RUN state after pipeline (default)")
    parser.add_argument("--no-leave-running", dest="leave_running", action="store_false",
                        help="Do not restart UmRT after pipeline")
    args = parser.parse_args()

    config_path = args.config or os.path.join(
        os.path.dirname(__file__), "twincat_tdd_config.json",
    )
    try:
        config = load_config(config_path, args.solution)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.task:
        config.tcunit_task_name = args.task
    if args.ams_net_id:
        config.ams_net_id = args.ams_net_id
    if args.tc_version:
        config.tc_version = args.tc_version
    if args.timeout:
        config.timeout_minutes = args.timeout
    config.leave_running = args.leave_running

    sys.exit(run_pipeline(config))


if __name__ == "__main__":
    main()
