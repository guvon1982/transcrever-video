"""Quem falou o quê: junta o texto (palavra por palavra) com os trechos de cada falante.

Lógica pura, sem modelo nem janela: o diarizador (quem fala quando) entra como dados.
Cada palavra vai para o falante que mais ocupa o tempo dela; palavras seguidas do
mesmo falante formam um turno.
"""

from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .subtitles import srt_block


@dataclass(frozen=True)
class SpeakerSpan:
    """Um trecho em que o diarizador ouviu um falante (0, 1, 2…)."""

    start: float
    end: float
    speaker: int


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Turn:
    """Fala contínua de um falante."""

    speaker: int
    start: float
    end: float
    text: str


def speaker_name(names: Mapping[int, str], speaker: int) -> str:
    """Nome dado pelo usuário, ou 'Falante N' (contagem a partir de 1)."""
    return names.get(speaker, "").strip() or f"Falante {speaker + 1}"


class _SpanIndex:
    """Busca rápida do falante de uma palavra, mesmo com 20 mil palavras e mil trechos."""

    def __init__(self, spans: Sequence[SpeakerSpan]):
        self.spans = sorted(spans, key=lambda s: (s.start, s.end))
        self.starts = [s.start for s in self.spans]
        # prefixo: posição do trecho que termina mais tarde entre os que começam antes
        self.latest_end: list[int] = []
        best = -1
        for i, span in enumerate(self.spans):
            if best < 0 or span.end > self.spans[best].end:
                best = i
            self.latest_end.append(best)

    def speaker_of(self, word: Word) -> int | None:
        """Falante com mais tempo dentro da palavra; se nenhum, o trecho mais próximo."""
        if not self.spans:
            return None
        hi = bisect_left(self.starts, word.end)  # trechos [0, hi) começam antes da palavra acabar
        best: int | None = None
        best_overlap = 0.0
        i = hi - 1
        while i >= 0:
            if self.spans[self.latest_end[i]].end <= word.start:
                break  # nenhum trecho daqui para trás alcança a palavra
            span = self.spans[i]
            overlap = min(word.end, span.end) - max(word.start, span.start)
            if overlap > best_overlap:
                best, best_overlap = span.speaker, overlap
            i -= 1
        if best is not None:
            return best
        # sem sobreposição: o trecho mais próximo, antes ou depois
        mid = (word.start + word.end) / 2
        candidates: list[SpeakerSpan] = []
        if hi > 0:
            candidates.append(self.spans[self.latest_end[hi - 1]])
        if hi < len(self.spans):
            candidates.append(self.spans[hi])
        return min(candidates, key=lambda s: min(abs(mid - s.start), abs(mid - s.end))).speaker


def build_turns(words: Sequence[Word], spans: Sequence[SpeakerSpan]) -> list[Turn]:
    """Agrupa as palavras em turnos. Sem nenhum trecho de falante, tudo vira do falante 0."""
    index = _SpanIndex(spans)
    turns: list[Turn] = []
    speaker = 0
    start = end = 0.0
    pieces: list[str] = []
    for word in words:
        text = word.text.strip()
        if not text:
            continue
        who = index.speaker_of(word)
        who = 0 if who is None else who
        if pieces and who != speaker:
            turns.append(Turn(speaker, start, end, " ".join(pieces)))
            pieces = []
        if not pieces:
            speaker, start = who, word.start
        end = word.end
        pieces.append(text)
    if pieces:
        turns.append(Turn(speaker, start, end, " ".join(pieces)))
    return turns


def reassign_turn(turns: Sequence[Turn], index: int, speaker: int) -> list[Turn]:
    """Troca o falante de um turno e junta com os vizinhos que passaram a ser do mesmo falante."""
    changed = [*turns]
    changed[index] = Turn(speaker, turns[index].start, turns[index].end, turns[index].text)
    merged: list[Turn] = []
    for turn in changed:
        if merged and merged[-1].speaker == turn.speaker:
            last = merged[-1]
            merged[-1] = Turn(last.speaker, last.start, turn.end, f"{last.text} {turn.text}")
        else:
            merged.append(turn)
    return merged


def render_txt(turns: Sequence[Turn], names: Mapping[int, str] | None = None) -> str:
    """'Nome: fala' por turno, com uma linha em branco entre os turnos."""
    names = names or {}
    return "\n\n".join(f"{speaker_name(names, t.speaker)}: {t.text}" for t in turns) + (
        "\n" if turns else ""
    )


def render_srt(turns: Sequence[Turn], names: Mapping[int, str] | None = None) -> str:
    """Um bloco de legenda por turno, com o nome do falante no começo do texto."""
    names = names or {}
    return "".join(
        srt_block(number, t.start, t.end, f"{speaker_name(names, t.speaker)}: {t.text}")
        for number, t in enumerate(turns, 1)
    )
