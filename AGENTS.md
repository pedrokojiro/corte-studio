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
```
iniciar.bat                 # Windows: checa Python e ffmpeg, instala dependências, sobe o app
python -m pytest -q         # 47 testes; precisam de ffmpeg no PATH. edge-tts e Whisper reais são pulados offline
python tests/medir_sync.py  # mede a defasagem da legenda (precisa de internet)
```
Ambiente de referência: Windows 10, Python 3.13 (Anaconda), ffmpeg 9 (gyan.dev), edge-tts 7.2.8, faster-whisper 1.2.1 (modelo `small`, CPU int8), sem GPU.

## Regras (definidas pelo dono do projeto)
1. ~~Origem do material obrigatória~~: **revogada pelo dono em 2026-10-11**. A origem não aparece mais na UI. O backend ainda aceita os campos opcionais `origem` e `prova`.
2. **Proibido**: login, postagem automática, API não oficial do TikTok, compra de engajamento ou qualquer coisa que arrisque a conta. Também não implementar download ou scraping de vídeo.
3. Manter o lembrete do toggle "conteúdo gerado por IA" e o aviso de vídeo com menos de 61s.
4. **Anti-overbuilding**: só o que foi pedido. Sem frameworks pesados, sem banco de dados, sem refatorar o que funciona.
5. Marcar afirmações como [FATO], [DADO], [HIPÓTESE], [ESTIMATIVA] ou [INFERÊNCIA]. Não apresentar estimativa como fato. Apontar contradições nos pedidos.
6. Ao fim de cada tarefa: **rodar os testes** e resumir em 3 linhas (o que funcionou, o que quebrou, o que ficou sem teste). Nunca declarar "pronto" sem ter executado.

## Armadilhas conhecidas (não "consertar" sem medir)
- **Windows**: `subprocess` usa `encoding="utf-8", errors="replace"`, porque o padrão cp1252 quebra com caminho acentuado.
- **Legenda ASS**: o filtro roda com `cwd` na pasta de trabalho e caminho relativo (`ass=legenda.ass`), o que evita escapar o `C:` no filtro.
- **edge-tts 7.x**: o padrão é `SentenceBoundary`. Para ter tempo por palavra, é preciso pedir `boundary="WordBoundary"`.
- **faster-whisper + Anaconda**: o numpy (MKL) e o ctranslate2 carregam OpenMP duas vezes e o processo aborta. O app define `KMP_DUPLICATE_LIB_OK=TRUE`. O áudio é decodificado pelo ffmpeg, não pelo módulo `av` (o av 19 é incompatível com o faster-whisper 1.2.1).
- **Whisper com cortes emendados** pulou fala repetida. Por isso cada corte é transcrito separado e os tempos são somados.
- **Velocidade de fala**: o pipeline estima 15 car/s [ESTIMATIVA]. Medido com Antonio a -5%: 12,2 car/s [DADO, 1 medição].

## Como colaborar entre agentes
- Uma **branch por tarefa** (`codex/<tarefa>` ou `claude/<tarefa>`) e um PR para a `main`. Ninguém faz push direto na `main`.
- Na descrição do PR: o que mudou, a saída do `pytest -q` e o que ficou sem teste.
- Se discordar do código de outro agente, comente no PR com evidência (comando, saída, medição), não com opinião.
- Nunca commitar mídia (`uploads/`, `saida/`, imagens e áudios dos projetos): já está no `.gitignore`.
- Decisões de produto (o que priorizar, regras) são do dono, não dos agentes.
