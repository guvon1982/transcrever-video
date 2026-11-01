"""Janela do app (tkinter). A transcrição roda numa thread; a janela só desenha."""

import ctypes
import os
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import download, engine
from .media import file_dialog_types, is_audio
from .progress import Progress, describe

LANGUAGES = {
    "Português": "pt",
    "Inglês": "en",
    "Espanhol": "es",
    "Detectar automaticamente": None,
}
POLL_MS = 100  # de quanto em quanto tempo a janela olha as mensagens da thread
FLASH_MS = 3000  # quanto tempo um aviso curto ("Copiado!") fica na tela
CHIP_NAME_MAX = 28  # nomes maiores são cortados nas abas
ICON_VIDEO = "▶"
ICON_AUDIO = "♪"
EDITOR_HINT = (
    "Edite à vontade (Ctrl+Z desfaz, Ctrl+S salva). Para dividir ou juntar parágrafos, use "
    "Enter e Backspace. Ao salvar, o .txt e a legenda (.srt) são atualizados."
)


class App(ttk.Frame):
    def __init__(self, root: tk.Tk):
        super().__init__(root, padding=16)
        self.root = root
        self.files: list[Path] = []
        self.messages: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.loaded: engine.LoadedModel | None = None
        self.saved = 0
        self.failed = 0
        self.download_audio = download.download_audio  # os testes trocam por um falso
        self.download_folder = download.default_download_dir()
        # Estado do editor: o texto de cada arquivo, onde salvar e o que ficou sem salvar.
        self.buffers: dict[Path, str] = {}
        self.results: dict[Path, engine.TranscriptionResult] = {}
        self.dirty: set[Path] = set()
        self.displayed: Path | None = None  # arquivo cujo texto está na caixa
        self.transcribing: Path | None = None  # arquivo sendo transcrito agora
        self.chip_buttons: list[ttk.Radiobutton] = []  # as "abas", uma por transcrição
        self._chip_signature: list[tuple[str, str]] = []
        self._chip_layout_key: tuple = ()
        self._flash_job: str | None = None
        self._poll_job: str | None = None
        self._finish_job: str | None = None
        self._build()
        self.pack(fill="both", expand=True)
        self._poll_job = self.root.after(POLL_MS, self._poll)

    def destroy(self) -> None:
        """Desfaz o que o App deixou pendurado na janela raiz (agendamentos e o atalho Ctrl+S)."""
        # Cancela pelo mesmo objeto que agendou: o Tkinter guarda os comandos de cada um.
        if self._poll_job is not None:
            self.root.after_cancel(self._poll_job)
        for job in (self._flash_job, self._finish_job):
            if job is not None:
                self.after_cancel(job)
        self._poll_job = self._flash_job = self._finish_job = None
        self.root.unbind("<Control-s>")
        super().destroy()

    # ---- construção da tela ----

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(11, weight=1)  # a caixa de texto ocupa o espaço que sobrar

        ttk.Label(self, text="Arquivos de vídeo ou áudio:").grid(row=0, column=0, sticky="w")
        list_frame = ttk.Frame(self)
        list_frame.grid(row=1, column=0, sticky="nsew", pady=(4, 8))
        list_frame.columnconfigure(0, weight=1)
        # exportselection=False: sem isso, selecionar texto na caixa apaga a seleção da lista
        self.listbox = tk.Listbox(list_frame, height=4, activestyle="none", exportselection=False)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(list_frame, command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        buttons = ttk.Frame(self)
        buttons.grid(row=2, column=0, sticky="w")
        self.add_button = ttk.Button(buttons, text="Adicionar arquivos…", command=self.choose_files)
        self.add_button.grid(row=0, column=0, padx=(0, 8))
        self.clear_button = ttk.Button(buttons, text="Limpar lista", command=self.clear_files)
        self.clear_button.grid(row=0, column=1)

        link = ttk.Frame(buttons)
        link.grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(link, text="Ou cole o link de um vídeo do YouTube:").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        self.url_entry = ttk.Entry(link, width=58)
        self.url_entry.grid(row=1, column=0, padx=(0, 8), pady=(2, 0))
        self.url_entry.bind("<Return>", lambda _event: self.add_link())
        self.link_button = ttk.Button(link, text="Baixar áudio do link", command=self.add_link)
        self.link_button.grid(row=1, column=1, pady=(2, 0))

        options = ttk.Frame(self)
        options.grid(row=3, column=0, sticky="w", pady=10)
        ttk.Label(options, text="Idioma do áudio:").grid(row=0, column=0, padx=(0, 8))
        self.language = ttk.Combobox(options, values=list(LANGUAGES), state="readonly", width=24)
        self.language.current(0)
        self.language.grid(row=0, column=1)

        actions = ttk.Frame(self)
        actions.grid(row=4, column=0, sticky="w")
        self.start_button = ttk.Button(actions, text="Transcrever", command=self.start)
        self.start_button.grid(row=0, column=0, padx=(0, 8))
        self.cancel_button = ttk.Button(actions, text="Cancelar", command=self.cancel)
        self.cancel_button.grid(row=0, column=1)

        self.current_label = ttk.Label(self, text="")
        self.current_label.grid(row=5, column=0, sticky="w", pady=(14, 2))
        self.progress_bar = ttk.Progressbar(self, maximum=100, mode="determinate")
        self.progress_bar.grid(row=6, column=0, sticky="ew")
        self.progress_label = ttk.Label(self, text="")
        self.progress_label.grid(row=7, column=0, sticky="w", pady=(2, 0))
        self.device_label = ttk.Label(self, text="", foreground="gray40")
        self.device_label.grid(row=8, column=0, sticky="w", pady=(2, 0))

        ttk.Label(self, text="Transcrição:").grid(row=9, column=0, sticky="w", pady=(12, 2))
        # "Abas": um botão com ícone por arquivo transcrito. Clicar troca o texto da caixa.
        self.view_var = tk.StringVar()
        self.chips_frame = ttk.Frame(self)
        self.chips_frame.grid(row=10, column=0, sticky="ew")
        self.chips_frame.bind("<Configure>", self._layout_chips)
        self.chips_hint = ttk.Label(
            self.chips_frame, text="Clique para ver a transcrição de:", foreground="gray40"
        )

        editor = ttk.Frame(self)
        editor.grid(row=11, column=0, sticky="nsew")
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(0, weight=1)
        # undo=True dá Ctrl+Z/Ctrl+Y; wrap="word" quebra a linha entre palavras, não no meio delas
        self.text = tk.Text(
            editor, wrap="word", undo=True, height=10, font=("Segoe UI", 11), state="disabled"
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        text_scroll = ttk.Scrollbar(editor, command=self.text.yview)
        text_scroll.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=text_scroll.set)
        self.text.bind("<<Modified>>", self._on_modified)
        self.root.bind("<Control-s>", lambda _event: self.save_current())

        self.editor_note = ttk.Label(self, text=EDITOR_HINT, foreground="gray40", wraplength=760)
        self.editor_note.grid(row=12, column=0, sticky="w", pady=(4, 0))

        footer = ttk.Frame(self)
        footer.grid(row=13, column=0, sticky="w", pady=(10, 0))
        self.copy_button = ttk.Button(footer, text="Copiar tudo", command=self.copy_all)
        self.copy_button.grid(row=0, column=0, padx=(0, 8))
        self.save_button = ttk.Button(footer, text="Salvar alterações", command=self.save_current)
        self.save_button.grid(row=0, column=1, padx=(0, 8))
        self.open_button = ttk.Button(
            footer, text="Abrir pasta desta transcrição", command=self.open_folder
        )
        self.open_button.grid(row=0, column=2)
        self._refresh_buttons()

    # ---- ações do usuário ----

    def choose_files(self) -> None:
        chosen = filedialog.askopenfilenames(
            title="Escolha vídeos ou áudios", filetypes=file_dialog_types()
        )
        self.add_files([Path(name) for name in chosen])

    def add_files(self, paths: list[Path]) -> None:
        for path in paths:
            if path not in self.files:
                self.files.append(path)
        self._refresh_buttons()

    def add_link(self) -> None:
        """Baixa o áudio do link colado e põe o arquivo na lista, pronto para transcrever."""
        if self.is_running:
            return
        try:
            url = download.validate_url(self.url_entry.get())
        except ValueError as error:
            messagebox.showwarning("Link não aceito", str(error))
            return
        self.cancel_event.clear()
        self.worker = threading.Thread(target=self._run_download, args=(url,), daemon=True)
        self.worker.start()
        self._refresh_buttons()

    def clear_files(self) -> None:
        if self.dirty and not messagebox.askyesno(
            "Limpar lista",
            f"Há alterações não salvas em {len(self.dirty)} arquivo(s). "
            "Limpar a lista descarta essas alterações.\n\nLimpar mesmo assim?",
        ):
            return
        self.files.clear()
        self.buffers.clear()
        self.results.clear()
        self.dirty.clear()
        self._show(None)
        self._refresh_buttons()

    def start(self) -> None:
        pending = [path for path in self.files if path not in self.results]
        if not pending or self.is_running:
            return
        self.cancel_event.clear()
        self.saved = 0
        self.failed = 0
        self.progress_bar["value"] = 0
        self.progress_label.configure(text="")
        language = LANGUAGES[self.language.get()]
        self.worker = threading.Thread(target=self._run, args=(pending, language), daemon=True)
        self.worker.start()
        self._refresh_buttons()

    def cancel(self) -> None:
        if self.is_running:
            self.cancel_event.set()
            self.current_label.configure(text="Cancelando… (termina o trecho atual)")

    def open_folder(self) -> None:
        """Abre a pasta da transcrição que está na tela, com o .txt dela já selecionado."""
        result = self.results.get(self.displayed) if self.displayed else None
        if result is None:
            return
        if result.txt_path.exists():
            # '/select,' vem separado do caminho para o Explorer aceitar nomes com espaços
            subprocess.Popen(["explorer", "/select,", str(result.txt_path)])
        elif hasattr(os, "startfile"):
            os.startfile(result.txt_path.parent)

    def copy_all(self) -> None:
        content = self.text.get("1.0", "end-1c")
        if not content:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self._flash("Texto copiado para a área de transferência.")

    def save_current(self) -> None:
        path = self.displayed
        if path is None or path not in self.dirty:
            return
        result = self.results[path]
        try:
            self.results[path] = engine.save_edits(result, self.buffers[path])
        except OSError as error:
            messagebox.showerror("Não foi possível salvar", f"{result.txt_path}\n\n{error}")
            return
        self.dirty.discard(path)
        self._flash(f"Salvo: {result.txt_path.name} e {result.srt_path.name} atualizados.")
        self._refresh_buttons()

    def on_close(self) -> None:
        if self.is_running:
            if not messagebox.askyesno(
                "Sair",
                "Há uma transcrição em andamento. Sair mesmo assim?\n"
                "O que já foi transcrito fica salvo.",
            ):
                return
            self.cancel_event.set()
        if self.dirty and not messagebox.askyesno(
            "Sair",
            f"Há alterações não salvas em {len(self.dirty)} arquivo(s).\n\nSair sem salvar?",
        ):
            return
        self.root.destroy()

    # ---- editor ----

    def _selected_path(self) -> Path | None:
        selection = self.listbox.curselection()
        return self.files[selection[0]] if selection else None

    def _on_select(self, _event=None) -> None:
        self._show(self._selected_path())
        self._refresh_buttons()

    def _on_chip(self, path: Path) -> None:
        self._show(path)
        self._refresh_buttons()

    def _show(self, path: Path | None) -> None:
        """Põe na caixa o texto de `path` (ou deixa vazia) e marca o arquivo na lista."""
        self.displayed = path
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if path is not None:
            self.text.insert("1.0", self.buffers.get(path, ""))
        self.text.edit_reset()  # o Ctrl+Z não pode "desfazer" a troca de arquivo
        self.text.edit_modified(False)
        self._update_editor_state()
        self.listbox.selection_clear(0, "end")
        if path in self.files:
            index = self.files.index(path)
            self.listbox.selection_set(index)
            self.listbox.see(index)

    def _update_editor_state(self) -> None:
        editable = self.displayed in self.results and self.displayed != self.transcribing
        self.text.configure(state="normal" if editable else "disabled")

    def _append_live(self, piece: str) -> None:
        """Acrescenta o pedaço que acabou de chegar da transcrição (a caixa é só leitura)."""
        self.text.configure(state="normal")
        self.text.insert("end", piece)
        self.text.see("end")
        self.text.edit_modified(False)
        self.text.configure(state="disabled")

    def _on_modified(self, _event=None) -> None:
        """Roda quando o texto muda. Só edição do usuário conta como 'alteração'."""
        if not self.text.edit_modified():
            return
        self.text.edit_modified(False)  # rearma o aviso para a próxima edição
        path = self.displayed
        if path is None or path == self.transcribing or path not in self.results:
            return
        self.buffers[path] = self.text.get("1.0", "end-1c")
        self.dirty.add(path)
        self._refresh_buttons()

    def _flash(self, message: str) -> None:
        """Mostra um aviso curto no lugar da dica e depois volta à dica."""
        if self._flash_job is not None:
            self.after_cancel(self._flash_job)
        self.editor_note.configure(text=message)
        self._flash_job = self.after(FLASH_MS, self._end_flash)

    def _end_flash(self) -> None:
        self._flash_job = None
        self.editor_note.configure(text=EDITOR_HINT)

    # ---- lista de arquivos e abas ----

    def _label(self, path: Path) -> str:
        if path == self.transcribing:
            return f"… {path.name}"
        if path in self.results:
            suffix = "  (alterado)" if path in self.dirty else ""
            return f"✓ {path.name}{suffix}"
        return path.name

    def _refresh_list(self) -> None:
        labels = [self._label(path) for path in self.files]
        if labels == list(self.listbox.get(0, "end")):
            return  # nada mudou: não mexe na lista (evita piscar e perder a rolagem)
        selected = self.listbox.curselection()
        self.listbox.delete(0, "end")
        for label in labels:
            self.listbox.insert("end", label)
        if selected and selected[0] < len(labels):
            self.listbox.selection_set(selected[0])

    def _chip_text(self, path: Path) -> str:
        icon = ICON_AUDIO if is_audio(path) else ICON_VIDEO
        name = path.name
        if len(name) > CHIP_NAME_MAX:
            name = name[: CHIP_NAME_MAX - 1] + "…"
        status = "  …" if path == self.transcribing else ("  ●" if path in self.dirty else "")
        return f"{icon} {name}{status}"

    def _refresh_chips(self) -> None:
        paths = [p for p in self.files if p in self.results or p == self.transcribing]
        signature = [(str(path), self._chip_text(path)) for path in paths]
        if signature != self._chip_signature:
            for button in self.chip_buttons:
                button.destroy()
            self.chip_buttons = [
                ttk.Radiobutton(
                    self.chips_frame,
                    text=self._chip_text(path),
                    value=str(path),
                    variable=self.view_var,
                    style="Toolbutton",
                    command=lambda path=path: self._on_chip(path),
                )
                for path in paths
            ]
            for button in self.chip_buttons:
                button.bind("<Configure>", self._layout_chips)  # refaz o layout se o tamanho mudar
            self._chip_signature = signature
            self._chip_layout_key = ()
            self._layout_chips()
            self.after_idle(self._layout_chips)  # de novo quando as abas já tiverem tamanho final
        self.view_var.set(str(self.displayed) if self.displayed else "")

    def _layout_chips(self, _event=None) -> None:
        """Distribui as abas em linhas, quebrando para a linha de baixo quando falta espaço."""
        widgets = (self.chips_hint, *self.chip_buttons)
        width = self.chips_frame.winfo_width()
        # A chave inclui o tamanho pedido por cada aba: as abas só ganham o tamanho final
        # depois de aparecerem, e aí o layout precisa ser refeito com a medida certa.
        key = (width, tuple(widget.winfo_reqwidth() for widget in widgets))
        if key == self._chip_layout_key:
            return
        self._chip_layout_key = key
        for widget in widgets:
            widget.grid_forget()
        if not self.chip_buttons:
            return  # sem transcrições, a faixa some por inteiro
        limit = width if width > 1 else 10_000  # antes de a janela aparecer, tudo numa linha
        x = row = column = 0
        for widget in widgets:
            needed = widget.winfo_reqwidth() + 8
            if column and x + needed > limit:
                row, column, x = row + 1, 0, 0
            widget.grid(row=row, column=column, padx=(0, 4), pady=2, sticky="w")
            x += needed
            column += 1

    # ---- estado ----

    @property
    def is_running(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _refresh_buttons(self) -> None:
        running = self.is_running
        idle = "disabled" if running else "normal"
        self.add_button.configure(state=idle)
        self.clear_button.configure(state=idle)
        self.url_entry.configure(state=idle)
        self.link_button.configure(state=idle)
        self.language.configure(state="disabled" if running else "readonly")
        has_pending = any(path not in self.results for path in self.files)
        self.start_button.configure(state="normal" if has_pending and not running else "disabled")
        self.cancel_button.configure(state="normal" if running else "disabled")
        has_text = self.displayed is not None and bool(self.buffers.get(self.displayed))
        self.copy_button.configure(state="normal" if has_text else "disabled")
        self.save_button.configure(state="normal" if self.displayed in self.dirty else "disabled")
        self.open_button.configure(state="normal" if self.displayed in self.results else "disabled")
        self._refresh_list()
        self._refresh_chips()

    # ---- thread de trabalho (nunca mexe na janela; só posta mensagens) ----

    def _post(self, kind: str, payload=None) -> None:
        self.messages.put((kind, payload))

    def _run(self, files: list[Path], language: str | None) -> None:
        try:
            if self.loaded is None:
                self._post(
                    "status",
                    "Carregando o modelo… (na primeira vez baixa alguns GB e pode demorar)",
                )
                self.loaded = engine.load_model()
            self._post("device", self.loaded.description)
            for number, path in enumerate(files, start=1):
                if self.cancel_event.is_set():
                    break
                self._post("file_start", (number, len(files), path))
                try:
                    result = engine.transcribe_file(
                        self.loaded.model,
                        path,
                        language=language,
                        on_progress=lambda progress: self._post("progress", progress),
                        on_text=lambda text, path=path: self._post("text", (path, text)),
                        cancel=self.cancel_event,
                    )
                except Exception as error:  # um arquivo ruim não derruba o resto da fila
                    self._post("file_error", (path, str(error)))
                    continue
                self._post("file_done", (path, result))
            self._post("finished")
        except Exception as error:
            self._post("fatal", str(error))

    def _run_download(self, url: str) -> None:
        self._post("dl_start")
        try:
            audio = self.download_audio(
                url,
                self.download_folder,
                on_progress=lambda progress: self._post("dl_progress", progress),
                cancel=self.cancel_event,
            )
        except download.DownloadCancelled:
            self._post("dl_cancelled")
        except Exception as error:  # DownloadError já traz a mensagem pronta
            self._post("dl_error", str(error))
        else:
            self._post("dl_done", audio)
        self._post("dl_end")

    # ---- mensagens da thread -> janela ----

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        self._poll_job = self.root.after(POLL_MS, self._poll)

    def _handle(self, kind: str, payload) -> None:
        if kind == "status":
            self.current_label.configure(text=payload)
        elif kind == "device":
            self.device_label.configure(text=f"Rodando em: {payload}")
        elif kind == "file_start":
            number, total, path = payload
            self.current_label.configure(text=f"Arquivo {number} de {total}: {path.name}")
            self.progress_bar["value"] = 0
            self.progress_label.configure(text="Preparando o áudio…")
            self.transcribing = path
            self.buffers[path] = ""
            self.dirty.discard(path)
            self._show(path)  # o texto vai aparecendo na caixa enquanto é transcrito
        elif kind == "text":
            path, piece = payload
            self.buffers[path] = self.buffers.get(path, "") + piece
            if path == self.displayed:
                self._append_live(piece)
        elif kind == "progress":
            progress: Progress = payload
            self.progress_bar["value"] = progress.fraction * 100
            self.progress_label.configure(text=describe(progress))
        elif kind == "file_done":
            path, result = payload
            if not result.cancelled:
                self.saved += 1
            self.results[path] = result
            self.transcribing = None
            self._update_editor_state()
        elif kind == "file_error":
            path, message = payload
            self.failed += 1
            self.transcribing = None
            self.buffers.pop(path, None)
            if path == self.displayed:
                self._show(path)
            messagebox.showerror("Não foi possível transcrever", f"{path.name}\n\n{message}")
        elif kind == "fatal":
            self.transcribing = None
            self._update_editor_state()
            messagebox.showerror("Erro", payload)
            self.current_label.configure(text="Erro. Veja a mensagem e tente de novo.")
        elif kind == "finished":
            self._finish()
        elif kind.startswith("dl_"):
            self._handle_download(kind, payload)
        self._refresh_buttons()

    def _handle_download(self, kind: str, payload) -> None:
        if kind == "dl_start":
            self.current_label.configure(text="Baixando o áudio do link…")
            self.progress_bar["value"] = 0
            self.progress_label.configure(text="Conectando…")
        elif kind == "dl_progress":
            self.progress_bar["value"] = payload.fraction * 100
            self.progress_label.configure(text=download.describe_download(payload))
        elif kind == "dl_done":
            self.progress_bar["value"] = 100
            self.url_entry.configure(state="normal")
            self.url_entry.delete(0, "end")
            self.add_files([payload.path])
            self._show(payload.path)
            self.current_label.configure(
                text=f"Áudio baixado: {payload.title}. Clique em Transcrever."
            )
            self.progress_label.configure(text=f"Salvo em: {payload.path.parent}")
        elif kind == "dl_cancelled":
            self.progress_label.configure(text="")
            self.current_label.configure(text="Download cancelado.")
        elif kind == "dl_error":
            self.progress_label.configure(text="")
            self.current_label.configure(text="Não foi possível baixar o áudio. Veja a mensagem.")
            messagebox.showerror("Não foi possível baixar", payload)
        elif kind == "dl_end":
            # o fim da thread só é visto como "parada" depois desta mensagem
            self._finish_job = self.after(50, self._refresh_buttons)

    def _finish(self) -> None:
        if self.cancel_event.is_set():
            text = "Cancelado. O que já foi transcrito ficou salvo ao lado do arquivo original."
        else:
            text = f"Pronto: {self.saved} arquivo(s) transcrito(s)."
            if self.failed:
                text += f" {self.failed} com erro."
            text += " Revise o texto abaixo; os .txt e .srt estão ao lado dos originais."
        self.current_label.configure(text=text)
        # o fim da thread só é visto como "parada" depois desta mensagem
        self._finish_job = self.after(50, self._refresh_buttons)


def _enable_sharp_text() -> None:
    """Evita texto borrado em telas com escala acima de 100% (só no Windows)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def main() -> None:
    _enable_sharp_text()
    root = tk.Tk()
    root.title("Transcrever Vídeo")
    root.geometry("860x800")
    root.minsize(700, 700)
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
