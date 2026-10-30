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
| `src/data/nba_collector.py`, `season_stats.py` | Ratings, jugadores y lesiones de la temporada (ESPN), con tabla de referencia como base |
| `src/data/espn_collector.py` | Marcadores y boxscores |
| `src/models/rating_engine.py` | Proyección y Monte Carlo |
| `src/models/benter_engine.py` | Ensamble 35/65, edge, EV, Quarter-Kelly |
| `src/models/risk_manager.py` | 1 apuesta por partido, máximo 6 al día, exposición ≤10% |
| `src/models/player_props.py`, `parlay_builder.py` | Patas y boletos del Satellite |
| `src/utils/notifier.py` | Telegram |
| `.github/workflows/daily_pipeline.yml` | Una sola corrida a las 11:00 CDMX (vía cron-job.org): pruebas → liquidar → picks → commit |
| `capture_closing.py`, `.github/workflows/closing_odds.yml` | Cuota de cierre y CLV de los picks pendientes del día |
| `scripts/archive_sample.py` | Archiva la muestra de una versión y reinicia el bankroll |

Historial: `data/history/picks_YYYY-MM-DD.csv`, maestro `todas_las_apuestas.csv`, centinelas `*.done`.

## Convenciones
- Python con type hints, `dataclass(frozen=True)`, `logging`, parámetros en `config.py`.
- Commits convencionales: `fix(scope): ...`. Commit y push solo si se piden.
- Toda corrección de lógica de dinero lleva una prueba que falle antes del arreglo (`tests/test_money_logic.py`).

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

## Estado del plan (v2)
Implementado en código; las líneas de la auditoría de arriba describen la v1.
1. Archivo: `python scripts/archive_sample.py v1`. Ejecutarlo al desplegar, porque el workflow sigue escribiendo en `main` hasta entonces.
2. Liquidación: una pata solo gana verificada; jugador ausente con su box score presente anula (PUSH); box score ausente deja PENDING. El bankroll aplica solo el PnL Core aún no aplicado.
3. Satellite fuera del bankroll, con cuotas marcadas como estimadas. Las líneas reales siguen pendientes.
4. Ratings y jugadores en `src/data/season_stats.py`, con almacén por fecha `data/cache/season_boxscores_2026_27.json`. Mezcla con referencia (`RATING_PRIOR_GAMES`) y resta por bajas (`INJURY_REPLACEMENT_RATE`): ambos parámetros son supuestos sin calibrar. Sin descansos todavía.
5. Filtro: `MAX_MARKET_DISCREPANCY` 10 puntos, con `MIN_EDGE`, `MIN_EV_PERCENT` y `MIN_KELLY_STAKE` bajados para que sea alcanzable. Con cuota 1.91 solo pasan discrepancias de unos 9.7 a 10 puntos: esperar poco volumen y calibrar con datos.
6. CLV: `capture_closing.py` y `.github/workflows/closing_odds.yml`. Gasta créditos de The Odds API en cada captura con picks pendientes.

Sin cuotas reales o sin partidos de la temporada, `main.py` no genera picks: el mercado sintético se eliminó.

## Mejoras de rentabilidad (v2)
- Probabilidad justa: consenso sin margen de todas las casas en la misma línea. Precio de referencia: `PRICE_BOOKMAKERS`. Se apuesta en Draftea, que no está en la API: Telegram da la cuota mínima (`min_odds`) y el resultado en papel solo es referencia.
- Simulador calibrado con 2026-27: desviación 19 en total y 13.5 en margen. Se corrigió `expected_pace` y el sesgo de totales por ORtg ≠ DRtg. Se evalúan spreads de ambos lados y la fatiga por back-to-back (supuesto sin calibrar).
- Props en Core (`market=player_prop`) con líneas reales de The Odds API, ambos lados y a lo sumo 1 por partido y 3 al día. La media del jugador se encoge hacia la media que implica el mercado (`PROP_LINE_PRIOR_GAMES`) y exige `MIN_PROP_GAMES` y `MIN_PROP_MINUTES`. Tope propio `MAX_PROP_DISCREPANCY`, sin calibrar.
- Créditos (plan de 500 al mes): marcador 1 por mercado para todos los partidos; props 1 por mercado y partido, sin opción en bloque. `prop_events_budget` reparte el mes. `ENABLE_PLAYER_PROPS=false` las apaga.
- Medición: `python scripts/evaluate_model.py` (validación sin mirar el futuro). Telegram da ROI por mercado, Brier y CLV.
- Primera medición (71 partidos): Brier del modelo 0.2548, sin mejorar a una moneda al aire. Sin evidencia de ventaja hasta ahora.

Criterio: 100 apuestas de la versión corregida como primer control (Brier frente al mercado y CLV), rentabilidad con 300 o más. Con 100 el ROI tiene ±22 puntos de margen.
