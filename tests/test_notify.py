import logging

import notify


def test_send_telegram_not_configured_logs_and_skips(monkeypatch, caplog):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with caplog.at_level(logging.INFO):
        result = notify.send_telegram("hello")
    assert result is False
    assert "not configured" in caplog.text.lower()


def test_send_email_not_configured_logs_and_skips(monkeypatch, caplog):
    monkeypatch.delenv("EMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("EMAIL_APP_PASSWORD", raising=False)
    with caplog.at_level(logging.INFO):
        result = notify.send_email("hello")
    assert result is False
    assert "not configured" in caplog.text.lower()


def test_send_telegram_splits_and_sends_each_part(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    sent = []

    class _FakeBot:
        def __init__(self, token):
            sent.append(("init", token))

        async def send_message(self, chat_id, text):
            sent.append(("send", chat_id, text))

    monkeypatch.setattr(notify.telegram, "Bot", _FakeBot)
    monkeypatch.setattr(notify, "split_for_telegram", lambda msg, limit=4096: ["part1", "part2"])

    result = notify.send_telegram("a long message")
    assert result is True
    assert ("send", "12345", "part1") in sent
    assert ("send", "12345", "part2") in sent


def test_send_telegram_api_failure_returns_false(monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    class _FailingBot:
        def __init__(self, token):
            pass

        async def send_message(self, chat_id, text):
            raise RuntimeError("telegram down")

    monkeypatch.setattr(notify.telegram, "Bot", _FailingBot)
    with (logging_capture := __import__("contextlib").nullcontext()):
        result = notify.send_telegram("hello")
    assert result is False


def test_send_email_success(monkeypatch):
    monkeypatch.setenv("EMAIL_ADDRESS", "bot@example.com")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "app-password")

    calls = []

    class _FakeSMTP:
        def __init__(self, host, port):
            calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            calls.append(("starttls",))

        def login(self, user, password):
            calls.append(("login", user, password))

        def send_message(self, msg):
            calls.append(("send", msg["Subject"]))

    monkeypatch.setattr(notify.smtplib, "SMTP", _FakeSMTP)

    result = notify.send_email("digest body text")
    assert result is True
    assert ("login", "bot@example.com", "app-password") in calls
    assert any(c[0] == "send" for c in calls)


def test_send_email_smtp_failure_returns_false(monkeypatch):
    monkeypatch.setenv("EMAIL_ADDRESS", "bot@example.com")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "app-password")

    class _FailingSMTP:
        def __init__(self, host, port):
            raise OSError("connection refused")

    monkeypatch.setattr(notify.smtplib, "SMTP", _FailingSMTP)
    assert notify.send_email("body") is False


def test_send_digest_telegram_failure_does_not_block_email(monkeypatch):
    monkeypatch.setattr(notify, "send_telegram", lambda msg: (_ for _ in ()).throw(RuntimeError("boom")))
    email_calls = []
    monkeypatch.setattr(notify, "send_email", lambda msg: email_calls.append(msg) or True)
    notify.send_digest("the digest")
    assert email_calls == ["the digest"]


def test_send_digest_email_failure_does_not_block_telegram(monkeypatch):
    telegram_calls = []
    monkeypatch.setattr(notify, "send_telegram", lambda msg: telegram_calls.append(msg) or True)
    monkeypatch.setattr(notify, "send_email", lambda msg: (_ for _ in ()).throw(RuntimeError("boom")))
    notify.send_digest("the digest")
    assert telegram_calls == ["the digest"]
