# Command reference

Generated from the CLI parser. Run `python3 scripts/generate_command_reference.py` after changing commands or flags.

## `ai init`

Initialize external state and detect repository tooling.

```text
usage: ai init [-h] [--base BASE] [--task-id TASK_ID]
```

## `ai run`

Build an orchestration prompt and launch Claude.

```text
usage: ai run [-h] [--profile {fast,standard,strict}] [--base BASE]
              [--figma FIGMA] [--no-figma] [--skill SKILL]
              [--ticket-file TICKET_FILE] [--task-id TASK_ID]
              [task]
```

## `ai plan`

Build an orchestration prompt without launching a model.

```text
usage: ai plan [-h] [--profile {fast,standard,strict}] [--base BASE]
               [--figma FIGMA] [--no-figma] [--skill SKILL]
               [--ticket-file TICKET_FILE] [--task-id TASK_ID]
               [task]
```

## `ai start`

Start a task with its own fresh contract and make it active.

```text
usage: ai start [-h] [--title TITLE] [--ticket-file TICKET_FILE] [--base BASE]
                [--resume] [--switch] [--task-id TASK_ID]
                id
```

## `ai switch`

Make an existing open task the active one.

```text
usage: ai switch [-h] [--task-id TASK_ID] id
```

## `ai close`

Close the active task and freeze its evidence.

```text
usage: ai close [-h] [--reason REASON] [--task-id TASK_ID] [id]
```

## `ai current`

Show the active task and its state.

```text
usage: ai current [-h] [--json] [--task-id TASK_ID]
```

## `ai tasks`

List tasks recorded for this checkout.

```text
usage: ai tasks [-h] [--status {active,paused,closed}] [--json]
                [--task-id TASK_ID]
```

## `ai ticket`

Analyze pasted ticket content.

```text
usage: ai ticket [-h] [--task-id TASK_ID] {check} ...
```

### `ai ticket check`



```text
usage: ai ticket check [-h] [--file FILE] [--text TEXT] [--json]
```

## `ai review`

Prepare or launch a bounded Codex review.

```text
usage: ai review [-h] [--profile {fast,standard,strict}] [--base BASE]
                 [--refresh] [--build] [--no-launch] [--task-id TASK_ID]
```

## `ai impact`

Report deterministic diff impact.

```text
usage: ai impact [-h] [--profile {fast,standard,strict}] [--base BASE]
                 [--refresh] [--build] [--task-id TASK_ID]
```

## `ai ready`

Certify readiness from fresh gate evidence.

```text
usage: ai ready [-h] [--task-id TASK_ID]
```

## `ai gate`

Run and record one evidence gate.

```text
usage: ai gate [-h] [--timeout TIMEOUT] [--task-id TASK_ID]
               {checks,regression,cleanup,provenance,ponytail,summary,contract,review,security,design}
               ...
```

## `ai pipeline`

Run all required configured gates in order.

```text
usage: ai pipeline [-h] [--dry-run] [--resume] [--task-id TASK_ID]
```

## `ai validators`

Inspect or configure reusable validators.

```text
usage: ai validators [-h] [--task-id TASK_ID] {show,install,remove,set} ...
```

### `ai validators show`



```text
usage: ai validators show [-h]
```

### `ai validators install`



```text
usage: ai validators install [-h]
```

### `ai validators remove`



```text
usage: ai validators remove [-h]
                            {checks,regression,cleanup,provenance,ponytail,summary,contract,review,security,design}
```

### `ai validators set`



```text
usage: ai validators set [-h] [--adapter {json,exit-code}]
                         [--evidence EVIDENCE] [--timeout TIMEOUT]
                         {checks,regression,cleanup,provenance,ponytail,summary,contract,review,security,design}
                         ...
```

## `ai validate`

Run a bundled semantic validator.

```text
usage: ai validate [-h] [--task-id TASK_ID]
                   {cleanup,contract,review,security,ponytail,design,summary,provenance}
```

## `ai metrics`

Report or prune task metrics.

```text
usage: ai metrics [-h] [--all-tasks] [--json]
                  [--by {day,week,gate,profile,task_type,task}]
                  [--since SINCE] [--top TOP] [--format {text,json,csv}]
                  [--budget BUDGET] [--task-id TASK_ID]
                  {prune} ...
```

### `ai metrics prune`



```text
usage: ai metrics prune [-h] --older-than OLDER_THAN [--confirm]
```

## `ai benchmark`

Run routing or pipeline benchmarks.

```text
usage: ai benchmark [-h] [--json] [--task-id TASK_ID]
                    {run,list,report,compare} ...
```

### `ai benchmark run`



```text
usage: ai benchmark run [-h] [--profile {fast,standard,strict}]
                        [--scenario SCENARIO] [--corpus CORPUS] [--json]
```

### `ai benchmark list`



```text
usage: ai benchmark list [-h] [--corpus CORPUS] [--json]
```

### `ai benchmark report`



```text
usage: ai benchmark report [-h] [--json] [run_id]
```

### `ai benchmark compare`



```text
usage: ai benchmark compare [-h] run_a run_b
```

## `ai profile`

Inspect the repository profile.

```text
usage: ai profile [-h] [--deep] [--refresh] [--timeout TIMEOUT]
                  [--task-id TASK_ID]
```

## `ai confidence`

Report historical outcome evidence.

```text
usage: ai confidence [-h] [--profile {fast,standard,strict}] [--base BASE]
                     [--json] [--task-id TASK_ID]
```

## `ai prompt`

Manage measured validator-prompt experiments.

```text
usage: ai prompt [-h] [--task-id TASK_ID]
                 {list,show,experiment,report,promote,reset,rollback,history}
                 ...
```

### `ai prompt list`



```text
usage: ai prompt list [-h]
```

### `ai prompt show`



```text
usage: ai prompt show [-h]
                      {cleanup,contract,review,security,ponytail,design,summary,provenance}
                      [variant]
```

### `ai prompt experiment`



```text
usage: ai prompt experiment [-h] {start,status,stop} ...
```

#### `ai prompt experiment start`



```text
usage: ai prompt experiment start [-h] --variants VARIANTS
                                  [--min-samples MIN_SAMPLES]
                                  {cleanup,contract,review,security,ponytail,design,summary,provenance}
```

#### `ai prompt experiment status`



```text
usage: ai prompt experiment status [-h]
```

#### `ai prompt experiment stop`



```text
usage: ai prompt experiment stop [-h]
```

### `ai prompt report`



```text
usage: ai prompt report [-h] [--json]
```

### `ai prompt promote`



```text
usage: ai prompt promote [-h] [--confirm]
                         {cleanup,contract,review,security,ponytail,design,summary,provenance}
                         variant
```

### `ai prompt reset`



```text
usage: ai prompt reset [-h]
                       {cleanup,contract,review,security,ponytail,design,summary,provenance}
```

### `ai prompt rollback`



```text
usage: ai prompt rollback [-h] [--confirm]
                          {cleanup,contract,review,security,ponytail,design,summary,provenance}
```

### `ai prompt history`



```text
usage: ai prompt history [-h]
                         [--slot {cleanup,contract,review,security,ponytail,design,summary,provenance}]
                         [--json]
```

## `ai failures`

Inspect recurring failure patterns.

```text
usage: ai failures [-h] [--gate GATE] [--min MIN] [--json] [--task-id TASK_ID]
                   {show,rebuild,export} ...
```

### `ai failures show`



```text
usage: ai failures show [-h] pattern_id
```

### `ai failures rebuild`



```text
usage: ai failures rebuild [-h]
```

### `ai failures export`



```text
usage: ai failures export [-h]
```

## `ai lessons`

Curate repository lessons.

```text
usage: ai lessons [-h] [--status {candidate,confirmed,retired,rejected}]
                  [--json] [--task-id TASK_ID]
                  {derive,add,confirm,reject,retire,promote,prune} ...
```

### `ai lessons derive`



```text
usage: ai lessons derive [-h]
```

### `ai lessons add`



```text
usage: ai lessons add [-h] [--scope SCOPE] [--gate GATE] text
```

### `ai lessons confirm`



```text
usage: ai lessons confirm [-h] lesson_id
```

### `ai lessons reject`



```text
usage: ai lessons reject [-h] lesson_id
```

### `ai lessons retire`



```text
usage: ai lessons retire [-h] lesson_id
```

### `ai lessons promote`



```text
usage: ai lessons promote [-h] lesson_id
```

### `ai lessons prune`



```text
usage: ai lessons prune [-h] [--unseen-days UNSEEN_DAYS]
```

## `ai status`

Show repository and task state.

```text
usage: ai status [-h] [--task-id TASK_ID]
```

## `ai doctor`

Check dependencies and external-state health.

```text
usage: ai doctor [-h] [--task-id TASK_ID]
```

## `ai path`

Print the active external task directory.

```text
usage: ai path [-h] [--task-id TASK_ID]
```

## `ai optimize`

Audit bundled prompt/context size.

```text
usage: ai optimize [-h] [--task-id TASK_ID]
```

## `ai deploy`

Detect deployment configuration.

```text
usage: ai deploy [-h] [--task-id TASK_ID]
```

## `ai skill`

Inspect, select, or create skills.

```text
usage: ai skill [-h] [--task-id TASK_ID]
                {list,explain,enable,disable,dry-run,create} ...
```

### `ai skill list`



```text
usage: ai skill list [-h] [--task TASK] [--profile {fast,standard,strict}]
```

### `ai skill explain`



```text
usage: ai skill explain [-h] name
```

### `ai skill enable`



```text
usage: ai skill enable [-h] name
```

### `ai skill disable`



```text
usage: ai skill disable [-h] name
```

### `ai skill dry-run`



```text
usage: ai skill dry-run [-h] [--profile {fast,standard,strict}] name
```

### `ai skill create`



```text
usage: ai skill create [-h] --category CATEGORY
                       [--cost {tiny,low,medium,high}] [--priority PRIORITY]
                       [--task-types TASK_TYPES] [--triggers TRIGGERS]
                       [--stages STAGES] --prompt PROMPT
                       [--description DESCRIPTION] [--always-consider]
                       name
```

## `ai handoff`

Record a compact continuation point.

```text
usage: ai handoff [-h] [--profile {fast,standard,strict}] [--base BASE]
                  [--state STATE] [--evidence EVIDENCE] [--next NEXT]
                  [--task-id TASK_ID]
                  [task]
```

## `ai rules`

Manage repository-specific rules.

```text
usage: ai rules [-h] [--task-id TASK_ID] {list,add,remove} ...
```

### `ai rules list`



```text
usage: ai rules list [-h]
```

### `ai rules add`



```text
usage: ai rules add [-h] [--scope SCOPE] rule
```

### `ai rules remove`



```text
usage: ai rules remove [-h] index
```

## `ai docs`

Use cached, version-specific library documentation.

```text
usage: ai docs [-h] [--task-id TASK_ID]
               {doctor,setup,detect,library,query} ...
```

### `ai docs doctor`



```text
usage: ai docs doctor [-h]
```

### `ai docs setup`



```text
usage: ai docs setup [-h] [--mcp] [--universal]
```

### `ai docs detect`



```text
usage: ai docs detect [-h]
```

### `ai docs library`



```text
usage: ai docs library [-h] name [query]
```

### `ai docs query`



```text
usage: ai docs query [-h] [--refresh] library query
```

## `ai figma`

Check or configure Figma connectivity.

```text
usage: ai figma [-h] [--task-id TASK_ID] {doctor,setup} ...
```

### `ai figma doctor`



```text
usage: ai figma doctor [-h]
```

### `ai figma setup`



```text
usage: ai figma setup [-h] [--claude-only] [--codex-only]
```

## `ai crg`

Manage Code Review Graph data.

```text
usage: ai crg [-h] [--task-id TASK_ID] {doctor,build,status,update,detect} ...
```

### `ai crg doctor`



```text
usage: ai crg doctor [-h]
```

### `ai crg build`



```text
usage: ai crg build [-h]
```

### `ai crg status`



```text
usage: ai crg status [-h]
```

### `ai crg update`



```text
usage: ai crg update [-h] [--base BASE] [--brief]
```

### `ai crg detect`



```text
usage: ai crg detect [-h] [--base BASE] [--brief]
```

## `ai graph`

Build or query the architecture graph.

```text
usage: ai graph [-h] [--task-id TASK_ID]
                {doctor,build,sync,query,path,explain} ...
```

### `ai graph doctor`



```text
usage: ai graph doctor [-h]
```

### `ai graph build`



```text
usage: ai graph build [-h]
```

### `ai graph sync`



```text
usage: ai graph sync [-h]
```

### `ai graph query`



```text
usage: ai graph query [-h] query
```

### `ai graph path`



```text
usage: ai graph path [-h] start end
```

### `ai graph explain`



```text
usage: ai graph explain [-h] node
```
