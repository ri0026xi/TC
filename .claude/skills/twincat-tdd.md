# TwinCAT TDD Pipeline

Run the TwinCAT TDD pipeline: UmRT start → TcUnit-Runner (Build + Activate + RUN + test) → xUnit parsing.

## When to Use

- After writing or modifying PLC code (.TcPOU, .TcGVL, .TcDUT)
- When the user asks to run tests, build, or verify TcUnit
- As part of TDD workflow (RED / GREEN / REFACTOR)
- To validate that a PLC change compiles and passes all unit tests

## How to Run

```bash
python scripts/twincat_tdd.py 2>tdd_log.txt
```

With options:
```bash
python scripts/twincat_tdd.py --task PlcTask --timeout 10 2>tdd_log.txt
python scripts/twincat_tdd.py --solution "C:\path\to\solution.sln" 2>tdd_log.txt
```

## TDD Cycle (TwinCAT / Structured Text)

### RED: Write a failing test

1. Create or edit a test Function Block (FB) that extends `FB_TestSuite`
2. Add test methods with assertions (`AssertEquals`, `AssertTrue`, etc.)
3. Register the test FB in `PRG_TEST` so TcUnit discovers it
4. Run the pipeline — expect `TEST_FAIL` or new test cases in output

### GREEN: Make the test pass

1. Edit the production FB / function / GVL to implement the logic
2. Run the pipeline — expect `PASS`
3. If `BUILD_ERROR` or `ERROR`: read the failure message and fix

### REFACTOR: Improve while green

1. Refactor production code (extract methods, rename, simplify)
2. Run the pipeline — confirm still `PASS`
3. Refactor test code if needed

## Interpreting Results

JSON output on stdout:

| Status | Meaning |
|--------|---------|
| `PASS` | All TcUnit tests passed |
| `TEST_FAIL` | One or more tests failed — read `suites[].test_cases[]` |
| `UMRT_ERROR` | Usermode Runtime failed to start |
| `TIMEOUT` | TcUnit-Runner timed out |
| `ERROR` | Runner missing, DTE load failure, or xUnit XML not produced |

Key fields:
- `phase` — `runtime` or `test`
- `summary.total`, `summary.passed`, `summary.failed`
- `suites[].test_cases[].failure_message` — individual failure details
- `artifacts.tcunit_log` — full TcUnit-Runner log
- `artifacts.xunit_xml` — xUnit XML path

## After Results

- **TEST_FAIL**: Read failure messages, locate the test FB (.TcPOU), suggest fixes to production code
- **ERROR / UMRT_ERROR**: Read `tcunit_runner.log` for diagnostics
- **PASS**: Proceed to next TDD step (refactor or next RED)

## Prerequisites

- TcUnit-Runner installed at `C:\Program Files (x86)\TcUnit-Runner\`
- TcUnit library referenced in PLC project
- Usermode Runtime configured at `C:\ProgramData\Beckhoff\TwinCAT\3.1\Runtimes\UmRT_Default`
- TwinCAT XAE (Visual Studio or TcXaeShell) installed
- `TWINCAT3DIR` environment variable set

## Config

Edit `scripts/twincat_tdd_config.json`:

```json
{
    "solution_path": "...",
    "tcunit_task_name": "PlcTask",
    "ams_net_id": "",
    "tc_version": "",
    "umrt_instance_path": "...",
    "timeout_minutes": 5,
    "tcunit_runner_exe": "..."
}
```

Leave `ams_net_id` empty for auto-detection from UmRT TcRegistry.xml.
