import os
import threading
import time
import tkinter as tk
from pathlib import Path

import pytest

from transcrever_video import gui
from transcrever_video.cues import Cue, render_srt
from transcrever_video.download import (
    DownloadCancelled,
    DownloadedAudio,
    DownloadError,
    DownloadProgress,
)
from transcrever_video.engine import LoadedModel, TranscriptionResult
from transcrever_video.gui import App
from transcrever_video.progress import Progress


@pytest.fixture(scope="session")
def tk_root():
    """Uma única janela Tk para a execução inteira dos testes.

    Criar um Tk() por teste falhava ao acaso no CI do Windows ("Can't find a usable init.tcl",
    ~6% por criação, em Python 3.12 e 3.13). Com uma só criação, e algumas tentativas, o
    problema some.
    """
    root = None
    error: tk.TclError | None = None
    for _attempt in range(3):
        try:
            root = tk.Tk()
            break
        except tk.TclError as caught:
            error = caught
            time.sleep(1)
    if root is None:
        if os.environ.get("REQUIRE_GUI"):
            raise error  # no CI, não abrir a janela é falha, e não um teste "pulado"
        pytest.skip(f"sem ambiente gráfico: {error}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def app(tk_root):
    window = App(tk_root)
    yield window
    # Solta a variável do Tk ainda na thread principal: se ela fosse descartada por outra
    # thread (coleta de lixo), o Tk reclama "main thread is not in main loop".
    window.view_var = None
    window.destroy()  # cancela agendamentos e atalhos, para um teste não vazar para o seguinte


def wait_for_worker(app: App) -> None:
    """Espera a thread de trabalho acabar. Folga grande: sob CPU saturada 5 s já não bastou."""
    app.worker.join(timeout=60)
    assert not app.worker.is_alive(), "a thread de trabalho não terminou a tempo"


def state(widget) -> str:
    return str(widget["state"])


def content(app: App) -> str:
    return app.text.get("1.0", "end-1c")


def drain(app: App) -> None:
    """Processa as mensagens da thread, como o _poll faz na janela de verdade."""
    while not app.messages.empty():
        kind, payload = app.messages.get()
        app._handle(kind, payload)


def run_file(app: App, tmp_path: Path, name: str, lines: list[str]) -> Path:
    """Simula, sem thread, o que a thread de trabalho manda para a janela."""
    media = tmp_path / name
    media.write_bytes(b"x")
    app.add_files([media])
    txt, srt = tmp_path / f"{media.stem}.txt", tmp_path / f"{media.stem}.srt"
    cues = tuple(Cue(i * 5.0, i * 5.0 + 5.0, line) for i, line in enumerate(lines))
    txt.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    srt.write_text(render_srt(cues), encoding="utf-8")
    app._handle("file_start", (1, 1, media))
    for number, line in enumerate(lines):
        app._handle("text", (media, line if number == 0 else "\n" + line))
    result = TranscriptionResult(txt, srt, len(lines), False, 1.0, cues)
    app._handle("file_done", (media, result))
    return media


def srt_texts(tmp_path: Path, stem: str) -> list[str]:
    """Só as linhas de texto do .srt (sem número nem horário)."""
    blocks = (tmp_path / f"{stem}.srt").read_text(encoding="utf-8").strip().split("\n\n")
    return [block.split("\n", 2)[2] for block in blocks if block]


def select(app: App, index: int) -> None:
    app.listbox.selection_clear(0, "end")
    app.listbox.selection_set(index)
    app._on_select()


def edit(app: App, extra: str) -> None:
    app.text.insert("end", extra)
    app.update()  # processa o aviso <<Modified>>, que o Tk entrega na fila de eventos


def test_destruir_o_app_nao_deixa_agendamentos_nem_atalhos_na_raiz(tk_root):
    # Regressão: cancelar um agendamento da raiz pelo objeto errado fazia o Tkinter estourar
    # "can't delete Tcl command" ao destruir a janela raiz.
    def pendentes():
        return set(tk_root.tk.splitlist(tk_root.tk.call("after", "info")))

    antes = pendentes()
    window = App(tk_root)
    window._flash("aviso")  # agenda o aviso curto
    window._finish()  # agenda a atualização de botões de 50 ms
    assert pendentes() != antes  # o app agendou coisas na raiz
    assert tk_root.bind("<Control-s>") != ""

    window.destroy()

    assert pendentes() == antes
    assert tk_root.bind("<Control-s>") == ""
    # Todo comando que a raiz acha que tem precisa existir de fato no Tcl; senão destruir a raiz
    # estoura. (_tclCommands é interno do tkinter, mas é exatamente o que quebrava.)
    fantasmas = [
        name for name in tk_root._tclCommands or () if not tk_root.tk.call("info", "commands", name)
    ]
    assert fantasmas == []


# ---- tela e lista ----


def test_tela_inicial_so_deixa_adicionar_arquivos(app):
    assert state(app.start_button) == "disabled"
    assert state(app.cancel_button) == "disabled"
    assert state(app.open_button) == "disabled"
    assert state(app.copy_button) == "disabled"
    assert state(app.save_button) == "disabled"
    assert state(app.text) == "disabled"
    assert state(app.add_button) == "normal"
    assert app.language.get() == "Português"


def test_adicionar_video_e_audio_habilita_transcrever(app):
    app.add_files([Path("C:/aulas/aula1.mp4"), Path("C:/aulas/podcast.mp3")])
    assert app.listbox.get(0, "end") == ("aula1.mp4", "podcast.mp3")
    assert state(app.start_button) == "normal"


def test_nao_duplica_e_limpa_a_lista(app):
    app.add_files([Path("a.mp4"), Path("a.mp4")])
    assert app.listbox.size() == 1
    app.clear_files()
    assert app.listbox.size() == 0
    assert state(app.start_button) == "disabled"


# ---- progresso ----


def test_barra_e_texto_acompanham_o_progresso(app):
    app.add_files([Path("aula1.mp4")])
    app._handle("file_start", (1, 2, Path("aula1.mp4")))
    assert "Arquivo 1 de 2: aula1.mp4" in app.current_label["text"]
    app._handle("progress", Progress(50, 100, 10, 10))
    assert float(app.progress_bar["value"]) == 50.0
    assert "50%" in app.progress_label["text"]
    assert "faltam" in app.progress_label["text"]


def test_mostra_calculando_enquanto_nao_ha_estimativa(app):
    app._handle("progress", Progress(1, 100, 2, None))
    assert "calculando" in app.progress_label["text"]


# ---- texto na janela ----


def test_texto_aparece_ao_vivo_e_so_depois_fica_editavel(app, tmp_path):
    media = tmp_path / "aula.mp4"
    media.write_bytes(b"x")
    app.add_files([media])
    app._handle("file_start", (1, 1, media))
    app._handle("text", (media, "Bom dia."))
    app._handle("text", (media, " Vamos começar."))
    app._handle("text", (media, "\n\nNovo parágrafo."))

    assert content(app) == "Bom dia. Vamos começar.\n\nNovo parágrafo."
    assert state(app.text) == "disabled"  # enquanto transcreve, ninguém edita
    assert app.listbox.get(0) == "… aula.mp4"

    result = TranscriptionResult(tmp_path / "aula.txt", tmp_path / "aula.srt", 2, False, 1.0)
    app._handle("file_done", (media, result))
    assert state(app.text) == "normal"
    assert app.listbox.get(0) == "✓ aula.mp4"
    assert state(app.copy_button) == "normal"


def test_editar_marca_como_alterado_e_salvar_grava_no_txt(app, tmp_path):
    media = run_file(app, tmp_path, "aula.mp4", ["Bom dia.", "Vamos começar."])
    assert state(app.save_button) == "disabled"

    edit(app, "\nTexto novo.")

    assert media in app.dirty
    assert state(app.save_button) == "normal"
    assert app.listbox.get(0) == "✓ aula.mp4  (alterado)"

    app.save_current()

    saved = (tmp_path / "aula.txt").read_text(encoding="utf-8")
    assert saved == "Bom dia.\nVamos começar.\nTexto novo.\n"
    assert not app.dirty
    assert state(app.save_button) == "disabled"
    assert "aula.txt e aula.srt atualizados" in app.editor_note["text"]


def test_salvar_atualiza_tambem_a_legenda_srt(app, tmp_path):
    run_file(app, tmp_path, "aula.mp4", ["Bom dia.", "Vamos começar.", "Ruído."])
    app.text.delete("1.0", "1.7")  # troca "Bom dia" (sem o ponto) por outra coisa
    app.text.insert("1.0", "Olá, Guvon")
    app.text.delete("2.end", "end")  # apaga a linha do ruído (a última)
    app.update()

    app.save_current()

    assert srt_texts(tmp_path, "aula") == ["Olá, Guvon.", "Vamos começar."]
    srt = (tmp_path / "aula.srt").read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:05,000" in srt  # horário do primeiro trecho preservado
    assert "00:00:05,000 --> 00:00:10,000" in srt  # o segundo manteve o dele


def test_duas_edicoes_seguidas_partem_da_legenda_ja_salva(app, tmp_path):
    run_file(app, tmp_path, "aula.mp4", ["Um.", "Dois.", "Três."])
    app.text.delete("1.end", "2.end")  # apaga "\nDois."
    app.update()
    app.save_current()
    edit(app, "\nQuatro.")
    app.save_current()
    # palavras novas entram no trecho da palavra anterior, com o horário dele
    assert srt_texts(tmp_path, "aula") == ["Um.", "Três. Quatro."]


def test_apagar_o_texto_vira_alteracao_que_pode_ser_salva(app, tmp_path):
    media = run_file(app, tmp_path, "aula.mp4", ["Bom dia.", "Vamos começar."])
    app.text.delete("1.0", "end")
    app.update()

    assert media in app.dirty
    assert state(app.copy_button) == "disabled"  # nada para copiar
    app.save_current()
    assert (tmp_path / "aula.txt").read_text(encoding="utf-8") == ""


def test_apagar_so_uma_linha(app, tmp_path):
    run_file(app, tmp_path, "aula.mp4", ["Bom dia.", "Ruído.", "Vamos começar."])
    app.text.delete("2.0", "3.0")  # linha 2 inteira
    app.update()
    app.save_current()
    assert (tmp_path / "aula.txt").read_text(encoding="utf-8") == "Bom dia.\nVamos começar.\n"


def test_copiar_tudo_vai_para_a_area_de_transferencia(app, tmp_path):
    run_file(app, tmp_path, "aula.mp4", ["Bom dia.", "Vamos começar."])
    edit(app, "\nExtra.")
    app.copy_all()
    try:
        copied = app.root.clipboard_get()
    except tk.TclError:
        pytest.skip("área de transferência indisponível")
    assert copied == "Bom dia.\nVamos começar.\nExtra."  # copia o que está na tela
    assert "copiado" in app.editor_note["text"]


def test_trocar_de_arquivo_mostra_o_texto_de_cada_um_e_guarda_as_edicoes(app, tmp_path):
    first = run_file(app, tmp_path, "um.mp4", ["Primeiro."])
    second = run_file(app, tmp_path, "dois.mp3", ["Segundo."])
    assert app.displayed == second

    select(app, 0)
    assert content(app) == "Primeiro."
    edit(app, "\nEditado.")

    select(app, 1)
    assert content(app) == "Segundo."
    assert second not in app.dirty

    select(app, 0)
    assert content(app) == "Primeiro.\nEditado."  # a edição de cada arquivo fica guardada
    assert first in app.dirty


def test_editar_um_arquivo_nao_marca_o_outro(app, tmp_path):
    first = run_file(app, tmp_path, "um.mp4", ["Primeiro."])
    second = run_file(app, tmp_path, "dois.mp3", ["Segundo."])
    select(app, 0)
    edit(app, "x")
    assert app.dirty == {first}
    assert second not in app.dirty


def test_desfazer_nao_traz_de_volta_o_texto_do_arquivo_anterior(app, tmp_path):
    run_file(app, tmp_path, "um.mp4", ["Primeiro."])
    run_file(app, tmp_path, "dois.mp3", ["Segundo."])
    select(app, 0)
    select(app, 1)
    assert not app.text.edit("canundo")  # histórico zerado na troca
    assert content(app) == "Segundo."


# ---- abas com ícone (uma por transcrição) ----


def chip_texts(app: App) -> list[str]:
    return [str(button["text"]) for button in app.chip_buttons]


def test_sem_transcricoes_nao_ha_abas(app):
    app.add_files([Path("a.mp4")])
    assert app.chip_buttons == []


def test_cada_transcricao_ganha_uma_aba_com_icone_de_video_ou_audio(app, tmp_path):
    run_file(app, tmp_path, "aula.mp4", ["Um."])
    run_file(app, tmp_path, "zap.ogg", ["Dois."])
    assert chip_texts(app) == ["▶ aula.mp4", "♪ zap.ogg"]


def test_clicar_na_aba_troca_o_texto_e_marca_a_aba_atual(app, tmp_path):
    first = run_file(app, tmp_path, "aula.mp4", ["Primeiro."])
    second = run_file(app, tmp_path, "zap.ogg", ["Segundo."])
    assert app.view_var.get() == str(second)  # a mais recente está na tela

    app.chip_buttons[0].invoke()
    assert content(app) == "Primeiro."
    assert app.view_var.get() == str(first)
    assert app.listbox.curselection() == (0,)  # a lista de cima acompanha

    select(app, 1)  # e o contrário: clicar na lista marca a aba
    assert app.view_var.get() == str(second)


def test_aba_mostra_que_esta_transcrevendo_e_que_ha_alteracao_nao_salva(app, tmp_path):
    media = tmp_path / "aula.mp4"
    media.write_bytes(b"x")
    app.add_files([media])
    app._handle("file_start", (1, 1, media))
    assert chip_texts(app) == ["▶ aula.mp4  …"]

    result = TranscriptionResult(tmp_path / "aula.txt", tmp_path / "aula.srt", 0, False, 1.0)
    app._handle("file_done", (media, result))
    assert chip_texts(app) == ["▶ aula.mp4"]
    edit(app, "x")
    assert chip_texts(app) == ["▶ aula.mp4  ●"]


def test_nome_muito_grande_e_cortado_na_aba(app, tmp_path):
    run_file(app, tmp_path, "a" * 60 + ".mp4", ["Um."])
    text = chip_texts(app)[0]
    assert text.startswith("▶ " + "a" * 27 + "…")
    assert len(text) < 40


def test_limpar_lista_remove_as_abas(app, tmp_path):
    run_file(app, tmp_path, "aula.mp4", ["Um."])
    app.clear_files()
    assert app.chip_buttons == []


# ---- abrir a pasta da transcrição mostrada ----


def test_abrir_pasta_seleciona_o_txt_da_transcricao_mostrada(app, tmp_path, monkeypatch):
    primeira = tmp_path / "um"
    segunda = tmp_path / "dois"
    primeira.mkdir()
    segunda.mkdir()
    run_file(app, primeira, "aula.mp4", ["Um."])
    run_file(app, segunda, "zap.ogg", ["Dois."])
    chamadas = []
    monkeypatch.setattr(gui.subprocess, "Popen", lambda args: chamadas.append(args))

    app.chip_buttons[0].invoke()
    app.open_folder()
    app.chip_buttons[1].invoke()
    app.open_folder()

    # cada clique abre a pasta do arquivo que está na tela, não a do último transcrito
    assert chamadas == [
        ["explorer", "/select,", str(primeira / "aula.txt")],
        ["explorer", "/select,", str(segunda / "zap.txt")],
    ]


def test_abrir_pasta_desligado_quando_nao_ha_transcricao_na_tela(app, tmp_path, monkeypatch):
    assert state(app.open_button) == "disabled"
    app.add_files([tmp_path / "pendente.mp4"])
    select(app, 0)
    assert state(app.open_button) == "disabled"
    chamadas = []
    monkeypatch.setattr(gui.subprocess, "Popen", lambda args: chamadas.append(args))
    app.open_folder()
    assert chamadas == []


def test_abrir_pasta_quando_o_txt_foi_apagado_abre_so_a_pasta(app, tmp_path, monkeypatch):
    run_file(app, tmp_path, "aula.mp4", ["Um."])
    (tmp_path / "aula.txt").unlink()
    abertos = []
    monkeypatch.setattr(gui.os, "startfile", abertos.append, raising=False)
    monkeypatch.setattr(gui.subprocess, "Popen", lambda args: pytest.fail("não devia"))
    app.open_folder()
    assert abertos == [tmp_path]


# ---- proteção contra perder alterações ----


def test_limpar_lista_com_alteracoes_pergunta_antes(app, tmp_path, monkeypatch):
    run_file(app, tmp_path, "aula.mp4", ["Bom dia."])
    edit(app, "x")

    perguntas = []
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a: perguntas.append(a) or False)
    app.clear_files()
    assert len(perguntas) == 1
    assert app.listbox.size() == 1  # disse "não": nada mudou

    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a: True)
    app.clear_files()
    assert app.listbox.size() == 0
    assert content(app) == ""
    assert not app.dirty


def test_fechar_com_alteracoes_pergunta_antes(app, tmp_path, monkeypatch):
    run_file(app, tmp_path, "aula.mp4", ["Bom dia."])
    edit(app, "x")
    fechou = []
    monkeypatch.setattr(app.root, "destroy", lambda: fechou.append(True))

    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a: False)
    app.on_close()
    assert not fechou

    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a: True)
    app.on_close()
    assert fechou


def test_fechar_sem_alteracoes_nao_pergunta(app, tmp_path, monkeypatch):
    run_file(app, tmp_path, "aula.mp4", ["Bom dia."])
    fechou = []
    monkeypatch.setattr(app.root, "destroy", lambda: fechou.append(True))
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a: pytest.fail("não devia perguntar"))
    app.on_close()
    assert fechou


def test_salvar_com_erro_avisa_e_mantem_como_alterado(app, tmp_path, monkeypatch):
    media = run_file(app, tmp_path, "aula.mp4", ["Bom dia."])
    edit(app, "x")
    erros = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda title, msg: erros.append(msg))

    def falha(result, text):
        raise PermissionError("sem permissão")

    monkeypatch.setattr(gui.engine, "save_edits", falha)
    app.save_current()
    assert len(erros) == 1 and "sem permissão" in erros[0]
    assert media in app.dirty


# ---- fluxo completo com thread e motor falso ----


def fake_engine(monkeypatch, tmp_path, calls=None, fail=()):
    monkeypatch.setattr(gui.engine, "load_model", lambda: LoadedModel(object(), "teste"))

    def fake_transcribe(model, path, *, language, on_progress, on_text, cancel):
        if calls is not None:
            calls.append(path.name)
        if path.name in fail:
            raise ValueError("arquivo corrompido")
        assert language == "pt"
        on_text("Linha um.")
        on_progress(Progress(5, 10, 6, 6))
        on_text(" Linha dois.")
        on_progress(Progress(10, 10, 12, 0.0))
        return TranscriptionResult(
            tmp_path / f"{path.stem}.txt", tmp_path / f"{path.stem}.srt", 2, False, 12
        )

    monkeypatch.setattr(gui.engine, "transcribe_file", fake_transcribe)


def test_fluxo_completo_mostra_o_texto_na_janela(app, monkeypatch, tmp_path):
    media = tmp_path / "aula.mp4"
    media.write_bytes(b"x")
    fake_engine(monkeypatch, tmp_path)

    app.add_files([media])
    app.start()
    wait_for_worker(app)
    drain(app)

    assert float(app.progress_bar["value"]) == 100.0
    assert "Pronto: 1 arquivo" in app.current_label["text"]
    assert app.device_label["text"] == "Rodando em: teste"
    assert content(app) == "Linha um. Linha dois."
    assert state(app.text) == "normal"
    assert state(app.open_button) == "normal"


def test_erro_em_um_arquivo_nao_derruba_a_fila(app, monkeypatch, tmp_path):
    ruim, bom = tmp_path / "ruim.mp4", tmp_path / "bom.mp3"
    for path in (ruim, bom):
        path.write_bytes(b"x")
    erros = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda title, msg: erros.append(msg))
    fake_engine(monkeypatch, tmp_path, fail={"ruim.mp4"})

    app.add_files([ruim, bom])
    app.start()
    wait_for_worker(app)
    drain(app)

    assert len(erros) == 1 and "arquivo corrompido" in erros[0]
    assert "Pronto: 1 arquivo" in app.current_label["text"]
    assert "1 com erro" in app.current_label["text"]
    assert app.listbox.get(1) == "✓ bom.mp3"
    assert app.listbox.get(0) == "ruim.mp4"  # continua pendente: dá para tentar de novo


def test_transcrever_de_novo_so_pega_os_arquivos_pendentes(app, monkeypatch, tmp_path):
    chamados = []
    fake_engine(monkeypatch, tmp_path, calls=chamados)
    primeiro, segundo = tmp_path / "um.mp4", tmp_path / "dois.mp3"
    for path in (primeiro, segundo):
        path.write_bytes(b"x")

    app.add_files([primeiro])
    app.start()
    wait_for_worker(app)
    drain(app)
    edit(app, "minha edição")  # não pode ser perdida por uma nova rodada

    app.add_files([segundo])
    app.start()
    wait_for_worker(app)
    drain(app)

    assert chamados == ["um.mp4", "dois.mp3"]  # o primeiro não foi refeito
    assert primeiro in app.dirty
    select(app, 0)
    assert content(app).endswith("minha edição")


# ---- link do YouTube ----


def fake_downloader(tmp_path: Path, calls: list):
    def download_audio(url, folder, *, on_progress, cancel):
        calls.append((url, folder))
        on_progress(DownloadProgress(50, 100, 5))
        return DownloadedAudio(tmp_path / "Aula [abc].m4a", "Aula")

    return download_audio


def paste_link(app: App, text: str) -> None:
    app.url_entry.delete(0, "end")
    app.url_entry.insert(0, text)
    app.add_link()


def test_link_valido_baixa_o_audio_e_poe_na_fila(app, tmp_path):
    calls: list = []
    app.download_audio = fake_downloader(tmp_path, calls)

    paste_link(app, "youtu.be/abc")
    wait_for_worker(app)
    drain(app)

    assert calls == [("https://youtu.be/abc", app.download_folder)]
    assert app.files == [tmp_path / "Aula [abc].m4a"]
    assert app.listbox.get(0, "end") == ("Aula [abc].m4a",)
    assert "Áudio baixado: Aula" in app.current_label["text"]
    assert app.url_entry.get() == ""  # campo limpo para o próximo link
    assert state(app.start_button) == "normal"  # pronto para transcrever


def test_link_baixado_vai_para_a_mesma_fila_dos_arquivos(app, tmp_path):
    app.download_audio = fake_downloader(tmp_path, [])
    app.add_files([Path("C:/aulas/aula1.mp4")])

    paste_link(app, "youtu.be/abc")
    wait_for_worker(app)
    drain(app)

    assert [path.name for path in app.files] == ["aula1.mp4", "Aula [abc].m4a"]


def test_barra_acompanha_o_download(app):
    app._handle("dl_start", None)
    assert "Baixando" in app.current_label["text"]
    app._handle("dl_progress", DownloadProgress(50, 100, 5))
    assert float(app.progress_bar["value"]) == 50.0
    assert "50%" in app.progress_label["text"]


def test_durante_o_download_os_controles_ficam_travados(app, tmp_path):
    liberar = threading.Event()

    def devagar(url, folder, *, on_progress, cancel):
        liberar.wait(timeout=30)
        return DownloadedAudio(tmp_path / "a.m4a", "A")

    app.download_audio = devagar
    paste_link(app, "youtu.be/abc")
    try:
        assert state(app.link_button) == "disabled"
        assert state(app.url_entry) == "disabled"
        assert state(app.add_button) == "disabled"
        assert state(app.cancel_button) == "normal"  # dá para cancelar o download
    finally:
        liberar.set()
        wait_for_worker(app)
        drain(app)


def test_link_nao_aceito_avisa_e_nao_baixa(app, monkeypatch):
    avisos: list = []
    monkeypatch.setattr(gui.messagebox, "showwarning", lambda *a: avisos.append(a))
    app.download_audio = lambda *a, **k: pytest.fail("não devia baixar")

    paste_link(app, "https://vimeo.com/123")

    assert len(avisos) == 1 and "YouTube" in avisos[0][1]
    assert app.worker is None
    assert app.files == []


def test_erro_de_download_mostra_mensagem_e_libera_a_tela(app, monkeypatch):
    erros: list = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *a: erros.append(a))

    def falha(url, folder, *, on_progress, cancel):
        raise DownloadError("Vídeo indisponível")

    app.download_audio = falha
    paste_link(app, "youtu.be/abc")
    wait_for_worker(app)
    drain(app)

    assert erros and "Vídeo indisponível" in erros[0][1]
    assert app.files == []
    assert state(app.link_button) == "normal"


def test_cancelar_o_download_nao_mostra_erro(app, monkeypatch):
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *a: pytest.fail("não é erro"))

    def cancelado(url, folder, *, on_progress, cancel):
        raise DownloadCancelled

    app.download_audio = cancelado
    paste_link(app, "youtu.be/abc")
    wait_for_worker(app)
    drain(app)

    assert app.current_label["text"] == "Download cancelado."
    assert app.files == []
