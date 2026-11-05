"""Modelos de voz para identificar falantes: baixa uma vez e confere o SHA-256.

Só se usa um arquivo baixado depois de conferir o checksum dele: um download já veio
truncado no experimento e só o SHA-256 pegou. Arquivo errado é apagado, nunca usado.
"""

import hashlib
import os
import tarfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .download import DownloadCancelled, DownloadProgress

ALLOWED_MODEL_HOST = "github.com"  # as duas URLs abaixo; o resto é bloqueado
CHUNK = 256 * 1024


class ModelError(Exception):
    """Falha ao preparar um modelo; a mensagem já vem pronta para mostrar ao usuário."""


@dataclass(frozen=True)
class ModelSpec:
    name: str  # nome curto para mensagens
    url: str
    size: int  # bytes do arquivo baixado (trava contra download sem fim)
    sha256: str  # do arquivo baixado
    filename: str  # nome do arquivo final dentro da pasta de modelos
    archive_member: str | None = None  # se o download é .tar.bz2: arquivo a extrair dele
    final_sha256: str | None = None  # do arquivo extraído (só quando há archive_member)


# Origem: https://github.com/k2-fsa/sherpa-onnx/releases (Apache-2.0). O hash do embedding é
# o publicado pelo projeto (checksum.txt da release). A segmentação não tem hash publicado:
# o dela foi calculado no download que testamos (confiança na primeira vez).
SEGMENTATION = ModelSpec(
    name="segmentação de falantes",
    url="https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/"
    "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
    size=6_958_444,
    sha256="24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488",
    filename="segmentation-pyannote-3-0.onnx",
    archive_member="sherpa-onnx-pyannote-segmentation-3-0/model.onnx",
    final_sha256="220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079",
)
EMBEDDING = ModelSpec(
    name="voz dos falantes",
    url="https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
    "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
    size=28_281_164,
    sha256="aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2",
    filename="embedding-campplus-zh-en.onnx",
)
ALL_MODELS = (SEGMENTATION, EMBEDDING)


def default_models_dir() -> Path:
    """Pasta dos modelos: %LOCALAPPDATA%\\TranscreverVideo\\models (ou ~/.transcrever-video)."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) / "TranscreverVideo" if base else Path.home() / ".transcrever-video"
    return root / "models"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def is_ready(spec: ModelSpec, folder: Path) -> bool:
    """O arquivo final existe e confere com o hash esperado."""
    target = folder / spec.filename
    expected = spec.final_sha256 or spec.sha256
    return target.is_file() and sha256_of(target) == expected


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_MODEL_HOST:
        raise ModelError("Endereço de modelo não permitido.")


def _download(
    spec: ModelSpec,
    part: Path,
    opener: Callable,
    on_progress: Callable[[DownloadProgress], None] | None,
    cancel: threading.Event | None,
) -> None:
    """Baixa para `part` (arquivo temporário) e confere tamanho e SHA-256."""
    _validate_url(spec.url)
    digest = hashlib.sha256()
    received = 0
    try:
        with opener(Request(spec.url, headers={"User-Agent": "transcrever-video"})) as response:
            with part.open("wb") as out:
                while chunk := response.read(CHUNK):
                    if cancel is not None and cancel.is_set():
                        raise DownloadCancelled
                    received += len(chunk)
                    if received > spec.size:
                        raise ModelError(
                            f"O download do modelo de {spec.name} veio maior que o esperado "
                            "e foi descartado."
                        )
                    digest.update(chunk)
                    out.write(chunk)
                    if on_progress:
                        on_progress(DownloadProgress(received, spec.size, None))
    except (OSError, ModelError, DownloadCancelled) as error:
        part.unlink(missing_ok=True)
        if isinstance(error, (ModelError, DownloadCancelled)):
            raise
        raise ModelError(
            f"Não consegui baixar o modelo de {spec.name}. Confira a internet e tente de novo."
        ) from error
    if received != spec.size or digest.hexdigest() != spec.sha256:
        part.unlink(missing_ok=True)
        raise ModelError(
            f"O modelo de {spec.name} baixou incompleto ou corrompido e foi descartado. "
            "Tente de novo."
        )


def _extract(spec: ModelSpec, archive: Path, target: Path) -> None:
    """Tira só o arquivo esperado do .tar.bz2 (sem criar pastas nem seguir caminhos do pacote)."""
    part = target.with_name(target.name + ".part")
    try:
        with tarfile.open(archive, "r:bz2") as tar:
            member = tar.getmember(spec.archive_member or "")
            source = tar.extractfile(member)
            if source is None:
                raise ModelError(f"O pacote do modelo de {spec.name} está inválido.")
            with source, part.open("wb") as out:
                while chunk := source.read(CHUNK):
                    out.write(chunk)
    except (KeyError, tarfile.TarError, OSError) as error:
        part.unlink(missing_ok=True)
        raise ModelError(f"O pacote do modelo de {spec.name} está inválido.") from error
    if sha256_of(part) != spec.final_sha256:
        part.unlink(missing_ok=True)
        raise ModelError(f"O modelo de {spec.name} extraído não confere e foi descartado.")
    part.replace(target)


def ensure_model(
    spec: ModelSpec,
    folder: Path,
    *,
    on_progress: Callable[[DownloadProgress], None] | None = None,
    cancel: threading.Event | None = None,
    opener: Callable | None = None,
) -> Path:
    """Devolve o caminho do modelo pronto, baixando se faltar ou se estiver errado."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / spec.filename
    if is_ready(spec, folder):
        return target
    target.unlink(missing_ok=True)  # existe mas não confere: não usar
    part = folder / (spec.filename + ".download")
    _download(spec, part, opener or urlopen, on_progress, cancel)
    try:
        if spec.archive_member:
            _extract(spec, part, target)
        else:
            part.replace(target)
    finally:
        part.unlink(missing_ok=True)
    return target


def ensure_all(
    folder: Path,
    *,
    on_progress: Callable[[ModelSpec, DownloadProgress], None] | None = None,
    cancel: threading.Event | None = None,
    opener: Callable | None = None,
) -> Mapping[str, Path]:
    """Prepara os dois modelos. Chaves: 'segmentation' e 'embedding'."""
    paths = {}
    for key, spec in (("segmentation", SEGMENTATION), ("embedding", EMBEDDING)):

        def report(progress: DownloadProgress, spec: ModelSpec = spec) -> None:
            if on_progress:
                on_progress(spec, progress)

        paths[key] = ensure_model(spec, folder, on_progress=report, cancel=cancel, opener=opener)
    return paths
