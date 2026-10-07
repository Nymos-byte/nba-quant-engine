# nba-quant-engine

Sistema algorítmico híbrido de trading deportivo para la NBA enfocado en valor esperado (+EV), simulación estadística y gestión institucional de riesgo, integrando además un submódulo de parlays recreativos de alta cuota con presupuesto acotado.

---

## 🏛️ Arquitectura General

El sistema opera bajo un enfoque desacoplado **Core & Satellite**:

```
                                 [ nba_api ]          [ The Odds API ]
                                      │                      │
                                      ▼                      ▼
                            [ RatingEngine (MC) ]    [ OddsCollector ]
                                      │                      │
                               P_model│                      │P_fair (De-vigged)
                                      └──────────┬───────────┘
                                                 ▼
                                        [ BenterEngine ]
                                        (Linear Ensemble)
                                                 │
                                                 ▼
                                     [ Risk Gate / Filters ]
                                 (Kelly, Floor/Ceiling, Max Exp)
                                                 │
                      ┌──────────────────────────┴──────────────────────────┐
                      ▼                                                     ▼
           [ Core Quant Engine ]                                 [ Satellite Parlays ]
          - Straight Bets (+EV)                                 - Player Props Combinations
          - Moneyline / Spread / Totals                         - Draftea / DFS Rules
          - Quarter-Kelly (1.0% - 1.75%)                        - Odds: 8.0 - 25.0
          - Max 10% daily exposure                              - Weekly budget: $150 MXN ($20/ticket)
                      │                                                     │
                      └──────────────────────────┬──────────────────────────┘
                                                 ▼
                                             [ main.py ]
                                    (Picks History & Reporting)
                                                 │
                                                 ▼
                                          [ settle_nba.py ]
                                     (Idempotent Settlement)
```

### 1. Motor Cuantitativo Institucional (Core)
- **Apuestas simples (+EV):** Moneyline, Spreads y Totales.
- **Ensamble híbrido estilo Bill Benter:** Combina el modelo fundamental (posesiones y ratings vía `nba_api`) con la probabilidad justa desprovista de vig (*de-vigged*) de casas de apuestas Sharp:
  $$P_{\text{final}} = (w_{\text{model}} \cdot P_{\text{model}}) + (w_{\text{market}} \cdot P_{\text{market\_fair}})$$
  con $w_{\text{model}} = 0.35$ y $w_{\text{market}} = 0.65$.
- **Simulación Monte Carlo:** 10,000 iteraciones con dispersión bivariada ($\sigma \approx 10.5$) incorporando ventaja de localía ($+2.8$ puntos netos).
- **Dimensionamiento por Criterio de Kelly Fraccional:** Quarter-Kelly ($f^* = 0.25 \times \text{Kelly}$).
- **Invariantes de Riesgo Institucional:**
  - **Piso mínimo:** Si $f^* < 1.00\%$, el pick se descarta (stake = 0).
  - **Techo individual:** Si $f^* > 1.75\%$, se trunca en $1.75\%$.
  - **Deduplicación estricta:** Máximo 1 apuesta por partido (`game_id`), conservando la de mayor EV%.
  - **Techo de exposición diaria:** Máximo $10.0\%$ del bankroll total. Si la suma de stakes supera el 10.0%, se reescala proporcionalmente con $S = 0.10 / \sum f^*$.

### 2. Generador de Parlays Recreativos (Satellite / Draftea)
- **Props de Jugadores:** Modelado estadístico con distribución Normal para puntos, y Poisson/Binomial Negativa para asistencias, rebotes y triples, ajustado al ritmo proyectado del partido.
- **Evaluador de Líneas Alternativas:** Determina $P(\text{stat} \ge \text{línea})$ y detecta si mover la línea en Draftea ofrece valor real (+EV) o es una trampa matemática (*mathematical trap*) por recorte desproporcionado de cuota.
- **Game Script Stacking:** Correlaciones positivas (Asistencias de Base + Puntos de Anotador en partidos de alto Pace, o absorción de uso por lesiones).
- **Parámetros Financieros:**
  - Presupuesto semanal: \$150 MXN.
  - Apuesta fija: \$20 MXN por boleto.
  - Rango de cuotas combinadas: Estrictamente entre 8.0 y 25.0 (+700 a +2400).
  - Número de patas: 3 a 4 selecciones correlacionadas.

---

## 📁 Estructura del Repositorio

```text
nba-quant-engine/
├── .gitignore
├── README.md
├── requirements.txt
├── config.py
├── data/
│   ├── cache/
│   └── history/
├── src/
│   ├── __init__.py
│   ├── data/
│   │   ├── __init__.py
│   │   ├── nba_collector.py
│   │   └── odds_collector.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── rating_engine.py
│   │   ├── benter_engine.py
│   │   ├── player_props.py
│   │   ├── parlay_builder.py
│   │   └── line_evaluator.py
│   └── utils/
│       ├── __init__.py
│       └── notifier.py
├── tests/
│   ├── __init__.py
│   ├── test_risk_management.py
│   ├── test_rating_engine.py
│   ├── test_benter_engine.py
│   └── test_parlays.py
├── main.py
└── settle_nba.py
```

---

## 🚀 Instalación y Requisitos

1. Clonar o inicializar el repositorio:
```bash
git clone https://github.com/<usuario>/nba-quant-engine.git
cd nba-quant-engine
```

2. Crear y activar un entorno virtual:
```bash
python -m venv .venv
# En Windows (PowerShell):
.venv\Scripts\Activate.ps1
# En Linux / macOS:
source .venv/bin/activate
```

3. Instalar dependencias:
```bash
pip install -r requirements.txt
```

4. Configurar variables de entorno (opcional) en un archivo `.env`:
```env
ODDS_API_KEY=tu_api_key_aqui
ODDS_API_REGION=us
TELEGRAM_BOT_TOKEN=tu_token_de_telegram
TELEGRAM_CHAT_ID=tu_chat_id
BANKROLL_CORE=1000.0
WEEKLY_FUN_BUDGET=150.0
```

---

## ⚙️ Uso del Sistema

### 1. Ejecutar Generación de Pronósticos Diarios
```bash
python main.py
```
- Descarga y valida métricas actualizadas de la NBA y cuotas de mercado.
- Ejecuta las simulaciones Monte Carlo y el ensamble Benter.
- Aplica los candados de riesgo (deduplicación por partido, techo diario del 10%, Quarter-Kelly).
- Genera el parlay recreativo Draftea con cuota entre 8.0 y 25.0.
- Guarda las apuestas aprobadas en `data/history/picks_YYYY-MM-DD.csv`.
- Envía el reporte formateado a Telegram o lo imprime en consola.

### 2. Liquidación Diaria de Apuestas
```bash
python settle_nba.py
```
- Lee el archivo histórico del día actual o fecha especificada.
- Consulta los marcadores finales de la NBA de forma idempotente.
- Actualiza resultados (`WON`, `LOST`, `PUSH`), calcula PnL y ROI.
- Genera el archivo bandera `data/history/settlement_YYYY-MM-DD.done`.

---

## 🧪 Pruebas Unitarias

Para ejecutar la suite completa de pruebas:
```bash
pytest tests/ -v
```

Cobertura de pruebas:
- `tests/test_risk_management.py`: Truncamiento en 1.75%, descarte < 1.00%, reescalado diario $\le 10\%$, deduplicación estricta.
- `tests/test_rating_engine.py`: Proyección de posesiones, ratings ofensivos/defensivos, HCA (2.8 pts), convergencia Monte Carlo.
- `tests/test_benter_engine.py`: De-vigging de cuotas, ensamble Benter, cálculo de Edge, EV% y Quarter-Kelly.
- `tests/test_parlays.py`: Construcción de combinadas correlacionadas, rango de cuotas (8.0 a 25.0), evaluación de líneas alternativas y detección de trampas.

---

## ☁️ Ejecución Online Automatizada (Cron Jobs en la Nube)

Tienes dos métodos listos para ejecutar el motor online sin necesidad de tener tu computadora encendida:

### Método 1: GitHub Actions (100% Nativo y Gratuito)
El repositorio incluye el workflow [`.github/workflows/nba_engine_cron.yml`](file:///.github/workflows/nba_engine_cron.yml) que corre automáticamente en los servidores de GitHub:
- **Pronósticos matutinos:** Corre todos los días a las **17:00 UTC** (11:00 AM CDMX).
- **Liquidación nocturna:** Corre todos los días a las **07:00 UTC** (01:00 AM CDMX).
- **Sincronización:** Guarda y comitea automáticamente los archivos de historial `picks_*.csv` al repositorio.

**Cómo activarlo con tus APIs:**
1. En GitHub, ve a tu repositorio ➔ **Settings** ➔ **Secrets and variables** ➔ **Actions**.
2. Haz clic en **New repository secret** y añade:
   - `ODDS_API_KEY`: Tu clave de The Odds API.
   - `TELEGRAM_BOT_TOKEN`: El token de tu bot de Telegram.
   - `TELEGRAM_CHAT_ID`: Tu ID de chat en Telegram.

### Método 2: Disparo con cron-job.org
Puedes usar [cron-job.org](https://cron-job.org) para disparar las tareas a la hora exacta que elijas:

**Opción A: Disparo directo a GitHub Actions vía Webhook**
Crea un trabajo en `cron-job.org` apuntando a la API de GitHub:
- **URL:** `https://api.github.com/repos/Nymos-byte/nba-quant-engine/dispatches`
- **Method:** `POST`
- **Headers:**
  - `Accept: application/vnd.github+json`
  - `Authorization: Bearer <TU_GITHUB_PERSONAL_ACCESS_TOKEN>`
- **Request Body (JSON):**
  ```json
  {"event_type": "run-pipeline"}
  ```
  (O `{"event_type": "run-settle"}` para liquidar).

**Opción B: Servidor Webhook Ligero (`app.py`)**
Si despliegas este repositorio en un hosting gratuito (Render, Railway, PythonAnywhere, etc.):
- Ejecuta `python app.py` (expone puerto 8080).
- Configura en `cron-job.org`:
  - `GET https://tu-servicio.onrender.com/run-pipeline`
  - `GET https://tu-servicio.onrender.com/settle`
