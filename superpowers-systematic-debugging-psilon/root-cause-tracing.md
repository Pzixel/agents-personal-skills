# Root Cause Tracing

## Overview

Bugs often manifest deep in the call stack (git init in wrong directory, file created in wrong location, database opened with wrong path). Your instinct is to fix where the error appears, but that's treating a symptom.

**Core principle:** Trace backward through the call chain until you find the original trigger, then fix at the source.

## When to Use

```dot
digraph when_to_use {
    "Bug appears deep in stack?" [shape=diamond];
    "Can trace backwards?" [shape=diamond];
    "More evidence available and authorized?" [shape=diamond];
    "Gather boundary evidence or authorized instrumentation" [shape=box];
    "BLOCKED - root cause remains unknown" [shape=doublecircle];
    "Trace to original trigger" [shape=box];
    "Fix at source" [shape=box];
    "Distinct evidenced boundary failure mode?" [shape=diamond];
    "Add minimum boundary safeguard" [shape=box];
    "Verify fix and supported paths" [shape=doublecircle];

    "Bug appears deep in stack?" -> "Can trace backwards?" [label="yes"];
    "Can trace backwards?" -> "Trace to original trigger" [label="yes"];
    "Can trace backwards?" -> "More evidence available and authorized?" [label="no"];
    "More evidence available and authorized?" -> "Gather boundary evidence or authorized instrumentation" [label="yes"];
    "More evidence available and authorized?" -> "BLOCKED - root cause remains unknown" [label="no"];
    "Gather boundary evidence or authorized instrumentation" -> "Can trace backwards?";
    "Trace to original trigger" -> "Fix at source";
    "Fix at source" -> "Distinct evidenced boundary failure mode?";
    "Distinct evidenced boundary failure mode?" -> "Add minimum boundary safeguard" [label="yes"];
    "Distinct evidenced boundary failure mode?" -> "Verify fix and supported paths" [label="no"];
    "Add minimum boundary safeguard" -> "Verify fix and supported paths";
}
```

**Use when:**
- Error happens deep in execution (not at entry point)
- Stack trace shows long call chain
- Unclear where invalid data originated
- Need to find which test/code triggers the problem

## The Tracing Process

### 1. Observe the Symptom
```
Error: git init failed in ~/project/packages/core
```

### 2. Find Immediate Cause
**What code directly causes this?**
```typescript
await execFileAsync('git', ['init'], { cwd: projectDir });
```

### 3. Ask: What Called This?
```typescript
WorktreeManager.createSessionWorktree(projectDir, sessionId)
  → called by Session.initializeWorkspace()
  → called by Session.create()
  → called by test at Project.create()
```

### 4. Keep Tracing Up
**What value was passed?**
- `projectDir = ''` (empty string!)
- Empty string as `cwd` resolves to `process.cwd()`
- That's the source code directory!

### 5. Find Original Trigger
**Where did empty string come from?**
```typescript
const context = setupCoreTest(); // Returns { tempDir: '' }
Project.create('name', context.tempDir); // Accessed before beforeEach!
```

## Adding Stack Traces

When read-only evidence cannot trace the call chain, add temporary
instrumentation only when governing authority permits it:

```typescript
// Before the problematic operation
async function gitInit(directory: string) {
  const stack = new Error().stack;
  console.error('DEBUG git init:', {
    directory,
    cwd: process.cwd(),
    nodeEnv: process.env.NODE_ENV,
    stack,
  });

  await execFileAsync('git', ['init'], { cwd: directory });
}
```

**Critical:** Use `console.error()` in tests (not logger - may not show)

**Run and capture:**
```bash
npm test 2>&1 | grep 'DEBUG git init'
```

**Analyze stack traces:**
- Look for test file names
- Find the line number triggering the call
- Identify the pattern (same test? same parameter?)

## Finding Which Test Causes Pollution

If something appears during tests but you don't know which test:

Use the bisection script `find-polluter.sh` in this directory:

```bash
./find-polluter.sh '.git' 'src/**/*.test.ts'
```

Runs tests one-by-one, stops at first polluter. See script for usage.

## Real Example: Empty projectDir

**Symptom:** `.git` created in `packages/core/` (source code)

**Trace chain:**
1. `git init` runs in `process.cwd()` ← empty cwd parameter
2. WorktreeManager called with empty projectDir
3. Session.create() passed empty string
4. Test accessed `context.tempDir` before beforeEach
5. setupCoreTest() returns `{ tempDir: '' }` initially

**Root cause:** Top-level variable initialization accessing empty value

**Fix:** Made tempDir a getter that throws if accessed before beforeEach

**Also added defense-in-depth:**
- Layer 1: Project.create() validates directory
- Layer 2: WorkspaceManager validates not empty
- Layer 3: NODE_ENV guard refuses git init outside tmpdir
- Layer 4: Stack trace logging before git init

## Key Principle

```dot
digraph principle {
    "Found immediate cause" [shape=ellipse];
    "Can trace one level up?" [shape=diamond];
    "More evidence available and authorized?" [shape=diamond];
    "Trace backwards" [shape=box];
    "Gather boundary evidence or authorized instrumentation" [shape=box];
    "BLOCKED - root cause remains unknown" [shape=doublecircle];
    "Is this the source?" [shape=diamond];
    "Fix at source" [shape=box];
    "Distinct evidenced boundary failure mode?" [shape=diamond];
    "Add safeguards at independently reachable boundaries" [shape=box];
    "Verify supported entry and bypass paths" [shape=doublecircle];

    "Found immediate cause" -> "Can trace one level up?";
    "Can trace one level up?" -> "Trace backwards" [label="yes"];
    "Can trace one level up?" -> "More evidence available and authorized?" [label="no"];
    "More evidence available and authorized?" -> "Gather boundary evidence or authorized instrumentation" [label="yes"];
    "More evidence available and authorized?" -> "BLOCKED - root cause remains unknown" [label="no"];
    "Gather boundary evidence or authorized instrumentation" -> "Can trace one level up?";
    "Trace backwards" -> "Is this the source?";
    "Is this the source?" -> "Trace backwards" [label="no - keeps going"];
    "Is this the source?" -> "Fix at source" [label="yes"];
    "Fix at source" -> "Distinct evidenced boundary failure mode?";
    "Distinct evidenced boundary failure mode?" -> "Add safeguards at independently reachable boundaries" [label="yes"];
    "Distinct evidenced boundary failure mode?" -> "Verify supported entry and bypass paths" [label="no"];
    "Add safeguards at independently reachable boundaries" -> "Verify supported entry and bypass paths";
}
```

**NEVER fix just where the error appears.** Trace back to find the original trigger.
If no further evidence is available or authorized, keep the root cause unknown
and report the blocked evidence boundary. Do not manufacture a source or loop on
the same evidence request.

## Stack Trace Tips

**In tests:** Use `console.error()` not logger - logger may be suppressed
**Before operation:** Log before the dangerous operation, not after it fails
**Include context:** Directory, cwd, environment variables, timestamps
**Capture stack:** `new Error().stack` shows complete call chain

## Real-World Impact

From debugging session (2025-10-03):
- Found root cause through 5-level trace
- Fixed at source (getter validation)
- Added 4 layers of defense
- 1847 tests passed, zero pollution
