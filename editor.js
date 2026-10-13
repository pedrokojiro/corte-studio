// Corte Studio · Editor. Toda mudança passa por mudar(): desfazer/refazer cobre tudo, inclusive sugestões aceitas.
'use strict';
const $ = id => document.getElementById(id);
const esc = t => String(t ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const fmt = t => { t = Math.max(0, t || 0); const m = Math.floor(t / 60); return m + ':' + (t % 60).toFixed(1).padStart(4, '0'); };
const novoId = p => p + Math.random().toString(16).slice(2, 12);

const PLANO_VAZIO = () => ({versao: 1, fontes: {}, segmentos: [], quadro: {crop_x: 0.5},
  legenda: {ativa: true, estilo: {}, frases: []}, titulos: [],
  efeitos: {zoom: {ativo: false, intensidade: 0.06}, transicao: {tipo: 'corte', duracao: 0.2}},
  audio: {volume_original: 1, normalizar: true, realce_voz: false, efeitos: [], musica: {id: null, volume: 0.12, abaixar_na_fala: true}}});

const st = {
  projeto: null, versoes: [{id: 'principal', nome: 'Principal', origem: 'usuario', plano: PLANO_VAZIO()}], ativa: 0,
  hist: [], fut: [], salvo: true, sel: null, mapa: [], eventos: [], durSaida: 0, presets: {}, ini: null, fim: null,
  musNome: '', sugestoes: {}, avisosLeg: [], efsOut: [], sons: {}, restrito: null,
  publicacao: {texto: ''}, sugTexto: {}, estilosTit: {},
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
function acompanhar(t, alvo, rotulo, aoParcial) {
  return new Promise((ok, falha) => {
    const box = document.createElement('div');
    box.innerHTML = `<div class="row"><span class="mut et">${esc(rotulo)}</span><button class="mini cn">Cancelar</button></div><div class="barra"><i></i></div>`;
    alvo.innerHTML = ''; alvo.appendChild(box);
    box.querySelector('.cn').onclick = () => api(`/api/tarefas/${t.id}/cancelar`, {method: 'POST'}).catch(() => {});
    const passo = async () => {
      let j; try { j = await api(`/api/tarefas/${t.id}`); } catch (e) { box.remove(); return falha(e); }
      box.querySelector('i').style.width = (j.progresso * 100).toFixed(0) + '%';
      box.querySelector('.et').textContent = `${rotulo}: ${j.etapa} (${(j.progresso * 100).toFixed(0)}%)`;
      if (aoParcial && j.parcial) { const k = JSON.stringify(j.parcial); if (k !== box.dataset.parcial) { box.dataset.parcial = k; aoParcial(j.parcial); } }
      if (j.estado === 'concluida') { box.remove(); return ok(j.resultado); }
      if (j.estado === 'erro') { box.remove(); return falha(new Error(j.erro)); }
      if (j.estado === 'cancelada') { box.remove(); return falha(new Error('Cancelado.')); }
      setTimeout(passo, 600);
    };
    passo();
  });
}

// ---------------------------------------------------------------- histórico e mudanças
function retrato() { return JSON.stringify({v: st.versoes, a: st.ativa, pub: st.publicacao.texto}); }
function mudar(fn) {
  st.hist.push(retrato()); if (st.hist.length > 150) st.hist.shift();
  st.fut = [];
  fn(plano());
  marcarAlterado(); tudo(); validarDepois();
}
function desfazer() { if (!st.hist.length) return; st.fut.push(retrato()); restaurar(st.hist.pop()); }
function refazer() { if (!st.fut.length) return; st.hist.push(retrato()); restaurar(st.fut.pop()); }
function restaurar(s) { const o = JSON.parse(s); st.versoes = o.v; st.ativa = o.a; st.publicacao.texto = o.pub ?? ''; marcarAlterado(); tudo(); validarDepois(); }
function marcarAlterado() { st.salvo = false; $('estadoSalvo').textContent = 'alterações não salvas'; $('estadoSalvo').className = 'warn'; }

// O servidor é a referência: valida, alinha à grade de quadros e devolve o mapa de saída e os eventos de legenda.
let tVal = null;
function validarDepois() { clearTimeout(tVal); tVal = setTimeout(validar, 250); }
async function validar() {
  try {
    const enviado = JSON.stringify(plano()), ativa = st.ativa;
    const j = await api('/api/plano/validar', {json: {plano: plano()}});
    // o usuário mexeu enquanto o servidor validava: não sobrescreve a mudança nova (outra validação já foi agendada)
    if (st.ativa !== ativa || JSON.stringify(plano()) !== enviado) return;
    st.versoes[st.ativa].plano = j.plano; st.mapa = j.segmentos; st.eventos = j.eventos; st.durSaida = j.duracao_saida;
    st.avisosLeg = j.avisos_legenda || []; st.efsOut = j.efeitos || [];
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
$('fonteVis').onchange = () => { if (PV.vista !== 'original') mudarVista('original', null, false); trocarFonte(); };
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
function tocar(fonte, ini, fim) {
  if (window.PV && PV.vista !== 'original') mudarVista('original', null, false);
  $('fonteVis').value = fonte; trocarFonte(); vid.currentTime = ini; st.pararEm = fim; vid.play().catch(() => {});
}

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
$('tl').onclick = e => { const d = e.target.closest('[data-i]'); if (!d) return; const s = plano().segmentos[+d.dataset.i]; st.sel = s.id; tudo(); tocar(s.fonte, s.ini, s.fim); };
$('segs').onclick = e => {
  const tr = e.target.closest('tr'); if (!tr) return; const i = +tr.dataset.i, s = plano().segmentos[i];
  const trocou = st.sel !== s.id; st.sel = s.id;
  const b = e.target.closest('button'); if (!b) { if (trocou) { tudo(); verFonteDoAtivo(); } return; }
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
  if (!plano().fontes[fid].tem_audio) return msg('Essa fonte não tem áudio: a descoberta por conteúdo precisa de fala.', 'warn');
  $('bSug').disabled = true; st.sugCortes = null; st.sugFonte = fid; st.sugParcial = null;
  // a lista anterior (talvez de OUTRA fonte) sai da tela: nada de resultado velho com cara de novo
  $('sugCortes').innerHTML = `<div class="mut">Analisando “${esc(plano().fontes[fid].nome)}”… as primeiras sugestões aparecem enquanto a fala é transcrita.</div>`;
  try {
    const t = await api('/api/cortes/descobrir', {json: {fonte: fid, min: +$('sMin').value, max: +$('sMax').value, provedor: provedor()}});
    st.sugCortes = await acompanhar(t, $('sugProg'), 'Descoberta de cortes (fonte inteira)', p => { st.sugParcial = p; desenharCortes(); });
    st.sugParcial = null; desenharCortes();
  } catch (e) { $('sugCortes').innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
  $('bSug').disabled = false;
};
const NIVEL = [['bad', 'fraco'], ['warn', 'médio'], ['ok', 'forte']];
function desenharCortes() {
  const r = st.sugCortes || st.sugParcial; if (!r) return;
  const prov = !st.sugCortes;
  const cab = prov
    ? `<div class="row" style="margin:6px 0"><span class="chip warn">Provisório</span><span class="mut">Ranking parcial com o que já foi transcrito (até ${fmt(r.ate_s)} da fonte), pela heurística local. A lista final substitui esta.</span></div>`
    : cabResultado(r) + `<div class="mut">${esc(r.ranking || '')} ${r.em_cache ? '· resultado reaproveitado' : ''}</div>` +
      (r.tempos ? `<div class="mut">Tempo: transcrição ${r.tempos.transcricao_s}s${r.transcricao_em_cache ? ' (cache)' : ''} · cenas ${r.tempos.cenas_s_em_paralelo}s em paralelo · análise ${r.tempos.analise_s}s · total ${r.tempos.total_s}s</div>` : '');
  $('sugCortes').innerHTML = cab + (r.cortes.length ? r.cortes.map((c, i) => `<div class="sug corte ${c.add ? 'aplicada' : ''}" data-c="${i}">
      <div class="row" style="align-items:baseline"><span class="rank" title="posição no ranking editorial">#${c.rank}</span><b class="titulo-corte">${esc(c.titulo)}</b></div>
      <div class="ideia">${esc(c.ideia)}</div>
      <div class="campo"><b>Por que selecionar:</b> ${esc(c.por_que)}</div>
      ${c.acima_do_proximo ? `<div class="campo mut">${esc(c.acima_do_proximo)}</div>` : ''}
      ${c.gancho ? `<div class="campo"><b>Gancho:</b> ${c.gancho.tipo === 'fala' ? `“${esc(c.gancho.texto)}” <span class="mut">(fala em ${fmt(c.gancho.ini)})</span>` : `“${esc(c.gancho.texto)}” <span class="mut">(texto novo na tela)</span>`}</div>` : ''}
      ${c.trecho ? `<div class="cita">“${esc(c.trecho.texto)}”</div>` : ''}
      ${c.nota_duracao ? `<div class="campo warn">⚠ ${esc(c.nota_duracao)}</div>` : ''}
      ${c.limitacoes && c.limitacoes.length ? `<div class="campo warn"><b>Falta de contexto/limites:</b> ${esc(c.limitacoes.join(' '))}</div>` : ''}
      ${c.criterios && c.criterios.length ? `<details><summary class="mut">Critérios do ranking</summary>${c.criterios.map(x => `<div class="campo">${x.nivel != null ? `<span class="chip ${NIVEL[x.nivel][0]}">${NIVEL[x.nivel][1]}</span> ` : ''}<b>${esc(x.nome)}:</b> ${esc(x.texto)}</div>`).join('')}</details>` : ''}
      <div class="row mut" style="margin-top:6px">Fonte <input type="number" step="0.1" min="0" value="${c.ini}" data-c="ini" aria-label="início na fonte"> a
        <input type="number" step="0.1" min="0" value="${c.fim}" data-c="fim" aria-label="fim na fonte"> s · ${(c.fim - c.ini).toFixed(1)}s · ${esc(c.analise || '')}</div>
      <div class="acoes"><button class="mini" data-a="ver" title="Toca o intervalo direto da fonte, sem renderizar">▶ Ver na fonte</button>
        <button class="mini pri" data-a="add" ${c.add ? 'disabled' : ''}>${c.add ? 'na edição' : '+ Adicionar à edição'}</button></div></div>`).join('')
    : `<div class="mut">${prov ? 'Ainda sem assunto completo no trecho transcrito.' : 'Nenhum assunto completo encontrado.'}</div>`);
}
$('sugCortes').onchange = e => {   // ajuste das bordas antes de adicionar (fica na sugestão; a edição só muda ao adicionar)
  const inp = e.target.closest('[data-c]'), box = e.target.closest('[data-c].corte, .corte'); if (!inp || !box) return;
  const r = st.sugCortes || st.sugParcial, c = r.cortes[+box.dataset.c], v = parseFloat(inp.value);
  if (isFinite(v)) c[inp.dataset.c] = Math.max(0, v);
  if (!(c.fim - c.ini >= 0.5)) { msg('O fim precisa ficar pelo menos 0,5 s depois do início.', 'warn'); }
  desenharCortes();
};
$('sugCortes').onclick = e => {
  const b = e.target.closest('button[data-a]'); const r = st.sugCortes || st.sugParcial; if (!b || !r) return;
  const c = r.cortes[+b.closest('.corte').dataset.c], fid = st.sugFonte;
  if (!plano().fontes[fid]) return msg('A fonte desta análise não está mais no projeto.', 'warn');
  if (b.dataset.a === 'ver') return tocar(fid, c.ini, c.fim);
  if (!(c.fim - c.ini >= 0.5)) return msg('Ajuste as bordas: o fim precisa ficar depois do início.', 'warn');
  const id = novoId('s'); mudar(p => p.segmentos.push({id, fonte: fid, ini: c.ini, fim: c.fim})); st.sel = id; c.add = true; desenharCortes(); tudo();
  msg(`“${esc(c.titulo)}” entrou no fim da linha do tempo e virou o corte ativo. A lista de sugestões não muda. Ctrl+Z desfaz.`, 'ok');
};

// ---------------------------------------------------------------- legenda
const meio = w => (w.ini + w.fim) / 2;
function frasesNoCorte(s) { return plano().legenda.frases.filter(f => f.fonte === s.fonte && f.palavras.some(w => meio(w) >= s.ini && meio(w) < s.fim)); }
function transcricaoBox() {
  const p = plano(), s = segAtivo(), n = p.segmentos.length;
  if (!s) { $('transBox').innerHTML = '<div class="mut">Adicione um corte para gerar a legenda.</div>'; return; }
  const i = p.segmentos.indexOf(s), fr = frasesNoCorte(s), ed = fr.filter(f => f.editada).length;
  $('transBox').innerHTML = `<div class="mut">Escopo da transcrição: só a fala que está dentro dos cortes. O resto da fonte não vira legenda.</div>
    <div class="row esp"><button class="pri" data-acao="corte">Gerar legenda do corte ativo (#${i + 1})</button>
      ${n > 1 ? `<button data-acao="todos">Gerar legenda de todos os cortes incluídos (${n})</button>` : ''}</div>
    <div class="mut dica">Corte #${i + 1} (fonte ${fmt(s.ini)}–${fmt(s.fim)}): ${fr.length ? `<span class="ok">${fr.length} frase(s)</span>` : 'sem legenda ainda'}${ed ? ` · ${ed} corrigida(s) por você: gerar de novo mantém as suas correções` : ''}.</div>`;
}
$('transBox').onclick = async e => {
  const b = e.target.closest('button[data-acao]'); if (!b) return;
  const p = plano(), segs = b.dataset.acao === 'corte' ? [segAtivo()] : p.segmentos;
  const trechos = segs.filter(s => s && p.fontes[s.fonte].tem_audio).map(s => ({fonte: s.fonte, ini: s.ini, fim: s.fim}));
  if (!trechos.length) return msg('Esse corte não tem áudio para transcrever.', 'warn');
  document.querySelectorAll('#transBox button').forEach(x => { x.disabled = true; });
  try {
    const rot = trechos.length > 1 ? `Transcrição de ${trechos.length} cortes` : 'Transcrição do corte ativo';
    const r = await tarefa('/api/transcricao', {trechos}, $('transProg'), rot);
    const j = await api('/api/legenda/gerar', {json: {plano: plano(), trechos}});
    mudar(q => { q.legenda.frases = j.frases; });
    const rs = j.resumo;
    msg(`Legenda ${trechos.length > 1 ? `de ${trechos.length} cortes` : 'do corte ativo'}: ${rs.novas} frase(s) a partir de ${rs.palavras} palavras` +
      (rs.mantidas_editadas ? `; ${rs.mantidas_editadas} correção(ões) sua(s) mantida(s)` : '') + '. ' +
      (r.em_cache ? 'Transcrição reaproveitada do cache.' : `Transcritos só ${r.transcrito_s.toFixed(1)}s (os trechos pedidos).`) +
      ' Confira o texto: o reconhecimento erra às vezes. Ctrl+Z desfaz.', 'ok');
  } catch (err) { msg('Legenda: ' + esc(err.message), 'bad'); }
  transcricaoBox();
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
  const num = Object.fromEntries(p.segmentos.map((s, i) => [s.id, i + 1]));
  const evs = $('soAtivo').checked && st.sel ? st.eventos.filter(ev => ev.seg === st.sel) : st.eventos;
  if (!evs.length) { $('frases').innerHTML = `<div class="mut" style="padding:6px">${p.legenda.frases.length ? ($('soAtivo').checked ? 'Nenhuma frase neste corte: gere a legenda do corte ativo acima.' : 'Nenhuma frase cai dentro dos segmentos atuais.') : 'Sem frases: gere a legenda do corte acima.'}</div>`; return; }
  $('frases').innerHTML = evs.map(ev => { const f = porId[ev.frase]; if (!f) return '';
    return `<div class="frase ${f.editada ? 'editada' : ''}" data-f="${f.id}"><button class="mini" data-acao="ouvir" title="tocar este trecho">${fmt(ev.ini)}</button>
      <input type="text" value="${esc(f.palavras.map(w => w.t).join(' '))}" aria-label="Texto da frase em ${fmt(ev.ini)}">
      <span style="white-space:nowrap"><button class="mini" data-acao="antes" title="mostrar 0,1s antes">−</button><button class="mini" data-acao="depois" title="mostrar 0,1s depois">+</button>
      <button class="mini" data-acao="juntar" title="juntar com a próxima">⤵</button><button class="mini" data-acao="remover" title="não mostrar esta frase">✕</button></span>
      <div class="tempos">saída ${fmt(ev.ini)} · corte #${num[ev.seg] || '?'} +${(ev.rel || 0).toFixed(1)}s · fonte ${fmt(ev.src)}${f.editada ? ' · <span class="ok">corrigida por você</span>' : ''}</div></div>`; }).join('');
}
$('soAtivo').onchange = frases;
function avisosLegenda() {
  $('avisosLeg').innerHTML = (st.avisosLeg || []).map((a, i) => `<div class="${a.tipo === 'cortada' ? 'warn' : 'mut'}" style="font-size:12.5px;margin:6px 0">⚠ ${esc(a.msg)} <button class="mini" data-av="${i}">ouvir na fonte</button></div>`).join('');
}
$('avisosLeg').onclick = e => { const b = e.target.closest('[data-av]'); if (!b) return; const a = st.avisosLeg[+b.dataset.av]; tocar(a.fonte, a.ini, a.fim + 0.2); };
$('frases').onclick = e => {
  const b = e.target.closest('button'); if (!b) return; const id = b.closest('[data-f]').dataset.f;
  const i = plano().legenda.frases.findIndex(f => f.id === id), f = plano().legenda.frases[i];
  if (b.dataset.acao === 'ouvir') return tocar(f.fonte, f.palavras[0].ini, f.palavras.at(-1).fim + 0.2);
  if (b.dataset.acao === 'antes' || b.dataset.acao === 'depois') {
    const d = b.dataset.acao === 'antes' ? -0.1 : 0.1;
    return mudar(p => { p.legenda.frases[i].palavras = f.palavras.map(w => ({...w, ini: Math.max(0, +(w.ini + d).toFixed(3)), fim: Math.max(0, +(w.fim + d).toFixed(3))})); p.legenda.frases[i].editada = true; });
  }
  if (b.dataset.acao === 'remover') return mudar(p => p.legenda.frases.splice(i, 1));
  if (b.dataset.acao === 'juntar') {
    const j = plano().legenda.frases.findIndex((x, k) => k > i && x.fonte === f.fonte && x.palavras[0].ini >= f.palavras.at(-1).ini);
    if (j < 0) return msg('Não há frase seguinte da mesma fonte para juntar.', 'warn');
    if (f.palavras.length + plano().legenda.frases[j].palavras.length > 60) return msg('A frase juntada passaria de 60 palavras.', 'warn');
    mudar(p => { p.legenda.frases[i].palavras = f.palavras.concat(p.legenda.frases[j].palavras); p.legenda.frases[i].editada = true; p.legenda.frases.splice(j, 1); });
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
    let k = 0; const novas = partes.map(pt => { const n = pt.split(/\s+/).length; const ws = j.frase.palavras.slice(k, k + n); k += n; return {id: novoId('f'), fonte: f.fonte, palavras: ws, editada: true}; });
    mudar(p => p.legenda.frases.splice(i, 1, ...novas.filter(x => x.palavras.length)));
  } catch (err) { msg(esc(err.message), 'bad'); frases(); }
};

// ---------------------------------------------------------------- títulos
function titulos() {
  const p = plano();
  const ests = Object.entries(st.estilosTit || {});
  $('titulos').innerHTML = p.titulos.map((t, i) => `<div class="sug" data-i="${i}">
    <div class="row"><span class="tag ${t.origem === 'usuario' ? '' : t.origem === 'sugestao_ia' ? 'ia' : 'loc'}">${t.origem === 'usuario' ? 'seu' : t.origem === 'sugestao_ia' ? 'sugerido por IA' : 'sugerido (local)'}</span>
    <input type="text" value="${esc(t.texto)}" data-campo="texto" style="flex:1;min-width:140px" maxlength="90" aria-label="Texto na tela ${i + 1}">
    <button class="mini" data-acao="sugerir" title="Sugerir textos para este item (não troca sem você aplicar)">✨ Sugerir</button>
    <button class="mini" data-acao="ver" title="Ver na vista Editado">👁 ver</button>
    <button class="mini" data-acao="remove" title="remover">✕</button></div>
    <div class="row" style="margin-top:6px"><span class="mut">saída</span>
    <input type="number" step="0.1" min="0" value="${t.ini}" data-campo="ini" aria-label="início na saída"> a
    <input type="number" step="0.1" min="0" value="${t.fim}" data-campo="fim" aria-label="fim na saída"> s ·
    <select data-campo="estilo" aria-label="aparência">${ests.map(([k, v]) => `<option value="${k}" ${k === (t.estilo || 'claro') ? 'selected' : ''}>${esc(v.nome)}</option>`).join('')}</select>
    <label class="inline mut">altura <input type="number" step="1" min="5" max="95" value="${Math.round((t.posicao_y ?? 0.14) * 100)}" data-campo="posicao_y" aria-label="posição vertical em % da altura"> %</label>
    <label class="inline mut">tam. <input type="number" step="2" min="24" max="160" value="${t.tamanho || 64}" data-campo="tamanho" aria-label="tamanho"></label></div>
    <details class="ajuste" style="margin-top:8px"><summary>Aparência (fonte, cores, destaque, entrada)</summary>${controlesVisuais(t, false)}</details></div>`).join('')
    || '<div class="mut">Nenhum texto na tela. Adicione um ou peça sugestões baseadas na fala do corte.</div>';
}
$('bAddTit').onclick = () => mudar(p => p.titulos.push({id: novoId('t'), texto: 'Seu texto', ini: 0, fim: 3, origem: 'usuario', estilo: 'claro', posicao_y: 0.14, tamanho: 64}));
$('titulos').onchange = e => {
  const ctl = e.target.closest('[data-v]');
  if (ctl) { const i = +ctl.closest('[data-i]').dataset.i; return mudar(p => { lerControle(ctl, p.titulos[i]); }); }
  const inp = e.target.closest('[data-campo]'); if (!inp) return; const i = +inp.closest('[data-i]').dataset.i, k = inp.dataset.campo;
  const v = k === 'texto' || k === 'estilo' ? inp.value : k === 'posicao_y' ? parseFloat(inp.value) / 100 : parseFloat(inp.value);
  if (typeof v === 'number' && !isFinite(v)) return titulos();
  mudar(p => { p.titulos[i][k] = v; }); };
$('titulos').onclick = e => { const b = e.target.closest('button[data-acao]'); if (!b) return; const i = +b.closest('[data-i]').dataset.i;
  if (b.dataset.acao === 'sugerir') return sugerirTexto('video', plano().titulos[i].id, false);
  if (b.dataset.acao === 'ver') return mudarVista('editado', plano().titulos[i].ini + 0.05);
  mudar(p => p.titulos.splice(i, 1)); };

// ---------------------------------------------------------------- efeitos e áudio
function efeitos() {
  const p = plano(), set = (id, v) => { if (document.activeElement !== $(id)) $(id).value = v; };
  set('cropX', p.quadro.crop_x); $('zAtivo').checked = p.efeitos.zoom.ativo; set('zInt', p.efeitos.zoom.intensidade);
  $('zV').textContent = Math.round(p.efeitos.zoom.intensidade * 100) + '%'; $('trTipo').value = p.efeitos.transicao.tipo; set('trDur', p.efeitos.transicao.duracao);
  set('vo', p.audio.volume_original); $('voV').textContent = Math.round(p.audio.volume_original * 100) + '%'; $('norm').checked = p.audio.normalizar;
  $('realce').checked = !!p.audio.realce_voz; desenharEfs();
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
liga('realce', (p, el) => { p.audio.realce_voz = el.checked; });
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
  const chave = chavePlano();
  try { const r = await renderizar('previa', plano(), $('tarefa')); st.previaChave = chave; $('previaBox').innerHTML = '<div id="previaEstado" class="warn"></div>' + blocoVideo(r); aoMudarPlano(); }
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
  const corpo = {nome: $('pNome').value || 'Sem nome', plano: st.versoes[0].plano, alternativas: st.versoes.slice(1), publicacao: st.publicacao};
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
    st.ativa = 0; st.hist = []; st.fut = []; st.sel = null; st.salvo = true; st.publicacao = {texto: (j.publicacao || {}).texto || ''};
    $('estadoSalvo').textContent = 'aberto'; $('estadoSalvo').className = 'ok';
    history.replaceState(null, '', '/editor?p=' + j.id);
    $('fonteVis').value = Object.keys(j.plano.fontes)[0] || ''; tudo(); trocarFonte(); validarDepois();
    if (j.fontes_faltando.length) msg(`Arquivos de fonte não encontrados em uploads/ (talvez apagados na limpeza): ${j.fontes_faltando.join(', ')}. Envie o vídeo de novo e troque a fonte.`, 'warn');
  } catch (e) { msg('Não abriu: ' + esc(e.message), 'bad'); }
}
$('pLista').onchange = () => { const v = $('pLista').value; if (v) abrir(v); $('pLista').value = ''; };
$('bSalvar').onclick = salvar;
$('bNovo').onclick = () => {
  if (!st.salvo && !confirm('Há alterações não salvas. Começar outro projeto mesmo assim?')) return;
  st.projeto = null; $('pNome').value = ''; st.versoes = [{id: 'principal', nome: 'Principal', origem: 'usuario', plano: PLANO_VAZIO()}];
  st.ativa = 0; st.hist = []; st.fut = []; st.salvo = true; st.publicacao = {texto: ''}; history.replaceState(null, '', '/editor'); tudo(); validarDepois();
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
  else if (e.key === '[') $('trimIniCur').click();
  else if (e.key === ']') $('trimFimCur').click();
  else if (e.key === 'p' || e.key === 'P') tocarCorte();
  else if (e.key === 'b' || e.key === 'B') $('bAB').click();
  else if (e.key === 'Delete' && st.sel) { const i = plano().segmentos.findIndex(s => s.id === st.sel); if (i >= 0) mudar(p => p.segmentos.splice(i, 1)); }
});

// ---------------------------------------------------------------- abas e desenho geral
document.querySelector('.tabs').onclick = e => { const b = e.target.closest('button[data-tab]'); if (!b) return;
  document.querySelectorAll('.tabs button').forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('.tab').forEach(x => x.classList.toggle('on', x.id === b.dataset.tab)); };
function tudo() {
  garantirSel();
  const p = plano(), atual = $('fonteVis').value;
  $('fonteVis').innerHTML = Object.entries(p.fontes).map(([fid, f]) => `<option value="${fid}">${esc(f.nome)}</option>`).join('');
  if (atual && p.fontes[atual]) $('fonteVis').value = atual;
  $('legAtiva').checked = p.legenda.ativa;
  $('bDesfazer').disabled = !st.hist.length; $('bRefazer').disabled = !st.fut.length;
  versoes(); segmentos(); transcricaoBox(); presets(); estiloForm(); frases(); titulos(); efeitos();
  corteAtivoInfo(); desenharTrim(); avisosLegenda(); publicacaoForm(); disponibilidade();
  if (window.desenharGanchos) window.desenharGanchos();
  if (window.aoMudarPlano) aoMudarPlano();
}
function disponibilidade() {   // estados vazios/desabilitados explicam o que falta
  const semCorte = !plano().segmentos.length, semFonte = !Object.keys(plano().fontes).length;
  for (const id of ['bSugTit', 'bGanchos', 'bSugAud', 'bSugPub', 'bPrevia', 'bExportar', 'trimPlay', 'bAB', 'bRendTrecho']) {
    if (!$(id)) continue;
    if (semCorte) { $(id).disabled = true; $(id).title = 'Adicione um vídeo e um corte primeiro.'; }
    else if ($(id).title === 'Adicione um vídeo e um corte primeiro.') { $(id).disabled = false; $(id).title = ''; }
  }
  if (semFonte) { $('bSug').disabled = true; $('bSug').title = 'Adicione um vídeo primeiro.'; }
  else if ($('bSug').title === 'Adicione um vídeo primeiro.') { $('bSug').disabled = false; $('bSug').title = ''; }
}
function publicacaoForm() {
  const el = $('pubTexto');
  if (document.activeElement !== el) el.value = st.publicacao.texto;
  $('pubConta').textContent = `${el.value.length} caracteres · fica salvo no projeto · não aparece no vídeo`;
}
$('pubTexto').addEventListener('focus', () => { st.pubAntes = retrato(); });
$('pubTexto').addEventListener('input', () => { st.publicacao.texto = $('pubTexto').value; marcarAlterado(); publicacaoForm(); });
$('pubTexto').addEventListener('change', () => { if (st.pubAntes) { st.hist.push(st.pubAntes); st.fut = []; st.pubAntes = retrato(); $('bDesfazer').disabled = false; } });
function definirPublicacao(texto) { st.hist.push(retrato()); st.fut = []; st.publicacao.texto = texto.slice(0, 4000); marcarAlterado(); tudo(); }
async function copiar(texto) {
  try { await navigator.clipboard.writeText(texto); msg('Copiado para a área de transferência.', 'ok'); }
  catch (e) { msg('O navegador não deixou copiar: selecione o texto e use Ctrl+C.', 'warn'); }
}
$('pubCopiar').onclick = () => copiar($('pubTexto').value);

// ================================================================ ganchos (fala real x título derivado)
async function aplicarOp(op, comoVersao, nome) {
  const j = await api('/api/operacao', {json: {plano: plano(), operacao: op}});
  if (comoVersao) novaVersao(nome, comoVersao, p => { Object.keys(p).forEach(k => delete p[k]); Object.assign(p, j.plano); });
  else mudar(() => { st.versoes[st.ativa].plano = j.plano; });
}
async function previaCom(op, a = 0, b = 10, rotulo) {
  try { await compararNoPlayer(Array.isArray(op) ? op : [op], a, b, rotulo); }
  catch (e) { msg(esc(e.message), 'bad'); }
}
// ================================================================ configuração do provedor (local: /api/config não sai do PC)
async function carregarConfig() {
  try { st.cfg = await api('/api/config'); } catch (e) { st.cfg = {}; }
  provInfo();
}

// ================================================================ corte ativo, provedor e tarefas
function segAtivo() { return plano().segmentos.find(s => s.id === st.sel) || null; }
function garantirSel() { const segs = plano().segmentos; if (!segs.some(s => s.id === st.sel)) st.sel = segs[0] ? segs[0].id : null; }
function verFonteDoAtivo() {
  const s = segAtivo(); if (!s) return;
  if (window.PV && PV.vista === 'editado') { const m = (st.mapa || []).find(x => x.id === s.id); if (m) irParaSaida(m.out_ini, false); return; }
  if (fonteVisivel() !== s.fonte) { $('fonteVis').value = s.fonte; trocarFonte(); }
}
function corteAtivoInfo() {
  const s = segAtivo(), el = $('corteAtivo');
  if (!s) { el.innerHTML = 'Sem corte ativo: adicione uma fonte e selecione um segmento na linha do tempo.'; return; }
  const p = plano(), i = p.segmentos.indexOf(s), m = (st.mapa || []).find(x => x.id === s.id), f = p.fontes[s.fonte];
  el.innerHTML = `Corte ativo <b>#${i + 1}</b> · ${esc(f ? f.nome : '')} · fonte <b>${fmt(s.ini)}–${fmt(s.fim)}</b> (${(s.fim - s.ini).toFixed(1)}s)` +
    (m ? ` · saída ${fmt(m.out_ini)}–${fmt(m.out_fim)}` : '') +
    '<br>Legenda, textos, ganchos e áudio usam só este trecho (ou a edição completa, quando você escolher).';
}
const provedor = () => $('provedor').value;
const envio = () => $('envioQuadros').checked;
function provInfo() {
  const c = st.cfg || {};
  $('provInfo').innerHTML = provedor() === 'local' ? 'Nada sai do seu PC.'
    : c.gemini ? `Gemini configurado (${esc(c.gemini_modelo)}): recebe o TEXTO da transcrição do escopo.`
      : '<span class="warn">Sem GEMINI_API_KEY: as análises usam a heurística local (sem IA).</span>';
}
$('provedor').onchange = () => { provInfo(); try { localStorage.setItem('cs_provedor', provedor()); } catch (e) { /* opcional */ } };
$('envioQuadros').onchange = () => { try { localStorage.setItem('cs_envio_quadros', envio() ? '1' : ''); } catch (e) { /* opcional */ } };
async function tarefa(url, corpo, alvo, rotulo) { const t = await api(url, {json: corpo}); return acompanhar(t, alvo, rotulo); }
function tagProv(r) { return r.provedor === 'gemini' ? `<span class="tag ia">IA · ${esc(r.modelo || 'Gemini')}</span>` : '<span class="tag loc">heurística local (sem IA)</span>'; }
function cabResultado(r) {
  return `<div class="row" style="margin:8px 0 4px">${tagProv(r)}<span class="mut" style="flex:1">${esc(r.metodo || '')}</span></div>` +
    (r.enviado ? `<div class="mut">Enviado ao provedor: ${esc(r.enviado)}</div>` : '') +
    (r.avisos || []).map(a => `<div class="warn" style="font-size:12.5px">⚠ ${esc(a)}</div>`).join('');
}
function ouvirDe(b) { const [f, a, z] = b.dataset.ouvir.split('|'); tocar(f, +a, +z); }

// ================================================================ recorte no player (alças, números, tocar só o corte)
const TR = {arr: null};
const grade = t => Math.round(t * 30) / 30;
function trimJanela(s) {
  const dur = plano().fontes[s.fonte].duracao;
  if (!$('trimZoom').checked) return [0, dur];
  const m = Math.max(5, (s.fim - s.ini) * 0.25);
  return [Math.max(0, s.ini - m), Math.min(dur, s.fim + m)];
}
function desenharTrim() {
  const s = segAtivo(), box = $('trim');
  if (!s || !plano().fontes[s.fonte]) { box.hidden = true; return; }
  box.hidden = false;
  const [v0, v1] = trimJanela(s), v = TR.arr || s, pct = t => Math.max(0, Math.min(100, (t - v0) / (v1 - v0) * 100));
  $('trimSel').style.left = pct(v.ini) + '%'; $('trimSel').style.width = (pct(v.fim) - pct(v.ini)) + '%';
  $('trimA').style.left = pct(v.ini) + '%'; $('trimB').style.left = pct(v.fim) + '%';
  $('trimA').setAttribute('aria-valuenow', v.ini.toFixed(2)); $('trimA').setAttribute('aria-valuetext', fmt(v.ini));
  $('trimB').setAttribute('aria-valuenow', v.fim.toFixed(2)); $('trimB').setAttribute('aria-valuetext', fmt(v.fim));
  $('trimTrilho').querySelectorAll('.trim-seg').forEach(x => x.remove());
  for (const o of plano().segmentos) {   // os outros cortes da mesma fonte, para referência
    if (o.id === s.id || o.fonte !== s.fonte || o.fim < v0 || o.ini > v1) continue;
    const d = document.createElement('div'); d.className = 'trim-seg'; d.title = `corte #${plano().segmentos.indexOf(o) + 1}`;
    d.style.left = pct(o.ini) + '%'; d.style.width = (pct(o.fim) - pct(o.ini)) + '%'; $('trimTrilho').prepend(d);
  }
  $('trimV0').textContent = fmt(v0); $('trimV1').textContent = fmt(v1);
  $('trimRot').innerHTML = `Recorte do corte <b>#${plano().segmentos.indexOf(s) + 1}</b> na fonte`;
  if (document.activeElement !== $('trimIni')) $('trimIni').value = v.ini.toFixed(2);
  if (document.activeElement !== $('trimFim')) $('trimFim').value = v.fim.toFixed(2);
  $('trimDur').textContent = (v.fim - v.ini).toFixed(2) + 's';
  cursorTrim();
}
function cursorTrim() {
  const s = segAtivo(); if (!s || $('trim').hidden) return;
  const [v0, v1] = trimJanela(s), vis = fonteVisivel() === s.fonte;
  $('trimCur').hidden = !vis;
  if (vis) $('trimCur').style.left = Math.max(0, Math.min(100, (vid.currentTime - v0) / (v1 - v0) * 100)) + '%';
}
function tDoPonteiro(e) {
  const [v0, v1] = trimJanela(segAtivo()), r = $('trimTrilho').getBoundingClientRect();
  return grade(v0 + Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * (v1 - v0));
}
function aplicarTrim(ini, fim) {
  const s = segAtivo(); if (!s) return;
  const dur = plano().fontes[s.fonte].duracao;
  ini = grade(Math.max(0, ini)); fim = grade(Math.min(dur, fim));
  if (!(fim - ini >= 0.1)) { msg('O corte precisa ter pelo menos 0,1 s, com o fim depois do início.', 'warn'); desenharTrim(); return; }
  if (Math.abs(ini - s.ini) < 1e-4 && Math.abs(fim - s.fim) < 1e-4) { desenharTrim(); return; }
  const id = s.id;
  mudar(p => { const x = p.segmentos.find(y => y.id === id); x.ini = ini; x.fim = fim; });
}
$('trimTrilho').addEventListener('pointerdown', e => {
  const s = segAtivo(); if (!s) return;
  if (PV.vista !== 'original') mudarVista('original', null, false);   // recortar é na fonte: mostra o quadro original da borda
  verFonteDoAtivo();
  const alca = e.target.id === 'trimA' ? 'a' : e.target.id === 'trimB' ? 'b' : null;
  if (!alca) { vid.currentTime = tDoPonteiro(e); return; }   // clique na trilha: leva o player até lá
  e.preventDefault(); e.target.focus(); $('trimTrilho').setPointerCapture(e.pointerId);
  TR.arr = {alca, ini: s.ini, fim: s.fim};
});
$('trimTrilho').addEventListener('pointermove', e => {
  if (!TR.arr) return;
  const t = tDoPonteiro(e);
  if (TR.arr.alca === 'a') TR.arr.ini = Math.min(t, grade(TR.arr.fim - 0.1)); else TR.arr.fim = Math.max(t, grade(TR.arr.ini + 0.1));
  vid.currentTime = TR.arr.alca === 'a' ? TR.arr.ini : TR.arr.fim;   // mostra o quadro da borda enquanto arrasta
  desenharTrim();
});
const soltarTrim = () => { if (!TR.arr) return; const {ini, fim} = TR.arr; TR.arr = null; aplicarTrim(ini, fim); };
$('trimTrilho').addEventListener('pointerup', soltarTrim);
$('trimTrilho').addEventListener('pointercancel', soltarTrim);
for (const [id, k] of [['trimA', 'ini'], ['trimB', 'fim']]) {
  $(id).addEventListener('keydown', e => {
    if (!['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    e.preventDefault(); e.stopPropagation();
    const s = segAtivo(); if (!s) return;
    const v = {ini: s.ini, fim: s.fim}; v[k] += (e.key === 'ArrowLeft' ? -1 : 1) * (e.shiftKey ? 1 : 1 / 30);
    verFonteDoAtivo(); vid.currentTime = Math.max(0, v[k]); aplicarTrim(v.ini, v.fim);
  });
}
$('trimIni').onchange = () => { const s = segAtivo(), v = parseFloat($('trimIni').value); if (s && isFinite(v)) aplicarTrim(v, s.fim); else desenharTrim(); };
$('trimFim').onchange = () => { const s = segAtivo(), v = parseFloat($('trimFim').value); if (s && isFinite(v)) aplicarTrim(s.ini, v); else desenharTrim(); };
function bordaNoCursor(k) {
  const s = segAtivo(); if (!s) return msg('Selecione um corte na linha do tempo.', 'warn');
  if (fonteVisivel() !== s.fonte) return msg('O player está em outra fonte: selecione o corte de novo.', 'warn');
  const t = vid.currentTime;
  if (k === 'ini' ? t >= s.fim - 0.1 : t <= s.ini + 0.1) return msg(`O cursor (${fmt(t)}) fica ${k === 'ini' ? 'depois do fim' : 'antes do início'} do corte.`, 'warn');
  aplicarTrim(k === 'ini' ? t : s.ini, k === 'fim' ? t : s.fim);
}
$('trimIniCur').onclick = () => bordaNoCursor('ini');
$('trimFimCur').onclick = () => bordaNoCursor('fim');
$('trimZoom').onchange = desenharTrim;
function tocarCorte() {
  const s = segAtivo(); if (!s) return;
  if (PV.vista === 'editado') {   // na vista Editado: o corte como ele sai (com legenda, texto e áudio)
    const m = (st.mapa || []).find(x => x.id === s.id); if (!m) return;
    PV.pararEm = m.out_fim - 0.03; return irParaSaida(m.out_ini, true);
  }
  if (PV.vista === 'render') mudarVista('original', null, false);
  verFonteDoAtivo(); st.pararEm = null; st.restrito = true; vid.currentTime = s.ini; vid.play().catch(() => {});
}
$('trimPlay').onclick = tocarCorte;
vid.addEventListener('seeked', () => {   // procurar fora do corte encerra a reprodução restrita
  const s = segAtivo(); if (st.restrito && s && !TR.arr && (vid.currentTime < s.ini - 0.05 || vid.currentTime > s.fim + 0.05)) st.restrito = null;
});
(function laco() {   // por quadro: cursor suave e parada exata no fim do corte
  cursorTrim();
  if (window.PV && PV.vista === 'editado' && !PV.pulando) {   // vista Editado: segue a linha do tempo de saída
    const m = (st.mapa || [])[PV.idx];
    if (m && !vid.paused && vid.currentTime >= m.fim - 0.03) {
      const prox = st.mapa[PV.idx + 1];
      if (prox && !(PV.pararEm != null && prox.out_ini >= PV.pararEm)) irParaSaida(prox.out_ini, true);
      else { vid.pause(); PV.pararEm = null; PV.extra = []; }
    }
    if (PV.pararEm != null && !vid.paused && saidaAtual() >= PV.pararEm) { vid.pause(); PV.pararEm = null; PV.extra = []; }
    desenharCamada();
  }
  const s = segAtivo();
  if (st.restrito && s && !vid.paused && vid.currentTime >= s.fim - 0.02) {
    if ($('trimLoop').checked) vid.currentTime = s.ini; else { vid.pause(); st.restrito = null; }
  }
  requestAnimationFrame(laco);
})();

// ================================================================ aplicar sugestões com registro (para desfazer só aquela)
function idsDe(p) { return {s: new Set(p.segmentos.map(x => x.id)), t: new Set(p.titulos.map(x => x.id)), e: new Set((p.audio.efeitos || []).map(x => x.id))}; }
async function aplicarRegistrado(ops, comoVersao, nome) {
  const antes = plano();
  const j = await api('/api/operacao', {json: {plano: antes, operacoes: ops}});
  const a = idsDe(antes), d = idsDe(j.plano);
  const reg = {segmentos: [...d.s].filter(x => !a.s.has(x)), titulos: [...d.t].filter(x => !a.t.has(x)), efeitos: [...d.e].filter(x => !a.e.has(x)),
    textos: Object.fromEntries(ops.filter(o => o.tipo === 'definir_titulo').map(o => [o.id, (antes.titulos.find(t => t.id === o.id) || {}).texto])),
    audio: ops.some(o => o.tipo === 'ajustar_audio') ? {realce_voz: antes.audio.realce_voz, normalizar: antes.audio.normalizar, abaixar_na_fala: antes.audio.musica.abaixar_na_fala} : null};
  if (comoVersao) { novaVersao(nome, comoVersao, p => { Object.keys(p).forEach(k => delete p[k]); Object.assign(p, j.plano); }); reg.versao = true; }
  else mudar(() => { st.versoes[st.ativa].plano = j.plano; });
  return reg;
}
function desfazerRegistro(reg) {
  if (reg.versao) { msg('Isso criou uma versão separada: a versão anterior continua intacta no seletor de versão.', 'warn'); return false; }
  mudar(p => {
    p.segmentos = p.segmentos.filter(x => !reg.segmentos.includes(x.id));
    p.titulos = p.titulos.filter(x => !reg.titulos.includes(x.id));
    p.audio.efeitos = p.audio.efeitos.filter(x => !reg.efeitos.includes(x.id));
    for (const [id, txt] of Object.entries(reg.textos)) { const t = p.titulos.find(x => x.id === id); if (t && txt != null) t.texto = txt; }
    if (reg.audio) { p.audio.realce_voz = reg.audio.realce_voz; p.audio.normalizar = reg.audio.normalizar; p.audio.musica.abaixar_na_fala = reg.audio.abaixar_na_fala; }
  });
  return true;
}
const origemDe = r => r.provedor === 'gemini' ? 'sugestao_ia' : 'sugestao_local';

// ================================================================ sugestões de texto (destino explícito)
const DESTINO = {video: {prog: 'titProg', caixa: 'titSug', aba: 'tTit', botao: 'bSugTit', rot: 'Texto na tela'},
  publicacao: {prog: 'pubProg', caixa: 'pubSug', aba: 'tPub', botao: 'bSugPub', rot: 'Texto da publicação'}};
async function sugerirTexto(destino, alvo, outras) {
  const s = segAtivo(); if (!s) return msg('Selecione um corte na linha do tempo.', 'warn');
  const D = DESTINO[destino], ant = st.sugTexto[destino];
  const excluir = outras && ant ? ant.excluir : [];
  $(D.botao).disabled = true;
  try {
    const res = await tarefa('/api/titulos/sugerir', {plano: plano(), segmento: s.id, provedor: provedor(), visual: envio(),
      permitir_envio: envio(), excluir, destino}, $(D.prog), D.rot);
    st.sugTexto[destino] = {alvo, res, excluir: excluir.concat(res.titulos.map(t => t.texto)), estado: {}, reg: {}};
    document.querySelector(`.tabs [data-tab="${D.aba}"]`).click();
    desenharSugTexto(destino);
  } catch (e) { $(D.caixa).innerHTML = `<div class="bad">${esc(e.message)}</div>`; }
  $(D.botao).disabled = false;
}
$('bSugTit').onclick = () => sugerirTexto('video', null, false);
$('bSugPub').onclick = () => sugerirTexto('publicacao', null, false);
function desenharSugTexto(destino) {
  const g = st.sugTexto[destino], D = DESTINO[destino]; if (!g) { $(D.caixa).innerHTML = ''; return; }
  const alvo = destino === 'video' && g.alvo && plano().titulos.find(t => t.id === g.alvo);
  const e1 = (g.res.escopo || [])[0];
  const acoes = destino === 'publicacao'
    ? '<button class="mini pri" data-a="usar">Usar no campo (substitui)</button><button class="mini" data-a="acrescentar">Acrescentar ao campo</button><button class="mini" data-a="copiar">Copiar</button>'
    : (alvo ? '<button class="mini pri" data-a="aplicar">Aplicar a este texto</button>' : '') +
      '<button class="mini" data-a="novo">Adicionar como texto na tela (0–3s)</button><button class="mini" data-a="previa">Prévia no player</button>';
  $(D.caixa).innerHTML = cabResultado(g.res) +
    `<div class="row" style="margin:6px 0"><span class="chip ${destino === 'publicacao' ? 'ok' : 'warn'}">Destino: ${esc(g.res.destino_rotulo || D.rot)}</span></div>` +
    `<div class="mut">${alvo ? `Para o texto “${esc(alvo.texto)}” (só muda se você clicar em Aplicar).` : destino === 'publicacao' ? 'Nada vira texto no vídeo: só o campo da publicação muda, e só quando você clicar.' : 'Vira um texto na tela novo só quando você clicar.'}
      Baseado na fala da fonte ${e1 ? fmt(e1.ini) + '–' + fmt(e1.fim) : ''}.</div>` +
    g.res.titulos.map((t, i) => `<div class="sug ${g.estado[t.id] || ''}" data-ts="${i}">
      <div class="row"><span class="tag">${esc(t.tipo)}</span>${t.alerta ? `<span class="chip warn">${esc(t.alerta)}</span>` : ''}</div>
      ${destino === 'publicacao' ? `<textarea rows="2" style="width:100%;margin:6px 0" aria-label="Texto sugerido (editável)">${esc(t.texto)}</textarea>`
        : `<input type="text" value="${esc(t.texto)}" maxlength="90" style="width:100%;margin:6px 0" aria-label="Texto sugerido (editável)">`}
      <div class="campo"><b>Por quê:</b> ${esc(t.explicacao)}</div>
      ${t.base.map(b => `<div class="cita">“${esc(b.texto)}” <button class="mini" data-ouvir="${b.fonte}|${b.ini}|${b.fim + 0.2}" title="toca o trecho original">▶ ${fmt(b.ini)}</button></div>`).join('')}
      <div class="acoes">${acoes}<button class="mini" data-a="desfazer" ${g.reg[t.id] ? '' : 'hidden'}>Desfazer</button></div></div>`).join('') +
    '<div class="row esp"><button data-a="outras">↻ Gerar alternativas</button><button class="mini" data-a="fechar">fechar</button></div>';
}
for (const destino of Object.keys(DESTINO)) {
  $(DESTINO[destino].caixa).onclick = async e => {
    const b = e.target.closest('button'); const g = st.sugTexto[destino]; if (!b || !g) return;
    if (b.dataset.ouvir) return ouvirDe(b);
    if (b.dataset.a === 'outras') return sugerirTexto(destino, g.alvo, true);
    if (b.dataset.a === 'fechar') { delete st.sugTexto[destino]; return desenharSugTexto(destino); }
    const box = b.closest('[data-ts]'); if (!box) return;
    const t = g.res.titulos[+box.dataset.ts], texto = box.querySelector('input,textarea').value.trim();
    try {
      if (b.dataset.a === 'desfazer') {
        const reg = g.reg[t.id];
        if (reg.pub != null) { definirPublicacao(reg.pub); delete g.reg[t.id]; delete g.estado[t.id]; }
        else if (desfazerRegistro(reg)) { delete g.reg[t.id]; delete g.estado[t.id]; }
      } else if (b.dataset.a === 'usar' || b.dataset.a === 'acrescentar') {
        const antes = st.publicacao.texto;
        definirPublicacao(b.dataset.a === 'usar' ? texto : (antes ? antes.trimEnd() + '\n\n' : '') + texto);
        g.reg[t.id] = {pub: antes}; g.estado[t.id] = 'aplicada';
      } else if (b.dataset.a === 'copiar') return copiar(texto);
      else if (b.dataset.a === 'aplicar') { g.reg[t.id] = await aplicarRegistrado([{tipo: 'definir_titulo', id: g.alvo, texto, origem: origemDe(g.res)}]); g.estado[t.id] = 'aplicada'; }
      else if (b.dataset.a === 'novo') { g.reg[t.id] = await aplicarRegistrado([{tipo: 'adicionar_titulo', texto, ini: 0, fim: 3, origem: origemDe(g.res)}]); g.estado[t.id] = 'aplicada'; }
      else if (b.dataset.a === 'previa') return previaCom({tipo: 'adicionar_titulo', texto, ini: 0, fim: 3, origem: origemDe(g.res)}, 0, 5, 'com o texto na tela');
      desenharSugTexto(destino);
    } catch (err) { msg(esc(err.message), 'bad'); }
  };
}

// ================================================================ aparência do texto na tela
const CAMPOS_VIS = ['fonte', 'cor', 'fundo_cor', 'caixa', 'maiusculas', 'italico', 'destaque_cor', 'animacao'];
function visualEfetivo(t) {
  const base = (st.estilosTit || {})[t.estilo || 'claro'] || {};
  const v = {...base}; CAMPOS_VIS.forEach(k => { if (t[k] != null) v[k] = t[k]; });
  return v;
}
function controlesVisuais(t, comDuracao) {
  const v = visualEfetivo(t), ws = String(t.texto || '').split(/\s+/);
  const dest = (t.destaque || []).map(k => ws[k]).filter(Boolean).join(', ');
  const opt = (lista, atual) => lista.map(([k, n]) => `<option value="${k}" ${k === atual ? 'selected' : ''}>${esc(n)}</option>`).join('');
  return `<div class="row"><label class="inline mut">Estilo <select data-v="estilo">${opt(Object.entries(st.estilosTit || {}).map(([k, x]) => [k, x.nome]), t.estilo || 'claro')}</select></label>
    <label class="inline mut">Fonte <input type="text" data-v="fonte" list="fontesComuns" class="w140" value="${esc(v.fonte)}"></label>
    <label class="inline mut">Cor <input type="color" data-v="cor" value="${v.cor}"></label>
    <label class="inline mut">${v.caixa ? 'Caixa' : 'Contorno'} <input type="color" data-v="fundo_cor" value="${v.fundo_cor}"></label></div>
    <div class="row"><label class="chk"><input type="checkbox" data-v="caixa" ${v.caixa ? 'checked' : ''}> caixa</label>
    <label class="chk"><input type="checkbox" data-v="maiusculas" ${v.maiusculas ? 'checked' : ''}> maiúsculas</label>
    <label class="chk"><input type="checkbox" data-v="italico" ${v.italico ? 'checked' : ''}> itálico</label>
    <label class="inline mut">Entrada <select data-v="animacao">${opt([['nenhuma', 'sem animação'], ['fade', 'aparecer suave'], ['pop', 'pop'], ['deslizar', 'deslizar']], v.animacao)}</select></label></div>
    <div class="row"><label class="inline mut">Destaque <input type="text" data-v="destaque_txt" value="${esc(dest)}" placeholder="palavras" style="width:130px"></label>
    <input type="color" data-v="destaque_cor" value="${v.destaque_cor}" aria-label="cor do destaque">
    <label class="inline mut">Tam. <input type="number" data-v="tamanho" min="24" max="160" step="2" value="${t.tamanho || 64}"></label>
    <label class="inline mut">Altura <input type="number" data-v="posicao_y" min="5" max="95" value="${Math.round((t.posicao_y ?? 0.14) * 100)}"> %</label>
    ${comDuracao ? `<label class="inline mut">Duração <input type="number" data-v="dur" min="0.5" max="15" step="0.1" value="${(t.fim - t.ini).toFixed(1)}"> s</label>` : ''}</div>`;
}
function lerControle(el, t) {   // aplica um controle ao objeto do texto na tela (sugestão ou item do plano)
  const k = el.dataset.v;
  if (k === 'estilo') { t.estilo = el.value; CAMPOS_VIS.forEach(c => { delete t[c]; }); return; }
  if (k === 'destaque_txt') {
    const alvo = el.value.split(/[,\s]+/).map(w => w.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '')).filter(Boolean);
    t.destaque = String(t.texto).split(/\s+/).map((w, i) => alvo.includes(w.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '')) ? i : -1).filter(i => i >= 0);
    return;
  }
  if (k === 'dur') { const d = parseFloat(el.value); if (isFinite(d)) t.fim = +(t.ini + d).toFixed(2); return; }
  if (k === 'tamanho') { const n = parseInt(el.value, 10); if (isFinite(n)) t.tamanho = n; return; }
  if (k === 'posicao_y') { const n = parseFloat(el.value); if (isFinite(n)) t.posicao_y = n / 100; return; }
  t[k] = el.type === 'checkbox' ? el.checked : el.type === 'color' ? el.value.toUpperCase() : el.value;
}
function maisImpacto(t) {
  const ws = String(t.texto).split(/\s+/);
  t.tamanho = Math.min(160, (t.tamanho || 64) + 12); t.maiusculas = true; t.animacao = 'pop'; t.caixa = true;
  if (!t.destaque || !t.destaque.length) { let m = 0; ws.forEach((w, i) => { if (w.length > ws[m].length) m = i; }); t.destaque = [m]; }
}
function maisDiscreto(t) { t.tamanho = Math.max(32, (t.tamanho || 64) - 12); t.animacao = 'fade'; t.caixa = false; t.maiusculas = false; t.destaque = []; }
function tituloDeAlt(a) { return {texto: a.texto, ini: 0, fim: a.fim, origem: 'usuario', ...a.visual}; }
async function miniaturasAlternativas() {   // miniatura REAL (libass) do corte com cada alternativa aplicada
  const g = st.ganchos; if (!g || !g.alternativas_visuais) return;
  for (const a of g.alternativas_visuais) {
    if (a.mini || a.pedindo) continue;
    a.pedindo = true;
    try {
      const r = await api('/api/titulos/miniatura', {json: {plano: plano(), titulo: tituloDeAlt(a), segmento: st.sel}});
      a.mini = r.url; const img = document.querySelector(`img[data-mini="${a.id}"]`); if (img) img.src = r.url;
    } catch (e) { a.miniErro = e.message; } finally { a.pedindo = false; }
  }
}

// ================================================================ ganchos analisados
const TIPO_G = {fala: ['fala', 'fala existente'], visual: ['ia', 'momento visual'], titulo: ['loc', 'título sobreposto (texto novo)'], narracao: ['', 'narração nova (precisa gravar)']};
const ENTREGA = {entrega: ['ok', 'o corte entrega'], provavel: ['ok', 'provavelmente entrega'], parcial: ['warn', 'entrega em parte'], incerta: ['warn', 'entrega incerta'], nao_entrega: ['bad', 'o corte não entrega']};
function opGancho(c) {
  if (c.tipo === 'fala' || c.tipo === 'visual') return {tipo: 'inserir_inicio', fonte: c.fonte, ini: c.ini, fim: c.fim};
  if (c.tipo === 'titulo') return {...c.operacao, texto: c.texto};
  return null;
}
function desenharGanchos() {
  const g = st.ganchos; if (!g || !g.ganchos) return;
  g.estado = g.estado || {}; g.reg = g.reg || {};
  const card = (c, i) => {
    const [cls, rot] = TIPO_G[c.tipo], [ec, er] = ENTREGA[c.entrega.status] || ['', c.entrega.status];
    const temTrecho = c.tipo === 'fala' || c.tipo === 'visual';
    return `<div class="sug ${g.estado[c.id] || ''}" data-g="${i}">
      <div class="row"><span class="tag ${cls}">${rot}</span><span class="tag">${esc(c.categoria)}</span><span class="chip ${ec}">${er}</span>${c.alerta ? `<span class="chip warn">${esc(c.alerta)}</span>` : ''}</div>
      ${temTrecho ? `<div class="cita">${c.tipo === 'fala' ? '“' + esc(c.texto) + '”' : esc(c.texto)}</div>
        <div class="row"><span class="mut">Trecho na fonte</span><input type="number" step="0.1" min="0" value="${c.ini}" data-c="ini" aria-label="início do gancho na fonte"> a
        <input type="number" step="0.1" min="0" value="${c.fim}" data-c="fim" aria-label="fim do gancho na fonte"> s</div>`
        : `<input type="text" value="${esc(c.texto)}" maxlength="${c.tipo === 'titulo' ? 90 : 300}" style="width:100%;margin:6px 0" data-c="texto" aria-label="Texto sugerido (editável)">`}
      <div class="campo"><b>Por que prende:</b> ${esc(c.motivo)}</div>
      ${c.promessa ? `<div class="campo"><b>Promete:</b> ${esc(c.promessa)}</div>` : ''}
      <div class="campo"><b>Entrega:</b> ${esc(c.entrega.explicacao || '')} ${c.entrega.trecho ? `<button class="mini" data-ouvir="${c.entrega.trecho.fonte}|${c.entrega.trecho.ini}|${c.entrega.trecho.ini + 5}">▶ ${fmt(c.entrega.trecho.ini)}</button>` : ''}</div>
      <div class="campo"><b>Edição proposta:</b> ${esc(c.descricao)}</div>
      <div class="acoes">${temTrecho ? '<button class="mini" data-a="ouvir">▶ ver/ouvir</button>' : ''}
        ${c.operacao ? '<button class="mini" data-a="previa">Prévia</button><button class="mini pri" data-a="aplicar">Aplicar</button><button class="mini" data-a="versao">Criar versão</button>'
          : '<button class="mini" data-a="comoTitulo">Usar como título (0–3s)</button>'}
        <button class="mini" data-a="desfazer" ${g.reg[c.id] ? '' : 'hidden'}>Desfazer</button><button class="mini" data-a="rejeitar">Rejeitar</button></div></div>`;
  };
  const grupo = (tipo, titulo) => { const l = g.ganchos.map((c, i) => [c, i]).filter(([c]) => c.tipo === tipo); return l.length ? `<h2 style="margin-top:12px">${titulo}</h2>` + l.map(([c, i]) => card(c, i)).join('') : ''; };
  $('ganchos').innerHTML = cabResultado(g) +
    (g.proposta ? `<div class="sug ${g.estado.tudo || ''}" data-tudo="1"><b>Edição proposta completa</b><div class="mut">${esc(g.proposta.descricao)} (${g.proposta.operacoes.length} operação(ões))</div>
      <div class="acoes"><button class="mini" data-a="tudoPrevia">Prévia</button><button class="mini pri" data-a="tudo">Aplicar tudo</button><button class="mini" data-a="tudoVersao">Como versão nova</button>
      <button class="mini" data-a="tudoDesfazer" ${g.reg.tudo ? '' : 'hidden'}>Desfazer</button></div></div>` : '') +
    alternativasHtml(g) +
    grupo('fala', 'Falas existentes (trecho real do corte)') + grupo('visual', 'Momentos visuais') +
    grupo('titulo', 'Títulos sobrepostos (texto novo, não é fala)') + grupo('narracao', 'Narração nova (precisa ser gravada)') +
    (g.ganchos.length ? '' : '<div class="mut">Nenhum gancho encontrado.</div>');
}
function alternativasHtml(g) {
  const alts = g.alternativas_visuais || []; if (!alts.length) return '';
  return `<h2 style="margin-top:12px">Gancho na tela: alternativas aplicadas ao corte</h2>
    <div class="mut">Texto NOVO na tela (não é fala). Cada uma já vem com estilo, cor, destaque, tamanho, posição, entrada e duração; ajuste o que quiser.</div>` +
    alts.map((a, i) => `<div class="sug ${g.estado[a.id] || ''}" data-alt="${i}"><div class="row" style="align-items:flex-start;flex-wrap:nowrap">
      <img class="mini-v" data-mini="${a.id}" src="${a.mini || ''}" alt="Miniatura do corte com o gancho ${esc(a.rotulo)}">
      <div style="flex:1;min-width:0"><div class="row"><span class="tag loc">${esc(a.rotulo)}</span><span class="tag">texto novo na tela · ${a.fim.toFixed(1)}s</span></div>
        <input type="text" value="${esc(a.texto)}" maxlength="90" data-alt-texto="1" style="width:100%;margin:6px 0" aria-label="Texto do gancho (editável)">
        <div class="campo mut">${esc(a.motivo)}</div>
        <div class="acoes"><button class="mini" data-a="impacto">Mais impactante</button><button class="mini" data-a="discreto">Mais discreto</button>
          <button class="mini" data-a="verAlt">👁 Ver no player</button><button class="mini pri" data-a="aplicarAlt">Aplicar</button>
          <button class="mini" data-a="desfazerAlt" ${g.reg[a.id] ? '' : 'hidden'}>Desfazer</button><button class="mini" data-a="salvarEstilo" title="Usa esta aparência nas próximas sugestões">Salvar como meu estilo</button></div>
        <details class="ajuste" style="margin-top:8px"><summary>Ajustar aparência</summary>${controlesVisuais(tituloDeAlt(a), true)}</details></div></div></div>`).join('');
}
window.desenharGanchos = desenharGanchos;
$('bGanchos').onclick = async () => {
  const s = segAtivo(); if (!s) return msg('Selecione um corte na linha do tempo.', 'warn');
  $('bGanchos').disabled = true; $('ganInfo').textContent = ''; PV.extraTit = [];
  try {
    st.ganchos = await tarefa('/api/ganchos/analisar', {plano: plano(), segmento: s.id, escopo: $('ganEscopo').value, provedor: provedor(),
      visual: envio(), permitir_envio: envio()}, $('ganProg'), 'Ganchos');
    desenharGanchos(); miniaturasAlternativas();
  } catch (e) { $('ganchos').innerHTML = `<div class="bad">${esc(e.message)}</div>`; }
  $('bGanchos').disabled = false;
};
$('ganchos').onchange = e => {   // edição do usuário fica guardada na sugestão
  const caixa = e.target.closest('[data-alt]');
  if (caixa) {   // alternativa visual: texto ou aparência mudou -> nova miniatura e prévia no player
    const a = st.ganchos.alternativas_visuais[+caixa.dataset.alt];
    const t = tituloDeAlt(a);
    if (e.target.dataset.altTexto) t.texto = e.target.value; else if (e.target.dataset.v) lerControle(e.target, t);
    const {texto, ini, fim, origem, ...vis} = t; a.texto = texto; a.fim = fim; a.visual = vis; a.mini = null;
    desenharGanchos(); miniaturasAlternativas(); if (PV.extraTit.length) verAlternativa(a, false); return;
  }
  const inp = e.target.closest('[data-c]'), box = e.target.closest('[data-g]'); if (!inp || !box) return;
  const c = st.ganchos.ganchos[+box.dataset.g];
  c[inp.dataset.c] = inp.dataset.c === 'texto' ? inp.value : parseFloat(inp.value);
};
$('ganchos').onclick = async e => {
  const b = e.target.closest('button'); if (!b || !st.ganchos) return;
  if (b.dataset.ouvir) return ouvirDe(b);
  const g = st.ganchos, a = b.dataset.a;
  try {
    const caixa = b.closest('[data-alt]');
    if (caixa) {
      const alt = g.alternativas_visuais[+caixa.dataset.alt];
      if (a === 'impacto' || a === 'discreto') {
        const t = tituloDeAlt(alt); (a === 'impacto' ? maisImpacto : maisDiscreto)(t);
        const {texto, ini, fim, origem, ...vis} = t; alt.visual = vis; alt.mini = null; desenharGanchos(); miniaturasAlternativas();
        return verAlternativa(alt, false);
      }
      if (a === 'verAlt') return verAlternativa(alt, true);
      if (a === 'aplicarAlt') { g.reg[alt.id] = await aplicarRegistrado([{tipo: 'adicionar_titulo', texto: alt.texto, ini: 0, fim: alt.fim, origem: origemDe(g), visual: alt.visual}]); g.estado[alt.id] = 'aplicada'; PV.extraTit = []; msg('Gancho aplicado: fica editável na aba Texto na tela e sai na exportação. “Desfazer” tira só ele.', 'ok'); }
      if (a === 'desfazerAlt' && desfazerRegistro(g.reg[alt.id])) { delete g.reg[alt.id]; delete g.estado[alt.id]; }
      if (a === 'salvarEstilo') { st.pref = await api('/api/preferencias', {method: 'PUT', json: {gancho: {...alt.visual, posicao: 'auto'}}}); msg('Estilo salvo: as próximas sugestões de gancho trazem “Meu estilo” primeiro.', 'ok'); }
      return desenharGanchos();
    }
    if (b.closest('[data-tudo]')) {
      const ops = g.proposta.operacoes;
      if (a === 'tudoPrevia') return previaCom(ops);
      if (a === 'tudo') { g.reg.tudo = await aplicarRegistrado(ops); g.estado.tudo = 'aplicada'; msg('Edição proposta aplicada. “Desfazer” tira só ela; Ctrl+Z também funciona.', 'ok'); }
      if (a === 'tudoVersao') { g.reg.tudo = await aplicarRegistrado(ops, 'edição proposta pelos ganchos', 'Gancho + título'); g.estado.tudo = 'aplicada'; }
      if (a === 'tudoDesfazer' && desfazerRegistro(g.reg.tudo)) { delete g.reg.tudo; delete g.estado.tudo; }
      return desenharGanchos();
    }
    const c = g.ganchos[+b.closest('[data-g]').dataset.g], op = opGancho(c);
    if (a === 'ouvir') return tocar(c.fonte, c.ini, c.fim);
    if (a === 'previa') return previaCom(op);
    if (a === 'aplicar') { g.reg[c.id] = await aplicarRegistrado([op]); g.estado[c.id] = 'aplicada'; }
    if (a === 'versao') { g.reg[c.id] = await aplicarRegistrado([op], `gancho: ${c.texto.slice(0, 40)}`, `Gancho ${fmt(c.ini || 0)}`); g.estado[c.id] = 'aplicada'; }
    if (a === 'comoTitulo') { g.reg[c.id] = await aplicarRegistrado([{tipo: 'adicionar_titulo', texto: c.texto.slice(0, 90), ini: 0, fim: 3, origem: origemDe(g)}]); g.estado[c.id] = 'aplicada'; }
    if (a === 'desfazer' && desfazerRegistro(g.reg[c.id])) { delete g.reg[c.id]; delete g.estado[c.id]; }
    if (a === 'rejeitar') g.estado[c.id] = 'rejeitada';
    desenharGanchos();
  } catch (err) { msg(esc(err.message), 'bad'); }
};

async function verAlternativa(alt, tocarJa) {   // mostra a alternativa AINDA NÃO aplicada sobre o corte, na vista Editado
  PV.extraTit = [{...tituloDeAlt(alt), id: 'previa'}];
  if (PV.vista !== 'editado') await mudarVista('editado', 0);
  await irParaSaida(0, tocarJa); if (tocarJa) PV.pararEm = alt.fim + 0.6;
  desenharCamada();
}

// ================================================================ efeitos sonoros (lista, ouvir, adicionar) e sugestões de áudio
function ouvirSom(som, dur, arquivo) {
  const a = $('audPrev'); a.src = som === 'arquivo' ? '/media/' + arquivo : `/api/efeitos/som/${som}?dur=${(+dur).toFixed(2)}`; a.play().catch(() => {});
}
function desenharEfs() {
  const p = plano(), out = Object.fromEntries((st.efsOut || []).map(e => [e.id, e]));
  p.audio.efeitos = p.audio.efeitos || [];
  $('efsLista').innerHTML = p.audio.efeitos.length ? '<div class="tabela"><table class="efs"><thead><tr><th>Som</th><th>Posição</th><th>Dur. (s)</th><th>Volume</th><th></th></tr></thead><tbody>' +
    p.audio.efeitos.map((e, i) => {
      const o = out[e.id], nome = e.som === 'arquivo' ? 'arquivo seu' : (st.sons[e.som] || {}).nome || e.som;
      const tag = e.origem === 'sugestao_ia' ? '<span class="tag ia">IA</span>' : e.origem === 'sugestao_local' ? '<span class="tag loc">local</span>' : '';
      const pos = e.fonte ? `fonte → ${o && o.t_saida != null ? 'saída ' + fmt(o.t_saida) : '<span class="warn">fora da linha do tempo</span>'}` : 'saída';
      return `<tr data-e="${i}"><td>${esc(nome)} ${tag}${e.objetivo ? `<div class="mut">${esc(e.objetivo)}</div>` : ''}</td>
        <td><span class="mut">${pos}</span><br><input type="number" step="0.1" min="0" value="${e.t}" data-c="t" aria-label="posição em segundos"></td>
        <td><input type="number" step="0.1" min="0.1" max="10" value="${e.dur}" data-c="dur" aria-label="duração"></td>
        <td><input type="number" step="0.05" min="0" max="1" value="${e.volume}" data-c="volume" aria-label="volume (0 a 1)"></td>
        <td style="white-space:nowrap"><button class="mini" data-a="ouvir" title="Só o som, isolado">▶ som</button><button class="mini" data-a="cena" title="Mix aproximado no navegador: fala + música + este efeito">🔊 nesta cena</button><button class="mini" data-a="rend" title="Trecho renderizado pelo motor da exportação">renderizado</button><button class="mini" data-a="rm" title="remover">✕</button></td></tr>`;
    }).join('') + '</tbody></table></div>' : '<div class="mut">Nenhum efeito na edição.</div>';
}
$('efsLista').onchange = e => {
  const inp = e.target.closest('[data-c]'); if (!inp) return; const i = +inp.closest('[data-e]').dataset.e, v = parseFloat(inp.value);
  if (!isFinite(v)) return desenharEfs();
  mudar(p => { p.audio.efeitos[i][inp.dataset.c] = v; });
};
$('efsLista').onclick = e => {
  const b = e.target.closest('button[data-a]'); if (!b) return; const i = +b.closest('[data-e]').dataset.e, ef = plano().audio.efeitos[i];
  if (b.dataset.a === 'ouvir') return ouvirSom(ef.som, ef.dur, ef.arquivo);
  if (b.dataset.a === 'cena') return ouvirNaCena(ef);
  if (b.dataset.a === 'rend') {
    const o = (st.efsOut || []).find(x => x.id === ef.id); if (!o || o.t_saida == null) return msg('Esse efeito está fora da linha do tempo.', 'warn');
    const a = Math.max(0, o.t_saida - 2); return mudarVista('render', a).then(() => renderizarTrecho(a, Math.min(st.durSaida, o.t_saida + ef.dur + 2), a));
  }
  if (b.dataset.a === 'rm') mudar(p => p.audio.efeitos.splice(i, 1));
};
$('efOuvir').onclick = () => { const k = $('efSom').value; if (st.sons[k]) ouvirSom(k, st.sons[k].dur); };
$('efAdd').onclick = () => {
  const fid = fonteVisivel(), k = $('efSom').value; if (!fid) return msg('Adicione uma fonte primeiro.', 'warn');
  const t = +vid.currentTime.toFixed(2);
  if (!fonteNaLinha(fid, t)) msg('Aviso: o cursor está num trecho que não entra na linha do tempo; o efeito fica guardado mas não toca.', 'warn');
  mudar(p => p.audio.efeitos.push({id: novoId('e'), som: k, arquivo: null, fonte: fid, t, dur: st.sons[k].dur, volume: 0.3, objetivo: '', origem: 'usuario'}));
};
function fonteNaLinha(fid, t) { return plano().segmentos.some(s => s.fonte === fid && s.ini <= t && t < s.fim); }
$('bSugAud').onclick = async () => {
  const s = segAtivo(); if (!s) return msg('Selecione um corte na linha do tempo.', 'warn');
  $('bSugAud').disabled = true;
  try {
    st.audSug = await tarefa('/api/audio/sugerir', {plano: plano(), segmento: s.id, escopo: $('audEscopo').value, provedor: provedor()}, $('audProg'),
      $('audEscopo').value === 'linha' ? 'Áudio (edição completa)' : 'Áudio (corte ativo)');
    st.audSug.estado = {}; st.audSug.reg = {}; desenharAudSug();
  } catch (e) { $('audSug').innerHTML = `<div class="bad">${esc(e.message)}</div>`; }
  $('bSugAud').disabled = false;
};
function itensAud() { const r = st.audSug; return r ? r.tratamento.concat(r.efeitos) : []; }
function opAud(x) { return x.tipo === 'efeito' ? {...x.operacao, t: x.t, dur: x.dur, volume: x.volume} : x.operacao; }
function desenharAudSug() {
  const r = st.audSug; if (!r) return;
  const itens = itensAud(), prontos = itens.filter(x => x.pronto && x.operacao);
  $('audSug').innerHTML = cabResultado(r) +
    (r.snr_db != null ? `<div class="mut">Fala x fundo nas pausas: ${r.snr_db} dB [DADO medido nestes cortes]</div>` : '') +
    (prontos.length > 1 ? `<div class="row esp"><button class="pri" data-a="tudo">Aplicar todas as prontas (${prontos.length})</button><button data-a="tudoPrevia">Prévia de todas</button>
      <button data-a="tudoDesfazer" ${r.reg.tudo ? '' : 'hidden'}>Desfazer</button></div>` : '') +
    itens.map((x, i) => `<div class="sug ${r.estado[x.id] || ''}" data-x="${i}">
      <div class="row"><b>${esc(x.nome)}</b><span class="tag">${x.tipo === 'tratamento' ? 'tratamento da voz' : 'efeito'}</span>
        ${x.pronto ? '<span class="chip ok">pronto para aplicar</span>' : '<span class="chip warn">precisa de um arquivo seu</span>'}</div>
      <div class="campo"><b>Objetivo:</b> ${esc(x.objetivo)}</div><div class="campo"><b>Por quê:</b> ${esc(x.motivo)}</div>
      ${x.tipo === 'efeito' ? `<div class="row"><span class="mut">${x.fonte ? 'na fonte' : 'na saída'}</span><input type="number" step="0.1" min="0" value="${x.t}" data-c="t" aria-label="posição"> s ·
        dur <input type="number" step="0.1" min="0.1" max="10" value="${x.dur}" data-c="dur" aria-label="duração"> s · vol <input type="number" step="0.05" min="0" max="1" value="${x.volume}" data-c="volume" aria-label="volume"></div>` : ''}
      <div class="acoes">${x.tipo === 'efeito' && x.pronto ? '<button class="mini" data-a="ouvir" title="Só o som, isolado">▶ ouvir o som</button><button class="mini" data-a="cena" title="Mix aproximado no navegador: fala + música + este efeito (ainda não aplicado)">🔊 Ouvir nesta cena</button>' : ''}
        ${x.pronto ? '<button class="mini" data-a="previa" title="Antes/depois renderizado pelo motor da exportação">Ouvir renderizado (antes/depois)</button><button class="mini pri" data-a="aplicar">Aplicar</button>'
          : '<label class="mut" style="margin:0">Enviar o som (com direito de uso): <input type="file" accept="audio/*" data-arq="1"></label>'}
        <button class="mini" data-a="desfazer" ${r.reg[x.id] ? '' : 'hidden'}>Desfazer</button><button class="mini" data-a="rejeitar">Rejeitar</button></div></div>`).join('');
}
$('audSug').onchange = async e => {
  const box = e.target.closest('[data-x]'); if (!box || !st.audSug) return;
  const x = itensAud()[+box.dataset.x];
  if (e.target.dataset.c) { const v = parseFloat(e.target.value); if (isFinite(v)) x[e.target.dataset.c] = v; return; }
  if (e.target.dataset.arq) {   // som que não existia pronto: vira efeito "arquivo" com o upload do usuário
    const f = e.target.files[0]; if (!f) return;
    try {
      const j = await enviar(f); if (!j.tem_audio) throw new Error('Esse arquivo não tem áudio.');
      st.audSug.reg[x.id] = await aplicarRegistrado([{tipo: 'adicionar_efeito', som: 'arquivo', arquivo: j.id, fonte: x.fonte, t: x.t, dur: x.dur,
        volume: x.volume, objetivo: x.objetivo, origem: origemDe(st.audSug)}]);
      st.audSug.estado[x.id] = 'aplicada'; desenharAudSug();
    } catch (err) { msg(esc(err.message), 'bad'); }
  }
};
$('audSug').onclick = async e => {
  const b = e.target.closest('button[data-a]'); if (!b || !st.audSug) return;
  const r = st.audSug, a = b.dataset.a;
  try {
    if (a === 'tudo' || a === 'tudoPrevia') {
      const ops = itensAud().filter(x => x.pronto && x.operacao && r.estado[x.id] !== 'rejeitada').map(opAud);
      if (a === 'tudoPrevia') { const a0 = Math.max(0, saidaAtual() - 2); return previaCom(ops, a0, a0 + 10, 'com o áudio sugerido'); }
      r.reg.tudo = await aplicarRegistrado(ops); itensAud().forEach(x => { if (x.pronto) r.estado[x.id] = 'aplicada'; });
      return desenharAudSug();
    }
    if (a === 'tudoDesfazer') { if (desfazerRegistro(r.reg.tudo)) { delete r.reg.tudo; r.estado = {}; } return desenharAudSug(); }
    const x = itensAud()[+b.closest('[data-x]').dataset.x];
    if (a === 'ouvir') return ouvirSom(x.som, x.dur);
    if (a === 'cena') return ouvirNaCena({som: x.som, arquivo: null, fonte: x.fonte, t: x.t, dur: x.dur, volume: x.volume});
    if (a === 'previa') {
      const ts = x.tipo === 'efeito' ? (x.fonte ? fonteParaSaida(x.fonte, x.t) : x.t) : saidaAtual();
      const a0 = Math.max(0, (ts ?? 0) - 2);
      return previaCom(opAud(x), a0, a0 + (x.tipo === 'efeito' ? x.dur + 4 : 8), x.nome);
    }
    if (a === 'aplicar') { r.reg[x.id] = await aplicarRegistrado([opAud(x)]); r.estado[x.id] = 'aplicada'; }
    if (a === 'desfazer' && desfazerRegistro(r.reg[x.id])) { delete r.reg[x.id]; delete r.estado[x.id]; }
    if (a === 'rejeitar') r.estado[x.id] = 'rejeitada';
    desenharAudSug();
  } catch (err) { msg(esc(err.message), 'bad'); }
};

// ================================================================ prévia integrada no player principal
// Três vistas: Original (a fonte), Editado (aproximado: linha do tempo tocada no navegador, com legenda e texto na
// tela desenhados por cima e áudio mixado via Web Audio) e Renderizado (trecho curto feito pelo MESMO render_plano
// da exportação). Resultado de render velho nunca substitui um pedido mais novo.
const vidR = $('vidR');
var PV = {vista: 'original', idx: 0, pulando: false, ctx: null, gVid: null, gMus: null, musEl: null, bufs: {}, nos: [],
  extra: [], extraTit: [], pararEm: null, rend: null, alt: null, mostrandoAlt: false, rendGen: 0, rendTarefa: null, emCurso: null,
  erroRend: null, tAuto: null, chaveLeg: '', chaveTit: ''};
const chavePlano = () => JSON.stringify(plano());
function mapaAtual() { return st.mapa || []; }
function segDaSaida(t) {
  const m = mapaAtual(); if (!m.length) return null;
  return m.find(x => t >= x.out_ini && t < x.out_fim) || (t >= m.at(-1).out_fim ? m.at(-1) : m[0]);
}
function fonteParaSaida(f, t) { const x = mapaAtual().find(m => m.fonte === f && t >= m.ini && t < m.fim); return x ? x.out_ini + t - x.ini : null; }
function saidaAtual() {
  if (PV.vista === 'render') { const r = PV.mostrandoAlt ? PV.alt : PV.rend; return r ? r.a + vidR.currentTime : 0; }
  if (PV.vista === 'editado') { const m = mapaAtual()[PV.idx]; return m ? m.out_ini + (vid.currentTime - m.ini) : 0; }
  const f = fonteVisivel(), t = fonteParaSaida(f, vid.currentTime);
  if (t != null) return t;
  const prox = mapaAtual().find(m => m.fonte === f && m.ini > vid.currentTime);   // fora dos cortes: o próximo trecho
  return prox ? prox.out_ini : 0;
}
function carregarFonte(fid) {
  return new Promise(ok => {
    if (vid.src.endsWith('/media/' + fid) && vid.readyState >= 1) return ok();
    vid.addEventListener('loadedmetadata', () => ok(), {once: true});
    $('fonteVis').value = fid; trocarFonte();
  });
}
async function irParaSaida(t, tocarDepois) {
  const m = segDaSaida(t); if (!m) return;
  PV.pulando = true;
  try {
    PV.idx = mapaAtual().indexOf(m);
    await carregarFonte(m.fonte);
    vid.currentTime = Math.min(m.fim - 0.02, Math.max(m.ini, m.ini + (t - m.out_ini)));
    if (tocarDepois) await vid.play().catch(() => {});
  } finally { PV.pulando = false; }
  desenharCamada(); if (tocarDepois) agendarAudio();
}
async function mudarVista(v, tAlvo, posicionar = true) {
  const t = tAlvo ?? saidaAtual();
  const tocando = !vid.paused || !vidR.paused;
  vid.pause(); vidR.pause(); pararAudio();
  PV.vista = v;
  document.querySelectorAll('.seg [data-vista]').forEach(b => { const on = b.dataset.vista === v; b.classList.toggle('on', on); b.setAttribute('aria-checked', on); });
  $('stage').classList.toggle('editado', v === 'editado');
  vid.hidden = v === 'render'; vidR.hidden = v !== 'render'; $('rendCtl').hidden = v !== 'render';
  if (PV.gVid) PV.gVid.gain.value = v === 'editado' ? plano().audio.volume_original : 1;
  infoVista(); statusPrevia();
  if (!posicionar) { desenharCamada(); moverCrop(); return; }
  if (v === 'original') {
    const m = segDaSaida(t);
    if (m) { await carregarFonte(m.fonte); vid.currentTime = m.ini + Math.max(0, t - m.out_ini); }
    moverCrop();
  } else if (v === 'editado') {
    if (!mapaAtual().length) return msg('Adicione um corte para ver a edição.', 'warn');
    garantirAudio(); await irParaSaida(t, tocando);
  } else {
    const r = PV.rend;
    if (r && t >= r.a && t < r.b && r.chave === chavePlano()) mostrarRender(t);
    else { const a = Math.max(0, Math.min(t - 2, st.durSaida - 10)); renderizarTrecho(a, Math.min(st.durSaida || a + 10, a + 10), t); }
  }
  desenharCamada();
}
document.querySelector('.seg').onclick = e => { const b = e.target.closest('[data-vista]'); if (b) mudarVista(b.dataset.vista); };
$('bAB').onclick = () => mudarVista(PV.vista === 'original' ? 'editado' : 'original');
function infoVista() {
  const txt = {
    original: 'Original: o vídeo-fonte, sem edição. A moldura mostra o enquadramento 9:16.',
    editado: 'Editado (aproximado, no navegador): segue a linha do tempo com legenda e texto na tela desenhados por cima, volume, música e efeitos mixados. ' +
      'Não mostra zoom, fade, realce de voz, normalização nem o abaixamento da música: para isso use Renderizado.',
    render: 'Renderizado: trecho em 540×960 feito pelo mesmo motor e plano da exportação (fiel). Zoom e música podem começar diferente quando o trecho corta um segmento ao meio.'};
  $('vistaInfo').innerHTML = esc(txt[PV.vista]) + ' <span id="tSaida" class="mut"></span>';
}

// ---- legenda e texto na tela desenhados sobre o player (aproximação do libass; a exportação usa o libass)
function caixaCrop() {
  const w = vid.clientWidth, h = vid.clientHeight; if (!w || !vid.videoWidth) return null;
  const ar = vid.videoWidth / vid.videoHeight, vw = Math.min(w, h * ar), vh = vw / ar, ox = (w - vw) / 2, oy = (h - vh) / 2;
  const bw = Math.min(vw, vh * 9 / 16);
  return {x: ox + (vw - bw) * plano().quadro.crop_x, y: oy, w: bw, h: vh};
}
const hex2rgba = (h, a) => `rgba(${parseInt(h.slice(1, 3), 16)},${parseInt(h.slice(3, 5), 16)},${parseInt(h.slice(5, 7), 16)},${a})`;
const contorno = (px, cor) => px <= 0 ? 'none' : [0, 45, 90, 135, 180, 225, 270, 315].map(g => `${(Math.cos(g * Math.PI / 180) * px).toFixed(1)}px ${(Math.sin(g * Math.PI / 180) * px).toFixed(1)}px 0 ${cor}`).join(',');
function desenharCamada() {
  const cam = $('camada');
  if (PV.vista !== 'editado') { cam.hidden = true; return; }
  const c = caixaCrop(); if (!c) { cam.hidden = true; return; }
  cam.hidden = false; Object.assign(cam.style, {left: c.x + 'px', top: c.y + 'px', width: c.w + 'px', height: c.h + 'px'});
  const t = saidaAtual(), k = c.h / 1920, p = plano(), e = p.legenda.estilo;
  if ($('tSaida')) $('tSaida').textContent = `· saída ${fmt(t)} de ${fmt(st.durSaida)}`;
  let leg = '';
  const ev = p.legenda.ativa ? (st.eventos || []).find(x => t >= x.ini && t < x.fim && x.palavras) : null;
  if (ev) {
    const ativa = e.destaque ? ev.palavras.reduce((acc, w, i) => (w[1] <= t ? i : acc), 0) : -1;
    const pal = ev.palavras.map((w, i) => `<span style="color:${i === ativa ? e.destaque_cor : e.cor}">${esc(e.maiusculas ? w[0].toUpperCase() : w[0])}</span>`).join(' ');
    const fs = e.tamanho * k * 0.85;
    const css = `top:${e.posicao_y * 100}%;font-family:'${esc(e.fonte)}',Arial,sans-serif;font-size:${fs.toFixed(1)}px;font-weight:${e.negrito ? 700 : 400};` +
      (e.fundo ? '' : `text-shadow:${contorno(e.contorno * k, e.contorno_cor)}`);
    leg = `<div class="ov" style="${css}">${e.fundo ? `<span class="caixa" style="background:${hex2rgba(e.fundo_cor, e.fundo_opacidade)}">${pal}</span>` : pal}</div>`;
  }
  if (leg !== PV.chaveLeg) { $('ovLeg').innerHTML = leg; PV.chaveLeg = leg; }
  const tits = p.titulos.concat(PV.extraTit || []).filter(x => t >= x.ini && t < x.fim).map(x => {
    const v = visualEfetivo(x), dt = t - x.ini, fim = x.fim - t;
    const dest = new Set(x.destaque || []);
    const pal = String(x.texto).split(/\s+/).map((w, i) => `<span style="color:${dest.has(i) ? v.destaque_cor : v.cor}">${esc(v.maiusculas ? w.toUpperCase() : w)}</span>`).join(' ');
    let op = 1, tr = 'translateY(-50%)';
    if (v.animacao === 'fade') op = Math.min(1, dt / 0.25, fim / 0.2);
    else if (v.animacao === 'pop') { const e = 1.3 - 0.3 * Math.min(1, dt / 0.18); tr += ` scale(${e.toFixed(3)})`; op = Math.min(1, dt / 0.06, fim / 0.15); }
    else if (v.animacao === 'deslizar') { tr += ` translateX(${(-260 * k * (1 - Math.min(1, dt / 0.22))).toFixed(1)}px)`; op = Math.min(1, dt / 0.12, fim / 0.15); }
    const css = `top:${(x.posicao_y ?? 0.14) * 100}%;transform:${tr};opacity:${Math.max(0, op).toFixed(2)};font-family:'${esc(v.fonte)}',Arial,sans-serif;font-weight:700;` +
      `font-style:${v.italico ? 'italic' : 'normal'};font-size:${((x.tamanho || 64) * k * 0.85).toFixed(1)}px;` + (v.caixa ? '' : `text-shadow:${contorno((v.borda || 7) * k, v.fundo_cor)}`);
    return `<div class="ov" style="${css}">${v.caixa ? `<span class="caixa" style="background:${hex2rgba(v.fundo_cor, 0.92)}">${pal}</span>` : pal}</div>`;
  }).join('');
  if (tits !== PV.chaveTit) { $('ovTits').innerHTML = tits; PV.chaveTit = tits; }
}
window.addEventListener('resize', desenharCamada);

// ---- áudio da vista Editado: fala (com o volume do plano), música e efeitos no mesmo relógio
function garantirAudio() {
  if (PV.ctx) return;
  const AC = window.AudioContext || window.webkitAudioContext; if (!AC) return;
  PV.ctx = new AC();
  PV.gVid = PV.ctx.createGain(); PV.ctx.createMediaElementSource(vid).connect(PV.gVid).connect(PV.ctx.destination);
  PV.musEl = new Audio(); PV.musEl.loop = true; PV.musEl.crossOrigin = 'anonymous';
  PV.gMus = PV.ctx.createGain(); PV.ctx.createMediaElementSource(PV.musEl).connect(PV.gMus).connect(PV.ctx.destination);
  PV.gVid.gain.value = PV.vista === 'editado' ? plano().audio.volume_original : 1;
}
function pararAudio() { PV.nos.forEach(n => { try { n.stop(); } catch (e) { /* já parou */ } }); PV.nos = []; if (PV.musEl) PV.musEl.pause(); }
async function bufferEfeito(e) {
  const url = e.som === 'arquivo' ? '/media/' + e.arquivo : `/api/efeitos/som/${e.som}?dur=${(+e.dur).toFixed(2)}`;
  if (!PV.bufs[url]) PV.bufs[url] = fetch(url).then(r => r.arrayBuffer()).then(b => PV.ctx.decodeAudioData(b)).catch(() => null);
  return PV.bufs[url];
}
function efeitosParaTocar() {
  const out = Object.fromEntries((st.efsOut || []).map(e => [e.id, e]));
  const doPlano = (plano().audio.efeitos || []).map(e => ({...e, t_saida: out[e.id] ? out[e.id].t_saida : null}));
  return doPlano.concat(PV.extra);
}
async function agendarAudio() {
  pararAudio();
  if (PV.vista !== 'editado' || vid.paused || !PV.ctx) return;
  await PV.ctx.resume();
  const p = plano(), t0 = saidaAtual();
  PV.gVid.gain.value = p.audio.volume_original;
  if (p.audio.musica.id) {
    const src = '/media/' + p.audio.musica.id;
    if (!PV.musEl.src.endsWith(src)) PV.musEl.src = src;
    PV.gMus.gain.value = p.audio.musica.volume;
    const pos = () => { try { PV.musEl.currentTime = PV.musEl.duration ? t0 % PV.musEl.duration : t0; } catch (e) { /* ainda carregando */ } };
    if (PV.musEl.readyState >= 1) pos(); else PV.musEl.addEventListener('loadedmetadata', pos, {once: true});
    PV.musEl.play().catch(() => {});
  }
  for (const e of efeitosParaTocar()) {
    if (e.t_saida == null || e.t_saida + e.dur < t0) continue;
    const buf = await bufferEfeito(e);
    if (!buf || PV.vista !== 'editado' || vid.paused) continue;
    const atraso = e.t_saida - saidaAtual(), off = Math.max(0, -atraso), dur = Math.min(e.dur, buf.duration);
    if (off >= dur) continue;
    const n = PV.ctx.createBufferSource(), g = PV.ctx.createGain();
    n.buffer = buf; g.gain.value = e.volume; n.connect(g).connect(PV.ctx.destination);
    n.start(PV.ctx.currentTime + Math.max(0, atraso), off, Math.max(0.01, dur - off)); PV.nos.push(n);
  }
}
vid.addEventListener('play', () => { if (PV.ctx) PV.ctx.resume(); if (PV.vista === 'editado' && !PV.pulando) agendarAudio(); });
vid.addEventListener('pause', () => { if (PV.vista === 'editado' && !PV.pulando) pararAudio(); });
vid.addEventListener('seeked', () => {
  if (PV.vista !== 'editado' || PV.pulando) return;
  const m = mapaAtual()[PV.idx];   // procura manual: acha o trecho da linha que contém esse instante da fonte
  if (!m || vid.currentTime < m.ini - 0.05 || vid.currentTime > m.fim + 0.05) {
    const k = mapaAtual().findIndex(x => x.fonte === fonteVisivel() && vid.currentTime >= x.ini && vid.currentTime < x.fim);
    if (k >= 0) PV.idx = k;
  }
  if (!vid.paused) agendarAudio(); desenharCamada();
});
['vo', 'vm'].forEach(id => $(id).addEventListener('input', () => {   // volume muda na hora, durante a reprodução
  if (!PV.gVid) return;
  if (id === 'vo' && PV.vista === 'editado') PV.gVid.gain.value = +$('vo').value;
  if (id === 'vm') PV.gMus.gain.value = +$('vm').value;
}));
async function ouvirNaCena(ef) {   // mix aproximado: fala + música + o efeito, no momento em que ele toca
  const ts = ef.t_saida ?? (ef.fonte ? fonteParaSaida(ef.fonte, ef.t) : ef.t);
  if (ts == null) return msg('Esse momento não está na linha do tempo atual: o efeito não tocaria.', 'warn');
  const jaNoPlano = ef.id && (plano().audio.efeitos || []).some(x => x.id === ef.id);
  PV.extra = jaNoPlano ? [] : [{...ef, t_saida: ts}];
  if (PV.vista !== 'editado') await mudarVista('editado', Math.max(0, ts - 1.5));
  garantirAudio(); PV.pararEm = ts + ef.dur + 1.2;
  await irParaSaida(Math.max(0, ts - 1.5), true);
  msg(`Ouvindo na cena (${jaNoPlano ? 'efeito aplicado' : 'efeito ainda NÃO aplicado'}): mix aproximado no navegador, ${fmt(Math.max(0, ts - 1.5))}–${fmt(ts + ef.dur + 1.2)} da saída. Para o som fiel, use “Ouvir renderizado”.`);
}

// ---- trecho renderizado (mesmo plano/motor da exportação), com estado e descarte de resultado velho
function statusPrevia() {
  const el = $('prevStatus');
  if (PV.vista === 'original') { el.textContent = ''; el.className = 'chip'; return; }
  if (PV.vista === 'editado') { el.textContent = 'Aproximado · atualiza na hora'; el.className = 'chip'; return; }
  if (PV.emCurso) { el.textContent = 'Atualizando prévia…'; el.className = 'chip warn'; }
  else if (PV.erroRend) { el.textContent = 'Prévia falhou'; el.className = 'chip bad'; el.title = PV.erroRend; }
  else if (!PV.rend) { el.textContent = 'Sem prévia renderizada'; el.className = 'chip'; }
  else if (PV.rend.chave === chavePlano()) { el.textContent = 'Prévia em dia'; el.className = 'chip ok'; }
  else { el.textContent = 'Prévia desatualizada'; el.className = 'chip bad'; }
  $('rendAlt').innerHTML = PV.alt ? `<span class="chip ${PV.mostrandoAlt ? 'warn' : ''}">${PV.mostrandoAlt ? 'Mostrando: ' + esc(PV.alt.rotulo) : 'Mostrando: plano atual'}</span>
    <button class="mini" id="bAltTroca">${PV.mostrandoAlt ? 'Ver o atual (antes)' : 'Ver com a sugestão (depois)'}</button><button class="mini" id="bAltFecha">fechar comparação</button>` : '';
}
function mostrarRender(t) {
  const r = PV.mostrandoAlt ? PV.alt : PV.rend; if (!r || PV.vista !== 'render') return statusPrevia();
  const alvo = Math.max(0, Math.min((t ?? saidaAtual()) - r.a, r.b - r.a - 0.05));
  const tocando = !vidR.paused;
  const pos = () => { vidR.currentTime = alvo; if (tocando) vidR.play().catch(() => {}); };
  if (!vidR.src.endsWith(r.url)) { vidR.src = r.url; vidR.addEventListener('loadedmetadata', pos, {once: true}); } else pos();
  statusPrevia();
}
async function renderizarTrecho(a, b, tAlvo) {
  const gen = ++PV.rendGen;
  if (!(b - a > 0.5)) return;
  PV.erroRend = null;
  let j;
  try { j = await api('/api/plano/validar', {json: {plano: plano()}}); } catch (e) { PV.erroRend = e.message; return statusPrevia(); }
  if (gen !== PV.rendGen) return;
  const chave = JSON.stringify(j.plano);
  if (PV.rendTarefa) api(`/api/tarefas/${PV.rendTarefa}/cancelar`, {method: 'POST'}).catch(() => {});
  PV.emCurso = {chave, a, b}; statusPrevia();
  try {
    const t = await api('/api/render', {json: {plano: j.plano, qualidade: 'previa', trecho: [a, b]}});
    PV.rendTarefa = t.id;
    const r = await acompanhar(t, $('rendProg'), `Prévia do trecho ${fmt(a)}–${fmt(b)}`);
    if (gen !== PV.rendGen) return;   // um pedido mais novo já existe: este resultado é velho e é descartado
    PV.rend = {url: r.url, a, b, chave, avisos: r.avisos}; PV.mostrandoAlt = false;
    mostrarRender(tAlvo);
  } catch (e) { if (gen === PV.rendGen && !/Cancelado/.test(e.message)) PV.erroRend = e.message; }
  finally { if (gen === PV.rendGen) { PV.emCurso = null; PV.rendTarefa = null; statusPrevia(); } }
}
$('bRendTrecho').onclick = () => { const t = saidaAtual(), a = Math.max(0, Math.min(t - 2, st.durSaida - 10)); renderizarTrecho(a, Math.min(st.durSaida, a + 10), t); };
$('rendAlt').onclick = e => {
  if (e.target.id === 'bAltTroca') { const t = saidaAtual(); PV.mostrandoAlt = !PV.mostrandoAlt; mostrarRender(t); }
  if (e.target.id === 'bAltFecha') { const t = saidaAtual(); PV.alt = null; PV.mostrandoAlt = false; mostrarRender(t); }
};
function aoMudarPlano() {   // chamada em todo desenho: marca prévias velhas e, se pedido, re-renderiza com atraso
  statusPrevia();
  if (PV.alt && PV.alt.base !== chavePlano()) { PV.alt = null; PV.mostrandoAlt = false; }
  if (PV.vista === 'editado') { desenharCamada(); if (!vid.paused) agendarAudio(); }
  clearTimeout(PV.tAuto);
  if (PV.vista === 'render' && $('rendAuto').checked && PV.rend && PV.rend.chave !== chavePlano()
      && !(PV.emCurso && PV.emCurso.chave === chavePlano())) {
    PV.tAuto = setTimeout(() => { if (PV.rend && PV.rend.chave !== chavePlano()) renderizarTrecho(PV.rend.a, Math.min(PV.rend.b, st.durSaida || PV.rend.b)); }, 1500);
  }
  const pv = $('previaEstado');
  if (pv) pv.textContent = st.previaChave && st.previaChave !== chavePlano() ? '⚠ Prévia completa desatualizada: o plano mudou depois dela.' : '';
}
// Antes/depois de uma sugestão, no player: renderiza o MESMO trecho com e sem a mudança.
async function compararNoPlayer(ops, a, b, rotulo) {
  const j = await api('/api/operacao', {json: {plano: plano(), operacoes: ops}});
  const dur = st.durSaida || 10;
  a = Math.max(0, a ?? 0); b = Math.min(Math.max(b ?? a + 10, a + 1), a + 30);
  if (PV.vista !== 'render') await mudarVista('render', a);
  if (!PV.rend || PV.rend.a !== a || PV.rend.b !== Math.min(b, dur) || PV.rend.chave !== chavePlano()) await renderizarTrecho(a, Math.min(b, dur), a);
  const base = chavePlano();
  const t = await api('/api/render', {json: {plano: j.plano, qualidade: 'previa', trecho: [a, b]}});
  const r = await acompanhar(t, $('rendProg'), 'Prévia com a sugestão (nada aplicado ainda)');
  if (base !== chavePlano()) return msg('O plano mudou enquanto a prévia renderizava: comparação descartada.', 'warn');
  PV.alt = {url: r.url, a, b, rotulo: rotulo || 'com a sugestão', base}; PV.mostrandoAlt = true; mostrarRender(a);
  msg('No player: “depois” (com a sugestão, ainda não aplicada). Use o botão abaixo do vídeo para ver o “antes”.', 'ok');
}

(async function iniciar() {
  try { const j = await api('/api/presets'); st.presets = j.presets; st.estilosTit = j.estilos_titulo || {}; } catch (e) { msg('Não carreguei os estilos: ' + esc(e.message), 'bad'); }
  try { st.sons = await api('/api/efeitos'); $('efSom').innerHTML = Object.entries(st.sons).map(([k, v]) => `<option value="${k}" title="${esc(v.uso)}">${esc(v.nome)}</option>`).join(''); } catch (e) { /* efeitos opcionais */ }
  try { const pv = localStorage.getItem('cs_provedor'); if (pv) $('provedor').value = pv; $('envioQuadros').checked = localStorage.getItem('cs_envio_quadros') === '1'; } catch (e) { /* opcional */ }
  const v = {...st.presets.classico}; delete v.nome; st.versoes[0].plano.legenda.estilo = v;
  try { st.pref = await api('/api/preferencias'); } catch (e) { st.pref = {}; }
  tudo(); listarProjetos(); infoVista(); statusPrevia();
  const pid = new URLSearchParams(location.search).get('p');
  if (pid) abrir(pid); else validarDepois();
  carregarConfig();
})();
