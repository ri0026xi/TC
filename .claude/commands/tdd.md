# TwinCAT Autonomous TDD Command

Fully autonomous TDD loop for TwinCAT Structured Text. Handles code writing, build, test execution, and iterative fixing without manual intervention.

## Arguments

$ARGUMENTS — Task description: what feature to implement, bug to fix, or test to add.

## Instructions

You are an autonomous TDD agent for TwinCAT PLC (Structured Text + TcUnit). Follow the RED → GREEN → REFACTOR cycle end-to-end. Do NOT ask the user for help mid-cycle — solve problems yourself.

### Phase 0: Understand the Task

1. Read the task description from `$ARGUMENTS`.
2. Read existing production code under `PLC_JobManagementFramework/JobManagementFramework/JobManagement/POUs/model/` to understand current architecture.
3. Read existing tests under `POUs/test/` to understand patterns in use.
4. Identify the target FB/function/program and what behavior needs testing.

### Phase 1: RED — Write Failing Test

1. **Create or edit a test FB** (`.TcPOU` XML file) under `POUs/test/`:
   - Naming: `FB_<TargetName>_Test` extending `TcUnit.FB_TestSuite`
   - Each test method: `TEST('MethodName')` → Arrange → Act → Assert → `TEST_FINISHED()`
   - Use TcUnit assertions: `AssertEquals_INT`, `AssertEquals_BOOL`, `AssertTrue`, `AssertFalse`, `AssertEquals_STRING`, etc.

2. **TcPOU XML structure** — follow this exact template:
   ```xml
   <?xml version="1.0" encoding="utf-8"?>
   <TcPlcObject Version="1.1.0.1" ProductVersion="3.1.4024.15">
     <POU Name="FB_Example_Test" Id="{generate-new-guid}" SpecialFunc="None">
       <Declaration><![CDATA[FUNCTION_BLOCK FB_Example_Test EXTENDS TcUnit.FB_TestSuite
   VAR
   END_VAR
   ]]></Declaration>
       <Implementation>
         <ST><![CDATA[TestMethodName();]]></ST>
       </Implementation>
       <Method Name="TestMethodName" Id="{generate-new-guid}">
         <Declaration><![CDATA[METHOD PRIVATE TestMethodName
   VAR
   END_VAR]]></Declaration>
         <Implementation>
           <ST><![CDATA[TEST('TestMethodName');
   // Arrange
   // Act
   // Assert
   AssertTrue(Condition := FALSE, Message := 'Not yet implemented');
   TEST_FINISHED();]]></ST>
         </Implementation>
       </Method>
     </POU>
   </TcPlcObject>
   ```

3. **Register in UnitTest program** (`POUs/test/UnitTest.TcPOU`):
   - Add instance VAR: `fbExample_Test : FB_Example_Test;`
   - Add call in body: `fbExample_Test();` (before `TcUnit.RUN();`)

4. **Register in .plcproj** (`JobManagement.plcproj`):
   - Add `<Compile Include="POUs\test\FB_Example_Test.TcPOU"><SubType>Code</SubType></Compile>` in the ItemGroup with other Compile entries.

5. **Generate GUIDs**: Use `python -c "import uuid; print('{' + str(uuid.uuid4()) + '}')"` to create unique IDs for POU and Method elements.

6. **Run the pipeline** to confirm RED:
   ```bash
   python scripts/twincat_tdd.py 2>tdd_log.txt
   ```

7. Parse JSON from stdout. Expected: `status: "TEST_FAIL"` with your new test failing.
   - If `status: "ERROR"` or `status: "UMRT_ERROR"`: read `tdd_log.txt`, diagnose, fix, re-run.
   - If build error: fix the .TcPOU XML syntax and re-run.

### Phase 2: GREEN — Minimal Implementation

1. Edit the production FB/function `.TcPOU` file to implement the **minimum code** to pass the failing test.
2. Do NOT add extra logic beyond what the test requires.
3. Run the pipeline:
   ```bash
   python scripts/twincat_tdd.py 2>tdd_log.txt
   ```
4. Parse JSON. Expected: `status: "PASS"`.
   - If still `TEST_FAIL`: read `failure_message`, adjust implementation, re-run.
   - Loop up to 5 iterations. If still failing after 5 attempts, report the situation with diagnostics.

### Phase 3: REFACTOR — Improve While Green

1. If the production code or test code can be improved (naming, structure, duplication), do it now.
2. Run the pipeline again to confirm `PASS` is maintained.
3. If refactoring breaks tests, revert the refactoring change and try a different approach.

### Phase 4: Report

Report results in this format:

```
## TDD Result: [PASS/FAIL]

### Cycle Summary
- RED: [test name] — wrote failing test for [behavior]
- GREEN: [what was implemented] — [N] iterations to pass
- REFACTOR: [what was improved, or "no refactoring needed"]

### Test Results
| Suite | Test | Result |
|-------|------|--------|
| ... | ... | PASS/FAIL |

Status: PASS
Tests: X passed, Y failed (Z total)
```

## Error Recovery

### Build Errors
- Read `tdd_log.txt` for detailed TcUnit-Runner output
- Common causes: missing semicolons, undeclared variables, wrong type names
- Fix the .TcPOU file and re-run

### UMRT_ERROR
> UmRTが起動していません。手動で起動してください：
> UmRT_Default のコンソールウィンドウで CONFIG 状態を確認してから再実行してください。

### TIMEOUT
- Check if UmRT is still running (console window visible)
- Read `tdd_log.txt` for TcUnit-Runner's last output
- May indicate PLC stuck in infinite loop — review production code

### Repeated TEST_FAIL After 5 Iterations
- Show all failure messages from each iteration
- Show the current production code
- Report what was tried and what failed
- Let the user decide next steps

## Key Paths

```
Project root:     PLC_JobManagementFramework/JobManagementFramework/JobManagement/
Production code:  POUs/model/library/    (FBs, interfaces)
Activities:       POUs/model/activities/ (Future FBs)
Tests:            POUs/test/             (test FBs + UnitTest program)
Project file:     JobManagement.plcproj
Pipeline:         scripts/twincat_tdd.py
Pipeline config:  scripts/twincat_tdd_config.json
```

## Prerequisites

- UmRT must be running (pipeline starts it automatically, but manual start is more reliable)
- .sln VisualStudioVersion must be 15.0 (32-bit TcXaeShell — already configured)
- No other TcXaeShell instances should be open

## Multiple Tests

If the task requires multiple test cases:
1. Write ALL test methods in the RED phase
2. Implement incrementally — one test at a time in GREEN phase
3. Run pipeline after each implementation change
4. Continue until all tests pass
