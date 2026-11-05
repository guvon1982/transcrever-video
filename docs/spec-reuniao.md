# Spec: transcrição de reunião com falantes (fase 1)

Status: **proposta, aguardando aprovação**. Escopo: identificar **quem fala o quê** em um arquivo de áudio/vídeo de reunião que o usuário já tem (gravado pelo próprio Meet, Teams ou outra ferramenta). **Fora do escopo:** gravar a reunião dentro do app (fase 2) e transcrição ao vivo.

## Objetivo e resultado esperado

O usuário escolhe um arquivo de reunião, informa quantas pessoas falaram e recebe um `.txt` e um `.srt` em que cada fala vem com o nome do falante ("Falante 1", renomeável). Offline, sem conta, sem enviar áudio para fora.

## Evidência (experimento de 2026-11-02 a 05)

Reunião real de 13 min, português, 3 pessoas, comparada com os turnos do Fathom (referência imperfeita):

| Motor | Concordância com o Fathom | Contagem automática | CPU (13 min de áudio) |
|---|---|---|---|
| sherpa-onnx + CAM++ (3D-Speaker zh_en, 27 MB), nº informado | 77,7% | 30 a 50 falantes (inutilizável) | 201 s |
| sherpa-onnx + titanet-small | 75,3% (fundiu duas vozes) | n/a | 168 s |
| pyannote community-1 | 77,4% | 3 (correto) | 702 s |

Teste de escuta nos 6 trechos de maior divergência: **os sistemas acertaram 6/6 e o Fathom 0/6**. A precisão real é maior que 77% (estimativa: ≥ 85%), mas a amostra é enviesada e o valor exato é desconhecido. Voz sintética: 98% com 2 vozes, ~85% com 3 vozes e falas curtas. Ponto fraco confirmado: **fala curta (< 4 s) e interrupção**.

## Decisões

1. **Motor padrão:** `sherpa-onnx` (Apache-2.0) com a segmentação pyannote-3.0 em onnx e o embedding CAM++ zh_en. Sem conta, 100% offline depois do download dos modelos.
2. **O app pergunta "quantos participantes?"** (mínimo 2). Não usar contagem automática do sherpa.
3. **Pyannote fica fora da fase 1.** Mesma precisão medida, ~3,5x mais lento, exige conta no Hugging Face, aceite de termos e token por usuário. Reavaliar depois como opção avançada (ele acerta o nº de falantes sozinho).
4. **Evitar** o embedding wespeaker CAM++ voxceleb (resultado ruim no experimento) e o titanet-small (fundiu vozes).
5. **Fluxo:** transcrever com o Whisper (já existente) e identificar falantes em seguida, um arquivo por vez. Os dois passos nunca disputam a GPU de 4 GB: a diarização roda na CPU.
6. **Falante de cada palavra:** o falante com mais tempo dentro da palavra; se nenhum, o mais próximo (já validado no experimento). Palavras seguidas do mesmo falante formam um turno.
7. **Nomes:** "Falante 1..N". O usuário renomeia na tela (vale para o arquivo todo). **Não memorizar voz entre reuniões** (embedding de voz é dado biométrico, LGPD; só com opt-in, em outra fase).
8. **Saída:** `.txt` com `Nome: fala` por turno (linha em branco entre turnos) e `.srt` com o nome no início de cada trecho. Mesmas regras de hoje: nunca sobrescrever (numerar), escrita atômica.
9. **Edição:** trocar o nome de um falante atualiza `.txt` e `.srt`. Corrigir o falante de uma fala específica (selecionar turno, escolher outro falante) entra na fase 1; dividir um turno em dois fica para depois.
10. **Modelos:** baixados na primeira vez para a pasta de dados do app, com **verificação de SHA-256 obrigatória** (um download veio truncado no experimento e só o checksum pegou). Falha de checksum = apagar e avisar, nunca usar.

## Aviso de privacidade e consentimento

Antes de processar, o app mostra uma vez: "Gravar e transcrever uma reunião pode exigir o consentimento dos participantes (LGPD). O áudio e o texto ficam só no seu computador." Nada é enviado para fora.

## Requisitos não funcionais

- Custo esperado: ~0,25x o tempo do áudio na CPU do autor (Ryzen 7 2700); 1 h de reunião ≈ 15 min de diarização, além do Whisper. A tela mostra progresso das duas etapas.
- A interface nunca trava (thread, como hoje). Cancelar vale para as duas etapas.
- Memória: o experimento falhou com < 2 GB de RAM livre (pyannote); medir o pico do sherpa antes de fechar o MVP.
- Lógica pura (juntar palavras e falantes, formatar saída, renomear) separada da interface, testável sem janela e sem modelo; testes com um diarizador falso, como o `YoutubeDL` falso do download.

## Critérios de aceite

1. Com a reunião de teste e 3 participantes, o resultado concorda com a escuta humana nos 6 trechos disputados (os do experimento).
2. Renomear "Falante 2" muda todos os turnos dele no texto e na legenda.
3. Cancelar durante a diarização não deixa arquivo parcial nem erro.
4. Download de modelo corrompido (checksum errado) é recusado com mensagem clara.
5. Áudio de 1 pessoa só ou com `quantos = 1` é tratado sem erro (um único falante).
6. `ruff`, `pytest` e CI verdes; nenhum modelo ou áudio de reunião versionado.

## Riscos abertos

- Fala curta e interrupção erram mais; a correção manual por turno é a rede de segurança.
- Qualidade medida em **uma** reunião de 3 pessoas em português. Validar com mais gravações (ruído, 5+ pessoas, sobreposição) antes de prometer "quem falou o quê" como confiável.
- Reunião longa (> 1 h) na CPU: tempo e memória não medidos.
- Dependência nova pesada? `sherpa-onnx` + `numpy` apenas (sem `torch`); confirmar o tamanho final do instalador.
- Gravação dentro do app (fase 2: loopback WASAPI + microfone em trilha separada, que dá "Você" x "os outros" quase de graça) fica para outra spec.

## Ordem de implementação sugerida

1. Módulo de diarização com diarizador falso + testes (lógica de juntar palavras e falantes, saída `.txt`/`.srt`).
2. Download dos modelos com checksum.
3. Integração com o motor (`transcribe_file` + palavras com horário) e a fila.
4. Interface: campo "quantos participantes", renomear falante, corrigir turno, aviso de consentimento.
5. Teste manual com a reunião real e README/CLAUDE.md atualizados.
