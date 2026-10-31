# transcrever-video

App Windows (janela simples) que transcreve vídeo e áudio com faster-whisper. Repositório **público**: nada de segredo, mídia ou dado pessoal no Git.

## Decisões travadas

- **Motor:** `faster-whisper` (SYSTRAN, MIT). Sem outro motor no MVP.
- **Modelo padrão:** `large-v3-turbo`, `compute_type="int8_float16"` na GPU (cabe em 4 GB; GTX 1650 do autor). Sem GPU: `int8` na CPU. `large-v3` em float16 **não cabe** em 4 GB.
- **Sem transcrição em lote** (`BatchedInferencePipeline`): usa mais VRAM e estoura 4 GB. Um arquivo por vez, `batch_size` 1.
- **`condition_on_previous_text=False` (obrigatório):** com o padrão (`True`) o Whisper usa o texto anterior como "exemplo de estilo" e, em áudio longo, degenera: trechos de 1 a 3 palavras, tudo minúsculo, sem pontuação, timestamps colados (gap 0) e até frases engolidas. Reproduzido e confirmado num vídeo de 19,7 min (CPU, `large-v3-turbo` int8, duas rodadas iguais exceto esse parâmetro): palavras por trecho nas faixas 0–5 / 10–14 / 14–17 / 17–20 min = **13,3 / 5,6 / 3,2 / 2,9** (True) contra **15,3 / 10,7 / 11,0 / 10,0** (False); trechos que terminam frase na faixa 17–20 min: 0% contra 43%; texto total 3.195 contra 3.140 palavras (sem perda) e a frase engolida em 13:58–14:03 voltou. Custo: a consistência de grafia de nomes próprios entre trechos pode piorar um pouco. Se algum dia reavaliar, compare de novo com áudio longo (>10 min), onde o defeito aparece.
- **Idioma:** português por padrão (`language="pt"`), configurável. `vad_filter=True` para reduzir texto inventado em silêncio.
- **Janela:** `tkinter` (já vem com o Python, zero dependência extra). Transcrição roda em thread; a interface nunca trava.
- **Progresso e ETA:** `segment.end / info.duration`. O tempo restante usa a velocidade média desde o início (áudio transcrito ÷ tempo real), que é estável; só aparece depois de 2% e 5 s, antes disso mostra "calculando".
- **Fila:** vários arquivos de uma vez, um por vez; o progresso é do arquivo atual ("Arquivo 2 de 5"). Erro num arquivo não derruba os demais. Nunca sobrescreve saída existente (numera: `aula (2).txt`).
- **`av` limitado a `<19`:** o faster-whisper 1.2.1 chama `av.open(metadata_errors=...)`, removido no av 19; sem o limite toda transcrição falha com `TypeError`. Se o Dependabot propuser av 19, ignorar até o faster-whisper corrigir.
- **GPU no Windows:** o `ctranslate2` não acha cuBLAS/cuDNN sozinho. Os pacotes pip `nvidia-cublas-cu12` e `nvidia-cudnn-cu12` (~1 GB) colocam as DLLs no `.venv` e `gpu.py` registra as pastas. A DLL ausente só falha na primeira inferência, não ao carregar o modelo; por isso `load_model` faz um aquecimento e cai para CPU se falhar.
- **Medido na GTX 1650 (4 GB):** modelo de ~1,8 GiB de VRAM em uso total do sistema; ~6× o tempo real num áudio de TTS de 6,4 min (beam_size 5). Não é garantia para aulas reais.
- **Editor de texto na janela:** o texto de cada arquivo aparece ao vivo numa caixa `tk.Text` (somente leitura enquanto aquele arquivo é transcrito; editável depois, inclusive se cancelado). Clicar num arquivo da lista mostra o texto dele; as edições ficam em memória por arquivo (`buffers`/`dirty`). "Salvar" (ou Ctrl+S) grava o `.txt` **e refaz o `.srt`** (`engine.save_edits`, escrita atômica). A legenda **não depende do layout do texto** (`cues.py`): `rebuild_cues` compara o texto antigo e o novo **palavra por palavra** (`difflib`). Palavra igual fica no trecho onde nasceu (mesmo horário); palavra trocada ocupa o lugar da antiga; palavra nova sem par entra no trecho da palavra anterior; trecho que ficou sem palavras some. Dividir, juntar ou criar parágrafos não altera a legenda. Medido: 20 mil palavras (~2 h) em <1 s. Armadilha de teste: texto sintético periódico engana o `difflib` (alinhamento deslocado); use vocabulário aleatório. `TranscriptionResult.cues` guarda os trechos gravados, para a edição seguinte partir do que está no disco. Sair ou limpar a lista com alterações não salvas pergunta antes.
- **Parágrafos no texto** (`paragraphs.py`, `ParagraphBuilder`, em fluxo, então funciona ao vivo): o `.txt` e a caixa de texto juntam trechos do Whisper em parágrafos (um por linha, linha em branco entre eles); a legenda `.srt` continua com um trecho por segmento. Só abre parágrafo **depois de frase terminada** (`. ? ! …`), e quando há pausa ≥ 0,45 s com parágrafo ≥ 220 caracteres, ou parágrafo ≥ 700 (fecha na próxima frase), ou ≥ 1400 sem pontuação (limite de segurança). Calibrado nos `.srt` reais do autor: a pausa **entre trechos** do Whisper é pequena (mediana 0,22 s; p90 0,46 s), então a pausa sozinha é sinal fraco; o tamanho e a pontuação carregam a decisão. `on_text` do motor entrega pedaços **já com o separador** (`""`, `" "` ou `"\n\n"`): concatenar os pedaços (+ `"\n"` final) dá o `.txt`.
- **Abas sob "Transcrição":** uma `ttk.Radiobutton` (estilo `Toolbutton`) por arquivo transcrito ou em transcrição, com ícone (▶ vídeo, ♪ áudio; `●` = alterado, `…` = transcrevendo). Clicar numa aba ou na lista mostra o texto; as duas ficam sincronizadas. As abas quebram de linha conforme a largura da janela; o layout mede o tamanho final das abas (a primeira medição, antes de aparecerem, é errada).
- **"Abrir pasta desta transcrição":** abre o Explorer na pasta da transcrição **mostrada na tela**, com o `.txt` selecionado (`explorer /select,`), não a pasta do último arquivo da fila. "Transcrever" só processa arquivos ainda pendentes, então nunca refaz (nem perde edição de) um arquivo já transcrito.
- **Saída parcial:** o `.txt`/`.srt` é gravado segmento a segmento, para não perder uma aula longa se o app fechar no meio.
- **Entrada:** tudo que o PyAV (FFmpeg embutido) decodifica; o seletor de arquivo filtra vídeo e áudio, mas aceita "todos os arquivos".

## Convenções

- Python 3.12+ (testado em 3.13). Nomes em inglês, comentários em português.
- Lint e formato: `ruff`. Testes: `pytest`.
- Lógica pura (ETA, formatação SRT, extensões) separada da interface, para testar sem janela.

## Comandos

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
.venv\Scripts\python -m pytest
.venv\Scripts\python -m transcrever_video   # abre a janela
```

Se o pytest der `PermissionError` em `%TEMP%\pytest-of-<usuario>` (pasta antiga com permissão quebrada no PC do autor), rode com `--basetemp="$env:TEMP\tv_pytest"`.

## Segurança (repo público)

- Nunca versionar: vídeos/áudios, transcrições, modelos, `.env`.
- Manter Dependabot ligado; alerta de CVE é trabalho a fazer.
- Instalar dependências só do PyPI, com o nome exato (`faster-whisper`).

## Git

`main` = releases; `develop` integra; código em `feature/<nome>` a partir de `develop`; PR → `develop` com merge `--no-ff`. Detalhes no `CLAUDE.md` global do autor.
