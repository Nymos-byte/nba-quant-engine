# NBA Quant Engine

Motor de picks de NBA en **paper trading**: objetivo actual, saber si el modelo es rentable. Responder en español. Moneda MXN, zona horaria `America/Mexico_City`.

La pretemporada fueron pruebas: ignorar `data/history/preseason_apuestas.csv` en análisis.

## Antes de trabajar
- `git pull`: el workflow publica historial a diario en `main`. Los datos vigentes están en `origin/main`.
- `CONTEXTO_PROYECTO.md` está desactualizado. Si contradice al código, manda el código.
- Leer solo los archivos que la tarea necesite; este archivo es el mapa.

## Comandos
- Pruebas: `python -m pytest tests/ -v`
- Picks del día: `python main.py [--date YYYY-MM-DD] [--refresh] [--force]`
- Ajustar bankroll: `python main.py --set-bankroll <monto>`
- Liquidar: `python settle_nba.py [--date YYYY-MM-DD] [--force]`
- ⚠️ `main.py` y `settle_nba.py` escriben en `data/` y envían Telegram si hay credenciales. Para probar, usar directorios temporales (`history_dir`, `bankroll_state_file`).
- Si no aparece `npx`: agregar `C:\Program Files\nodejs` al PATH.

## Mapa
| Archivo | Función |
|---|---|
| `config.py` | Parámetros de riesgo y estado del bankroll |
| `main.py` | Pipeline: datos → simulación → Benter/Kelly → filtros → parlay → CSV → Telegram |
| `settle_nba.py` | Liquidación con ESPN, CSV maestro, sincronización del bankroll |
| `src/data/odds_collector.py` | The Odds API y quitar margen (cae a un mercado sintético si falla) |
| `src/data/nba_collector.py` | Ratings y jugadores, caché y tabla de respaldo |
| `src/data/espn_collector.py` | Marcadores y boxscores |
| `src/models/rating_engine.py` | Proyección y Monte Carlo |
| `src/models/benter_engine.py` | Ensamble 35/65, edge, EV, Quarter-Kelly |
| `src/models/risk_manager.py` | 1 apuesta por partido, máximo 6 al día, exposición ≤10% |
| `src/models/player_props.py`, `parlay_builder.py` | Patas y boletos del Satellite |
| `src/utils/notifier.py` | Telegram |
| `.github/workflows/daily_pipeline.yml` | Una sola corrida a las 11:00 CDMX (vía cron-job.org): pruebas → liquidar → picks → commit |

Historial: `data/history/picks_YYYY-MM-DD.csv`, maestro `todas_las_apuestas.csv`, centinelas `*.done`.

## Convenciones
- Python con type hints, `dataclass(frozen=True)`, `logging`, parámetros en `config.py`.
- Commits convencionales: `fix(scope): ...`. Commit y push solo si se piden.
- Toda corrección de lógica de dinero lleva una prueba que falle antes del arreglo: las 32 actuales pasan con estos errores presentes.

## Auditoría de la v1 (30 oct 2026)
Core: 33 liquidadas, 13-20 (39.4%), −$74.60, ROI −13.5%. Brier del modelo 0.309 frente a 0.210 del mercado. El +$171.40 del bankroll no es real: sin los 2 parlays mal ganados, estaría entre $765.40 y $805.40.

Problemas, por prioridad:
1. **Parlays ganados sin verificar** (`settle_nba.py:440` y `:442`): `"WON" if odds <= 12.0` cuando faltan datos de un jugador. Los 2 parlays ganados (27 y 28 oct) pasaron así.
2. **Doble conteo del bankroll** (`settle_nba.py:511-599`): reliquidar un día con pendientes o con `--force` vuelve a sumar el PnL de todo el día. Visto en el código, no reproducido.
3. **Cuotas de parlay inventadas** (`player_props.py:139`): fórmula limitada a 1.70–2.15, sin líneas reales de Draftea. Además, el PnL del Satellite se mezcla con el bankroll del Core (`settle_nba.py:598`).
4. **Ratings congelados**: la caché es idéntica a `NBA_TEAMS_REFERENCE` (sin cambios desde el 7 oct). Su validez se mide por `mtime`, y un checkout fresco la hace parecer nueva, por lo que probablemente nunca se refresca. Sin lesiones ni descansos.
5. **Filtro con selección adversa**: pasar el edge de 4% con peso de modelo 35% exige discrepar mucho del mercado (mínimo observado 16 puntos, media 31).
6. **Plantillas viejas** en `player_stats_2025_26.json` (Garland en CLE, DeRozan en SAC, entre otros).

Menores: `LA Clippers` no coincide con `Los Angeles Clippers` (usa valores genéricos); spreads solo del local; desviación de totales 15.7 frente a 19.9 real; `expected_pace = (h+a)/2.2`; cuotas por defecto de región `us`, no de casas mexicanas.

## Plan acordado
1. Archivar la muestra como v1 y reiniciar el bankroll, sin mezclarla con la siguiente.
2. Corregir la liquidación: jugador sin datos anula el boleto o lo deja pendiente, nunca gana. Corregir el doble conteo.
3. Separar el Satellite del bankroll del Core hasta tener líneas reales.
4. Ratings de la temporada en curso, plantillas y lesiones actualizados a diario.
5. Rehacer el filtro con un tope de discrepancia frente al mercado (partida: 8–10 puntos, a calibrar).
6. Guardar la cuota de cierre para medir CLV.

Criterio: 100 apuestas de la versión corregida como primer control (Brier frente al mercado y CLV), rentabilidad con 300 o más. Con 100 el ROI tiene ±22 puntos de margen.
