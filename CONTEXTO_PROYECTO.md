# 🏀 NBA QUANT TRADING ENGINE — DOCUMENTO DE CONTEXTO Y TRANSFERENCIA

> **Propósito del Documento:** Proveer contexto técnico completo, arquitectura, estado actual del sistema, decisiones de ingeniería y comandos operativos para continuar el desarrollo o mantenimiento del proyecto en una nueva sesión con un asistente de IA (Claude, Cursor, Antigravity, etc.).

---

## 1. Visión General del Proyecto
**NBA Quant Trading Engine** es un sistema cuantitativo automatizado de inversión y apuestas deportivas para la **NBA**, diseñado bajo la filosofía cuantitativa de **Bill Benter** y dimensionamiento de capital mediante **Kelly Criterion**.

* **Repositorio Git:** `https://github.com/Nymos-byte/nba-quant-engine.git` (Rama `main`)
* **Directorio local de trabajo:** `c:\nba`
* **Moneda base:** Pesos Mexicanos (**MXN**)
* **Zona Horaria Oficial:** `America/Mexico_City` (CDMX)
* **Bookmakers objetivo:** Casas de apuestas mexicanas tradicionales y plataformas de Daily Fantasy / Props (**Draftea**, **Caliente**, etc.).

---

## 2. Arquitectura de Inversión: Core & Satellite

El sistema opera dividiendo estrictamente el capital en dos componentes que **nunca se mezclan**:

```
                  ┌──────────────────────────────────────────────┐
                  │           BANKROLL TOTAL (MXN)               │
                  └──────────────────────┬───────────────────────┘
                                         │
             ┌───────────────────────────┴───────────────────────────┐
             ▼                                                       ▼
   [🟢 CORE QUANT (90%+)]                                [🎰 ACTION SATELLITE (<10%)]
   • Apuestas Directas (Straight)                        • Soñadoras / Parlays (Draftea)
   • Moneyline, Spreads, Totals                          • Player Props correlacionados
   • Modelo Benter + Quarter-Kelly                       • Líneas alternativas (P >= 75%)
   • Gestión estricta de riesgo institucional            • Stake fijo recreativo ($20 MXN)
```

### 🟢 A. Módulo Core Quant (Institucional / Alta Convicción)
* **Ensamble de Bill Benter:**
  $$P_{\text{final}} = 0.35 \times P_{\text{Modelo}} + 0.65 \times P_{\text{Mercado Devigged}}$$
* **Modelo Fundamental:** Simulación Monte Carlo (10,000 iteraciones) alimentada por ratings de ritmo (*Pace*), eficiencia ofensiva (*ORtg*) y defensiva (*DRtg*), con ajuste de ventaja de localía (+2.8 pts).
* **Filtros Institucionales de Riesgo:**
  * **Edge mínimo:** $\ge +4.0\%$ respecto al mercado justo desviggeado.
  * **EV% mínimo:** $\ge +5.0\%$ de valor esperado.
  * **Quarter-Kelly:** $0.25 \times \text{Kelly Formula}$.
  * **Piso de Kelly:** Si el stake sugerido es $< 1.00\%$ del bankroll, se descarta ($0.00\%$).
  * **Techo por apuesta:** Truncamiento máximo a **$1.75\%$** del bankroll por selección.
  * **Techo diario total:** Exposición total del portafolio limitada al **$10.0\%$ diario** (aplica reescalado proporcional automático si la suma excede este límite).
  * **Deduplicación:** Máximo 1 sola apuesta por partido (se elige la de mayor EV%) y máximo 6 apuestas Core al día para evitar dilución.
  * **Piso operativo:** Mínimo de **$5.00 MXN** por apuesta (mínimo permitido por casas mexicanas).

### 🎰 B. Módulo Action Satellite / "Soñadoras" (Draftea / Recreativo)
* **Objetivo:** Capturar asimetría positiva con riesgo acotado mediante boletos de 4 patas correlacionadas.
* **Presupuesto semanal:** Máximo **$150.00 MXN semanales** acumulados.
* **Stake por boleto:** **$20.00 MXN fijos**.
* **Líneas de Piso Alternativas:** Para no depender del azar de las líneas altas, se proyectan estadísticas de jugadores y se seleccionan **líneas de piso alternativas de Draftea con probabilidad teórica $\ge 75\%$** (distribución Gamma/Poisson).
* **Correlaciones Matemáticas:**
  * *Game Script Stacking:* Asistencias del generador (AST) + Puntos del anotador (PTS) del mismo equipo.
  * *Pace Stacking:* Múltiples selecciones Over impulsadas por partidos de alto ritmo proyectado.
* ⚠️ **REGLA DE ORO:** **PROHIBIDO** incluir selecciones directas del Core en los parlays.

---

## 3. Pipeline de Datos y Conectividad

1. **Momios y Líneas Sharp (`The Odds API`):**
   * Archivo: `src/data/odds_collector.py`
   * Extrae Moneyline, Spreads y Totals. Elimina el margen de la casa (*overround / vig stripping*) para obtener probabilidades justas simétricas que sumen 1.0.
   * Cuenta con fixture offline sintético en caso de que no haya API Key configurada.
2. **Resultados y Boxscores en Vivo (`ESPN API`):**
   * Archivo: `src/data/espn_collector.py`
   * **Akamai-proof:** Usa endpoints públicos de ESPN (`site.api.espn.com`) que no sufren bloqueos de IP ni rate-limiting en GitHub Actions o servidores cloud.
   * Calcula posesiones y *Four Factors* mediante la fórmula oficial de Dean Oliver:
     $$\text{Posesiones} = FGA + 0.44 \times FTA - OREB + TO$$
3. **Estadísticas de Jugadores y Plantillas (`nba_api` + Cache Local):**
   * Archivo: `src/data/nba_collector.py`
   * Almacena en `data/cache/player_stats_2025_26.json` una base completa de **110 jugadores titulares de los 30 equipos con todos los traspasos recientes reflejados** (Towns en NYK, Randle/DiVincenzo en MIN, Thompson en DAL, George en PHI, DeRozan en SAC, Caruso en OKC, Paul en SAS, Bridges en NYK, etc.).

---

## 4. Estado Actual del Sistema y Últimos Cambios Implementados

1. **Temporada Regular Activa:**
   * Pretemporada concluida: `config.IS_PRESEASON_MODE = False`.
   * El historial de pretemporada quedó archivado en `data/history/preseason_apuestas.csv` (balance final positivo: **+$200.07 MXN** gracias a las Soñadoras).
   * El archivo oficial de temporada regular `data/history/todas_las_apuestas.csv` está limpio y listo para el registro de la nueva temporada.
2. **Filtro Estricto de Fecha y Hora Local (CDMX):**
   * Anteriormente *The Odds API* enviaba partidos dentro de una ventana de 36 horas en UTC, provocando que a las 11:00 AM CDMX se evaluaran juegos de mañana y se mezclaran jugadores de días diferentes en las Soñadoras.
   * Ahora `main.py` convierte el `commence_time` a la zona horaria `America/Mexico_City` e ignora cualquier partido que no sea de la fecha local en curso.
3. **Gestión Persistente del Bankroll:**
   * Archivo de estado: `data/bankroll_state.json` con formato:
     ```json
     {
       "current_bankroll": 1000.0,
       "last_updated": "2026-10-21 15:17:19"
     }
     ```
   * Comando CLI en `main.py`: `--set-bankroll <monto>` para ajustar saldo base desde la terminal sin tocar código.
   * Sincronización automática nocturna en `settle_nba.py`: al liquidar la jornada, suma algebraicamente el PnL neto del día al bankroll y lo guarda en el JSON.
4. **Batería de Pruebas Automatizadas:**
   * **32 pruebas unitarias pasando al 100%** mediante `python -m pytest tests/ -v`.

---

## 5. Estructura del Proyecto

```text
nba-quant-engine/
├── config.py                       # Configuración centralizada, parámetros Benter/Kelly y Bankroll
├── main.py                         # Orquestador diario (escaneo +EV, Kelly, Soñadoras, Telegram)
├── settle_nba.py                   # Motor de liquidación nocturna e historial PnL
├── CONTEXTO_PROYECTO.md            # Este documento de contexto técnico
├── data/
│   ├── bankroll_state.json         # Estado persistente del bankroll en tiempo real
│   ├── cache/                      # JSONs cacheados de stats y momios
│   └── history/
│       ├── todas_las_apuestas.csv  # Historial maestro oficial de temporada regular
│       └── preseason_apuestas.csv  # Histórico archivado de la pretemporada
├── src/
│   ├── data/
│   │   ├── espn_collector.py       # API ESPN (scoreboard, boxscores, four factors)
│   │   ├── nba_collector.py        # stats.nba.com / fallback de jugadores
│   │   └── odds_collector.py       # The Odds API y algoritmo devig
│   ├── models/
│   │   ├── benter_engine.py        # Ensamble Benter y cálculo Quarter-Kelly
│   │   ├── rating_engine.py        # Proyecciones de posesiones y Monte Carlo
│   │   ├── risk_manager.py         # Deduplicación por partido y reescalado de exposición
│   │   ├── player_props.py         # Modelado de distribuciones y líneas de piso P>=75%
│   │   └── parlay_builder.py       # Ensamblador de boletos Draftea correlacionados
│   └── utils/
│       └── notifier.py             # Formateo y despacho de alertas vía Telegram
└── tests/                          # 32 pruebas unitarias automatizadas con pytest
    ├── test_benter_engine.py
    ├── test_espn_collector.py
    ├── test_parlays.py
    ├── test_rating_engine.py
    ├── test_risk_management.py
    └── test_settlement.py
```

---

## 6. Comandos Operativos Clave

* **Ajustar el bankroll manualmente (ejemplo a $1,250 MXN):**
  ```bash
  python main.py --set-bankroll 1250.0
  ```
* **Ejecutar el pipeline del día (forzando ejecución aunque ya exista centinela):**
  ```bash
  python main.py --force
  ```
* **Ejecutar liquidación de partidos de ayer:**
  ```bash
  python settle_nba.py
  ```
* **Correr la suite completa de pruebas unitarias:**
  ```bash
  python -m pytest tests/ -v
  ```

---

## 7. Automatización y Horarios de Ejecución (Cronjobs)
* **Generación de Picks (Diurno): `11:00 AM - 12:00 PM CDMX`**
  * Horario óptimo: las casas ya tienen momios consolidados y Draftea ya abrió las líneas de jugadores para la jornada.
* **Liquidación de Resultados (Nocturno): `01:00 AM CDMX`**
  * Horario óptimo: los partidos de la Costa Oeste (Lakers, Warriors, Clippers) ya finalizaron y ESPN tiene los boxscores cerrados.
* **Workflows en GitHub Actions:** `.github/workflows/daily_pipeline.yml` está configurado para dispararse vía webhook (`repository_dispatch` / `workflow_dispatch`) mediante servicios externos como `cron-job.org` para evitar retrasos de cola en GitHub Actions.
