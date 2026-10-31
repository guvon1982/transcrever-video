"""Trechos de legenda (cues) e como eles acompanham as edições do texto.

O texto na tela pode estar dividido em parágrafos como você quiser; a legenda
não depende disso. Comparamos o texto novo com o antigo **palavra por palavra**:
cada palavra continua no trecho (e no horário) onde nasceu, palavras trocadas
atualizam o trecho delas e palavras apagadas saem da legenda.
"""

import difflib
from collections.abc import Sequence
from dataclasses import dataclass

from .subtitles import srt_block


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


def render_srt(cues: Sequence[Cue]) -> str:
    return "".join(srt_block(number, c.start, c.end, c.text) for number, c in enumerate(cues, 1))


def rebuild_cues(old: Sequence[Cue], text: str) -> list[Cue]:
    """Monta os trechos da legenda para o texto editado, mantendo os horários antigos.

    - Palavras iguais ficam no trecho em que já estavam.
    - Palavras trocadas ocupam o lugar das antigas (se forem mais ou menos, são
      espalhadas em proporção pelos trechos que elas substituem).
    - Palavras novas, sem par, entram no trecho da palavra que vem antes.
    - Trecho que ficou sem nenhuma palavra some da legenda.
    """
    new_words = text.split()
    old_words: list[str] = []
    owner: list[int] = []  # índice do trecho dono de cada palavra antiga
    for index, cue in enumerate(old):
        for word in cue.text.split():
            old_words.append(word)
            owner.append(index)
    if not old_words:
        return []  # sem horários de referência não há como montar a legenda

    assigned: list[list[str]] = [[] for _ in old]
    matcher = difflib.SequenceMatcher(None, old_words, new_words, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                assigned[owner[i1 + offset]].append(new_words[j1 + offset])
        elif tag == "insert":
            assigned[owner[max(i1 - 1, 0)]].extend(new_words[j1:j2])
        elif tag == "replace":
            count = j2 - j1
            for step in range(count):
                position = i1 + step * (i2 - i1) // count  # espalha as palavras novas
                assigned[owner[position]].append(new_words[j1 + step])
        # "delete": as palavras somem e o trecho some junto se ficar vazio

    pairs = zip(old, assigned, strict=True)
    return [Cue(cue.start, cue.end, " ".join(words)) for cue, words in pairs if words]
