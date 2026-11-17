import sys
import threading
import wave
from types import SimpleNamespace

import pytest

from transcrever_video import meeting
from transcrever_video.meeting import (
    DiarizationCancelled,
    SherpaDiarizer,
    load_audio,
    save_meeting,
    transcribe_meeting,
)
from transcrever_video.models import ModelError
from transcrever_video.speakers import SpeakerSpan, Turn


def word(text, start, end):
    return SimpleNamespace(word=text, start=start, end=end)


# (início, fim, texto, palavras) de cada trecho do Whisper
SEGMENTS = [
    (
        0.0,
        4.0,
        " Bom dia, pessoal.",
        [word(" Bom", 0.5, 1.0), word(" dia,", 1.1, 1.6), word(" pessoal.", 1.7, 2.4)],
    ),
    (
        5.0,
        9.0,
        " Oi, tudo bem?",
        [word(" Oi,", 5.2, 5.6), word(" tudo", 5.7, 6.1), word(" bem?", 6.2, 6.8)],
    ),
]
SPANS = [SpeakerSpan(0.0, 4.5, 0), SpeakerSpan(4.5, 10.0, 1)]


class FakeModel:
    def __init__(self, segments=SEGMENTS, duration=10.0):
        self._segments = segments
        self._duration = duration
        self.calls = []

    def transcribe(self, path, **kwargs):
        self.calls.append((path, kwargs))
        generate = (
            SimpleNamespace(start=s, end=e, text=t, words=w) for s, e, t, w in self._segments
        )
        return generate, SimpleNamespace(duration=self._duration)


class FakeDiarizer:
    def __init__(self, spans=SPANS, error=None):
        self.spans = spans
        self.error = error
        self.calls = []

    def diarize(self, samples, num_speakers, *, on_progress=None, cancel=None):
        self.calls.append((samples, num_speakers))
        if on_progress:
            on_progress(0.5)
            on_progress(1.0)
        if self.error:
            raise self.error
        return self.spans


@pytest.fixture
def media(tmp_path):
    path = tmp_path / "reuniao.mp4"
    path.write_bytes(b"fake")
    return path


def run(media, model=None, diarizer=None, **kwargs):
    kwargs.setdefault("num_speakers", 2)
    kwargs.setdefault("audio_loader", lambda path: "audio")
    return transcribe_meeting(
        model or FakeModel(), media, diarizer=diarizer or FakeDiarizer(), **kwargs
    )


def test_grava_txt_e_srt_com_o_nome_de_cada_falante(media):
    result = run(media)
    assert result.txt_path.read_text(encoding="utf-8") == (
        "Falante 1: Bom dia, pessoal.\n\nFalante 2: Oi, tudo bem?\n"
    )
    assert result.srt_path.read_text(encoding="utf-8") == (
        "1\n00:00:00,500 --> 00:00:02,400\nFalante 1: Bom dia, pessoal.\n\n"
        "2\n00:00:05,200 --> 00:00:06,800\nFalante 2: Oi, tudo bem?\n\n"
    )
    assert not result.cancelled
    assert result.speakers_found == 2


def test_usa_horario_por_palavra_e_as_opcoes_obrigatorias_do_whisper(media):
    model = FakeModel()
    run(media, model=model, language="pt")
    _, kwargs = model.calls[0]
    assert kwargs["word_timestamps"] is True
    assert kwargs["language"] == "pt"
    assert kwargs["vad_filter"] is True
    assert kwargs["condition_on_previous_text"] is False


def test_repassa_o_numero_de_participantes_e_o_audio_ao_diarizador(media):
    diarizer = FakeDiarizer()
    loaded = []
    run(
        media, diarizer=diarizer, num_speakers=3, audio_loader=lambda p: loaded.append(p) or "audio"
    )
    assert diarizer.calls == [("audio", 3)]
    assert loaded == [media]


def test_com_um_participante_nao_roda_a_identificacao(media):
    diarizer = FakeDiarizer()
    result = run(media, diarizer=diarizer, num_speakers=1)
    assert diarizer.calls == []
    assert result.speakers_found == 1
    assert result.txt_path.read_text(encoding="utf-8").startswith("Falante 1: Bom dia")


def test_progresso_passa_pelas_duas_fases_em_ordem(media):
    events = []
    run(media, on_progress=lambda phase, fraction: events.append((phase, fraction)))
    phases = [phase for phase, _ in events]
    assert phases == sorted(phases, key=["transcribing", "diarizing"].index)
    assert ("transcribing", 0.9) in events
    assert events[-1] == ("diarizing", 1.0)


def test_texto_corrido_aparece_enquanto_o_whisper_trabalha(media):
    pieces = []
    run(media, on_text=pieces.append)
    assert "".join(pieces).replace("\n", " ").split() == "Bom dia, pessoal. Oi, tudo bem?".split()


def test_cancelar_durante_o_whisper_nao_grava_nada(media, tmp_path):
    cancel = threading.Event()
    cancel.set()
    diarizer = FakeDiarizer()
    result = run(media, diarizer=diarizer, cancel=cancel)
    assert result.cancelled
    assert diarizer.calls == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["reuniao.mp4"]


def test_cancelar_durante_a_identificacao_nao_grava_nada(media, tmp_path):
    result = run(media, diarizer=FakeDiarizer(error=DiarizationCancelled()))
    assert result.cancelled
    assert sorted(p.name for p in tmp_path.iterdir()) == ["reuniao.mp4"]


def test_trecho_sem_palavras_vira_uma_palavra_so(media):
    segments = [(0.0, 4.0, " Bom dia, pessoal.", None)]
    result = run(media, model=FakeModel(segments), num_speakers=1)
    assert result.txt_path.read_text(encoding="utf-8") == "Falante 1: Bom dia, pessoal.\n"


def test_nunca_sobrescreve_a_saida(media):
    first = run(media)
    second = run(media)
    assert second.txt_path != first.txt_path
    assert second.txt_path.name == "reuniao (2).txt"


def test_erros_de_uso(media, tmp_path):
    with pytest.raises(FileNotFoundError):
        run(tmp_path / "nao_existe.mp4")
    with pytest.raises(ValueError):
        run(media, num_speakers=0)


def test_renomear_falante_regrava_txt_e_srt(media):
    result = run(media)
    saved = save_meeting(result, result.turns, {0: "Gustavo", 1: "Paula"})
    assert "Gustavo: Bom dia" in result.txt_path.read_text(encoding="utf-8")
    assert "Paula: Oi, tudo bem?" in result.srt_path.read_text(encoding="utf-8")
    assert saved.turns == result.turns


def test_corrigir_um_turno_regrava_os_arquivos(media):
    result = run(media)
    turns = [Turn(0, 0.5, 6.8, "Bom dia, pessoal. Oi, tudo bem?")]
    saved = save_meeting(result, turns, {})
    assert result.txt_path.read_text(encoding="utf-8") == (
        "Falante 1: Bom dia, pessoal. Oi, tudo bem?\n"
    )
    assert saved.speakers_found == 1


# --- SherpaDiarizer, com um sherpa_onnx de mentira (os modelos reais não vão para o CI) ---


class FakeConfigPiece:
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs


class FakeSherpa:
    def __init__(self, validate=True, aborts_at=None):
        self._validate = validate
        self._aborts_at = aborts_at
        self.configs = []
        self.abort_codes = []
        outer = self

        class Config(FakeConfigPiece):
            def validate(self):
                return outer._validate

        class Diarization:
            def __init__(self, config):
                outer.configs.append(config)

            def process(self, samples, callback=None):
                for done in range(1, 5):
                    code = callback(done, 4)
                    outer.abort_codes.append(code)
                    if code:
                        break
                segs = [
                    SimpleNamespace(start=0.0, end=2.0, speaker=0),
                    SimpleNamespace(start=2.0, end=5.0, speaker=1),
                ]
                return SimpleNamespace(sort_by_start_time=lambda: segs)

        self.OfflineSpeakerDiarizationConfig = Config
        self.OfflineSpeakerSegmentationModelConfig = FakeConfigPiece
        self.OfflineSpeakerSegmentationPyannoteModelConfig = FakeConfigPiece
        self.SpeakerEmbeddingExtractorConfig = FakeConfigPiece
        self.FastClusteringConfig = FakeConfigPiece
        self.OfflineSpeakerDiarization = Diarization


@pytest.fixture
def fake_sherpa(monkeypatch):
    fake = FakeSherpa()
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake)
    return fake


def test_sherpa_devolve_os_trechos_e_usa_o_numero_de_falantes(fake_sherpa, tmp_path):
    progress = []
    diarizer = SherpaDiarizer(tmp_path / "seg.onnx", tmp_path / "emb.onnx", threads=2)
    spans = diarizer.diarize([0.0], 3, on_progress=progress.append)
    assert spans == [SpeakerSpan(0.0, 2.0, 0), SpeakerSpan(2.0, 5.0, 1)]
    config = fake_sherpa.configs[0]
    assert config.kwargs["clustering"].kwargs["num_clusters"] == 3
    assert progress == [0.25, 0.5, 0.75, 1.0]


def test_sherpa_aborta_quando_o_usuario_cancela(fake_sherpa, tmp_path):
    cancel = threading.Event()
    cancel.set()
    diarizer = SherpaDiarizer(tmp_path / "seg.onnx", tmp_path / "emb.onnx")
    with pytest.raises(DiarizationCancelled):
        diarizer.diarize([0.0], 2, cancel=cancel)
    assert fake_sherpa.abort_codes == [1]  # o primeiro aviso já mandou parar


def test_sherpa_com_modelos_invalidos_da_erro_claro(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "sherpa_onnx", FakeSherpa(validate=False))
    diarizer = SherpaDiarizer(tmp_path / "seg.onnx", tmp_path / "emb.onnx")
    with pytest.raises(ModelError, match="não puderam ser carregados"):
        diarizer.diarize([0.0], 2)


# --- leitura de áudio (PyAV de verdade, com um .wav pequeno) ---


def write_wav(path, rate, channels, seconds):
    frames = b"\x00\x10" * channels * int(rate * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)


def test_load_audio_entrega_float32_mono_16k(tmp_path):
    path = tmp_path / "a.wav"
    write_wav(path, 44100, 2, 1.0)
    samples = load_audio(path)
    assert samples.dtype.name == "float32"
    assert samples.ndim == 1
    assert abs(len(samples) - meeting.SAMPLE_RATE) < 200


def test_load_audio_de_arquivo_que_nao_e_midia_da_erro_claro(tmp_path):
    path = tmp_path / "lixo.wav"
    path.write_bytes(b"isto nao e audio")
    with pytest.raises(ModelError, match="Não consegui ler"):
        load_audio(path)
