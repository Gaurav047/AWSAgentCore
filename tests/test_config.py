import os

from appointment_app.config import load_env


def test_load_env_reads_nearest_dotenv_without_overriding(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("APP_TEST_FROM_FILE=file\nAPP_TEST_PRESET=file\n")
    nested = tmp_path / "sub" / "dir"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.delenv("APP_SKIP_DOTENV")
    monkeypatch.delenv("APP_TEST_FROM_FILE", raising=False)
    monkeypatch.setenv("APP_TEST_PRESET", "shell")

    assert load_env() == str(tmp_path / ".env")
    assert os.environ["APP_TEST_FROM_FILE"] == "file"
    assert os.environ["APP_TEST_PRESET"] == "shell"  # real environment wins
    monkeypatch.delenv("APP_TEST_FROM_FILE")


def test_load_env_can_be_disabled(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("APP_TEST_FROM_FILE=file\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_SKIP_DOTENV", "1")
    monkeypatch.delenv("APP_TEST_FROM_FILE", raising=False)

    assert load_env() is None
    assert "APP_TEST_FROM_FILE" not in os.environ
