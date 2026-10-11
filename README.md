# Corte Studio

Duas ferramentas locais, sem conta, sem login e sem publicação automática (você posta manualmente, de propósito):

- **`/editor`** (`editor.py`, `editor.html`, `editor.js`): editor assistido com linha do tempo, legendas com estilo, títulos, efeitos, versões, ganchos e análise de referências.
- **`app.py` + `index.html`** (modo simples): front-end no navegador para cortar vídeo em 9:16 com legenda, narração e trilha.
- **`dark.py`**: pipeline por linha de comando que transforma um roteiro JSON e imagens geradas por você em vídeo vertical narrado.

## Instalar e abrir
1. Python **3.12 ou mais novo** (as versões fixadas de numpy e av exigem). Testado com 3.13.5.
2. ffmpeg no PATH. No Windows: `winget install Gyan.FFmpeg`, depois reabra o terminal e teste com `ffmpeg -version`.
3. Windows: duplo clique em `iniciar.bat`. Linux/macOS (e Git Bash): `./iniciar.sh`. O app abre em http://127.0.0.1:5000.
   - Na 1a vez o script cria um ambiente próprio em `.venv/` e instala ali as versões fixadas (`requirements.txt` + `constraints.txt`). **Nada é instalado nem atualizado no Python do sistema** (verificado: `pip freeze` global idêntico antes e depois).
   - Ele só reinstala quando `requirements.txt` ou `constraints.txt` mudam. Faltou ffmpeg, Python ou internet, ou a porta 5000 já está em uso (app aberto duas vezes)? A mensagem sai em português e a janela espera você ler.
   - A 1a transcrição baixa o modelo `small` do Whisper (~480 MB) do Hugging Face.

## Editor assistido (`/editor`)
Abra http://127.0.0.1:5000/editor (há um link "modo simples" de volta para a tela antiga, que continua funcionando).

**Como funciona por dentro.** Tudo o que você faz vira um **plano de edição** (JSON versão 1, validado no servidor) com fontes, segmentos (em tempo da *fonte*), legenda (palavras em tempo da *fonte*), títulos (em tempo de *saída*), efeitos e áudio. A **prévia e a exportação são feitas pela mesma função, a partir do mesmo plano**: a prévia só usa resolução menor (540×960, marcada como tal). A mídia original nunca é alterada.

1. **Fontes e linha do tempo**: envie um ou mais vídeos. Cada um entra inteiro como segmento. Marque com <kbd>I</kbd>/<kbd>O</kbd> e <kbd>Enter</kbd>, divida no cursor (<kbd>S</kbd>), apare início e fim (campos ou "= cursor"), reordene (↑↓), remova (✕ ou <kbd>Del</kbd>). "Sugerir" acha cortes limpos (cenas e pausas). Desfazer e refazer (<kbd>Ctrl+Z</kbd>/<kbd>Ctrl+Y</kbd>) cobrem tudo.
2. **Legenda**: "Transcrever e gerar frases" transcreve a fonte inteira (com barra de progresso e botão de cancelar; fica em cache pelo conteúdo do arquivo). Edite o texto de cada frase (o tempo das palavras é preservado), use `|` para dividir, ⤵ para juntar, −/+ para adiantar ou atrasar 0,1s, ✕ para esconder. Como as palavras guardam o tempo da fonte, **cortar, reordenar ou repetir trechos mantém a sincronia** sem refazer nada.
3. **Estilo**: 4 presets (Clássico, Caixa, Palavra em destaque, Discreto) com miniaturas renderizadas pelo mesmo motor (libass) da exportação, e ajuste de fonte, tamanho, cor, contorno, caixa de fundo, posição, maiúsculas, negrito e destaque da palavra falada. Fonte não instalada gera aviso.
4. **Títulos** (aba Títulos): texto editorial, numa caixa clara no alto, **separado da legenda de fala** e marcado pela origem (seu / sugerido).
5. **Efeitos e áudio**, cada um independente: enquadramento 9:16, zoom lento por segmento (intensidade 2–15%), corte seco ou fade pelo preto (não muda a duração), volume original, normalização −14 LUFS, música com volume e "abaixar quando alguém fala".
6. **Versões**: "Duplicar versão" ou aplicar uma sugestão como versão; "Comparar versões" mostra as duas prévias lado a lado. Salve com <kbd>Ctrl+S</kbd> (fica em `edicoes/`) e reabra pelo menu "Abrir projeto". Se um vídeo-fonte foi apagado de `uploads/`, o editor avisa ao abrir.
7. **Ganchos** (aba Ganchos, precisa da transcrição): trechos **falados de verdade**, com tempo na fonte e o porquê (pergunta, número, palavras de impacto, fala direta, tamanho, intensidade da voz, posição), e **títulos derivados** dessa fala, separados e marcados como "não é citação". Cada sugestão pode ser ouvida, ajustada, vista em prévia, aplicada (o trecho é repetido no início; o original continua no lugar), virar uma versão ou ser rejeitada. É heurística local: não prevê desempenho.
8. **Referências** (aba Referências):
   - **Arquivo enviado por você** → análise local de verdade: trocas de cena, ritmo, plano mais longo, primeira e última fala, repetições, silêncios, volume (LUFS), formato e quadros nas trocas de cena, tudo com tempo. Estilo de legenda e efeitos **não** são detectados localmente (está escrito no resultado).
   - **URL do YouTube** → metadados oficiais (views, data, duração, canal, inscritos) **se** houver `YOUTUBE_API_KEY`. Vídeo e áudio só pelo **Gemini**, se houver `GEMINI_API_KEY`.
   - **URL do TikTok** → só título e autor (oEmbed oficial, sem views). Para analisar o vídeo, envie o arquivo.
   - O app **não baixa vídeos de terceiros**. Métricas que você digitar ficam marcadas como "informado por você".
   - Para analisar um arquivo local com o Gemini, o vídeo é **enviado ao Google**: só com a caixa de consentimento marcada.
   - **Comparar** mostra views/dia (com a origem de cada número), as métricas medidas, notas sobre diferenças de exposição, canal e formato, **hipóteses rotuladas** ("evidência muito fraca/fraca", nunca causa) e recomendações para o seu vídeo, cada uma com a evidência, o trecho da fonte e os botões Prévia / Criar versão / Aplicar.

**Credenciais (opcionais)**: crie `config_local.env` na pasta do app (já está no `.gitignore`) ou use variáveis de ambiente:
```
YOUTUBE_API_KEY=...        # YouTube Data API v3 (só metadados; 1 unidade de cota por chamada)
GEMINI_API_KEY=...         # Gemini (análise audiovisual de URL pública do YouTube ou de arquivo com consentimento)
GEMINI_MODELO=gemini-3.8-flash
```
As chaves ficam só no servidor: não vão para a página, para o projeto, para a exportação nem para mensagens de erro. Sem chaves, o editor, a legenda, os ganchos e a análise local funcionam normalmente.

## Usar o app (cortes)
1. Envie o vídeo. Marque início e fim com os botões ou com as teclas I e O. Adicione quantos cortes quiser.
   Ou clique em **Sugerir cortes**: o app detecta trocas de cena e pausas de fala e sugere cortes entre o mínimo e o máximo de segundos escolhidos, sem cortar no meio de uma frase. Use "ver" para conferir e "+" ou "Adicionar todas" para mandar para a lista. Ele não entende o conteúdo (não sabe o que é interessante), só onde dá para cortar limpo. Num vídeo de 18 min em 1080p, a análise levou 91s nesta máquina [DADO, 1 medição].
2. Ajuste a posição do recorte 9:16 no slider (moldura laranja).
3. Narração e legenda:
   - **Sem narração (padrão)**: usa a fala do próprio vídeo. Adicione os cortes, clique em **Transcrever fala**, corrija o texto e renderize. A legenda segue o tempo de cada palavra mesmo depois da sua correção.
   - **Enviar áudio meu**: envie o áudio e clique em **Transcrever fala** para gerar a legenda dele.
   - **Gerar com IA**: você escreve o texto e o edge-tts lê.
4. Trilha opcional. Renderize. O vídeo e um registro `.json` ficam na pasta `saida`, com nome único (dois renders ao mesmo tempo não se sobrescrevem). Avisos (legenda refeita, fonte ausente, menos de 1 minuto) aparecem embaixo do botão.
5. **Espaço em disco**: nada é apagado sozinho. "Limpar temporários" remove restos de renders interrompidos; "Limpar uploads" remove as cópias enviadas ao app, menos as abertas na tela e as em uso. A pasta `saida` nunca é tocada.

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
python dark.py render  projetos/<id>/roteiro.json   [--trilha musica.mp3] [--vol-trilha 0.10] [--fonte Arial] (avisa se a fonte não existir)
python dark.py tudo    projetos/<id>/roteiro.json   auditar + voz + render
python dark.py lote    projetos                     roda "tudo" em cada projetos/*/roteiro.json e segue se um falhar
```
Saída: MP4 1080x1920, 30 fps, H.264 + AAC, com zoom suave (filtro `perspective`, precisão subpixel) alternando entrada e saída por cena, legenda em grupos de 3 palavras e um `.json` de registro em `saida/`. O pipeline não gera imagens e não publica nada.

## Regras de uso
- A declaração de origem deixou de ser obrigatória (decisão do usuário em 2026-10-11). O app não verifica direitos autorais. Cena de filme ou série de terceiros é a causa mais comum de vídeo derrubado e de strike [INFERÊNCIA]: o risco fica com a conta.
- Ative o toggle "conteúdo gerado por IA" no TikTok quando houver voz ou imagem sintética.
- Ficção é declarada como ficção. Nunca "história real".
- Para o Creator Rewards o vídeo precisa passar de 1 minuto [DADO, fonte secundária, confirme no app]. O app avisa abaixo de 61s, e o `auditar` abaixo de 65s, porque ali a duração é estimada.

## Voz e legenda
- A voz "gerar com IA" usa o edge-tts, um acesso não oficial ao serviço de leitura do Microsoft Edge, que pode mudar ou parar. Plano B, já pronto: gere a voz em outra ferramenta e use "Enviar áudio" (app) ou salve como `audio_XX.mp3` (pipeline).
- **Transcrição**: faster-whisper, modelo `small`, roda local na CPU. Baixa ~480 MB na 1a vez. Medido nesta máquina (i7-4790): 58s de fala transcritos em 28s [DADO, 1 medição]. Erra às vezes (ex.: "15 %" em vez de "85 %" no começo de um corte), por isso o texto fica editável. Cada corte é transcrito separado, porque, emendados, o Whisper pulou fala repetida.
- **Sincronia**: com edge-tts ou com a transcrição (da fala do vídeo ou do áudio enviado), a legenda usa o tempo real de cada palavra. Se você corrigir o texto, as palavras são casadas com as transcritas e mantêm o tempo. Se a transcrição ficou velha (os cortes ou o áudio mudaram) ou não foi feita, o render transcreve de novo e avisa; se não conseguir, avisa que a legenda não saiu (sem narração) ou que saiu proporcional ao texto (áudio enviado). Nunca some em silêncio. O registro `.json` diz qual foi usado (`legenda_sync`) e guarda os avisos.
- **Linha do tempo**: os cortes são alinhados à grade de quadros (1/30 s) e cada trecho tem exatamente N quadros e N×1470 amostras, por isso a transcrição e o vídeo final usam a mesma base de tempo. Medição com 6 cortes irregulares (bipe e flash por corte, `tests/test_linha_do_tempo.py`): antes, deriva de até +211 ms no áudio, +267 ms no vídeo e áudio/vídeo desalinhados em até 81 ms; depois, 0 ms e 0 quadro em todas as fronteiras [DADO, 2026-10-11]. O erro do reconhecimento de fala é separado: Whisper x WordBoundary, média de 51 ms e máximo de 186 ms (`tests/medir_sync.py`).
- Medição (`python tests/medir_sync.py`, 54,7s de fala, 39 grupos):

  | | média | mediana | máx |
  |---|---|---|---|
  | Antes (proporcional) x início real do grupo | 662 ms | 614 ms | 1639 ms |
  | Depois (WordBoundary) x retomada de fala detectada no áudio | 71 ms | 76 ms | 106 ms |

- Estimativa de fala: o pipeline usa 15 car/s [ESTIMATIVA]. Medido com Antonio a -5%: 12,2 car/s [DADO, 1 medição], ou seja, a fala real sai cerca de 20% mais longa que a estimada. Depois de rodar `voz`, o `auditar` mostra a duração medida.

## Testes
```
.venv\Scripts\python -m pip install -r requirements-dev.txt -c constraints.txt   (Windows; no Linux/macOS: .venv/bin/python)
.venv\Scripts\python -m pytest -q
.venv\Scripts\python tests/medir_sync.py      (medição manual; precisa de internet)
```
São 169 testes, todos com mídia sintética gerada pelo ffmpeg em pastas temporárias. Cobrem: linha do tempo entre cortes (bipe e flash em cada fronteira), transcrição com Whisper falso e real, legenda velha ou ausente, downloads e nomes de dispositivo do Windows, uploads inválidos, nomes únicos com renders simultâneos, proteção de origem/host, tipos do roteiro, tremido do zoom, prazos sem processo órfão, limpeza, fonte ausente, contorno de OpenMP só no conda, além dos testes anteriores (cortes, vídeo sem áudio, tpad, duração, pipeline). Os testes de edge-tts e Whisper reais são pulados sem internet ou sem o modelo.

## Ambiente testado
- **Sistema operacional testado de verdade**: só Windows 10 Pro 22H2 (10.0.19045). O `iniciar.sh` foi executado no Git Bash desse Windows, não em Linux nem macOS.
- Python 3.13.5 (base do Anaconda, mas rodando no `.venv`), ffmpeg 9.0.2 (gyan.dev, com libass 0.17.5).
- Diretas: Flask 3.1.3, Werkzeug 3.1.9, edge-tts 7.2.8, aiohttp 3.14.3, faster-whisper 1.2.1, numpy 2.5.3; dev: pytest 9.1.1. O conjunto completo está em `constraints.txt` (`pip freeze`). Versões e alertas conferidos no PyPI e no OSV em 2026-10-11. Escolhi versões com algumas semanas de uso, sem alerta aberto (ex.: aiohttp 3.14.3 em vez da 3.14.5, publicada no mesmo dia).
- **OpenMP**: no `.venv`, o numpy vem do PyPI (OpenBLAS) e o Whisper roda **sem** `KMP_DUPLICATE_LIB_OK`. O contorno só é ligado se o app rodar direto num Python conda (Anaconda/Miniconda), onde o numpy com MKL e o ctranslate2 carregam duas `libiomp5md.dll`. Nesse caso o app avisa no terminal. A Intel chama esse contorno de "não suportado".
- faster-whisper 1.2.1 declara `av>=11`, e o av 19 quebra o `decode_audio` dele. O app não usa essa função: decodifica o áudio com o ffmpeg.

## O que foi testado de verdade (Windows 10, 2026-10-11)
- **Editor no navegador**: fonte enviada, dividir no cursor, reordenar, transcrever e ver as frases seguirem a nova ordem, 4 miniaturas de estilo, preset com destaque, título, zoom, prévia (quadros inspecionados: título em caixa no alto, palavra falada em destaque), desfazer/refazer, salvar e reabrir, gancho virando versão, comparação de versões lado a lado, referência local analisada, URL do YouTube sem chave e do TikTok com 429 viraram avisos, comparação com hipóteses rotuladas.
- `iniciar.bat`: sem ffmpeg (mensagem e pausa), caminho feliz pelo `.venv` e porta já ocupada. `iniciar.sh` no Git Bash: sem ffmpeg, porta ocupada e caminho feliz.
- Interface no navegador: upload, cortes, transcrição, estado da legenda (sincronizada / desatualizada), avisos do render, cartão de espaço em disco.
- Render, legenda, narração (enviada e edge-tts real), trilha, vídeo sem áudio, entrada 4K curta, pipeline com 3 cenas e `lote`, projeto em pasta com espaço e acento.
- Zoom: medido numericamente (borda em x e y) e inspecionado visualmente em quimógrafo, isto é, a trajetória da borda quadro a quadro numa imagem. **Não** assisti o vídeo em movimento.

## O que ficou sem teste
- **Análise audiovisual pelo Gemini nunca foi chamada de verdade**: não há `GEMINI_API_KEY` nesta máquina. O formato do pedido e da resposta segue a documentação oficial (endpoint `interactions`, consultada em 2026-10-11) e é testado com respostas simuladas, inclusive malformadas, mas o comportamento real do serviço não foi verificado.
- **YouTube Data API**: sem chave; só testada com respostas simuladas.
- **TikTok oEmbed**: a única chamada real recebeu HTTP 429 (limite de taxa) e o app registrou a limitação; uma resposta de sucesso real não foi vista.
- Títulos derivados localmente às vezes repetem a frase inteira (a heurística só tira muletas e corta em vírgula/7 palavras).
- Linux e macOS de verdade (`iniciar.sh`, wheels das versões fixadas, `xdg-open`/`open`).
- Python do python.org e o launcher `py -3` no `.bat` (esta máquina não tem o `py`).
- Vídeos longos (mais de 10 min) na interface; tempo de render em máquina mais fraca; os prazos (`prazo_midia`) foram calibrados só nesta máquina.
- edge-tts com texto muito longo ou com o serviço bloqueado de verdade (só simulado).
- A detecção de fonte ausente compara o nome pedido com o nome que o libass escolheu. Uma fonte instalada com nome interno muito diferente pode gerar aviso falso [HIPÓTESE].
- A proteção contra outros sites (Origin/Host) foi testada com requisições simuladas, não com um site malicioso real num navegador.
