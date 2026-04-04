# TwinCAT TDD Workflow

## Pipeline Architecture

```
Variant → Test  →  UmRT Start (CONFIG)  →  TcUnit-Runner (Build + Activate + RUN + Test)  →  xUnit XML  →  JSON Report  →  Variant → Release
```

The pipeline uses TwinCAT Variants (TE1000) to switch between **Test** and **Release** configurations:
- **Test variant**: includes test FBs and TcUnit library (no `Release` define)
- **Release variant**: excludes test code via `{IF NOT defined (Release)}` pragmas

Variant switching is done via DTE COM (`scripts/twincat_variant.py`) before/after TcUnit-Runner.
TcUnit-Runner handles the Build → Activate → RUN → test collection chain.
The pipeline script (`scripts/twincat_tdd.py`) manages UmRT lifecycle, variant switching, and result parsing.

## TDD Cycle for Structured Text

### RED: Write a failing test first

1. Create a test FB extending `FB_TestSuite` (naming: `FB_<TargetName>_Test`)
2. Add test methods: `TEST('MethodName')` + assertions + `TEST_FINISHED()`
3. Register the test FB in `PRG_TEST`
4. Run `python scripts/twincat_tdd.py 2>tdd_log.txt`
5. Expect `TEST_FAIL` in JSON output

### GREEN: Minimal implementation

1. Edit the production FB/function to pass the test
2. Run pipeline — expect `PASS`
3. Do not add extra logic beyond what the test requires

### REFACTOR: Improve while green

1. Restructure production code or tests
2. Run pipeline — confirm `PASS` is maintained

## Test FB Structure

All test code must be wrapped in conditional compilation pragmas to exclude from Release builds:

```iec-st
{IF NOT defined (Release)}
FUNCTION_BLOCK FB_MyModule_Test EXTENDS FB_TestSuite
VAR
END_VAR
{END_IF}

// Methods also need the pragma:
{IF NOT defined (Release)}
METHOD TestSomeBehavior
    TEST('TestSomeBehavior');
    // Arrange
    // Act
    // Assert
    AssertEquals_INT(Expected := 42, Actual := result, Message := 'Expected 42');
    TEST_FINISHED();
END_METHOD
{END_IF}
```

## Test Program Registration

Test FBs are registered in `UnitTest` (or `PRG_TEST`), wrapped in pragmas:

```iec-st
PROGRAM UnitTest
VAR
{IF NOT defined (Release)}
    fbMyModuleTest : FB_MyModule_Test;
{END_IF}
END_VAR

{IF NOT defined (Release)}
fbMyModuleTest();
TcUnit.RUN();
{END_IF}
```

## Naming Conventions

| Item | Convention | Example |
|------|-----------|---------|
| Test FB | `FB_<Target>_Test` | `FB_JobScheduler_Test` |
| Test method | `Test<Behavior>` | `TestStartJobSucceeds` |
| Test program | `PRG_TEST` | — |
| Test task | `PlcTask` (shared) | — |

## JSON Output Interpretation

When the pipeline runs, parse stdout JSON:

- `status: "PASS"` → all green, proceed
- `status: "TEST_FAIL"` → read `suites[].test_cases[]` for failures
- `status: "ERROR"` → infrastructure problem (DTE, runner, XML)
- `status: "UMRT_ERROR"` → Usermode Runtime did not start
- `status: "TIMEOUT"` → increase `timeout_minutes` or investigate hang

## Automatic Test Trigger

Run the pipeline after any `.TcPOU`, `.TcGVL`, or `.TcDUT` file modification:

```bash
python scripts/twincat_tdd.py 2>tdd_log.txt
```

stderr contains human-readable logs; stdout contains machine-readable JSON.

## Variant Configuration

Config in `scripts/twincat_tdd_config.json`:

| Key | Default | Description |
|-----|---------|-------------|
| `variant_test` | `"Test"` | Variant activated before test run |
| `variant_release` | `"Release"` | Variant restored after test run |

Set either to `""` to skip variant switching (e.g. during initial setup before Variants are configured).
