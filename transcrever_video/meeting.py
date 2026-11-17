"""Transcrição de reunião: Whisper (palavra por palavra) + quem fala quando → .txt e .srt.

Duas etapas, uma depois da outra, para nunca disputar a GPU de 4 GB: o Whisper roda
(na GPU, se houver) e depois a identificação de falantes roda no processador.
Se o usuário cancelar em qualquer etapa, nenhum arquivo é gravado.
"""

import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from .engine import WHISPER_OPTIONS, output_paths, save_text
from .models import ModelError
from .paragraphs import ParagraphBuilder
from .speakers import SpeakerSpan, Turn, Word, build_turns, render_srt, render_txt

SAMPLE_RATE = 16000  # o que o modelo de falantes espera
PHASE_TRANSCRIBING = "transcribing"
PHASE_DIARIZING = "diarizing"


class DiarizationCancelled(Exception):
    """O usuário cancelou durante a identificação de falantes."""


class Diarizer(Protocol):
    def diarize(
        self,
        samples,
        num_speakers: int,
        *,
        on_progress: Callable[[float], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> list[SpeakerSpan]: ...


@dataclass(frozen=True)
class MeetingResult:
    txt_path: Path
    srt_path: Path
    turns: tuple[Turn, ...]
    cancelled: bool
    elapsed: float

    @property
    def speakers_found(self) -> int:
        return len({turn.speaker for turn in self.turns})


class SherpaDiarizer:
    """Identificação de falantes com o sherpa-onnx (segmentação pyannote + voz CAM++)."""

    def __init__(self, segmentation: Path, embedding: Path, threads: int | None = None):
        self._segmentation = segmentation
        self._embedding = embedding
        self._threads = threads or min(4, os.cpu_count() or 1)

    def diarize(
        self,
        samples,
        num_speakers: int,
        *,
        on_progress: Callable[[float], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> list[SpeakerSpan]:
        import sherpa_onnx  # import tardio: só quem usa reunião paga o carregamento

        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(self._segmentation)
                ),
                num_threads=self._threads,
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(self._embedding), num_threads=self._threads
            ),
            # nº de falantes informado pelo usuário: a contagem automática não presta (achou
            # 30 a 50 falantes numa reunião de 3 pessoas)
            clustering=sherpa_onnx.FastClusteringConfig(num_clusters=num_speakers),
        )
        if not config.validate():
            raise ModelError("Os modelos de identificação de falantes não puderam ser carregados.")
        engine = sherpa_onnx.OfflineSpeakerDiarization(config)

        def callback(done: int, total: int) -> int:
            if on_progress and total:
                on_progress(min(done / total, 1.0))
            return 1 if cancel is not None and cancel.is_set() else 0  # diferente de 0 = abortar

        result = engine.process(samples, callback=callback)
        if cancel is not None and cancel.is_set():
            raise DiarizationCancelled
        return [SpeakerSpan(s.start, s.end, s.speaker) for s in result.sort_by_start_time()]


def load_audio(path: Path):
    """Áudio do arquivo como números float32, mono, 16 kHz (o que o modelo de falantes lê)."""
    import av
    import numpy as np

    chunks = []
    try:
        with av.open(str(path)) as container:
            if not container.streams.audio:
                raise ModelError("O arquivo não tem trilha de áudio.")
            resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
            for frame in container.decode(container.streams.audio[0]):
                chunks.extend(out.to_ndarray().reshape(-1) for out in resampler.resample(frame))
            chunks.extend(out.to_ndarray().reshape(-1) for out in resampler.resample(None))
    except av.error.FFmpegError as error:
        raise ModelError("Não consegui ler o áudio deste arquivo.") from error
    return np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(0, dtype=np.float32)


def _words_of(segment) -> list[Word]:
    """Palavras com horário; se o modelo não deu, o trecho inteiro vira uma 'palavra' só."""
    words = getattr(segment, "words", None)
    if words:
        return [Word(w.start, w.end, w.word) for w in words]
    return [Word(segment.start, segment.end, segment.text)]


def save_meeting(
    result: MeetingResult, turns: Sequence[Turn], names: Mapping[int, str]
) -> MeetingResult:
    """Grava .txt e .srt com os turnos e nomes dados (renomear falante, corrigir turno)."""
    save_text(result.txt_path, render_txt(turns, names))
    save_text(result.srt_path, render_srt(turns, names))
    return replace(result, turns=tuple(turns))


def transcribe_meeting(
    model,
    media_path: str | Path,
    *,
    num_speakers: int,
    diarizer: Diarizer,
    language: str | None = "pt",
    output_dir: Path | None = None,
    on_progress: Callable[[str, float], None] | None = None,
    on_text: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
    clock: Callable[[], float] = time.monotonic,
    audio_loader: Callable[[Path], object] = load_audio,
) -> MeetingResult:
    """Transcreve uma reunião e grava .txt e .srt com o nome do falante em cada turno.

    `on_progress(fase, fração)`: fase 'transcribing' ou 'diarizing', fração de 0 a 1.
    `on_text` recebe o texto corrido (sem falantes) enquanto o Whisper trabalha, igual ao
    `transcribe_file`: serve para mostrar algo na tela; o resultado final traz os falantes.
    Com 1 participante não há o que separar: pula a identificação.
    """
    media = Path(media_path)
    if not media.is_file():
        raise FileNotFoundError(f"Arquivo não encontrado: {media}")
    if num_speakers < 1:
        raise ValueError("O número de participantes precisa ser pelo menos 1.")

    start = clock()

    def cancelled_result() -> MeetingResult:
        return MeetingResult(media, media, (), True, clock() - start)  # sem arquivos gravados

    segments, info = model.transcribe(
        str(media), language=language, word_timestamps=True, **WHISPER_OPTIONS
    )
    words: list[Word] = []
    paragraphs = ParagraphBuilder()
    try:
        for segment in segments:
            if cancel is not None and cancel.is_set():
                return cancelled_result()
            words.extend(_words_of(segment))
            text = segment.text.strip()
            piece = paragraphs.feed(text, segment.start, segment.end) + text
            if on_text:
                on_text(piece)
            if on_progress and info.duration > 0:
                on_progress(PHASE_TRANSCRIBING, min(segment.end / info.duration, 1.0))
    finally:
        close = getattr(segments, "close", None)
        if close:
            close()

    spans: list[SpeakerSpan] = []
    if num_speakers > 1 and words:
        if on_progress:
            on_progress(PHASE_DIARIZING, 0.0)
        try:
            spans = diarizer.diarize(
                audio_loader(media),
                num_speakers,
                on_progress=(lambda f: on_progress(PHASE_DIARIZING, f)) if on_progress else None,
                cancel=cancel,
            )
        except DiarizationCancelled:
            return cancelled_result()
    if cancel is not None and cancel.is_set():
        return cancelled_result()

    turns = tuple(build_turns(words, spans))
    txt_path, srt_path = output_paths(media, output_dir)
    save_text(txt_path, render_txt(turns))
    save_text(srt_path, render_srt(turns))
    return MeetingResult(txt_path, srt_path, turns, False, clock() - start)
