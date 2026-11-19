from pagemint.config import Settings
from pagemint.search.secrets import redact


def test_env_populates_search_settings(monkeypatch):
    monkeypatch.setenv("PAGEMINT_SEARXNG_URL", "http://localhost:8888")
    monkeypatch.setenv("PAGEMINT_TAVILY_API_KEY", "tvly-secret-123")
    monkeypatch.setenv("PAGEMINT_BRAVE_API_KEY", "brave-secret-456")
    monkeypatch.setenv("PAGEMINT_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("PAGEMINT_SEARCH_FALLBACK", "1")
    s = Settings.from_env()
    assert (s.searxng_url, s.tavily_api_key, s.brave_api_key, s.search_provider, s.search_fallback) == (
        "http://localhost:8888", "tvly-secret-123", "brave-secret-456", "tavily", True)
    text = repr(s)
    assert "tvly-secret-123" not in text and "brave-secret-456" not in text


def test_defaults():
    s = Settings()
    assert s.searxng_url == "" and s.tavily_api_key == "" and s.search_fallback is False


def test_redact():
    s = Settings(tavily_api_key="tvly-secret-123", brave_api_key="brave-secret-456")
    out = redact("401 for key tvly-secret-123 and brave-secret-456 here", s)
    assert "secret" not in out and out.count("***") == 2
    assert redact("nothing", Settings()) == "nothing"
    assert redact("abc", Settings(tavily_api_key="ab")) == "abc"  # too short to redact safely
