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

**Organização.** À esquerda, o **player** (sempre visível) com o recorte do corte ativo e a linha do tempo. À direita, o **corte ativo e o escopo** e cinco abas: **Cortes** (descobrir cortes e ganchos), **Legendas**, **Texto na tela**, **Áudio e efeitos**, **Publicação** (texto do post, exportação e referências). Ajustes avançados ficam recolhidos ("Análise de conteúdo", "Imagem: enquadramento, zoom e transição", "Novo corte por marcação").

**Prévia integrada no player** (botões acima do vídeo; <kbd>B</kbd> alterna antes/depois no mesmo instante):
- **Original**: o vídeo-fonte, sem edição.
- **Editado (aproximado)**: toca a linha do tempo de saída no navegador, com legenda e texto na tela desenhados por cima (aproximação do libass), volume do áudio original, música e efeitos mixados (Web Audio). Muda na hora. **Não** mostra zoom, fade, realce de voz, normalização nem o abaixamento da música: está escrito embaixo do player.
- **Renderizado (trecho)**: ~10 s em 540×960 feitos pelo **mesmo `render_plano` e o mesmo plano validado da exportação**. Mostra "Atualizando prévia…", "Prévia em dia" ou "Prévia desatualizada"; com "atualizar sozinho", re-renderiza 1,5 s depois da última edição; resultado de um pedido antigo é descartado (só o mais novo aparece). "Prévia" de uma sugestão renderiza o mesmo trecho com e sem ela (antes/depois).

**Como funciona por dentro.** Tudo o que você faz vira um **plano de edição** (JSON versão 1, validado no servidor) com fontes, segmentos (em tempo da *fonte*), legenda (palavras em tempo da *fonte*), títulos (em tempo de *saída*), efeitos e áudio. A **prévia e a exportação são feitas pela mesma função, a partir do mesmo plano**: a prévia só usa resolução menor (540×960, marcada como tal). A mídia original nunca é alterada.

1. **Fontes e linha do tempo**: envie um ou mais vídeos. Cada um entra inteiro como segmento. Marque com <kbd>I</kbd>/<kbd>O</kbd> e <kbd>Enter</kbd>, divida no cursor (<kbd>S</kbd>), apare início e fim (campos ou "= cursor"), reordene (↑↓), remova (✕ ou <kbd>Del</kbd>). Desfazer e refazer (<kbd>Ctrl+Z</kbd>/<kbd>Ctrl+Y</kbd>) cobrem tudo.
   - **Corte ativo**: o segmento selecionado. Aparece no topo da coluna da direita (fonte, tempo na fonte e na saída) e é o escopo de legenda, títulos, ganchos, áudio e pesquisa.
   - **Recorte no player**: abaixo do vídeo, uma trilha com duas alças (início/fim) do corte ativo; os outros cortes da mesma fonte aparecem em cinza. Arraste as alças (o player mostra o quadro da borda), digite os tempos, use "no cursor" (<kbd>[</kbd>/<kbd>]</kbd>), setas ±1 quadro (Shift: ±1 s) com a alça focada, "aproximar no corte" para precisão e "Tocar só o corte" (<kbd>P</kbd>, com "repetir"). Tudo entra no desfazer/refazer; o arquivo original não muda.
   - **Descobrir cortes pelo conteúdo** (aba Cortes; fonte inteira, só quando você pede): acha os **assuntos completos** (histórias, explicações, argumentos, passo a passo) pela estrutura natural da fala, sem número fixo de cortes nem durações iguais; pausas e cenas só ajustam as bordas. Um assunto mais longo ou mais curto que a faixa pedida fica inteiro e vem com o aviso do porquê. Cada cartão mostra: posição no ranking, título específico, ideia central, por que selecionar, por que fica acima do próximo, gancho, trecho da fala, falta de contexto e os seis critérios (abertura, interesse, clareza, funciona sozinho, ritmo, fechamento). É avaliação editorial, não previsão de views. Dá para ajustar as bordas, ver o intervalo direto na fonte (sem render) e adicionar à edição; a lista não muda a edição. Enquanto transcreve, mostra sugestões **provisórias**; repetir a descoberta reaproveita o resultado. Com Gemini, o modelo lê a transcrição em blocos com sobreposição (até 2 pedidos ao mesmo tempo) e uma ordenação final junta tudo; sem Gemini, título, ideia e ranking saem de regras (rotulado).
2. **Legenda**: "Gerar legenda do corte ativo" transcreve **só o trecho do corte** (ou "de todos os cortes incluídos"); fala de fora dos cortes não vira legenda. A transcrição fica em cache por conteúdo do arquivo e por trecho: o que já foi transcrito (inclusive a fonte inteira) é reaproveitado e só o que falta é transcrito. Gerar de novo **mantém as frases que você corrigiu**; se um recorte cortar uma correção pela metade ou deixá-la fora, aparece um aviso (a frase fica guardada). Cada frase mostra três tempos: saída, relativo ao corte e fonte; "só do corte ativo" filtra a lista. Edite o texto de cada frase (o tempo das palavras é preservado), use `|` para dividir, ⤵ para juntar, −/+ para adiantar ou atrasar 0,1s, ✕ para esconder. Como as palavras guardam o tempo da fonte, **cortar, reordenar ou repetir trechos mantém a sincronia** sem refazer nada.
3. **Estilo**: 4 presets (Clássico, Caixa, Palavra em destaque, Discreto) com miniaturas renderizadas pelo mesmo motor (libass) da exportação, e ajuste de fonte, tamanho, cor, contorno, caixa de fundo, posição, maiúsculas, negrito e destaque da palavra falada. Fonte não instalada gera aviso.
4. **Texto na tela** (aba Texto na tela): texto editorial queimado no vídeo, **separado da legenda de fala**, com tempo, posição (altura em %), tamanho e aparência próprios (caixa clara, caixa escura, contorno amarelo) e marcado pela origem (seu / sugerido). "✨ Sugerir" ao lado de cada item (ou "Sugerir títulos para o corte ativo") lê a fala do corte (transcreve se precisar) e propõe 5 títulos com explicação e a fala que sustenta cada um; termos que não aparecem na fala ficam sinalizados. Dá para editar, aplicar (só com clique), adicionar como título novo, desfazer e "Gerar outras".
5. **Efeitos e áudio**, cada um independente: enquadramento 9:16, zoom lento por segmento (intensidade 2–15%), corte seco ou fade pelo preto (não muda a duração), volume original, normalização −14 LUFS, realce de voz, música com volume e "abaixar quando alguém fala". **Efeitos sonoros** (passagem, impacto, subida, ambiência grave, bipe) são sintetizados pelo ffmpeg, sem licença a conferir; dá para ouvir, adicionar no cursor, ajustar posição/duração/volume e remover. Efeito preso à fonte acompanha o trecho ao cortar/reordenar. "Sugerir áudio" mede fala x ruído e só sugere efeito onde há virada e pausa (sem cobrir fala); som que não existe pronto aparece como "precisa de um arquivo seu" e vira efeito quando você envia o arquivo.
6. **Publicação** (aba Publicação): **texto do post do TikTok**, que **não aparece no vídeo**. Fica salvo no projeto (projetos antigos abrem com o campo vazio), entra no desfazer/refazer e tem botão Copiar. "✨ Sugerir texto da publicação" lê a fala do corte e propõe opções com hashtags; cada sugestão diz "Destino: Texto da publicação" e só muda o campo com "Usar no campo" ou "Acrescentar". O app não publica nada.
7. **Versões**: "Duplicar versão" ou aplicar uma sugestão como versão; "Comparar versões" mostra as duas prévias lado a lado. Salve com <kbd>Ctrl+S</kbd> (fica em `edicoes/`) e reabra pelo menu "Abrir projeto". Se um vídeo-fonte foi apagado de `uploads/`, o editor avisa ao abrir.
8. **Ganchos** (aba Cortes). Para os melhores textos de gancho na tela, o app monta **alternativas completas** (texto +
   estilo Esporte, Impacto ou Limpo + cor + palavra em destaque + tamanho + altura + entrada + duração), escolhidas pelo
   conteúdo (ex.: vocabulário de jogo → Esporte; revelação/conflito → Impacto), pela faixa da imagem com menos detalhe
   (longe da legenda e do topo/rodapé do TikTok; rostos e ação NÃO são detectados) e pelo “Meu estilo” salvo. Cada
   alternativa tem miniatura real do corte (libass), “Mais impactante”, “Mais discreto”, ajuste de cada propriedade,
   “Ver no player”, Aplicar/Desfazer e “Salvar como meu estilo” (fica em `edicoes/preferencias.json`). Aplicado, o gancho
   fica editável na aba Texto na tela, salva no projeto e sai na exportação.
   Análise: "Analisar ganchos" transcreve o corte ativo (ou a linha inteira) se precisar e separa **fala existente** (citada da transcrição, com tempo na fonte), **momento visual** (só com quadros autorizados), **título sobreposto** (texto novo) e **narração nova** (precisa ser gravada; não é aplicada). Cada um traz categoria (afirmação, pergunta, conflito, revelação), por que prende, o que promete, **se o corte entrega** (com o trecho que entrega) e a edição proposta. Prévia, aplicar, criar versão, desfazer só aquela, rejeitar, e "Aplicar tudo" (gancho + título) como uma edição completa.

**Credenciais (opcionais)**: crie `config_local.env` na pasta do app (já está no `.gitignore`) ou use variáveis de ambiente:
```
GEMINI_API_KEY=...         # Gemini: análise de conteúdo (texto da transcrição), quadros só com consentimento, URL pública do YouTube
GEMINI_MODELO=gemini-3.8-flash
```
Para usar a sua chave do Google AI Studio: crie o arquivo `config_local.env` na pasta do app com a linha `GEMINI_API_KEY=sua-chave` e reinicie o app. Não cole a chave em chat, código ou projeto.

As chaves ficam só no servidor: não vão para a página, para o projeto, para a exportação nem para mensagens de erro. Sem chaves, o editor, a legenda, os ganchos e a análise local funcionam normalmente.

**Análise de conteúdo: IA ou heurística, sempre rotulado.** Com `GEMINI_API_KEY`, cortes, títulos, ganchos, áudio e buscas usam o modelo, que recebe **só o texto da transcrição do escopo** (quadros do corte só com a caixa "Autorizo enviar QUADROS" marcada). A fala citada nas sugestões vem da transcrição, referenciada por índice, e não do texto do modelo; resposta fora do formato é descartada e o resultado diz quantas. Sem chave, com o seletor em "Só heurística local" ou se o provedor falhar, o app usa uma **heurística local (sem IA)**: pontuação, pausas, cenas, vocabulário repetido e marcadores de pergunta/conflito/revelação. Ela não entende o conteúdo e o resultado diz isso.

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

- **Conteúdo por corte (2026-10-11)**: vídeo com dois assuntos (0–19,7 s lenda da cobra; 22,7–37 s receita de bolo), voz real do edge-tts e Whisper real. A legenda do corte do bolo não traz nada da cobra e a fonte inteira não foi transcrita. Títulos e ganchos de um corte não citam o outro assunto. A descoberta de cortes separou os dois assuntos. Tudo também conferido no Chrome (headless, puppeteer) contra uma instância isolada: alças, números, desfazer/refazer, tocar só o corte (parou a ~10 ms do fim), legenda do corte, reordenar com a legenda seguindo, aviso de correção, título/gancho aplicados e desfeitos, efeito, pesquisa sem chave e prévia.

- **Interface reorganizada e prévia integrada (2026-10-12)**, no Chrome headless contra instância isolada, com o vídeo de dois assuntos: abas e estados vazios; legenda só do corte; vista Editado com legenda e texto na tela no player (tamanho e posição mudando na hora) e volume ao vivo; Original↔Editado preservando o instante (saída 5,0 s = fonte 27,4 s); trecho renderizado "em dia" → "desatualizada" ao mudar um ajuste → três edições rápidas e a prévia exibida é a da última; publicação sugerida preenchendo só o campo (nenhum texto no vídeo), desfazer/refazer, salvar e reabrir com publicação, texto na tela e ajustes; "Ouvir nesta cena" agendando o efeito no mix; escopo de áudio explícito; pesquisa informando cada caminho indisponível; 390 px de largura sem rolagem horizontal. Exportação 1080×1920 do projeto salvo inspecionada em quadro: legenda e texto na tela onde o player mostrava.

- **Descoberta de cortes: desempenho medido (2026-10-12, esta máquina, mesma faixa 20–75 s, heurística local)**. Fonte de 6 assuntos (4,6 min, voz sintética do Windows): total a frio 124,5 s → 57,7 s; primeira sugestão 124,5 s → 47,2 s (provisória); a quente 0,12 s → 0,02 s. Vídeo real de 13 min: total a frio 521,7 s → 210,8 s; primeira sugestão 521,7 s → 111,6 s; cenas (95 s) passaram a rodar em paralelo à transcrição. Ganho principal: Whisper em lote (8 trechos, beam 1) no lugar de beam 5: 54,7 s x 142,0 s no mesmo áudio, erro de palavras 0,327 x 0,325, início das palavras com mediana de 10 ms de diferença (p90 55 ms). Bordas: os 6 assuntos saíram com as bordas certas (um deles dividido em dois subpontos), contra assuntos cortados ao meio na versão anterior.
- **Precisão da descoberta de cortes (2026-10-12, heurística local)**, medida contra fronteiras de assunto anotadas à mão lendo a transcrição (`tests/anotacoes_descoberta.json`; rode `tests/medir_descoberta.py`). F1 das fronteiras (tolerância 8–10 s) e pureza (quanto de cada corte cai num único assunto): gameplay narrado de 13 min 0,10 → 0,66 (pureza 0,71 → 0,95); entrevista de 18 min 0,32 → 0,61 (0,80 → 0,90); entrevista de 6:44 usada só como CONTROLE (não entrou no ajuste) 0,43 → 0,55. O que mudou: frases sem pontuação de até 90 s viram unidades de até 10 s cortadas em pausa/vírgula; janela de comparação de 50 s; pausa deixou de valer como fronteira; pergunta de entrevista com conteúdo reforça fronteira; assunto curto não é mais juntado com o vizinho. Piorou: a fonte sintética de 6 assuntos foi de 0,91 para 0,73 (ali as pausas marcavam as trocas).
- **Pesquisa de referências externas removida do fluxo (2026-10-12)**, a pedido do dono: sem aba, sem rotas registradas e sem chamadas automáticas. Os arquivos de referência já salvos em `referencias/` ficam no disco, intocados. `referencias.py` continua só como biblioteca do cliente Gemini.

## O que ficou sem teste
- **Descoberta e ganchos sem IA dependem da transcrição**: com a voz sintética do Windows o Whisper inventa palavras (“copyright”, “Nyan Libra”) e alguns títulos locais herdam isso (ex.: “Argumento: ponto uncopright…”); o tipo de fala também erra às vezes (a lenda da cobra saiu como “Explicação”). Com voz humana espera-se menos ruído [HIPÓTESE, não medido]. Com Gemini, título e ideia seriam escritos pelo modelo [não testado: sem chave].
- Rostos e ação na imagem NÃO são detectados ao posicionar o gancho: só a faixa com menos detalhe, longe da legenda e do topo/rodapé.
- **Gemini sem chave nesta máquina (2026-10-12)**: nenhuma chamada real (análise, publicação, Busca Google). Tudo testado com respostas simuladas.
- **edge-tts recebendo HTTP 403** do serviço da Microsoft em 2026-10-12 (versão 7.2.8 fixada): narração por IA do modo simples e 4 testes reais pulados. Provável mudança do serviço; não atualizei o pacote [INFERÊNCIA].
- Vista Editado: o som não foi ouvido por pessoa (testado que o efeito é agendado no mix e que o volume muda); a legenda desenhada é uma aproximação do libass (fonte, contorno e quebra de linha podem diferir um pouco).
- **Nenhuma análise de conteúdo por IA foi feita de verdade** (títulos, ganchos, cortes, áudio, buscas): sem `GEMINI_API_KEY` nesta máquina. Só respostas simuladas (válidas, malformadas, inventando fala, provedor fora do ar). A qualidade real das sugestões do modelo não foi avaliada. Envio de quadros como imagem inline segue a documentação de 2026-10, mas não foi chamado.
- A heurística local de "entrega" (o corte responde ao gancho?) é por vocabulário repetido: erra quando a resposta usa outras palavras [INFERÊNCIA, visto no teste da cobra: marcou "parcial"].
- Efeitos sonoros: posição medida numericamente no render (bipe), mas o som de cada efeito não foi avaliado por ouvido humano.
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
