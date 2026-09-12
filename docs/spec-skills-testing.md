# Spec: Cobertura de tests y fixes del modo Skills

Estado: listo para implementar · Autor: diseño 2026-09-12 · Implementa: otro agente

## Objective

Garantizar con tests automatizados que el motor de skills (`ai_stack/skills.py`) y sus consumidores (`core.classify_task`, `lifecycle.build_prompt`, `ai plan`, `ai skill *`, instalación) se comportan como está documentado, y corregir 5 defectos confirmados.

## Context

- Motor: `ai_stack/skills.py` (281 líneas). Clasificador y `fold`: `ai_stack/core.py:583-603`. Límites de perfil: `core.context_caps` (`skills`: fast=1, standard=2, strict=3; `context_chars`: 12000/24000/36000). Consumidor: `ai_stack/lifecycle.py:91-172`. Instalación: `ai_stack/install.py`.
- Tests existentes que hay que mantener en verde: `tests/test_skills_{routing,scope,create,upstream}.py`, `tests/test_performance_budgets.py`, `tests/test_human_only_guard.py`, `tests/test_capabilities.py`, `tests/smoke.sh`. Línea base: 112 passed, 53 subtests.
- Se testea con `unittest`/`pytest`. Hay que seguir el estilo de los tests vecinos: `tempfile` para el state y los `sys.path` imports que ya usan los tests de skills.
- Cada entrada del CHANGELOG es un párrafo largo que explica el porqué. Hay que agregar una así en la sección no publicada.

## Constraints

- Ningún test escribe en el árbol fuente ni en el checkout. Hay que usar state temporal, `HOME` aislado y parchear `skills.skill_root` cuando haga falta.
- Sin red. `git ls-remote` siempre mockeado.
- No hay que cambiar el routing de las tareas del corpus golden (§Task 2), salvo lo que exija un fix de esta spec.
- `registry.json` sigue siendo la fuente de verdad. Si se toca un `skill.json`, hay que sincronizarlo con el registry.
- Los comandos que mutan (`enable`/`disable`/`create`) siguen protegidos por `require_human`.

## Decisions (no reabrir)

- D1: `"fix typo"` → `bug` → `diagnosing-bugs`. Se conserva el comportamiento actual y se congela en el golden.
- D2: el tipo `design` solo se activa con Figma (`figma` param o `figma.com/` en el texto). Se conserva.
- D3: `--skill X` explícito se salta `requires_trigger` a propósito. Se testea y se documenta en el docstring de `select_skills`.

---

## Task 1 — Tests en rojo y fixes de defectos confirmados

Por cada defecto hay que escribir primero el test (debe fallar sobre `main`) y después el fix.

### B1 — override no booleano se interpreta como activado
- Dónde: `skills.enabled_skills` (`bool(overrides.get(...))`).
- Repro: `skill-overrides.json` = `{"tdd":"false"}` → `enabled_skills(state)['tdd']['enabled'] is True`.
- Fix esperado: solo un `bool` real cuenta como override. Si el valor no es `bool`, hay que ignorarlo, usar el `enabled` del registry y avisar una sola vez por stderr, nombrando la skill y el archivo.
- Tests: `"false"`, `0`, `null`, `"true"` → se aplica el default del registry y se emite el aviso; `false`/`true` reales → se respetan.

### B2 — `registry.json` del repo corrupto se ignora en silencio
- Dónde: `skills.skill_registry` usa `core.load_json`, que devuelve el default ante cualquier excepción.
- Repro: `<state>/skills/registry.json` = `{not json` → las skills del repo desaparecen sin aviso.
- Fix esperado: si el archivo existe pero no es JSON válido, o si `skills` no es un dict, hay que avisar por stderr (`Invalid repo skill registry: <path>`) y continuar solo con las skills bundled. `ai doctor` lo reporta (hay que reutilizar `core.json_file_health`). Un registry bundled corrupto sigue fallando cerrado.
- Tests: JSON inválido, `{"skills": []}`, `[]` → aviso, bundled intactas, sin excepción. En `ai doctor` aparece la línea.

### B3 — nombres de skill del registry del repo sin validar (path traversal)
- Dónde: `skill_registry` (merge del repo) y `resolve_skill_file` (`root/name/filename`).
- Repro: una entrada `"../../evil"` en el registry del repo → `select_skills(state,'add feature','standard')` devuelve `['tdd','../../evil']`.
- Fix esperado: hay que extraer la regex de `cmd_skill_create` (`[a-z][a-z0-9-]*`) a una constante `NAME_RE`. `skill_registry` descarta con aviso las entradas cuyo nombre no cumpla. `resolve_skill_file` devuelve `None` si el path resuelto no queda dentro de `root`.
- Tests: `"../../evil"`, `"Evil"`, `"a/b"` → no se listan, no se seleccionan, se emite el aviso. `resolve_skill_file(state,'../x','prompt.md')` es `None` aunque el archivo exista.

### B4 — `ai skill create` en scope stack se pierde con el upgrade
- Dónde: `cmd_skill_create` escribe en `skill_root()` = `STACK_ROOT/skills`, que en una instalación es el directorio del release. `install.py` crea un release nuevo copiando `skills/` desde el código fuente.
- Fix esperado: si `STACK_ROOT` está bajo un directorio `ai-agent-stack-releases`, `create` sin `--repo` debe fallar con exit≠0 y este mensaje: `Stack-scoped skills cannot be created in an installed release (they would be lost on upgrade). Use --repo, or create it in the source checkout.` Desde un checkout fuente sigue funcionando como hoy.
- Tests: unit con `STACK_ROOT` parcheado a `.../ai-agent-stack-releases/release-x` → `SystemExit` y nada escrito. Otro unit con un `STACK_ROOT` normal → se crea la skill. En el E2E (Task 5), comprobar que falla en la instalación.

### B5 — `--skill` explícito recortado por el límite sin avisar
- Dónde: `select_skills`, `[...][:caps['skills']]`.
- Repro: `select_skills(state,'x','fast',explicit=['research','tdd'])` → `['research']`, y `tdd` se descarta sin mensaje.
- Fix esperado: hay que deduplicar el explícito preservando el orden. Si supera el límite, avisar por stderr: `Profile <p> allows <n> skill(s); dropping: <names>`.
- Tests: aviso emitido con los nombres correctos; `['tdd','tdd']` → `['tdd']` sin aviso de límite.

**Aceptación Task 1:** los 5 tests nuevos fallan sobre `ea77955` y pasan después del fix, y la suite existente sigue en verde.

---

## Task 2 — Corpus golden de routing

- Crear `tests/fixtures/skill_routing_golden.json`: una lista de `{ "task": str, "figma": str|null, "type": str, "fast": [..], "standard": [..], "strict": [..] }`.
- Crear `tests/test_skills_golden.py`: un subtest por fila que compara `classify_task` y `select_skills(state_vacío, task, profile, figma)` para los 3 perfiles. Si falla, el mensaje debe mostrar el `score_skills` de esa tarea.
- Mínimo 40 filas: ≥20 en español, ≥1 fila positiva por cada skill `requires_trigger`, ≥1 negativa por cada una (tarea parecida sin el trigger) y ≥1 con `figma`.
- Filas obligatorias (salida actual verificada):

| task | type | fast | standard | strict |
|------|------|------|----------|--------|
| latest dashboard slow | feature | tdd | tdd | tdd |
| arregla el login que falla | bug | diagnosing-bugs | diagnosing-bugs, tdd | diagnosing-bugs, tdd |
| Añadir métricas y SLOs al checkout | feature | observability-and-instrumentation | observability-and-instrumentation, tdd | observability-and-instrumentation, tdd |
| migrar de REST a GraphQL con deprecación | architecture | wayfinder | wayfinder, deprecation-and-migration | wayfinder, deprecation-and-migration, codebase-design |
| break down the epic into tickets | planning | to-tickets | to-tickets | to-tickets |
| prototype: can we stream PDFs? | prototype | prototype | prototype | prototype |
| resolve merge conflict in main | feature | resolving-merge-conflicts | resolving-merge-conflicts, tdd | resolving-merge-conflicts, tdd |
| investiga la documentación oficial de Next 15 | feature | source-driven-development | source-driven-development, research | source-driven-development, research, tdd |
| add git guardrails hook for claude code | feature | git-guardrails-claude-code | git-guardrails-claude-code, tdd | git-guardrails-claude-code, tdd |
| build a wizard for onboarding | feature | wizard | wizard, tdd | wizard, tdd |
| grill me on the design | feature | grilling | grilling, tdd | grilling, tdd |
| fix typo | bug | diagnosing-bugs | diagnosing-bugs, tdd | diagnosing-bugs, tdd |
| login regression returns 403 | bug | diagnosing-bugs | diagnosing-bugs, tdd | diagnosing-bugs, tdd |
| (cadena vacía) | feature | tdd | tdd | tdd |

- Para las filas adicionales, el valor esperado es la salida actual del motor **después** de la Task 1. Si alguna parece incorrecta, no hay que cambiar el routing: se registra en la sección "Routing observations" del PR.

**Aceptación Task 2:** el fixture tiene ≥40 filas que cumplen las cuotas y el test pasa. Si se cambia el `priority` de cualquier skill del registry, al menos una fila falla (hay que comprobarlo a mano y dejarlo anotado en el PR).

---

## Task 3 — Invariantes del registry y tests unitarios

Archivo nuevo `tests/test_skills_invariants.py`, más ampliaciones en `tests/test_skills_routing.py`.

Invariantes (sobre el registry bundled):
1. Cada skill tiene `prompt.md` y `README.md`, y cada carpeta de `skills/` (excepto archivos sueltos) está en el registry.
2. `task_types` ⊆ `TASK_TYPES`; `primary_for` ⊆ `task_types`; `cost` ∈ `COSTS`; `priority` es `int`; `stages` es una lista no vacía.
3. Toda skill con `requires_trigger: true` tiene ≥1 trigger.
4. Todo nombre cumple `NAME_RE`.
5. Toda skill con `provenance` figura en `upstreams.json` (el inverso de lo que ya valida `upstream_status`).
6. Con el presupuesto de strict (`max(2000, 36000//3)`), las 3 skills de mayor tamaño juntas caben sin truncar.

Unit:
- `trigger_hits`: triggers con caracteres de regex (`c++`, `node.js`) → hit literal. Mayúsculas y puntuación pegada (`"Tests,"`, `(SLO)`) → hit. Emoji, `ß` y NFD vs NFC → sin excepción y resultado igual en NFC y NFD.
- `classify_task`: `None`, `""` y `"   "` → `feature`. Precedencia `figma > bug > architecture > prototype > planning > feature` (ej. `"fix the migration"` → bug, `"refactor prototype"` → architecture). Tabla parametrizada con **cada** keyword en español de las regex de `core.py:590-599`.
- `score_skills`/`select_skills`: nunca se excede el límite en ningún perfil para todas las filas del golden. Determinismo: 50 llamadas dan el mismo resultado. Desempate score → priority → name con skills sintéticas. `always_consider` suma solo en feature/bug/design. Un explícito desconocido lanza `SystemExit`. D3 (explícito sin trigger es aceptado). Si todas están deshabilitadas → `[]`. `figma` fuerza `design`.
- `load_skill_context`: con varias skills, la segunda recibe solo lo que queda y con el presupuesto agotado no se agrega un header vacío. Una skill seleccionada sin `prompt.md` se omite sin excepción. Una skill del repo que hace shadow sin `prompt.md` cae al bundled (congelar este comportamiento con un test y un comentario).
- Override para una skill inexistente → ignorado sin excepción.

**Aceptación Task 3:** todos los tests pasan y la cobertura de líneas de `ai_stack/skills.py` es ≥95% (`coverage report --include='ai_stack/skills.py'`).

---

## Task 4 — CLI e integración

Tests en `tests/test_skills_cli.py`. Hay que invocar `cli.main([...])` o subprocess, según el patrón de `tests/test_cli.py`, dentro de un repo git temporal con `HOME` aislado.

CLI:
- `ai skill` sin subcomando produce la misma salida que `ai skill list`.
- `list --task T --profile P`: la línea `Recommended:` coincide con `select_skills`, `Task type:` con `classify_task`, y las filas `*` son exactamente las seleccionadas. Probar 1 tarea EN y 1 ES.
- `explain tdd`: JSON sin `enabled` + README. `explain nope` → exit≠0 con `Unknown skill: nope`.
- `disable tdd` → `list --task "add feature"` no la recomienda → `enable tdd` → vuelve.
- Fuera de un repo git, cada subcomando falla con un mensaje claro y sin traceback, o funciona con las skills bundled (documentar cuál aplica).
- Tras cada comando no mutante: `git status --porcelain` vacío.
- Guard: `explain`, `dry-run` y `upstream` están permitidos dentro de un gate (hay que extender la tabla de `tests/test_human_only_guard.py`).

Integración:
- `build_prompt` contiene `Active skills (lazy-loaded; max N): <sel>` y un `## Skill: <name>` por cada skill con prompt.
- `--skill` repetido llega a `build_prompt` y queda en `current-plan.json["skills"]`.
- La métrica `plan` en `metrics.jsonl` registra las mismas `skills`.
- `task_cache_key` cambia tras `disable` de una skill seleccionada.
- `evidence_fingerprint()` cambia tras `disable`.
- Con `--profile` omitido y un scope pequeño de bajo riesgo, `resolve_profile` → `fast` → exactamente 1 skill.
- `ai benchmark` y `ai plan` eligen las mismas skills para la misma tarea y el mismo perfil.
- `upstream --check` con `git ls-remote` fallando → exit≠0 y un mensaje con el nombre del source, sin traceback.

**Aceptación Task 4:** todos los tests pasan y ninguno escribe fuera de directorios temporales.

---

## Task 5 — E2E de instalación

Crear `tests/skills_e2e.sh` (con el mismo esqueleto que `tests/smoke.sh`: `set -euo pipefail`, repo y `HOME` temporales, `install.sh`):

1. `ai init`, `ai start e2e-1 --base HEAD`.
2. `ai skill list --task` para: `login regression returns 403` (bug EN), `arregla el login que falla` (bug ES), `migrar de REST a GraphQL con deprecación` (--profile strict), `resolve merge conflict in main`. `grep` del `Recommended:` esperado según el golden.
3. `ai skill disable tdd` → `ai plan 'add feature' --base HEAD` no incluye `tdd` → `ai skill enable tdd`.
4. `ai skill create e2e-local --repo --category test --triggers zanahoria --task-types feature --prompt 'marker E2E'` → `ai plan 'zanahoria feature' --base HEAD` incluye `e2e-local` → `ai skill list` muestra `[repo]`.
5. `ai skill create e2e-global --category test --prompt p` → exit≠0 (B4).
6. Re-ejecutar `install.sh` → `ai skill list` sigue mostrando `e2e-local`.
7. `ai skill upstream --json | python3 -m json.tool`.
8. Al final: `git status --porcelain` vacío; imprimir `skills-e2e: PASS`.

**Aceptación Task 5:** `bash tests/skills_e2e.sh` termina con exit 0 e imprime `skills-e2e: PASS`.

---

## Task 6 — Documentación

- `CHANGELOG.md`, sección no publicada: un párrafo para los fixes B1–B5 y otro para el corpus golden, con el estilo existente (qué fallaba, cómo se midió y qué cambia).
- `docs/commands.md`: mencionar los nuevos avisos de `--skill` y el rechazo de `create` en un release instalado.

---

## Definition of Done

```sh
python3 -m pytest -q -n auto
bash tests/smoke.sh
bash tests/skills_e2e.sh
python3 tests/validate.py
coverage run -m pytest tests/test_skills_*.py && coverage report --include='ai_stack/skills.py' --fail-under=95
git status --porcelain   # vacío salvo los cambios de esta spec
```

- [ ] B1–B5: test en rojo antes del fix (anotar el commit o la salida en el PR) y en verde después.
- [ ] Golden ≥40 filas con las cuotas de Task 2.
- [ ] Invariantes y unit de Task 3; cobertura de `skills.py` ≥95%.
- [ ] CLI e integración de Task 4.
- [ ] `tests/skills_e2e.sh` en verde.
- [ ] CHANGELOG y docs actualizados.
- [ ] Ningún cambio de routing fuera de lo exigido por B3/B5; las observaciones van en el PR.

## Out of scope

- Cambiar triggers, priorities o task types del registry.
- Nuevas skills o nuevas keywords del clasificador.
- Rediseñar dónde viven las skills stack-scoped fuera del release (B4 solo bloquea el caso peligroso).
