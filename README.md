# Corte Studio

Duas ferramentas locais, sem conta, sem login e sem publicação automática (você posta manualmente, de propósito):

- **`app.py` + `index.html`**: front-end no navegador para cortar vídeo em 9:16 com legenda, narração e trilha.
- **`dark.py`**: pipeline por linha de comando que transforma um roteiro JSON e imagens geradas por você em vídeo vertical narrado.

## Instalar (uma vez)
1. Python 3.10 ou mais novo.
2. ffmpeg no PATH. No Windows: `winget install Gyan.FFmpeg`, depois reabra o terminal e teste com `ffmpeg -version`.
3. Dê duplo clique em `iniciar.bat` (Windows) ou rode `./iniciar.sh`. O app abre em http://127.0.0.1:5000.
   O `.bat` checa Python e ffmpeg, instala as dependências e mostra o erro em português se faltar algo.

## Usar o app (cortes)
1. Envie o vídeo. Marque início e fim com os botões ou com as teclas I e O. Adicione quantos cortes quiser.
   Ou clique em **Sugerir cortes**: o app detecta trocas de cena e pausas de fala e sugere cortes entre o mínimo e o máximo de segundos escolhidos, sem cortar no meio de uma frase. Use "ver" para conferir e "+" ou "Adicionar todas" para mandar para a lista. Ele não entende o conteúdo (não sabe o que é interessante), só onde dá para cortar limpo. Num vídeo de 18 min em 1080p, a análise levou 91s nesta máquina [DADO, 1 medição].
2. Ajuste a posição do recorte 9:16 no slider (moldura laranja).
3. Narração e legenda:
   - **Sem narração (padrão)**: usa a fala do próprio vídeo. Adicione os cortes, clique em **Transcrever fala**, corrija o texto e renderize. A legenda segue o tempo de cada palavra mesmo depois da sua correção.
   - **Enviar áudio meu**: envie o áudio e clique em **Transcrever fala** para gerar a legenda dele.
   - **Gerar com IA**: você escreve o texto e o edge-tts lê.
4. Trilha opcional. Renderize. O vídeo e um registro .json da origem ficam na pasta `saida`.

## Pipeline dark/IA (`dark.py`)
Cada roteiro fica numa pasta própria:
```
projetos/<id>/roteiro.json     formato abaixo (gere com prompts/gerar_roteiros.md)
projetos/<id>/img_01.png ...   uma imagem por cena, você gera fora (png/jpg/webp)
projetos/<id>/audio_01.mp3 ... opcional: seu áudio, usado no lugar do edge-tts
```
```json
{"id": "boitata-01", "titulo": "...", "voz": "pt-BR-AntonioNeural", "taxa": "-5%",
 "estilo_visual": "...", "ficcao": true,
 "cenas": [{"texto": "fala da cena", "prompt_imagem": "o que aparece"}]}
```
Comandos:
```
python dark.py auditar projetos/<id>/roteiro.json   duração, gancho <= 20 palavras, imagens faltando, aviso < 65s
python dark.py prompts projetos/<id>/roteiro.json   gera prompts.txt (prompt_imagem + estilo_visual)
python dark.py voz     projetos/<id>/roteiro.json   gera audio_XX.mp3 + tempos_XX.json (--refazer regera)
python dark.py render  projetos/<id>/roteiro.json   [--trilha musica.mp3] [--vol-trilha 0.10] [--fonte Arial]
python dark.py tudo    projetos/<id>/roteiro.json   auditar + voz + render
python dark.py lote    projetos                     roda "tudo" em cada projetos/*/roteiro.json e segue se um falhar
```
Saída: MP4 1080x1920, 30 fps, H.264 + AAC, com zoom suave alternando entrada e saída por cena, legenda em grupos de 3 palavras e um `.json` de registro em `saida/`. O pipeline não gera imagens e não publica nada.

## Regras de uso
- A declaração de origem deixou de ser obrigatória (decisão do usuário em 2026-10-11). O app não verifica direitos autorais. Cena de filme ou série de terceiros é a causa mais comum de vídeo derrubado e de strike [INFERÊNCIA]: o risco fica com a conta.
- Ative o toggle "conteúdo gerado por IA" no TikTok quando houver voz ou imagem sintética.
- Ficção é declarada como ficção. Nunca "história real".
- Para o Creator Rewards o vídeo precisa passar de 1 minuto [DADO, fonte secundária, confirme no app]. O app avisa abaixo de 61s, e o `auditar` abaixo de 65s, porque ali a duração é estimada.

## Voz e legenda
- A voz "gerar com IA" usa o edge-tts, um acesso não oficial ao serviço de leitura do Microsoft Edge, que pode mudar ou parar. Plano B, já pronto: gere a voz em outra ferramenta e use "Enviar áudio" (app) ou salve como `audio_XX.mp3` (pipeline).
- **Transcrição**: faster-whisper, modelo `small`, roda local na CPU. Baixa ~480 MB na 1a vez. Medido nesta máquina (i7-4790): 58s de fala transcritos em 28s [DADO, 1 medição]. Erra às vezes (ex.: "15 %" em vez de "85 %" no começo de um corte), por isso o texto fica editável. Cada corte é transcrito separado, porque, emendados, o Whisper pulou fala repetida.
- **Sincronia**: com edge-tts ou com a transcrição, a legenda usa o tempo real de cada palavra. Se você corrigir o texto, as palavras são casadas com as transcritas e mantêm o tempo. Sem transcrição, com áudio enviado, cai no método antigo, que divide o tempo pelo tamanho do texto. O registro `.json` diz qual foi usado (`legenda_sync`).
- Medição (`python tests/medir_sync.py`, 54,7s de fala, 39 grupos):

  | | média | mediana | máx |
  |---|---|---|---|
  | Antes (proporcional) x início real do grupo | 662 ms | 614 ms | 1639 ms |
  | Depois (WordBoundary) x retomada de fala detectada no áudio | 71 ms | 76 ms | 106 ms |

- Estimativa de fala: o pipeline usa 15 car/s [ESTIMATIVA]. Medido com Antonio a -5%: 12,2 car/s [DADO, 1 medição], ou seja, a fala real sai cerca de 20% mais longa que a estimada. Depois de rodar `voz`, o `auditar` mostra a duração medida.

## Testes
```
python -m pip install pytest
python -m pytest -q
```
São 47 testes: transcrição real (pulada sem internet ou sem modelo), alinhamento do texto corrigido, origem opcional, sugestão de cortes (cenas e pausas), cortes inválidos, vídeo sem áudio, narração maior que o vídeo (tpad), duração e aviso de 61s, legenda por palavra e proporcional, e o pipeline (auditar, prompts, render com áudio próprio, trilha, lote). O teste do edge-tts real é pulado se não houver internet.

## O que foi testado de verdade (Windows 10, Python 3.13.5/Anaconda, ffmpeg 9.0.2 gyan.dev, edge-tts 7.2.8, Flask 3.1.0)
- `iniciar.bat` sobe o servidor e a página abre.
- Fluxo completo da UI no navegador (upload, corte, origem, voz IA, render, link de download), sem erro no console.
- Render via HTTP: cortes, recorte 9:16, legenda queimada com acentos (fonte Arial do Windows), narração enviada, trilha, vídeo sem áudio, validações.
- edge-tts real: voz gerada, áudio audível no vídeo final e tempo por palavra.
- Pipeline `tudo` com 3 cenas, trilha e edge-tts: 1080x1920, 30 fps, zoom visível entre frames.
- Projeto inteiro numa pasta com espaço e acento (`pasta com espaço ação`): testes e pipeline passaram.

## O que ficou sem teste
- Windows sem Anaconda (Python do python.org) e o caminho `py -3` do `.bat` (esta máquina não tem o launcher `py`).
- `iniciar.sh` (Linux/Mac) depois das mudanças.
- Tremido do zoom: não avaliei o vídeo em movimento com imagens reais, só frames isolados. O zoompan pode tremer um pouco [HIPÓTESE].
- Vídeos longos (mais de 10 min) ou 4K como fonte; tempo de render em máquina mais fraca.
- Fontes que não sejam Arial (`--fonte` e o campo `fonte` do app).
- Comportamento do edge-tts com texto muito longo (mais de alguns minutos) ou com bloqueio do serviço.
- Alinhamento local (faster-whisper) para áudio enviado: não foi implementado, porque o WordBoundary funcionou e a tarefa pedia isso só como alternativa.
- No Anaconda, o app liga `KMP_DUPLICATE_LIB_OK=TRUE` antes de carregar o Whisper (conflito de OpenMP entre o numpy e o ctranslate2). É o contorno conhecido para esse caso. Funcionou nos testes, mas a própria Intel o chama de "não suportado".
