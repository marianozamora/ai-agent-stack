# Plan de implementación — `ai deploy`: plan + ejecución a dev

Objetivo: llevar `ai deploy` de "detección" a "entender, documentar y ejecutar" un despliegue a
dev, reusando el patrón de `validators.json` (comando declarado + adapter + evidencia) y sin
romper el principio zero-footprint (todo el estado vive fuera del repo objetivo).

## Estado actual (verificado)

- `ai_stack/tools.py:113` `detect_deploy(root)` — heurística offline sobre `git ls-files`;
  devuelve `{docker, ci_cd, paas, infra, scripts, docs}`.
- `ai_stack/tools.py:133` `cmd_deploy(args)` — imprime esos buckets. Sin subcomandos.
- `ai_stack/cli.py:113` registra `deploy` como parser sin argumentos.
- `tests/test_tools.py` cubre `detect_deploy` (6 tests) y `cmd_deploy` (1 test).
- Piezas reusables: `workflow.execute()` (mata el process group en timeout),
  `workflow.validate_config()` (modelo a imitar), `core.require_human()`,
  `core.save_json/load_json`, `metrics.record_metric()`, `validators.run_codex_json()`
  (Codex read-only + output-schema, ya usado por `ai profile --deep` en `repo.py:74`).

## Decisiones de diseño (respetarlas, no reabrirlas)

1. **Un deploy NO es un gate.** No se añade a `core.GATES` ni a `workflow.ORDER` ni entra en
   `ai pipeline`. Los gates certifican *readiness para PR*; desplegar es una acción mutante y
   hacia afuera. Se toca la readiness solo como línea informativa.
2. **Semántica exit-code siempre.** Un target de deploy no tiene adapter `json`: exit 0 = ok.
   `--evidence` es obligatorio (describe qué prueba el éxito), igual que exige
   `validate_config` para el adapter `exit-code`.
3. **Dry-run por defecto.** `ai deploy run dev` imprime y no ejecuta. Solo `--execute` ejecuta.
4. **El modelo nunca configura el target.** `ai deploy plan` *sugiere* un comando; el humano lo
   revisa y lo declara con `ai deploy set`. No auto-escritura de config desde salida del modelo.
5. **`require_human` en la ejecución.** Un modelo corriendo dentro de un gate/validator
   (`AI_GATE`/`AI_TASK_DIR` en el entorno) no puede desplegar. Es exactamente el invariante que
   `core.require_human()` ya implementa.
6. **Estado fuera del repo.** Runbook y registros van a `repo_state(root)`, nunca a `docs/`
   del repo objetivo.
7. **Árbol sucio: avisa, no bloquea.** Desplegar a dev con cambios sin commitear es legítimo;
   se registra `dirty: true` en el record y se imprime aviso.

## Cambios de archivos

### 1. Nuevo `ai_stack/deploy.py`

Mover aquí `detect_deploy` y `cmd_deploy` desde `tools.py` (borrarlos de `tools.py`, junto con
los imports que queden huérfanos: revisar `fnmatch` y `re` — `re` sigue usándose en `tools.py`,
`fnmatch` probablemente no). Estilo del proyecto: compacto, punto y coma, cuerpos de una línea.

Imports: `from core import git_root, load_json, profile_repo, repo_state, require_human, run, safe_head, save_json, task_state`, `from metrics import record_metric`, `from validators import run_codex_json`, `from workflow import execute`. Sin ciclos (`deploy` no lo importa nadie salvo `cli`).

Contenido:

- `TARGET_RE = re.compile(r'[a-z][a-z0-9_-]{0,31}')` — usar `fullmatch`.
- `detect_deploy(root)` — sin cambios funcionales.
- `RUNBOOK_SCHEMA` — objeto con `additionalProperties: False` y `required`:
  `summary` (str), `prerequisites`, `dev_steps`, `verification`, `rollback`,
  `confidence_caveats` (arrays de str), `suggested_dev_command` (array de str, puede ir vacío).
- `check_runbook(value)` — mismo contrato que `validators.check_verdict`:
  `set(value) != set(required)` → `ValueError`; `summary` no vacío; cada lista con strings no
  vacíos. `suggested_dev_command` vacío es válido (= no hay ruta de deploy legible).
- `validate_deploy_config(config)` — espejo de `workflow.validate_config`:
  `version == 1`; `targets` dict; nombre que pase `TARGET_RE`; `command` lista no vacía de str
  no vacíos; `timeout` int > 0; `evidence` str no vacío. Devuelve el config.
- `deploy_config(state)` — lee `state/'deploy.json'` (default `{'version':1,'targets':{}}`),
  valida, convierte `OSError/ValueError` en `SystemExit('Invalid deploy configuration: ...')`.
  Espejo exacto de `gates.validator_config`.
- `render_runbook(runbook) -> str` — markdown con secciones Prerequisites / Deploy to dev /
  Verification / Rollback / Caveats, el comando sugerido en bloque ```text``` y debajo la línea
  literal `ai deploy set dev --evidence "..." -- <cmd>` para copiar. Cierra con el caveat de
  "AI-generated, verify before relying on it".
- `cmd_deploy(args)` — dispatcher sobre `getattr(args,'deploy_cmd',None)`:
  `None`/`'detect'` → detección (comportamiento actual, back-compat), y el resto delega en
  helpers `_plan`, `_show`, `_set`, `_remove`, `_run`.

Detalle de cada subcomando:

**`plan`** (`--refresh`, `--timeout` default 600)
- `profile_repo` si falta `project-profile.json`; carga perfil + `detect_deploy`.
- Cachea por commit: si `state/'deploy-runbook.json'` existe con `analyzed_commit == safe_head(root)`
  y no hay `--refresh`, imprime y sale (igual que `cmd_profile --deep`, `repo.py:49`).
- Requiere Codex (`shutil.which('codex')`), si no: `SystemExit('Codex CLI missing. ...')`.
- Prompt read-only, citando rutas, con estas restricciones explícitas: no modificar nada, no
  inventar comandos ni credenciales que no estén en el repo, centrarse en el entorno **dev**,
  y si no hay ruta de deploy a dev → `suggested_dev_command: []` y explicarlo en
  `confidence_caveats`. Inyectar `json.dumps(detect_deploy(root))` y el perfil como hechos ya
  detectados, más el `deployment` del deep profile si `project-deep-profile.json` existe.
- `run_codex_json(executable, root, state/'review', 'deploy-runbook', prompt, RUNBOOK_SCHEMA, check_runbook, timeout=args.timeout)`.
- Guarda `deploy-runbook.json` (+ `version`, `generated_at`, `analyzed_commit`, `caveat`) y
  `deploy-runbook.md` vía `render_runbook`. Imprime ambas rutas.

**`show`** — imprime targets configurados, el último record por target
(`state/'deploy'/<target>.json`: éxito, exit code, commit, fecha, log) y la ruta + `summary`
del runbook si existe.

**`set <target> --evidence "..." [--timeout 1800] [--description ...] -- <cmd>`**
- Strip del `--` inicial como hace `gates.cmd_validators`.
- Escribe en config, valida, `save_json`, imprime el JSON y `Saved: <path>`.

**`remove <target>`** — `pop` + save.

**`run [target=dev] [--execute] `**
- Target inexistente → `SystemExit` con el `ai deploy set <target> -- ...` sugerido.
- Imprime siempre: target, comando, cwd, timeout, commit, evidencia esperada, si el árbol está
  sucio, y la readiness actual (`task_state(state)/'state/readiness.json'` → `status`, o
  `unknown`) como **línea informativa, no bloqueante**.
- Sin `--execute`: `Dry run: nothing executed. Re-run with --execute to deploy.` y `return`.
- Con `--execute`: `require_human(f'Deploying to {target}')` → luego ejecuta.
- Log a `state/'deploy'/f'{target}-{timestamp}-{uuid4().hex[:8]}.log'` (crear el dir).
- Env del proceso: `dict(os.environ, AI_DEPLOY_TARGET=..., AI_DEPLOY_COMMIT=..., AI_REPO_STATE=...)`.
  **Nunca** setear `AI_GATE` ni `AI_TASK_DIR` aquí.
- `code = execute(item['command'], root, env, out, item['timeout'])`.
- `save_json(state/'deploy'/f'{target}.json', {...})` con: `version`, `target`, `command`,
  `succeeded`, `exit_code`, `evidence` (solo si `code == 0`, si no `None`), `commit`, `dirty`,
  `readiness`, `duration_seconds`, `log`, `log_hash` (sha256 del log), `created_at`.
- `record_metric(state,'deploy',target=..., succeeded=..., exit_code=..., duration_seconds=..., dirty=...)`.
  El índice sqlite de `metrics.py` es genérico, un evento `deploy` no necesita cambios ahí.
- Imprime `f"{target}: {'DEPLOYED' if code==0 else 'FAILED'} (exit {code}, {duration}s) | {log}"`;
  si falla, `raise SystemExit(1)`.

### 2. `ai_stack/cli.py`

- Import: quitar `cmd_deploy` de la línea `from tools import ...`, añadir `from deploy import cmd_deploy`.
- `COMMAND_DESCRIPTIONS['deploy']` → `'Detect, document and run declared deployments.'`
- Sacar `sp.add_parser('deploy')...` de la línea compartida de `cli.py:113` y montar:

```python
dep=sp.add_parser('deploy'); dps=dep.add_subparsers(dest='deploy_cmd'); dep.set_defaults(func=cmd_deploy)
dps.add_parser('detect'); dps.add_parser('show')
dplan=dps.add_parser('plan'); dplan.add_argument('--refresh',action='store_true'); dplan.add_argument('--timeout',type=int,default=600)
dset=dps.add_parser('set'); dset.add_argument('target'); dset.add_argument('--evidence',required=True); dset.add_argument('--timeout',type=int,default=1800); dset.add_argument('--description',default=''); dset.add_argument('command',nargs=argparse.REMAINDER)
drm=dps.add_parser('remove'); drm.add_argument('target')
drun=dps.add_parser('run'); drun.add_argument('target',nargs='?',default='dev'); drun.add_argument('--execute',action='store_true')
```

`ai deploy` sin subcomando debe seguir funcionando (`deploy_cmd=None` → detección); no poner
`required=True` en el subparser.

### 3. `docs/commands.md` — REGENERAR

CI corre `python3 scripts/generate_command_reference.py --check` y **falla si está desfasado**.
Ejecutar `python3 scripts/generate_command_reference.py` y commitear el resultado.

### 4. Tests

- Nuevo `tests/test_deploy.py`. Mover ahí `DetectDeployTests` y `CmdDeployDispatcherTests` desde
  `tests/test_tools.py` (cambiando `tools.` → `deploy.`), reusando el helper `_GitSandbox`
  (copiarlo; `test_tools.py` lo mantiene para sus otras clases).
- Tests nuevos mínimos:
  - `validate_deploy_config`: rechaza version != 1, target con nombre inválido, command vacío,
    command con no-strings, timeout 0/negativo/no-int, evidence vacía o ausente.
  - `deploy_config`: archivo ausente → default; archivo corrupto → `SystemExit`.
  - `set` guarda y `remove` borra; `set` con `--` inicial lo descarta.
  - `run` sin `--execute` no ejecuta nada (comando que crearía un fichero: verificar que no
    existe) y sale con 0.
  - `run --execute` con `true`/`sh -c 'exit 0'`: record `succeeded=True`, log escrito,
    `log_hash` correcto, fila `deploy` en `metrics.jsonl`.
  - `run --execute` con comando que falla: `SystemExit(1)`, `succeeded=False`, `evidence=None`.
  - `run --execute` con `AI_GATE` en el entorno → `SystemExit` de `require_human`.
  - `run` con target no configurado → `SystemExit` mencionando `ai deploy set`.
  - `check_runbook`: acepta un runbook válido con `suggested_dev_command: []`; rechaza summary
    vacío, lista con string vacío, campo extra o faltante.
  - `render_runbook`: contiene la línea `ai deploy set dev` cuando hay comando sugerido, y el
    mensaje de "no dev deploy command" cuando la lista está vacía.
  - **No** testear `plan` end-to-end (requiere Codex): mockear `deploy.run_codex_json` para
    verificar cacheo por commit y `--refresh`.

### 5. Documentación y changelog

- `README.md`: sección corta "Deployment" después del Quickstart, con el ciclo
  `ai deploy` → `ai deploy plan` → revisar runbook → `ai deploy set dev ...` → `ai deploy run dev`
  → `ai deploy run dev --execute`. Dejar claro que el runbook vive fuera del repo y que el
  comando sugerido por el modelo se revisa antes de declararlo.
- `CHANGELOG.md`: entrada bajo la versión en curso.
- No tocar `VERSION`/`pyproject.toml` salvo que se decida cortar release.

## Orden de trabajo sugerido

1. `deploy.py` con `detect_deploy` movido + config (`validate_deploy_config`, `deploy_config`)
   + `set`/`remove`/`show`. Wire en `cli.py`. Tests de config.
2. `run` con dry-run, `--execute`, record, metric, `require_human`. Tests de ejecución.
3. `plan` (schema, checker, prompt, cacheo, render markdown). Tests con `run_codex_json` mockeado.
4. Regenerar `docs/commands.md`, README, CHANGELOG.
5. `python3 -m pytest -q --ignore=tests/test_workflow.py && ruff check . && mypy ai_stack &&
   python3 scripts/generate_command_reference.py --check`.

## Riesgos y trampas

- **`docs/commands.md` desfasado rompe CI** — es el fallo más probable de esta tarea.
- `mypy ai_stack` corre en CI: `deploy.py` debe tipar lo suficiente para pasar (el resto del
  proyecto usa anotaciones ligeras; imitar `tools.py`).
- `ruff` con `B` activo: cuidado con `raise ... from` (B904) en los `except` que convierten a
  `SystemExit`.
- Mover `detect_deploy` rompe `tests/test_tools.py` si no se actualizan los imports —
  hacerlo en el mismo commit.
- `record_metric` llama a `task_state`, que exige un task id válido (rama actual). En un repo
  sin commits la rama existe igual; los tests deben commitear al menos una vez.
- El comando sugerido en el markdown se renderiza con `' '.join(command)` sin quoting: es texto
  informativo para revisar, no algo que se ejecute. No prometer que es copy-paste seguro con
  argumentos que lleven espacios.
