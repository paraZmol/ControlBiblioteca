// Detectar si se abre como archivo local o desde el servidor

const _isFile = window.location.protocol === 'file:';

const API_BASE  = _isFile ? 'http://localhost:8000/api'   : window.location.origin + '/api';

const WS_BASE   = _isFile ? 'ws://localhost:8000'         : `ws://${window.location.host}`;



let token   = null;

let wsAdmin = null;

let _sesionExpirada = false;  // evita disparar el aviso de expiración varias veces

let _reconnectTimer = null;

let _serverIp = 'localhost';

let _reconnectAttempts = 0;

const _MAX_RECONNECT_ATTEMPTS = 10;

let _reconnectDelay = 2000;



let _pendingUnlockModal = null; // referencia al modal de desbloqueo abierto



// ── Sistema de Roles ──────────────────────────────────────────────
// 'admin' = funciones básicas | 'superadmin' = acceso total
// El rol viene del JWT tras login

let _rolServidor = 'admin'; // 'superadmin' | 'admin' — viene del JWT tras login



function _aplicarRol() {

    const esSuperAdmin = _rolServidor === 'superadmin';

    // Estadísticas, equipos y consola: visibles para todos
    const stats = document.getElementById('seccionStats');
    if (stats) stats.style.display = '';   // dejar que el CSS gobierne el layout (flex horizontal); 'grid' era resto de un diseno viejo y forzaba los KPIs en vertical

    const equipos = document.getElementById('seccionEquipos');
    if (equipos) equipos.style.display = '';

    const footerMonitoreo = document.getElementById('footer-monitoreo');
    if (footerMonitoreo) footerMonitoreo.style.display = '';

    // Botones de control global y acciones: visibles para todos
    const btnFinalizar = document.querySelector('.btn-finalizar');
    const btnLimpiar   = document.querySelector('.btn-limpiar');
    if (btnFinalizar) btnFinalizar.style.display = '';
    if (btnLimpiar)   btnLimpiar.style.display   = '';

    const btnImportar = document.getElementById('btnImportarExcel');
    if (btnImportar) btnImportar.style.display = 'inline-block';

    const btnMaestro = document.getElementById('btnMaestro');
    if (btnMaestro) btnMaestro.style.display = '';

    // Base de datos: visible para todos (admin con funciones limitadas)
    const navBD = document.getElementById('nav-basedatos');
    if (navBD) navBD.style.display = '';

    // BD — botones y secciones solo para superadmin
    ['btnExportarMaestro', 'btnImportarMaestro', 'subtab-btn-personal'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = esSuperAdmin ? '' : 'none';
    });
    document.querySelectorAll('.bd-solo-superadmin').forEach(el => {
        el.style.display = esSuperAdmin ? '' : 'none';
    });

    // Configuración: visible para todos, pero sub-tabs avanzados solo superadmin
    const navCfg = document.getElementById('nav-configuracion');
    if (navCfg) navCfg.style.display = '';

    ['actividad', 'credenciales', 'aplicacion', 'actualizaciones', 'auditoria'].forEach(s => {
        const btn = document.getElementById('subtab-btn-cfg-' + s);
        if (btn) btn.style.display = esSuperAdmin ? '' : 'none';
    });

    // Sección de mantenimiento (atajo Ctrl+Alt+F7 + PIN): solo superadmin
    const secMantenimiento = document.getElementById('seccion-mantenimiento-backdoor');
    if (secMantenimiento) secMantenimiento.style.display = esSuperAdmin ? '' : 'none';

    if (typeof switchTab === 'function') {
        const tabActualBD = document.getElementById('tab-basedatos');
        const tabActualCfg = document.getElementById('tab-configuracion');
        if (tabActualCfg && tabActualCfg.style.display !== 'none' && !esSuperAdmin) {
            if (typeof switchSubtabCfg === 'function') switchSubtabCfg('backdoor');
        }
    }
}






// ── Estado de filtros/orden/periodo ───────────────────────────────

let _sortBy      = 'fecha';

let _sortDir     = 'desc';

let _periodo     = 'anio';      // dia | mes | anio | rango | todo

let _fechaInicio = '';

let _fechaFin    = '';

const _SESIONES_LIMIT = 15;

let _sesionesOffset  = 0;

let _sesionesTotal   = 0;



// ── Tema claro / oscuro ───────────────────────────────────────────

function toggleTema() {

    const html = document.documentElement;
    const isDark = html.classList.toggle('dark');
    localStorage.theme = isDark ? 'dark' : 'light';

    const icon = document.getElementById('iconTema');
    if (icon) {
        icon.classList.toggle('ph-sun', isDark);
        icon.classList.toggle('ph-moon', !isDark);
    }

}

// ── Menú: franja de iconos que se EXPANDE superpuesto al hacer clic ──
// (en escritorio). Tooltips nativos en cada icono cuando está en franja.
function _aplicarTooltipsMenu() {
    document.querySelectorAll('#sidebar .nav-item').forEach(item => {
        const txt = item.querySelector('span')?.textContent?.trim() || '';
        if (txt) item.title = txt;
    });
}

function toggleMenuExpandido() {
    const sb = document.getElementById('sidebar');
    if (!sb) return;
    const abierto = sb.classList.toggle('expanded');
    document.body.classList.toggle('sidebar-open', abierto);
}

function cerrarMenuExpandido() {
    const sb = document.getElementById('sidebar');
    if (!sb) return;
    sb.classList.remove('expanded');
    document.body.classList.remove('sidebar-open');
}



(function _initTema() {

    const html = document.documentElement;
    const icon = document.getElementById('iconTema');
    const isDark = localStorage.theme === 'dark' || (!('theme' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches);
    if (isDark) {
        html.classList.add('dark');
        if (icon) { icon.classList.add('ph-sun'); icon.classList.remove('ph-moon'); }
    } else {
        html.classList.remove('dark');
        if (icon) { icon.classList.add('ph-moon'); icon.classList.remove('ph-sun'); }
    }

})();



// ── CONSOLA DE MONITOREO (disponible globalmente) ──────────────────

const _consolePaused = { activity: false, errors: false };

function addLog(category, message) {

    const time = new Date().toLocaleTimeString('es-PE', { hour12: false });

    const consoleId = category === 'error' ? 'console-errors' : 'console-activity';

    const consoleEl = document.getElementById(consoleId);

    if (!consoleEl) { console.log(`[${category}] ${message}`); return; }

    if (_consolePaused[category === 'error' ? 'errors' : 'activity']) return;

    // Derive semantic CSS class from message content
    let cls = category === 'error' ? 'error' : 'ok';
    if (category === 'error') {
        if (/timeout|desconect|offline/i.test(message))   cls = 'error';
        else if (/advertencia|warning/i.test(message))    cls = 'warning';
        else cls = 'error';
    } else {
        if (/conectado|exitoso|correcto|\bok\b/i.test(message))    cls = 'ok';
        else if (/advertencia|warning/i.test(message))             cls = 'warning';
        else if (/login|sesión verificada|nivel 2/i.test(message)) cls = 'info';
        else if (/heartbeat|status update/i.test(message))         cls = 'muted';
        else if (/conectando|reintentando/i.test(message))         cls = 'warning';
        else cls = 'ok';
    }

    const _logIco = { ok: 'ph-check-circle', error: 'ph-x-circle', warning: 'ph-warning', info: 'ph-info', muted: 'ph-dots-three' };

    const div = document.createElement('div');

    div.className = `console-item ${cls}`;

    div.innerHTML = `<span class="console-time">[${time}]</span><span class="console-ico"><i class="ph ${_logIco[cls] || 'ph-circle'}"></i></span><span class="console-msg">${escapeHtml(message)}</span>`;

    consoleEl.appendChild(div);

    consoleEl.scrollTop = consoleEl.scrollHeight;

    // Limitar a 200 líneas

    while (consoleEl.children.length > 200) consoleEl.removeChild(consoleEl.firstChild);

}

function appendLog(msg, nivel = 'info') { addLog(nivel === 'error' ? 'error' : 'activity', msg); }

// ── Console controls ──────────────────────────────────────────────

function togglePausarConsola(id) {
    _consolePaused[id] = !_consolePaused[id];
    const btn = document.querySelector(`button[onclick="togglePausarConsola('${id}')"] i`);
    if (btn) { btn.className = _consolePaused[id] ? 'ph ph-play' : 'ph ph-pause'; }
}

function limpiarConsola(id) {
    const el = document.getElementById(`console-${id}`);
    if (el) el.innerHTML = '';
}

function descargarLog(id) {
    const el = document.getElementById(`console-${id}`);
    if (!el) return;
    const lines = [...el.querySelectorAll('.console-item')].map(row => {
        const t = row.querySelector('.console-time')?.textContent || '';
        const m = row.querySelector('.console-msg')?.textContent || '';
        return `${t} ${m}`;
    }).join('\n');
    const blob = new Blob([lines], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `log_${id}_${new Date().toISOString().slice(0,19).replace(/:/g,'-')}.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
}

function escapeHtml(text) { const d = document.createElement('div'); d.textContent = String(text ?? ''); return d.innerHTML; }

function esc(s) { return escapeHtml(s); }



// ── Autenticación ──────────────────────────────────────────────────



async function login() {

    const username = document.getElementById('username').value.trim();

    const password = document.getElementById('password').value;

    const errorEl  = document.getElementById('loginError');

    const btn      = document.getElementById('btnLogin');

    errorEl.textContent = '';



    if (!username || !password) { errorEl.textContent = 'Ingrese usuario y contraseña'; return; }



    // Estado de carga

    btn.disabled = true;

    btn.textContent = 'Verificando...';



    try {

        const form = new URLSearchParams({ username, password });

        const res  = await fetch(`${API_BASE}/auth/login`, {

            method:  'POST',

            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },

            body:    form

        });



        if (!res.ok) {

            const err = await res.json().catch(() => ({}));

            errorEl.textContent = err.detail || 'Credenciales incorrectas';

            btn.disabled = false;

            btn.textContent = 'Iniciar Sesión';

            return;

        }



        const data = await res.json();

        token = data.access_token;
        sessionStorage.setItem('panel_token', token);  // persistir para sobrevivir a F5

        btn.disabled = false;
        btn.textContent = 'Iniciar Sesión';

        await _entrarDashboard(username);
        addLog('activity', `Login exitoso como '${username}'`);

    } catch (e) {

        errorEl.textContent = 'No se pudo conectar al servidor (¿está corriendo en :8000?)';

        addLog('error', `Login fallido: ${e.message || 'sin conexión'}`);

        btn.disabled = false;

        btn.textContent = 'Iniciar Sesión';

    }

}

// Evita que los intervalos de refresco se dupliquen al re-entrar al dashboard
let _intervalosDashboard = false;

// Lógica común de entrada al dashboard, compartida por login() y _restaurarSesion()
async function _entrarDashboard(username) {
    _sesionExpirada = false;  // sesión válida: rearmar el aviso de expiración

    // Decodificar rol del servidor desde el JWT (sin verificar firma — solo lectura UI)
    try {
        const payload = JSON.parse(atob(token.split('.')[1]));
        _rolServidor = payload.rol || 'admin';
    } catch(e) { _rolServidor = 'admin'; }

    document.getElementById('usuarioActual').textContent = username;
    const rolEl = document.getElementById('usuarioRol');
    if (rolEl) rolEl.textContent = _rolServidor === 'superadmin' ? 'Super Admin' : 'Admin';

    document.getElementById('loginPanel').style.display = 'none';
    document.getElementById('dashboard').style.display  = 'flex';
    document.body.classList.remove('login-screen');

    _aplicarRol(); // aplica visibilidad según rol del JWT
    _aplicarConfigApp(); // aplica logo y textos personalizados si existen
    _poblarAnios();      // popula selector de año en historial
    cargarSospechas();   // carga badge de sospechas pendientes

    await obtenerYMostrarIpServidor();

    addLog('activity', `API: ${API_BASE}`);
    addLog('activity', `WS: ${WS_BASE}/ws/admin`);

    cargarDashboard();
    conectarWebSocket();
    _cargarResumenIncidencias();
    _cargarEventosMini();

    // Registrar los intervalos una sola vez por carga de página
    if (!_intervalosDashboard) {
        _intervalosDashboard = true;
        setInterval(cargarDashboard, 15000);
        setInterval(_cargarResumenIncidencias, 60000);
        setInterval(_cargarEventosMini, 30000);
    }

    // Restaurar la pestaña donde estaba el usuario antes del F5
    const tabGuardada = sessionStorage.getItem('panel_tab');
    if (tabGuardada && typeof switchTab === 'function') switchTab(tabGuardada);
}

// Al recargar la página (F5): si hay un token guardado, validarlo y entrar
// directo al dashboard sin pedir login de nuevo.
async function _restaurarSesion() {
    const guardado = sessionStorage.getItem('panel_token');
    if (!guardado) { document.documentElement.classList.remove('con-sesion'); return; }
    token = guardado;

    // Validar el token contra el servidor antes de confiar en él
    try {
        const res = await fetch(`${API_BASE}/auth/me`, { headers: authHeaders() });
        if (!res.ok) {
            // Token inválido/expirado: limpiar y mostrar el login
            token = null;
            sessionStorage.removeItem('panel_token');
            document.documentElement.classList.remove('con-sesion');
            return;
        }
        const me = await res.json();
        await _entrarDashboard(me.username || '');
    } catch (e) {
        // Sin conexión u otro error: mostrar el login para que reintente
        token = null;
        sessionStorage.removeItem('panel_token');
        document.documentElement.classList.remove('con-sesion');
    }
}



async function obtenerYMostrarIpServidor() {

    try {

        const res = await fetch(`${API_BASE}/server-info`, { headers: authHeaders(), cache: 'no-store' });

        if (res.ok) {

            const data = await res.json();

            _serverIp = data.ip || 'localhost';

            const badge = document.getElementById('serverIpBadge');

            if (badge) {

                badge.textContent = _serverIp;

            }

            addLog('activity', `IP del servidor: ${_serverIp}`);

        }

    } catch (e) {

        addLog('error', `No se pudo obtener IP del servidor: ${e.message}`);

    }

}



function logout() {

    token = null;
    sessionStorage.removeItem('panel_token');  // limpiar token persistido
    document.documentElement.classList.remove('con-sesion');  // login visible al recargar

    if (wsAdmin) { wsAdmin.onclose = null; wsAdmin.close(); wsAdmin = null; }

    clearTimeout(_reconnectTimer);

    document.getElementById('dashboard').style.display  = 'none';

    document.getElementById('loginPanel').style.display = 'flex';

    document.getElementById('username').value = '';

    document.getElementById('password').value = '';

    document.body.classList.add('login-screen');

    _rolServidor = 'admin';

    setWsStatus(false);

}



// ── Manejo de expiración de sesión (token JWT vencido = HTTP 401) ────
// El token dura 4h. Antes, al expirar, los fetch fallaban en silencio y el
// panel se quedaba vacío sin avisar. Ahora un interceptor global detecta el
// 401 de cualquier llamada a la API y lleva al login con un aviso claro.

function _sesionVencida() {
    if (_sesionExpirada) return;       // ya se está manejando
    _sesionExpirada = true;
    logout();
    const err = document.getElementById('loginError');
    if (err) err.textContent = 'Su sesión expiró. Vuelva a iniciar sesión.';
}

// Interceptor global: envuelve window.fetch para detectar el 401 de la API.
// No toca el login (que también puede devolver 401 por credenciales malas):
// solo dispara el aviso de expiración cuando YA había un token activo.
(function instalarInterceptor401() {
    const _fetchOriginal = window.fetch.bind(window);
    window.fetch = async function (recurso, opciones) {
        const resp = await _fetchOriginal(recurso, opciones);
        try {
            const url = (typeof recurso === 'string') ? recurso : (recurso && recurso.url) || '';
            // /auth/login y /auth/me manejan su propio 401 (login fallido o token
            // expirado al restaurar sesión) — no deben disparar el aviso global.
            const esAuth = url.includes('/auth/login') || url.includes('/auth/me');
            if (resp.status === 401 && token && !esAuth) {
                _sesionVencida();
            }
        } catch (_) { /* nunca romper la petición por el interceptor */ }
        return resp;
    };
})();


// ── REST API ───────────────────────────────────────────────────────



function authHeaders() {

    return { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };

}



function _buildParams() {

    const search    = document.getElementById('filtroSearch')?.value.trim()    || '';
    const actividad = document.getElementById('filtroActividad')?.value.trim() || '';
    const estado    = document.getElementById('filtroEstado')?.value.trim()    || '';
    const desde     = document.getElementById('rangoDesde')?.value             || '';
    const hasta     = document.getElementById('rangoHasta')?.value             || '';

    _fechaInicio = desde;
    _fechaFin    = hasta;

    const mes   = document.getElementById('filtroMes')?.value  || '';
    const anio  = document.getElementById('filtroAnio')?.value || '';

    // Si hay rango de fechas, usar periodo rango; si hay mes/anio, usar periodo especifico; si no, usar el periodo seleccionado
    const periodoEfectivo = (desde && hasta) ? 'rango' : _periodo;

    const params = new URLSearchParams({ sort_by: _sortBy, order: _sortDir, periodo: periodoEfectivo, limit: _SESIONES_LIMIT, offset: _sesionesOffset });

    if (search)    params.set('search',    search);
    if (actividad) params.set('actividad', actividad);
    if (estado)    params.set('estado',    estado);

    if (desde && hasta) {
        params.set('fecha_inicio', desde);
        params.set('fecha_fin',    hasta);
    }

    if (mes)  params.set('mes',  mes);
    if (anio) params.set('anio', anio);

    return params;

}



function _sesionesUrl() {

    return `${API_BASE}/sesiones/activas?${_buildParams()}`;

}



function setPeriodo(p) {

    _periodo = p;

    ['btn-hoy','btn-mes','btn-anio','btn-todo'].forEach(id => {

        const b = document.getElementById(id);

        if (b) b.classList.toggle('hist-chip-on', b.dataset.periodo === p);

    });

    aplicarFiltros();

}

function toggleFiltrosAvanzados() {

    const panel = document.getElementById('histAdvFilters');

    const btn   = document.getElementById('btnFiltrosAvanzados');

    if (!panel) return;

    const open = panel.classList.toggle('open');

    if (btn) btn.classList.toggle('active', open);

}

function limpiarFiltrosAvanzados() {

    const desde  = document.getElementById('rangoDesde');
    const hasta  = document.getElementById('rangoHasta');
    const estado = document.getElementById('filtroEstado');
    const mes    = document.getElementById('filtroMes');
    const anio   = document.getElementById('filtroAnio');

    if (desde)  desde.value  = '';
    if (hasta)  hasta.value  = '';
    if (estado) estado.value = '';
    if (mes)    mes.value    = '';
    if (anio)   anio.value   = '';

    _fechaInicio = '';
    _fechaFin    = '';

    aplicarFiltros();

}



async function aplicarFiltros(resetOffset = true) {

    if (resetOffset) _sesionesOffset = 0;

    try {

        const res = await fetch(_sesionesUrl(), { headers: authHeaders(), cache: 'no-store' });

        if (!res.ok) return;

        const data = await res.json();

        const sesiones = Array.isArray(data) ? data : (data.items || []);

        _sesionesTotal = Array.isArray(data) ? sesiones.length : (data.total || 0);

        renderSesiones(sesiones);

        _renderPaginacionSesiones();

        _actualizarFlechas();

    } catch (e) {

        addLog('error', `Error al filtrar sesiones: ${e.message}`);

    }

}



function _renderPaginacionSesiones() {

    const totalPages = Math.ceil(_sesionesTotal / _SESIONES_LIMIT);

    const curPage    = Math.floor(_sesionesOffset / _SESIONES_LIMIT);

    // Actualizar contador superior
    const contador = document.getElementById('contadorSesiones');
    if (contador) contador.textContent = _sesionesTotal ? `${_sesionesTotal} registros` : '';

    let cont = document.getElementById('paginacionSesiones');

    if (!cont) return;

    if (totalPages <= 1) { cont.innerHTML = _sesionesTotal ? `<span class="pag-info">Mostrando <b>${_sesionesTotal}</b> de <b>${_sesionesTotal}</b> registros</span>` : ''; return; }

    const desde = _sesionesOffset + 1;
    const hasta = Math.min(_sesionesOffset + _SESIONES_LIMIT, _sesionesTotal);
    const info = `<span class="pag-info">Mostrando <b>${desde}–${hasta}</b> de <b>${_sesionesTotal}</b> registros</span>`;

    cont.innerHTML = info + `<div class="pag-btns">${_renderPagHtml(curPage, totalPages, 'irPaginaSesiones')}</div>`;

}



// ── Paginación reutilizable ────────────────────────────────────────
// Genera HTML con < 1 2 3 ... 57 > y puntos suspensivos
function _renderPagHtml(curPage, totalPages, onClickFn) {
    if (totalPages <= 1) return '';
    const btn = (i, label, disabled = false, active = false) =>
        `<button class="pag-btn${active ? ' pag-btn-activo' : ''}" ${disabled ? 'disabled' : `onclick="${onClickFn}(${i})"`}>${label}</button>`;

    let h = `<button class="pag-btn pag-arrow" ${curPage === 0 ? 'disabled' : `onclick="${onClickFn}(${curPage - 1})"`}><i class="ph ph-caret-left"></i></button>`;

    const pages = new Set();
    pages.add(0);
    pages.add(totalPages - 1);
    for (let i = Math.max(0, curPage - 1); i <= Math.min(totalPages - 1, curPage + 1); i++) pages.add(i);

    let prev = -1;
    for (const p of [...pages].sort((a, b) => a - b)) {
        if (prev !== -1 && p - prev > 1) h += `<span class="pag-dots">…</span>`;
        h += btn(p, p + 1, false, p === curPage);
        prev = p;
    }

    h += `<button class="pag-btn pag-arrow" ${curPage >= totalPages - 1 ? 'disabled' : `onclick="${onClickFn}(${curPage + 1})"`}><i class="ph ph-caret-right"></i></button>`;
    return h;
}

function irPaginaSesiones(pagina) {

    _sesionesOffset = pagina * _SESIONES_LIMIT;

    aplicarFiltros(false);

}



function ordenarPor(col) {

    if (_sortBy === col) {

        _sortDir = _sortDir === 'asc' ? 'desc' : 'asc';

    } else {

        _sortBy  = col;

        _sortDir = 'asc';

    }

    aplicarFiltros();

}



function _actualizarFlechas() {

    document.querySelectorAll('.hist-sort-btn, .th-sortable').forEach(btn => {

        const arrow = btn.querySelector('.sort-arrow');

        if (btn.dataset.col === _sortBy) {

            if (arrow) arrow.textContent = _sortDir === 'asc' ? ' ▲' : ' ▼';

            btn.dataset.active = '1';

        } else {

            if (arrow) arrow.textContent = '';

            delete btn.dataset.active;

        }

    });

}






function exportarPdfFiltrado() {

    _descargarArchivo(

        `${API_BASE}/admin/exportar-pdf?${_buildParams()}`,

        'Descargando PDF filtrado...',

        'historial.pdf'

    );

}



async function cargarDashboard() {

    try {

        const [statsRes, termRes, sesRes] = await Promise.all([

            fetch(`${API_BASE}/dashboard/stats`,  { headers: authHeaders(), cache: 'no-store' }),

            fetch(`${API_BASE}/terminales`,        { headers: authHeaders(), cache: 'no-store' }),

            fetch(_sesionesUrl(),                  { headers: authHeaders(), cache: 'no-store' })

        ]);



        let terminales = [];

        let sesiones  = [];



        if (termRes.ok) terminales = await termRes.json();

        if (sesRes.ok) {
            const sesData = await sesRes.json();
            sesiones = Array.isArray(sesData) ? sesData : (sesData.items || []);
            _sesionesTotal = Array.isArray(sesData) ? sesiones.length : (sesData.total || 0);
        }



        if (statsRes.ok) {

            const s = await statsRes.json();

            document.getElementById('totalTerminales').textContent  = s.total_terminales;

            document.getElementById('terminalesActivas').textContent = s.terminales_activas;

            document.getElementById('sesionesActivas').textContent  = s.sesiones_activas;

            // Tarjeta KPI de sesiones (Monitoreo, layout dashboard)
            const sesKpi = document.getElementById('sesionesActivasKpi');
            if (sesKpi) sesKpi.textContent = s.sesiones_activas;

            document.getElementById('totalAlumnos').textContent     = s.total_alumnos;

            // Fecha de hoy en stat card
            const fechaEl = document.getElementById('monFechaHoy');
            if (fechaEl) {
                const hoy = new Date();
                fechaEl.textContent = hoy.toLocaleDateString('es-PE', { day: 'numeric', month: 'long', year: 'numeric' });
            }

            // Badge de equipos
            const badgeEl = document.getElementById('badgeEquipos');
            if (badgeEl) badgeEl.textContent = `${s.total_terminales} equipo${s.total_terminales !== 1 ? 's' : ''} en línea`;

        }



        renderTerminales(terminales, sesiones);

        renderSesiones(sesiones);

    } catch (e) {

        addLog('error', `Error cargando dashboard: ${e.message}`);

    }

}



// ── WebSocket ──────────────────────────────────────────────────────



function conectarWebSocket() {

    if (wsAdmin && wsAdmin.readyState === WebSocket.OPEN) return;



    // Si hemos intentado demasiadas veces, esperar más

    if (_reconnectAttempts >= _MAX_RECONNECT_ATTEMPTS) {

        addLog('error', `Máximo número de reintentos alcanzado (${_MAX_RECONNECT_ATTEMPTS}). Próximo intento en ${_reconnectDelay/1000}s...`);

    } else if (_reconnectAttempts > 0) {

        addLog('activity', `Reintentando conexión WebSocket (intento ${_reconnectAttempts}/${_MAX_RECONNECT_ATTEMPTS})...`);

    } else {

        addLog('activity', 'Conectando WebSocket admin...');

    }



    // A-8: el token YA NO va en la URL (quedaría en logs/historial). Se envía
    // como primer mensaje {tipo:"auth"} apenas se abre la conexión.
    wsAdmin = new WebSocket(`${WS_BASE}/ws/admin`);



    wsAdmin.onopen = () => {

        // Autenticación: primer mensaje obligatorio antes que cualquier otra cosa.
        wsAdmin.send(JSON.stringify({ tipo: 'auth', token: token }));

        setWsStatus(true);

        clearTimeout(_reconnectTimer);

        _reconnectAttempts = 0;

        _reconnectDelay = 2000; // Resetear delay

        addLog('activity', 'WebSocket admin CONECTADO');

    };



    wsAdmin.onmessage = (event) => {

        let data;

        try { data = JSON.parse(event.data); } catch { return; }



        if (data.tipo === 'status_update') {

            addLog('activity', `Status update: ${data.total} terminal(es) conectada(s) [${(data.terminales||[]).join(', ')}]`);

            cargarDashboard();

        } else if (data.tipo === 'evento_log') {

            const cat = (data.nivel === 'error' || data.nivel === 'offline') ? 'error' : 'activity';

            addLog(cat, data.mensaje);

            if (data.nivel === 'offline' || (data.mensaje && data.mensaje.includes('desconectada'))) {

                cargarDashboard();

            }

        } else if (data.tipo === 'ok') {

            addLog('activity', `Servidor: ${data.mensaje}`);

            mostrarNotificacion(data.mensaje, 'ok');

            // Cerrar modal de desbloqueo si estaba esperando

            if (_pendingUnlockModal) { _pendingUnlockModal.style.display = 'none'; _pendingUnlockModal = null; }

            cargarDashboard();

        } else if (data.tipo === 'info') {

            addLog('activity', `${data.motivo}`);

        } else if (data.tipo === 'error') {

            addLog('error', `Servidor: ${data.motivo}`);

            // Si hay modal de desbloqueo abierto, mostrar el error dentro en lugar de cerrar

            if (_pendingUnlockModal) {

                const errEl = document.getElementById('modal-desbloqueo-error');

                if (errEl) { errEl.style.color = '#ef4444'; errEl.textContent = data.motivo; }

                const btnC = document.getElementById('btn-desbloqueo-cancelar');

                const btnX = document.getElementById('btn-desbloqueo-confirmar');

                if (btnC) btnC.disabled = false;

                if (btnX) btnX.disabled = false;

                _pendingUnlockModal = null;

            } else {

                mostrarNotificacion(data.motivo, 'error');

            }

        } else if (data.tipo === 'actividad') {

            // Actualizar tabla de actividad en tiempo real si la pestaña está visible
            const tabActivo = document.getElementById('tab-actividad');
            if (tabActivo && tabActivo.style.display !== 'none') {
                cargarActividad(false);
            }
            // Actualizar badge y mini-panel siempre
            _actActualizarBadge();
            _cargarEventosMini();

        } else if (data.tipo === 'sospecha') {

            _handleSospecha(data);

        } else {

            addLog('activity', `WS msg: ${JSON.stringify(data).substring(0, 120)}`);

        }

    };



    wsAdmin.onclose = () => {

        setWsStatus(false);

        _reconnectAttempts++;

        

        // Calcular delay exponencial (cap a 60s)

        const exponentialDelay = Math.min(2000 * Math.pow(1.5, _reconnectAttempts - 1), 60000);

        _reconnectDelay = exponentialDelay;

        

        if (_reconnectAttempts === 1) {

            addLog('error', `WebSocket admin DESCONECTADO — reintentando en ${_reconnectDelay/1000}s...`);

        } else if (_reconnectAttempts <= _MAX_RECONNECT_ATTEMPTS) {

            addLog('error', `WebSocket desconectado (intento ${_reconnectAttempts}) — reintentando en ${_reconnectDelay/1000}s...`);

        } else {

            // Seguir reintentando cada 60s indefinidamente hasta que el servidor vuelva
            _reconnectDelay = 60000;
            addLog('error', `WebSocket: sin conexión. Reintentando cada 60s...`);

        }



        _reconnectTimer = setTimeout(conectarWebSocket, _reconnectDelay);

    };



    wsAdmin.onerror = (e) => {

        addLog('error', 'Error en WebSocket admin — intentando reconectar...');

        wsAdmin.close();

    };

}



function wsEnviar(payload) {

    if (!wsAdmin || wsAdmin.readyState !== WebSocket.OPEN) {

        addLog('error', `WS no conectado — no se pudo enviar: ${payload.tipo}`);

        mostrarNotificacion('Sin conexión WebSocket con el servidor', 'error');

        return false;

    }

    addLog('activity', `Enviando WS: ${payload.tipo} ${payload.ip ? '-> ' + payload.ip : ''}`);

    wsAdmin.send(JSON.stringify(payload));

    return true;

}



// ── Sincronización de tiempos ──────────────────────────────────────

function obtenerHoraActual() {

    return new Date().toLocaleString('es-PE');

}



// ── Acciones de terminal ───────────────────────────────────────────



function bloquearTerminal(ip, nombrePc, nombreAlumno) {

    const pcLabel      = nombrePc    || ip;

    const alumnoLinea  = nombreAlumno

        ? `Se finalizará la sesión de <strong>${escapeHtml(nombreAlumno)}</strong> y se registrará su hora de salida.`

        : 'La terminal quedará disponible.';

    mostrarConfirmacionBloqueo(

        `<strong>[${escapeHtml(pcLabel)}]</strong><br>${alumnoLinea}`,

        () => {

            addLog('activity', `Confirmado BLOQUEAR terminal: ${ip}`);

            wsEnviar({ tipo: 'bloquear_terminal', ip });

        }

    );

}



function mostrarConfirmacionBloqueo(htmlMensaje, onConfirm) {

    const modal = document.getElementById('modal-bloqueo');

    document.getElementById('modal-bloqueo-mensaje').innerHTML = htmlMensaje;

    _rebindBtn('btn-bloqueo-cancelar',  () => { modal.style.display = 'none'; });

    _rebindBtn('btn-bloqueo-confirmar', () => { modal.style.display = 'none'; onConfirm(); });

    modal.style.display = 'flex';

}



function mostrarModalDesbloqueo(ip, nombrePc) {

    const modal      = document.getElementById('modal-desbloqueo');

    const inputDni   = document.getElementById('modal-dni');

    const selectAct  = document.getElementById('modal-actividad');

    const otrosPanel = document.getElementById('modal-otros-panel');

    const otrosTxt   = document.getElementById('modal-otros-texto');

    const errorEl    = document.getElementById('modal-desbloqueo-error');

    const labelPc    = document.getElementById('modal-desbloqueo-nombre');



    // Resetear estado

    inputDni.value     = '';

    selectAct.value    = '';

    otrosPanel.style.display = 'none';

    otrosTxt.value     = '';

    errorEl.textContent = '';

    errorEl.style.color = '#ef4444';

    document.getElementById('btn-desbloqueo-confirmar').disabled = false;

    document.getElementById('btn-desbloqueo-cancelar').disabled  = false;

    _pendingUnlockModal = null;

    labelPc.textContent = nombrePc ? `Terminal: ${nombrePc}` : `Terminal: ${ip}`;



    selectAct.onchange = () => {

        otrosPanel.style.display = selectAct.value === 'Otros' ? 'block' : 'none';

        if (selectAct.value !== 'Otros') otrosTxt.value = '';

        errorEl.textContent = '';

    };



    _rebindBtn('btn-desbloqueo-cancelar', () => { modal.style.display = 'none'; });



    _rebindBtn('btn-desbloqueo-confirmar', () => {

        const dni = inputDni.value.trim();

        if (!dni || !/^\d{8}$/.test(dni)) {

            errorEl.textContent = 'Ingrese un DNI válido (8 dígitos)';

            inputDni.focus();

            return;

        }

        const actividad = selectAct.value;

        if (!actividad) {

            errorEl.textContent = 'Seleccione una actividad';

            selectAct.focus();

            return;

        }

        let razon = actividad;

        if (actividad === 'Otros') {

            const esp = otrosTxt.value.trim();

            if (!esp) {

                errorEl.textContent = 'Especifique la actividad';

                otrosTxt.focus();

                return;

            }

            razon = `Otros: ${esp}`;

        }

        // Mostrar estado de espera dentro del modal antes de cerrar

        errorEl.style.color = '#60a5fa';

        errorEl.textContent = 'Verificando en la UNASAM...';

        document.getElementById('btn-desbloqueo-confirmar').disabled = true;

        document.getElementById('btn-desbloqueo-cancelar').disabled  = true;



        addLog('activity', `Desbloquear terminal: ${ip} | DNI: ${dni} | Actividad: ${razon}`);

        wsEnviar({ tipo: 'desbloquear_terminal', ip, dni, razon_uso: razon });



        // El modal se cierra al recibir ok/error desde el WS (ver onmessage)

        // Guardamos contexto para poder cerrarlo desde el handler

        _pendingUnlockModal = modal;

        setTimeout(() => {

            // Seguridad: cerrar si no hubo respuesta en 15s

            if (_pendingUnlockModal) {

                _pendingUnlockModal = null;

                modal.style.display = 'none';

            }

        }, 15000);

    });



    // Enter en input DNI pasa al select

    inputDni.onkeydown = (e) => { if (e.key === 'Enter') selectAct.focus(); };



    modal.style.display = 'flex';

    setTimeout(() => inputDni.focus(), 50);

}



function bloquearTodas() {

    mostrarConfirmacion(

        'Esta acción enviará una orden de bloqueo inmediato a todos los equipos conectados. Los alumnos no podrán usar las PCs hasta que sean desbloqueadas manualmente o por el administrador.',

        () => {

            addLog('activity', 'Botón BLOQUEAR TODAS las terminales');

            wsEnviar({ tipo: 'bloquear_todas' });

        },

        { titulo: 'Bloquear Todos los Equipos', textoConfirmar: 'Confirmar Bloqueo Global' }

    );

}



async function finalizarTodo() {

    mostrarConfirmacion(

        'Advertencia: Se cerrarán todas las sesiones activas en este momento. Se registrará la hora de salida actual para todos los alumnos, pero los registros de la base de datos se mantendrán intactos.',

        async () => {

            addLog('activity', 'Botón FINALIZAR TODAS — solicitando cierre masivo...');

            try {

                const res = await fetch(`${API_BASE}/admin/cerrar-todas`, {

                    method: 'POST',

                    headers: authHeaders(),

                    cache: 'no-store'

                });

                if (res.ok) {

                    const body = await res.json();

                    addLog('activity', `Servidor: ${body.mensaje}`);

                    mostrarNotificacion('Sesiones finalizadas correctamente', 'ok');

                    cargarDashboard();

                } else {

                    const err = await res.json();

                    addLog('error', `Finalizar todo: HTTP ${res.status} — ${err.detail || 'Fallo'}`);

                    mostrarNotificacion('ERROR: ' + (err.detail || 'Fallo al finalizar'), 'error');

                }

            } catch (e) {

                addLog('error', `Error de red al finalizar todo: ${e.message}`);

                mostrarNotificacion('ERROR: Problema de conexión', 'error');

            }

        },

        { titulo: 'Finalizar Sesiones', textoConfirmar: 'Finalizar Sesiones' }

    );

}



async function limpiarTodo() {

    mostrarConfirmacion(

        'PELIGRO: Esta acción finalizará todas las sesiones activas Y BORRARÁ permanentemente el historial de sesiones actual de la base de datos. Use esto solo si desea iniciar un nuevo periodo desde cero.',

        async () => {

            addLog('activity', 'Botón LIMPIAR TODO — borrando historial de sesiones...');

            try {

                const res = await fetch(`${API_BASE}/admin/limpiar-sesiones`, {

                    method: 'DELETE',

                    headers: authHeaders(),

                    cache: 'no-store'

                });

                if (res.ok) {

                    const body = await res.json();

                    addLog('activity', `Servidor: ${body.mensaje}`);

                    mostrarNotificacion('Historial borrado. Nuevo periodo iniciado.', 'ok');

                    cargarDashboard();

                } else {

                    const err = await res.json().catch(() => ({}));

                    addLog('error', `Limpiar sesiones: HTTP ${res.status} — ${err.mensaje || err.detail || 'Fallo'}`);

                    mostrarNotificacion('ERROR: ' + (err.detail || 'Fallo al limpiar'), 'error');

                }

            } catch (e) {

                addLog('error', `Error de red al limpiar: ${e.message}`);

                mostrarNotificacion('ERROR: Problema de conexión', 'error');

            }

        },

        { titulo: 'Limpiar Historial', textoConfirmar: 'BORRAR TODO Y REINICIAR', critico: true }

    );

}



async function cerrarSesion(sesionId, motivo = 'admin', silent = false, nombrePc = null, nombreAlumno = null) {

    if (silent) {

        await ejecutarCierre(sesionId, motivo, true);

    } else {

        const pcPart = nombrePc ? ` en [${nombrePc}]` : '';

        const alumnoPart = nombreAlumno ? ` de ${nombreAlumno}` : '';

        mostrarConfirmacion(`¿Estás seguro de finalizar la sesión${alumnoPart}${pcPart}? Esta acción es irreversible.`, async () => {

            await ejecutarCierre(sesionId, motivo, false);

        });

    }

}



async function ejecutarCierre(sesionId, motivo, silent) {

    addLog('activity', `Cerrando sesión #${sesionId} (motivo: ${motivo})`);

    try {

        const res = await fetch(`${API_BASE}/sesiones/${sesionId}/cerrar?motivo=${motivo}&hora_salida=${encodeURIComponent(obtenerHoraActual())}`, {

            method: 'POST', headers: authHeaders()

        });

        if (res.ok) {

            let data = {};
            try { data = await res.json(); } catch (_) {}

            if (data.ya_cerrada) {
                addLog('activity', `Sesión #${sesionId} ya estaba cerrada`);
            } else {
                addLog('activity', `Sesión #${sesionId} cerrada OK`);
            }

            if (!silent) cargarDashboard();

        } else if (res.status === 404) {

            // La sesión no existe (ej. fantasma cancelada) — no es un fallo real
            // para el flujo de apagado; la PC igual recibirá el shutdown.
            addLog('activity', `Sesión #${sesionId} no encontrada (probablemente ya finalizada)`);

        } else {

            addLog('error', `Error cerrando sesión #${sesionId}: HTTP ${res.status}`);

        }

    } catch (e) {

        addLog('error', `Error de red cerrando sesión: ${e.message}`);

    }

}



async function apagarPc(ip, sesionId = null, nombrePc = null, nombreAlumno = null) {

    const pcLabel = nombrePc || ip;

    const alumnoLine = nombreAlumno ? `\nSe cerrará la sesión de: ${nombreAlumno}` : '';

    mostrarConfirmacion(`¿Confirmas el apagado de [${pcLabel}]?${alumnoLine}`, async () => {

        addLog('activity', `Botón APAGAR PC: ${ip}`);

        if (sesionId) {

            await cerrarSesion(sesionId, 'apagar', true);

        }

        wsEnviar({ tipo: 'remote_command', action: 'shutdown', ip, hora_salida: obtenerHoraActual() });

    });

}



// ── Renderizado ────────────────────────────────────────────────────



function renderTerminales(terminales, sesiones = []) {

    const grid = document.getElementById('terminalesGrid');

    if (!terminales.length) {

        grid.innerHTML = '<p class="empty-msg" style="grid-column:1/-1">No hay equipos registrados</p>';

        return;

    }

    // Si todas están offline después de filtrar, mostrar mensaje apropiado
    const conectadas = terminales.filter(t => t.estado !== 'offline');
    if (!conectadas.length) {
        grid.innerHTML = '<p class="empty-msg" style="grid-column:1/-1">Ninguna PC conectada en este momento</p>';
        return;
    }



    const lista = terminales.filter(t => t.estado !== 'offline');



    grid.innerHTML = lista.map(t => {

        const inputId = `unlock-${t.ip.replace(/\./g, '-')}`;

        const online  = t.estado !== 'offline';

        const bloqueado = t.estado === 'bloqueado';

        const faltaDesbloqueo = bloqueado && online;

        

        // Sesión activa embebida en la terminal (fuente canónica, sin límite ni filtros)
        // Fallback: buscar en el array de historial por IP (compatibilidad con respuestas antiguas)
        const sesion = t.sesion_activa || sesiones.find(s => s.terminal_ip === t.ip && s.activa);



        let botonesPrimarios = '';

        const pcNombre = esc(t.nombre || t.ip);

        const alumnoNombre = sesion ? esc(sesion.alumno_nombre) : '';



        if (t.nombre === 'IMPORTADO') {

            botonesPrimarios = `<p style="font-size:11px;color:var(--text-muted);text-align:center;margin:4px 0;line-height:1.5">Terminal virtual — necesaria para el historial importado desde Excel</p>`;

        } else if (!online) {

            const btnEliminar = _rolServidor === 'superadmin'
                ? `<button class="btn-card-eliminar-terminal" aria-label="Eliminar terminal" title="Eliminar terminal" onclick="eliminarTerminalFantasma(${t.id}, '${pcNombre}')"><i class="ph ph-trash"></i> <span class="btn-card-txt">Eliminar</span></button>`
                : '';

            botonesPrimarios = `

                <button class="btn-card-apagar" aria-label="Apagar PC" title="Apagar PC" onclick="apagarPc('${esc(t.ip)}', ${sesion ? sesion.id : 'null'}, '${pcNombre}', '${alumnoNombre}')"><i class="ph ph-power"></i> <span class="btn-card-txt">Apagar PC</span></button>
                ${btnEliminar}

            `;

        } else if (faltaDesbloqueo) {

            botonesPrimarios = `

                <button class="btn-card-desbloquear" aria-label="Desbloquear" title="Desbloquear" onclick="mostrarModalDesbloqueo('${esc(t.ip)}', '${pcNombre}')"><i class="ph ph-lock-open"></i> <span class="btn-card-txt">Desbloquear</span></button>

                <button class="btn-card-apagar" aria-label="Apagar PC" title="Apagar PC" onclick="apagarPc('${esc(t.ip)}', ${sesion ? sesion.id : 'null'}, '${pcNombre}', '${alumnoNombre}')"><i class="ph ph-power"></i> <span class="btn-card-txt">Apagar PC</span></button>

            `;

        } else {

            botonesPrimarios = `

                <button class="btn-card-bloquear" aria-label="Bloquear" title="Bloquear" onclick="bloquearTerminal('${esc(t.ip)}', '${pcNombre}', '${alumnoNombre}')"><i class="ph ph-lock"></i> <span class="btn-card-txt">Bloquear</span></button>

                <button class="btn-card-apagar" aria-label="Apagar PC" title="Apagar PC" onclick="apagarPc('${esc(t.ip)}', ${sesion ? sesion.id : 'null'}, '${pcNombre}', '${alumnoNombre}')"><i class="ph ph-power"></i> <span class="btn-card-txt">Apagar PC</span></button>

            `;

        }



        const dotClass = t.estado === 'activo' ? 'tc-dot-ok' : t.estado === 'bloqueado' ? 'tc-dot-wrn' : 'tc-dot-off';
        const alumnoHtml = sesion && sesion.activa
            ? `<div class="tc-alumno-pill"><span class="tc-alumno-name">${escapeHtml(sesion.alumno_nombre)}</span></div>`
            : t.estado === 'bloqueado'
                ? `<p class="tc-alumno-empty">PC Bloqueada por Admin</p>`
                : `<p class="tc-alumno-empty">Sin usuario asignado</p>`;

        return `
            <div class="terminal-card ${t.estado}">
                <div class="tc-header">
                    <div class="tc-name-row">
                        <span class="tc-status-dot ${dotClass}"></span>
                        <div>
                            <div class="terminal-nombre">${escapeHtml(t.nombre || t.ip)}</div>
                            <div class="terminal-ip">${escapeHtml(t.ip)}</div>
                        </div>
                    </div>
                    <div class="terminal-estado estado-${t.estado}">${estadoLabel(t.estado)}</div>
                </div>
                <div class="tc-user-area">${alumnoHtml}</div>
                <div class="tc-acciones">${botonesPrimarios}</div>
            </div>`;

    }).join('');

}



function renderSesiones(sesiones) {

    const body        = document.getElementById('sesionesBody');

    const sinSesiones = document.getElementById('sinSesiones');



    if (!sesiones.length) {

        body.innerHTML = '';

        sinSesiones.style.display = 'block';

        return;

    }



    sinSesiones.style.display = 'none';

    // BUG-6: enmascarar el DNI en la consola del panel (que es exportable a .txt),
    // coherente con el enmascarado de PII del servidor (B-8). Solo últimos 4 dígitos.
    sesiones.filter(s => s.activa).forEach(s => {
        const dni = s.alumno_dni || s.dni || '';
        const dniMasked = dni ? `****${String(dni).slice(-4)}` : '—';
        addLog('activity', `[ID] ${s.alumno_nombre} | Código: ${s.alumno_codigo} | DNI: ${dniMasked}`);
    });

    body.innerHTML = sesiones.map(s => {

        const inicio = new Date(s.inicio).toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit' });

        const salidaRaw = s.hora_salida_fmt
            ? s.hora_salida_fmt
            : (s.hora_salida ? new Date(s.hora_salida).toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit' }) : null);

        const duracionStr = s.activa
            ? `<span class="hc-duration live">En curso</span>`
            : `<span class="hc-duration">${esc(s.duracion_fmt || '—')}</span>`;

        const horaRango = salidaRaw
            ? `<span class="hc-time-range">${inicio} – ${salidaRaw}</span>`
            : `<span class="hc-time-range">${inicio} – Actualidad</span>`;

        const estadoBadge = s.activa
            ? `<span class="hc-badge live"><span class="dot"></span>ACTIVA</span>`
            : `<span class="hc-badge closed">COMPLETADA</span>`;

        const fecha = s.fecha_uso
            ? new Date(s.fecha_uso + 'T00:00:00').toLocaleDateString('es-PE', { day: '2-digit', month: '2-digit', year: 'numeric' })
            : new Date(s.inicio).toLocaleDateString('es-PE', { day: '2-digit', month: '2-digit', year: 'numeric' });

        return `
        <div class="hc-row${s.activa ? ' hc-row--active' : ''}">
            <div class="hc-left">
                <span class="hc-date"><i class="ph ph-calendar-blank"></i> ${fecha}</span>
                <span class="hc-pc">${esc(s.terminal_nombre || '—')}</span>
            </div>
            <div class="hc-center">
                <span class="hc-name">${esc(s.alumno_nombre)}</span>
                <div class="hc-meta">
                    <code class="hc-dni">${esc(s.alumno_dni || s.dni || '—')}</code>
                    <span class="hc-sep">•</span>
                    <span>${esc(s.facultad || '—')}</span>
                    <span class="hc-sep">•</span>
                    <span class="hc-activity">${esc(s.razon_uso || '—')}</span>
                </div>
            </div>
            <div class="hc-right">
                ${duracionStr}
                ${horaRango}
                ${estadoBadge}
            </div>
        </div>`;

    }).join('');

    _actualizarFlechas();

}



function _descargarArchivo(url, logMsg, fallbackName) {

    addLog('activity', logMsg);

    fetch(url, { headers: authHeaders() })

        .then(r => {

            if (!r.ok) throw new Error(`HTTP ${r.status}`);

            const disp = r.headers.get('Content-Disposition') || '';

            const match = disp.match(/filename=([^;]+)/);

            const filename = match ? match[1] : fallbackName;

            return r.blob().then(b => ({ b, filename }));

        })

        .then(({ b, filename }) => {

            const blobUrl = URL.createObjectURL(b);

            const link = document.createElement('a');

            link.href = blobUrl;

            link.download = filename;

            link.click();

            URL.revokeObjectURL(blobUrl);

            addLog('activity', `${fallbackName.split('.').pop().toUpperCase()} descargado correctamente`);

        })

        .catch(e => {

            addLog('error', `Error al exportar: ${e.message}`);

            mostrarNotificacion('Error al exportar archivo', 'error');

        });

}



function exportarExcel() {

    _descargarArchivo(`${API_BASE}/admin/exportar-excel`, 'Descargando historial Excel...', 'historial.xlsx');

}



function abrirImportarExcel() {

    mostrarConfirmacion(

        'Esta acción insertará registros masivos en el historial. Asegúrese de que el Excel siga el formato de exportación estándar para evitar errores.',

        () => { document.getElementById('inputImportarExcel').click(); },

        { titulo: 'Importar Historial (Excel)', textoConfirmar: 'Seleccionar archivo' }

    );

}



async function ejecutarImportacion(input) {

    const archivo = input.files[0];

    if (!archivo) return;

    input.value = '';



    mostrarNotificacion('Importando Excel...', 'ok');

    addLog('activity', `Importando archivo: ${archivo.name}`);



    const form = new FormData();

    form.append('archivo', archivo);



    try {

        const res = await fetch(`${API_BASE}/admin/importar-excel`, {

            method: 'POST',

            headers: { 'Authorization': `Bearer ${token}` },

            body: form

        });

        const body = await res.json();

        if (res.ok) {

            mostrarNotificacion(`${body.mensaje}`, 'ok');

            addLog('activity', `Importación: ${body.mensaje}`);

            if (body.detalle_errores && body.detalle_errores.length > 0) {

                body.detalle_errores.forEach(e => addLog('error', e));

            }

            cargarDashboard();

        } else {

            mostrarNotificacion(body.detail || 'Error al importar', 'error');

            addLog('error', `Importación fallida: ${body.detail}`);

        }

    } catch (e) {

        mostrarNotificacion('Error de conexión al importar', 'error');

        addLog('error', `Error de red al importar: ${e.message}`);

    }

}



function exportarPdf() {

    _descargarArchivo(`${API_BASE}/admin/exportar-pdf`, 'Descargando historial PDF...', 'historial.pdf');

}



// ── UI helpers ─────────────────────────────────────────────────────



// Reemplaza un botón por un clon limpio y le asigna el nuevo handler.

// Evita acumulación de listeners duplicados al reusar modales.

function _rebindBtn(id, handler) {

    const btn = document.getElementById(id);

    const clone = btn.cloneNode(true);

    btn.parentNode.replaceChild(clone, btn);

    clone.addEventListener('click', handler);

    return clone;

}



function mostrarConfirmacion(mensaje, onConfirm, { titulo = 'Advertencia', textoConfirmar = 'Confirmar', critico = false } = {}) {

    const modal = document.getElementById('modal-advertencia');

    const tituloEl   = document.getElementById('modal-titulo');

    const mensajeEl  = document.getElementById('modal-mensaje');

    const btnConfirm = document.getElementById('btn-modal-confirmar');



    if (tituloEl)  tituloEl.textContent  = titulo;

    if (mensajeEl) mensajeEl.textContent  = mensaje;

    if (btnConfirm) {

        btnConfirm.textContent = textoConfirmar;

        btnConfirm.style.background = critico ? '#dc2626' : '';

        btnConfirm.style.boxShadow  = critico ? '0 0 12px rgba(220,38,38,0.5)' : '';

    }

    _rebindBtn('btn-modal-confirmar', () => { modal.style.display = 'none'; onConfirm(); });

    _rebindBtn('btn-modal-cancelar',  () => { modal.style.display = 'none'; });

    modal.style.display = 'flex';

}



function estadoLabel(estado) {

    return { activo: 'Disponible', bloqueado: 'Bloqueado', offline: 'Offline' }[estado] ?? estado;

}



function setWsStatus(ok) {

    const el = document.getElementById('wsStatus');

    if (!el) return;

    el.className = ok ? 'ws-badge ok' : 'ws-badge off';

    const label = el.querySelector('.ws-label');

    if (label) label.textContent = ok ? 'WS Conectado' : 'WS Desconectado';

}



let _notifTimer = null;

function mostrarNotificacion(msg, tipo) {

    const el = document.getElementById('notificacion');

    if (!el) return;

    el.textContent = msg;

    el.className   = `notificacion ${tipo}`;

    el.style.display = 'block';

    clearTimeout(_notifTimer);

    _notifTimer = setTimeout(() => { el.style.display = 'none'; }, 4000);

}





// ── Maestro de Alumnos ────────────────────────────────────────────



let _maestroOffset  = 0;

const _maestroLimit = 10;

let _maestroSearch  = '';

let _maestroVisible = false;

let _maestroSoloVencidos = false;   // filtro "Solo vencidas"



function toggleMaestro() {

    // Con el nuevo sidebar, ir al tab de base de datos directamente
    if (typeof switchTab === 'function') {
        switchTab('basedatos');
    } else {
        _maestroVisible = !_maestroVisible;
        const sec = document.getElementById('seccionMaestro');
        if (sec) sec.style.display = _maestroVisible ? '' : 'none';
        if (_maestroVisible) { _maestroOffset = 0; cargarMaestro(); }
    }

}



let _buscarMaestroTimer = null;

function buscarMaestro() {

    clearTimeout(_buscarMaestroTimer);

    _buscarMaestroTimer = setTimeout(() => {

        _maestroSearch = document.getElementById('maestroBuscar')?.value.trim() || '';

        _maestroOffset = 0;

        cargarMaestro();

    }, 300);

}



async function cargarMaestro() {

    const params = new URLSearchParams({ limit: _maestroLimit, offset: _maestroOffset });

    if (_maestroSearch) params.set('search', _maestroSearch);

    if (_maestroSoloVencidos) params.set('solo_vencidos', 'true');

    try {

        const res = await fetch(`${API_BASE}/admin/maestro?${params}`, { headers: authHeaders(), cache: 'no-store' });

        if (!res.ok) { addLog('error', `Error cargando maestro: HTTP ${res.status}`); return; }

        const data = await res.json();

        renderMaestro(data);

    } catch (e) {

        addLog('error', `Error de red al cargar maestro: ${e.message}`);

    }

}



function renderMaestro(data) {

    const esSuperAdmin = _rolServidor === 'superadmin';
    const body   = document.getElementById('maestroBody');

    const empty  = document.getElementById('sinMaestro');

    const total  = document.getElementById('maestroTotal');

    const pag    = document.getElementById('maestroPaginacion');

    if (!body) return;



    if (total) total.textContent = `${data.total} registro(s)`;



    if (!data.alumnos.length) {

        body.innerHTML = '';

        if (empty) empty.style.display = '';

        if (pag)   pag.innerHTML = '';

        return;

    }

    if (empty) empty.style.display = 'none';



    body.innerHTML = data.alumnos.map(a => {
        // Estado de vigencia de la credencial.
        const vigHtml = a.vencido
            ? `<span class="vig-badge vig-vencido" title="Vencida${a.vence ? ' el ' + _fechaCorta(a.vence) : ''}">Vencida</span>`
            : (a.vence
                ? `<span class="vig-badge vig-ok" title="Vence el ${_fechaCorta(a.vence)}">Vence ${_fechaCorta(a.vence)}</span>`
                : `<span class="vig-badge vig-base" title="Padrón base, sin vencimiento">—</span>`);
        // Botón Renovar: solo si está vencida (admin o superadmin).
        const btnRenovar = a.vencido
            ? `<button title="Renovar credencial (otros ${data.vigencia_meses || 24} meses)" class="tbl-btn tbl-btn-ok"
                 onclick="renovarCredencial('${esc(a.dni)}','${esc(a.nombre)}')"><i class="ph ph-arrow-clockwise"></i></button>`
            : '';
        return `
        <tr${a.vencido ? ' class="fila-vencida"' : ''}>
            <td><code>${esc(a.dni)}</code></td>
            <td>${esc(a.nombre)}</td>
            <td>${esc(a.codigo || '—')}</td>
            <td style="font-size:12px">${esc(a.facultad || '—')}</td>
            <td style="font-size:12px">${esc(a.escuela  || '—')}</td>
            <td>${vigHtml}</td>
            <td>
                <div style="display:flex;gap:4px;align-items:center">
                    ${btnRenovar}
                    ${esSuperAdmin ? `<button title="Editar" class="tbl-btn tbl-btn-edit"
                        onclick="abrirEditarMaestro('${esc(a.dni)}','${esc(a.nombre)}','${esc(a.codigo||'')}','${esc(a.facultad||'')}','${esc(a.escuela||'')}')"><i class="ph ph-pencil-simple"></i></button>` : ''}
                    <button title="Registrar incidencia" class="tbl-btn tbl-btn-warn"
                        onclick="abrirNuevaIncidencia('${esc(a.dni)}')"><i class="ph ph-warning-circle"></i></button>
                    ${esSuperAdmin ? `<button title="Banear" class="tbl-btn tbl-btn-ban"
                        onclick="abrirBanearUsuario('${esc(a.dni)}','${esc(a.nombre)}')"><i class="ph ph-prohibit"></i></button>` : ''}
                </div>
            </td>
        </tr>`;
    }).join('');



    // Paginación simple

    if (pag) {

        const totalPages = Math.ceil(data.total / _maestroLimit);
        const curPage    = Math.floor(_maestroOffset / _maestroLimit);
        pag.innerHTML = _renderPagHtml(curPage, totalPages, 'irPaginaMaestro');

    }

}



// Formatea "2026-06-30T..." -> "30/06/2026" (para vigencia).
function _fechaCorta(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return '';
    const dd = String(d.getDate()).padStart(2, '0');
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    return `${dd}/${mm}/${d.getFullYear()}`;
}

// Alterna el filtro "Solo vencidas" y recarga el maestro.
function toggleMaestroVencidos() {
    _maestroSoloVencidos = !_maestroSoloVencidos;
    const btn = document.getElementById('maestroFiltroVencidos');
    if (btn) {
        btn.classList.toggle('db-btn-filtro-activo', _maestroSoloVencidos);
        btn.setAttribute('aria-pressed', _maestroSoloVencidos ? 'true' : 'false');
    }
    _maestroOffset = 0;
    cargarMaestro();
}

// Renueva la credencial de un alumno (resetea su vigencia a hoy).
function renovarCredencial(dni, nombre) {
    mostrarConfirmacion(
        `Renovar la credencial de <strong>${escapeHtml(nombre)}</strong>. Su acceso volverá a estar vigente desde hoy.`,
        async () => {
            try {
                const res = await fetch(`${API_BASE}/admin/maestro/${encodeURIComponent(dni)}/renovar`, {
                    method: 'POST', headers: authHeaders(),
                });
                const data = await res.json();
                if (!res.ok) { mostrarNotificacion(data.detail || 'No se pudo renovar', 'error'); return; }
                mostrarNotificacion(`Credencial renovada (vence ${_fechaCorta(data.vence)})`, 'ok');
                cargarMaestro();
            } catch (e) {
                mostrarNotificacion('Error de conexión', 'error');
            }
        },
        { titulo: 'Renovar credencial', textoConfirmar: 'Renovar' }
    );
}



function irPaginaMaestro(pagina) {

    _maestroOffset = pagina * _maestroLimit;

    cargarMaestro();

}



function abrirEditarMaestro(dni, nombre, codigo, facultad, escuela) {

    const modal = document.getElementById('modal-maestro');

    document.getElementById('modal-maestro-dni').textContent   = `DNI: ${dni}`;

    document.getElementById('maestro-edit-nombre').value    = nombre;

    document.getElementById('maestro-edit-codigo').value    = codigo;

    document.getElementById('maestro-edit-facultad').value  = facultad;

    document.getElementById('maestro-edit-escuela').value   = escuela;

    document.getElementById('modal-maestro-error').textContent = '';

    modal._dni = dni;



    _rebindBtn('btn-maestro-cancelar', () => { modal.style.display = 'none'; });

    _rebindBtn('btn-maestro-guardar',  async () => {

        const errEl = document.getElementById('modal-maestro-error');

        const nombre  = document.getElementById('maestro-edit-nombre').value.trim();

        const codigo  = document.getElementById('maestro-edit-codigo').value.trim();

        const fac     = document.getElementById('maestro-edit-facultad').value.trim();

        const esc_val = document.getElementById('maestro-edit-escuela').value.trim();



        if (!nombre) { errEl.textContent = 'El nombre es requerido'; return; }



        try {

            const res = await fetch(`${API_BASE}/admin/maestro/${encodeURIComponent(modal._dni)}`, {

                method: 'PUT',

                headers: authHeaders(),

                body: JSON.stringify({ nombre, codigo: codigo || null, facultad: fac || null, escuela: esc_val || null })

            });

            const body = await res.json();

            if (res.ok) {

                modal.style.display = 'none';

                mostrarNotificacion(body.mensaje, 'ok');

                addLog('activity', `Maestro actualizado: DNI ${modal._dni}`);

                cargarMaestro();

            } else {

                errEl.textContent = body.detail || 'Error al guardar';

            }

        } catch (e) {

            errEl.textContent = 'Error de conexión';

        }

    });

    modal.style.display = 'flex';

    setTimeout(() => document.getElementById('maestro-edit-nombre').focus(), 50);

}



function eliminarMaestro(dni, nombre) {

    mostrarConfirmacion(

        `¿Eliminar a <strong>${escapeHtml(nombre)}</strong> (DNI: ${escapeHtml(dni)}) del maestro?`,

        async () => {

            try {

                const res = await fetch(`${API_BASE}/admin/maestro/${encodeURIComponent(dni)}`, {

                    method: 'DELETE', headers: authHeaders()

                });

                const body = await res.json();

                if (res.ok) {

                    mostrarNotificacion(body.mensaje, 'ok');

                    addLog('activity', `Maestro: eliminado DNI ${dni}`);

                    cargarMaestro();

                } else {

                    mostrarNotificacion(body.detail || 'Error', 'error');

                }

            } catch (e) {

                mostrarNotificacion('Error de conexión', 'error');

            }

        },

        { titulo: 'Eliminar del Maestro', textoConfirmar: 'Eliminar', critico: true }

    );

}



async function importarMaestro(input) {

    const archivo = input.files[0];

    if (!archivo) return;

    input.value = '';



    const resultado = document.getElementById('maestroResultado');

    if (resultado) { resultado.style.display = ''; resultado.className = 'maestro-resultado cargando'; resultado.textContent = 'Importando...'; }

    mostrarNotificacion('Importando maestro...', 'ok');

    addLog('activity', `Importando maestro: ${archivo.name}`);



    const form = new FormData();

    form.append('archivo', archivo);



    try {

        const res  = await fetch(`${API_BASE}/admin/importar-maestro`, {

            method: 'POST',

            headers: { 'Authorization': `Bearer ${token}` },

            body: form

        });

        const data = await res.json();

        if (res.ok) {

            const msg = `${data.insertados} nuevo(s)  |  ${data.actualizados} actualizado(s)${data.errores ? '  |  ' + data.errores + ' ignorado(s)' : ''}`;

            if (resultado) { resultado.className = 'maestro-resultado ok'; resultado.innerHTML = msg; }

            mostrarNotificacion('Importación completada', 'ok');

            addLog('activity', `Maestro: ${data.mensaje}`);

            if (data.detalle_errores?.length) data.detalle_errores.forEach(e => addLog('error', e));

            _maestroOffset = 0;

            cargarMaestro();

        } else {

            const err = data.detail || 'Error en importación';

            if (resultado) { resultado.className = 'maestro-resultado error'; resultado.textContent = err; }

            mostrarNotificacion(err, 'error');

            addLog('error', `Maestro importación: ${err}`);

        }

    } catch (e) {

        if (resultado) { resultado.className = 'maestro-resultado error'; resultado.textContent = 'Error de conexión'; }

        mostrarNotificacion('Error de conexión', 'error');

        addLog('error', `Error de red al importar maestro: ${e.message}`);

    }

}



// escapeHtml y esc definidos al inicio del archivo



// ── Nuevo Usuario Manual ──────────────────────────────────────────────



function abrirNuevoUsuario() {

    const modal   = document.getElementById('modal-nuevo-usuario');

    const errorEl = document.getElementById('nuevo-usuario-error');



    document.getElementById('nuevo-dni').value      = '';

    document.getElementById('nuevo-nombre').value   = '';

    document.getElementById('nuevo-codigo').value   = '';

    document.getElementById('nuevo-facultad').value = '';

    document.getElementById('nuevo-escuela').value  = '';

    errorEl.textContent = '';

    modal.style.display = 'flex';

    setTimeout(() => document.getElementById('nuevo-dni').focus(), 50);



    _rebindBtn('btn-nuevo-cancelar', () => { modal.style.display = 'none'; });

    _rebindBtn('btn-nuevo-guardar', async () => {

        const dni      = document.getElementById('nuevo-dni').value.trim();

        const nombre   = document.getElementById('nuevo-nombre').value.trim();

        const codigo   = document.getElementById('nuevo-codigo').value.trim();

        const facultad = document.getElementById('nuevo-facultad').value.trim();

        const escuela  = document.getElementById('nuevo-escuela').value.trim();



        if (!/^\d{8}$/.test(dni)) {

            errorEl.textContent = 'El DNI debe tener exactamente 8 dígitos numéricos.';

            document.getElementById('nuevo-dni').focus();

            return;

        }

        if (!nombre) {

            errorEl.textContent = 'El nombre completo es obligatorio.';

            document.getElementById('nuevo-nombre').focus();

            return;

        }



        errorEl.textContent = '';

        try {

            const res = await fetch(`${API_BASE}/admin/maestro/nuevo`, {

                method: 'POST',

                headers: authHeaders(),

                body: JSON.stringify({ dni, nombre, codigo: codigo || null, facultad: facultad || null, escuela: escuela || null })

            });

            const body = await res.json();

            if (res.ok) {

                modal.style.display = 'none';

                mostrarNotificacion(body.mensaje, 'ok');

                addLog('activity', `Nuevo usuario registrado: ${nombre} (DNI ${dni})`);

                _maestroOffset = 0;

                cargarMaestro();

                // Refrescar indicadores: el alta cambia 'Nuevos del mes', el total
                // y el gráfico de alumnos nuevos. Si la pestaña no está visible, igual
                // deja el estado al día para la próxima vez.
                if (typeof cargarIndicadores === 'function') cargarIndicadores();

            } else {

                errorEl.textContent = body.detail || 'Error al registrar.';

            }

        } catch (e) {

            errorEl.textContent = 'Error de conexión.';

        }

    });

}



// ── Limpiar Base de Datos de Alumnos (solo Nivel 2) ──────────────────






// Enter para login

document.addEventListener('keydown', (e) => {

    if (e.key === 'Enter' && document.getElementById('loginPanel').style.display !== 'none')

        login();

});



// =========================================================================
// EXPORTAR BACKUP DEL MAESTRO A EXCEL
// =========================================================================
function exportarMaestroBackup() {
    if (!token) return;
    _descargarArchivo(API_BASE + '/alumnos/exportar', 'Descargando backup de usuarios...', 'backup_alumnos.xlsx');
}


// =========================================================================
// SISTEMA DE BANS
// =========================================================================

function abrirBanearUsuario(dni, nombre) {
    const modal   = document.getElementById('modal-ban');
    const errEl   = document.getElementById('modal-ban-error');
    const nombreEl = document.getElementById('modal-ban-nombre');

    document.getElementById('ban-dias').value   = '7';
    document.getElementById('ban-motivo').value = '';
    errEl.textContent = '';
    nombreEl.textContent = `Usuario: ${nombre} — DNI: ${dni}`;
    modal._dni    = dni;
    modal._nombre = nombre;
    modal.style.display = 'flex';
    setTimeout(() => document.getElementById('ban-dias').focus(), 50);

    _rebindBtn('btn-ban-cancelar',  () => { modal.style.display = 'none'; });
    _rebindBtn('btn-ban-confirmar', async () => {
        const dias   = parseInt(document.getElementById('ban-dias').value) || 0;
        const motivo = document.getElementById('ban-motivo').value.trim();
        if (dias < 1 || dias > 365) { errEl.textContent = 'Ingrese entre 1 y 365 días'; return; }
        if (!motivo) { errEl.textContent = 'El motivo es obligatorio'; document.getElementById('ban-motivo').focus(); return; }
        errEl.textContent = '';
        try {
            const res  = await fetch(`${API_BASE}/admin/bans`, {
                method: 'POST', headers: authHeaders(),
                body: JSON.stringify({ dni: modal._dni, motivo, dias })
            });
            const body = await res.json();
            if (res.ok) {
                modal.style.display = 'none';
                mostrarNotificacion(body.mensaje, 'ok');
                addLog('activity', `Baneado: ${modal._nombre} (DNI ${modal._dni}) por ${dias} día(s)`);
                cargarBans();
            } else {
                errEl.textContent = body.detail || 'Error al banear';
            }
        } catch (e) {
            errEl.textContent = 'Error de conexión';
        }
    });
}

async function cargarBans() {
    try {
        const res  = await fetch(`${API_BASE}/admin/bans`, { headers: authHeaders(), cache: 'no-store' });
        if (!res.ok) return;
        const bans = await res.json();
        renderBans(bans);
    } catch (e) {
        addLog('error', `Error cargando bans: ${e.message}`);
    }
}

function renderBans(bans) {
    const body   = document.getElementById('bansBody');
    const empty  = document.getElementById('sinBans');
    const total  = document.getElementById('bansTotal');
    if (!body) return;

    if (total) total.textContent = `${bans.length} usuario(s) baneado(s)`;

    if (!bans.length) {
        body.innerHTML = '';
        if (empty) empty.style.display = '';
        return;
    }
    if (empty) empty.style.display = 'none';

    body.innerHTML = bans.map(b => {
        const fechaIni = b.fecha_ini ? new Date(b.fecha_ini).toLocaleDateString('es-PE') : '—';
        const expira   = b.fecha_fin ? new Date(b.fecha_fin).toLocaleDateString('es-PE') : 'Indefinido';
        return `<tr>
            <td><code>${esc(b.dni)}</code></td>
            <td>${esc(b.nombre)}</td>
            <td style="font-size:12px;color:var(--t2)">${esc(b.motivo)}</td>
            <td style="font-size:12px"><span class="cell-time">${fechaIni}</span></td>
            <td style="font-size:12px"><span class="cell-time">${expira}</span></td>
            <td style="font-size:12px;color:var(--t3)">${esc(b.baneado_por || '—')}</td>
            <td>
                <button title="Levantar ban" style="padding:4px 10px;font-size:12px;background:var(--err-dim);border:1px solid var(--err-bdr);border-radius:6px;cursor:pointer;color:var(--err);font-weight:600"
                    onclick="levantarBan(${b.id},'${esc(b.nombre)}')">Levantar</button>
            </td>
        </tr>`;
    }).join('');
}

// =========================================================================
// PERSONAL UNIVERSITARIO
// =========================================================================

let _personalOffset  = 0;
const _personalLimit = 10;
let _personalSearch  = '';
let _buscarPersonalTimer = null;

function buscarPersonal() {
    clearTimeout(_buscarPersonalTimer);
    _buscarPersonalTimer = setTimeout(() => {
        _personalSearch = document.getElementById('personalBuscar')?.value.trim() || '';
        _personalOffset = 0;
        cargarPersonal();
    }, 300);
}

async function cargarPersonal() {
    const params = new URLSearchParams({ limit: _personalLimit, offset: _personalOffset });
    if (_personalSearch) params.set('search', _personalSearch);
    try {
        const res = await fetch(`${API_BASE}/admin/personal?${params}`, { headers: authHeaders(), cache: 'no-store' });
        if (!res.ok) { addLog('error', `Error cargando personal: HTTP ${res.status}`); return; }
        renderPersonal(await res.json());
    } catch (e) {
        addLog('error', `Error de red al cargar personal: ${e.message}`);
    }
}

function renderPersonal(data) {
    const body  = document.getElementById('personalBody');
    const empty = document.getElementById('sinPersonal');
    const total = document.getElementById('personalTotal');
    const pag   = document.getElementById('personalPaginacion');
    if (!body) return;

    if (total) total.textContent = `${data.total} registro(s)`;

    if (!data.personal.length) {
        body.innerHTML = '';
        if (empty) empty.style.display = '';
        if (pag)   pag.innerHTML = '';
        return;
    }
    if (empty) empty.style.display = 'none';

    body.innerHTML = data.personal.map(p => `
        <tr>
            <td><code>${esc(p.dni)}</code></td>
            <td>${esc(p.nombre)}</td>
            <td><span class="cell-tag">${esc(p.cargo || '—')}</span></td>
            <td style="font-size:12px;color:var(--t2)">${esc(p.area || '—')}</td>
            <td style="font-size:12px;color:var(--t3)">${esc(p.correo || '—')}</td>
            <td style="font-size:12px;color:var(--t3)">${esc(p.telefono || '—')}</td>
            <td>
                <div style="display:flex;gap:4px;align-items:center">
                    <button title="Editar" class="tbl-btn tbl-btn-edit"
                        onclick="abrirEditarPersonal('${esc(p.dni)}','${esc(p.nombre)}','${esc(p.cargo||'')}','${esc(p.area||'')}','${esc(p.correo||'')}','${esc(p.telefono||'')}')"><i class="ph ph-pencil-simple"></i></button>
                    <button title="Eliminar" class="tbl-btn tbl-btn-delete"
                        onclick="eliminarPersonal('${esc(p.dni)}','${esc(p.nombre)}')"><i class="ph ph-trash"></i></button>
                </div>
            </td>
        </tr>`).join('');

    if (pag) {
        const totalPages = Math.ceil(data.total / _personalLimit);
        const curPage    = Math.floor(_personalOffset / _personalLimit);
        pag.innerHTML = _renderPagHtml(curPage, totalPages, 'irPaginaPersonal');
    }
}

function irPaginaPersonal(pagina) {
    _personalOffset = pagina * _personalLimit;
    cargarPersonal();
}

function abrirNuevoPersonal() {
    const modal   = document.getElementById('modal-nuevo-personal');
    const errorEl = document.getElementById('np-error');
    ['np-dni','np-nombre','np-cargo','np-area','np-correo','np-telefono'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    errorEl.textContent = '';
    modal.style.display = 'flex';
    setTimeout(() => document.getElementById('np-dni').focus(), 50);

    _rebindBtn('btn-np-cancelar', () => { modal.style.display = 'none'; });
    _rebindBtn('btn-np-guardar', async () => {
        const dni      = document.getElementById('np-dni').value.trim();
        const nombre   = document.getElementById('np-nombre').value.trim();
        const cargo    = document.getElementById('np-cargo').value.trim();
        const area     = document.getElementById('np-area').value.trim();
        const correo   = document.getElementById('np-correo').value.trim();
        const telefono = document.getElementById('np-telefono').value.trim();

        if (!/^\d{8}$/.test(dni)) { errorEl.textContent = 'El DNI debe tener 8 dígitos numéricos.'; document.getElementById('np-dni').focus(); return; }
        if (!nombre) { errorEl.textContent = 'El nombre es obligatorio.'; document.getElementById('np-nombre').focus(); return; }
        errorEl.textContent = '';
        try {
            const res = await fetch(`${API_BASE}/admin/personal/nuevo`, {
                method: 'POST', headers: authHeaders(),
                body: JSON.stringify({ dni, nombre, cargo: cargo||null, area: area||null, correo: correo||null, telefono: telefono||null })
            });
            const body = await res.json();
            if (res.ok) {
                modal.style.display = 'none';
                mostrarNotificacion(body.mensaje, 'ok');
                addLog('activity', `Personal registrado: ${nombre} (DNI ${dni})`);
                _personalOffset = 0;
                cargarPersonal();
            } else {
                errorEl.textContent = body.detail || 'Error al registrar.';
            }
        } catch (e) {
            errorEl.textContent = 'Error de conexión.';
        }
    });
}

function abrirEditarPersonal(dni, nombre, cargo, area, correo, telefono) {
    const modal   = document.getElementById('modal-editar-personal');
    const errorEl = document.getElementById('ep-error');
    document.getElementById('ep-dni-label').textContent = `DNI: ${dni}`;
    document.getElementById('ep-nombre').value   = nombre;
    document.getElementById('ep-cargo').value    = cargo;
    document.getElementById('ep-area').value     = area;
    document.getElementById('ep-correo').value   = correo;
    document.getElementById('ep-telefono').value = telefono;
    errorEl.textContent = '';
    modal._dni = dni;
    modal.style.display = 'flex';
    setTimeout(() => document.getElementById('ep-nombre').focus(), 50);

    _rebindBtn('btn-ep-cancelar', () => { modal.style.display = 'none'; });
    _rebindBtn('btn-ep-guardar', async () => {
        const nombre   = document.getElementById('ep-nombre').value.trim();
        const cargo    = document.getElementById('ep-cargo').value.trim();
        const area     = document.getElementById('ep-area').value.trim();
        const correo   = document.getElementById('ep-correo').value.trim();
        const telefono = document.getElementById('ep-telefono').value.trim();
        if (!nombre) { errorEl.textContent = 'El nombre es obligatorio.'; return; }
        errorEl.textContent = '';
        try {
            const res = await fetch(`${API_BASE}/admin/personal/${encodeURIComponent(modal._dni)}`, {
                method: 'PUT', headers: authHeaders(),
                body: JSON.stringify({ nombre, cargo: cargo||null, area: area||null, correo: correo||null, telefono: telefono||null })
            });
            const body = await res.json();
            if (res.ok) {
                modal.style.display = 'none';
                mostrarNotificacion(body.mensaje, 'ok');
                addLog('activity', `Personal actualizado: DNI ${modal._dni}`);
                cargarPersonal();
            } else {
                errorEl.textContent = body.detail || 'Error al guardar';
            }
        } catch (e) {
            errorEl.textContent = 'Error de conexión.';
        }
    });
}

function eliminarPersonal(dni, nombre) {
    mostrarConfirmacion(
        `¿Eliminar a <strong>${escapeHtml(nombre)}</strong> (DNI: ${escapeHtml(dni)}) del registro de personal?`,
        async () => {
            try {
                const res  = await fetch(`${API_BASE}/admin/personal/${encodeURIComponent(dni)}`, { method: 'DELETE', headers: authHeaders() });
                const body = await res.json();
                if (res.ok) {
                    mostrarNotificacion(body.mensaje, 'ok');
                    addLog('activity', `Personal eliminado: DNI ${dni}`);
                    cargarPersonal();
                } else {
                    mostrarNotificacion(body.detail || 'Error', 'error');
                }
            } catch (e) {
                mostrarNotificacion('Error de conexión', 'error');
            }
        },
        { titulo: 'Eliminar Personal', textoConfirmar: 'Eliminar', critico: true }
    );
}

async function importarPersonal(input) {
    const archivo = input.files[0];
    if (!archivo) return;
    input.value = '';
    const resultado = document.getElementById('personalResultado');
    if (resultado) { resultado.style.display = ''; resultado.className = 'maestro-resultado cargando'; resultado.textContent = 'Importando...'; }
    mostrarNotificacion('Importando personal...', 'ok');
    const form = new FormData();
    form.append('archivo', archivo);
    try {
        const res  = await fetch(`${API_BASE}/admin/personal/importar`, { method: 'POST', headers: { 'Authorization': `Bearer ${token}` }, body: form });
        const data = await res.json();
        if (res.ok) {
            const msg = `${data.insertados} nuevo(s)  |  ${data.actualizados} actualizado(s)${data.errores ? '  |  ' + data.errores + ' ignorado(s)' : ''}`;
            if (resultado) { resultado.className = 'maestro-resultado ok'; resultado.innerHTML = msg; }
            mostrarNotificacion('Importación completada', 'ok');
            addLog('activity', `Personal: ${data.mensaje}`);
            _personalOffset = 0;
            cargarPersonal();
        } else {
            const err = data.detail || 'Error en importación';
            if (resultado) { resultado.className = 'maestro-resultado error'; resultado.textContent = err; }
            mostrarNotificacion(err, 'error');
        }
    } catch (e) {
        if (resultado) { resultado.className = 'maestro-resultado error'; resultado.textContent = 'Error de conexión'; }
        mostrarNotificacion('Error de conexión', 'error');
    }
}

function exportarPersonal() {
    _descargarArchivo(`${API_BASE}/admin/personal/exportar`, 'Exportando personal...', 'personal_universidad.xlsx');
}

// ── Egresados / Docentes / Autoridad (genérico parametrizado) ──────────
const _GRUPO_CFG = {
    egresados: {
        singular: 'Egresado', icono: 'ph-graduation-cap', archivo: 'egresados.xlsx',
        campos: [
            { key: 'codigo',      label: 'Código Univ.', max: 30,  ph: 'ej: 2020-1234' },
            { key: 'escuela',     label: 'Escuela',      max: 200, ph: 'ej: Ingeniería de Sistemas' },
            { key: 'anio_egreso', label: 'Año de Egreso',max: 4,   ph: 'ej: 2024' },
        ],
        cols: r => `
            <td><span class="cell-tag">${esc(r.codigo || '—')}</span></td>
            <td style="font-size:12px;color:var(--t2)">${esc(r.escuela || '—')}</td>
            <td style="font-size:12px;color:var(--t3)">${esc(r.anio_egreso || '—')}</td>`,
    },
    docentes: {
        singular: 'Docente', icono: 'ph-chalkboard-teacher', archivo: 'docentes.xlsx',
        campos: [
            { key: 'facultad', label: 'Facultad', max: 200, ph: 'ej: Ciencias' },
            { key: 'escuela',  label: 'Escuela',  max: 200, ph: 'ej: Ingeniería de Sistemas' },
            { key: 'correo',   label: 'Correo',   max: 150, ph: 'usuario@unasam.edu.pe' },
            { key: 'telefono', label: 'Teléfono', max: 20,  ph: 'ej: 943 000 000' },
        ],
        cols: r => `
            <td style="font-size:12px;color:var(--t2)">${esc(r.facultad || '—')}</td>
            <td style="font-size:12px;color:var(--t2)">${esc(r.escuela || '—')}</td>
            <td style="font-size:12px;color:var(--t3)">${esc(r.correo || '—')}</td>
            <td style="font-size:12px;color:var(--t3)">${esc(r.telefono || '—')}</td>`,
    },
    autoridades: {
        singular: 'Autoridad', icono: 'ph-shield-star', archivo: 'autoridades.xlsx',
        campos: [
            { key: 'cargo',    label: 'Cargo',    max: 150, ph: 'ej: Decano, Director' },
            { key: 'correo',   label: 'Correo',   max: 150, ph: 'usuario@unasam.edu.pe' },
            { key: 'telefono', label: 'Teléfono', max: 20,  ph: 'ej: 943 000 000' },
        ],
        cols: r => `
            <td><span class="cell-tag">${esc(r.cargo || '—')}</span></td>
            <td style="font-size:12px;color:var(--t3)">${esc(r.correo || '—')}</td>
            <td style="font-size:12px;color:var(--t3)">${esc(r.telefono || '—')}</td>`,
    },
};
const _grupoEstado = {
    egresados:   { limit: 50, offset: 0, search: '' },
    docentes:    { limit: 50, offset: 0, search: '' },
    autoridades: { limit: 50, offset: 0, search: '' },
};

async function cargarGrupo(grupo) {
    const st = _grupoEstado[grupo];
    const params = new URLSearchParams({ limit: st.limit, offset: st.offset });
    if (st.search) params.set('search', st.search);
    try {
        const res = await fetch(`${API_BASE}/admin/${grupo}?${params}`, { headers: authHeaders(), cache: 'no-store' });
        if (!res.ok) { addLog('error', `Error cargando ${grupo}: HTTP ${res.status}`); return; }
        _renderGrupo(grupo, await res.json());
    } catch (e) {
        addLog('error', `Error de red al cargar ${grupo}: ${e.message}`);
    }
}

function _cap(grupo) { return grupo.charAt(0).toUpperCase() + grupo.slice(1); }

function _renderGrupo(grupo, data) {
    const cfg   = _GRUPO_CFG[grupo];
    const body  = document.getElementById(`${grupo}Body`);
    const empty = document.getElementById(`sin${_cap(grupo)}`);
    const total = document.getElementById(`${grupo}Total`);
    const pag   = document.getElementById(`${grupo}Paginacion`);
    if (!body) return;
    if (total) total.textContent = `${data.total} registro(s)`;

    if (!data.items.length) {
        body.innerHTML = '';
        if (empty) empty.style.display = '';
        if (pag)   pag.innerHTML = '';
        return;
    }
    if (empty) empty.style.display = 'none';

    body.innerHTML = data.items.map(r => {
        const json = encodeURIComponent(JSON.stringify(r));
        return `
        <tr>
            <td><code>${esc(r.dni)}</code></td>
            <td>${esc(r.nombre)}</td>
            ${cfg.cols(r)}
            <td>
                <div style="display:flex;gap:4px;align-items:center">
                    <button title="Editar" class="tbl-btn tbl-btn-edit" onclick="abrirEditarGrupo('${grupo}','${json}')"><i class="ph ph-pencil-simple"></i></button>
                    <button title="Eliminar" class="tbl-btn tbl-btn-delete" onclick="eliminarGrupo('${grupo}','${esc(r.dni)}','${esc(r.nombre)}')"><i class="ph ph-trash"></i></button>
                </div>
            </td>
        </tr>`;
    }).join('');

    if (pag) {
        const totalPages = Math.ceil(data.total / _grupoEstado[grupo].limit);
        const curPage    = Math.floor(_grupoEstado[grupo].offset / _grupoEstado[grupo].limit);
        pag.innerHTML = _renderPagHtml(curPage, totalPages, `irPaginaGrupo_${grupo}`);
    }
}

// Handlers de paginación por grupo (el helper de paginación llama por nombre)
function irPaginaGrupo_egresados(p)   { _grupoEstado.egresados.offset   = p * _grupoEstado.egresados.limit;   cargarGrupo('egresados'); }
function irPaginaGrupo_docentes(p)    { _grupoEstado.docentes.offset    = p * _grupoEstado.docentes.limit;    cargarGrupo('docentes'); }
function irPaginaGrupo_autoridades(p) { _grupoEstado.autoridades.offset = p * _grupoEstado.autoridades.limit; cargarGrupo('autoridades'); }

let _grupoBuscarTimer = null;
function buscarGrupo(grupo) {
    const inp = document.getElementById(`${grupo}Buscar`);
    clearTimeout(_grupoBuscarTimer);
    _grupoBuscarTimer = setTimeout(() => {
        _grupoEstado[grupo].search = (inp?.value || '').trim();
        _grupoEstado[grupo].offset = 0;
        cargarGrupo(grupo);
    }, 250);
}

function _grupoCamposHtml(cfg, valores) {
    valores = valores || {};
    let html = `
        <label class="modal-field-label block text-xs text-zinc-500 dark:text-zinc-400 mb-1">DNI <span class="text-rose-500">*</span></label>
        <input id="grp-dni" type="text" maxlength="8" ${valores.dni ? 'value="'+esc(valores.dni)+'" disabled' : ''} class="modal-input w-full bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-zinc-900 dark:text-white text-sm rounded-lg px-3 py-2 mb-3 focus:outline-none focus:border-indigo-500 transition-colors ${valores.dni ? 'opacity-60' : ''}" placeholder="8 dígitos">
        <label class="modal-field-label block text-xs text-zinc-500 dark:text-zinc-400 mb-1">Nombre Completo <span class="text-rose-500">*</span></label>
        <input id="grp-nombre" type="text" maxlength="200" value="${esc(valores.nombre || '')}" class="modal-input w-full bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-zinc-900 dark:text-white text-sm rounded-lg px-3 py-2 mb-3 focus:outline-none focus:border-indigo-500 transition-colors" placeholder="Apellidos y Nombres">`;
    cfg.campos.forEach(c => {
        html += `
        <label class="modal-field-label block text-xs text-zinc-500 dark:text-zinc-400 mb-1">${esc(c.label)}</label>
        <input id="grp-${c.key}" type="text" maxlength="${c.max}" value="${esc(valores[c.key] || '')}" class="modal-input w-full bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-zinc-900 dark:text-white text-sm rounded-lg px-3 py-2 mb-3 focus:outline-none focus:border-indigo-500 transition-colors" placeholder="${esc(c.ph)}">`;
    });
    return html;
}

function _grupoRecolectar(cfg) {
    const obj = {
        dni:    document.getElementById('grp-dni').value.trim(),
        nombre: document.getElementById('grp-nombre').value.trim(),
    };
    cfg.campos.forEach(c => { obj[c.key] = document.getElementById('grp-' + c.key).value.trim() || null; });
    return obj;
}

function abrirNuevoGrupo(grupo) {
    const cfg = _GRUPO_CFG[grupo];
    const modal = document.getElementById('modal-grupo');
    document.getElementById('grupo-modal-titulo').innerHTML = `<i class="ph ${cfg.icono} text-indigo-500"></i> Nuevo ${cfg.singular}`;
    document.getElementById('grupo-modal-sub').textContent = `Registra un nuevo registro en ${cfg.singular}.`;
    document.getElementById('grupo-modal-campos').innerHTML = _grupoCamposHtml(cfg, null);
    document.getElementById('grupo-modal-error').textContent = '';
    document.getElementById('btn-grupo-guardar').textContent = 'Registrar';
    modal.style.display = 'flex';
    setTimeout(() => document.getElementById('grp-dni').focus(), 50);

    _rebindBtn('btn-grupo-cancelar', () => { modal.style.display = 'none'; });
    _rebindBtn('btn-grupo-guardar', async () => {
        const errorEl = document.getElementById('grupo-modal-error');
        const datos = _grupoRecolectar(cfg);
        if (!/^\d{8}$/.test(datos.dni)) { errorEl.textContent = 'El DNI debe tener 8 dígitos numéricos.'; return; }
        if (!datos.nombre) { errorEl.textContent = 'El nombre es obligatorio.'; return; }
        errorEl.textContent = '';
        try {
            const res = await fetch(`${API_BASE}/admin/${grupo}/nuevo`, {
                method: 'POST', headers: authHeaders(), body: JSON.stringify(datos)
            });
            const body = await res.json();
            if (res.ok) {
                modal.style.display = 'none';
                mostrarNotificacion(body.mensaje, 'ok');
                addLog('activity', `${cfg.singular} registrado: ${datos.nombre} (DNI ${datos.dni})`);
                _grupoEstado[grupo].offset = 0;
                cargarGrupo(grupo);
            } else {
                errorEl.textContent = body.detail || 'Error al registrar.';
            }
        } catch (e) { errorEl.textContent = 'Error de conexión.'; }
    });
}

function abrirEditarGrupo(grupo, jsonEnc) {
    const cfg = _GRUPO_CFG[grupo];
    const r = JSON.parse(decodeURIComponent(jsonEnc));
    const modal = document.getElementById('modal-grupo');
    document.getElementById('grupo-modal-titulo').innerHTML = `<i class="ph ph-pencil text-amber-500"></i> Editar ${cfg.singular}`;
    document.getElementById('grupo-modal-sub').textContent = `DNI: ${r.dni}`;
    document.getElementById('grupo-modal-campos').innerHTML = _grupoCamposHtml(cfg, r);
    document.getElementById('grupo-modal-error').textContent = '';
    document.getElementById('btn-grupo-guardar').textContent = 'Guardar';
    modal.style.display = 'flex';
    setTimeout(() => document.getElementById('grp-nombre').focus(), 50);

    _rebindBtn('btn-grupo-cancelar', () => { modal.style.display = 'none'; });
    _rebindBtn('btn-grupo-guardar', async () => {
        const errorEl = document.getElementById('grupo-modal-error');
        const datos = _grupoRecolectar(cfg);
        datos.dni = r.dni; // el DNI no se edita
        if (!datos.nombre) { errorEl.textContent = 'El nombre es obligatorio.'; return; }
        errorEl.textContent = '';
        try {
            const res = await fetch(`${API_BASE}/admin/${grupo}/${encodeURIComponent(r.dni)}`, {
                method: 'PUT', headers: authHeaders(), body: JSON.stringify(datos)
            });
            const body = await res.json();
            if (res.ok) {
                modal.style.display = 'none';
                mostrarNotificacion(body.mensaje, 'ok');
                addLog('activity', `${cfg.singular} actualizado: DNI ${r.dni}`);
                cargarGrupo(grupo);
            } else {
                errorEl.textContent = body.detail || 'Error al guardar';
            }
        } catch (e) { errorEl.textContent = 'Error de conexión.'; }
    });
}

function eliminarGrupo(grupo, dni, nombre) {
    const cfg = _GRUPO_CFG[grupo];
    mostrarConfirmacion(
        `¿Eliminar a <strong>${escapeHtml(nombre)}</strong> (DNI: ${escapeHtml(dni)}) de ${cfg.singular}?`,
        async () => {
            try {
                const res  = await fetch(`${API_BASE}/admin/${grupo}/${encodeURIComponent(dni)}`, { method: 'DELETE', headers: authHeaders() });
                const body = await res.json();
                if (res.ok) {
                    mostrarNotificacion(body.mensaje, 'ok');
                    addLog('activity', `${cfg.singular} eliminado: DNI ${dni}`);
                    cargarGrupo(grupo);
                } else {
                    mostrarNotificacion(body.detail || 'Error', 'error');
                }
            } catch (e) { mostrarNotificacion('Error de conexión', 'error'); }
        },
        { titulo: `Eliminar ${cfg.singular}`, textoConfirmar: 'Eliminar', critico: true }
    );
}

async function importarGrupo(grupo, input) {
    const cfg = _GRUPO_CFG[grupo];
    const archivo = input.files[0];
    if (!archivo) return;
    input.value = '';
    const resultado = document.getElementById(`${grupo}Resultado`);
    if (resultado) { resultado.style.display = ''; resultado.className = 'maestro-resultado cargando'; resultado.textContent = 'Importando...'; }
    mostrarNotificacion(`Importando ${grupo}...`, 'ok');
    const form = new FormData();
    form.append('archivo', archivo);
    try {
        const res  = await fetch(`${API_BASE}/admin/${grupo}/importar`, { method: 'POST', headers: { 'Authorization': `Bearer ${token}` }, body: form });
        const data = await res.json();
        if (res.ok) {
            const msg = `${data.insertados} nuevo(s)  |  ${data.actualizados} actualizado(s)${data.errores ? '  |  ' + data.errores + ' ignorado(s)' : ''}`;
            if (resultado) { resultado.className = 'maestro-resultado ok'; resultado.innerHTML = msg; }
            mostrarNotificacion('Importación completada', 'ok');
            addLog('activity', `${cfg.singular}: ${data.mensaje}`);
            _grupoEstado[grupo].offset = 0;
            cargarGrupo(grupo);
        } else {
            const err = data.detail || 'Error en importación';
            if (resultado) { resultado.className = 'maestro-resultado error'; resultado.textContent = err; }
            mostrarNotificacion(err, 'error');
        }
    } catch (e) {
        if (resultado) { resultado.className = 'maestro-resultado error'; resultado.textContent = 'Error de conexión'; }
        mostrarNotificacion('Error de conexión', 'error');
    }
}

function exportarGrupo(grupo) {
    const cfg = _GRUPO_CFG[grupo];
    _descargarArchivo(`${API_BASE}/admin/${grupo}/exportar`, `Exportando ${grupo}...`, cfg.archivo);
}

function levantarBan(banId, nombre) {
    mostrarConfirmacion(
        `¿Levantar el ban de <strong>${escapeHtml(nombre)}</strong>? El usuario podrá acceder de nuevo y sus incidencias activas quedarán reseteadas.`,
        async () => {
            try {
                const res  = await fetch(`${API_BASE}/admin/bans/${banId}`, { method: 'DELETE', headers: authHeaders() });
                const body = await res.json();
                if (res.ok) {
                    mostrarNotificacion(body.mensaje, 'ok');
                    addLog('activity', `Ban levantado para: ${nombre}`);
                    cargarBans();
                } else {
                    mostrarNotificacion(body.detail || 'Error', 'error');
                }
            } catch (e) {
                mostrarNotificacion('Error de conexión', 'error');
            }
        },
        { titulo: 'Levantar Ban', textoConfirmar: 'Levantar' }
    );
}

// ── Historial de Bans ─────────────────────────────────────────────────

let _banHistOffset = 0;
const _banHistLimit = 20;
let _banHistTotal  = 0;
let _banVistaActual = 'activos';

function banCambiarVista(vista) {
    _banVistaActual = vista;
    document.getElementById('ban-view-activos').style.display   = vista === 'activos'   ? '' : 'none';
    document.getElementById('ban-view-historial').style.display = vista === 'historial' ? '' : 'none';
    const btnA = document.getElementById('ban-view-activos-btn');
    const btnH = document.getElementById('ban-view-historial-btn');
    if (btnA) {
        btnA.className = vista === 'activos'
            ? 'px-4 py-1.5 text-sm font-semibold rounded-lg border border-rose-300 dark:border-rose-700 bg-rose-500 text-white transition-colors'
            : 'px-4 py-1.5 text-sm font-semibold rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-zinc-600 dark:text-zinc-300 transition-colors';
    }
    if (btnH) {
        btnH.className = vista === 'historial'
            ? 'px-4 py-1.5 text-sm font-semibold rounded-lg border border-zinc-400 dark:border-zinc-500 bg-zinc-700 text-white transition-colors'
            : 'px-4 py-1.5 text-sm font-semibold rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-zinc-600 dark:text-zinc-300 transition-colors';
    }
    if (vista === 'historial') {
        _banHistOffset = 0;
        cargarHistorialBans();
    }
}

async function cargarHistorialBans() {
    const dni    = document.getElementById('ban-hist-dni')?.value.trim()    || '';
    const estado = document.getElementById('ban-hist-estado')?.value         || '';
    const params = new URLSearchParams({ limit: _banHistLimit, offset: _banHistOffset });
    if (dni)    params.set('dni',    dni);
    if (estado) params.set('estado', estado);

    const body = document.getElementById('banHistBody');
    if (body) body.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:16px;color:var(--t3)">Cargando...</td></tr>';

    try {
        const res  = await fetch(`${API_BASE}/admin/bans/historial?${params}`, { headers: authHeaders(), cache: 'no-store' });
        if (!res.ok) return;
        const data = await res.json();
        _banHistTotal = data.total || 0;
        renderHistorialBans(data.items || []);
        _banHistActualizarPag();
    } catch (e) {
        addLog('error', `Error cargando historial de bans: ${e.message}`);
    }
}

function renderHistorialBans(items) {
    const body  = document.getElementById('banHistBody');
    const empty = document.getElementById('sinBanHist');
    if (!body) return;

    if (!items.length) {
        body.innerHTML = '';
        if (empty) empty.style.display = '';
        return;
    }
    if (empty) empty.style.display = 'none';

    const estadoBadge = {
        activo:    `<span style="background:oklch(0.56 0.20 20 / 0.12);border:1px solid oklch(0.56 0.20 20 / 0.4);color:oklch(0.56 0.20 20);border-radius:6px;padding:2px 8px;font-size:11px;font-weight:600;white-space:nowrap">Activo</span>`,
        expirado:  `<span style="background:var(--sur2);border:1px solid var(--bdr);color:var(--t3);border-radius:6px;padding:2px 8px;font-size:11px;white-space:nowrap">Expirado</span>`,
        levantado: `<span style="background:oklch(0.60 0.16 220 / 0.12);border:1px solid oklch(0.60 0.16 220 / 0.4);color:oklch(0.60 0.16 220);border-radius:6px;padding:2px 8px;font-size:11px;font-weight:600;white-space:nowrap">Levantado</span>`,
    };

    body.innerHTML = items.map(b => {
        const fechaIni  = b.fecha_ini       ? new Date(b.fecha_ini).toLocaleDateString('es-PE')       : '—';
        const fechaFin  = b.fecha_fin       ? new Date(b.fecha_fin).toLocaleDateString('es-PE')       : 'Indefinido';
        const fechaLev  = b.fecha_levantado ? new Date(b.fecha_levantado).toLocaleDateString('es-PE') : '';
        const badge     = estadoBadge[b.estado] || estadoBadge.expirado;
        const levInfo   = b.levantado_por
            ? `<span style="font-size:12px;color:var(--t2)">${esc(b.levantado_por)}</span>${fechaLev ? `<div style="font-size:11px;color:var(--t3)">${fechaLev}</div>` : ''}`
            : `<span style="color:var(--t3);font-size:12px">${b.estado === 'expirado' ? 'Sistema' : '—'}</span>`;
        return `<tr>
            <td><code>${esc(b.dni)}</code></td>
            <td style="font-size:13px">${esc(b.nombre)}</td>
            <td style="font-size:12px;color:var(--t2);max-width:180px">${esc(b.motivo)}</td>
            <td style="font-size:12px;color:var(--t2)">${esc(b.baneado_por || '—')}</td>
            <td style="font-size:12px"><span class="cell-time">${fechaIni}</span></td>
            <td style="font-size:12px"><span class="cell-time">${fechaFin}</span></td>
            <td>${badge}</td>
            <td>${levInfo}</td>
        </tr>`;
    }).join('');
}

function _banHistActualizarPag() {
    const info = document.getElementById('ban-hist-pag-info');
    const prev = document.getElementById('ban-hist-prev');
    const next = document.getElementById('ban-hist-next');
    const desde = _banHistOffset + 1;
    const hasta = Math.min(_banHistOffset + _banHistLimit, _banHistTotal);
    if (info) info.textContent = _banHistTotal ? `${desde}–${hasta} de ${_banHistTotal}` : '';
    if (prev) prev.disabled = _banHistOffset === 0;
    if (next) next.disabled = _banHistOffset + _banHistLimit >= _banHistTotal;
}

function banHistCambiarPag(dir) {
    _banHistOffset = Math.max(0, _banHistOffset + dir * _banHistLimit);
    cargarHistorialBans();
}

// ── Auditoría (solo superadmin) ──────────────────────────────────────
let _audOffset = 0;
const _audLimit = 50;
let _audTotal  = 0;

// Etiquetas legibles para cada acción registrada
const _AUD_ACCION_LABEL = {
    bloquear_terminal:          'Bloquear terminal',
    desbloquear_terminal:       'Desbloquear terminal',
    apagar_terminal:            'Apagar terminal',
    bloquear_todas:             'Bloquear toda la sala',
    banear_alumno:              'Banear alumno',
    levantar_ban:               'Levantar ban',
    crear_alumno:               'Crear estudiante',
    editar_alumno:              'Editar estudiante',
    importar_maestro:           'Importar padrón estudiantes',
    crear_personal:             'Crear personal',
    editar_personal:            'Editar personal',
    eliminar_personal:          'Eliminar personal',
    importar_personal:          'Importar personal',
    crear_incidencia:           'Crear incidencia',
    eliminar_incidencia:        'Eliminar incidencia',
    aprobar_sospecha:           'Aprobar sospecha',
    descartar_sospecha:         'Descartar sospecha',
    agregar_proceso_ignorado:   'Agregar proceso ignorado',
    quitar_proceso_ignorado:    'Quitar proceso ignorado',
    eliminar_terminal:          'Eliminar terminal',
    crear_mensaje:              'Crear mensaje',
    editar_mensaje:             'Editar mensaje',
    toggle_mensaje:             'Activar/desactivar mensaje',
    eliminar_mensaje:           'Eliminar mensaje',
    editar_usuario:             'Editar usuario (admin)',
    crear_usuario:              'Crear usuario (admin)',
    cambiar_config_backdoor:    'Config backdoor',
    cambiar_config_offline:     'Config offline',
    cambiar_ruta_distribucion:  'Ruta distribución',
    cerrar_todas_sesiones:      'Cerrar todas las sesiones',
    limpiar_historial_sesiones: 'Limpiar historial',
    limpiar_todo:               'Limpiar todo',
    reset_maestro:              'Reset maestro',
    reset_total:                'Reset total',
    eliminar_alumno:            'Eliminar alumno',
    backup_sql:                 'Backup SQL',
};

async function cargarAuditoria() {
    const usuario = document.getElementById('aud-usuario')?.value.trim() || '';
    const accion  = document.getElementById('aud-accion')?.value          || '';
    const desde   = document.getElementById('aud-desde')?.value           || '';
    const hasta   = document.getElementById('aud-hasta')?.value           || '';
    const params  = new URLSearchParams({ limit: _audLimit, offset: _audOffset });
    if (usuario) params.set('usuario', usuario);
    if (accion)  params.set('accion',  accion);
    if (desde)   params.set('desde',   desde);
    if (hasta)   params.set('hasta',   hasta);

    const body = document.getElementById('audBody');
    if (body) body.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:16px;color:var(--t3)">Cargando...</td></tr>';

    try {
        const res = await fetch(`${API_BASE}/admin/auditoria?${params}`, { headers: authHeaders(), cache: 'no-store' });
        if (!res.ok) return;
        const data = await res.json();
        _audTotal = data.total || 0;
        renderAuditoria(data.items || []);
        _audActualizarPag();
    } catch (e) {
        addLog('error', `Error cargando auditoría: ${e.message}`);
    }
}

function renderAuditoria(items) {
    const body  = document.getElementById('audBody');
    const empty = document.getElementById('sinAud');
    if (!body) return;

    if (!items.length) {
        body.innerHTML = '';
        if (empty) empty.style.display = '';
        return;
    }
    if (empty) empty.style.display = 'none';

    body.innerHTML = items.map(a => {
        const fecha     = a.fecha_hora ? new Date(a.fecha_hora).toLocaleString('es-PE') : '—';
        const accionLbl = _AUD_ACCION_LABEL[a.accion] || a.accion;
        return `<tr>
            <td class="px-3 py-3 text-xs whitespace-nowrap"><span class="cell-time">${esc(fecha)}</span></td>
            <td class="px-3 py-3 text-xs font-medium whitespace-nowrap">${esc(a.usuario)}</td>
            <td class="px-3 py-3 text-xs whitespace-nowrap">${esc(a.rol)}</td>
            <td class="px-3 py-3 text-xs whitespace-nowrap">${esc(accionLbl)}</td>
            <td class="px-3 py-3 text-xs text-zinc-500 dark:text-zinc-400">${esc(a.objetivo)}</td>
            <td class="px-3 py-3 text-xs text-zinc-500 dark:text-zinc-400">${esc(a.detalle)}</td>
        </tr>`;
    }).join('');
}

function _audActualizarPag() {
    const info = document.getElementById('aud-pag-info');
    const prev = document.getElementById('aud-prev');
    const next = document.getElementById('aud-next');
    const desde = _audOffset + 1;
    const hasta = Math.min(_audOffset + _audLimit, _audTotal);
    if (info) info.textContent = _audTotal ? `${desde}–${hasta} de ${_audTotal}` : '';
    if (prev) prev.disabled = _audOffset === 0;
    if (next) next.disabled = _audOffset + _audLimit >= _audTotal;
}

function audCambiarPag(dir) {
    _audOffset = Math.max(0, _audOffset + dir * _audLimit);
    cargarAuditoria();
}

// ── Incidencias ──────────────────────────────────────────────────────

let _incidenciasCache = [];

async function cargarIncidencias() {
    const soloActivas = document.getElementById('incidenciasSoloActivas')?.checked ?? true;
    const params = new URLSearchParams({ limit: 200, offset: 0 });
    if (soloActivas) params.set('solo_activas', 'true');
    try {
        const res  = await fetch(`${API_BASE}/admin/incidencias?${params}`, { headers: authHeaders() });
        const data = await res.json();
        if (!res.ok) return;
        _incidenciasCache = data.items || [];
        const totalEl = document.getElementById('incidenciasTotal');
        if (totalEl) totalEl.textContent = `${data.total} registro(s)`;
        _renderIncidencias(_incidenciasCache);
        await _cargarResumenIncidencias();
    } catch (e) {
        // silencioso
    }
}

// ── Badges de navegación como notificaciones (marcar como visto) ──────
// El badge del ícono cuenta cosas de estado (incidencias/sospechas) que no
// "se leen" solas. Guardamos en localStorage cuántas había la última vez que
// el operador abrió esa pestaña; el badge del ícono solo muestra número si el
// total ACTUAL supera lo visto (hay novedades). Al abrir la pestaña se marca
// visto y el badge desaparece hasta que lleguen nuevas.
function _notifVistas(clave) {
    try { return parseInt(localStorage.getItem('notif_visto_' + clave) || '0', 10) || 0; }
    catch (e) { return 0; }
}
function _marcarNotifsVistas(clave, total) {
    try { localStorage.setItem('notif_visto_' + clave, String(total)); } catch (e) {}
    // Ocultar de inmediato el badge del ícono correspondiente.
    const map = { incidencias: 'incidencias-badge', actividad: 'actividad-badge' };
    const b = document.getElementById(map[clave]);
    if (b) b.style.display = 'none';
    // Recordar el último total conocido para poder re-marcar al abrir.
    _notifTotales[clave] = total;
}
// Último total conocido de cada badge (lo llenan los renderizadores).
const _notifTotales = { incidencias: 0, actividad: 0 };

// Llamada desde switchTab al abrir una pestaña con badge: marca visto su total.
function marcarPestanaVista(tab) {
    if (tab === 'incidencias') _marcarNotifsVistas('incidencias', _notifTotales.incidencias || 0);
    if (tab === 'actividad')   _marcarNotifsVistas('actividad',   _notifTotales.actividad   || 0);
}

async function _cargarResumenIncidencias() {
    try {
        const res  = await fetch(`${API_BASE}/admin/incidencias/resumen`, { headers: authHeaders() });
        const data = await res.json();
        if (!res.ok) return;
        const alertas = data.filter(r => r.total >= 3);
        const alertasDiv  = document.getElementById('incidenciasAlertas');
        const alertasList = document.getElementById('incidenciasAlertasList');
        const badge       = document.getElementById('incidencias-badge');
        if (alertasDiv && alertasList) {
            if (alertas.length > 0) {
                alertasDiv.style.display = '';
                alertasList.innerHTML = alertas.map(r =>
                    `<span class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-amber-100 dark:bg-amber-900/40 border border-amber-300 dark:border-amber-700/60 text-xs font-semibold text-amber-800 dark:text-amber-300 cursor-pointer hover:bg-amber-200 dark:hover:bg-amber-800/50 transition-colors"
                        onclick="abrirBanDesdeIncidencia('${escapeHtml(r.dni)}','${escapeHtml(r.nombre)}')">
                        <i class="ph ph-warning-circle"></i> ${escapeHtml(r.nombre)} — ${r.total} inc.
                    </span>`
                ).join('');
            } else {
                alertasDiv.style.display = 'none';
            }
        }
        const total = data.reduce((s, r) => s + r.total, 0);
        _notifTotales.incidencias = total;
        // Si el operador está viendo la pestaña Incidencias, se considera visto al
        // instante (no debe quedar un badge de algo que tiene delante).
        const viendoInc = document.getElementById('tab-incidencias')?.style.display !== 'none'
                          && document.getElementById('tab-incidencias');
        if (viendoInc) { try { localStorage.setItem('notif_visto_incidencias', String(total)); } catch(e) {} }
        // Badge del ÍCONO (navegación): notificación → solo si hay NOVEDAD.
        const nuevas = total - _notifVistas('incidencias');
        if (badge) {
            if (nuevas > 0) { badge.textContent = nuevas > 99 ? '99+' : nuevas; badge.style.display = ''; }
            else badge.style.display = 'none';
        }
        // Badge del SUB-panel (dentro de la pestaña): siempre el total real.
        const badgeSub = document.getElementById('incidencias-badge-sub');
        if (badgeSub) {
            if (total > 0) { badgeSub.textContent = total; badgeSub.style.display = ''; }
            else badgeSub.style.display = 'none';
        }

        // Poblar mini-panel de alertas en el hero del tab Monitoreo
        _renderMonAlertasMini(data);
    } catch (e) {
        // silencioso
    }
}

function _renderMonAlertasMini(data) {
    const activas = data.filter(r => r.total > 0);

    // Tarjeta KPI "Incidencias Pendientes" (Monitoreo, layout dashboard):
    // numero = alumnos con incidencias activas; tag "Critico" si hay alguno grave (>=3).
    const numEl = document.getElementById('monIncidenciasNum');
    if (numEl) numEl.textContent = String(activas.length).padStart(2, '0');
    const tagEl = document.getElementById('monIncidenciasTag');
    if (tagEl) tagEl.style.display = activas.some(r => r.total >= 3) ? '' : 'none';

    const el = document.getElementById('monAlertasMini');
    if (!el) return;
    const lista = activas.slice(0, 4);
    if (!lista.length) {
        el.innerHTML = '<p class="mon-alerts-empty">Sin incidencias activas</p>';
        return;
    }
    el.innerHTML = lista.map(r => {
        const grave = r.total >= 3;
        return `<div class="mon-alert-item">
            <div class="mon-alert-ico ${grave ? 'grave' : 'leve'}">
                <i class="ph ph-${grave ? 'warning' : 'warning-circle'}"></i>
            </div>
            <div style="min-width:0">
                <div class="mon-alert-name">${escapeHtml(r.nombre)}</div>
                <div class="mon-alert-desc">${r.total} incidencia${r.total !== 1 ? 's' : ''}</div>
            </div>
        </div>`;
    }).join('');
}

async function _cargarEventosMini() {
    try {
        // Actividad en vivo: los 11 eventos mas recientes (la lista tiene scroll).
        const res = await fetch(`${API_BASE}/admin/actividad?limit=11&offset=0`, { headers: authHeaders() });
        if (!res.ok) return;
        const data = await res.json();
        const items = data.items || [];
        const el = document.getElementById('monEventosMini');
        const cnt = document.getElementById('monLiveCount');
        if (cnt) cnt.textContent = data.total != null ? data.total : items.length;
        if (!el) return;
        if (!items.length) {
            el.innerHTML = '<p class="mon-alerts-empty">Sin eventos recientes</p>';
            return;
        }
        const filas = items.map(r => {
            const hora = r.fecha_hora ? new Date(r.fecha_hora).toLocaleTimeString('es-PE', {hour:'2-digit', minute:'2-digit'}) : '—';
            const isCrit = r.nivel === 'sospechoso';
            const quien = (r.nombre_alumno || '').trim();
            const term  = (r.nombre_terminal || '').trim();
            // La descripcion ya viene legible del servidor ("Abrio: ...", "Herramienta
            // del sistema: ..."); el exe crudo solo se muestra si no esta ya en ella.
            const desc  = (r.descripcion || r.tipo || '—').trim();
            const exe   = (r.proceso_exe || '').trim();
            const mostrarExe = exe && !desc.toLowerCase().includes(exe.toLowerCase());
            return `<div class="mon-event-item${isCrit ? ' critical' : ''}">
                <div class="mon-event-head">
                    ${quien ? `<span class="mon-event-quien">${escapeHtml(quien)}</span>` : '<span class="mon-event-quien mon-event-quien-anon">Sistema</span>'}
                    ${isCrit ? '<span class="mon-event-flag">Sospechoso</span>' : ''}
                </div>
                <div class="mon-event-desc">${escapeHtml(desc)}${mostrarExe ? ` <code class="mon-event-exe">${escapeHtml(exe)}</code>` : ''}</div>
                <div class="mon-event-meta">
                    <span class="mon-event-time">${hora}</span>
                    ${term ? `<span class="mon-event-term"><i class="ph ph-desktop"></i> ${escapeHtml(term)}</span>` : ''}
                </div>
            </div>`;
        }).join('');
        // Fila final: "Ver más de hoy" -> abre Eventos filtrado al día de hoy.
        const verMas = `<button class="mon-event-vermas" onclick="actVerHoy()">
            <i class="ph ph-clock-counter-clockwise"></i> Ver toda la actividad de hoy
        </button>`;
        el.innerHTML = filas + verMas;
    } catch(e) { /* silencioso */ }
}

// Abrir/cerrar el drawer lateral "Actividad en vivo" (se superpone).
function toggleActividadVivo() {
    const drawer = document.getElementById('monLiveSide');
    if (!drawer) return;
    const abierto = drawer.classList.toggle('open');
    document.body.classList.toggle('mon-live-open', abierto);
    drawer.setAttribute('aria-hidden', abierto ? 'false' : 'true');
}

function _renderIncidencias(items) {
    const tbody = document.getElementById('incidenciasBody');
    const empty = document.getElementById('sinIncidencias');
    if (!tbody) return;
    const buscar = (document.getElementById('incidenciasBuscar')?.value || '').toLowerCase();
    const tipo   = document.getElementById('incidenciasFiltroTipo')?.value || '';
    let filtrado = items;
    if (buscar) filtrado = filtrado.filter(i => i.nombre_alumno.toLowerCase().includes(buscar) || i.dni.includes(buscar));
    if (tipo)   filtrado = filtrado.filter(i => i.tipo === tipo);
    if (filtrado.length === 0) {
        tbody.innerHTML = '';
        if (empty) empty.style.display = '';
        return;
    }
    if (empty) empty.style.display = 'none';
    tbody.innerHTML = filtrado.map(i => {
        const tipoCls  = i.tipo === 'grave' ? 'color:oklch(0.56 0.20 20)' : 'color:oklch(0.65 0.17 70)';
        const tipoBg   = i.tipo === 'grave'
            ? 'background:oklch(0.56 0.20 20 / 0.12);border:1px solid oklch(0.56 0.20 20 / 0.35)'
            : 'background:oklch(0.80 0.17 80 / 0.15);border:1px solid oklch(0.80 0.17 80 / 0.4)';
        const estadoBadge = i.activa
            ? '<span style="background:oklch(0.60 0.16 220 / 0.15);border:1px solid oklch(0.60 0.16 220 / 0.4);color:oklch(0.60 0.16 220);border-radius:6px;padding:2px 8px;font-size:11px;font-weight:600">Activa</span>'
            : '<span style="background:var(--sur2);border:1px solid var(--bdr);color:var(--t3);border-radius:6px;padding:2px 8px;font-size:11px">Reseteada</span>';
        const fecha = i.fecha ? new Date(i.fecha).toLocaleDateString('es-PE', { day:'2-digit', month:'2-digit', year:'numeric' }) : '—';
        return `<tr>
            <td class="px-3 py-3 text-xs font-mono whitespace-nowrap">${escapeHtml(i.dni)}</td>
            <td class="px-3 py-3 text-xs font-medium whitespace-nowrap">${escapeHtml(i.nombre_alumno)}</td>
            <td class="px-3 py-3"><span style="${tipoBg};${tipoCls};border-radius:6px;padding:2px 10px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em">${escapeHtml(i.tipo)}</span></td>
            <td class="px-3 py-3 text-xs whitespace-nowrap">${escapeHtml(i.motivo)}</td>
            <td class="px-3 py-3 text-xs text-slate-500 dark:text-slate-400 truncate" style="max-width:180px" title="${escapeHtml(i.descripcion || '')}">${escapeHtml(i.descripcion || '—')}</td>
            <td class="px-3 py-3 text-xs whitespace-nowrap">${fecha}</td>
            <td class="px-3 py-3 text-xs whitespace-nowrap">${escapeHtml(i.registrado_por)}</td>
            <td class="px-3 py-3">${estadoBadge}</td>
            <td class="px-3 py-3">
                <div class="flex items-center gap-2">
                    ${i.activa && i.tipo === 'grave' ? `<button onclick="abrirBanearUsuario('${escapeHtml(i.dni)}','${escapeHtml(i.nombre_alumno)}')" class="text-amber-500 hover:text-amber-700 dark:text-amber-400 dark:hover:text-amber-200 transition-colors" title="Banear alumno"><i class="ph ph-prohibit text-base"></i></button>` : ''}
                    ${i.activa ? `<button onclick="eliminarIncidencia(${i.id},'${escapeHtml(i.nombre_alumno)}')" class="text-rose-400 hover:text-rose-600 dark:text-rose-500 dark:hover:text-rose-300 transition-colors" title="Eliminar incidencia"><i class="ph ph-trash text-base"></i></button>` : ''}
                </div>
            </td>
        </tr>`;
    }).join('');
}

function filtrarIncidencias() {
    _renderIncidencias(_incidenciasCache);
    const soloActivas = document.getElementById('incidenciasSoloActivas')?.checked ?? true;
    if (soloActivas !== _incidenciasCache._soloActivas) cargarIncidencias();
}

let _incDni = null;

async function abrirNuevaIncidencia(dni = null) {
    _incDni = dni;
    const modal = document.getElementById('modal-incidencia');
    if (!modal) return;
    document.getElementById('inc-dni').value = dni || '';
    document.getElementById('inc-dni').readOnly = !!dni;
    document.getElementById('inc-tipo').value = 'leve';
    document.getElementById('inc-motivo').value = '';
    document.getElementById('inc-descripcion').value = '';
    document.getElementById('modal-incidencia-error').textContent = '';
    document.getElementById('inc-advertencia-ban').style.display = 'none';
    const nombreEl = document.getElementById('modal-incidencia-nombre');
    if (dni) {
        // Buscar nombre en cache de maestro si está disponible
        const alumno = (window._maestroCache || []).find(a => a.dni === dni);
        if (alumno && nombreEl) nombreEl.textContent = `DNI ${dni} — ${alumno.nombre}`;
        await _actualizarContadorIncidencia(dni);
    } else {
        if (nombreEl) nombreEl.textContent = '';
    }
    modal.style.display = '';
    document.getElementById('btn-inc-cancelar').onclick = () => { modal.style.display = 'none'; };
    document.getElementById('btn-inc-guardar').onclick = guardarIncidencia;
    // Actualizar contador si cambia el DNI manualmente
    document.getElementById('inc-dni').oninput = async function() {
        if (this.value.length === 8) await _actualizarContadorIncidencia(this.value);
        else document.getElementById('inc-advertencia-ban').style.display = 'none';
    };
}

async function _actualizarContadorIncidencia(dni) {
    try {
        const res  = await fetch(`${API_BASE}/admin/incidencias?dni=${dni}&solo_activas=true&limit=10`, { headers: authHeaders() });
        const data = await res.json();
        if (res.ok && data.total >= 2) {
            document.getElementById('inc-contador').textContent = `${data.total}`;
            document.getElementById('inc-advertencia-ban').style.display = '';
        } else {
            document.getElementById('inc-advertencia-ban').style.display = 'none';
        }
    } catch (e) { /* silencioso */ }
}

async function guardarIncidencia() {
    const errEl = document.getElementById('modal-incidencia-error');
    errEl.textContent = '';
    const dni         = document.getElementById('inc-dni').value.trim();
    const tipo        = document.getElementById('inc-tipo').value;
    const motivo      = document.getElementById('inc-motivo').value.trim();
    const descripcion = document.getElementById('inc-descripcion').value.trim();
    if (!dni || dni.length !== 8 || !/^\d+$/.test(dni)) { errEl.textContent = 'DNI debe tener 8 dígitos numéricos'; return; }
    if (!motivo) { errEl.textContent = 'El motivo es obligatorio'; return; }
    const btn = document.getElementById('btn-inc-guardar');
    btn.disabled = true;
    btn.textContent = 'Guardando...';
    try {
        const res  = await fetch(`${API_BASE}/admin/incidencias`, {
            method: 'POST',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ dni, tipo, motivo, descripcion: descripcion || null }),
        });
        const data = await res.json();
        if (res.ok) {
            document.getElementById('modal-incidencia').style.display = 'none';
            mostrarNotificacion(data.mensaje, 'ok');
            addLog('activity', `Incidencia ${tipo} registrada para DNI ${dni}: ${motivo}`);
            cargarIncidencias();
            if (data.total_activas >= 3) {
                mostrarNotificacion(`Atención: ${data.total_activas} incidencias activas para este alumno`, 'warning');
            }
        } else {
            errEl.textContent = data.detail || 'Error al registrar';
        }
    } catch (e) {
        errEl.textContent = 'Error de conexión';
    } finally {
        btn.disabled = false;
        btn.textContent = 'Registrar';
    }
}

function eliminarIncidencia(id, nombre) {
    mostrarConfirmacion(
        `¿Eliminar esta incidencia de <strong>${escapeHtml(nombre)}</strong>? La incidencia quedará marcada como reseteada.`,
        async () => {
            try {
                const res  = await fetch(`${API_BASE}/admin/incidencias/${id}`, { method: 'DELETE', headers: authHeaders() });
                const data = await res.json();
                if (res.ok) {
                    mostrarNotificacion(data.mensaje, 'ok');
                    cargarIncidencias();
                } else {
                    mostrarNotificacion(data.detail || 'Error', 'error');
                }
            } catch (e) {
                mostrarNotificacion('Error de conexión', 'error');
            }
        },
        { titulo: 'Eliminar Incidencia', textoConfirmar: 'Eliminar', critico: true }
    );
}

function abrirBanDesdeIncidencia(dni, nombre) {
    switchTab('incidencias');
    setTimeout(() => { switchSubtabInc('bans'); abrirBanearUsuario(dni, nombre); }, 300);
}

// ── Configuración — gestión de usuarios ──────────────────────────

async function guardarUsuario(rol) {
    const esSuperadmin = rol === 'superadmin';
    const username = esSuperadmin ? '' : (document.getElementById('cfg-admin-username')?.value.trim() || '');
    const password = document.getElementById(esSuperadmin ? 'cfg-superadmin-nueva' : 'cfg-admin-nueva')?.value || '';
    const errEl    = document.getElementById(esSuperadmin ? 'cfg-superadmin-error' : 'cfg-admin-error');

    errEl.textContent = '';

    if (!username && !password) {
        errEl.textContent = 'Ingresa al menos un campo para actualizar';
        return;
    }
    if (username && username.length < 3) {
        errEl.textContent = 'El username debe tener al menos 3 caracteres';
        return;
    }
    // BUG-3: la validación debe coincidir con la del servidor
    // (_validar_complejidad_password: 8+ caracteres, 1 mayúscula y 1 número).
    if (password && (password.length < 8 || !/[A-Z]/.test(password) || !/[0-9]/.test(password))) {
        errEl.textContent = 'La contraseña debe tener al menos 8 caracteres, una mayúscula y un número';
        return;
    }

    try {
        const res  = await fetch(`${API_BASE}/config/usuario`, {
            method: 'PUT',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ rol_objetivo: rol, nuevo_username: username, nueva_password: password }),
        });
        const data = await res.json();
        if (res.ok) {
            mostrarNotificacion(data.mensaje, 'ok');
            if (!esSuperadmin) document.getElementById('cfg-admin-username').value = '';
            document.getElementById(esSuperadmin ? 'cfg-superadmin-nueva' : 'cfg-admin-nueva').value = '';
        } else {
            errEl.textContent = data.detail || 'Error al guardar';
        }
    } catch (e) {
        errEl.textContent = 'Error de conexión';
    }
}

// ── Configuración de Aplicación (logo + textos login) ─────────────────────────

const _CFG_LOGO_KEY    = 'cfg_app_logo';
const _CFG_TITULO_KEY  = 'cfg_app_titulo';
const _CFG_SUBTITULO_KEY = 'cfg_app_subtitulo';
const _CFG_FOOTER_KEY  = 'cfg_app_footer';

// Textos por defecto del login — se usan si el campo se deja vacío
const _CFG_TITULO_DEFAULT    = 'Control Biblioteca';
const _CFG_SUBTITULO_DEFAULT = 'UNASAM — PANEL DE ADMINISTRACIÓN';
const _CFG_FOOTER_DEFAULT    = '© 2026 UNASAM — Dirección de Biblioteca Central';

// Carga la apariencia desde el SERVIDOR (config global, la misma para todas
// las PCs) y la cachea en localStorage como respaldo para cuando no haya red.
// Es pública: funciona aún en la pantalla de login (sin sesión).
async function _cargarAparienciaServidor() {
    try {
        const res = await fetch(`${API_BASE}/config/apariencia`, { cache: 'no-store' });
        if (!res.ok) return false;
        const d = await res.json();
        // Guardar en localStorage solo lo que el servidor tenga definido;
        // así _aplicarConfigApp (que lee de localStorage) refleja lo global.
        if (d.logo)      localStorage.setItem(_CFG_LOGO_KEY, d.logo);      else localStorage.removeItem(_CFG_LOGO_KEY);
        if (d.titulo)    localStorage.setItem(_CFG_TITULO_KEY, d.titulo);  else localStorage.removeItem(_CFG_TITULO_KEY);
        if (d.subtitulo) localStorage.setItem(_CFG_SUBTITULO_KEY, d.subtitulo); else localStorage.removeItem(_CFG_SUBTITULO_KEY);
        if (d.footer)    localStorage.setItem(_CFG_FOOTER_KEY, d.footer);  else localStorage.removeItem(_CFG_FOOTER_KEY);
        _aplicarConfigApp();
        _cargarCamposConfigApp();
        return true;
    } catch (e) {
        // Sin red: se queda con lo cacheado en localStorage (degrada con dignidad).
        return false;
    }
}

function _aplicarConfigApp() {
    const logo      = localStorage.getItem(_CFG_LOGO_KEY);
    const titulo    = localStorage.getItem(_CFG_TITULO_KEY);
    const subtitulo = localStorage.getItem(_CFG_SUBTITULO_KEY);
    const footer    = localStorage.getItem(_CFG_FOOTER_KEY);

    // Logo sidebar — reemplaza la "U" por la imagen
    const badgeEl = document.getElementById('sidebar-logo-badge');
    if (logo && badgeEl) {
        badgeEl.innerHTML = `<img src="${logo}" class="w-full h-full object-contain rounded-lg" alt="Logo">`;
        badgeEl.style.background = 'transparent';
        badgeEl.style.padding = '2px';
    } else if (!logo && badgeEl) {
        badgeEl.innerHTML = 'U';
        badgeEl.style.background = '';
        badgeEl.style.padding = '';
    }

    // Favicon
    const faviconEl = document.getElementById('app-favicon');
    if (logo && faviconEl) faviconEl.href = logo;

    // Logo login
    const loginIconWrap = document.querySelector('.login-icon-wrap');
    if (logo && loginIconWrap) {
        loginIconWrap.innerHTML = `<img src="${logo}" class="w-12 h-12 rounded-xl object-contain" alt="Logo">`;
    }

    // Textos login — usa el guardado o el default, nunca queda en blanco
    const loginTitle = document.querySelector('.login-title');
    const loginSub   = document.querySelector('.login-subtitle');
    const loginFoot  = document.querySelector('.login-footer p');
    if (loginTitle) loginTitle.textContent = titulo    || _CFG_TITULO_DEFAULT;
    if (loginSub)   loginSub.textContent   = subtitulo || _CFG_SUBTITULO_DEFAULT;
    if (loginFoot)  loginFoot.textContent  = footer    || _CFG_FOOTER_DEFAULT;
}

function _cargarCamposConfigApp() {
    const t = localStorage.getItem(_CFG_TITULO_KEY);
    const s = localStorage.getItem(_CFG_SUBTITULO_KEY);
    const f = localStorage.getItem(_CFG_FOOTER_KEY);
    const l = localStorage.getItem(_CFG_LOGO_KEY);
    if (t) document.getElementById('cfg-login-titulo').value    = t;
    if (s) document.getElementById('cfg-login-subtitulo').value = s;
    if (f) document.getElementById('cfg-login-footer').value    = f;
    if (l) {
        const preview = document.getElementById('cfg-logo-preview');
        if (preview) preview.innerHTML = `<img src="${l}" class="w-full h-full object-contain p-1" alt="Logo">`;
        const nombre = document.getElementById('cfg-logo-nombre');
        if (nombre) nombre.textContent = 'Imagen personalizada';
    }
}

let _logoDataUrl = null;

function previsualizarLogo(input) {
    const file   = input.files[0];
    const errEl  = document.getElementById('cfg-logo-error');
    const btnEl  = document.getElementById('btn-guardar-logo');
    errEl.textContent = '';
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) { errEl.textContent = 'El archivo no debe superar 2 MB'; return; }
    const reader = new FileReader();
    reader.onload = e => {
        _logoDataUrl = e.target.result;
        const preview = document.getElementById('cfg-logo-preview');
        if (preview) preview.innerHTML = `<img src="${_logoDataUrl}" class="w-full h-full object-contain p-1" alt="Logo">`;
        const nombre = document.getElementById('cfg-logo-nombre');
        if (nombre) nombre.textContent = file.name;
        if (btnEl) btnEl.style.display = '';
    };
    reader.readAsDataURL(file);
}

async function guardarLogo() {
    if (!_logoDataUrl) return;
    const btnEl = document.getElementById('btn-guardar-logo');
    const errEl = document.getElementById('cfg-logo-error');
    if (errEl) errEl.textContent = '';
    if (btnEl) btnEl.disabled = true;
    try {
        const res = await fetch(`${API_BASE}/config/apariencia`, {
            method: 'PUT', headers: authHeaders(),
            body: JSON.stringify({ logo: _logoDataUrl })
        });
        const body = await res.json();
        if (!res.ok) {
            if (errEl) errEl.textContent = body.detail || 'Error al guardar el logo.';
            return;
        }
        // El servidor guardó el archivo y nos devuelve su RUTA. Releemos del
        // servidor para obtenerla y cachearla (no guardamos el base64).
        await _cargarAparienciaServidor();
        mostrarNotificacion('Logo aplicado para todo el panel', 'ok');
        if (btnEl) btnEl.style.display = 'none';
        _logoDataUrl = null;
    } catch (e) {
        if (errEl) errEl.textContent = 'Error de conexión.';
    } finally {
        if (btnEl) btnEl.disabled = false;
    }
}

function guardarTextosApp() {
    const errEl = document.getElementById('cfg-app-error');
    errEl.textContent = '';

    // Si un campo se deja vacío, vuelve al texto por defecto (nunca queda en blanco)
    const titulo    = document.getElementById('cfg-login-titulo').value.trim()    || _CFG_TITULO_DEFAULT;
    const subtitulo = document.getElementById('cfg-login-subtitulo').value.trim() || _CFG_SUBTITULO_DEFAULT;
    const footer    = document.getElementById('cfg-login-footer').value.trim()    || _CFG_FOOTER_DEFAULT;

    // Reflejar los defaults aplicados en los inputs
    document.getElementById('cfg-login-titulo').value    = titulo;
    document.getElementById('cfg-login-subtitulo').value = subtitulo;
    document.getElementById('cfg-login-footer').value    = footer;

    // Guardar en el servidor (config global para todas las PCs)
    (async () => {
        try {
            const res = await fetch(`${API_BASE}/config/apariencia`, {
                method: 'PUT', headers: authHeaders(),
                body: JSON.stringify({ titulo, subtitulo, footer })
            });
            const body = await res.json();
            if (!res.ok) { errEl.textContent = body.detail || 'Error al guardar.'; return; }
            // Cachear local para respaldo offline y aplicar
            localStorage.setItem(_CFG_TITULO_KEY, titulo);
            localStorage.setItem(_CFG_SUBTITULO_KEY, subtitulo);
            localStorage.setItem(_CFG_FOOTER_KEY, footer);
            _aplicarConfigApp();
            mostrarNotificacion('Textos actualizados para todo el panel', 'ok');
        } catch (e) {
            errEl.textContent = 'Error de conexión.';
        }
    })();
}

async function descargarBackup() {
    const btn    = document.getElementById('btn-backup');
    const status = document.getElementById('backup-status');
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="ph ph-spinner ph-spin"></i> Generando...'; }
    if (status) status.textContent = 'Preparando volcado SQL, un momento...';
    try {
        const res = await fetch(`${API_BASE}/admin/backup-sql`, { headers: authHeaders() });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        const blob = await res.blob();
        const cd   = res.headers.get('Content-Disposition') || '';
        const name = cd.match(/filename="([^"]+)"/)?.[1] || 'backup.sql';
        const url  = URL.createObjectURL(blob);
        const a    = document.createElement('a');
        a.href = url; a.download = name; a.click();
        URL.revokeObjectURL(url);
        if (status) status.textContent = `Descargado: ${name}`;
        mostrarNotificacion('Backup descargado correctamente', 'ok');
    } catch (e) {
        if (status) status.textContent = `Error: ${e.message}`;
        mostrarNotificacion(`Error al generar backup: ${e.message}`, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = '<i class="ph ph-download-simple"></i> Descargar backup .sql'; }
    }
}

async function abrirIncidenciasDni(dni, nombre) {
    switchTab('incidencias');
    await cargarIncidencias();
    const buscarEl = document.getElementById('incidenciasBuscar');
    if (buscarEl) { buscarEl.value = dni; filtrarIncidencias(); }
}

function _setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

// ═══════════════════════════════════════════════════════════════════
// SOSPECHAS
// ═══════════════════════════════════════════════════════════════════

const _SOSPECHA_TIPO_LABEL = {
    cambio_pc_rapido:    'Cambio rápido de PC',
    dni_baneado_intento: 'Intento con DNI baneado',
    sesion_larga:        'Sesión excesivamente larga',
};

async function cargarSospechas() {
    const estado = document.getElementById('sospechasFiltroEstado')?.value ?? 'pendiente';
    const fecha  = document.getElementById('sospechasFiltroFecha')?.value  || '';
    const params = new URLSearchParams({ limit: 100, offset: 0 });
    if (estado) params.set('estado', estado);
    try {
        const res  = await fetch(`${API_BASE}/admin/sospechas?${params}`, { headers: authHeaders() });
        if (!res.ok) return;
        const data = await res.json();
        let items  = data.items || [];

        // Filtrar por fecha en el frontend
        if (fecha) {
            items = items.filter(s => {
                if (!s.fecha) return false;
                return new Date(s.fecha).toLocaleDateString('en-CA') === fecha;
            });
        }

        _setText('sospechasTotal', `${items.length} registro(s)`);
        _renderSospechas(items);
        _actualizarBadgeSospechas(data.total, estado);
    } catch(e) { /* silencioso */ }
}

function sospechasLimpiarFecha() {
    const el = document.getElementById('sospechasFiltroFecha');
    if (el) el.value = '';
    cargarSospechas();
}

function _actualizarBadgeSospechas(total, estado) {
    const badge = document.getElementById('sospechas-badge');
    if (!badge) return;
    if (estado === 'pendiente' && total > 0) {
        badge.textContent = total > 99 ? '99+' : total;
        badge.style.display = '';
    } else {
        badge.style.display = 'none';
    }
}

function _renderSospechas(items) {
    const tbody   = document.getElementById('sospechasBody');
    const sinEl   = document.getElementById('sinSospechas');
    if (!tbody) return;
    if (!items.length) {
        tbody.innerHTML = '';
        if (sinEl) sinEl.style.display = '';
        return;
    }
    if (sinEl) sinEl.style.display = 'none';

    const ESTADO_PILL = {
        pendiente:  'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300',
        aprobada:   'bg-rose-100 dark:bg-rose-900/40 text-rose-700 dark:text-rose-300',
        descartada: 'bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400',
    };

    tbody.innerHTML = items.map(s => {
        const pillCls  = ESTADO_PILL[s.estado] || '';
        const fecha    = s.fecha ? new Date(s.fecha).toLocaleString('es-PE', { dateStyle:'short', timeStyle:'short' }) : '—';
        const tipoLbl  = _SOSPECHA_TIPO_LABEL[s.tipo] || s.tipo;
        // Extraer nombre de terminal del detalle: "[PC-01] descripción..."
        const pcMatch = (s.detalle || '').match(/^\[([^\]]+)\]/);
        const pcSosp  = pcMatch ? pcMatch[1] : '';
        // Escape para contexto de atributo onclick (string JS entre comillas simples)
        const jsAttr = v => String(v ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;').replace(/</g, '&lt;');

        const btnVer = `<button onclick="actVerDesdeSospecha('${jsAttr(pcSosp)}', '${jsAttr(s.dni)}', '${jsAttr(s.fecha || '')}')"
                class="px-2 py-1 text-xs font-semibold rounded-lg bg-cyan-100 dark:bg-cyan-900/40 text-cyan-700 dark:text-cyan-300 hover:bg-cyan-200 dark:hover:bg-cyan-800/60 transition-colors ml-1">
                <i class="ph ph-eye"></i> Ver
            </button>`;

        const acciones = s.estado === 'pendiente'
            ? `<button onclick="accionSospecha(${s.id},'aprobar')"
                    class="px-2 py-1 text-xs font-semibold rounded-lg bg-rose-100 dark:bg-rose-900/40 text-rose-700 dark:text-rose-300 hover:bg-rose-200 dark:hover:bg-rose-800/60 transition-colors">
                    <i class="ph ph-check"></i> Aprobar
               </button>
               <button onclick="accionSospecha(${s.id},'descartar')"
                    class="px-2 py-1 text-xs font-semibold rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors ml-1">
                    <i class="ph ph-x"></i> Descartar
               </button>${btnVer}`
            : `<span class="text-xs text-slate-400">${escapeHtml(s.revisado_por || '—')}</span>${btnVer}`;

        return `<tr class="hover:bg-violet-50/50 dark:hover:bg-violet-900/10 transition-colors">
            <td class="p-4">
                <div class="font-semibold text-slate-800 dark:text-slate-200 text-xs">${escapeHtml(s.nombre_alumno)}</div>
                <div class="text-[11px] text-slate-400 dark:text-slate-500">${escapeHtml(s.dni)}</div>
            </td>
            <td class="p-4">
                <span class="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300">${escapeHtml(tipoLbl)}</span>
            </td>
            <td class="p-4 text-xs text-slate-600 dark:text-slate-400 max-w-xs">${escapeHtml(s.detalle)}</td>
            <td class="p-4 text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">${fecha}</td>
            <td class="p-4">
                <span class="px-2 py-0.5 rounded-full text-[11px] font-semibold ${pillCls}">${escapeHtml(s.estado)}</span>
            </td>
            <td class="p-4">${acciones}</td>
        </tr>`;
    }).join('');
}

async function accionSospecha(id, accion) {
    const confirmar = accion === 'aprobar'
        ? '¿Aprobar sospecha y crear incidencia leve automáticamente?'
        : '¿Descartar esta sospecha?';
    if (!confirm(confirmar)) return;
    try {
        const res = await fetch(`${API_BASE}/admin/sospechas/${id}/${accion}`, {
            method: 'POST', headers: authHeaders(),
        });
        const data = await res.json();
        if (res.ok) {
            mostrarNotificacion(data.mensaje, 'ok');
            cargarSospechas();
            if (accion === 'aprobar') cargarIncidencias();
        } else {
            mostrarNotificacion(data.detail || 'Error', 'error');
        }
    } catch(e) {
        mostrarNotificacion('Error de conexión', 'error');
    }
}

// Escucha mensajes de sospecha desde el WebSocket
function _handleSospecha(msg) {
    const badge = document.getElementById('sospechas-badge');
    if (badge) {
        const actual = parseInt(badge.textContent) || 0;
        badge.textContent = actual + 1;
        badge.style.display = '';
    }
    addLog('warn', msg.mensaje || 'Nueva sospecha detectada');
    mostrarNotificacion('Nueva sospecha detectada — revisa la pestaña Sospechas', 'warning');
}

// ── historial: filtro mes/año ────────────────────────────────────────
function aplicarFiltroMesAnio() {
    const desde = document.getElementById('rangoDesde');
    const hasta = document.getElementById('rangoHasta');
    if (desde) desde.value = '';
    if (hasta) hasta.value = '';
    _fechaInicio = '';
    _fechaFin    = '';
    aplicarFiltros();
}

function limpiarMesAnio() {
    const mes  = document.getElementById('filtroMes');
    const anio = document.getElementById('filtroAnio');
    if (mes)  mes.value  = '';
    if (anio) anio.value = '';
}

function _poblarAnios() {
    const anioActual = new Date().getFullYear();
    const anioBase   = 2023;
    [document.getElementById('filtroAnio')].forEach(sel => {
        if (!sel || sel.options.length > 1) return;
        for (let y = anioActual; y >= anioBase; y--) {
            const opt = document.createElement('option');
            opt.value = String(y); opt.textContent = String(y);
            sel.appendChild(opt);
        }
    });
}

// ═══════════════════════════════════════════════════════════════════
// ACTIVIDAD DE ALUMNOS
// ═══════════════════════════════════════════════════════════════════

let _actOffset   = 0;
const _ACT_LIMIT = 50;
let _actTotal    = 0;
let _actVerIgnorados = false;   // mostrar también procesos ocultados

async function cargarActividad(resetPagina = true) {
    if (resetPagina) _actOffset = 0;

    const dni      = document.getElementById('act-filtro-dni')?.value.trim()      || '';
    const terminal = document.getElementById('act-filtro-terminal')?.value.trim() || '';
    const nivel    = document.getElementById('act-filtro-nivel')?.value            || '';
    const fecha    = document.getElementById('act-filtro-fecha')?.value            || '';

    const params = new URLSearchParams({ limit: _ACT_LIMIT, offset: _actOffset });
    if (dni)      params.set('dni',      dni);
    if (terminal) params.set('terminal', terminal);
    if (nivel)    params.set('nivel',    nivel);
    if (fecha)    params.set('fecha',    fecha);
    if (_actVerIgnorados) params.set('incluir_ignorados', 'true');

    const tbody = document.getElementById('act-tabla-body');
    if (tbody) tbody.innerHTML = '<tr><td colspan="6" class="act-empty-msg">Cargando…</td></tr>';

    try {
        const res  = await fetch(`${API_BASE}/admin/actividad?${params}`, { headers: authHeaders() });
        if (!res.ok) { _actRenderError(); return; }
        const data = await res.json();
        _actTotal  = data.total || 0;
        _actRenderTabla(data.items || []);
        _actActualizarPaginacion();
        _actActualizarBadge();
    } catch(e) {
        _actRenderError();
    }
}

function _actRenderTabla(items) {
    const tbody = document.getElementById('act-tabla-body');
    if (!tbody) return;

    if (!items.length) {
        tbody.innerHTML = `<tr><td colspan="6" class="act-empty-msg">Sin registros para los filtros aplicados</td></tr>`;
        return;
    }

    const tipoIcono = { proceso:'ph-cpu', archivo:'ph-file', comando:'ph-terminal-window', navegador:'ph-globe' };

    tbody.innerHTML = items.map(r => {
        const hora     = r.fecha_hora ? new Date(r.fecha_hora).toLocaleTimeString('es-PE', {hour:'2-digit', minute:'2-digit', second:'2-digit'}) : '—';
        const isCrit   = r.nivel === 'sospechoso';
        const iconCls  = tipoIcono[(r.tipo||'').toLowerCase()] || 'ph-activity';
        const tipoBadge = r.tipo ? `<span class="act-tipo-badge">${escapeHtml(r.tipo)}</span>` : '';
        const descCls  = isCrit ? 'act-desc-crit' : 'act-desc';
        const detalle  = r.detalle ? `<div class="act-detalle" title="${escapeHtml(r.detalle)}">${escapeHtml(r.detalle)}</div>` : '';
        const rowCls   = isCrit ? 'act-row act-row-crit' : 'act-row';

        // Botón "Ignorar": solo en eventos normales con proceso_exe conocido.
        // Marca ese ejecutable como ruido para que no se vuelva a registrar.
        const btnIgnorar = (!isCrit && r.proceso_exe)
            ? `<button class="act-btn-ignorar" title="No registrar más este proceso"
                 onclick="ignorarProceso('${escapeHtml(r.proceso_exe)}')">
                 <i class="ph ph-eye-slash"></i> Ignorar
               </button>`
            : '';

        // Botón "Clasificar": solo en eventos SOSPECHOSOS con proceso_exe, y
        // solo superadmin. Abre el modal del banco para mandarlo a apps o ruido
        // (retroactivo: corrige este y los eventos pasados de ese .exe).
        const esSuper = _rolServidor === 'superadmin';
        const btnClasificar = (isCrit && r.proceso_exe && esSuper)
            ? `<button class="act-btn-clasificar" title="Clasificar este programa (banco de apps o de ruido)"
                 onclick="bancoClasificarDesdeFlujo('${escapeHtml(r.proceso_exe)}', '${escapeHtml(r.descripcion || '')}')">
                 <i class="ph ph-check-square"></i> Clasificar
               </button>`
            : '';

        return `<tr class="${rowCls}">
            <td class="act-cell-time">${hora}</td>
            <td class="act-cell-ico">
                <div class="act-ico-wrap ${isCrit ? 'act-ico-crit' : 'act-ico-normal'}">
                    <i class="ph ${iconCls}"></i>
                </div>
            </td>
            <td class="act-cell-pc">${escapeHtml(r.nombre_terminal || '—')}</td>
            <td class="act-cell-alumno">${escapeHtml(r.nombre_alumno || '—')}</td>
            <td class="act-cell-dni">${escapeHtml(r.dni_alumno || '—')}</td>
            <td class="act-cell-desc">
                ${tipoBadge}
                <div class="${descCls}">${escapeHtml(r.descripcion || '—')}</div>
                ${detalle}
                ${btnIgnorar}
                ${btnClasificar}
            </td>
        </tr>`;
    }).join('');
}

function actSetNivel(valor) {
    const sel = document.getElementById('act-filtro-nivel');
    if (sel) sel.value = valor;
    document.getElementById('act-lvl-todos').classList.toggle('act-lvl-active', valor === '');
    document.getElementById('act-lvl-sospechosos').classList.toggle('act-lvl-active', valor === 'sospechoso');
    _actOffset = 0;
    cargarActividad();
}

function _actRenderError() {
    const tbody = document.getElementById('act-tabla-body');
    if (tbody) tbody.innerHTML = '<tr><td colspan="6" class="act-empty-msg" style="color:var(--err)">Error al cargar datos</td></tr>';
}

function _actActualizarPaginacion() {
    const label   = document.getElementById('act-total-label');
    const btnPrev = document.getElementById('act-btn-prev');
    const btnNext = document.getElementById('act-btn-next');
    const desde   = _actOffset + 1;
    const hasta   = Math.min(_actOffset + _ACT_LIMIT, _actTotal);
    if (label) label.textContent = _actTotal > 0 ? `Mostrando ${desde}–${hasta} de ${_actTotal}` : 'Sin resultados';
    if (btnPrev) btnPrev.disabled = _actOffset === 0;
    if (btnNext) btnNext.disabled = (_actOffset + _ACT_LIMIT) >= _actTotal;
}

function actCambiarPagina(dir) {
    _actOffset = Math.max(0, _actOffset + dir * _ACT_LIMIT);
    cargarActividad(false);
}

function actLimpiarFiltros() {
    ['act-filtro-dni', 'act-filtro-terminal', 'act-filtro-fecha'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    const nivel = document.getElementById('act-filtro-nivel');
    if (nivel) nivel.value = '';
    _actVerIgnorados = false;          // volver a vista limpia
    _actSyncToggleIgnorados();
    _actOffset = 0;
    cargarActividad();
}

// ── Procesos ignorados ──────────────────────────────────────────────

async function ignorarProceso(nombreExe) {
    if (!nombreExe) return;
    if (!confirm(`¿Dejar de registrar el proceso "${nombreExe}"?\n\nNo aparecerá más en el flujo de actividad de ninguna PC. Podrás revertirlo desde "Procesos ignorados".`)) return;
    try {
        const res = await fetch(`${API_BASE}/admin/procesos-ignorados`, {
            method: 'POST',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ nombre_exe: nombreExe }),
        });
        if (res.ok) {
            addLog('activity', `Proceso "${nombreExe}" agregado a ignorados`);
            cargarActividad(false);
        } else {
            addLog('error', `No se pudo ignorar "${nombreExe}": HTTP ${res.status}`);
        }
    } catch (e) {
        addLog('error', `Error de red al ignorar proceso: ${e.message}`);
    }
}

async function abrirProcesosIgnorados() {
    const cont = document.getElementById('procesos-ignorados-lista');
    const modal = document.getElementById('modal-procesos-ignorados');
    if (!modal || !cont) return;
    modal.style.display = 'flex';
    cont.innerHTML = '<p class="empty-msg">Cargando…</p>';
    try {
        const res = await fetch(`${API_BASE}/admin/procesos-ignorados`, { headers: authHeaders() });
        const data = await res.json();
        const items = data.items || [];
        if (!items.length) {
            cont.innerHTML = '<p class="empty-msg">No hay procesos ignorados</p>';
            return;
        }
        cont.innerHTML = items.map(p => `
            <div class="proc-ign-row">
                <code class="proc-ign-exe">${escapeHtml(p.nombre_exe)}</code>
                <span class="proc-ign-meta">${escapeHtml(p.agregado_por || '—')}</span>
                <button class="proc-ign-quitar" onclick="quitarProcesoIgnorado(${p.id}, '${escapeHtml(p.nombre_exe)}')">
                    <i class="ph ph-trash"></i> Quitar
                </button>
            </div>`).join('');
    } catch (e) {
        cont.innerHTML = '<p class="empty-msg" style="color:var(--err)">Error al cargar</p>';
    }
}

function cerrarProcesosIgnorados() {
    const modal = document.getElementById('modal-procesos-ignorados');
    if (modal) modal.style.display = 'none';
}

async function quitarProcesoIgnorado(id, nombre) {
    if (!confirm(`¿Quitar "${nombre}" de ignorados? Volverá a registrarse.`)) return;
    try {
        const res = await fetch(`${API_BASE}/admin/procesos-ignorados/${id}`, {
            method: 'DELETE', headers: authHeaders(),
        });
        if (res.ok) {
            addLog('activity', `Proceso "${nombre}" quitado de ignorados`);
            abrirProcesosIgnorados();
        } else {
            addLog('error', `No se pudo quitar: HTTP ${res.status}`);
        }
    } catch (e) {
        addLog('error', `Error de red: ${e.message}`);
    }
}

function actVerDesdeSospecha(pc, dni, fecha) {
    switchTab('actividad');
    // Al investigar una sospecha, mostrar TODO el contexto del alumno,
    // incluidos los procesos normalmente ocultados.
    _actVerIgnorados = true;
    _actSyncToggleIgnorados();
    setTimeout(() => {
        const filtroDni      = document.getElementById('act-filtro-dni');
        const filtroTerminal = document.getElementById('act-filtro-terminal');
        const filtroNivel    = document.getElementById('act-filtro-nivel');
        const filtroFecha    = document.getElementById('act-filtro-fecha');
        if (filtroDni)      filtroDni.value      = dni;
        if (filtroTerminal) filtroTerminal.value  = pc;
        if (filtroNivel)    filtroNivel.value     = '';
        // Precargar la fecha de la sospecha
        if (filtroFecha && fecha) {
            filtroFecha.value = new Date(fecha).toLocaleDateString('en-CA');
        }
        cargarActividad();
    }, 150);
}

// "Ver más de hoy": va a la pestaña Eventos filtrando solo el día de hoy.
function actVerHoy() {
    cerrarMenuExpandido();
    if (typeof toggleActividadVivo === 'function') {
        const d = document.getElementById('monLiveSide');
        if (d && d.classList.contains('open')) toggleActividadVivo();  // cerrar el drawer si está abierto
    }
    switchTab('actividad');
    setTimeout(() => {
        ['act-filtro-dni', 'act-filtro-terminal'].forEach(id => {
            const el = document.getElementById(id); if (el) el.value = '';
        });
        const filtroNivel = document.getElementById('act-filtro-nivel');
        if (filtroNivel) filtroNivel.value = '';
        const filtroFecha = document.getElementById('act-filtro-fecha');
        if (filtroFecha) filtroFecha.value = new Date().toLocaleDateString('en-CA');  // YYYY-MM-DD de hoy
        cargarActividad();
    }, 150);
}

// Alterna entre vista limpia (ignorados ocultos) y vista completa.
function actToggleIgnorados() {
    _actVerIgnorados = !_actVerIgnorados;
    _actSyncToggleIgnorados();
    _actOffset = 0;
    cargarActividad();
}

function _actSyncToggleIgnorados() {
    const btn = document.getElementById('act-toggle-ignorados');
    if (!btn) return;
    btn.classList.toggle('act-toggle-on', _actVerIgnorados);
    btn.innerHTML = _actVerIgnorados
        ? '<i class="ph ph-eye"></i> Viendo todo'
        : '<i class="ph ph-eye-slash"></i> Ver ignorados';
}

async function _actActualizarBadge() {
    try {
        const res  = await fetch(`${API_BASE}/admin/actividad/resumen-hoy`, { headers: authHeaders() });
        if (!res.ok) return;
        const data = await res.json();
        const badge = document.getElementById('actividad-badge');
        if (!badge) return;
        const n = data.sospechosos_hoy || 0;
        _notifTotales.actividad = n;
        // Si está viendo la pestaña Actividad/Eventos, se marca visto al instante.
        const viendoAct = document.getElementById('tab-actividad')?.style.display !== 'none'
                          && document.getElementById('tab-actividad');
        if (viendoAct) { try { localStorage.setItem('notif_visto_actividad', String(n)); } catch(e) {} }
        // Badge del ícono: notificación → solo si hay sospechosos NUEVOS.
        const nuevas = n - _notifVistas('actividad');
        if (nuevas > 0) { badge.textContent = nuevas > 99 ? '99+' : nuevas; badge.style.display = ''; }
        else badge.style.display = 'none';
    } catch(e) { /* silencioso */ }
}

// ═══════════════════════════════════════════════════════════════════
// BANCO DE APPS / BANCO DE RUIDO (sub-pestañas de Eventos)
// ═══════════════════════════════════════════════════════════════════
// Modelo de 3 estados: en banco_apps (app real), en banco_ruido (proceso
// de fondo, oculto del Flujo) o en ninguno (sospechoso hasta clasificar).
let _bancoVista = 'flujo';        // 'flujo' | 'banco-apps' | 'banco-ruido'
let _bancoAppsCache  = [];        // para poblar el desplegable "Pertenece a"
let _bancoPendientes = [];        // últimos pendientes cargados (para clasificar por índice)

function actSetVista(v) {
    _bancoVista = v;
    document.querySelectorAll('.kpi-tab[data-actvista]').forEach(b => {
        const activo = b.dataset.actvista === v;
        b.classList.toggle('kpi-tab-activo', activo);
        b.setAttribute('aria-selected', activo ? 'true' : 'false');
    });
    const paneles = {
        'flujo':       'act-vista-flujo',
        'banco-apps':  'act-vista-banco-apps',
        'banco-ruido': 'act-vista-banco-ruido',
    };
    Object.entries(paneles).forEach(([k, id]) => {
        const el = document.getElementById(id);
        if (el) el.style.display = (k === v) ? '' : 'none';
    });
    if (v === 'flujo')       cargarActividad();
    if (v === 'banco-apps')  { bancoCargarPendientes(); bancoCargarApps(); }
    if (v === 'banco-ruido') bancoCargarRuido();
}

// ── Pendientes por clasificar ──
async function bancoCargarPendientes() {
    const cont = document.getElementById('banco-pendientes-body');
    const cnt  = document.getElementById('banco-pend-contador');
    if (!cont) return;
    const esSuper = _rolServidor === 'superadmin';
    try {
        const res = await fetch(`${API_BASE}/admin/banco/pendientes?limit=50`, { headers: authHeaders() });
        if (!res.ok) { cont.innerHTML = '<p class="kpi-empty">No se pudo cargar.</p>'; return; }
        const data = await res.json();
        const items = data.items || [];
        _bancoPendientes = items;   // clasificamos por índice (evita inyección por atributo)
        if (cnt) cnt.textContent = data.total || 0;
        if (!items.length) {
            cont.innerHTML = '<p class="kpi-empty">Nada por clasificar. Todo lo visto ya está en un banco.</p>';
            return;
        }
        cont.innerHTML = items.map((it, idx) => {
            const sug = it.sugerencia_tipo === 'app'   ? '<span class="banco-sug banco-sug-app">Parece app</span>'
                      : it.sugerencia_tipo === 'ruido' ? '<span class="banco-sug banco-sug-ruido">Parece ruido</span>'
                      : '<span class="banco-sug banco-sug-desc">Desconocido</span>';
            const desc = it.sugerencia_desc ? `<span class="banco-pend-desc">${esc(it.sugerencia_desc)}</span>` : '';
            const acciones = esSuper ? `
                <div class="banco-pend-acc">
                    <button class="banco-mini-btn banco-mini-app" onclick="bancoClasificarRapido(${idx}, 'app')">Es programa</button>
                    <button class="banco-mini-btn banco-mini-ruido" onclick="bancoClasificarRapido(${idx}, 'ruido')">Es ruido</button>
                </div>` : '';
            return `
                <div class="banco-pend-row">
                    <div class="banco-pend-main">
                        <code class="banco-pend-exe">${esc(it.nombre_exe)}</code>
                        ${sug}
                        ${desc}
                    </div>
                    <div class="banco-pend-stat">
                        <span title="Alumnos distintos que lo usaron"><i class="ph ph-users"></i> ${it.alumnos}</span>
                        <span title="Veces visto"><i class="ph ph-eye"></i> ${it.veces}</span>
                    </div>
                    ${acciones}
                </div>`;
        }).join('');
    } catch (e) {
        cont.innerHTML = '<p class="kpi-empty">Error de conexión.</p>';
    }
}

// Clasificación rápida desde un pendiente (por índice en _bancoPendientes).
// Abre el modal pre-rellenado con la sugerencia del catálogo.
function bancoClasificarRapido(idx, destino) {
    const it = _bancoPendientes[idx];
    if (!it) return;
    if (destino === 'app') {
        bancoAbrirModal('app', {
            nombre_exe: it.nombre_exe,
            nombre_amigable: it.sugerencia_nombre || '',
            descripcion: it.sugerencia_desc || '',
        });
    } else {
        bancoAbrirModal('ruido', {
            nombre_exe: it.nombre_exe,
            nombre_amigable: it.sugerencia_nombre || '',
            descripcion: it.sugerencia_desc || '',
            dueno_exe: it.sugerencia_dueno || '__sistema__',
        });
    }
}

// ── Tabla banco de apps ──
async function bancoCargarApps() {
    const tbody = document.getElementById('banco-apps-body');
    if (!tbody) return;
    const esSuper = _rolServidor === 'superadmin';
    const q = document.getElementById('banco-apps-buscar')?.value.trim() || '';
    try {
        const url = `${API_BASE}/admin/banco-apps` + (q ? `?q=${encodeURIComponent(q)}` : '');
        const res = await fetch(url, { headers: authHeaders() });
        const data = await res.json();
        const items = data.items || [];
        _bancoAppsCache = items;   // para el desplegable de dueño
        if (!items.length) {
            tbody.innerHTML = `<tr><td colspan="5" class="act-empty-msg">Sin programas${q ? ' que coincidan' : ''}.</td></tr>`;
            return;
        }
        tbody.innerHTML = items.map(a => `
            <tr>
                <td class="act-td">${esc(a.nombre_amigable)}</td>
                <td class="act-td"><code class="proc-ign-exe">${esc(a.nombre_exe)}</code></td>
                <td class="act-td">${esc(a.categoria || '—')}</td>
                <td class="act-td act-td-desc">${esc(a.descripcion || '—')}</td>
                <td class="act-td">${esSuper ? `<button class="tbl-btn tbl-btn-ban" title="Quitar del banco" onclick="bancoEliminar('app','${esc(a.nombre_exe)}')"><i class="ph ph-trash"></i></button>` : ''}</td>
            </tr>`).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="5" class="act-empty-msg" style="color:var(--err)">Error al cargar</td></tr>';
    }
}

// ── Tabla banco de ruido ──
async function bancoCargarRuido() {
    const tbody = document.getElementById('banco-ruido-body');
    if (!tbody) return;
    const esSuper = _rolServidor === 'superadmin';
    const q = document.getElementById('banco-ruido-buscar')?.value.trim() || '';
    try {
        const url = `${API_BASE}/admin/banco-ruido` + (q ? `?q=${encodeURIComponent(q)}` : '');
        const res = await fetch(url, { headers: authHeaders() });
        const data = await res.json();
        const items = data.items || [];
        if (!items.length) {
            tbody.innerHTML = `<tr><td colspan="5" class="act-empty-msg">Sin procesos${q ? ' que coincidan' : ''}.</td></tr>`;
            return;
        }
        tbody.innerHTML = items.map(r => `
            <tr>
                <td class="act-td">${esc(r.nombre_amigable || '—')}</td>
                <td class="act-td"><code class="proc-ign-exe">${esc(r.nombre_exe)}</code></td>
                <td class="act-td">${esc(r.dueno_legible || '—')}</td>
                <td class="act-td act-td-desc">${esc(r.descripcion || '—')}</td>
                <td class="act-td">${esSuper ? `<button class="tbl-btn tbl-btn-ban" title="Quitar del banco de ruido" onclick="bancoEliminar('ruido','${esc(r.nombre_exe)}')"><i class="ph ph-trash"></i></button>` : ''}</td>
            </tr>`).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="5" class="act-empty-msg" style="color:var(--err)">Error al cargar</td></tr>';
    }
}

// ── Modal agregar/clasificar ──
let _bancoModalDestino = 'app';     // 'app' | 'ruido'
let _bancoModalDesdeFlujo = false;  // true si se abrió desde el Flujo (refresca el Flujo al guardar)
let _bancoModalDesdeRanking = false;// true si se abrió desde el ranking de Programas (Indicadores)
let _bancoPreDueno = '__sistema__'; // dueño sugerido para reponer al alternar a 'ruido'

function bancoAbrirNuevo(destino) {
    _bancoModalDesdeFlujo = false;
    _bancoModalDesdeRanking = false;
    bancoAbrirModal(destino, {});
}

// Aplica el modo (app/ruido) al modal ya abierto: título, qué campos se ven,
// y rellena el desplegable de dueño en modo ruido.
function _bancoAplicarDestino(destino) {
    _bancoModalDestino = destino;
    const esApp = destino === 'app';
    document.getElementById('modal-banco-titulo').innerHTML = esApp
        ? '<i class="ph ph-check-square"></i> Programa reconocido'
        : '<i class="ph ph-wave-sine"></i> Proceso de ruido';
    document.getElementById('banco-f-grupo-cat').style.display   = esApp ? '' : 'none';
    document.getElementById('banco-f-grupo-dueno').style.display = esApp ? 'none' : '';
    // Resaltar el botón activo del toggle (si está visible)
    document.getElementById('banco-dest-app')?.classList.toggle('banco-dest-activo', esApp);
    document.getElementById('banco-dest-ruido')?.classList.toggle('banco-dest-activo', !esApp);
    if (!esApp) {
        const sel = document.getElementById('banco-f-dueno');
        const opciones = ['<option value="__sistema__">Windows / Sistema</option>']
            .concat(_bancoAppsCache.map(a => `<option value="${esc(a.nombre_exe)}">${esc(a.nombre_amigable)} (${esc(a.nombre_exe)})</option>`));
        sel.innerHTML = opciones.join('');
        sel.value = _bancoPreDueno || '__sistema__';
    }
}

// Cambia el destino desde el toggle del modal (clasificar desde el Flujo).
function bancoSetDestino(destino) { _bancoAplicarDestino(destino); }

function bancoAbrirModal(destino, pre) {
    if (_rolServidor !== 'superadmin') return;
    const modal = document.getElementById('modal-banco');
    if (!modal) return;
    _bancoPreDueno = pre.dueno_exe || '__sistema__';
    // El selector de destino (Programa/Ruido) se muestra al clasificar desde el
    // Flujo o desde el ranking de Programas, para que el superadmin pueda decidir.
    const grupoDest = document.getElementById('banco-f-grupo-destino');
    if (grupoDest) grupoDest.style.display = (_bancoModalDesdeFlujo || _bancoModalDesdeRanking) ? '' : 'none';
    // Rellenar campos comunes
    document.getElementById('banco-f-exe').value    = pre.nombre_exe || '';
    document.getElementById('banco-f-nombre').value = pre.nombre_amigable || '';
    document.getElementById('banco-f-cat').value    = pre.categoria || '';
    document.getElementById('banco-f-desc').value   = pre.descripcion || '';
    _bancoAplicarDestino(destino);
    modal.style.display = 'flex';
}

// Abre el modal de clasificación DESDE una fila sospechosa del Flujo.
// Pre-rellena exe + nombre amigable parseado de la descripción, y muestra el
// selector Programa/Ruido para que el superadmin decida.
function bancoClasificarDesdeFlujo(exe, descripcion) {
    if (_rolServidor !== 'superadmin') return;
    _bancoModalDesdeFlujo = true;
    // Parsear nombre amigable de descripciones tipo "Abrió: File Picker UI Host (PickerHost.exe)"
    let nombre = '';
    const m = (descripcion || '').match(/:\s*(.+?)\s*\(/);
    if (m) nombre = m[1].trim();
    // Por defecto sugerimos "ruido" (el caso más común de sospechas por banco).
    bancoAbrirModal('ruido', { nombre_exe: exe, nombre_amigable: nombre });
}

function bancoCerrarModal() {
    const modal = document.getElementById('modal-banco');
    if (modal) modal.style.display = 'none';
    _bancoModalDesdeFlujo = false;
    _bancoModalDesdeRanking = false;
}

// Abrir el modal de clasificación desde el ranking de Programas (Indicadores).
// A diferencia del Flujo, aquí sugerimos "app" por defecto: el ranking lista
// programas que el alumno abre, y lo común es querer reconocerlos como apps
// (aunque el superadmin puede alternar a ruido en el mismo modal).
function bancoClasificarDesdeRanking(exe) {
    if (_rolServidor !== 'superadmin') return;
    _bancoModalDesdeRanking = true;
    bancoAbrirModal('app', { nombre_exe: exe });
}

async function bancoGuardarModal() {
    const btn = document.getElementById('banco-btn-guardar');
    const exe = document.getElementById('banco-f-exe').value.trim();
    if (!exe) { mostrarNotificacion('Indica el ejecutable (.exe)', 'error'); return; }
    const esApp = _bancoModalDestino === 'app';
    // Usamos /clasificar siempre: da de alta en el banco Y corrige retroactivo.
    const cuerpo = {
        nombre_exe: exe,
        destino: esApp ? 'app' : 'ruido',
        nombre_amigable: document.getElementById('banco-f-nombre').value.trim() || null,
        descripcion: document.getElementById('banco-f-desc').value.trim() || null,
    };
    if (esApp) cuerpo.categoria = document.getElementById('banco-f-cat').value.trim() || null;
    else       cuerpo.dueno_exe = document.getElementById('banco-f-dueno').value;
    if (btn) btn.disabled = true;
    try {
        const res = await fetch(`${API_BASE}/admin/banco/clasificar`, {
            method: 'POST',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify(cuerpo),
        });
        const data = await res.json();
        if (!res.ok) { mostrarNotificacion(data.detail || 'Error al guardar', 'error'); return; }
        const extra = data.eventos_corregidos
            ? ` (${data.eventos_corregidos} evento(s) corregido(s))` : '';
        mostrarNotificacion(`Guardado${extra}`, 'ok');
        const desdeFlujo    = _bancoModalDesdeFlujo;
        const desdeRanking  = _bancoModalDesdeRanking;
        _bancoModalDesdeFlujo = false;
        _bancoModalDesdeRanking = false;
        bancoCerrarModal();
        if (desdeFlujo) {
            // Se clasificó desde el Flujo: refrescar la lista para que la fila
            // (ya no sospechosa) desaparezca de la vista limpia.
            cargarActividad();
        } else if (desdeRanking) {
            // Se clasificó desde el ranking de Programas: recargar el ranking
            // para que la fila pase de "Sin clasificar" a app/ruido.
            _kpiCargarRanking();
        } else {
            bancoCargarPendientes();
            if (esApp) bancoCargarApps(); else bancoCargarRuido();
        }
    } catch (e) {
        mostrarNotificacion('Error de conexión', 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function bancoEliminar(tipo, exe) {
    if (_rolServidor !== 'superadmin') return;
    const etiqueta = tipo === 'app' ? 'del banco de apps' : 'del banco de ruido';
    if (!confirm(`¿Quitar "${exe}" ${etiqueta}? Volverá a aparecer como sospechoso.`)) return;
    const ruta = tipo === 'app' ? 'banco-apps' : 'banco-ruido';
    try {
        const res = await fetch(`${API_BASE}/admin/${ruta}/${encodeURIComponent(exe)}`, {
            method: 'DELETE', headers: authHeaders(),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { mostrarNotificacion(data.detail || 'No se pudo quitar', 'error'); return; }
        mostrarNotificacion('Quitado del banco', 'ok');
        bancoCargarPendientes();
        if (tipo === 'app') bancoCargarApps(); else bancoCargarRuido();
    } catch (e) {
        mostrarNotificacion('Error de conexión', 'error');
    }
}

// ═══════════════════════════════════════════════════════════════════
// CONFIGURACIÓN — RUTA DE DISTRIBUCIÓN (auto-actualización)
// ═══════════════════════════════════════════════════════════════════

async function cargarRutaDistribucion() {
    try {
        const res  = await fetch(`${API_BASE}/admin/config/ruta-distribucion`, { headers: authHeaders() });
        if (!res.ok) return;
        const data = await res.json();
        const input = document.getElementById('cfg-ruta-distribucion');
        if (input) input.value = data.ruta || '';
    } catch(e) { /* silencioso */ }
}

async function guardarRutaDistribucion() {
    const input  = document.getElementById('cfg-ruta-distribucion');
    const status = document.getElementById('cfg-ruta-status');
    const ruta   = input?.value.trim() || '';

    if (!ruta) {
        if (status) { status.textContent = 'La ruta no puede estar vacía.'; status.className = 'text-xs text-red-500 min-h-4'; }
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/admin/config/ruta-distribucion`, {
            method:  'POST',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body:    JSON.stringify({ ruta }),
        });
        const data = await res.json();
        if (!res.ok) {
            if (status) { status.textContent = data.detail || 'Error al guardar.'; status.className = 'text-xs text-red-500 min-h-4'; }
            return;
        }
        if (status) { status.textContent = 'Ruta guardada correctamente.'; status.className = 'text-xs text-emerald-500 min-h-4'; }
        setTimeout(() => { if (status) status.textContent = ''; }, 3000);
    } catch(e) {
        if (status) { status.textContent = 'Error de conexión.'; status.className = 'text-xs text-red-500 min-h-4'; }
    }
}


// ═══════════════════════════════════════════════════════════════════
// CONFIGURACIÓN — BACKDOOR
// ═══════════════════════════════════════════════════════════════════

async function cargarConfigBackdoor() {
    try {
        const res = await fetch(`${API_BASE}/config/backdoor`, { headers: authHeaders() });
        if (!res.ok) return;
        const data = await res.json();
        
        const mods = data.backdoor_modifiers;
        const eAlt = document.getElementById('cfg-backdoor-alt');
        const eCtrl = document.getElementById('cfg-backdoor-ctrl');
        const eShift = document.getElementById('cfg-backdoor-shift');
        
        if (eAlt) eAlt.checked = (mods & 0x0001) !== 0;
        if (eCtrl) eCtrl.checked = (mods & 0x0002) !== 0;
        if (eShift) eShift.checked = (mods & 0x0004) !== 0;
        
        const eKey = document.getElementById('cfg-backdoor-key');
        if (eKey) eKey.value = data.backdoor_key;
        
        const ePin = document.getElementById('cfg-backdoor-pin');
        if (ePin) ePin.value = data.backdoor_pin;
    } catch(e) {
        console.error('Error cargando config backdoor:', e);
    }
}

async function guardarConfigBackdoor() {
    const status = document.getElementById('cfg-backdoor-status');
    const setStatus = (txt, color) => {
        if(status) { status.textContent = txt; status.className = `text-xs min-h-4 mt-2 text-right ${color}`; }
    };
    
    let mods = 0;
    if (document.getElementById('cfg-backdoor-alt')?.checked) mods |= 0x0001;
    if (document.getElementById('cfg-backdoor-ctrl')?.checked) mods |= 0x0002;
    if (document.getElementById('cfg-backdoor-shift')?.checked) mods |= 0x0004;
    
    if (mods === 0) {
        setStatus('Debes seleccionar al menos un modificador (Ctrl, Alt o Shift).', 'text-red-500');
        return;
    }
    
    const key = parseInt(document.getElementById('cfg-backdoor-key')?.value || 0, 10);
    const pin = (document.getElementById('cfg-backdoor-pin')?.value || '').trim();
    
    if (!pin) {
        setStatus('El PIN de desbloqueo no puede estar vacío.', 'text-red-500');
        return;
    }
    
    try {
        const res = await fetch(`${API_BASE}/config/backdoor`, {
            method: 'PUT',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({
                backdoor_modifiers: mods,
                backdoor_key: key,
                backdoor_pin: pin
            })
        });
        const data = await res.json();
        if (!res.ok) {
            setStatus(data.detail || 'Error al guardar configuración.', 'text-red-500');
            return;
        }
        setStatus('Configuración guardada correctamente.', 'text-emerald-500');
        setTimeout(() => { if(status) status.textContent = ''; }, 3000);
    } catch (e) {
        setStatus('Error de conexión.', 'text-red-500');
    }
}


// ═══════════════════════════════════════════════════════════════════
// CONFIGURACIÓN — MODO OFFLINE
// ═══════════════════════════════════════════════════════════════════

async function cargarConfigOffline() {
    try {
        const res = await fetch(`${API_BASE}/config/offline-pin`, { headers: authHeaders() });
        if (!res.ok) return;
        const data = await res.json();

        const mods = data.offline_modifiers;
        const eAlt   = document.getElementById('cfg-offline-alt');
        const eCtrl  = document.getElementById('cfg-offline-ctrl');
        const eShift = document.getElementById('cfg-offline-shift');

        if (eAlt)   eAlt.checked   = (mods & 0x0001) !== 0;
        if (eCtrl)  eCtrl.checked  = (mods & 0x0002) !== 0;
        if (eShift) eShift.checked = (mods & 0x0004) !== 0;

        const eKey = document.getElementById('cfg-offline-key');
        if (eKey) eKey.value = data.offline_key;

        const ePin = document.getElementById('cfg-offline-pin');
        if (ePin) ePin.value = data.offline_pin;
    } catch(e) {
        console.error('Error cargando config offline:', e);
    }
}

async function guardarConfigOffline() {
    const status = document.getElementById('cfg-offline-status');
    const setStatus = (txt, color) => {
        if (status) { status.textContent = txt; status.className = `text-xs min-h-4 mt-2 text-right ${color}`; }
    };

    let mods = 0;
    if (document.getElementById('cfg-offline-alt')?.checked)   mods |= 0x0001;
    if (document.getElementById('cfg-offline-ctrl')?.checked)  mods |= 0x0002;
    if (document.getElementById('cfg-offline-shift')?.checked) mods |= 0x0004;

    if (mods === 0) {
        setStatus('Debes seleccionar al menos un modificador (Ctrl, Alt o Shift).', 'text-red-500');
        return;
    }

    const key = parseInt(document.getElementById('cfg-offline-key')?.value || 0, 10);
    const pin = (document.getElementById('cfg-offline-pin')?.value || '').trim();

    if (!pin) {
        setStatus('El PIN offline no puede estar vacío.', 'text-red-500');
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/config/offline-pin`, {
            method: 'PUT',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ offline_modifiers: mods, offline_key: key, offline_pin: pin })
        });
        const data = await res.json();
        if (!res.ok) {
            setStatus(data.detail || 'Error al guardar.', 'text-red-500');
            return;
        }
        setStatus('Configuracion offline guardada correctamente.', 'text-emerald-500');
        setTimeout(() => { if (status) status.textContent = ''; }, 3000);
    } catch (e) {
        setStatus('Error de conexion.', 'text-red-500');
    }
}


// ── Mensajes Programados ──────────────────────────────────────────

async function cargarMensajes() {
    try {
        const res  = await fetch(`${API_BASE}/mensajes`, { headers: authHeaders() });
        if (!res.ok) return;
        const data = await res.json();

        // Cierre diario
        const cierre = data.find(m => m.tipo === 'cierre');
        if (cierre) {
            const h = document.getElementById('msg-cierre-hora');
            const t = document.getElementById('msg-cierre-texto');
            const a = document.getElementById('msg-cierre-activo');
            if (h) h.value = cierre.hora_envio;
            if (t) t.value = cierre.mensaje;
            if (a) a.checked = cierre.activo;
        }

        // Extras pendientes (no enviados)
        const extras = data.filter(m => m.tipo === 'extra' && !m.enviado);
        _renderMensajesExtra(extras);

        // Duración del aviso (global, en minutos)
        try {
            const rd = await fetch(`${API_BASE}/mensajes/duracion`, { headers: authHeaders() });
            if (rd.ok) {
                const dd = await rd.json();
                const inp = document.getElementById('msg-duracion-min');
                if (inp && dd.minutos) inp.value = dd.minutos;
            }
        } catch (e) { /* silencioso */ }
    } catch (e) { /* silencioso */ }
}

async function guardarDuracionMensaje() {
    const inp    = document.getElementById('msg-duracion-min');
    const status = document.getElementById('msg-duracion-status');
    const setS   = (msg, cls) => { if (status) { status.textContent = msg; status.className = `text-xs min-h-4 ${cls}`; } };
    let minutos = parseInt(inp?.value, 10);
    if (isNaN(minutos) || minutos < 1 || minutos > 15) {
        setS('Elige un valor entre 1 y 15 minutos.', 'text-red-500');
        return;
    }
    try {
        const res = await fetch(`${API_BASE}/mensajes/duracion`, {
            method: 'PUT',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ minutos }),
        });
        const data = await res.json();
        if (!res.ok) { setS(data.detail || 'Error al guardar.', 'text-red-500'); return; }
        setS('Guardado', 'text-emerald-500');
        setTimeout(() => { if (status) status.textContent = ''; }, 3000);
    } catch (e) { setS('Error de conexión.', 'text-red-500'); }
}

function _renderMensajesExtra(lista) {
    const contenedor = document.getElementById('msg-extra-lista');
    if (!contenedor) return;
    if (!lista.length) {
        contenedor.innerHTML = '<p class="text-xs text-zinc-400 dark:text-zinc-500 text-center py-2">Sin mensajes pendientes</p>';
        return;
    }
    contenedor.innerHTML = lista.map(m => `
        <div class="flex items-center gap-3 bg-zinc-50 dark:bg-zinc-800 rounded-xl px-4 py-2.5 border border-zinc-200 dark:border-zinc-700">
            <div class="flex-1 min-w-0">
                <p class="text-xs font-semibold text-zinc-700 dark:text-zinc-200 truncate">${esc(m.mensaje)}</p>
                <p class="text-xs text-zinc-400">${m.fecha_envio || 'hoy'} · ${m.hora_envio}</p>
            </div>
            <button onclick="eliminarMensaje(${m.id})" title="Eliminar" class="tbl-btn tbl-btn-ban flex-shrink-0">
                <i class="ph ph-trash"></i>
            </button>
        </div>
    `).join('');
}

async function guardarMensajeCierre() {
    const hora  = (document.getElementById('msg-cierre-hora')?.value  || '').trim();
    const texto = (document.getElementById('msg-cierre-texto')?.value || '').trim();
    const activo = document.getElementById('msg-cierre-activo')?.checked ?? true;
    const status = document.getElementById('msg-cierre-status');
    const setS   = (msg, cls) => { if (status) { status.textContent = msg; status.className = `text-xs min-h-4 ${cls}`; } };

    if (!hora || !texto) { setS('Completa la hora y el mensaje.', 'text-red-500'); return; }

    try {
        const res = await fetch(`${API_BASE}/mensajes`, {
            method: 'POST',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ mensaje: texto, hora_envio: hora, tipo: 'cierre' }),
        });
        const data = await res.json();
        if (!res.ok) { setS(data.detail || 'Error al guardar.', 'text-red-500'); return; }

        // Si el usuario desactivó el toggle, hacer toggle en el backend
        if (!activo) {
            const id = data.id;
            if (id) await fetch(`${API_BASE}/mensajes/${id}/toggle`, { method: 'PUT', headers: authHeaders() });
        }

        setS('Guardado', 'text-emerald-500');
        setTimeout(() => { if (status) status.textContent = ''; }, 3000);
        await cargarMensajes();
    } catch (e) { setS('Error de conexión.', 'text-red-500'); }
}

async function agregarMensajeExtra() {
    const fecha = (document.getElementById('msg-extra-fecha')?.value || '').trim();
    const hora  = (document.getElementById('msg-extra-hora')?.value  || '').trim();
    const texto = (document.getElementById('msg-extra-texto')?.value || '').trim();
    const status = document.getElementById('msg-extra-status');
    const setS   = (msg, cls) => { if (status) { status.textContent = msg; status.className = `text-xs min-h-4 ${cls}`; } };

    if (!hora || !texto) { setS('Completa la hora y el mensaje.', 'text-red-500'); return; }

    try {
        const res = await fetch(`${API_BASE}/mensajes`, {
            method: 'POST',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ mensaje: texto, hora_envio: hora, tipo: 'extra', fecha_envio: fecha }),
        });
        const data = await res.json();
        if (!res.ok) { setS(data.detail || 'Error al agregar.', 'text-red-500'); return; }

        setS('Agregado', 'text-emerald-500');
        setTimeout(() => { if (status) status.textContent = ''; }, 2000);
        const fEl = document.getElementById('msg-extra-fecha');
        const hEl = document.getElementById('msg-extra-hora');
        const tEl = document.getElementById('msg-extra-texto');
        if (fEl) fEl.value = '';
        if (hEl) hEl.value = '';
        if (tEl) tEl.value = '';
        await cargarMensajes();
    } catch (e) { setS('Error de conexión.', 'text-red-500'); }
}

// ── Eliminar terminal fantasma ─────────────────────────────────────
async function eliminarTerminalFantasma(terminalId, nombre) {
    if (!confirm(`¿Eliminar la terminal "${nombre}" de la base de datos?\n\nEsto es irreversible. Solo hazlo si la PC ya no existe en la red.`)) return;
    try {
        const res = await fetch(`${API_BASE}/terminales/${terminalId}`, {
            method: 'DELETE',
            headers: authHeaders()
        });
        if (res.ok) {
            addLog('activity', `Terminal fantasma "${nombre}" eliminada de la DB`);
            await cargarDashboard();
        } else {
            const err = await res.json().catch(() => ({}));
            addLog('error', `Error al eliminar terminal: ${err.detail || res.status}`);
        }
    } catch (e) {
        addLog('error', `Error de red al eliminar terminal: ${e.message}`);
    }
}

async function eliminarMensaje(id) {
    try {
        await fetch(`${API_BASE}/mensajes/${id}`, { method: 'DELETE', headers: authHeaders() });
        await cargarMensajes();
    } catch (e) { /* silencioso */ }
}

// Envía un aviso de prueba inmediato a todas las PCs conectadas (no se guarda).
async function probarMensajeAhora() {
    const status = document.getElementById('msg-probar-status');
    const setS   = (msg, cls) => { if (status) { status.textContent = msg; status.className = `text-xs min-h-4 ${cls}`; } };
    const texto  = (document.getElementById('msg-cierre-texto')?.value || '').trim()
                || 'Mensaje de prueba del panel.';
    try {
        const res = await fetch(`${API_BASE}/mensajes/probar`, {
            method: 'POST',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ mensaje: texto, hora_envio: '00:00', tipo: 'extra' }),
        });
        const data = await res.json();
        if (!res.ok) { setS(data.detail || 'Error al enviar.', 'text-red-500'); return; }
        const n = data.entregados ?? 0;
        if (n > 0) setS(`Enviado a ${n} PC(s) conectada(s).`, 'text-emerald-500');
        else       setS('Ninguna PC conectada en este momento.', 'text-amber-500');
        setTimeout(() => { if (status) status.textContent = ''; }, 4000);
    } catch (e) { setS('Error de conexión.', 'text-red-500'); }
}

// ── Mensaje rápido a la sala (desde Comandos Globales en Monitoreo) ──
function abrirMensajeRapido() {
    const modal = document.getElementById('modal-msg-rapido');
    if (!modal) return;
    const inp = document.getElementById('msg-rapido-texto');
    if (inp) inp.value = '';
    const st = document.getElementById('msg-rapido-status');
    if (st) st.textContent = '';
    modal.style.display = 'flex';
    setTimeout(() => inp?.focus(), 50);
}

function cerrarMensajeRapido() {
    const modal = document.getElementById('modal-msg-rapido');
    if (modal) modal.style.display = 'none';
}

async function enviarMensajeRapido() {
    const inp    = document.getElementById('msg-rapido-texto');
    const btn    = document.getElementById('msg-rapido-btn');
    const status = document.getElementById('msg-rapido-status');
    const setS   = (msg, cls) => { if (status) { status.textContent = msg; status.className = `text-xs min-h-4 ${cls}`; } };
    const texto  = (inp?.value || '').trim();
    if (!texto) { setS('Escribe un mensaje.', 'text-red-500'); return; }
    if (btn) btn.disabled = true;
    try {
        // Reutiliza el envío inmediato del servidor (mismo de "Probar ahora").
        const res = await fetch(`${API_BASE}/mensajes/probar`, {
            method: 'POST',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ mensaje: texto, hora_envio: '00:00', tipo: 'extra' }),
        });
        const data = await res.json();
        if (!res.ok) { setS(data.detail || 'Error al enviar.', 'text-red-500'); return; }
        const n = data.entregados ?? 0;
        if (n > 0) {
            mostrarNotificacion(`Mensaje enviado a ${n} PC(s).`, 'ok');
            cerrarMensajeRapido();
        } else {
            setS('Ninguna PC conectada en este momento.', 'text-amber-500');
        }
    } catch (e) {
        setS('Error de conexión.', 'text-red-500');
    } finally {
        if (btn) btn.disabled = false;
    }
}

// ── Indicadores / KPIs ────────────────────────────────────────────────
let _kpiCatalogo = [];          // catálogo completo recibido del servidor
let _kpiActivos  = [];          // claves activas (en orden)
let _kpiPuedeEditar = false;
let _kpiPestana = 'equipos';    // pestaña activa: 'equipos' | 'usuarios' | 'programas'

// Iconos por categoría (Phosphor) — decorativo, no funcional
const _KPI_ICONOS = {
    'Equipos':   'ph-desktop',
    'Alumnos':   'ph-users',
    'Seguridad': 'ph-shield-warning',
    'Sistema':   'ph-gear-six',
};

// Pestaña de cada KPI según el catálogo (fallback 'usuarios' si el server es viejo)
function _kpiPestanaDe(clave) {
    const k = _kpiPorClave(clave);
    return (k && k.pestana) || 'usuarios';
}

// Cambia la pestaña activa de Indicadores y reordena lo que se ve.
function kpiSetPestana(p) {
    _kpiPestana = p;
    document.querySelectorAll('.kpi-tab[data-pestana]').forEach(b => {
        const activo = b.dataset.pestana === p;
        b.classList.toggle('kpi-tab-activo', activo);
        b.setAttribute('aria-selected', activo ? 'true' : 'false');
    });
    // Programas: ranking de los más usados (con badge de estado y clasificar).
    // Reusa el selector de período de la zona de gráficos, pero oculta las
    // tarjetas de gráfico y la grilla de KPIs (que no aplican aquí).
    const esProgramas = (p === 'programas');
    const zonaGraf = document.getElementById('kpi-graf-zona');
    const ranking  = document.getElementById('kpi-programas-zona');
    const grid     = document.getElementById('kpi-grid');
    // La zona de gráficos queda visible siempre (alberga el selector de período);
    // sus tarjetas internas se muestran/ocultan por pestaña abajo.
    if (zonaGraf) zonaGraf.style.display = '';
    if (ranking)  ranking.style.display  = esProgramas ? '' : 'none';
    if (grid)     grid.style.display     = esProgramas ? 'none' : '';
    // Mostrar solo el gráfico que corresponde a esta pestaña (ninguno en Programas).
    document.querySelectorAll('.kpi-graf-card[data-graf-pestana]').forEach(c => {
        c.style.display = (!esProgramas && c.dataset.grafPestana === p) ? '' : 'none';
    });
    // El título "Gráficos" no aplica en Programas (solo queda el período).
    const grafTitulo = document.querySelector('.kpi-graf-titulo');
    if (grafTitulo) grafTitulo.textContent = esProgramas ? 'Período' : 'Gráficos';
    if (esProgramas) { _kpiCargarRanking(); return; }
    _renderKpiGrid();
    cargarGraficos();
}

async function cargarIndicadores() {
    const grid = document.getElementById('kpi-grid');
    try {
        const res = await fetch(`${API_BASE}/kpis`, { headers: authHeaders() });
        if (!res.ok) { if (grid) grid.innerHTML = '<p class="kpi-empty">No se pudieron cargar los indicadores.</p>'; return; }
        const data = await res.json();
        _kpiCatalogo    = data.catalogo || [];
        _kpiActivos     = data.activos  || [];
        _kpiPuedeEditar = !!data.puede_editar;
        // Aplica la pestaña activa (también pinta grilla + gráfico correctos)
        kpiSetPestana(_kpiPestana);
    } catch (e) {
        if (grid) grid.innerHTML = '<p class="kpi-empty">Error de conexión.</p>';
    }
}

// ── Gráficos (SVG a mano, sin librería — funciona offline) ─────────────
let _kpiPeriodo = 'semana';
let _kpiRango = { desde: '', hasta: '' };

function kpiSetPeriodo(p) {
    _kpiPeriodo = p;
    document.querySelectorAll('.kpi-periodo-btn[data-periodo]').forEach(b => {
        b.classList.toggle('kpi-periodo-activo', b.dataset.periodo === p);
    });
    const campos = document.getElementById('kpi-rango-campos');
    if (campos) campos.style.display = (p === 'rango') ? '' : 'none';
    if (p !== 'rango') cargarGraficos();
}

function kpiAplicarRango() {
    const d = document.getElementById('kpi-rango-desde')?.value;
    const h = document.getElementById('kpi-rango-hasta')?.value;
    if (!d || !h) { mostrarNotificacion('Elegí ambas fechas', 'error'); return; }
    if (h < d)    { mostrarNotificacion('La fecha final no puede ser anterior a la inicial', 'error'); return; }
    _kpiRango = { desde: d, hasta: h };
    cargarGraficos();
}

function _kpiParams() {
    const p = new URLSearchParams({ periodo: _kpiPeriodo });
    if (_kpiPeriodo === 'rango') { p.set('desde', _kpiRango.desde); p.set('hasta', _kpiRango.hasta); }
    return p.toString();
}

async function cargarGraficos() {
    if (_kpiPeriodo === 'rango' && (!_kpiRango.desde || !_kpiRango.hasta)) return;
    const params = _kpiParams();
    // Solo se carga el gráfico de la pestaña activa (Programas no tiene gráfico aún).
    if (_kpiPestana === 'equipos') {
        // Uso por facultad (horizontal)
        _kpiCargarUno(`${API_BASE}/kpis/grafico/facultades?${params}`, 'graf-facultades',
            d => _svgBarras(d.items.map(i => ({ etiqueta: i.facultad, valor: i.valor, full: i.facultad })), 'Sesiones', true));
    } else if (_kpiPestana === 'usuarios') {
        // Alumnos nuevos registrados por día (horizontal)
        _kpiCargarUno(`${API_BASE}/kpis/grafico/nuevos?${params}`, 'graf-nuevos',
            d => _svgBarras(d.items.map(i => ({ etiqueta: _kpiFechaCorta(i.fecha), valor: i.valor, full: i.fecha })), 'Nuevos', true));
    } else if (_kpiPestana === 'programas') {
        // Ranking de programas más usados (respeta el período).
        _kpiCargarRanking();
    }
}

// ── Ranking de programas (pestaña Programas de Indicadores) ────────────
async function _kpiCargarRanking() {
    if (_kpiPeriodo === 'rango' && (!_kpiRango.desde || !_kpiRango.hasta)) return;
    const cont = document.getElementById('kpi-programas-lista');
    if (!cont) return;
    cont.innerHTML = '<p class="kpi-empty">Cargando…</p>';
    try {
        const res = await fetch(`${API_BASE}/kpis/programas-ranking?${_kpiParams()}&limite=15`, { headers: authHeaders() });
        if (!res.ok) { cont.innerHTML = '<p class="kpi-empty">No se pudo cargar el ranking.</p>'; return; }
        const data = await res.json();
        _kpiRenderRanking(data.items || []);
    } catch (e) {
        cont.innerHTML = '<p class="kpi-empty">Error de conexión.</p>';
    }
}

function _kpiRenderRanking(items) {
    const cont = document.getElementById('kpi-programas-lista');
    if (!cont) return;
    if (!items.length) {
        cont.innerHTML = '<p class="kpi-empty">Sin actividad de programas en este período.</p>';
        return;
    }
    const esSuper = _rolServidor === 'superadmin';
    const maxAlum = Math.max(...items.map(i => i.alumnos || 0), 1);
    cont.innerHTML = items.map((it, i) => {
        const pct = Math.round(((it.alumnos || 0) / maxAlum) * 100);
        const sinClasif = it.estado === 'sin_clasificar';
        const badge = sinClasif
            ? '<span class="prog-badge prog-badge-pend">Sin clasificar</span>'
            : (it.categoria ? `<span class="prog-badge prog-badge-cat">${escapeHtml(it.categoria)}</span>` : '');
        // Botón Clasificar: solo superadmin y solo en filas sin clasificar.
        const accion = (sinClasif && esSuper)
            ? `<button class="prog-clasificar" onclick="bancoClasificarDesdeRanking('${escapeHtml(it.exe)}')" title="Clasificar este programa">
                 <i class="ph ph-tag"></i> Clasificar
               </button>`
            : '';
        return `
            <div class="prog-row${sinClasif ? ' prog-row-pend' : ''}">
                <span class="prog-rank">${i + 1}</span>
                <div class="prog-info">
                    <div class="prog-top">
                        <span class="prog-nombre">${escapeHtml(it.nombre || it.exe)}</span>
                        ${badge}
                    </div>
                    <div class="prog-bar-wrap">
                        <div class="prog-bar" style="width:${pct}%"></div>
                    </div>
                </div>
                <div class="prog-metricas">
                    <span class="prog-alumnos"><strong>${it.alumnos || 0}</strong> alumno${(it.alumnos === 1) ? '' : 's'}</span>
                    <span class="prog-veces">${it.veces || 0} apertura${(it.veces === 1) ? '' : 's'}</span>
                </div>
                ${accion}
            </div>`;
    }).join('');
}

async function _kpiCargarUno(url, contId, render) {
    const cont = document.getElementById(contId);
    if (!cont) return;
    try {
        const res = await fetch(url, { headers: authHeaders() });
        if (!res.ok) { cont.innerHTML = '<p class="kpi-empty">No disponible.</p>'; return; }
        const data = await res.json();
        if (!data.items || !data.items.length || data.items.every(i => i.valor === 0)) {
            cont.innerHTML = '<p class="kpi-empty">Sin datos en este período.</p>';
            return;
        }
        cont.innerHTML = render(data);
    } catch (e) {
        cont.innerHTML = '<p class="kpi-empty">Error de conexión.</p>';
    }
}

function _kpiFechaCorta(iso) {
    // "2026-06-17" -> "Mié 17/06" (abreviación del día + fecha)
    const [a, m, d] = iso.split('-');
    // Mediodía local para que el día de la semana no se corra por zona horaria.
    const dia = new Date(Number(a), Number(m) - 1, Number(d), 12);
    const dias = ['Dom', 'Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb'];
    const abrev = dias[dia.getDay()] || '';
    return `${abrev} ${d}/${m}`;
}

// Genera un gráfico de barras en SVG. items: [{etiqueta, valor, full}]
// horizontal=true para barras horizontales (mejor con muchas etiquetas largas, ej. facultades)
// Gráfico de barras con HTML + CSS (flexbox), no SVG. Para barras simples es la
// práctica recomendada: altura fija y ancho fluido salen gratis del layout, y el
// texto es DOM real (nunca se deforma, escala y es accesible). La altura/ancho de
// cada barra es un % relativo al máximo; el navegador hace el resto.
function _svgBarras(items, unidad, horizontal = false) {
    const max = Math.max(...items.map(i => i.valor), 1);
    if (horizontal) {
        // Barras horizontales: filas [etiqueta | barra | valor].
        const filas = items.map(it => {
            const pct = Math.round((it.valor / max) * 100);
            return `
                <div class="kpi-bar-row" title="${esc(it.full)}: ${it.valor}">
                    <span class="kpi-bar-rlbl">${esc(_kpiTrunc(it.etiqueta, 24))}</span>
                    <div class="kpi-bar-rtrack">
                        <div class="kpi-bar-rfill" style="width:${Math.max(pct, 2)}%"></div>
                    </div>
                    <span class="kpi-bar-rval">${it.valor}</span>
                </div>`;
        }).join('');
        return `<div class="kpi-bars kpi-bars-h" role="img" aria-label="Gráfico de barras: ${esc(unidad)}">${filas}</div>`;
    } else {
        // Barras verticales: columnas [valor arriba | barra | etiqueta abajo].
        // Si las etiquetas son largas (ej. nombres de facultad) o hay muchas, se
        // inclinan para que no se encimen; si son cortas (fechas) van rectas.
        const largas = items.some(i => (i.etiqueta || '').length > 6) || items.length > 8;
        const cols = items.map(it => {
            const pct = Math.round((it.valor / max) * 100);
            return `
                <div class="kpi-bar-col" title="${esc(it.full)}: ${it.valor}">
                    <span class="kpi-bar-cval">${it.valor > 0 ? it.valor : ''}</span>
                    <div class="kpi-bar-ctrack">
                        <div class="kpi-bar-cfill" style="height:${it.valor > 0 ? Math.max(pct, 2) : 0}%"></div>
                    </div>
                    <span class="kpi-bar-clbl">${esc(it.etiqueta)}</span>
                </div>`;
        }).join('');
        return `<div class="kpi-bars kpi-bars-v${largas ? ' kpi-bars-v-rot' : ''}" role="img" aria-label="Gráfico de barras: ${esc(unidad)}">${cols}</div>`;
    }
}

function _kpiTrunc(s, n) { return s.length > n ? s.slice(0, n - 1) + '…' : s; }

function _kpiPorClave(clave) {
    return _kpiCatalogo.find(k => k.clave === clave);
}

function _renderKpiGrid() {
    const grid = document.getElementById('kpi-grid');
    if (!grid) return;
    // Solo los activos que pertenecen a la pestaña actual (conservando el orden).
    const dePestana = _kpiActivos.filter(c => _kpiPorClave(c) && _kpiPestanaDe(c) === _kpiPestana);
    if (!dePestana.length) {
        grid.innerHTML = '<p class="kpi-empty">No hay indicadores activos en esta pestaña. ' +
            (_kpiPuedeEditar ? 'Usa "Personalizar" para agregar.' : 'Pídele al superadmin que configure el tablero.') + '</p>';
        return;
    }
    grid.innerHTML = dePestana.map(clave => {
        const k = _kpiPorClave(clave);
        if (!k) return '';
        const icono = _KPI_ICONOS[k.categoria] || 'ph-chart-bar';
        return `
            <div class="kpi-card">
                <div class="kpi-card-top">
                    <span class="kpi-card-cat">${esc(k.categoria)}</span>
                    <i class="ph ${icono} kpi-card-ico" aria-hidden="true"></i>
                </div>
                <div class="kpi-card-val">${esc(k.valor_fmt)}</div>
                <div class="kpi-card-lbl">${esc(k.etiqueta)}</div>
            </div>`;
    }).join('');
}

// ── Editor (solo superadmin) ──
// Pestañas que se muestran en el editor (Programas se omite hasta que tenga KPIs).
const _KPI_PESTANAS = [
    { clave: 'equipos',  nombre: 'Equipos',  icono: 'ph-desktop' },
    { clave: 'usuarios', nombre: 'Usuarios', icono: 'ph-users' },
];

function kpiAbrirEditor() {
    if (!_kpiPuedeEditar) return;
    const cont = document.getElementById('kpi-editor-lista');
    if (!cont) return;

    // Un grupo por pestaña. Dentro de cada grupo: primero los activos (en su
    // orden guardado), luego los inactivos. El drag-drop solo mueve dentro del grupo.
    cont.innerHTML = _KPI_PESTANAS.map(pest => {
        const delGrupo  = _kpiCatalogo.filter(k => (k.pestana || 'usuarios') === pest.clave).map(k => k.clave);
        if (!delGrupo.length) return '';
        const activos   = _kpiActivos.filter(c => delGrupo.includes(c));
        const inactivos = delGrupo.filter(c => !activos.includes(c));
        const orden     = [...activos, ...inactivos];
        const filas = orden.map(clave => {
            const k = _kpiPorClave(clave);
            if (!k) return '';
            const activo = _kpiActivos.includes(clave);
            return `
                <div class="kpi-edit-row" draggable="true" data-clave="${esc(clave)}">
                    <i class="ph ph-dots-six-vertical kpi-edit-drag" aria-hidden="true"></i>
                    <label class="kpi-edit-check">
                        <input type="checkbox" ${activo ? 'checked' : ''} aria-label="Mostrar ${esc(k.etiqueta)}">
                        <span class="kpi-edit-txt">
                            <span class="kpi-edit-nombre">${esc(k.etiqueta)}</span>
                            <span class="kpi-edit-cat">${esc(k.categoria)}</span>
                        </span>
                    </label>
                </div>`;
        }).join('');
        return `
            <div class="kpi-edit-grupo">
                <h4 class="kpi-edit-grupo-titulo"><i class="ph ${pest.icono}" aria-hidden="true"></i> ${esc(pest.nombre)}</h4>
                <div class="kpi-edit-grupo-lista" data-pestana="${esc(pest.clave)}">${filas}</div>
            </div>`;
    }).join('');

    // El drag-drop se activa por cada grupo de forma independiente.
    cont.querySelectorAll('.kpi-edit-grupo-lista').forEach(g => _kpiActivarDragDrop(g));
    const modal = document.getElementById('modal-kpi');
    if (modal) modal.style.display = 'flex';
}

function kpiCerrarEditor() {
    const modal = document.getElementById('modal-kpi');
    if (modal) modal.style.display = 'none';
}

function _kpiActivarDragDrop(cont) {
    let arrastrado = null;
    cont.querySelectorAll('.kpi-edit-row').forEach(row => {
        row.addEventListener('dragstart', () => { arrastrado = row; row.classList.add('kpi-edit-dragging'); });
        row.addEventListener('dragend',   () => { arrastrado = null; row.classList.remove('kpi-edit-dragging'); });
        row.addEventListener('dragover', (e) => {
            e.preventDefault();
            if (!arrastrado || arrastrado === row) return;
            const rect = row.getBoundingClientRect();
            const despues = (e.clientY - rect.top) > rect.height / 2;
            cont.insertBefore(arrastrado, despues ? row.nextSibling : row);
        });
    });
}

async function kpiGuardarConfig() {
    const cont = document.getElementById('kpi-editor-lista');
    const btn  = document.getElementById('kpi-btn-guardar');
    if (!cont) return;
    // Recolectar en el orden actual del DOM, solo los marcados
    const nuevos = [];
    cont.querySelectorAll('.kpi-edit-row').forEach(row => {
        const chk = row.querySelector('input[type=checkbox]');
        if (chk && chk.checked) nuevos.push(row.dataset.clave);
    });
    if (btn) btn.disabled = true;
    try {
        const res = await fetch(`${API_BASE}/kpis/config`, {
            method: 'PUT',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ activos: nuevos }),
        });
        const data = await res.json();
        if (!res.ok) { mostrarNotificacion(data.detail || 'Error al guardar', 'error'); return; }
        _kpiActivos = data.activos || nuevos;
        _renderKpiGrid();
        kpiCerrarEditor();
        mostrarNotificacion('Indicadores actualizados', 'ok');
    } catch (e) {
        mostrarNotificacion('Error de conexión', 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
}

// Al cargar la página: aplicar logo/textos personalizados a la pantalla de login,
// rellenar los campos de Configuración, y restaurar la sesión si hay token (F5).
document.addEventListener('DOMContentLoaded', () => {
    _aplicarConfigApp();          // 1) pinta de inmediato lo cacheado (sin parpadeo)
    _cargarCamposConfigApp();
    _cargarAparienciaServidor();  // 2) refresca desde el servidor (config global)
    _aplicarTooltipsMenu();    // tooltips de los iconos del menú en franja
    _restaurarSesion();
});
