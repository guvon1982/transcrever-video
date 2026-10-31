# Política de segurança

## Como o app trata os seus dados

- Todo o processamento é **local**: o áudio e o vídeo não saem do seu computador.
- A única conexão de rede do app é baixar o modelo de transcrição do Hugging Face, na primeira execução.
- O app não tem login, conta nem servidor.

## Versões com correção de segurança

Só a versão mais recente (a branch `develop` e o último release) recebe correções.

## Como reportar uma vulnerabilidade

**Não abra uma issue pública com detalhes técnicos da falha.**

1. Use o botão **Security → Report a vulnerability** deste repositório no GitHub (relato privado).
2. Se o botão não aparecer, abra uma issue pública pedindo um contato privado, **sem descrever a falha**.

Informe: o que acontece, como reproduzir, a versão (ou o commit) e o impacto que você imagina.
Respondo assim que possível; este é um projeto pessoal, sem prazo garantido.

## O que está no escopo

- Código deste repositório (`transcrever_video/`, scripts `.bat`).
- Dependências diretas que o app instala (`faster-whisper`, `av` e as bibliotecas da NVIDIA).

Falhas no próprio faster-whisper, no CTranslate2 ou no modelo Whisper devem ser reportadas aos respectivos projetos.

## Como o repositório se protege

- O CI roda lint, formatação e testes a cada push e pull request.
- Uma auditoria (`pip-audit`) procura vulnerabilidades conhecidas nas dependências a cada execução e **uma vez por semana**.
- O Dependabot propõe atualizações semanais de dependências e das actions do CI (fixadas por hash de commit).
- O GitHub bloqueia o push de segredos (secret scanning com push protection).
- O repositório nunca deve conter vídeos, áudios, transcrições, modelos nem arquivos `.env`; o `.gitignore` bloqueia esses tipos.
