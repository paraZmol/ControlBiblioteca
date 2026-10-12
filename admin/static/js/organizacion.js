// organizacion.js - Reportes imprimibles, ciclos academicos y sedes.
// Depende de app.js (API_BASE, authHeaders, escapeHtml, mostrarNotificacion,
// mostrarConfirmacion, _rolServidor). Sin dependencias externas: la impresion
// genera un documento autonomo (CSS en linea) y funciona sin internet.

// ── Estado compartido ────────────────────────────────────────────────
let _orgSedes = [];          // sedes activas [{id, nombre, ...}]
let _orgMiSede = null;       // sede fija del usuario (encargado limitado) o null
let _orgCiclos = [];         // ciclos [{id, nombre, fecha_inicio, fecha_fin, actual}]
let _orgSedesCargadas = false;

async function _orgFetch(url, opts = {}) {
    const res = await fetch(`${API_BASE}${url}`, { headers: authHeaders(), cache: 'no-store', ...opts });
    let data = null;
    try { data = await res.json(); } catch (e) { /* respuesta sin cuerpo */ }
    if (!res.ok) {
        const msg = (data && (typeof data.detail === 'string' ? data.detail : null)) || `Error ${res.status}`;
        throw new Error(msg);
    }
    return data;
}

async function orgCargarSedes(forzar = false) {
    if (_orgSedesCargadas && !forzar) return _orgSedes;
    try {
        const d = await _orgFetch('/admin/sedes');
        _orgSedes = d.items || [];
        _orgMiSede = d.mi_sede || null;
        _orgSedesCargadas = true;
    } catch (e) {
        _orgSedes = [];
    }
    return _orgSedes;
}

function _orgNombreSede(id) {
    const s = _orgSedes.find(x => x.id === id);
    return s ? s.nombre : 'Sin sede';
}

function _orgOpcionesSede(select, { incluirTodas = null, incluirNinguna = null, valor = '' } = {}) {
    let html = '';
    if (incluirTodas)   html += `<option value="">${escapeHtml(incluirTodas)}</option>`;
    if (incluirNinguna) html += `<option value="__none">${escapeHtml(incluirNinguna)}</option>`;
    html += _orgSedes.map(s => `<option value="${s.id}">${escapeHtml(s.nombre)}</option>`).join('');
    select.innerHTML = html;
    select.value = valor === null || valor === undefined ? '' : String(valor);
}

function _fmtFecha(iso) {
    if (!iso) return '—';
    const [y, m, d] = iso.slice(0, 10).split('-');
    return `${d}/${m}/${y}`;
}

function _hoyISO() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function _num(n) { return Number(n || 0).toLocaleString('es-PE'); }


// ══════════════════════════════════════════════════════════════════════
//  REPORTES
// ══════════════════════════════════════════════════════════════════════

let _repTipo = 'mes';
let _repInicializado = false;
let _repUltimo = null;      // ultimo reporte de estadisticas generado
let _repSeq = 0;            // descarta respuestas viejas si el usuario cambia rapido

async function repInit() {
    if (!_repInicializado) {
        _repInicializado = true;
        const hoy = _hoyISO();
        document.getElementById('rep-dia').value = hoy;
        document.getElementById('rep-mes').value = hoy.slice(0, 7);
        document.getElementById('rep-anio').value = hoy.slice(0, 4);
        document.getElementById('rep-desde').value = hoy.slice(0, 8) + '01';
        document.getElementById('rep-hasta').value = hoy;
    }
    await Promise.all([orgCargarSedes(true), _repCargarCiclos()]);

    // Selector de sede: solo si el usuario puede elegir y hay mas de una sede.
    const campo = document.getElementById('rep-sede-campo');
    const sel = document.getElementById('rep-sede');
    const previo = sel.value;
    if (!_orgMiSede && _orgSedes.length > 1) {
        _orgOpcionesSede(sel, { incluirTodas: 'Todas las sedes', valor: previo });
        campo.style.display = '';
    } else {
        campo.style.display = 'none';
        sel.innerHTML = '';
    }
    repSetTipo(_repTipo);
}

async function _repCargarCiclos() {
    const sel = document.getElementById('rep-ciclo');
    try {
        const d = await _orgFetch('/admin/ciclos');
        _orgCiclos = d.items || [];
    } catch (e) {
        _orgCiclos = [];
    }
    const previo = sel.value;
    if (!_orgCiclos.length) {
        sel.innerHTML = '<option value="">No hay ciclos registrados</option>';
        return;
    }
    sel.innerHTML = _orgCiclos.map(c =>
        `<option value="${c.id}">${escapeHtml(c.nombre)}${c.actual ? ' (actual)' : ''} — ${_fmtFecha(c.fecha_inicio)} al ${_fmtFecha(c.fecha_fin)}</option>`
    ).join('');
    const actual = _orgCiclos.find(c => c.actual);
    sel.value = previo && _orgCiclos.some(c => String(c.id) === previo) ? previo : String((actual || _orgCiclos[0]).id);
}

function repSetTipo(tipo) {
    _repTipo = tipo;
    document.querySelectorAll('[data-rep-tipo]').forEach(b => {
        const on = b.dataset.repTipo === tipo;
        b.classList.toggle('kpi-periodo-activo', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    document.querySelectorAll('[data-rep-para]').forEach(el => {
        el.style.display = el.dataset.repPara === tipo ? '' : 'none';
    });
    if (tipo !== 'rango') repGenerar();
}

// Traduce el periodo elegido a parametros de la API. Lanza Error con un
// mensaje para el operador si falta algo.
function _repParams() {
    const p = new URLSearchParams();
    if (_repTipo === 'dia') {
        const v = document.getElementById('rep-dia').value;
        if (!v) throw new Error('Elige la fecha');
        p.set('desde', v); p.set('hasta', v);
    } else if (_repTipo === 'mes') {
        const v = document.getElementById('rep-mes').value;  // AAAA-MM
        if (!v) throw new Error('Elige el mes');
        const [y, m] = v.split('-').map(Number);
        const ultimo = new Date(y, m, 0).getDate();
        p.set('desde', `${v}-01`); p.set('hasta', `${v}-${String(ultimo).padStart(2, '0')}`);
    } else if (_repTipo === 'ciclo') {
        const v = document.getElementById('rep-ciclo').value;
        if (!v) throw new Error('No hay ciclos registrados. El superadmin los configura en Configuración > Ciclos académicos.');
        p.set('ciclo_id', v);
    } else if (_repTipo === 'anio') {
        const y = parseInt(document.getElementById('rep-anio').value, 10);
        if (!y || y < 2000 || y > 2100) throw new Error('Escribe un año válido');
        p.set('desde', `${y}-01-01`); p.set('hasta', `${y}-12-31`);
    } else {
        const d = document.getElementById('rep-desde').value;
        const h = document.getElementById('rep-hasta').value;
        if (!d || !h) throw new Error('Completa las dos fechas del rango');
        if (h < d) throw new Error('La fecha "Hasta" no puede ser anterior a "Desde"');
        p.set('desde', d); p.set('hasta', h);
    }
    const sede = document.getElementById('rep-sede')?.value;
    if (sede) p.set('id_sede', sede);
    return p;
}

function _repTituloPeriodo(per) {
    if (per.ciclo) return `Ciclo ${per.ciclo} (${_fmtFecha(per.desde)} al ${_fmtFecha(per.hasta)})`;
    if (per.desde === per.hasta) return `Día ${_fmtFecha(per.desde)}`;
    const meses = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
    if (_repTipo === 'mes') { const [y, m] = per.desde.split('-'); return `${meses[+m - 1][0].toUpperCase()}${meses[+m - 1].slice(1)} ${y}`; }
    if (_repTipo === 'anio') return `Año ${per.desde.slice(0, 4)}`;
    return `Del ${_fmtFecha(per.desde)} al ${_fmtFecha(per.hasta)}`;
}

async function repGenerar() {
    const errEl = document.getElementById('rep-error');
    const out = document.getElementById('rep-resultado');
    errEl.textContent = '';
    let params;
    try { params = _repParams(); } catch (e) { errEl.textContent = e.message; out.innerHTML = ''; _repUltimo = null; return; }
    const seq = ++_repSeq;
    out.innerHTML = '<p class="kpi-empty">Generando reporte…</p>';
    try {
        const d = await _orgFetch(`/admin/reportes/estadisticas?${params}`);
        if (seq !== _repSeq) return;
        _repUltimo = d;
        out.innerHTML = _repHtmlVista(d);
    } catch (e) {
        if (seq !== _repSeq) return;
        out.innerHTML = '';
        errEl.textContent = e.message || 'No se pudo generar el reporte';
    }
}

function _repBarras(items, { campo = 'sesiones', etiqueta = 'nombre', max = 10 } = {}) {
    const lista = (items || []).slice(0, max);
    if (!lista.length) return '<p class="rep-vacio">Sin datos en este periodo</p>';
    const tope = Math.max(...lista.map(i => i[campo] || 0), 1);
    return `<div class="rep-barras">${lista.map(i => `
        <div class="rep-barra-fila">
            <span class="rep-barra-lbl" title="${escapeHtml(i[etiqueta])}">${escapeHtml(i[etiqueta])}</span>
            <span class="rep-barra-pista"><span class="rep-barra" style="width:${Math.max(2, Math.round((i[campo] || 0) * 100 / tope))}%"></span></span>
            <span class="rep-barra-val">${_num(i[campo])}</span>
        </div>`).join('')}</div>`;
}

function _repHtmlVista(d) {
    const r = d.resumen, inc = d.incidencias, per = d.periodo;
    const kpi = (lbl, val, ico, extra = '') =>
        `<div class="rep-kpi${extra}${String(val).length > 8 ? ' rep-kpi-txt' : ''}"><i class="ph ${ico}" aria-hidden="true"></i><span class="rep-kpi-val">${val}</span><span class="rep-kpi-lbl">${lbl}</span></div>`;
    const multiSede = (d.por_sede || []).length > 1;
    return `
        <div class="rep-cabecera">
            <h3>${escapeHtml(_repTituloPeriodo(per))}</h3>
            <span>${escapeHtml(per.sede)} · ${per.dias} día${per.dias !== 1 ? 's' : ''}</span>
        </div>
        <div class="rep-kpis">
            ${kpi('Sesiones', _num(r.sesiones), 'ph-sign-in')}
            ${kpi('Usuarios distintos', _num(r.usuarios), 'ph-users')}
            ${kpi('Horas de uso', _num(r.horas_uso), 'ph-clock')}
            ${kpi('Minutos por sesión', _num(r.promedio_min), 'ph-timer')}
            ${kpi('Hora pico', r.hora_pico || '—', 'ph-chart-line-up')}
            ${kpi('Incidencias', _num(inc.total), 'ph-warning-circle', inc.graves ? ' rep-kpi-alerta' : '')}
        </div>
        <div class="rep-grid">
            <div class="rep-card"><h4>${d.serie_tipo === 'mes' ? 'Sesiones por mes' : 'Sesiones por día'}</h4>${_repColumnas(d.serie)}</div>
            <div class="rep-card"><h4>Uso por facultad</h4>${_repBarras(d.por_facultad)}</div>
            <div class="rep-card"><h4>Motivo de uso</h4>${_repBarras(d.por_motivo)}</div>
            <div class="rep-card"><h4>Día de la semana</h4>${_repBarras(d.por_dia_semana, { max: 7 })}</div>
            ${multiSede ? `<div class="rep-card"><h4>Uso por sede</h4>${_repBarras(d.por_sede)}</div>` : ''}
            <div class="rep-card"><h4>PCs más usadas</h4>${_repBarras(d.por_pc)}</div>
            <div class="rep-card"><h4>Incidencias</h4>
                <p class="rep-inc-res"><b>${_num(inc.leves)}</b> leves · <b>${_num(inc.graves)}</b> graves · <b>${_num(inc.alumnos)}</b> alumno(s)</p>
                ${_repBarras(inc.motivos, { campo: 'total' })}
            </div>
        </div>`;
}

function _repColumnas(serie) {
    if (!serie || !serie.length) return '<p class="rep-vacio">Sin datos</p>';
    const tope = Math.max(...serie.map(s => s.sesiones), 1);
    if (!serie.some(s => s.sesiones)) return '<p class="rep-vacio">Sin sesiones en este periodo</p>';
    return `<div class="rep-cols" role="img" aria-label="Sesiones por ${serie.length > 31 ? 'mes' : 'día'}">${serie.map(s =>
        `<span class="rep-col" title="${escapeHtml(s.etiqueta)}: ${s.sesiones} sesiones" style="height:${Math.max(s.sesiones ? 3 : 0, Math.round(s.sesiones * 100 / tope))}%"></span>`
    ).join('')}</div>
    <div class="rep-cols-eje"><span>${escapeHtml(serie[0].etiqueta)}</span><span>${escapeHtml(serie[serie.length - 1].etiqueta)}</span></div>`;
}

// ── Impresion ────────────────────────────────────────────────────────
// Abre la ventana ANTES del fetch (sincrono con el click) para que el
// navegador no la bloquee como popup, y la llena cuando llegan los datos.
async function repImprimir(que) {
    const errEl = document.getElementById('rep-error');
    errEl.textContent = '';
    let params;
    try { params = _repParams(); } catch (e) { errEl.textContent = e.message; return; }
    const win = window.open('', '_blank');
    if (!win) { errEl.textContent = 'El navegador bloqueó la ventana de impresión. Permite ventanas emergentes para este sitio.'; return; }
    win.document.write('<!doctype html><meta charset="utf-8"><title>Preparando reporte…</title><p style="font:14px sans-serif;padding:24px">Preparando reporte…</p>');
    try {
        const url = que === 'incidencias' ? '/admin/reportes/incidencias' : '/admin/reportes/estadisticas';
        const d = await _orgFetch(`${url}?${params}`);
        const html = que === 'incidencias' ? _repDocIncidencias(d) : _repDocEstadisticas(d);
        win.document.open();
        win.document.write(html);
        win.document.close();
        // Dar un instante para que el navegador maquete antes del dialogo.
        setTimeout(() => { try { win.focus(); win.print(); } catch (e) { /* el usuario puede imprimir a mano */ } }, 350);
    } catch (e) {
        win.close();
        errEl.textContent = e.message || 'No se pudo generar el reporte';
    }
}

function _repInstitucion() {
    let logo = null, nombre = 'Universidad Nacional Santiago Antúnez de Mayolo';
    try { logo = localStorage.getItem('cfg_app_logo'); } catch (e) { /* sin storage */ }
    return { logo, nombre };
}

// CSS de impresion: tonos neutros, siempre claro (el papel es blanco).
const _REP_CSS_IMPRESION = `
    *{box-sizing:border-box}
    body{font-family:"Segoe UI",Arial,sans-serif;color:#18181b;margin:0;padding:28px 32px;font-size:12px}
    header{display:flex;align-items:center;gap:14px;border-bottom:2px solid #4f46e5;padding-bottom:10px;margin-bottom:16px}
    header img{width:48px;height:48px;object-fit:contain}
    header .inst{font-size:11px;color:#52525b;text-transform:uppercase;letter-spacing:.04em}
    header h1{font-size:18px;margin:2px 0 0}
    .meta{display:flex;flex-wrap:wrap;gap:6px 22px;margin-bottom:16px;font-size:11.5px;color:#3f3f46}
    .meta b{color:#18181b}
    .kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:18px}
    .kpi{border:1px solid #d4d4d8;border-radius:8px;padding:8px 10px}
    .kpi .v{font-size:18px;font-weight:700;display:block}
    .kpi .l{font-size:10.5px;color:#52525b;text-transform:uppercase;letter-spacing:.03em}
    h2{font-size:13px;margin:18px 0 6px;padding-bottom:3px;border-bottom:1px solid #e4e4e7}
    table{width:100%;border-collapse:collapse;margin-bottom:6px}
    th,td{text-align:left;padding:4px 6px;border-bottom:1px solid #e4e4e7;vertical-align:top}
    th{background:#f4f4f5;font-size:10.5px;text-transform:uppercase;letter-spacing:.03em;color:#3f3f46}
    td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
    .dos{display:grid;grid-template-columns:1fr 1fr;gap:0 22px}
    .bar{display:inline-block;height:7px;background:#6366f1;border-radius:2px;vertical-align:middle}
    .grave{color:#b91c1c;font-weight:700}
    .vacio{color:#71717a;font-style:italic;padding:6px 0}
    footer{margin-top:22px;padding-top:8px;border-top:1px solid #e4e4e7;font-size:10px;color:#71717a;display:flex;justify-content:space-between}
    .firma{margin-top:46px;display:flex;justify-content:flex-end}
    .firma div{border-top:1px solid #18181b;width:220px;text-align:center;padding-top:4px;font-size:11px}
    .aviso{background:#fef3c7;border:1px solid #f59e0b;padding:6px 10px;border-radius:6px;margin-bottom:10px}
    tr{page-break-inside:avoid}
    h2{page-break-after:avoid}
    @page{margin:14mm}
    @media print{body{padding:0}}
`;

function _repEncabezado(titulo, per, d) {
    const inst = _repInstitucion();
    return `
    <header>
        ${inst.logo ? `<img src="${escapeHtml(inst.logo)}" alt="">` : ''}
        <div><div class="inst">${escapeHtml(inst.nombre)} · Control de acceso a PCs</div><h1>${escapeHtml(titulo)}</h1></div>
    </header>
    <div class="meta">
        <span>Periodo: <b>${escapeHtml(_repTituloPeriodo(per))}</b></span>
        <span>Sede: <b>${escapeHtml(per.sede)}</b></span>
        <span>Generado: <b>${escapeHtml(d.generado)}</b> por <b>${escapeHtml(d.generado_por)}</b></span>
    </div>`;
}

function _repTabla(items, columnas, { vacio = 'Sin datos en este periodo', conBarra = null } = {}) {
    if (!items || !items.length) return `<p class="vacio">${escapeHtml(vacio)}</p>`;
    const tope = conBarra ? Math.max(...items.map(i => i[conBarra] || 0), 1) : 1;
    return `<table><thead><tr>${columnas.map(c => `<th class="${c.n ? 'n' : ''}">${escapeHtml(c.t)}</th>`).join('')}${conBarra ? '<th style="width:28%"></th>' : ''}</tr></thead><tbody>${
        items.map(i => `<tr>${columnas.map(c => `<td class="${c.n ? 'n' : ''}">${c.n ? _num(i[c.k]) : escapeHtml(i[c.k] ?? '')}</td>`).join('')}${
            conBarra ? `<td><span class="bar" style="width:${Math.max(1, Math.round((i[conBarra] || 0) * 100 / tope))}%"></span></td>` : ''}</tr>`).join('')
    }</tbody></table>`;
}

function _repDocEstadisticas(d) {
    const r = d.resumen, inc = d.incidencias, per = d.periodo;
    const kpi = (l, v) => `<div class="kpi"><span class="v">${v}</span><span class="l">${escapeHtml(l)}</span></div>`;
    const totalSes = r.sesiones || 0;
    const fac = (d.por_facultad || []).map(f => ({ ...f, pct: totalSes ? `${(f.sesiones * 100 / totalSes).toFixed(1)}%` : '0%' }));
    return `<!doctype html><html lang="es"><head><meta charset="utf-8">
    <title>Estadísticas — ${escapeHtml(_repTituloPeriodo(per))}</title><style>${_REP_CSS_IMPRESION}</style></head><body>
    ${_repEncabezado('Reporte estadístico de uso de PCs', per, d)}
    <div class="kpis">
        ${kpi('Sesiones', _num(r.sesiones))}
        ${kpi('Usuarios distintos', _num(r.usuarios))}
        ${kpi('Horas de uso', _num(r.horas_uso))}
        ${kpi('Minutos por sesión', _num(r.promedio_min))}
        ${kpi('Días con actividad', _num(r.dias_con_actividad))}
        ${kpi('Sesiones por día', _num(r.promedio_sesiones_dia))}
        ${kpi('Hora pico', escapeHtml(r.hora_pico || '—'))}
        ${kpi('Incidencias', `${_num(inc.total)}`)}
    </div>
    ${r.alumnos_nuevos !== null && r.alumnos_nuevos !== undefined ? `<div class="meta"><span>Alumnos nuevos en el padrón: <b>${_num(r.alumnos_nuevos)}</b></span><span>Suspensiones (bans) aplicadas: <b>${_num(r.bans)}</b></span></div>` : ''}

    <h2>${d.serie_tipo === 'mes' ? 'Uso por mes' : 'Uso por día'}</h2>
    ${_repTabla(d.serie.filter(s => d.serie_tipo === 'mes' || s.sesiones), [
        { t: d.serie_tipo === 'mes' ? 'Mes' : 'Día', k: 'etiqueta' }, { t: 'Sesiones', k: 'sesiones', n: 1 }, { t: 'Usuarios', k: 'usuarios', n: 1 }],
        { conBarra: 'sesiones', vacio: 'Sin sesiones en este periodo' })}

    <h2>Uso por facultad</h2>
    ${_repTabla(fac, [{ t: 'Facultad', k: 'nombre' }, { t: 'Sesiones', k: 'sesiones', n: 1 }, { t: 'Usuarios', k: 'usuarios', n: 1 }, { t: '%', k: 'pct' }], { conBarra: 'sesiones' })}

    ${(d.por_sede || []).length > 1 ? `<h2>Uso por sede</h2>${_repTabla(d.por_sede.map(s => ({ ...s, horas: (s.minutos / 60).toFixed(1) })), [
        { t: 'Sede', k: 'nombre' }, { t: 'Sesiones', k: 'sesiones', n: 1 }, { t: 'Usuarios', k: 'usuarios', n: 1 }, { t: 'Horas', k: 'horas' }], { conBarra: 'sesiones' })}` : ''}

    <div class="dos">
        <div><h2>Escuelas con más uso</h2>${_repTabla(d.por_escuela, [{ t: 'Escuela', k: 'nombre' }, { t: 'Sesiones', k: 'sesiones', n: 1 }])}</div>
        <div><h2>Motivo de uso</h2>${_repTabla(d.por_motivo, [{ t: 'Motivo', k: 'nombre' }, { t: 'Sesiones', k: 'sesiones', n: 1 }])}</div>
        <div><h2>Día de la semana</h2>${_repTabla(d.por_dia_semana, [{ t: 'Día', k: 'nombre' }, { t: 'Sesiones', k: 'sesiones', n: 1 }], { conBarra: 'sesiones' })}</div>
        <div><h2>Franja horaria</h2>${_repTabla(d.por_hora.filter(h => h.sesiones).map(h => ({ ...h, franja: `${String(h.hora).padStart(2, '0')}:00 - ${String(h.hora + 1).padStart(2, '0')}:00` })),
            [{ t: 'Hora', k: 'franja' }, { t: 'Sesiones', k: 'sesiones', n: 1 }], { conBarra: 'sesiones' })}</div>
        <div><h2>PCs más usadas</h2>${_repTabla(d.por_pc.map(p => ({ ...p, horas: (p.minutos / 60).toFixed(1) })), [{ t: 'PC', k: 'nombre' }, { t: 'Sesiones', k: 'sesiones', n: 1 }, { t: 'Horas', k: 'horas' }])}</div>
        <div><h2>Incidencias</h2>
            <p>${_num(inc.leves)} leves · <span class="grave">${_num(inc.graves)} graves</span> · ${_num(inc.alumnos)} alumno(s) involucrado(s)</p>
            ${_repTabla(inc.motivos, [{ t: 'Motivo', k: 'nombre' }, { t: 'Total', k: 'total', n: 1 }], { vacio: 'Sin incidencias en este periodo' })}
        </div>
    </div>
    <div class="firma"><div>Responsable del centro de cómputo</div></div>
    <footer><span>Sesiones canceladas por falta de confirmación del kiosco no se cuentan. Cada sesión suma como máximo 12 h.</span><span>${escapeHtml(d.generado)}</span></footer>
    </body></html>`;
}

function _repDocIncidencias(d) {
    const per = d.periodo;
    const multiSede = new Set(d.items.map(i => i.sede)).size > 1;
    const filas = d.items.map((i, n) => {
        const f = i.fecha ? new Date(i.fecha) : null;
        const fecha = f ? `${f.toLocaleDateString('es-PE', { day: '2-digit', month: '2-digit', year: 'numeric' })} ${f.toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit' })}` : '—';
        return `<tr>
            <td class="n">${n + 1}</td><td>${escapeHtml(fecha)}</td><td>${escapeHtml(i.dni)}</td><td>${escapeHtml(i.nombre_alumno)}</td>
            <td class="${i.tipo === 'grave' ? 'grave' : ''}">${i.tipo === 'grave' ? 'Grave' : 'Leve'}</td>
            <td>${escapeHtml(i.motivo)}${i.descripcion ? `<br><span style="color:#52525b">${escapeHtml(i.descripcion)}</span>` : ''}</td>
            ${multiSede ? `<td>${escapeHtml(i.sede)}</td>` : ''}
            <td>${escapeHtml(i.registrado_por)}</td><td>${i.activa ? 'Activa' : 'Reseteada'}</td>
        </tr>`;
    }).join('');
    return `<!doctype html><html lang="es"><head><meta charset="utf-8">
    <title>Incidencias — ${escapeHtml(_repTituloPeriodo(per))}</title><style>${_REP_CSS_IMPRESION}</style></head><body>
    ${_repEncabezado('Reporte de incidencias', per, d)}
    <div class="kpis">
        <div class="kpi"><span class="v">${_num(d.total)}</span><span class="l">Incidencias</span></div>
        <div class="kpi"><span class="v">${_num(d.leves)}</span><span class="l">Leves</span></div>
        <div class="kpi"><span class="v grave">${_num(d.graves)}</span><span class="l">Graves</span></div>
        <div class="kpi"><span class="v">${_num(new Set(d.items.map(i => i.dni)).size)}</span><span class="l">Alumnos</span></div>
    </div>
    ${d.truncado ? `<p class="aviso">Se muestran las primeras ${_num(d.items.length)} de ${_num(d.total)} incidencias. Use un periodo más corto para verlas todas.</p>` : ''}
    ${d.items.length ? `<table><thead><tr><th class="n">#</th><th>Fecha</th><th>DNI</th><th>Alumno</th><th>Tipo</th><th>Motivo / detalle</th>${multiSede ? '<th>Sede</th>' : ''}<th>Registró</th><th>Estado</th></tr></thead><tbody>${filas}</tbody></table>`
        : '<p class="vacio">No se registraron incidencias en este periodo.</p>'}
    <div class="firma"><div>Responsable del centro de cómputo</div></div>
    <footer><span>Incluye incidencias activas y reseteadas del periodo.</span><span>${escapeHtml(d.generado)}</span></footer>
    </body></html>`;
}


// ══════════════════════════════════════════════════════════════════════
//  CICLOS ACADEMICOS (Configuracion, solo superadmin)
// ══════════════════════════════════════════════════════════════════════

let _cicloEditandoId = null;

async function ciclosCargar() {
    const cont = document.getElementById('ciclos-lista');
    if (!cont) return;
    try {
        const d = await _orgFetch('/admin/ciclos');
        _orgCiclos = d.items || [];
        if (!_orgCiclos.length) {
            cont.innerHTML = '<p class="empty-msg">Aún no hay ciclos. Registra el ciclo actual (por ejemplo 2026-II) con sus fechas.</p>';
            return;
        }
        cont.innerHTML = _orgCiclos.map(c => `
            <div class="org-item${c.actual ? ' org-item-actual' : ''}">
                <div class="org-ico"><i class="ph ph-calendar-dots" aria-hidden="true"></i></div>
                <div class="org-info">
                    <span class="org-nombre">${escapeHtml(c.nombre)}</span>
                    <span class="org-sub">${_fmtFecha(c.fecha_inicio)} al ${_fmtFecha(c.fecha_fin)} · ${c.dias} días</span>
                </div>
                <div class="org-badges">
                    ${c.actual ? '<span class="enc-tag enc-tag-yo">En curso</span>' : ''}
                    <span class="enc-acciones">
                        <button class="enc-btn" title="Editar" aria-label="Editar ciclo ${escapeHtml(c.nombre)}" onclick="cicloAbrirModal(${c.id})"><i class="ph ph-pencil-simple"></i></button>
                        <button class="enc-btn enc-btn-danger" title="Eliminar" aria-label="Eliminar ciclo ${escapeHtml(c.nombre)}" onclick="cicloEliminar(${c.id})"><i class="ph ph-trash"></i></button>
                    </span>
                </div>
            </div>`).join('');
    } catch (e) {
        cont.innerHTML = `<p class="empty-msg">${escapeHtml(e.message)}</p>`;
    }
}

function cicloAbrirModal(id = null) {
    _cicloEditandoId = id;
    const c = id ? _orgCiclos.find(x => x.id === id) : null;
    document.getElementById('modal-ciclo-titulo').textContent = c ? `Editar ciclo ${c.nombre}` : 'Nuevo ciclo';
    document.getElementById('ciclo-nombre').value = c ? c.nombre : '';
    document.getElementById('ciclo-inicio').value = c ? c.fecha_inicio : '';
    document.getElementById('ciclo-fin').value = c ? c.fecha_fin : '';
    document.getElementById('ciclo-error').textContent = '';
    document.getElementById('modal-ciclo').style.display = 'flex';
    document.getElementById('ciclo-nombre').focus();
}

function cicloCerrarModal() {
    document.getElementById('modal-ciclo').style.display = 'none';
    _cicloEditandoId = null;
}

async function cicloGuardar() {
    const err = document.getElementById('ciclo-error');
    const btn = document.getElementById('btn-ciclo-guardar');
    const body = {
        nombre: document.getElementById('ciclo-nombre').value.trim(),
        fecha_inicio: document.getElementById('ciclo-inicio').value,
        fecha_fin: document.getElementById('ciclo-fin').value,
    };
    err.textContent = '';
    if (!body.nombre) { err.textContent = 'Escribe el nombre del ciclo (ej: 2026-II)'; return; }
    if (!body.fecha_inicio || !body.fecha_fin) { err.textContent = 'Completa las fechas de inicio y fin'; return; }
    if (body.fecha_fin < body.fecha_inicio) { err.textContent = 'La fecha de fin no puede ser anterior a la de inicio'; return; }
    btn.disabled = true;
    try {
        const d = await _orgFetch(_cicloEditandoId ? `/admin/ciclos/${_cicloEditandoId}` : '/admin/ciclos', {
            method: _cicloEditandoId ? 'PUT' : 'POST', body: JSON.stringify(body),
        });
        mostrarNotificacion(d.mensaje, 'ok');
        cicloCerrarModal();
        ciclosCargar();
    } catch (e) {
        err.textContent = e.message;
    } finally {
        btn.disabled = false;
    }
}

function cicloEliminar(id) {
    const c = _orgCiclos.find(x => x.id === id);
    if (!c) return;
    mostrarConfirmacion(
        `¿Eliminar el ciclo <strong>${escapeHtml(c.nombre)}</strong>? No se borra ningún dato de uso; solo deja de aparecer en los reportes por ciclo.`,
        async () => {
            try {
                const d = await _orgFetch(`/admin/ciclos/${id}`, { method: 'DELETE' });
                mostrarNotificacion(d.mensaje, 'ok');
                ciclosCargar();
            } catch (e) {
                mostrarNotificacion(e.message, 'error');
            }
        },
        { titulo: 'Eliminar ciclo', textoConfirmar: 'Eliminar', critico: true }
    );
}


// ══════════════════════════════════════════════════════════════════════
//  SEDES (Configuracion, solo superadmin)
// ══════════════════════════════════════════════════════════════════════

let _sedeEditandoId = null;
let _sedesTodas = [];       // incluye inactivas (solo en Configuracion)
let _sedesPCs = [];

async function sedesCargar() {
    const cont = document.getElementById('sedes-lista');
    if (!cont) return;
    try {
        const d = await _orgFetch('/admin/sedes?incluir_inactivas=true');
        _sedesTodas = d.items || [];
        _orgSedes = _sedesTodas.filter(s => s.activo);
        _orgSedesCargadas = true;
        cont.innerHTML = _sedesTodas.map(s => `
            <div class="org-item${s.activo ? '' : ' org-item-off'}">
                <div class="org-ico"><i class="ph ph-buildings" aria-hidden="true"></i></div>
                <div class="org-info">
                    <span class="org-nombre">${escapeHtml(s.nombre)}</span>
                    <span class="org-sub">${s.terminales} PC(s)${s.redes ? ` · red ${escapeHtml(s.redes.replace(/,/g, ', '))}` : ''}${s.descripcion ? ` · ${escapeHtml(s.descripcion)}` : ''}</span>
                </div>
                <div class="org-badges">
                    ${s.activo ? '' : '<span class="enc-tag enc-tag-admin" style="opacity:.7">De baja</span>'}
                    <span class="enc-acciones">${s.activo
                        ? `<button class="enc-btn" title="Editar" aria-label="Editar sede ${escapeHtml(s.nombre)}" onclick="sedeAbrirModal(${s.id})"><i class="ph ph-pencil-simple"></i></button>
                           <button class="enc-btn enc-btn-danger" title="Dar de baja" aria-label="Dar de baja la sede ${escapeHtml(s.nombre)}" onclick="sedeDesactivar(${s.id})"><i class="ph ph-minus-circle"></i></button>`
                        : `<button class="enc-btn" title="Reactivar" aria-label="Reactivar sede ${escapeHtml(s.nombre)}" onclick="sedeReactivar(${s.id})"><i class="ph ph-arrow-counter-clockwise"></i></button>`}
                    </span>
                </div>
            </div>`).join('') || '<p class="empty-msg">No hay sedes.</p>';
        await sedesCargarPCs(d.sin_sede);
    } catch (e) {
        cont.innerHTML = `<p class="empty-msg">${escapeHtml(e.message)}</p>`;
    }
}

async function sedesCargarPCs(sinSede = 0) {
    const filtro = document.getElementById('sede-pc-filtro');
    const destino = document.getElementById('sede-pc-destino');
    const previoFiltro = filtro.value;
    _orgOpcionesSede(filtro, { incluirTodas: 'Todas las PCs', incluirNinguna: `Sin sede (${sinSede})` });
    filtro.value = [...filtro.options].some(o => o.value === previoFiltro) ? previoFiltro : '';
    _orgOpcionesSede(destino, { incluirNinguna: 'Quitar sede (sin asignar)' });
    destino.value = _orgSedes.length ? String(_orgSedes[0].id) : '__none';
    try {
        _sedesPCs = await _orgFetch('/admin/sedes-terminales');
    } catch (e) {
        _sedesPCs = [];
        document.getElementById('sedes-pcs').innerHTML = `<p class="empty-msg">${escapeHtml(e.message)}</p>`;
        return;
    }
    sedesRenderPCs();
}

function sedesRenderPCs() {
    const cont = document.getElementById('sedes-pcs');
    const f = document.getElementById('sede-pc-filtro').value;
    const lista = _sedesPCs.filter(p => !f || (f === '__none' ? !p.id_sede : String(p.id_sede) === f));
    if (!lista.length) { cont.innerHTML = '<p class="empty-msg">No hay PCs en este filtro.</p>'; return; }
    cont.innerHTML = `
        <label class="org-pc org-pc-todas"><input type="checkbox" onchange="document.querySelectorAll('.org-pc-chk').forEach(c => c.checked = this.checked)"> Marcar todas (${lista.length})</label>
        ${lista.map(p => `
        <label class="org-pc">
            <input type="checkbox" class="org-pc-chk" value="${p.id}">
            <span class="org-pc-nom">${escapeHtml(p.nombre)}</span>
            <span class="org-pc-ip">${escapeHtml(p.ip)}</span>
            <span class="org-pc-sede">${escapeHtml(p.id_sede ? _orgNombreSede(p.id_sede) : 'Sin sede')}</span>
        </label>`).join('')}`;
}

async function sedesAsignarPCs() {
    const ids = [...document.querySelectorAll('.org-pc-chk:checked')].map(c => parseInt(c.value, 10));
    if (!ids.length) { mostrarNotificacion('Marca al menos una PC', 'warning'); return; }
    const v = document.getElementById('sede-pc-destino').value;
    const btn = document.getElementById('sede-pc-btn');
    btn.disabled = true;
    try {
        const d = await _orgFetch('/admin/sedes-terminales', {
            method: 'PUT', body: JSON.stringify({ terminales: ids, id_sede: v === '__none' ? null : parseInt(v, 10) }),
        });
        mostrarNotificacion(d.mensaje, 'ok');
        sedesCargar();
    } catch (e) {
        mostrarNotificacion(e.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

function sedeAbrirModal(id = null) {
    _sedeEditandoId = id;
    const s = id ? _sedesTodas.find(x => x.id === id) : null;
    document.getElementById('modal-sede-titulo').textContent = s ? 'Editar sede' : 'Nueva sede';
    document.getElementById('sede-nombre').value = s ? s.nombre : '';
    document.getElementById('sede-desc').value = s ? s.descripcion : '';
    document.getElementById('sede-redes').value = s ? s.redes.replace(/,/g, ', ') : '';
    document.getElementById('sede-error').textContent = '';
    document.getElementById('modal-sede').style.display = 'flex';
    document.getElementById('sede-nombre').focus();
}

function sedeCerrarModal() {
    document.getElementById('modal-sede').style.display = 'none';
    _sedeEditandoId = null;
}

async function sedeGuardar() {
    const err = document.getElementById('sede-error');
    const btn = document.getElementById('btn-sede-guardar');
    const body = {
        nombre: document.getElementById('sede-nombre').value.trim(),
        descripcion: document.getElementById('sede-desc').value.trim(),
        redes: document.getElementById('sede-redes').value.trim(),
    };
    err.textContent = '';
    if (body.nombre.length < 3) { err.textContent = 'El nombre debe tener al menos 3 caracteres'; return; }
    btn.disabled = true;
    try {
        const d = await _orgFetch(_sedeEditandoId ? `/admin/sedes/${_sedeEditandoId}` : '/admin/sedes', {
            method: _sedeEditandoId ? 'PUT' : 'POST', body: JSON.stringify(body),
        });
        mostrarNotificacion(d.mensaje, 'ok');
        sedeCerrarModal();
        sedesCargar();
    } catch (e) {
        err.textContent = e.message;
    } finally {
        btn.disabled = false;
    }
}

function sedeDesactivar(id) {
    const s = _sedesTodas.find(x => x.id === id);
    if (!s) return;
    mostrarConfirmacion(
        `¿Dar de baja la sede <strong>${escapeHtml(s.nombre)}</strong>? Sus PCs y su historial se conservan; podrás reactivarla después.`,
        async () => {
            try {
                const d = await _orgFetch(`/admin/sedes/${id}`, { method: 'DELETE' });
                mostrarNotificacion(d.mensaje, 'ok');
                sedesCargar();
            } catch (e) {
                mostrarNotificacion(e.message, 'error');
            }
        },
        { titulo: 'Dar de baja sede', textoConfirmar: 'Dar de baja', critico: true }
    );
}

async function sedeReactivar(id) {
    try {
        const d = await _orgFetch(`/admin/sedes/${id}/reactivar`, { method: 'POST' });
        mostrarNotificacion(d.mensaje, 'ok');
        sedesCargar();
    } catch (e) {
        mostrarNotificacion(e.message, 'error');
    }
}


// ══════════════════════════════════════════════════════════════════════
//  MONITOREO POR SEDE (tarjetas por facultad / centro de computo)
// ══════════════════════════════════════════════════════════════════════

let _monSedeSel = (() => { try { return sessionStorage.getItem('mon_sede') || ''; } catch (e) { return ''; } })();
let _monSedesData = [];
let _monSedesPidiendo = false;

// '' = todas; 'none' = sin sede; '<id>' = una sede
function monSedesFiltrar(terminales) {
    if (!_monSedeSel) return terminales;
    if (_monSedeSel === 'none') return terminales.filter(t => !t.id_sede);
    return terminales.filter(t => String(t.id_sede) === _monSedeSel);
}

async function monSedesActualizar() {
    if (_monSedesPidiendo) return;
    _monSedesPidiendo = true;
    try {
        const d = await _orgFetch('/admin/sedes-resumen');
        _monSedesData = d.items || [];
        // Encargado con sede fija: el servidor ya limita sus PCs; no hay que elegir.
        if (d.mi_sede) { _monSedesData = []; _monSedeSel = ''; }
        _monSedesRender();
    } catch (e) {
        /* sin resumen: el mapa sigue funcionando sin la fila de sedes */
    } finally {
        _monSedesPidiendo = false;
    }
}

function _monSedesRender() {
    const sec = document.getElementById('monSedes');
    const grid = document.getElementById('monSedesGrid');
    if (!sec || !grid) return;
    if (!_monSedesData.length) { sec.style.display = 'none'; return; }
    sec.style.display = '';
    // Si la sede elegida ya no existe, volver a "Todas".
    const claves = _monSedesData.map(s => s.id === null ? 'none' : String(s.id));
    if (_monSedeSel && !claves.includes(_monSedeSel)) { monSedeElegir(''); return; }

    const tot = _monSedesData.reduce((a, s) => ({
        total: a.total + s.total, en_uso: a.en_uso + s.en_uso, libres: a.libres + s.libres, apagadas: a.apagadas + s.apagadas,
    }), { total: 0, en_uso: 0, libres: 0, apagadas: 0 });
    const badge = document.getElementById('monSedesBadge');
    if (badge) badge.textContent = `${_monSedesData.length} sede${_monSedesData.length !== 1 ? 's' : ''} · ${tot.en_uso + tot.libres} de ${tot.total} PCs encendidas`;

    const tarjeta = (clave, nombre, s, ico) => {
        const encendidas = s.en_uso + s.libres;
        // Estado general: sin PCs / todas apagadas / parte apagada / todo encendido
        let est = 'ok', txt = 'Todo en orden';
        if (!s.total) { est = 'nada'; txt = 'Sin PCs registradas'; }
        else if (!encendidas) { est = 'off'; txt = 'Todas apagadas'; }
        else if (s.apagadas) { est = 'parcial'; txt = `${s.apagadas} apagada${s.apagadas !== 1 ? 's' : ''}`; }
        const sel = _monSedeSel === clave;
        const pctUso = s.total ? (s.en_uso * 100 / s.total) : 0;
        const pctLib = s.total ? (s.libres * 100 / s.total) : 0;
        return `
        <button type="button" class="mon-sede mon-sede-${est}${sel ? ' mon-sede-sel' : ''}" aria-pressed="${sel}"
                onclick="monSedeElegir('${escJs(clave)}')" title="Ver las PCs de ${escapeHtml(nombre)}">
            <span class="mon-sede-top">
                <i class="ph ${ico}" aria-hidden="true"></i>
                <span class="mon-sede-nom">${escapeHtml(nombre)}</span>
                <span class="mon-sede-dot" aria-hidden="true"></span>
            </span>
            <span class="mon-sede-nums">
                <span><b>${s.en_uso}</b> en uso</span>
                <span><b>${s.libres}</b> libres</span>
                <span><b>${s.apagadas}</b> apagadas</span>
            </span>
            <span class="mon-sede-barra" aria-hidden="true"><span class="mon-sede-uso" style="width:${pctUso}%"></span><span class="mon-sede-lib" style="width:${pctLib}%"></span></span>
            <span class="mon-sede-est">${escapeHtml(txt)} · ${encendidas}/${s.total} encendidas</span>
        </button>`;
    };
    grid.innerHTML = (_monSedesData.length > 1 ? tarjeta('', 'Todas las sedes', tot, 'ph-squares-four') : '')
        + _monSedesData.map(s => tarjeta(s.id === null ? 'none' : String(s.id), s.nombre, s, s.id === null ? 'ph-question' : 'ph-buildings')).join('');
}

function monSedeElegir(clave) {
    _monSedeSel = (_monSedeSel === clave) ? '' : clave;   // segundo clic = ver todas
    try { sessionStorage.setItem('mon_sede', _monSedeSel); } catch (e) { /* sin storage */ }
    _monSedesRender();
    if (_monTerminalesUlt && typeof renderTerminales === 'function') {
        renderTerminales(monSedesFiltrar(_monTerminalesUlt.terminales), _monTerminalesUlt.sesiones);
    }
    const ttl = document.getElementById('monMapaTitulo');
    const sede = _monSedesData.find(s => (s.id === null ? 'none' : String(s.id)) === _monSedeSel);
    if (ttl) ttl.textContent = sede ? `Mapa de Terminales — ${sede.nombre}` : 'Mapa de Terminales';
}
