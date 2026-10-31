from types import SimpleNamespace

import pytest

from transcrever_video import gpu
from transcrever_video.media import file_dialog_types, is_audio, is_supported
from transcrever_video.subtitles import format_timestamp, srt_block


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00:00,000"),
        (3661.5, "01:01:01,500"),
        (0.9996, "00:00:01,000"),  # arredonda para cima sem estourar os milissegundos
        (-3, "00:00:00,000"),
    ],
)
def test_format_timestamp(seconds, expected):
    assert format_timestamp(seconds) == expected


def test_srt_block():
    block = srt_block(2, 1.0, 3.25, "  Olá, turma.  ")
    assert block == "2\n00:00:01,000 --> 00:00:03,250\nOlá, turma.\n\n"


@pytest.mark.parametrize("name", ["aula.mp4", "AULA.MKV", "podcast.mp3", "voz.M4A", "a.flac"])
def test_formatos_aceitos(name):
    assert is_supported(name)


@pytest.mark.parametrize("name", ["notas.txt", "foto.jpg", "semextensao", "arquivo.mp4.zip"])
def test_formatos_recusados(name):
    assert not is_supported(name)


def test_distingue_audio_de_video():
    assert is_audio("podcast.MP3") and is_audio("zap.ogg")
    assert not is_audio("aula.mp4") and not is_audio("forno.MOV")
    assert not is_audio("sem_extensao")  # o que não é áudio conhecido conta como vídeo


def test_filtros_do_seletor_incluem_audio_video_e_todos():
    filters = dict(file_dialog_types())
    assert "*.mp4" in filters["Vídeo"]
    assert "*.mp3" in filters["Áudio"]
    assert "*.mp4" in filters["Vídeo e áudio"]
    assert "*.mp3" in filters["Vídeo e áudio"]
    assert filters["Todos os arquivos"] == "*.*"


def test_cuda_dll_dirs_acha_pastas_bin(tmp_path, monkeypatch):
    (tmp_path / "cublas" / "bin").mkdir(parents=True)
    (tmp_path / "cudnn" / "bin").mkdir(parents=True)
    (tmp_path / "cudnn" / "include").mkdir()  # não deve entrar
    spec = SimpleNamespace(submodule_search_locations=[str(tmp_path)])
    monkeypatch.setattr(gpu.importlib.util, "find_spec", lambda name: spec)
    assert [p.parent.name for p in gpu.cuda_dll_dirs()] == ["cublas", "cudnn"]


def test_cuda_dll_dirs_sem_pacote_nvidia(monkeypatch):
    monkeypatch.setattr(gpu.importlib.util, "find_spec", lambda name: None)
    assert gpu.cuda_dll_dirs() == []
    assert gpu.setup_cuda_dll_paths() == []
