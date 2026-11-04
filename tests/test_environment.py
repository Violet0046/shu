"""安装后配置不应依赖源码包所在目录。"""
import pytest

from shu_grading.config import load_environment


def test_environment_uses_working_directory_and_preserves_system_values(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('SHU_ENV_FILE', raising=False)
    monkeypatch.setenv('SHU_WEB_PORT', '8123')
    monkeypatch.delenv('SHU_PUBLIC_URL', raising=False)
    (tmp_path / '.env').write_text('SHU_WEB_PORT=9000\nSHU_PUBLIC_URL=https://school.example\n', encoding='utf-8')
    load_environment()
    import os
    assert os.environ['SHU_WEB_PORT'] == '8123'
    assert os.environ['SHU_PUBLIC_URL'] == 'https://school.example'


def test_explicit_configuration_takes_precedence_over_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / '.env').write_text('SHU_WEB_PORT=9000\n', encoding='utf-8')
    selected = tmp_path / 'server.env'
    selected.write_text('SHU_WEB_PORT=8124\n', encoding='utf-8')
    monkeypatch.setenv('SHU_ENV_FILE', str(selected))
    monkeypatch.delenv('SHU_WEB_PORT', raising=False)
    load_environment()
    import os
    assert os.environ['SHU_WEB_PORT'] == '8124'


def test_missing_explicit_configuration_fails_clearly(tmp_path, monkeypatch):
    monkeypatch.setenv('SHU_ENV_FILE', str(tmp_path / 'missing.env'))
    with pytest.raises(FileNotFoundError, match='SHU_ENV_FILE'):
        load_environment()
