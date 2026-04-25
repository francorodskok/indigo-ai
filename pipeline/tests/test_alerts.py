"""
Tests del módulo alerts.py — emails de alerta post-ciclo.

Cubre:
  - would_alert: dispara con material drifts, missing, unexpected; no dispara con clean
  - build_alert_body: subject y body reflejan stage failures y report
  - send_alert: no-op si no hay config; usa client factory inyectado en tests
  - send_alert: nunca raise ante errores SMTP
  - maybe_alert_cycle: orquesta decisión + envío
"""

import os

import pytest


# ── would_alert ───────────────────────────────────────────────────────────────

class TestWouldAlert:
    def test_no_report_does_not_alert(self):
        from pipeline.alerts import would_alert
        ok, reason = would_alert(None)
        assert ok is False
        assert reason

    def test_clean_report_does_not_alert(self):
        from pipeline.alerts import would_alert
        report = {
            "summary": {"n_material_drifts": 0},
            "missing_from_account": [],
            "unexpected_in_account": [],
        }
        ok, _ = would_alert(report)
        assert ok is False

    def test_material_drifts_trigger_alert(self):
        from pipeline.alerts import would_alert
        report = {
            "summary": {"n_material_drifts": 2},
            "missing_from_account": [],
            "unexpected_in_account": [],
        }
        ok, reason = would_alert(report)
        assert ok is True
        assert "2" in reason and "material" in reason.lower()

    def test_missing_tickers_trigger_alert(self):
        from pipeline.alerts import would_alert
        report = {
            "summary": {"n_material_drifts": 0},
            "missing_from_account": ["AAPL", "MSFT"],
            "unexpected_in_account": [],
        }
        ok, reason = would_alert(report)
        assert ok is True
        assert "AAPL" in reason or "faltant" in reason.lower()

    def test_unexpected_tickers_trigger_alert(self):
        from pipeline.alerts import would_alert
        report = {
            "summary": {"n_material_drifts": 0},
            "missing_from_account": [],
            "unexpected_in_account": ["XYZ"],
        }
        ok, reason = would_alert(report)
        assert ok is True
        assert "XYZ" in reason or "inesperado" in reason.lower()


# ── build_alert_body ──────────────────────────────────────────────────────────

class TestBuildAlertBody:
    def test_subject_includes_cycle_id(self):
        from pipeline.alerts import build_alert_body
        subject, _ = build_alert_body(results=[], report=None, cycle_id="2026-04-25")
        assert "2026-04-25" in subject
        assert "Indigo AI" in subject

    def test_subject_mentions_stage_failures(self):
        from pipeline.alerts import build_alert_body
        results = [
            {"stage": "filter", "ok": True, "seconds": 1.0},
            {"stage": "analyst", "ok": False, "seconds": 5.0, "error": "RuntimeError: boom"},
        ]
        subject, body = build_alert_body(results, report=None, cycle_id="2026-04-25")
        assert "FALLARON" in subject or "fallaron" in subject.lower()
        assert "analyst" in body
        assert "RuntimeError: boom" in body

    def test_subject_mentions_material_drifts(self):
        from pipeline.alerts import build_alert_body
        report = {
            "summary": {"n_material_drifts": 3, "total_abs_drift_bps": 200,
                        "max_drift_ticker": "NVDA", "max_drift_bps": 90,
                        "cash_drift_bps": 10},
            "missing_from_account": [],
            "unexpected_in_account": [],
        }
        subject, body = build_alert_body(results=[], report=report, cycle_id="2026-04-25")
        assert "3" in subject
        assert "NVDA" in body
        assert "200" in body  # total_abs_drift_bps

    def test_body_lists_missing_and_unexpected(self):
        from pipeline.alerts import build_alert_body
        report = {
            "summary": {"n_material_drifts": 0, "total_abs_drift_bps": 0,
                        "max_drift_ticker": "—", "max_drift_bps": 0,
                        "cash_drift_bps": 0},
            "missing_from_account": ["AAA"],
            "unexpected_in_account": ["ZZZ"],
        }
        _, body = build_alert_body(results=[], report=report, cycle_id="X")
        assert "AAA" in body
        assert "ZZZ" in body

    def test_works_with_StageResult_objects(self):
        """Acepta tanto dicts como StageResult objects."""
        from pipeline.alerts import build_alert_body
        from pipeline.orchestrate import StageResult
        sr = StageResult("constructor")
        sr.ok = False
        sr.error = "ValueError: bad config"
        sr.seconds = 2.0
        subject, body = build_alert_body([sr], cycle_id="2026-04-25")
        assert "constructor" in body
        assert "ValueError: bad config" in body


# ── send_alert ────────────────────────────────────────────────────────────────

class _FakeSMTP:
    """SMTP fake para tests — registra todas las llamadas."""
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.calls: list[tuple] = []

    def ehlo(self):
        self.calls.append(("ehlo",))

    def starttls(self):
        self.calls.append(("starttls",))

    def login(self, user, pw):
        self.calls.append(("login", user, pw))

    def sendmail(self, frm, to, msg):
        self.calls.append(("sendmail", frm, to, msg))

    def quit(self):
        self.calls.append(("quit",))


class TestSendAlert:
    def test_returns_false_when_config_missing(self, monkeypatch):
        """Si las env vars no están, no envía y retorna False (no-op)."""
        from pipeline.alerts import send_alert
        for v in ("INDIGO_ALERT_SMTP_HOST", "INDIGO_ALERT_SMTP_PORT",
                  "INDIGO_ALERT_SMTP_USER", "INDIGO_ALERT_SMTP_PASSWORD",
                  "INDIGO_ALERT_FROM", "INDIGO_ALERT_TO"):
            monkeypatch.delenv(v, raising=False)
        result = send_alert("subject", "body")
        assert result is False

    def test_sends_with_injected_factory(self):
        from pipeline.alerts import send_alert
        captured = {}

        def factory(host, port):
            srv = _FakeSMTP(host, port)
            captured["server"] = srv
            return srv

        cfg = {
            "host": "smtp.test", "port": 587,
            "user": "u", "password": "p",
            "from": "u@test", "to": ["a@test", "b@test"],
            "_client_factory": factory,
        }
        ok = send_alert("hola", "cuerpo del email", smtp_config=cfg)
        assert ok is True
        srv = captured["server"]
        # Debe haber hecho sendmail con from y la lista to
        sendmail_calls = [c for c in srv.calls if c[0] == "sendmail"]
        assert len(sendmail_calls) == 1
        assert sendmail_calls[0][1] == "u@test"
        assert sendmail_calls[0][2] == ["a@test", "b@test"]
        # El subject viene como header plain; el body puede venir base64 si es utf-8
        from email import message_from_string
        msg = message_from_string(sendmail_calls[0][3])
        assert msg["Subject"] == "hola"
        body = msg.get_payload(decode=True).decode("utf-8")
        assert "cuerpo del email" in body
        # También verificamos que hubo login + starttls + quit
        actions = [c[0] for c in srv.calls]
        assert "login" in actions
        assert "quit" in actions

    def test_swallows_smtp_errors(self):
        """Si el cliente SMTP raise, no propaga — retorna False."""
        from pipeline.alerts import send_alert

        def factory(host, port):
            raise ConnectionError("smtp down")

        cfg = {
            "host": "smtp.test", "port": 587,
            "user": "u", "password": "p",
            "from": "u@test", "to": ["a@test"],
            "_client_factory": factory,
        }
        # No raise, retorna False
        ok = send_alert("s", "b", smtp_config=cfg)
        assert ok is False

    def test_starttls_failure_does_not_abort(self):
        """Si STARTTLS falla, igual debe seguir y enviar (servers dev)."""
        from pipeline.alerts import send_alert
        import smtplib

        class NoTlsSMTP(_FakeSMTP):
            def starttls(self):
                raise smtplib.SMTPException("no tls")

        cfg = {
            "host": "smtp.test", "port": 587,
            "user": "u", "password": "p",
            "from": "u@test", "to": ["a@test"],
            "_client_factory": lambda h, p: NoTlsSMTP(h, p),
        }
        ok = send_alert("s", "b", smtp_config=cfg)
        assert ok is True

    def test_reads_config_from_env(self, monkeypatch):
        """Si no se inyecta config, lee de env vars."""
        from pipeline import alerts
        monkeypatch.setenv("INDIGO_ALERT_SMTP_HOST", "smtp.x")
        monkeypatch.setenv("INDIGO_ALERT_SMTP_PORT", "587")
        monkeypatch.setenv("INDIGO_ALERT_SMTP_USER", "u")
        monkeypatch.setenv("INDIGO_ALERT_SMTP_PASSWORD", "p")
        monkeypatch.setenv("INDIGO_ALERT_FROM", "u@x")
        monkeypatch.setenv("INDIGO_ALERT_TO", "a@x,b@x")
        cfg = alerts._read_smtp_config()
        assert cfg["host"] == "smtp.x"
        assert cfg["port"] == 587
        assert cfg["to"] == ["a@x", "b@x"]

    def test_invalid_port_returns_none(self, monkeypatch):
        from pipeline import alerts
        monkeypatch.setenv("INDIGO_ALERT_SMTP_HOST", "smtp.x")
        monkeypatch.setenv("INDIGO_ALERT_SMTP_PORT", "no-numero")
        monkeypatch.setenv("INDIGO_ALERT_SMTP_USER", "u")
        monkeypatch.setenv("INDIGO_ALERT_SMTP_PASSWORD", "p")
        monkeypatch.setenv("INDIGO_ALERT_FROM", "u@x")
        monkeypatch.setenv("INDIGO_ALERT_TO", "a@x")
        assert alerts._read_smtp_config() is None


# ── maybe_alert_cycle ─────────────────────────────────────────────────────────

class TestMaybeAlertCycle:
    def test_no_alert_when_clean(self, monkeypatch):
        """Sin failures ni drifts, no se intenta enviar."""
        from pipeline.alerts import maybe_alert_cycle
        report = {"summary": {"n_material_drifts": 0},
                  "missing_from_account": [], "unexpected_in_account": []}
        results = [{"stage": "filter", "ok": True}]
        attempted = maybe_alert_cycle(results, report, cycle_id="X")
        assert attempted is False

    def test_alert_when_stage_failed(self, monkeypatch):
        """Con un stage fallado intenta enviar (sin SMTP, no-op pero attempted=True)."""
        from pipeline import alerts
        for v in ("INDIGO_ALERT_SMTP_HOST", "INDIGO_ALERT_SMTP_PORT"):
            monkeypatch.delenv(v, raising=False)
        results = [{"stage": "analyst", "ok": False, "error": "boom"}]
        attempted = alerts.maybe_alert_cycle(results, None, cycle_id="X")
        # attempted=True porque hay razón; el envío en sí falla silenciosamente
        # por falta de config.
        assert attempted is True

    def test_alert_when_material_drifts(self, monkeypatch):
        from pipeline import alerts
        for v in ("INDIGO_ALERT_SMTP_HOST", "INDIGO_ALERT_SMTP_PORT"):
            monkeypatch.delenv(v, raising=False)
        report = {"summary": {"n_material_drifts": 1},
                  "missing_from_account": [], "unexpected_in_account": []}
        attempted = alerts.maybe_alert_cycle([], report, cycle_id="X")
        assert attempted is True
