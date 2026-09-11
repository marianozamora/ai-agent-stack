# Plan de consolidación — de ambición a evidencia

Estado al 2026-09-08: v0.9.4, 2.893 LOC en `ai_stack/`, 39 commits, un autor, cero
usuarios externos. Este documento es el plan para cerrar la brecha entre lo que el
stack promete y lo que puede demostrar.

## Principio ordenador

Los cuatro problemas no son independientes. La deuda técnica, la fragilidad de las
dependencias y la falta de adopción son todas **consecuencias** de no haber medido
todavía si la capa aporta algo. Sin ese número no hay criterio para decidir qué
refactorizar, qué hacer opcional ni qué borrar.

Por lo tanto: **congelar features hasta cerrar la Fase 1.** La única excepción es
trabajo que la propia Fase 1 necesita para poder correr.

Orden: medir → afilar → ensanchar → pagar deuda → exponer.

---

## Fase 1 — Prueba de valor (bloqueante)

**Pregunta a responder:** ¿un PR producido a través del stack es mejor, más barato o
más rápido que el mismo PR producido con `claude` directo?

Hoy `ai_stack/benchmark.py` responde otra pregunta. Sus 6 fixtures son diccionarios
estáticos (`BENCHMARK_TASKS`) y sólo verifican que `classify`, `classify_task`,
`select_skills` y `context_caps` sean deterministas y estables. Eso es un test de
regresión del router, no una medición de resultados. Sirve — pero no es evidencia de
valor y no debe seguir presentándose como tal.

### 1.1 Corpus con verdad de referencia

Elegir 3 repos OSS reales con suite de tests propia (Python, TS y uno con migraciones
o schema). De cada uno, tomar **5 PRs ya mergeados** con test que falla antes y pasa
después. Revertir el merge y usar el issue original como enunciado de la tarea.

15 tareas, distribuidas a propósito por riesgo: ~5 triviales (tipo `ui-copy`), ~6
medias, ~4 de alto riesgo (auth, pagos, migraciones). Las categorías ya existen en
`classify` — usar las mismas para poder cruzar resultados con el router.

Ventaja de este diseño: hay diff de referencia y hay una suite que dictamina
correctitud sin juicio humano. Es la parte cara del trabajo; hacerla una sola vez.

- [ ] `templates/benchmarks/live/<repo>/<task>.json` con: repo, commit base, issue,
      diff de referencia, comando de test, test que debe pasar
- [ ] Script de checkout/reset reproducible por tarea

### 1.2 Brazo de control

Para cada tarea, dos corridas desde el mismo commit base:

- **A (control):** `claude` directo sobre el repo, con el enunciado del issue y nada más.
- **B (stack):** `ai plan` / `ai run` / `ai pipeline` / `ai ready` con perfil `standard`.

Mismo modelo, misma semilla de enunciado, sandbox limpio por corrida. Tres
repeticiones por brazo por tarea para tener dispersión (45 corridas por brazo).

### 1.3 Métricas

Cinco, todas ya derivables de `metrics.jsonl` salvo la última:

| Métrica | Definición | Por qué importa |
|---|---|---|
| Corrección | ¿pasa el test de referencia sin editarlo? | el resultado, no el proceso |
| Rondas hasta verde | intentos hasta que la suite pasa | el argumento central del gating |
| Costo | tokens totales de la tarea, ambos brazos | contra-argumento obvio: la capa gasta más |
| Latencia | wall-clock de extremo a extremo | costo humano de esperar |
| Fugas | fallos que ninguno de los dos brazos detecta pero el diff de referencia sí | mide la calidad del gate, no su existencia |

Registrar además la **tasa de falsos `PR_READY`**: corridas certificadas como listas
cuyo test de referencia falla. Es la métrica que más daño hace a la tesis si es alta,
y por eso la más importante de publicar.

### 1.4 Criterio de decisión

Escribir **antes** de correr nada:

- **Sigue:** el brazo B gana en corrección o en rondas-hasta-verde por un margen que
  sobrevive la dispersión de 3 repeticiones, sin más de 2× el costo del brazo A.
- **Se recorta:** B empata en calidad pero cuesta más. Entonces el valor no está en
  la orquestación completa; identificar qué gate concreto produjo el delta y reducir
  el stack a eso.
- **Se archiva la tesis grande:** B no mejora nada medible. Resultado honesto y
  perfectamente publicable: el proyecto pasa a ser una herramienta personal
  documentada como tal, y el README deja de prometer un beneficio inexistente.

Hipótesis a priori, para poder equivocarse en público: el delta aparecerá en las
tareas de alto riesgo (contrato + evidencia fresca evitan el PR plausible-pero-roto) y
será negativo en las triviales, donde la capa sólo agrega latencia y tokens. Si eso se
confirma, la conclusión de producto es que el perfil `fast` debería ser un passthrough.

- [ ] `docs/evidence/RESULTS.md` con tabla cruda, no sólo el resumen
- [ ] Corridas crudas versionadas en `docs/evidence/runs/`
- [ ] `ai benchmark --live` como el modo opt-in ya previsto en el ROADMAP

**Salida de fase:** un número defendible por un escéptico, o la decisión de recortar.

---

## Fase 2 — Afilar la propuesta

Sólo después de la Fase 1, y en función de lo que diga.

- [ ] Reescribir el README alrededor de **una** afirmación medida, con el número y el
      enlace a la evidencia. Hoy el README abre describiendo la arquitectura
      ("zero-footprint, token-aware workflow") — describe cómo está hecho, no qué gana
      quien lo use.
- [ ] Mover a `docs/architecture.md` todo lo que sea explicación de mecanismo.
- [ ] Podar del ROADMAP lo que la evidencia no respalde. Los seis ítems de "v1.0" y
      los cuatro "diferidos" son, hoy, ambición sin demanda. La sección "Deferred
      pending usage evidence" ya tiene el criterio correcto — aplicarlo también a
      "v1.0 — production workflow".
- [ ] Declarar explícitamente qué **no** hace el stack.

---

## Fase 3 — Ensanchar el happy path

El pipeline debe correr entero sin ninguna herramienta opcional instalada. Hoy no:
`tools.py:19`, `:35`, `:56` y `:104` hacen `raise SystemExit` cuando falta `ctx7`,
`graphify` o `codex`, y `crg.py` sigue el mismo patrón.

- [ ] Interfaz de driver por herramienta externa (`codex`, `crg`, `graphify`, `ctx7`,
      `rtk`) con implementación nula que **degrada, no aborta**: el gate se salta,
      queda registrado en `metrics.jsonl` como `skipped: tool-missing`, y `ai ready`
      lo refleja en el veredicto en vez de hacer creer que se ejecutó
- [ ] `ai doctor` imprime una matriz de capacidades: qué está instalado, qué gate
      habilita cada cosa, y qué se pierde sin ella
- [ ] Job de CI en contenedor limpio (sólo Python + git, cero herramientas
      opcionales) que corre el pipeline completo de punta a punta. Es la única
      garantía real de que el camino angosto se ensanchó
- [ ] Medir el tiempo de `git clone` → primer `ai ready` verde en máquina limpia.
      Objetivo: 10 minutos. Publicarlo

Este es el prerequisito de cualquier usuario externo: nadie instala cinco CLIs para
probar algo que todavía no le demostró nada.

---

## Fase 4 — Deuda técnica (sólo la que bloquea)

Criterio: se paga la deuda que impide las fases anteriores. El resto espera.

**Bloqueante para la Fase 3** (drivers e inyección de dobles de prueba son
impracticables con imports planos):

- [ ] Migración a paquete: `from core import x` → `from ai_stack.core import x`,
      eliminar `sys.path.insert` de `cli.py:11` y de los 15 archivos de test. El
      `pyproject.toml` ya documenta el alcance exacto —cuatro rutas de invocación más
      la migración de los `validators.json` ya instalados— y ese análisis sigue
      vigente. Lo que cambió es la razón: en v0.9 no valía el radio de impacto; con
      drivers e inyección de dependencias encima, sí
- [ ] Migración de `validators.json` con versionado de esquema y test de upgrade
- [ ] Unificar en pytest. El job `unittest` de CI cubre el contrato "corre como script
      con stdlib sola" — ese contrato se preserva mejor con un smoke test explícito
      que manteniendo 15 archivos en dos dialectos

**Bloqueante para contribuidores externos:**

- [ ] Partir `workflow.py` (405 líneas), `lifecycle.py` (279) y `gates.py` (268) por
      responsabilidad
- [ ] Reconsiderar el estilo comprimido (`import json, os, re, subprocess, sys`,
      cuerpos en una línea, punto y coma). La decisión de no correr `ruff format`
      está documentada y es defendible para un autor único; deja de serlo el día que
      llegue el primer contribuidor. Si la Fase 5 arranca, hacer el reformateo en un
      commit único y aislado

**Proceso** — la ironía señalada, que es la más barata de arreglar:

- [ ] Un worktree por sesión de agente, obligatorio. Ya existen `.claude/worktrees/`
      con dos worktrees; falta que la regla sea forzada y no opcional
- [ ] Lock por tarea (ya está en el ROADMAP como "per-task pipeline locking")
- [ ] Aplicar el propio stack a su propio desarrollo y registrar esas corridas como
      evidencia. Un framework de orquestación que no se usa a sí mismo tiene un
      problema de credibilidad que ninguna cantidad de tests resuelve

---

## Fase 5 — Validación externa

Sólo con Fase 1 positiva y Fase 3 cerrada. Antes de eso, buscar usuarios es pedirles
que hagan de QA de una hipótesis sin probar.

- [ ] 3 a 5 usuarios alfa, elegidos a mano, con repos reales y suites reales
- [ ] Sesión de instalación observada con cada uno, sin ayudar. Donde se traban es el
      dato; lo que dicen después, no tanto
- [ ] Contrato de feedback explícito: 2 semanas de uso, una llamada de cierre
- [ ] `CONTRIBUTING.md` y buenos primeros issues sólo después de que un alfa haya
      llegado solo a un `PR_READY`

---

## Secuencia y criterios de salida

| Fase | Salida | No empezar la siguiente hasta |
|---|---|---|
| 1 · Evidencia | `docs/evidence/RESULTS.md` | haber decidido seguir / recortar / archivar |
| 2 · Propuesta | README con una afirmación medida | que la afirmación tenga número detrás |
| 3 · Robustez | CI en contenedor limpio en verde | instalar en 10 min sin herramientas opcionales |
| 4 · Deuda | paquete + pytest unificado | que las Fases 1–3 no estén bloqueadas por el layout |
| 5 · Adopción | 3 alfas con un `PR_READY` propio | — |

Fase 1 es el trabajo real: construir el corpus y correr 90 sesiones de agente es de
lejos lo más caro del plan, en tiempo y en tokens. Las fases 2 a 4 son en comparación
mecánicas. Vale la pena resistir la tentación de empezar por la Fase 4, que es la
cómoda.

## Lo que este plan asume

Que el objetivo es un proyecto con usuarios externos. Si el objetivo real es una
herramienta personal afilada, la Fase 1 sigue valiendo la pena —es lo que dice si el
stack te sirve a vos— pero las Fases 3 y 5 se caen enteras y la Fase 4 se reduce a lo
que moleste al usarlo. Vale la pena decidir eso explícitamente antes de arrancar.
