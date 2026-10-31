"""Formatação de legenda SRT."""


def format_timestamp(seconds: float) -> str:
    """Converte segundos em 'HH:MM:SS,mmm', o formato de horário do SRT."""
    total_ms = round(max(seconds, 0.0) * 1000)
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def srt_block(index: int, start: float, end: float, text: str) -> str:
    """Um bloco de legenda: número, intervalo, texto e linha em branco."""
    return f"{index}\n{format_timestamp(start)} --> {format_timestamp(end)}\n{text.strip()}\n\n"
