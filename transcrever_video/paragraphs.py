"""Agrupa os trechos transcritos em parágrafos (pausas, pontuação e tamanho).

O Whisper devolve trechos curtos, um por "respiro". Para o texto ficar legível,
juntamos trechos seguidos num parágrafo e só abrimos um novo em momentos que
fazem sentido: depois de uma frase terminada e, de preferência, numa pausa.
"""

SENTENCE_ENDINGS = (".", "?", "!", "…")
CLOSERS = "\"'”’)»]"  # aspas e parênteses que podem vir depois do ponto final

PAUSE = 0.45  # segundos de silêncio entre trechos que contam como "pausa de parágrafo"
MIN_CHARS = 220  # antes disso um parágrafo não fecha só por causa de uma pausa
MAX_CHARS = 700  # depois disso fecha na próxima frase terminada, mesmo sem pausa


def ends_sentence(text: str) -> bool:
    return text.rstrip().rstrip(CLOSERS).endswith(SENTENCE_ENDINGS)


class ParagraphBuilder:
    """Decide, trecho a trecho, se o próximo texto continua o parágrafo ou abre outro.

    Funciona em fluxo (não precisa ver o áudio inteiro), então serve para mostrar o
    texto na tela enquanto ele ainda está sendo transcrito.
    """

    def __init__(
        self, *, pause: float = PAUSE, min_chars: int = MIN_CHARS, max_chars: int = MAX_CHARS
    ):
        self._pause = pause
        self._min_chars = min_chars
        self._max_chars = max_chars
        self._chars = 0  # tamanho do parágrafo atual
        self._last_end = 0.0
        self._last_text = ""
        self._started = False

    def feed(self, text: str, start: float, end: float) -> str:
        """Devolve o que vai antes de `text`.

        '' para o primeiro trecho, ' ' para continuar o parágrafo ou '\\n\\n' para abrir outro.
        """
        if not self._started:
            separator = ""
        elif self._opens_paragraph(start):
            separator = "\n\n"
            self._chars = 0
        else:
            separator = " "
        self._started = True
        self._chars += len(text) + len(separator)
        self._last_end = end
        self._last_text = text
        return separator

    def _opens_paragraph(self, start: float) -> bool:
        # Sem pontuação nenhuma o texto nunca "termina frase"; este é o limite de segurança.
        if self._chars >= 2 * self._max_chars:
            return True
        if not ends_sentence(self._last_text):
            return False  # nunca corta no meio de uma frase
        paused = start - self._last_end >= self._pause
        return (paused and self._chars >= self._min_chars) or self._chars >= self._max_chars
