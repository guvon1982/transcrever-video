"""Baixa o áudio de um link do YouTube para uma pasta, usando o yt-dlp.

Só o áudio é baixado: é bem menor que o vídeo e basta para transcrever.
"""

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .progress import format_duration

# Só estes sites: o app não vira um "baixador de qualquer endereço". O Vimeo não entra porque
# o yt-dlp exige login nele (testado: vimeo.com/<id> público falha com "only works when
# logged-in"), e o app não lida com senha nem cookie do usuário.
ALLOWED_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
MAX_BYTES = 2 * 1024**3  # trava de segurança: áudio de aula nunca chega perto disso
# m4a primeiro: o app mostra o ícone de áudio (♪) para ele; o resto é plano B.
AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio/best"
OUTPUT_TEMPLATE = "%(title).80B [%(id)s].%(ext)s"  # o id evita misturar vídeos de mesmo título
ANSI_CODES = re.compile(r"\x1b\[[0-9;]*m")


class DownloadError(Exception):
    """Falha ao baixar; a mensagem já vem pronta para mostrar ao usuário."""


class DownloadCancelled(Exception):
    """O usuário cancelou o download."""


@dataclass(frozen=True)
class DownloadProgress:
    downloaded: int  # bytes já baixados
    total: int | None  # bytes totais (ou estimativa); None = desconhecido
    eta: float | None  # segundos restantes segundo o yt-dlp

    @property
    def fraction(self) -> float:
        if not self.total:
            return 0.0
        return min(max(self.downloaded / self.total, 0.0), 1.0)


@dataclass(frozen=True)
class DownloadedAudio:
    path: Path
    title: str


def default_download_dir() -> Path:
    return Path.home() / "Documents" / "Transcrever Vídeo"


def validate_url(text: str) -> str:
    """Confere se o texto colado é um link de vídeo aceito. Levanta ValueError em português."""
    url = text.strip()
    if not url:
        raise ValueError("Cole o link do vídeo no campo.")
    if any(char.isspace() for char in url):
        raise ValueError("O link não pode ter espaços. Cole só o endereço do vídeo.")
    if "://" not in url:
        url = "https://" + url  # quem cola "youtu.be/abc" sem o https:// também é atendido
    try:
        parts = urlparse(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise ValueError("Esse link não parece um endereço válido.") from None
    if parts.scheme not in ("http", "https"):
        raise ValueError("O link precisa começar com https://")
    if host not in ALLOWED_HOSTS:
        raise ValueError(
            "Por enquanto só links do YouTube (youtube.com ou youtu.be) são aceitos.\n"
            "Para outros sites, baixe o vídeo e use “Adicionar arquivos”."
        )
    return url


def format_size(size: float) -> str:
    """Bytes em texto curto, com vírgula decimal: '246 KB', '12,3 MB'."""
    if size >= 1024**3:
        return f"{size / 1024**3:.1f} GB".replace(".", ",")
    if size >= 1024**2:
        return f"{size / 1024**2:.1f} MB".replace(".", ",")
    return f"{size / 1024:.0f} KB"


def describe_download(progress: DownloadProgress) -> str:
    """Texto de uma linha para a janela: porcentagem, tamanho e tempo restante."""
    if not progress.total:
        return f"Baixando… {format_size(progress.downloaded)}"
    text = (
        f"{progress.fraction * 100:.0f}% • {format_size(progress.downloaded)} "
        f"de {format_size(progress.total)}"
    )
    if progress.eta is not None and progress.eta > 0:
        text += f" • faltam ~{format_duration(progress.eta)}"
    return text


class _SilentLogger:
    """O yt-dlp escreve erros no terminal por conta própria; aqui eles viram mensagem na janela."""

    def debug(self, message: str) -> None: ...

    def warning(self, message: str) -> None: ...

    def error(self, message: str) -> None: ...


def _clean_message(error: Exception) -> str:
    message = ANSI_CODES.sub("", str(error)).strip()
    return message.removeprefix("ERROR:").strip() or error.__class__.__name__


def download_audio(
    url: str,
    folder: Path,
    *,
    on_progress: Callable[[DownloadProgress], None] | None = None,
    cancel: threading.Event | None = None,
    ydl_class=None,
) -> DownloadedAudio:
    """Baixa o áudio de `url` para `folder` e devolve o caminho do arquivo.

    `ydl_class` existe só para os testes trocarem o yt-dlp por um falso.
    """
    url = validate_url(url)
    if ydl_class is None:
        from yt_dlp import YoutubeDL  # import tardio: a janela abre mais rápido

        ydl_class = YoutubeDL

    def hook(data: dict) -> None:
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled
        if data.get("status") == "downloading" and on_progress is not None:
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            on_progress(DownloadProgress(data.get("downloaded_bytes") or 0, total, data.get("eta")))

    options = {
        "format": AUDIO_FORMAT,
        "noplaylist": True,  # link de vídeo dentro de uma lista baixa só o vídeo
        "paths": {"home": str(folder)},
        "outtmpl": OUTPUT_TEMPLATE,
        "windowsfilenames": True,
        "cachedir": False,  # não deixa arquivos de cache do yt-dlp espalhados pelo PC
        "logger": _SilentLogger(),
        "noprogress": True,
        "no_color": True,
        "socket_timeout": 30,
        "retries": 3,
        "max_filesize": MAX_BYTES,
        "progress_hooks": [hook],
    }
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with ydl_class(options) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise DownloadError("O link não devolveu nenhum vídeo.")
            if info.get("_type") == "playlist" or info.get("entries") is not None:
                raise DownloadError(
                    "Esse link é de uma lista de reprodução ou canal. Cole o link de um vídeo só."
                )
            if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
                raise DownloadError(
                    "Esse vídeo é uma transmissão ao vivo. Espere ela terminar para baixar o áudio."
                )
            done = ydl.process_ie_result(info, download=True)
            path = Path(done["requested_downloads"][0]["filepath"])
    except (DownloadCancelled, DownloadError):
        raise
    except Exception as error:
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled from error
        raise DownloadError(
            f"Não foi possível baixar o áudio deste link.\n\n{_clean_message(error)}\n\n"
            "Se o erro persistir, o YouTube pode ter mudado: atualize o yt-dlp "
            "(veja o README) e tente de novo."
        ) from error
    return DownloadedAudio(path, str(info.get("title") or path.stem))
