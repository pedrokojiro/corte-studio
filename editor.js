// Corte Studio · Editor. Toda mudança passa por mudar(): desfazer/refazer cobre tudo, inclusive sugestões aceitas.
'use strict';
const $ = id => document.getElementById(id);
const esc = t => String(t ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const fmt = t => { t = Math.max(0, t || 0); const m = Math.floor(t / 60); return m + ':' + (t % 60).toFixed(1).padStart(4, '0'); };
const novoId = p => p + Math.random().toString(16).slice(2, 12);

const PLANO_VAZIO = () => ({versao: 1, fontes: {}, segmentos: [], quadro: {crop_x: 0.5},
  legenda: {ativa: true, estilo: {}, frases: []}, titulos: [],
  efeitos: {zoom: {ativo: false, intensidade: 0.06}, transicao: {tipo: 'corte', duracao: 0.2}},
  audio: {volume_original: 1, normalizar: true, musica: {id: null, volume: 0.12, abaixar_na_fala: true}}});

const st = {
  projeto: null, versoes: [{id: 'principal', nome: 'Principal', origem: 'usuario', plano: PLANO_VAZIO()}], ativa: 0,
  hist: [], fut: [], salvo: true, sel: null, mapa: [], eventos: [], durSaida: 0, presets: {}, ini: null, fim: null,
  musNome: '', sugestoes: {}, refs: [], comparacao: null,
};
const plano = () => st.versoes[st.ativa].plano;

// ---------------------------------------------------------------- utilidades de rede
async function api(url, opt = {}) {
  const o = {...opt};
  if (o.json !== undefined) { o.method = o.method || 'POST'; o.headers = {'Content-Type': 'application/json'}; o.body = JSON.stringify(o.json); delete o.json; }
  const r = await fetch(url, o);
  let j; try { j = await r.json(); } catch (e) { j = {erro: `Resposta inesperada do servidor (HTTP ${r.status}).`}; }
  if (!r.ok) throw new Error(j.erro || `HTTP ${r.status}`);
  return j;
}
function msg(t, cls = '') { $('msg').innerHTML = t ? `<div class="${cls}" style="padding:6px 0">${t}</div>` : ''; }
async function enviar(file) {
  const fd = new FormData(); fd.append('arquivo', file);
  return api('/upload', {method: 'POST', body: fd});
}
// Acompanha uma tarefa do servidor: barra, etapa, cancelar. Resolve com o resultado.
function acompanhar(t, alvo, rotulo) {
  return new Promise((ok, falha) => {
    const box = document.createElement('div');
    box.innerHTML = `<div class="row"><span class="mut et">${esc(rotulo)}</span><button class="mini cn">Cancelar</button></div><div class="barra"><i></i></div>`;
    alvo.innerHTML = ''; alvo.appendChild(box);
    box.querySelector('.cn').onclick = () => api(`/api/tarefas/${t.id}/cancelar`, {method: 'POST'}).catch(() => {});
    const passo = async () => {
      let j; try { j = await api(`/api/tarefas/${t.id}`); } catch (e) { box.remove(); return falha(e); }
      box.querySelector('i').style.width = (j.progresso * 100).toFixed(0) + '%';
      box.querySelector('.et').textContent = `${rotulo}: ${j.etapa} (${(j.progresso * 100).toFixed(0)}%)`;
      if (j.estado === 'concluida') { box.remove(); return ok(j.resultado); }
      if (j.estado === 'erro') { box.remove(); return falha(new Error(j.erro)); }
      if (j.estado === 'cancelada') { box.remove(); return falha(new Error('Cancelado.')); }
      setTimeout(passo, 600);
    };
    passo();
  });
}

// ---------------------------------------------------------------- histórico e mudanças
function retrato() { return JSON.stringify({v: st.versoes, a: st.ativa}); }
function mudar(fn) {
  st.hist.push(retrato()); if (st.hist.length > 150) st.hist.shift();
  st.fut = [];
  fn(plano());
  marcarAlterado(); tudo(); validarDepois();
}
function desfazer() { if (!st.hist.length) return; st.fut.push(retrato()); restaurar(st.hist.pop()); }
function refazer() { if (!st.fut.length) return; st.hist.push(retrato()); restaurar(st.fut.pop()); }
function restaurar(s) { const o = JSON.parse(s); st.versoes = o.v; st.ativa = o.a; marcarAlterado(); tudo(); validarDepois(); }
function marcarAlterado() { st.salvo = false; $('estadoSalvo').textContent = 'alterações não salvas'; $('estadoSalvo').className = 'warn'; }

// O servidor é a referência: valida, alinha à grade de quadros e devolve o mapa de saída e os eventos de legenda.
let tVal = null;
function validarDepois() { clearTimeout(tVal); tVal = setTimeout(validar, 250); }
async function validar() {
  try {
    const j = await api('/api/plano/validar', {json: {plano: plano()}});
    st.versoes[st.ativa].plano = j.plano; st.mapa = j.segmentos; st.eventos = j.eventos; st.durSaida = j.duracao_saida;
    tudo(); miniaturasDepois();
  } catch (e) { msg('Plano com problema: ' + esc(e.message), 'bad'); }
}

// ---------------------------------------------------------------- fontes e player
const vid = $('vid');
function fonteVisivel() { return $('fonteVis').value || Object.keys(plano().fontes)[0] || ''; }
$('arqFonte').onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  msg('Enviando o vídeo…');
  try {
    const j = await enviar(f);
    if (!j.largura) throw new Error('Esse arquivo não tem vídeo. Use-o como música na aba Efeitos e áudio.');
    mudar(p => {
      p.fontes[j.id] = {nome: j.nome, duracao: j.duracao, tem_audio: j.tem_audio, largura: j.largura, altura: j.altura};
      p.segmentos.push({id: novoId('s'), fonte: j.id, ini: 0, fim: j.duracao});
    });
    $('fonteVis').value = j.id; trocarFonte();
    msg(`Fonte adicionada (${fmt(j.duracao)}). Ela entrou inteira como um segmento: corte, divida e reordene à vontade.`, 'ok');
  } catch (err) { msg(esc(err.message), 'bad'); }
  e.target.value = '';
};
function trocarFonte() {
  const fid = fonteVisivel(); const f = plano().fontes[fid];
  if (!fid) { vid.removeAttribute('src'); $('infoFonte').textContent = ''; return; }
  if (!vid.src.endsWith('/media/' + fid)) vid.src = '/media/' + fid;
  $('infoFonte').textContent = f ? `${f.largura}x${f.altura} · ${fmt(f.duracao)}${f.tem_audio ? '' : ' · sem áudio'}` : '';
}
$('fonteVis').onchange = trocarFonte;
vid.addEventListener('timeupdate', () => {
  $('cursor').textContent = fmt(vid.currentTime);
  if (st.pararEm != null && vid.currentTime >= st.pararEm) { vid.pause(); st.pararEm = null; }
});
vid.addEventListener('loadedmetadata', moverCrop); window.addEventListener('resize', moverCrop);
function moverCrop() {
  const w = vid.clientWidth, h = vid.clientHeight, c = $('crop'); if (!w || !vid.videoWidth) { c.hidden = true; return; }
  const ar = vid.videoWidth / vid.videoHeight, vw = Math.min(w, h * ar), vh = vw / ar, ox = (w - vw) / 2;
  const bw = Math.min(vw, vh * 9 / 16); c.hidden = false;
  c.style.width = bw + 'px'; c.style.left = (ox + (vw - bw) * plano().quadro.crop_x) + 'px';
  c.style.top = ((h - vh) / 2) + 'px'; c.style.bottom = 'auto'; c.style.height = vh + 'px';
}
function tocar(fonte, ini, fim) { $('fonteVis').value = fonte; trocarFonte(); vid.currentTime = ini; st.pararEm = fim; vid.play(); }

$('bIni').onclick = () => { st.ini = vid.currentTime; marcas(); };
$('bFim').onclick = () => { st.fim = vid.currentTime; marcas(); };
function marcas() { $('marcas').textContent = `marcado: ${st.ini != null ? fmt(st.ini) : '–'} → ${st.fim != null ? fmt(st.fim) : '–'}`; }
$('bAddSeg').onclick = () => {
  const fid = fonteVisivel(); if (!fid) return msg('Adicione uma fonte primeiro.', 'warn');
  const ini = st.ini ?? 0, fim = st.fim ?? Math.min(plano().fontes[fid].duracao, ini + 5);
  if (!(fim - ini >= 0.1)) return msg('O fim precisa ser depois do início.', 'warn');
  const id = novoId('s');
  mudar(p => p.segmentos.push({id, fonte: fid, ini, fim})); st.sel = id; st.ini = st.fim = null; marcas();
};
function dividir() {
  const i = plano().segmentos.findIndex(s => s.id === st.sel);
  if (i < 0) return msg('Selecione um segmento na lista para dividir.', 'warn');
  const s = plano().segmentos[i], t = vid.currentTime;
  if (fonteVisivel() !== s.fonte || !(t > s.ini + 0.1 && t < s.fim - 0.1)) return msg('Posicione o cursor do player dentro do segmento selecionado.', 'warn');
  mudar(p => p.segmentos.splice(i, 1, {...s, fim: t}, {id: novoId('s'), fonte: s.fonte, ini: t, fim: s.fim}));
}
$('bDividir').onclick = dividir;

// ---------------------------------------------------------------- linha do tempo e segmentos
const CORES = ['#3a74e0', '#c0567a', '#3fa58a', '#b98a2c', '#7a5fd0', '#d06a3a'];
function corFonte(fid) { return CORES[Object.keys(plano().fontes).indexOf(fid) % CORES.length]; }
function segmentos() {
  const p = plano(), segs = p.segmentos, tl = $('tl'), tb = $('segs');
  const mapa = Object.fromEntries((st.mapa || []).map(m => [m.id, m]));
  const total = segs.reduce((a, s) => a + (s.fim - s.ini), 0) || 1;
  tl.innerHTML = segs.map((s, i) => `<div role="listitem" data-i="${i}" class="${s.id === st.sel ? 'sel' : ''}" style="flex:${(s.fim - s.ini) / total};background:${corFonte(s.fonte)}" title="${esc(fmt(s.ini))}–${esc(fmt(s.fim))}">${i + 1}</div>`).join('');
  $('tlInfo').innerHTML = segs.length ? `Duração de saída: <b class="${st.durSaida >= 61 ? 'ok' : 'warn'}">${st.durSaida.toFixed(1)}s</b> em ${segs.length} segmento(s)` + (st.durSaida < 61 ? ' — abaixo de 61s não conta para o Creator Rewards [DADO, fonte secundária]' : '') : 'Adicione uma fonte para começar.';
  tb.innerHTML = segs.map((s, i) => `<tr data-i="${i}" class="${s.id === st.sel ? 'sel' : ''}">
    <td><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${corFonte(s.fonte)}"></span> ${i + 1}</td>
    <td class="mut">${mapa[s.id] ? fmt(mapa[s.id].out_ini) : '…'}</td>
    <td><input type="number" step="0.1" min="0" value="${s.ini.toFixed(2)}" data-campo="ini" aria-label="início do segmento ${i + 1}"> <button class="mini" data-acao="iniCursor" title="início = cursor">⟵</button></td>
    <td><input type="number" step="0.1" min="0" value="${s.fim.toFixed(2)}" data-campo="fim" aria-label="fim do segmento ${i + 1}"> <button class="mini" data-acao="fimCursor" title="fim = cursor">⟶</button></td>
    <td>${(s.fim - s.ini).toFixed(1)}s</td>
    <td style="white-space:nowrap"><button class="mini" data-acao="tocar" title="tocar trecho">▶</button>
      <button class="mini" data-acao="sobe" title="mover para cima">↑</button><button class="mini" data-acao="desce" title="mover para baixo">↓</button>
      <button class="mini" data-acao="remove" title="remover da linha do tempo">✕</button></td></tr>`).join('');
}
$('tl').onclick = e => { const d = e.target.closest('[data-i]'); if (!d) return; const s = plano().segmentos[+d.dataset.i]; st.sel = s.id; segmentos(); tocar(s.fonte, s.ini, s.fim); };
$('segs').onclick = e => {
  const tr = e.target.closest('tr'); if (!tr) return; const i = +tr.dataset.i, s = plano().segmentos[i];
  st.sel = s.id;
  const b = e.target.closest('button'); if (!b) { segmentos(); return; }
  const a = b.dataset.acao;
  if (a === 'tocar') return tocar(s.fonte, s.ini, s.fim);
  if (a === 'sobe' && i > 0) mudar(p => p.segmentos.splice(i - 1, 2, p.segmentos[i], p.segmentos[i - 1]));
  if (a === 'desce' && i < plano().segmentos.length - 1) mudar(p => p.segmentos.splice(i, 2, p.segmentos[i + 1], p.segmentos[i]));
  if (a === 'remove') mudar(p => p.segmentos.splice(i, 1));
  if (a === 'iniCursor' || a === 'fimCursor') {
    if (fonteVisivel() !== s.fonte) return msg('O player está em outra fonte.', 'warn');
    const t = vid.currentTime; mudar(p => { p.segmentos[i][a === 'iniCursor' ? 'ini' : 'fim'] = t; });
  }
};
$('segs').onchange = e => {
  const inp = e.target.closest('input[data-campo]'); if (!inp) return;
  const i = +inp.closest('tr').dataset.i, v = parseFloat(inp.value);
  if (!isFinite(v)) return segmentos();
  mudar(p => { p.segmentos[i][inp.dataset.campo] = v; });
};

// sugestão de cortes (cenas e pausas) da fonte visível
$('bSug').onclick = async () => {
  const fid = fonteVisivel(); if (!fid) return msg('Adicione uma fonte primeiro.', 'warn');
  $('sugCortes').innerHTML = '<span class="mut">Analisando trocas de cena e pausas…</span>';
  try {
    const j = await api('/analisar', {json: {video_id: fid, min: +$('sMin').value, max: +$('sMax').value}});
    st.sugCortes = j.sugestoes;
    $('sugCortes').innerHTML = `<div class="mut">${j.cenas} trocas de cena, ${j.pausas} pausas. ${j.sugestoes.length} sugestões (onde dá para cortar limpo; não avalia o conteúdo).</div>` +
      j.sugestoes.map((c, i) => `<div class="row"><span class="mut">${fmt(c.ini)}–${fmt(c.fim)} (${c.dur.toFixed(1)}s) · ${esc(c.motivo)}</span>
        <button class="mini" data-v="${i}">▶</button><button class="mini" data-a="${i}">+ adicionar</button></div>`).join('');
  } catch (e) { $('sugCortes').innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
};
$('sugCortes').onclick = e => {
  const b = e.target.closest('button'); if (!b) return; const fid = fonteVisivel();
  if (b.dataset.v !== undefined) { const c = st.sugCortes[+b.dataset.v]; return tocar(fid, c.ini, c.fim); }
  const c = st.sugCortes[+b.dataset.a]; mudar(p => p.segmentos.push({id: novoId('s'), fonte: fid, ini: c.ini, fim: c.fim})); b.disabled = true;
};

// ---------------------------------------------------------------- legenda
function transcricaoBox() {
  const fontes = Object.entries(plano().fontes);
  $('transBox').innerHTML = fontes.length ? fontes.map(([fid, f]) => `<div class="row" style="margin:4px 0" data-f="${fid}">
      <span class="mut" style="flex:1">${esc(f.nome)}: ${plano().legenda.frases.some(x => x.fonte === fid) ? `<span class="ok">${plano().legenda.frases.filter(x => x.fonte === fid).length} frases</span>` : 'sem legenda'}</span>
      <button class="mini" data-acao="transcrever">Transcrever e gerar frases</button></div><div data-prog="${fid}"></div>`).join('')
    : '<div class="mut">Adicione uma fonte para transcrever.</div>';
}
$('transBox').onclick = async e => {
  const b = e.target.closest('button[data-acao="transcrever"]'); if (!b) return;
  const fid = b.closest('[data-f]').dataset.f;
  const jaTem = plano().legenda.frases.some(x => x.fonte === fid);
  if (jaTem && !confirm('Gerar as frases de novo substitui as frases desta fonte (inclusive as suas edições). Continuar? Dá para desfazer.')) return;
  b.disabled = true;
  try {
    const t = await api('/api/transcricao', {json: {fonte: fid}});
    const r = await acompanhar(t, document.querySelector(`[data-prog="${fid}"]`), 'Transcrição');
    const j = await api('/api/legenda/frases', {json: {fonte: fid, palavras_por_frase: plano().legenda.estilo.palavras_por_frase || 3}});
    mudar(p => { p.legenda.frases = p.legenda.frases.filter(x => x.fonte !== fid).concat(j.frases); });
    msg(`Transcrição ${r.em_cache ? 'reaproveitada do cache' : 'pronta'}: ${r.palavras} palavras, ${j.frases.length} frases. Confira o texto: o reconhecimento erra às vezes.`, 'ok');
  } catch (err) { msg('Transcrição: ' + esc(err.message), 'bad'); }
  b.disabled = false;
};
$('legAtiva').onchange = () => mudar(p => { p.legenda.ativa = $('legAtiva').checked; });

function presets() {
  const e = plano().legenda.estilo;
  $('presets').innerHTML = Object.entries(st.presets).map(([k, v]) => `<div class="preset ${e.preset === k ? 'on' : ''}" data-p="${k}" role="button" tabindex="0">
    <img alt="Prévia do estilo ${esc(v.nome)}" src="${st.mini?.[k] || ''}"><div>${esc(v.nome)}</div></div>`).join('') +
    (e.preset === 'personalizado' ? `<div class="preset on"><img alt="Prévia do estilo atual" src="${st.mini?.atual || ''}"><div>Personalizado</div></div>` : '');
}
$('presets').onclick = e => { const d = e.target.closest('[data-p]'); if (!d) return; const v = {...st.presets[d.dataset.p]}; delete v.nome; mudar(p => { p.legenda.estilo = v; }); };
let tMini = null;
function miniaturasDepois() { clearTimeout(tMini); tMini = setTimeout(miniaturas, 500); }
async function miniaturas() {
  if (!Object.keys(plano().fontes).length || !Object.keys(st.presets).length) return;
  const estilos = {}; for (const [k, v] of Object.entries(st.presets)) { const c = {...v}; delete c.nome; estilos[k] = c; }
  if (plano().legenda.estilo.preset === 'personalizado') estilos.atual = plano().legenda.estilo;
  const chave = JSON.stringify([estilos, plano().segmentos.slice(0, 3), plano().legenda.frases.slice(0, 50).map(f => f.id), plano().quadro]);
  if (chave === st.miniChave) return; st.miniChave = chave;
  try { st.mini = await api('/api/miniaturas', {json: {plano: plano(), estilos}}); presets(); } catch (e) { /* miniatura é opcional */ }
}
const CAMPOS = {eFonte: 'fonte', eTamanho: 'tamanho', eCor: 'cor', eContorno: 'contorno', eContornoCor: 'contorno_cor', eFundo: 'fundo',
  eFundoCor: 'fundo_cor', eFundoOp: 'fundo_opacidade', ePos: 'posicao_y', eMai: 'maiusculas', eNeg: 'negrito', eDest: 'destaque', eDestCor: 'destaque_cor', ePpf: 'palavras_por_frase'};
function estiloForm() {
  const e = plano().legenda.estilo;
  for (const [id, k] of Object.entries(CAMPOS)) { const el = $(id); if (document.activeElement === el) continue; if (el.type === 'checkbox') el.checked = !!e[k]; else if (e[k] !== undefined) el.value = e[k]; }
  $('ePosV').textContent = Math.round((e.posicao_y || 0) * 100) + '% do topo';
}
for (const [id, k] of Object.entries(CAMPOS)) {
  $(id).addEventListener('change', () => {
    const el = $(id); let v = el.type === 'checkbox' ? el.checked : el.type === 'number' || el.type === 'range' ? parseFloat(el.value) : el.type === 'color' ? el.value.toUpperCase() : el.value;
    mudar(p => { p.legenda.estilo = {...p.legenda.estilo, [k]: v, preset: 'personalizado'}; });
  });
}

function frases() {
  const p = plano(), porId = Object.fromEntries(p.legenda.frases.map(f => [f.id, f]));
  if (!st.eventos.length) { $('frases').innerHTML = `<div class="mut" style="padding:6px">${p.legenda.frases.length ? 'Nenhuma frase cai dentro dos segmentos atuais.' : 'Sem frases: transcreva a fonte acima.'}</div>`; return; }
  $('frases').innerHTML = st.eventos.map(ev => { const f = porId[ev.frase]; if (!f) return '';
    return `<div class="frase" data-f="${f.id}"><button class="mini" data-acao="ouvir" title="tocar este trecho">${fmt(ev.ini)}</button>
      <input type="text" value="${esc(f.palavras.map(w => w.t).join(' '))}" aria-label="Texto da frase em ${fmt(ev.ini)}">
      <span style="white-space:nowrap"><button class="mini" data-acao="antes" title="mostrar 0,1s antes">−</button><button class="mini" data-acao="depois" title="mostrar 0,1s depois">+</button>
      <button class="mini" data-acao="juntar" title="juntar com a próxima">⤵</button><button class="mini" data-acao="remover" title="não mostrar esta frase">✕</button></span></div>`; }).join('');
}
$('frases').onclick = e => {
  const b = e.target.closest('button'); if (!b) return; const id = b.closest('[data-f]').dataset.f;
  const i = plano().legenda.frases.findIndex(f => f.id === id), f = plano().legenda.frases[i];
  if (b.dataset.acao === 'ouvir') return tocar(f.fonte, f.palavras[0].ini, f.palavras.at(-1).fim + 0.2);
  if (b.dataset.acao === 'antes' || b.dataset.acao === 'depois') {
    const d = b.dataset.acao === 'antes' ? -0.1 : 0.1;
    return mudar(p => { p.legenda.frases[i].palavras = f.palavras.map(w => ({...w, ini: Math.max(0, +(w.ini + d).toFixed(3)), fim: Math.max(0, +(w.fim + d).toFixed(3))})); });
  }
  if (b.dataset.acao === 'remover') return mudar(p => p.legenda.frases.splice(i, 1));
  if (b.dataset.acao === 'juntar') {
    const j = plano().legenda.frases.findIndex((x, k) => k > i && x.fonte === f.fonte && x.palavras[0].ini >= f.palavras.at(-1).ini);
    if (j < 0) return msg('Não há frase seguinte da mesma fonte para juntar.', 'warn');
    if (f.palavras.length + plano().legenda.frases[j].palavras.length > 60) return msg('A frase juntada passaria de 60 palavras.', 'warn');
    mudar(p => { p.legenda.frases[i].palavras = f.palavras.concat(p.legenda.frases[j].palavras); p.legenda.frases.splice(j, 1); });
  }
};
$('frases').onchange = async e => {
  const inp = e.target.closest('input'); if (!inp) return; const id = inp.closest('[data-f]').dataset.f;
  const i = plano().legenda.frases.findIndex(f => f.id === id), f = plano().legenda.frases[i];
  const partes = inp.value.split('|').map(x => x.trim()).filter(Boolean);
  try {
    if (partes.length <= 1) {
      const j = await api('/api/legenda/editar', {json: {frase: f, texto: partes[0] || ''}});
      return mudar(p => { p.legenda.frases[i] = j.frase; });
    }
    // dividir: realinha o texto inteiro e corta pelo número de palavras de cada parte
    const j = await api('/api/legenda/editar', {json: {frase: f, texto: partes.join(' ')}});
    let k = 0; const novas = partes.map(pt => { const n = pt.split(/\s+/).length; const ws = j.frase.palavras.slice(k, k + n); k += n; return {id: novoId('f'), fonte: f.fonte, palavras: ws}; });
    mudar(p => p.legenda.frases.splice(i, 1, ...novas.filter(x => x.palavras.length)));
  } catch (err) { msg(esc(err.message), 'bad'); frases(); }
};

// ---------------------------------------------------------------- títulos
function titulos() {
  const p = plano();
  $('titulos').innerHTML = p.titulos.map((t, i) => `<div class="row" data-i="${i}" style="margin:4px 0">
    <span class="tag ${t.origem === 'usuario' ? '' : t.origem === 'sugestao_ia' ? 'ia' : 'loc'}">${t.origem === 'usuario' ? 'seu' : t.origem === 'sugestao_ia' ? 'sugerido por IA' : 'sugerido (local)'}</span>
    <input type="text" value="${esc(t.texto)}" data-campo="texto" style="flex:1;min-width:140px" maxlength="90" aria-label="Texto do título">
    <input type="number" step="0.1" min="0" value="${t.ini}" data-campo="ini" aria-label="início (saída)"> a
    <input type="number" step="0.1" min="0" value="${t.fim}" data-campo="fim" aria-label="fim (saída)"> s
    <button class="mini" data-acao="remove">✕</button></div>`).join('') || '<div class="mut">Nenhum título.</div>';
}
$('bAddTit').onclick = () => mudar(p => p.titulos.push({id: novoId('t'), texto: 'Seu título', ini: 0, fim: 3, origem: 'usuario'}));
$('titulos').onchange = e => { const inp = e.target.closest('input'); if (!inp) return; const i = +inp.closest('[data-i]').dataset.i, k = inp.dataset.campo;
  mudar(p => { p.titulos[i][k] = k === 'texto' ? inp.value : parseFloat(inp.value); }); };
$('titulos').onclick = e => { const b = e.target.closest('button[data-acao="remove"]'); if (!b) return; const i = +b.closest('[data-i]').dataset.i; mudar(p => p.titulos.splice(i, 1)); };

// ---------------------------------------------------------------- efeitos e áudio
function efeitos() {
  const p = plano(), set = (id, v) => { if (document.activeElement !== $(id)) $(id).value = v; };
  set('cropX', p.quadro.crop_x); $('zAtivo').checked = p.efeitos.zoom.ativo; set('zInt', p.efeitos.zoom.intensidade);
  $('zV').textContent = Math.round(p.efeitos.zoom.intensidade * 100) + '%'; $('trTipo').value = p.efeitos.transicao.tipo; set('trDur', p.efeitos.transicao.duracao);
  set('vo', p.audio.volume_original); $('voV').textContent = Math.round(p.audio.volume_original * 100) + '%'; $('norm').checked = p.audio.normalizar;
  set('vm', p.audio.musica.volume); $('vmV').textContent = Math.round(p.audio.musica.volume * 100) + '%'; $('duck').checked = p.audio.musica.abaixar_na_fala;
  $('musInfo').textContent = p.audio.musica.id ? (st.musNome || 'música carregada') : 'sem música';
  moverCrop();
}
const liga = (id, fn, ev = 'change') => $(id).addEventListener(ev, () => mudar(p => fn(p, $(id))));
liga('cropX', (p, el) => { p.quadro.crop_x = +el.value; });
liga('zAtivo', (p, el) => { p.efeitos.zoom.ativo = el.checked; });
liga('zInt', (p, el) => { p.efeitos.zoom.intensidade = +el.value; });
liga('trTipo', (p, el) => { p.efeitos.transicao.tipo = el.value; });
liga('trDur', (p, el) => { p.efeitos.transicao.duracao = +el.value; });
liga('vo', (p, el) => { p.audio.volume_original = +el.value; });
liga('norm', (p, el) => { p.audio.normalizar = el.checked; });
liga('vm', (p, el) => { p.audio.musica.volume = +el.value; });
liga('duck', (p, el) => { p.audio.musica.abaixar_na_fala = el.checked; });
$('cropX').addEventListener('input', () => { plano().quadro.crop_x = +$('cropX').value; moverCrop(); });
$('arqMus').onchange = async e => { const f = e.target.files[0]; if (!f) return;
  try { const j = await enviar(f); if (!j.tem_audio) throw new Error('Esse arquivo não tem áudio.'); st.musNome = j.nome; mudar(p => { p.audio.musica.id = j.id; }); } catch (err) { msg(esc(err.message), 'bad'); }
  e.target.value = ''; };
$('bSemMus').onclick = () => mudar(p => { p.audio.musica.id = null; });

// ---------------------------------------------------------------- prévia, exportação e comparação de versões
async function renderizar(qualidade, plan, alvo, nome) {
  const t = await api('/api/render', {json: {plano: plan, qualidade, nome}});
  return acompanhar(t, alvo, qualidade === 'previa' ? 'Prévia' : 'Exportação');
}
function blocoVideo(r, titulo) {
  return `<div class="mut" style="margin:6px 0">${esc(titulo || r.rotulo)} · ${r.duracao.toFixed(1)}s${r.em_cache ? ' · do cache (plano igual)' : ''}</div>
    <video controls src="${r.url}"></video>${(r.avisos || []).map(a => `<div class="warn">⚠ ${esc(a)}</div>`).join('')}`;
}
$('bPrevia').onclick = async () => {
  $('bPrevia').disabled = true;
  try { const r = await renderizar('previa', plano(), $('tarefa')); $('previaBox').innerHTML = blocoVideo(r); }
  catch (e) { $('previaBox').innerHTML = `<div class="bad">${esc(e.message)}</div>`; }
  $('bPrevia').disabled = false;
};
$('bExportar').onclick = async () => {
  $('bExportar').disabled = true;
  try { const r = await renderizar('final', plano(), $('tarefa'), $('pNome').value || 'edicao');
    $('previaBox').innerHTML = blocoVideo(r) + `<p><a href="${r.url}?dl=1" style="color:var(--acc)">Baixar ${esc(r.arquivo)}</a> <span class="mut">(também em saida/, com um .json do plano)</span></p>`; }
  catch (e) { $('previaBox').innerHTML = `<div class="bad">${esc(e.message)}</div>`; }
  $('bExportar').disabled = false;
};
$('bComparaVers').onclick = async () => {
  if (st.versoes.length < 2) return msg('Crie uma versão alternativa (Duplicar versão ou aplicar uma sugestão) para comparar.', 'warn');
  const outra = st.ativa === 0 ? 1 : 0, a = st.versoes[st.ativa], b = st.versoes[outra];
  try {
    const ra = await renderizar('previa', a.plano, $('tarefa')), rb = await renderizar('previa', b.plano, $('tarefa'));
    $('previaBox').innerHTML = `<div class="cmp"><div>${blocoVideo(ra, a.nome)}</div><div>${blocoVideo(rb, b.nome)}</div></div>`;
  } catch (e) { $('previaBox').innerHTML = `<div class="bad">${esc(e.message)}</div>`; }
};

// ---------------------------------------------------------------- versões
function versoes() {
  $('versao').innerHTML = st.versoes.map((v, i) => `<option value="${i}" ${i === st.ativa ? 'selected' : ''}>${esc(v.nome)}</option>`).join('');
  $('versao').title = st.versoes[st.ativa].origem || '';
}
$('versao').onchange = () => { st.hist.push(retrato()); st.fut = []; st.ativa = +$('versao').value; st.sel = null; tudo(); validarDepois(); };
function novaVersao(nome, origem, fn) {
  st.hist.push(retrato()); st.fut = [];
  const plan = JSON.parse(JSON.stringify(plano())); fn && fn(plan);
  st.versoes.push({id: novoId('v'), nome, origem, plano: plan}); st.ativa = st.versoes.length - 1;
  marcarAlterado(); tudo(); validarDepois();
}
$('bDuplicar').onclick = () => novaVersao(`Alternativa ${st.versoes.length}`, 'cópia feita por você');

// ---------------------------------------------------------------- projetos
async function listarProjetos() {
  try { const l = await api('/api/projetos');
    $('pLista').innerHTML = '<option value="">Abrir projeto…</option>' + l.map(p => `<option value="${p.id}">${esc(p.nome)} · ${esc(p.atualizado_em || '')}</option>`).join('');
  } catch (e) { /* lista é opcional */ }
}
async function salvar() {
  const corpo = {nome: $('pNome').value || 'Sem nome', plano: st.versoes[0].plano, alternativas: st.versoes.slice(1)};
  try {
    const j = st.projeto ? await api(`/api/projetos/${st.projeto}`, {method: 'PUT', json: corpo}) : await api('/api/projetos', {json: corpo});
    st.projeto = j.id; st.salvo = true; $('estadoSalvo').textContent = 'salvo ' + j.atualizado_em; $('estadoSalvo').className = 'ok';
    history.replaceState(null, '', '/editor?p=' + j.id); listarProjetos();
  } catch (e) { msg('Não salvou: ' + esc(e.message), 'bad'); }
}
async function abrir(pid) {
  if (!st.salvo && !confirm('Há alterações não salvas. Abrir outro projeto mesmo assim?')) return;
  try {
    const j = await api('/api/projetos/' + pid);
    st.projeto = j.id; $('pNome').value = j.nome;
    st.versoes = [{id: 'principal', nome: 'Principal', origem: 'usuario', plano: j.plano}, ...j.alternativas];
    st.ativa = 0; st.hist = []; st.fut = []; st.sel = null; st.salvo = true;
    $('estadoSalvo').textContent = 'aberto'; $('estadoSalvo').className = 'ok';
    history.replaceState(null, '', '/editor?p=' + j.id);
    $('fonteVis').value = Object.keys(j.plano.fontes)[0] || ''; tudo(); trocarFonte(); validarDepois();
    if (j.fontes_faltando.length) msg(`Arquivos de fonte não encontrados em uploads/ (talvez apagados na limpeza): ${j.fontes_faltando.join(', ')}. Envie o vídeo de novo e troque a fonte.`, 'warn');
    if (window.carregarRefs) window.carregarRefs();
  } catch (e) { msg('Não abriu: ' + esc(e.message), 'bad'); }
}
$('pLista').onchange = () => { const v = $('pLista').value; if (v) abrir(v); $('pLista').value = ''; };
$('bSalvar').onclick = salvar;
$('bNovo').onclick = () => {
  if (!st.salvo && !confirm('Há alterações não salvas. Começar outro projeto mesmo assim?')) return;
  st.projeto = null; $('pNome').value = ''; st.versoes = [{id: 'principal', nome: 'Principal', origem: 'usuario', plano: PLANO_VAZIO()}];
  st.ativa = 0; st.hist = []; st.fut = []; st.salvo = true; history.replaceState(null, '', '/editor'); tudo(); validarDepois();
};
$('pNome').onchange = marcarAlterado;
$('bDesfazer').onclick = desfazer; $('bRefazer').onclick = refazer;
window.addEventListener('beforeunload', e => { if (!st.salvo) { e.preventDefault(); e.returnValue = ''; } });
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); return salvar(); }
  const t = e.target.tagName; if (t === 'INPUT' || t === 'TEXTAREA' || t === 'SELECT') return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); return desfazer(); }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') { e.preventDefault(); return refazer(); }
  if (e.key === 'i' || e.key === 'I') $('bIni').click();
  else if (e.key === 'o' || e.key === 'O') $('bFim').click();
  else if (e.key === 'Enter') $('bAddSeg').click();
  else if (e.key === 's' || e.key === 'S') dividir();
  else if (e.key === 'Delete' && st.sel) { const i = plano().segmentos.findIndex(s => s.id === st.sel); if (i >= 0) mudar(p => p.segmentos.splice(i, 1)); }
});

// ---------------------------------------------------------------- abas e desenho geral
document.querySelector('.tabs').onclick = e => { const b = e.target.closest('button[data-tab]'); if (!b) return;
  document.querySelectorAll('.tabs button').forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('.tab').forEach(x => x.classList.toggle('on', x.id === b.dataset.tab)); };
function tudo() {
  const p = plano(), atual = $('fonteVis').value;
  $('fonteVis').innerHTML = Object.entries(p.fontes).map(([fid, f]) => `<option value="${fid}">${esc(f.nome)}</option>`).join('');
  if (atual && p.fontes[atual]) $('fonteVis').value = atual;
  $('legAtiva').checked = p.legenda.ativa;
  $('bDesfazer').disabled = !st.hist.length; $('bRefazer').disabled = !st.fut.length;
  versoes(); segmentos(); transcricaoBox(); presets(); estiloForm(); frases(); titulos(); efeitos();
  if (window.desenharGanchos) window.desenharGanchos();
}

(async function iniciar() {
  try { const j = await api('/api/presets'); st.presets = j.presets; } catch (e) { msg('Não carreguei os estilos: ' + esc(e.message), 'bad'); }
  const v = {...st.presets.classico}; delete v.nome; st.versoes[0].plano.legenda.estilo = v;
  tudo(); listarProjetos();
  const pid = new URLSearchParams(location.search).get('p');
  if (pid) abrir(pid); else validarDepois();
  if (window.iniciarReferencias) window.iniciarReferencias();
})();
