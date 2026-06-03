// Detectar si se abre como archivo local o desde el servidor

const _isFile = window.location.protocol === 'file:';

const API_BASE  = _isFile ? 'http://localhost:8000/api'   : window.location.origin + '/api';

const WS_BASE   = _isFile ? 'ws://localhost:8000'         : `ws://${window.location.host}`;



let token   = null;

let wsAdmin = null;

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
    if (stats) stats.style.display = 'grid';

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

    ['actividad', 'credenciales', 'aplicacion', 'actualizaciones'].forEach(s => {
        const btn = document.getElementById('subtab-btn-cfg-' + s);
        if (btn) btn.style.display = esSuperAdmin ? '' : 'none';
    });

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

        // Decodificar rol del servidor desde el JWT (sin verificar firma — solo lectura UI)
        try {
            const payload = JSON.parse(atob(token.split('.')[1]));
            _rolServidor = payload.rol || 'admin';
        } catch(e) { _rolServidor = 'admin'; }

        document.getElementById('usuarioActual').textContent = username;
        const rolEl = document.getElementById('usuarioRol');
        if (rolEl) rolEl.textContent = _rolServidor === 'superadmin' ? 'Super Admin' : 'Admin';

        document.getElementById('loginPanel').style.display  = 'none';

        document.getElementById('dashboard').style.display   = 'flex';

        document.body.classList.remove('login-screen');

        btn.disabled = false;

        btn.textContent = 'Iniciar Sesión';



        _aplicarRol(); // aplica visibilidad según rol del JWT

        _aplicarConfigApp(); // aplica logo y textos personalizados si existen
        _poblarAnios();      // popula selector de año en historial
        cargarSospechas();   // carga badge de sospechas pendientes



        // Obtener y mostrar IP real del servidor

        await obtenerYMostrarIpServidor();



        addLog('activity', `Login exitoso como '${username}'`);

        addLog('activity', `API: ${API_BASE}`);

        addLog('activity', `WS: ${WS_BASE}/ws/admin`);

        cargarDashboard();

        conectarWebSocket();

        setInterval(cargarDashboard, 15000);

        _cargarResumenIncidencias();
        _cargarEventosMini();

        setInterval(_cargarResumenIncidencias, 60000);
        setInterval(_cargarEventosMini, 30000);

    } catch (e) {

        errorEl.textContent = 'No se pudo conectar al servidor (¿está corriendo en :8000?)';

        addLog('error', `Login fallido: ${e.message || 'sin conexión'}`);

        btn.disabled = false;

        btn.textContent = 'Iniciar Sesión';

    }

}



async function obtenerYMostrarIpServidor() {

    try {

        const res = await fetch(`${API_BASE}/server-info`, { cache: 'no-store' });

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



    wsAdmin = new WebSocket(`${WS_BASE}/ws/admin?token=${encodeURIComponent(token)}`);



    wsAdmin.onopen = () => {

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

            addLog('activity', `ℹ️ ${data.motivo}`);

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

    addLog('activity', `Enviando WS: ${payload.tipo} ${payload.ip ? '→ ' + payload.ip : ''}`);

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

        

        // Buscar sesión activa para esta terminal

        const sesion = sesiones.find(s => s.terminal_ip === t.ip);



        let botonesPrimarios = '';

        const pcNombre = esc(t.nombre || t.ip);

        const alumnoNombre = sesion ? esc(sesion.alumno_nombre) : '';



        if (t.nombre === 'IMPORTADO') {

            botonesPrimarios = `<p style="font-size:11px;color:var(--text-muted);text-align:center;margin:4px 0;line-height:1.5">Terminal virtual — necesaria para el historial importado desde Excel</p>`;

        } else if (!online) {

            const btnEliminar = _rolServidor === 'superadmin'
                ? `<button class="btn-card-eliminar-terminal" onclick="eliminarTerminalFantasma(${t.id}, '${pcNombre}')"><i class="ph ph-trash"></i> Eliminar</button>`
                : '';

            botonesPrimarios = `

                <button class="btn-card-apagar" onclick="apagarPc('${esc(t.ip)}', ${sesion ? sesion.id : 'null'}, '${pcNombre}', '${alumnoNombre}')"<i class="ph ph-power"></i> Apagar PC</button>
                ${btnEliminar}

            `;

        } else if (faltaDesbloqueo) {

            botonesPrimarios = `

                <button class="btn-card-desbloquear" onclick="mostrarModalDesbloqueo('${esc(t.ip)}', '${pcNombre}')"><i class="ph ph-lock-open"></i> Desbloquear</button>

                <button class="btn-card-apagar" onclick="apagarPc('${esc(t.ip)}', ${sesion ? sesion.id : 'null'}, '${pcNombre}', '${alumnoNombre}')"<i class="ph ph-power"></i> Apagar PC</button>

            `;

        } else {

            botonesPrimarios = `

                <button class="btn-card-bloquear" onclick="bloquearTerminal('${esc(t.ip)}', '${pcNombre}', '${alumnoNombre}')"><i class="ph ph-lock"></i> Bloquear</button>

                <button class="btn-card-apagar" onclick="apagarPc('${esc(t.ip)}', ${sesion ? sesion.id : 'null'}, '${pcNombre}', '${alumnoNombre}')"<i class="ph ph-power"></i> Apagar PC</button>

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

    sesiones.filter(s => s.activa).forEach(s =>

        addLog('activity', `[ID] ${s.alumno_nombre} | Código: ${s.alumno_codigo} | DNI: ${s.alumno_dni || s.dni || '—'}`)

    );

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



    body.innerHTML = data.alumnos.map(a => `
        <tr>
            <td><code>${esc(a.dni)}</code></td>
            <td>${esc(a.nombre)}</td>
            <td>${esc(a.codigo || '—')}</td>
            <td style="font-size:12px">${esc(a.facultad || '—')}</td>
            <td style="font-size:12px">${esc(a.escuela  || '—')}</td>
            <td>
                <div style="display:flex;gap:4px;align-items:center">
                    ${esSuperAdmin ? `<button title="Editar" class="tbl-btn tbl-btn-edit"
                        onclick="abrirEditarMaestro('${esc(a.dni)}','${esc(a.nombre)}','${esc(a.codigo||'')}','${esc(a.facultad||'')}','${esc(a.escuela||'')}')"><i class="ph ph-pencil-simple"></i></button>` : ''}
                    <button title="Registrar incidencia" class="tbl-btn tbl-btn-warn"
                        onclick="abrirNuevaIncidencia('${esc(a.dni)}')"><i class="ph ph-warning-circle"></i></button>
                    ${esSuperAdmin ? `<button title="Banear" class="tbl-btn tbl-btn-ban"
                        onclick="abrirBanearUsuario('${esc(a.dni)}','${esc(a.nombre)}')"><i class="ph ph-prohibit"></i></button>` : ''}
                </div>
            </td>
        </tr>`).join('');



    // Paginación simple

    if (pag) {

        const totalPages = Math.ceil(data.total / _maestroLimit);
        const curPage    = Math.floor(_maestroOffset / _maestroLimit);
        pag.innerHTML = _renderPagHtml(curPage, totalPages, 'irPaginaMaestro');

    }

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
        [badge, document.getElementById('incidencias-badge-sub')].forEach(b => {
            if (!b) return;
            if (total > 0) { b.textContent = total; b.style.display = ''; }
            else b.style.display = 'none';
        });

        // Poblar mini-panel de alertas en el hero del tab Monitoreo
        _renderMonAlertasMini(data);
    } catch (e) {
        // silencioso
    }
}

function _renderMonAlertasMini(data) {
    const el = document.getElementById('monAlertasMini');
    if (!el) return;
    const activas = data.filter(r => r.total > 0).slice(0, 4);
    if (!activas.length) {
        el.innerHTML = '<p class="mon-alerts-empty">Sin incidencias activas</p>';
        return;
    }
    el.innerHTML = activas.map(r => {
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
        const res = await fetch(`${API_BASE}/admin/actividad?limit=4&offset=0`, { headers: authHeaders() });
        if (!res.ok) return;
        const data = await res.json();
        const items = data.items || [];
        const el = document.getElementById('monEventosMini');
        if (!el) return;
        if (!items.length) {
            el.innerHTML = '<p class="mon-alerts-empty">Sin eventos recientes</p>';
            return;
        }
        el.innerHTML = items.map(r => {
            const hora = r.fecha_hora ? new Date(r.fecha_hora).toLocaleTimeString('es-PE', {hour:'2-digit', minute:'2-digit', second:'2-digit'}) : '—';
            const isCrit = r.nivel === 'sospechoso';
            return `<div class="mon-event-item${isCrit ? ' critical' : ''}">
                <div class="mon-event-time">${hora} · ${escapeHtml(r.nombre_terminal || '—')}</div>
                <div class="mon-event-desc">${escapeHtml(r.descripcion || r.tipo || '—')}</div>
            </div>`;
        }).join('');
    } catch(e) { /* silencioso */ }
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
            <td class="p-4 text-xs font-mono">${escapeHtml(i.dni)}</td>
            <td class="p-4 text-xs font-medium">${escapeHtml(i.nombre_alumno)}</td>
            <td class="p-4"><span style="${tipoBg};${tipoCls};border-radius:6px;padding:2px 10px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em">${escapeHtml(i.tipo)}</span></td>
            <td class="p-4 text-xs">${escapeHtml(i.motivo)}</td>
            <td class="p-4 text-xs text-slate-500 dark:text-slate-400 max-w-xs truncate" title="${escapeHtml(i.descripcion || '')}">${escapeHtml(i.descripcion || '—')}</td>
            <td class="p-4 text-xs">${fecha}</td>
            <td class="p-4 text-xs">${escapeHtml(i.registrado_por)}</td>
            <td class="p-4">${estadoBadge}</td>
            <td class="p-4">
                ${i.activa ? `<button onclick="eliminarIncidencia(${i.id},'${escapeHtml(i.nombre_alumno)}')" class="text-rose-400 hover:text-rose-600 dark:text-rose-500 dark:hover:text-rose-300 transition-colors" title="Eliminar incidencia"><i class="ph ph-trash text-base"></i></button>` : ''}
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
    if (password && password.length < 6) {
        errEl.textContent = 'La contraseña debe tener al menos 6 caracteres';
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

function _aplicarConfigApp() {
    const logo      = localStorage.getItem(_CFG_LOGO_KEY);
    const titulo    = localStorage.getItem(_CFG_TITULO_KEY);
    const subtitulo = localStorage.getItem(_CFG_SUBTITULO_KEY);
    const footer    = localStorage.getItem(_CFG_FOOTER_KEY);

    // Logo sidebar
    const sidebarIconWrap = document.querySelector('.sidebar .flex.items-center.gap-3 i.ph-books')?.parentElement?.parentElement;
    if (logo && sidebarIconWrap) {
        const existing = sidebarIconWrap.querySelector('img.cfg-logo-sidebar');
        if (!existing) {
            const img = document.createElement('img');
            img.className = 'cfg-logo-sidebar w-8 h-8 rounded-lg object-contain';
            sidebarIconWrap.querySelector('i')?.replaceWith(img);
        }
        sidebarIconWrap.querySelector('img.cfg-logo-sidebar').src = logo;
    }

    // Logo login
    const loginIconWrap = document.querySelector('.login-icon-wrap');
    if (logo && loginIconWrap) {
        loginIconWrap.innerHTML = `<img src="${logo}" class="w-12 h-12 rounded-xl object-contain" alt="Logo">`;
    }

    // Textos login
    const loginTitle = document.querySelector('.login-title');
    const loginSub   = document.querySelector('.login-subtitle');
    const loginFoot  = document.querySelector('.login-footer p');
    if (titulo    && loginTitle) loginTitle.textContent = titulo;
    if (subtitulo && loginSub)   loginSub.innerHTML     = subtitulo;
    if (footer    && loginFoot)  loginFoot.innerHTML    = footer;
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

function guardarLogo() {
    if (!_logoDataUrl) return;
    localStorage.setItem(_CFG_LOGO_KEY, _logoDataUrl);
    _aplicarConfigApp();
    mostrarNotificacion('Logo aplicado correctamente', 'ok');
    document.getElementById('btn-guardar-logo').style.display = 'none';
    _logoDataUrl = null;
}

function guardarTextosApp() {
    const titulo    = document.getElementById('cfg-login-titulo').value.trim();
    const subtitulo = document.getElementById('cfg-login-subtitulo').value.trim();
    const footer    = document.getElementById('cfg-login-footer').value.trim();
    const errEl     = document.getElementById('cfg-app-error');
    errEl.textContent = '';
    if (!titulo && !subtitulo && !footer) { errEl.textContent = 'Completa al menos un campo'; return; }
    if (titulo)    localStorage.setItem(_CFG_TITULO_KEY, titulo);
    if (subtitulo) localStorage.setItem(_CFG_SUBTITULO_KEY, subtitulo);
    if (footer)    localStorage.setItem(_CFG_FOOTER_KEY, footer);
    _aplicarConfigApp();
    mostrarNotificacion('Textos actualizados correctamente', 'ok');
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
        if (status) status.textContent = `✓ Descargado: ${name}`;
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

        const btnVer = `<button onclick="actVerDesdeSospecha('${pcSosp}', '${s.dni}', '${s.fecha || ''}')"
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
            : `<span class="text-xs text-slate-400">${s.revisado_por || '—'}</span>${btnVer}`;

        return `<tr class="hover:bg-violet-50/50 dark:hover:bg-violet-900/10 transition-colors">
            <td class="p-4">
                <div class="font-semibold text-slate-800 dark:text-slate-200 text-xs">${s.nombre_alumno}</div>
                <div class="text-[11px] text-slate-400 dark:text-slate-500">${s.dni}</div>
            </td>
            <td class="p-4">
                <span class="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300">${tipoLbl}</span>
            </td>
            <td class="p-4 text-xs text-slate-600 dark:text-slate-400 max-w-xs">${s.detalle}</td>
            <td class="p-4 text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">${fecha}</td>
            <td class="p-4">
                <span class="px-2 py-0.5 rounded-full text-[11px] font-semibold ${pillCls}">${s.estado}</span>
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
        if (n > 0) { badge.textContent = n > 99 ? '99+' : n; badge.style.display = ''; }
        else badge.style.display = 'none';
    } catch(e) { /* silencioso */ }
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
        if (status) { status.textContent = '✓ Ruta guardada correctamente.'; status.className = 'text-xs text-emerald-500 min-h-4'; }
        setTimeout(() => { if (status) status.textContent = ''; }, 3000);
    } catch(e) {
        if (status) { status.textContent = 'Error de conexión.'; status.className = 'text-xs text-red-500 min-h-4'; }
    }
}

// ═══════════════════════════════════════════════════════════════════
// CONFIGURACIÓN — PUBLICAR NUEVA VERSIÓN DEL CLIENTE
// ═══════════════════════════════════════════════════════════════════

async function publicarCliente() {
    const inputVer     = document.getElementById('cfg-publicar-version');
    const inputFile    = document.getElementById('cfg-publicar-archivo');
    const status       = document.getElementById('cfg-publicar-status');
    const btn          = document.getElementById('cfg-publicar-btn');
    const progressWrap = document.getElementById('cfg-publicar-progress');
    const progressBar  = document.getElementById('cfg-publicar-bar');

    const version = (inputVer?.value || '').trim();
    const file    = inputFile?.files?.[0];

    const setStatus = (txt, color) => {
        if (status) { status.textContent = txt; status.className = `text-xs min-h-4 ${color}`; }
    };

    if (!version)                         { setStatus('Especifica una versión.', 'text-red-500'); return; }
    if (!file)                            { setStatus('Selecciona el archivo .exe.', 'text-red-500'); return; }
    if (!file.name.toLowerCase().endsWith('.exe')) { setStatus('El archivo debe ser un .exe.', 'text-red-500'); return; }
    if (file.size < 1024 * 1024)          { setStatus('Archivo demasiado pequeño (<1MB).', 'text-red-500'); return; }

    if (!confirm(`¿Publicar versión ${version}? Los kioskos la aplicarán al próximo reinicio.`)) return;

    const fd = new FormData();
    fd.append('version', version);
    fd.append('archivo', file);

    btn.disabled = true;
    btn.classList.add('opacity-60', 'cursor-not-allowed');
    progressWrap.classList.remove('hidden');
    progressBar.style.width = '0%';
    setStatus(`Subiendo ${(file.size / 1024 / 1024).toFixed(1)} MB...`, 'text-slate-500');

    // Usamos XHR en vez de fetch para poder mostrar progreso real de subida
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API_BASE}/admin/publicar-cliente`);
    const headers = authHeaders();
    for (const k in headers) xhr.setRequestHeader(k, headers[k]);

    xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
            const pct = (e.loaded / e.total) * 100;
            progressBar.style.width = pct.toFixed(1) + '%';
            setStatus(`Subiendo ${(e.loaded / 1024 / 1024).toFixed(1)} / ${(e.total / 1024 / 1024).toFixed(1)} MB (${pct.toFixed(0)}%)`, 'text-slate-500');
        }
    };

    xhr.onload = () => {
        btn.disabled = false;
        btn.classList.remove('opacity-60', 'cursor-not-allowed');
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch {}
        if (xhr.status >= 200 && xhr.status < 300) {
            progressBar.style.width = '100%';
            setStatus(`✓ Versión ${data.version} publicada (${(data.tamano_bytes / 1024 / 1024).toFixed(1)} MB). Los kioskos se actualizarán al reiniciar.`, 'text-emerald-500');
            inputFile.value = '';
            setTimeout(() => { progressWrap.classList.add('hidden'); }, 4000);
        } else {
            setStatus(`Error: ${data.detail || xhr.statusText || 'fallo al publicar'}`, 'text-red-500');
            progressWrap.classList.add('hidden');
        }
    };

    xhr.onerror = () => {
        btn.disabled = false;
        btn.classList.remove('opacity-60', 'cursor-not-allowed');
        setStatus('Error de conexión durante la subida.', 'text-red-500');
        progressWrap.classList.add('hidden');
    };

    xhr.send(fd);
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
        setStatus('✓ Configuración guardada correctamente.', 'text-emerald-500');
        setTimeout(() => { if(status) status.textContent = ''; }, 3000);
    } catch (e) {
        setStatus('Error de conexión.', 'text-red-500');
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
    } catch (e) { /* silencioso */ }
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

        setS('✓ Guardado', 'text-emerald-500');
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

        setS('✓ Agregado', 'text-emerald-500');
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
