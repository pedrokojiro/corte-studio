# AGENTS.md — contexto compartilhado (Codex, Claude e qualquer agente)

Leia isto antes de mexer no código. Vale para todos os agentes que trabalham neste repositório.

## O projeto
Canal de terror e mistério dark/IA no TikTok em PT-BR. Produção solo, de 5 a 10 h por semana, orçamento R$ 0.
O plano atual (30 dias) é **validar formato e retenção**, não monetizar. A meta futura é o Creator Rewards (10 mil seguidores, 100 mil views em 30 dias, vídeos acima de 1 min) [DADO, fonte secundária].

- `app.py` + `index.html`: app Flask local (http://127.0.0.1:5000). Corta vídeo em 9:16, sugere cortes (trocas de cena e pausas), transcreve a fala (faster-whisper), queima a legenda (ASS), narra com edge-tts ou áudio enviado, mixa a trilha e grava um `.json` de registro ao lado de cada vídeo.
- `dark.py`: pipeline CLI, sem UI. Recebe um roteiro JSON e imagens geradas fora e devolve um MP4 1080x1920 com zoom, voz e legenda por palavra. Comandos: `auditar`, `prompts`, `voz`, `render`, `tudo`, `lote`.
- `prompts/gerar_roteiros.md`: prompt mestre para gerar roteiros no formato do `dark.py`.
- `README.md`: uso, medições e a lista do que foi testado e do que ficou sem teste.

## Rodar e testar
Sempre pelo ambiente próprio `.venv` (criado pelos scripts de início). Não instale nada no Python global.
```
iniciar.bat / ./iniciar.sh                       # cria .venv, instala requirements.txt + constraints.txt, sobe o app
.venv\Scripts\python -m pip install -r requirements-dev.txt -c constraints.txt
.venv\Scripts\python -m pytest -q                 # mídia sintética; edge-tts e Whisper reais são pulados offline
.venv\Scripts\python tests/medir_sync.py          # mede a sincronia da legenda e o erro do Whisper (precisa de internet)
```
(No Linux/macOS: `.venv/bin/python`.) Ambiente testado: **só Windows 10**, Python 3.13.5, ffmpeg 9.0.2. Versões em `constraints.txt`. Exige Python 3.12+.

## Interfaces estáveis (o que a próxima fase pode usar)
- `POST /transcrever` `{video_id, cortes}` ou `{audio_id}` → `{texto, tempos:[[ini,fim,palavra]], chave}`. Os tempos estão na linha do tempo do vídeo final.
- `POST /render` aceita `tempos` + `tempos_chave` (a `chave` acima). Se a chave não bater com os cortes/áudio atuais, o render transcreve de novo. Resposta: `{arquivo, duracao, avisos:[...], legenda_sync}`; `.json` com o mesmo nome em `saida/`.
- `POST /analisar` `{video_id, min, max}` → sugestões de corte. `GET/POST /limpeza` → espaço em disco e limpeza explícita.
- Funções: `ler_cortes()` (alinha à grade de 1/30 s), `linha_do_tempo()`, `chave_transcricao()`, `transcrever()`, `alinhar()`, `grupos()`, `ass_doc()`, `verificar_fonte()`, `run(cmd, timeout=)`/`prazo_midia()`, `pasta_de_trabalho()`, `em_uso()`, `nome_saida()`/`salvar_par()`. No `dark.py`: `validar()`, `filtro_zoom()`.
- Toda rota POST exige `Content-Type: application/json` (exceto `/upload`, multipart) e recusa Host fora de 127.0.0.1/localhost e `Origin` de outro site. Em `curl`, mande `-H "Content-Type: application/json"`.
- Erros sempre em JSON: `{"erro": "..."}`, com status 400/403/404/413/415/500/504.

## Editor assistido (módulos e contratos)
- `editor.py`: plano de edição v1 (`validar_plano` é a porta de entrada de QUALQUER plano, inclusive vindo de IA), `mapa`/`fonte_para_saida`/`saida_para_fonte` (fonte x saída), `eventos_legenda` (legenda em tempo de fonte projetada na saída), `ass_plano` (estilos Legenda e Titulo), `render_plano` (prévia e exportação, a mesma função), `transcrever_fonte` (cache por sha256 + modelo + idioma), projetos em `edicoes/`.
- `tarefas.py`: tarefas em segundo plano (uma pesada por vez), progresso, cancelamento; `tarefas.ffmpeg` mata o processo ao cancelar ou estourar o prazo.
- `referencias.py`: hoje só BIBLIOTECA (sem rotas registradas): credenciais (`credencial`, `status_config`), HTTP com tentativas limitadas, `gemini_json` e `json_do_texto`. A pesquisa de referências externas foi REMOVIDA do fluxo pelo dono em 2026-10-12: não reintroduzir nem substituir por outro serviço de descoberta. Credenciais só do ambiente/`config_local.env`, nunca em log nem exportação.
- `sugestoes.py`: `ganchos` (fala real + títulos derivados, separados), `aplicar_operacao` (conjunto FECHADO: inserir_inicio, adicionar_titulo, remover_trecho, cortar_inicio, ativar_legenda, ativar_zoom; sempre revalida), `recomendacoes`.
- `conteudo.py`: análise do conteúdo do corte. Cortes por conteúdo (`descobrir_cortes`, fonte inteira, sem legenda), `sugerir_titulos`, `analisar_ganchos`, `sugerir_audio`, `pesquisar_referencias`. Provedor: `gemini` (referencia falas por ÍNDICE de frase; o texto vem da transcrição) ou `local` (heurística, rotulada), com fallback que avisa (`com_ia`). Escopo `corte` | `linha`; a transcrição necessária é automática e só dos trechos (`editor.transcrever_plano`).
- Transcrição por trecho: `editor.transcrever_trechos` (cache `*_trechos.json` com `cobertura`; só transcreve o que falta), `transcricao_combinada`, `palavras_no_intervalo`. Legenda: `mesclar_frases` (só dentro dos trechos; frase `editada` nunca é sobrescrita), `avisos_legenda`. Eventos de legenda têm `seg`, `src` (fonte) e `rel` (relativo ao corte).
- Plano: `audio.realce_voz` e `audio.efeitos` (`som` do catálogo `editor.SONS`, sintetizado pelo ffmpeg, ou `arquivo`; ancorado na fonte ou na saída: `efeitos_saida`). Operações novas: `adicionar_segmento`, `definir_titulo`, `adicionar_efeito`, `remover_efeito`, `ajustar_audio`; `/api/operacao` aceita `operacoes: [...]` (lote atômico).
- Rotas: `/editor`, `/api/plano/validar`, `/api/render` (202 + tarefa), `/api/tarefas/<id>` (+ `/cancelar`), `/api/transcricao` (`{trechos}` ou `{fonte}` = fonte inteira), `/api/legenda/frases|editar|gerar`, `/api/miniaturas`, `/api/projetos`, `/api/ganchos` (antiga) e `/api/ganchos/analisar`, `/api/titulos/sugerir`, `/api/titulos/miniatura`, `/api/preferencias`, `/api/audio/sugerir`, `/api/cortes/descobrir`, `/api/efeitos` (+ `/som/<som>`), `/api/operacao`, `/api/config`.
- Prévia integrada (`editor.js`, objeto `PV`): vistas Original / Editado (aproximado, Web Audio + camada HTML; não é render) / Renderizado (trecho via `POST /api/render` com `trecho: [a, b]` ≤ 30 s → `editor.recortar_plano`, mesma `render_plano`). Resultado de render velho é descartado por geração (`PV.rendGen`); estado em dia/desatualizada compara o plano validado.
- Texto na tela (`titulos`): `estilo` (`editor.ESTILOS_TITULO`: claro/escuro/contorno), `posicao_y`, `tamanho`; padrões = visual antigo. Texto da PUBLICAÇÃO fica no PROJETO (`publicacao.texto`), nunca no plano nem no vídeo. `/api/titulos/sugerir` aceita `destino: "video" | "publicacao"`.
- Precisão da descoberta: mude parâmetros (`UNIDADE_MAX_S`, `JANELA_S`, `LIMIAR_K`, `FRONTEIRA_MIN_S`, `PESO_PERGUNTA`) só medindo com `tests/medir_descoberta.py` contra `tests/anotacoes_descoberta.json`, e não ajuste usando o caso de CONTROLE. Ao mudar o método, suba `VERSAO_DESCOBERTA` (invalida o cache).
- Descoberta de cortes (`conteudo.descobrir_cortes`): assuntos por coesão (`blocos_de_assunto`), cortes pela estrutura natural (`descobrir_local`), ranking por 6 critérios (`avaliar`) com motivo; Gemini em blocos de 320 frases com 30 de sobreposição, 2 pedidos simultâneos e uma ordenação final (`descobrir_ia`). Cache em `cache/descobertas/` por mídia, Whisper, `VERSAO_DESCOBERTA`, provedor/modelo e faixa. Cenas em paralelo com a transcrição. Provisórios em `tarefa.parcial`. `tarefas.iniciar(..., chave=)` devolve a mesma tarefa para pedido idêntico em andamento.
- Texto na tela: presets `editor.ESTILOS_TITULO` (claro, escuro, contorno, esporte, impacto, limpo) + sobrescritas `CAMPOS_VISUAIS` e `destaque` (posições de palavra), animação de entrada em `dialogo_titulo` (\fad, \t, \move). `miniatura_titulo` (libass), `area_livre` (menos detalhe na imagem, longe da legenda), preferências em `edicoes/preferencias.json` (`/api/preferencias`). Operações de título aceitam `visual: {...}` (chaves fechadas).
- Não baixe vídeos de terceiros nem tente burlar plataforma: URL sem acesso audiovisual oficial = só metadados + pedir o arquivo.

## Regras (definidas pelo dono do projeto)
1. ~~Origem do material obrigatória~~: **revogada pelo dono em 2026-10-11**. A origem não aparece mais na UI. O backend ainda aceita os campos opcionais `origem` e `prova`.
2. **Proibido**: login, postagem automática, API não oficial do TikTok, compra de engajamento ou qualquer coisa que arrisque a conta. Também não implementar download ou scraping de vídeo.
3. Manter o lembrete do toggle "conteúdo gerado por IA" e o aviso de vídeo com menos de 61s.
4. **Anti-overbuilding**: só o que foi pedido. Sem frameworks pesados, sem banco de dados, sem refatorar o que funciona.
5. Marcar afirmações como [FATO], [DADO], [HIPÓTESE], [ESTIMATIVA] ou [INFERÊNCIA]. Não apresentar estimativa como fato. Apontar contradições nos pedidos.
6. Ao fim de cada tarefa: **rodar os testes** e resumir em 3 linhas (o que funcionou, o que quebrou, o que ficou sem teste). Nunca declarar "pronto" sem ter executado.

## Armadilhas conhecidas (não "consertar" sem medir)
- **Windows**: `subprocess` usa `encoding="utf-8", errors="replace"`, porque o padrão cp1252 quebra com caminho acentuado. Nomes como `CON`, `AUX` e `NUL` travam ou enganam o acesso a arquivo: `/saida` só aceita o formato que o app gera.
- **Legenda ASS**: o filtro roda com `cwd` na pasta de trabalho e caminho relativo (`ass=legenda.ass`), o que evita escapar o `C:` no filtro. Uma fonte inexistente cai em Arial **em silêncio**; só o log `-loglevel verbose` (`fontselect:`) mostra a troca.
- **Linha do tempo**: cada clipe intermediário tem exatamente N quadros e N×1470 amostras, em PCM/MKV, com busca meio quadro antes do corte. Não volte para clipes AAC/MP4 emendados: antes isso dava deriva de até +267 ms em 6 cortes e áudio/vídeo desalinhados em até 81 ms (`tests/test_linha_do_tempo.py`).
- **edge-tts 7.x**: o padrão é `SentenceBoundary`. Para ter tempo por palavra, é preciso pedir `boundary="WordBoundary"`.
- **OpenMP**: no Python conda (Anaconda), o numpy com MKL e o ctranslate2 carregam duas `libiomp5md.dll` e o processo aborta. O app só liga `KMP_DUPLICATE_LIB_OK` quando detecta conda (`precisa_contorno_openmp`); no `.venv` não precisa. O áudio é decodificado pelo ffmpeg, não pelo módulo `av` (o av 19 é incompatível com o faster-whisper 1.2.1).
- **Whisper com cortes emendados** pulou fala repetida. Por isso cada corte é transcrito separado, deslocado por `linha_do_tempo()`.
- **Zoom**: o `zoompan` arredonda para pixel inteiro e treme. Usar o `perspective` com `eval=frame` (`dark.filtro_zoom`), testado por `test_zoom_move_a_borda_sem_voltar`.
- **Velocidade de fala**: o pipeline estima 15 car/s [ESTIMATIVA]. Medido com Antonio a -5%: 12,2 car/s [DADO, 1 medição].

## Como colaborar entre agentes
- Uma **branch por tarefa** (`codex/<tarefa>` ou `claude/<tarefa>`) e um PR para a `main`. Ninguém faz push direto na `main`.
- Na descrição do PR: o que mudou, a saída do `pytest -q` e o que ficou sem teste.
- Se discordar do código de outro agente, comente no PR com evidência (comando, saída, medição), não com opinião.
- Nunca commitar mídia (`uploads/`, `saida/`, imagens e áudios dos projetos): já está no `.gitignore`.
- Decisões de produto (o que priorizar, regras) são do dono, não dos agentes.
