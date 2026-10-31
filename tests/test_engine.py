import threading
from types import SimpleNamespace

import pytest

from transcrever_video.cues import Cue
from transcrever_video.engine import output_paths, save_edits, save_text, transcribe_file


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeModel:
    """Imita o WhisperModel: devolve segmentos prontos e a duração total."""

    def __init__(self, segments, duration, clock=None, step=0.0):
        self._segments = segments
        self._duration = duration
        self._clock = clock
        self._step = step
        self.calls = []

    def transcribe(self, path, **kwargs):
        self.calls.append((path, kwargs))

        def generate():
            for start, end, text in self._segments:
                if self._clock:
                    self._clock.now += self._step  # cada segmento "demora" um pouco
                yield SimpleNamespace(start=start, end=end, text=text)

        return generate(), SimpleNamespace(duration=self._duration)


@pytest.fixture
def media(tmp_path):
    path = tmp_path / "aula.mp4"
    path.write_bytes(b"fake")
    return path


SEGMENTS = [(0.0, 5.0, " Bom dia. "), (5.0, 10.0, "Vamos começar.")]


def test_grava_txt_e_srt(media):
    result = transcribe_file(FakeModel(SEGMENTS, 10.0), media)
    assert result.txt_path.read_text(encoding="utf-8") == "Bom dia. Vamos começar.\n"
    assert result.srt_path.read_text(encoding="utf-8") == (
        "1\n00:00:00,000 --> 00:00:05,000\nBom dia.\n\n"
        "2\n00:00:05,000 --> 00:00:10,000\nVamos começar.\n\n"
    )
    assert result.segments == 2
    assert not result.cancelled


def test_usa_idioma_e_filtro_de_silencio(media):
    model = FakeModel(SEGMENTS, 10.0)
    transcribe_file(model, media, language="pt")
    _, kwargs = model.calls[0]
    assert kwargs["language"] == "pt"
    assert kwargs["vad_filter"] is True
    # Guarda da correção do texto picotado em áudios longos (ver CLAUDE.md). Este teste só
    # trava o parâmetro; a prova de que ele resolve foi medida com o modelo real.
    assert kwargs["condition_on_previous_text"] is False


def test_progresso_cresce_e_termina_em_100(media):
    clock = FakeClock()
    model = FakeModel(SEGMENTS, 10.0, clock, step=3.0)
    events = []
    transcribe_file(model, media, on_progress=events.append, clock=clock)
    fractions = [e.fraction for e in events]
    assert fractions == sorted(fractions)
    assert fractions[-1] == 1.0
    assert events[-1].remaining == 0.0


def test_on_text_recebe_cada_segmento_ja_sem_espacos_nas_pontas(media):
    recebidos = []
    transcribe_file(FakeModel(SEGMENTS, 10.0), media, on_text=recebidos.append)
    assert recebidos == ["Bom dia.", " Vamos começar."]  # o espaço já vem junto


def test_pausas_depois_de_frases_viram_paragrafos_no_txt_e_no_texto_ao_vivo(media):
    frase = "Esta é uma frase de tamanho razoável para o teste de parágrafos."
    # 5 frases coladas (sem pausa); só depois de uma pausa abre-se um novo parágrafo
    segmentos = [(i * 4.0, i * 4.0 + 4.0, frase) for i in range(5)]
    segmentos.append((22.0, 24.0, "Outro assunto."))
    recebidos = []
    result = transcribe_file(FakeModel(segmentos, 24.0), media, on_text=recebidos.append)

    conteudo = result.txt_path.read_text(encoding="utf-8")
    assert conteudo.count("\n\n") == 1
    assert conteudo.endswith("\n\nOutro assunto.\n")
    assert "".join(recebidos) + "\n" == conteudo  # o que aparece na tela é o que vai pro arquivo
    # a legenda continua com um trecho por segmento, sem parágrafos
    assert len(result.cues) == 6
    assert result.srt_path.read_text(encoding="utf-8").count("-->") == 6


def test_save_text_substitui_o_arquivo_e_nao_deixa_temporario(tmp_path):
    alvo = tmp_path / "aula.txt"
    alvo.write_text("antigo\n", encoding="utf-8")
    save_text(alvo, "novo texto")
    assert alvo.read_text(encoding="utf-8") == "novo texto\n"
    assert [p.name for p in tmp_path.iterdir()] == ["aula.txt"]


def test_save_text_vazio_continua_vazio(tmp_path):
    alvo = tmp_path / "aula.txt"
    save_text(alvo, "")
    assert alvo.read_text(encoding="utf-8") == ""


def test_resultado_guarda_os_trechos_da_legenda(media):
    result = transcribe_file(FakeModel(SEGMENTS, 10.0), media)
    assert result.cues == (Cue(0.0, 5.0, "Bom dia."), Cue(5.0, 10.0, "Vamos começar."))


def test_save_edits_atualiza_txt_e_srt_e_devolve_os_novos_trechos(media):
    result = transcribe_file(FakeModel(SEGMENTS, 10.0), media)

    saved = save_edits(result, "Bom dia, Guvon.\nVamos começar.\n")

    assert result.txt_path.read_text(encoding="utf-8") == "Bom dia, Guvon.\nVamos começar.\n"
    assert result.srt_path.read_text(encoding="utf-8") == (
        "1\n00:00:00,000 --> 00:00:05,000\nBom dia, Guvon.\n\n"
        "2\n00:00:05,000 --> 00:00:10,000\nVamos começar.\n\n"
    )
    assert saved.cues[0].text == "Bom dia, Guvon."
    # a próxima edição parte do que está gravado agora
    again = save_edits(saved, "Vamos começar.\n")
    assert again.srt_path.read_text(encoding="utf-8") == (
        "1\n00:00:05,000 --> 00:00:10,000\nVamos começar.\n\n"
    )
    assert [p.name for p in media.parent.iterdir() if p.suffix == ".tmp"] == []


def test_cancelar_mantem_o_que_ja_foi_gravado(media):
    cancel = threading.Event()

    def on_progress(_):
        cancel.set()  # cancela depois do primeiro segmento

    model = FakeModel(SEGMENTS, 10.0)
    result = transcribe_file(model, media, on_progress=on_progress, cancel=cancel)
    assert result.cancelled
    assert result.segments == 1
    assert result.txt_path.read_text(encoding="utf-8") == "Bom dia.\n"


def test_nao_sobrescreve_saida_existente(media):
    (media.parent / "aula.txt").write_text("antigo", encoding="utf-8")
    txt, srt = output_paths(media)
    assert txt.name == "aula (2).txt"
    assert srt.name == "aula (2).srt"
    assert (media.parent / "aula.txt").read_text(encoding="utf-8") == "antigo"


def test_arquivo_inexistente(tmp_path):
    with pytest.raises(FileNotFoundError):
        transcribe_file(FakeModel([], 0.0), tmp_path / "nao_existe.mp4")


def test_aceita_audio_do_mesmo_jeito(tmp_path):
    audio = tmp_path / "podcast.mp3"
    audio.write_bytes(b"fake")
    result = transcribe_file(FakeModel(SEGMENTS, 10.0), audio)
    assert result.txt_path.name == "podcast.txt"
    assert result.srt_path.name == "podcast.srt"
