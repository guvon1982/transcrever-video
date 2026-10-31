"""Progresso e estimativa de tempo restante (lógica pura, sem interface)."""

import time
from collections.abc import Callable
from dataclasses import dataclass

# Antes disso a estimativa oscila demais para valer a pena mostrar.
MIN_FRACTION_FOR_ETA = 0.02
MIN_ELAPSED_FOR_ETA = 5.0


@dataclass(frozen=True)
class Progress:
    processed_seconds: float  # quanto do áudio já foi transcrito
    total_seconds: float  # duração total do áudio
    elapsed: float  # tempo real gasto até agora
    remaining: float | None  # tempo real restante estimado; None = ainda sem base

    @property
    def fraction(self) -> float:
        if self.total_seconds <= 0:
            return 0.0
        return min(max(self.processed_seconds / self.total_seconds, 0.0), 1.0)


class ProgressTracker:
    """Calcula progresso e ETA pela velocidade média desde o início.

    A velocidade média (segundos de áudio por segundo real) é estável numa
    transcrição, então a estimativa não pula a cada segmento.
    """

    def __init__(self, total_seconds: float, clock: Callable[[], float] = time.monotonic):
        self._total = total_seconds
        self._clock = clock
        self._start = clock()

    def update(self, processed_seconds: float) -> Progress:
        elapsed = self._clock() - self._start
        progress = Progress(processed_seconds, self._total, elapsed, None)
        if (
            progress.fraction >= MIN_FRACTION_FOR_ETA
            and elapsed >= MIN_ELAPSED_FOR_ETA
            and processed_seconds > 0
        ):
            speed = processed_seconds / elapsed
            remaining = max(self._total - processed_seconds, 0.0) / speed
            return Progress(processed_seconds, self._total, elapsed, remaining)
        return progress


def describe(progress: Progress) -> str:
    """Texto de uma linha para a janela: porcentagem, tempo restante e decorrido."""
    percent = f"{progress.fraction * 100:.0f}%"
    elapsed = format_duration(progress.elapsed)
    if progress.remaining is None:
        return f"{percent} • calculando o tempo restante… • decorrido {elapsed}"
    if progress.remaining <= 0:
        return f"{percent} • concluído em {elapsed}"
    return f"{percent} • faltam ~{format_duration(progress.remaining)} • decorrido {elapsed}"


def format_duration(seconds: float) -> str:
    """Formata segundos como '45 s', '12 min 05 s' ou '1 h 02 min'."""
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {secs:02d} s"
    return f"{secs} s"
