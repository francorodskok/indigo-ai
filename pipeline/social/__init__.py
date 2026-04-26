"""Pipeline de copy editorial para redes sociales (Tier 1: solo generación).

Genera drafts de posts a partir de los outputs del pipeline analítico
(`portfolio_*.json`, `debate_*.json`, `nav_history.jsonl`) y los guarda en
`pipeline/outputs/social/drafts/`. Una segunda pasada (`regulatory_filter`)
valida cada draft contra restricciones regulatorias y de tono antes de que
un humano apruebe en el dashboard.

Ver ADR: docs/decisions/2026-04-25-social-copy-pipeline.md
"""
