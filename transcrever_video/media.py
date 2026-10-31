"""Formatos de mídia aceitos pelo app (vídeo e áudio)."""

from pathlib import Path

VIDEO_EXTENSIONS = (
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".webm",
    ".flv",
    ".wmv",
    ".m4v",
    ".mpg",
    ".mpeg",
)
AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma")


def is_supported(path: str | Path) -> bool:
    """Diz se a extensão do arquivo é de vídeo ou áudio conhecido."""
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS + AUDIO_EXTENSIONS


def is_audio(path: str | Path) -> bool:
    """Áudio puro (mp3, wav…). Qualquer outra extensão é tratada como vídeo."""
    return Path(path).suffix.lower() in AUDIO_EXTENSIONS


def file_dialog_types() -> list[tuple[str, str]]:
    """Filtros para o seletor de arquivos (tkinter). Sempre deixa a opção 'todos'."""

    def pattern(extensions: tuple[str, ...]) -> str:
        return " ".join(f"*{ext}" for ext in extensions)

    return [
        ("Vídeo e áudio", pattern(VIDEO_EXTENSIONS + AUDIO_EXTENSIONS)),
        ("Vídeo", pattern(VIDEO_EXTENSIONS)),
        ("Áudio", pattern(AUDIO_EXTENSIONS)),
        ("Todos os arquivos", "*.*"),
    ]
