# transcrever-video

Aplicativo simples para Windows que transcreve **vídeos e áudios** (aulas, palestras, reuniões) em texto, de graça e **local**: o arquivo não sai do seu computador (a internet só é usada se você colar um link).

> **Status: em construção.** Ainda não há versão utilizável. Esta página será atualizada conforme o app ficar pronto.

## O que o app vai fazer

- Transcrever vídeo (`mp4`, `mkv`, `avi`, `mov`, `webm`…) e áudio (`mp3`, `wav`, `m4a`, `flac`, `ogg`…).
- Aceitar também o **link de um vídeo do YouTube**: o app baixa só o áudio (bem menor que o vídeo), guarda em `Documentos\Transcrever Vídeo` e põe na fila. Links de lista de reprodução, transmissão ao vivo e vídeos privados ou que exigem login não funcionam; outros sites (como o Vimeo, que exige login) ainda não são aceitos. Baixe o vídeo por conta própria e use "Adicionar arquivos".
- Mostrar **barra de progresso** e **estimativa de tempo restante**.
- Mostrar a **transcrição na própria janela**, aparecendo enquanto é feita, e deixar você **editar, apagar trechos e copiar** o texto.
- Gerar `.txt` (texto corrido) e `.srt` (legenda com horários) ao lado do arquivo original.
- Organizar o texto em **parágrafos** (pela pontuação, pelas pausas e pelo tamanho), em vez de uma linha por trecho.
- Ao salvar uma edição, atualizar o `.txt` **e** o `.srt`. A legenda acompanha o texto palavra por palavra: palavras editadas mantêm o horário, trechos apagados somem da legenda, e dividir ou juntar parágrafos não mexe nela.
- Mostrar uma "aba" com ícone (▶ vídeo, ♪ áudio) para cada arquivo transcrito, para alternar entre as transcrições com um clique.
- Funcionar com placa de vídeo NVIDIA de 4 GB, ou só com o processador (mais lento).

## Como funciona

Usa o [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (licença MIT), uma implementação rápida do modelo Whisper da OpenAI. Na primeira execução o modelo é baixado do Hugging Face (alguns GB, uma vez só). Depois disso, nada é enviado para a internet.

Para baixar de links usa o [yt-dlp](https://github.com/yt-dlp/yt-dlp) (domínio público). O YouTube muda com frequência e o yt-dlp precisa acompanhar: se um link que funcionava parar de funcionar, atualize com:

```powershell
.venv\Scripts\python -m pip install -U yt-dlp
```

Baixar vídeos pode ferir os termos de uso de alguns sites. Use só para conteúdo que você tem direito de assistir e para uso pessoal.

## Privacidade

- A transcrição é local: nada do que você transcreve sai do computador.
- A internet só é usada para baixar o modelo (uma vez) e, se você colar um link, para baixar o áudio dele.
- Este repositório **nunca** deve conter vídeos, áudios, transcrições ou modelos: o `.gitignore` bloqueia esses arquivos.

## Segurança

Encontrou uma falha? Veja o [SECURITY.md](SECURITY.md) para reportar de forma privada.

## Licença

[MIT](LICENSE).
