"""Motor: liga o faster-whisper ao progresso e aos arquivos de saída."""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from .cues import Cue, rebuild_cues, render_srt
from .gpu import setup_cuda_dll_paths
from .paragraphs import ParagraphBuilder
from .progress import Progress, ProgressTracker
from .subtitles import srt_block

DEFAULT_MODEL = "large-v3-turbo"


@dataclass(frozen=True)
class LoadedModel:
    model: object
    description: str  # texto para mostrar ao usuário, ex.: "GPU NVIDIA (int8_float16)"


@dataclass(frozen=True)
class TranscriptionResult:
    txt_path: Path
    srt_path: Path
    segments: int
    cancelled: bool
    elapsed: float
    cues: tuple[Cue, ...] = ()  # os trechos da legenda como estão gravados no .srt


def output_paths(media_path: Path, output_dir: Path | None = None) -> tuple[Path, Path]:
    """Caminhos do .txt e do .srt. Nunca sobrescreve: se já existem, numera ('aula (2).txt')."""
    folder = output_dir or media_path.parent
    number = 1
    while True:
        suffix = "" if number == 1 else f" ({number})"
        txt = folder / f"{media_path.stem}{suffix}.txt"
        srt = folder / f"{media_path.stem}{suffix}.srt"
        if not txt.exists() and not srt.exists():
            return txt, srt
        number += 1


def save_text(path: Path, text: str) -> None:
    """Grava o texto editado sem deixar o arquivo pela metade se algo falhar no meio."""
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text if not text or text.endswith("\n") else text + "\n", encoding="utf-8")
    temp.replace(path)


def save_edits(result: TranscriptionResult, text: str) -> TranscriptionResult:
    """Grava o texto editado no .txt e ajusta a legenda (.srt) a ele.

    Devolve o resultado com os trechos de legenda atualizados, para a próxima edição
    partir do que está gravado agora.
    """
    cues = tuple(rebuild_cues(result.cues, text))
    save_text(result.txt_path, text)
    save_text(result.srt_path, render_srt(cues))
    return replace(result, cues=cues)


def _warm_up(model) -> None:
    """Roda 1 s de silêncio para forçar o erro de DLL ausente já no carregamento.

    O ctranslate2 só reclama de uma DLL da NVIDIA faltando na primeira inferência,
    não ao carregar o modelo; por isso o teste prévio.
    """
    import numpy as np

    segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="pt")
    list(segments)


def load_model(model_name: str = DEFAULT_MODEL, device: str = "auto") -> LoadedModel:
    """Carrega o modelo. 'auto' tenta a GPU NVIDIA e cai para o processador se falhar."""
    from faster_whisper import WhisperModel  # import tardio: a janela abre mais rápido

    setup_cuda_dll_paths()
    if device in ("auto", "cuda"):
        try:
            model = WhisperModel(model_name, device="cuda", compute_type="int8_float16")
            _warm_up(model)
            return LoadedModel(model, "GPU NVIDIA (int8_float16)")
        except Exception:
            if device == "cuda":
                raise
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    return LoadedModel(model, "Processador (int8), mais lento que a GPU")


def transcribe_file(
    model,
    media_path: str | Path,
    *,
    language: str | None = "pt",
    output_dir: Path | None = None,
    on_progress: Callable[[Progress], None] | None = None,
    on_text: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> TranscriptionResult:
    """Transcreve um vídeo ou áudio, gravando .txt e .srt segmento a segmento.

    Gravar aos poucos evita perder uma aula longa se o app fechar no meio.
    `on_text` recebe cada pedaço de texto assim que fica pronto, já com o que vem
    antes dele: nada (primeiro), um espaço (mesmo parágrafo) ou uma linha em branco
    (novo parágrafo). Concatenando os pedaços obtém-se o conteúdo do .txt.
    `language=None` pede detecção automática do idioma.
    """
    media = Path(media_path)
    if not media.is_file():
        raise FileNotFoundError(f"Arquivo não encontrado: {media}")

    start = clock()
    # condition_on_previous_text=False: sem isso, em áudios longos o modelo passa a imitar o
    # próprio texto anterior, começa a picotar (uma palavra por trecho), perde maiúsculas e
    # pontuação e chega a engolir frases. Medido num vídeo de 19,7 min: ver CLAUDE.md.
    segments, info = model.transcribe(
        str(media),
        language=language,
        vad_filter=True,
        beam_size=5,
        condition_on_previous_text=False,
    )
    tracker = ProgressTracker(info.duration, clock)
    txt_path, srt_path = output_paths(media, output_dir)
    count = 0
    cancelled = False
    cues: list[Cue] = []
    paragraphs = ParagraphBuilder()

    try:
        with (
            txt_path.open("w", encoding="utf-8") as txt,
            srt_path.open("w", encoding="utf-8") as srt,
        ):
            for segment in segments:
                if cancel is not None and cancel.is_set():
                    cancelled = True
                    break
                count += 1
                text = segment.text.strip()
                piece = paragraphs.feed(text, segment.start, segment.end) + text
                txt.write(piece)
                srt.write(srt_block(count, segment.start, segment.end, text))
                cues.append(Cue(segment.start, segment.end, text))
                txt.flush()
                srt.flush()
                if on_text:
                    on_text(piece)
                if on_progress:
                    on_progress(tracker.update(segment.end))
            if count:
                txt.write("\n")
    finally:
        close = getattr(segments, "close", None)
        if close:
            close()

    elapsed = clock() - start
    if on_progress and not cancelled:
        on_progress(Progress(info.duration, info.duration, elapsed, 0.0))
    return TranscriptionResult(txt_path, srt_path, count, cancelled, elapsed, tuple(cues))
