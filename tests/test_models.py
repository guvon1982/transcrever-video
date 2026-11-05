import hashlib
import io
import re
import tarfile
import threading
from pathlib import Path

import pytest

from transcrever_video import models
from transcrever_video.download import DownloadCancelled
from transcrever_video.models import ModelError, ModelSpec, ensure_all, ensure_model, is_ready

URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/x/modelo.onnx"
DATA = b"conteudo-do-modelo" * 50


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def spec_for(data: bytes = DATA, **changes) -> ModelSpec:
    values = {
        "name": "teste",
        "url": URL,
        "size": len(data),
        "sha256": sha(data),
        "filename": "modelo.onnx",
    }
    return ModelSpec(**{**values, **changes})


class FakeResponse:
    def __init__(self, data: bytes):
        self._io = io.BytesIO(data)

    def read(self, size=-1):
        return self._io.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def fake_opener(data: bytes):
    calls = []

    def opener(request):
        calls.append(request.full_url)
        return FakeResponse(data)

    opener.calls = calls
    return opener


def leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name != "modelo.onnx")


def test_baixa_confere_e_deixa_so_o_arquivo_final(tmp_path):
    opener = fake_opener(DATA)
    path = ensure_model(spec_for(), tmp_path, opener=opener)
    assert path.read_bytes() == DATA
    assert leftovers(tmp_path) == []
    assert opener.calls == [URL]


def test_modelo_ja_pronto_nao_baixa_de_novo(tmp_path):
    ensure_model(spec_for(), tmp_path, opener=fake_opener(DATA))
    opener = fake_opener(b"nao deveria ser chamado")
    ensure_model(spec_for(), tmp_path, opener=opener)
    assert opener.calls == []


def test_arquivo_existente_com_hash_errado_e_baixado_de_novo(tmp_path):
    (tmp_path / "modelo.onnx").write_bytes(b"estragado")
    assert not is_ready(spec_for(), tmp_path)
    path = ensure_model(spec_for(), tmp_path, opener=fake_opener(DATA))
    assert path.read_bytes() == DATA


def test_download_corrompido_do_mesmo_tamanho_e_descartado(tmp_path):
    corrupted = b"x" * len(DATA)
    with pytest.raises(ModelError, match="corrompido"):
        ensure_model(spec_for(), tmp_path, opener=fake_opener(corrupted))
    assert list(tmp_path.iterdir()) == []


def test_download_truncado_e_descartado(tmp_path):
    with pytest.raises(ModelError, match="incompleto"):
        ensure_model(spec_for(), tmp_path, opener=fake_opener(DATA[:-10]))
    assert list(tmp_path.iterdir()) == []


def test_download_maior_que_o_esperado_e_interrompido(tmp_path):
    with pytest.raises(ModelError, match="maior que o esperado"):
        ensure_model(spec_for(), tmp_path, opener=fake_opener(DATA + b"extra"))
    assert list(tmp_path.iterdir()) == []


def test_cancelar_nao_deixa_arquivo_parcial(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "CHUNK", 16)
    cancel = threading.Event()
    seen = []

    def on_progress(progress):
        seen.append(progress.downloaded)
        cancel.set()  # cancela no primeiro pedaço

    with pytest.raises(DownloadCancelled):
        ensure_model(
            spec_for(), tmp_path, on_progress=on_progress, cancel=cancel, opener=fake_opener(DATA)
        )
    assert list(tmp_path.iterdir()) == []
    assert len(seen) == 1


def test_erro_de_rede_vira_mensagem_clara(tmp_path):
    def broken(request):
        raise OSError("sem internet")

    with pytest.raises(ModelError, match="Confira a internet"):
        ensure_model(spec_for(), tmp_path, opener=broken)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/k2-fsa/modelo.onnx",
        "https://github.com.evil.com/modelo.onnx",
        "https://evil.com/github.com/modelo.onnx",
        "https://github.com@evil.com/modelo.onnx",
        "file:///C:/Windows/system.ini",
    ],
)
def test_so_baixa_de_https_do_github(tmp_path, url):
    opener = fake_opener(DATA)
    with pytest.raises(ModelError, match="não permitido"):
        ensure_model(spec_for(url=url), tmp_path, opener=opener)
    assert opener.calls == []


def test_progresso_vai_ate_o_total(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "CHUNK", 100)
    seen = []
    ensure_model(spec_for(), tmp_path, on_progress=seen.append, opener=fake_opener(DATA))
    assert seen[-1].downloaded == len(DATA)
    assert seen[-1].total == len(DATA)
    assert [p.downloaded for p in seen] == sorted(p.downloaded for p in seen)


def make_archive(tmp_path: Path, member: str, content: bytes) -> bytes:
    path = tmp_path / "pacote.tar.bz2"
    with tarfile.open(path, "w:bz2") as tar:
        info = tarfile.TarInfo(member)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return path.read_bytes()


def test_pacote_tem_so_o_arquivo_esperado_extraido(tmp_path):
    inner = b"modelo-de-dentro" * 30
    archive = make_archive(tmp_path, "pasta/model.onnx", inner)
    folder = tmp_path / "modelos"
    spec = spec_for(
        archive,
        archive_member="pasta/model.onnx",
        final_sha256=sha(inner),
    )
    path = ensure_model(spec, folder, opener=fake_opener(archive))
    assert path.read_bytes() == inner
    assert [p.name for p in folder.iterdir()] == ["modelo.onnx"]
    assert is_ready(spec, folder)


def test_pacote_com_arquivo_de_dentro_diferente_e_descartado(tmp_path):
    archive = make_archive(tmp_path, "pasta/model.onnx", b"outro conteudo")
    folder = tmp_path / "modelos"
    spec = spec_for(archive, archive_member="pasta/model.onnx", final_sha256=sha(b"esperado"))
    with pytest.raises(ModelError, match="não confere"):
        ensure_model(spec, folder, opener=fake_opener(archive))
    assert list(folder.iterdir()) == []


def test_pacote_sem_o_arquivo_esperado_da_erro_claro(tmp_path):
    archive = make_archive(tmp_path, "pasta/outro.txt", b"x")
    folder = tmp_path / "modelos"
    spec = spec_for(archive, archive_member="pasta/model.onnx", final_sha256=sha(b"x"))
    with pytest.raises(ModelError, match="inválido"):
        ensure_model(spec, folder, opener=fake_opener(archive))
    assert list(folder.iterdir()) == []


def test_pacote_nunca_escreve_fora_da_pasta_de_modelos(tmp_path):
    # um pacote malicioso com caminho '../': só o membro pedido é lido, e ele vai para o nome fixo
    archive = make_archive(tmp_path, "../fora.onnx", b"conteudo")
    folder = tmp_path / "modelos"
    spec = spec_for(archive, archive_member="pasta/model.onnx", final_sha256=sha(b"conteudo"))
    with pytest.raises(ModelError):
        ensure_model(spec, folder, opener=fake_opener(archive))
    assert not (tmp_path / "fora.onnx").exists()


def test_ensure_all_prepara_os_dois_modelos(tmp_path, monkeypatch):
    seg_data, emb_data = b"segmentacao" * 20, b"embedding" * 20
    seg = spec_for(seg_data, url=URL + "?seg", filename="seg.onnx")
    emb = spec_for(emb_data, url=URL + "?emb", filename="emb.onnx")
    monkeypatch.setattr(models, "SEGMENTATION", seg)
    monkeypatch.setattr(models, "EMBEDDING", emb)
    by_url = {seg.url: seg_data, emb.url: emb_data}
    reported = []

    def opener(request):
        return FakeResponse(by_url[request.full_url])

    paths = ensure_all(
        tmp_path, on_progress=lambda s, p: reported.append(s.filename), opener=opener
    )
    assert paths["segmentation"].read_bytes() == seg_data
    assert paths["embedding"].read_bytes() == emb_data
    assert set(reported) == {"seg.onnx", "emb.onnx"}


def test_pasta_padrao_fica_no_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert models.default_models_dir() == tmp_path / "TranscreverVideo" / "models"


def test_modelos_reais_estao_bem_declarados():
    for spec in models.ALL_MODELS:
        assert spec.url.startswith("https://github.com/k2-fsa/sherpa-onnx/releases/download/")
        assert re.fullmatch(r"[0-9a-f]{64}", spec.sha256)
        assert spec.size > 0
    assert re.fullmatch(r"[0-9a-f]{64}", models.SEGMENTATION.final_sha256)
    assert len({s.filename for s in models.ALL_MODELS}) == 2
