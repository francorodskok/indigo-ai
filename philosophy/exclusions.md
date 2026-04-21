# Exclusiones del Sistema Indigo AI

**Versión:** 1.0
**Referenciado por:** Constitución sección 11, sección 3
**Última modificación:** abril de 2026

Toda empresa que figure en cualquiera de las categorías de este documento es inelegible para el portafolio, sin excepción y sin importar cuán atractiva sea su valuación o sus fundamentals. El filtro cuantitativo las descarta antes de que ningún agente las evalúe.

---

## 1. Sectores excluidos por valores

| Sector / Actividad | Criterio de identificación |
|---|---|
| Armamento y defensa ofensiva | SIC 3489, 3761, 3769, 3812 o equivalente GICS; empresas donde >25% del revenue proviene de armas, municiones, o sistemas de armas ofensivos |
| Tabaco | GICS 30203010; cualquier empresa con manufactura o distribución de productos de tabaco como actividad principal |
| Juego de apuestas y casinos | GICS 25301040; casinos, apuestas deportivas, loterías privadas |
| Pornografía | Cualquier empresa cuya actividad principal sea producción o distribución de contenido para adultos |

Empresas con exposición menor al 10% en estas actividades no se excluyen automáticamente, pero el agente debe declarar la exposición en el rationale.

---

## 2. Tipos de empresa excluidos por riesgo

| Tipo | Criterio |
|---|---|
| Empresas en proceso de quiebra | Chapter 11, Chapter 7, o equivalente; o con "going concern" warning en el último auditor report |
| SPACs sin target definido | Cualquier SPAC que no haya completado su merger y no tenga target público |
| Empresas bajo investigación SEC activa | Investigación formal abierta (no preliminary inquiry); se verifica en la base de datos pública de la SEC |
| Re-statement de estados financieros | Cualquier restatement en los últimos 24 meses, independientemente de la razón declarada |
| Empresas con auditor no-Big4 sin justificación | Para capitalizaciones > USD 5B, el auditor debe ser una firma reconocida internacionalmente |

---

## 3. Instrumentos excluidos

El sistema no opera ninguno de los siguientes instrumentos, aunque el broker los permita:

- Derivados de cualquier tipo (opciones, futuros, swaps, warrants)
- ETFs y fondos cerrados (el sistema construye exposición accionaria directa)
- ADRs de empresas que no forman parte del S&P 500
- Posiciones cortas (short selling)
- Posiciones apalancadas
- Criptomonedas y tokens digitales de cualquier tipo

---

## 4. Criterios de liquidez

| Criterio | Umbral | Consecuencia |
|---|---|---|
| Volumen mínimo de entrada | USD 50M promedio diario en los últimos 30 días | No entra al pool de candidatos |
| Volumen mínimo de permanencia | USD 30M promedio diario en los últimos 30 días | Se marca para salida en el siguiente ciclo de rebalanceo |

La diferencia entre umbral de entrada (50M) y umbral de permanencia (30M) es deliberada: evita que el sistema rote innecesariamente ante caídas transitorias de volumen.

---

## 5. Enmiendas a esta lista

Nuevas exclusiones pueden agregarse por decisión de cualquiera de los dos firmantes, con notificación al otro, y producen una versión menor del documento (1.0 → 1.1). La fecha de la enmienda se registra en el historial de Git. El sistema lee la versión vigente al inicio de cada ciclo.

Ninguna exclusión puede eliminarse sin acuerdo explícito de los dos firmantes.

---

*Fin de exclusions.md, versión 1.0.*
