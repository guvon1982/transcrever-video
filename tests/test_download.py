import threading
from pathlib import Path

import pytest

from transcrever_video import download
from transcrever_video.download import (
    DownloadCancelled,
    DownloadError,
    DownloadProgress,
    describe_download,
    download_audio,
    format_size,
    validate_url,
)

# ---- validação do link ----


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        (
            "https://www.youtube.com/watch?v=jNQXAC9IVRw",
            "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        ),
        ("  https://youtu.be/jNQXAC9IVRw  ", "https://youtu.be/jNQXAC9IVRw"),
        ("youtu.be/jNQXAC9IVRw", "https://youtu.be/jNQXAC9IVRw"),  # sem o https://
        ("http://m.youtube.com/watch?v=abc", "http://m.youtube.com/watch?v=abc"),
        ("https://YOUTUBE.com/watch?v=abc", "https://YOUTUBE.com/watch?v=abc"),
    ],
)
def test_aceita_links_do_youtube(typed, expected):
    assert validate_url(typed) == expected


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "   ",
        "meu video.mp4",
        "ftp://youtube.com/watch?v=abc",
        "file:///C:/Users/guvon/segredo.txt",
        "https://vimeo.com/76979871",  # o Vimeo exige login: fora do escopo
        "https://youtube.com.evil.com/watch?v=abc",  # nome parecido, dono diferente
        "https://evil.com/?u=https://youtube.com/watch?v=abc",
        "https://www.youtube.com@evil.com/watch?v=abc",  # o que vale é o que vem depois do @
        "http://localhost:8000/video",
        "http://127.0.0.1/video",
        "https://[::1/video",
    ],
)
def test_recusa_o_que_nao_e_link_do_youtube(typed):
    with pytest.raises(ValueError):
        validate_url(typed)


def test_mensagem_de_site_nao_aceito_explica_o_que_fazer():
    with pytest.raises(ValueError, match="Adicionar arquivos"):
        validate_url("https://vimeo.com/1")


# ---- textos de progresso ----


def test_format_size():
    assert format_size(246 * 1024) == "246 KB"
    assert format_size(12.3 * 1024**2) == "12,3 MB"
    assert format_size(1.5 * 1024**3) == "1,5 GB"


def test_describe_download():
    assert describe_download(DownloadProgress(50 * 1024**2, 100 * 1024**2, 90)) == (
        "50% • 50,0 MB de 100,0 MB • faltam ~1 min 30 s"
    )
    assert describe_download(DownloadProgress(3 * 1024**2, None, None)) == "Baixando… 3,0 MB"
    sem_eta = describe_download(DownloadProgress(10, 100, None))
    assert "faltam" not in sem_eta


def test_fracao_do_download():
    assert DownloadProgress(0, None, None).fraction == 0.0
    assert DownloadProgress(50, 100, None).fraction == 0.5
    assert DownloadProgress(150, 100, None).fraction == 1.0


# ---- download com um yt-dlp falso (nada de rede nos testes) ----


def fake_ydl(info, *, chunks=(), extract_error=None, process_error=None, filepath="aula.m4a"):
    """Monta uma classe que imita o YoutubeDL e guarda o que o app pediu a ele."""
    seen: dict = {}

    class FakeYDL:
        def __init__(self, options):
            seen["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def extract_info(self, url, download):
            seen["url"] = url
            seen["extract_download"] = download
            if extract_error:
                raise extract_error
            return info

        def process_ie_result(self, info, download):
            seen["process_download"] = download
            for data in chunks:
                for hook in seen["options"]["progress_hooks"]:
                    hook(data)
            if process_error:
                raise process_error
            return {"requested_downloads": [{"filepath": filepath}]}

    return FakeYDL, seen


VIDEO = {"title": "Aula 1", "id": "abc"}


def test_baixa_o_audio_e_devolve_caminho_e_titulo(tmp_path):
    ydl, seen = fake_ydl(VIDEO, filepath=str(tmp_path / "Aula 1 [abc].m4a"))
    pasta = tmp_path / "nova" / "pasta"

    audio = download_audio("youtu.be/abc", pasta, ydl_class=ydl)

    assert audio.path == tmp_path / "Aula 1 [abc].m4a"
    assert audio.title == "Aula 1"
    assert pasta.is_dir()  # a pasta de destino é criada
    assert seen["url"] == "https://youtu.be/abc"
    assert seen["extract_download"] is False  # primeiro só olha o vídeo
    assert seen["process_download"] is True


def test_pede_so_o_audio_de_um_video_dentro_de_limites(tmp_path):
    ydl, seen = fake_ydl(VIDEO)

    download_audio("https://youtu.be/abc", tmp_path, ydl_class=ydl)

    options = seen["options"]
    assert options["format"].startswith("bestaudio")
    assert options["noplaylist"] is True
    assert options["paths"] == {"home": str(tmp_path)}
    assert options["max_filesize"] == download.MAX_BYTES
    assert options["cachedir"] is False
    assert "[%(id)s]" in options["outtmpl"]


def test_repassa_o_progresso(tmp_path):
    chunks = [
        {"status": "downloading", "downloaded_bytes": 10, "total_bytes": 100, "eta": 4},
        {"status": "downloading", "downloaded_bytes": 60, "total_bytes_estimate": 120},
        {"status": "finished", "downloaded_bytes": 120},  # não é "baixando": ignorado
    ]
    ydl, _ = fake_ydl(VIDEO, chunks=chunks)
    received = []

    download_audio("youtu.be/abc", tmp_path, on_progress=received.append, ydl_class=ydl)

    assert received == [DownloadProgress(10, 100, 4), DownloadProgress(60, 120, None)]


def test_recusa_lista_de_reproducao(tmp_path):
    ydl, seen = fake_ydl({"_type": "playlist", "entries": [VIDEO]})
    with pytest.raises(DownloadError, match="lista de reprodução"):
        download_audio("https://www.youtube.com/watch?v=abc&list=x", tmp_path, ydl_class=ydl)
    assert "process_download" not in seen  # nada foi baixado


@pytest.mark.parametrize("info", [{"is_live": True}, {"live_status": "is_upcoming"}])
def test_recusa_transmissao_ao_vivo(tmp_path, info):
    ydl, seen = fake_ydl({"title": "Live", **info})
    with pytest.raises(DownloadError, match="ao vivo"):
        download_audio("youtu.be/abc", tmp_path, ydl_class=ydl)
    assert "process_download" not in seen


def test_link_sem_video_vira_erro_claro(tmp_path):
    ydl, _ = fake_ydl(None)
    with pytest.raises(DownloadError, match="nenhum vídeo"):
        download_audio("youtu.be/abc", tmp_path, ydl_class=ydl)


def test_erro_do_yt_dlp_vira_mensagem_limpa_com_dica_de_atualizar(tmp_path):
    erro = RuntimeError("\x1b[0;31mERROR:\x1b[0m [youtube] abc: Video unavailable")
    ydl, _ = fake_ydl(VIDEO, extract_error=erro)

    with pytest.raises(DownloadError) as caught:
        download_audio("youtu.be/abc", tmp_path, ydl_class=ydl)

    message = str(caught.value)
    assert "[youtube] abc: Video unavailable" in message
    assert "\x1b" not in message and "ERROR:" not in message
    assert "atualize o yt-dlp" in message


def test_link_invalido_nem_chama_o_yt_dlp(tmp_path):
    ydl, seen = fake_ydl(VIDEO)
    with pytest.raises(ValueError):
        download_audio("https://vimeo.com/1", tmp_path, ydl_class=ydl)
    assert "options" not in seen


def test_cancelar_pelo_progresso_levanta_cancelado(tmp_path):
    cancel = threading.Event()
    chunks = [{"status": "downloading", "downloaded_bytes": 1, "total_bytes": 10}]
    ydl, _ = fake_ydl(VIDEO, chunks=chunks)
    cancel.set()

    with pytest.raises(DownloadCancelled):
        download_audio("youtu.be/abc", tmp_path, cancel=cancel, ydl_class=ydl)


def test_cancelar_vale_mesmo_se_o_yt_dlp_embrulhar_a_excecao(tmp_path):
    # O yt-dlp pode trocar a exceção do gancho por um erro dele; se o usuário cancelou,
    # continua sendo um cancelamento, e não "erro de download".
    cancel = threading.Event()
    cancel.set()
    ydl, _ = fake_ydl(VIDEO, process_error=RuntimeError("download interrompido"))

    with pytest.raises(DownloadCancelled):
        download_audio("youtu.be/abc", tmp_path, cancel=cancel, ydl_class=ydl)


def test_pasta_padrao_fica_nos_documentos_do_usuario():
    pasta = download.default_download_dir()
    assert pasta.parent == Path.home() / "Documents"
