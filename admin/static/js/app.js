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

// ── Estado de filtros/orden/periodo ───────────────────────────────
let _sortBy      = 'fecha';
let _sortDir     = 'desc';
let _periodo     = 'dia';       // dia | mes | anio | rango | todo
let _fechaInicio = '';
let _fechaFin    = '';

// ── Tema claro / oscuro ───────────────────────────────────────────
function toggleTema() {
    const isLight = document.body.classList.toggle('light-mode');
    localStorage.setItem('tema', isLight ? 'light' : 'dark');
    const btn = document.getElementById('btnTema');
    if (btn) btn.textContent = isLight ? '☀️' : '🌙';
}

(function _initTema() {
    // Marcar body como pantalla de login al cargar
    document.body.classList.add('login-screen');
    if (localStorage.getItem('tema') === 'light') {
        document.body.classList.add('light-mode');
        const btn = document.getElementById('btnTema');
        if (btn) btn.textContent = '☀️';
    }
})();

// ── CONSOLA DE MONITOREO (disponible globalmente) ──────────────────
function addLog(category, message) {
    const time = new Date().toLocaleTimeString('es-PE', { hour12: false });
    const consoleId = category === 'error' ? 'console-errors' : 'console-activity';
    const consoleEl = document.getElementById(consoleId);
    if (!consoleEl) { console.log(`[${category}] ${message}`); return; }
    const div = document.createElement('div');
    div.className = `console-item ${category}`;
    div.innerHTML = `<span class="console-time">[${time}]</span><span class="console-msg">${escapeHtml(message)}</span>`;
    consoleEl.appendChild(div);
    consoleEl.scrollTop = consoleEl.scrollHeight;
    // Limitar a 200 líneas
    while (consoleEl.children.length > 200) consoleEl.removeChild(consoleEl.firstChild);
}
function appendLog(msg, nivel = 'info') { addLog(nivel === 'error' ? 'error' : 'activity', msg); }
function escapeHtml(text) { const d = document.createElement('div'); d.textContent = String(text ?? ''); return d.innerHTML; }
function esc(s) { return escapeHtml(s); }

// ── Autenticación ──────────────────────────────────────────────────

async function login() {
    const username = document.getElementById('username').value.trim();
    const password = document.getElementById('password').value;
    const errorEl  = document.getElementById('loginError');
    errorEl.textContent = '';

    if (!username || !password) { errorEl.textContent = 'Ingrese usuario y contraseña'; return; }

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
            return;
        }

        const data = await res.json();
        token = data.access_token;
        document.getElementById('usuarioActual').textContent = username;
        document.getElementById('loginPanel').style.display  = 'none';
        document.getElementById('dashboard').style.display   = 'block';
        document.body.classList.remove('login-screen');

        // Obtener y mostrar IP real del servidor
        await obtenerYMostrarIpServidor();

        addLog('activity', `✅ Login exitoso como '${username}'`);
        addLog('activity', `🌐 API: ${API_BASE}`);
        addLog('activity', `🔌 WS: ${WS_BASE}/ws/admin`);
        cargarDashboard();
        conectarWebSocket();
        setInterval(cargarDashboard, 15000);
    } catch (e) {
        errorEl.textContent = 'No se pudo conectar al servidor (¿está corriendo en :8000?)';
        addLog('error', `❌ Login fallido: ${e.message || 'sin conexión'}`);
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
                badge.innerHTML = `🌐 <strong>IP SERVIDOR:</strong> <code>${_serverIp}</code>`;
                badge.style.backgroundColor = '#FF9800';
                badge.style.color = '#000000';
                badge.style.fontWeight = 'bold';
                badge.style.padding = '8px 12px';
                badge.style.borderRadius = '4px';
                badge.style.display = 'inline-block';
                badge.style.fontSize = '14px';
                badge.style.whiteSpace = 'nowrap';
            }
            addLog('activity', `✅ IP del servidor: ${_serverIp}`);
        }
    } catch (e) {
        addLog('error', `⚠️ No se pudo obtener IP del servidor: ${e.message}`);
    }
}

function logout() {
    token = null;
    if (wsAdmin) { wsAdmin.onclose = null; wsAdmin.close(); wsAdmin = null; }
    clearTimeout(_reconnectTimer);
    document.getElementById('dashboard').style.display  = 'none';
    document.getElementById('loginPanel').style.display = 'block';
    document.getElementById('username').value = '';
    document.getElementById('password').value = '';
    document.body.classList.add('login-screen');
    setWsStatus(false);
}

// ── REST API ───────────────────────────────────────────────────────

function authHeaders() {
    return { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
}

function _buildParams() {
    const search    = document.getElementById('filtroSearch')?.value.trim()    || '';
    const actividad = document.getElementById('filtroActividad')?.value.trim() || '';
    const params = new URLSearchParams({ sort_by: _sortBy, order: _sortDir, periodo: _periodo });
    if (search)    params.set('search',    search);
    if (actividad) params.set('actividad', actividad);
    if (_periodo === 'rango' && _fechaInicio && _fechaFin) {
        params.set('fecha_inicio', _fechaInicio);
        params.set('fecha_fin',    _fechaFin);
    }
    return params;
}

function _sesionesUrl() {
    return `${API_BASE}/sesiones/activas?${_buildParams()}`;
}

function setPeriodo(p) {
    _periodo = p;
    // Mostrar/ocultar panel de rango
    const panelRango = document.getElementById('panelRango');
    if (panelRango) panelRango.style.display = p === 'rango' ? 'flex' : 'none';
    // Actualizar botón activo
    ['btn-hoy','btn-mes','btn-anio','btn-todo','btn-rango'].forEach(id => {
        const b = document.getElementById(id);
        if (b) b.classList.toggle('btn-periodo-activo', b.dataset.periodo === p);
    });
    aplicarFiltros();
}

async function aplicarFiltros() {
    try {
        const res = await fetch(_sesionesUrl(), { headers: authHeaders(), cache: 'no-store' });
        if (!res.ok) return;
        const sesiones = await res.json();
        renderSesiones(sesiones);
        _actualizarFlechas();
    } catch (e) {
        addLog('error', `❌ Error al filtrar sesiones: ${e.message}`);
    }
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
    document.querySelectorAll('.th-sortable').forEach(th => {
        const arrow = th.querySelector('.sort-arrow');
        if (!arrow) return;
        if (th.dataset.col === _sortBy) {
            arrow.textContent = _sortDir === 'asc' ? ' ▲' : ' ▼';
            th.style.color = '#1d4ed8';
        } else {
            arrow.textContent = '';
            th.style.color = '';
        }
    });
}

function aplicarRango() {
    _fechaInicio = document.getElementById('rangoDesde')?.value || '';
    _fechaFin    = document.getElementById('rangoHasta')?.value || '';
    if (_fechaInicio && _fechaFin) aplicarFiltros();
}

function exportarPdfFiltrado() {
    _descargarArchivo(
        `${API_BASE}/admin/exportar-pdf?${_buildParams()}`,
        '📄 Descargando PDF filtrado...',
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
        if (sesRes.ok)  sesiones  = await sesRes.json();

        if (statsRes.ok) {
            const s = await statsRes.json();
            document.getElementById('totalTerminales').textContent  = s.total_terminales;
            document.getElementById('terminalesActivas').textContent = s.terminales_activas;
            document.getElementById('sesionesActivas').textContent  = s.sesiones_activas;
            document.getElementById('totalAlumnos').textContent     = s.total_alumnos;
        }

        renderTerminales(terminales, sesiones);
        renderSesiones(sesiones);
    } catch (e) {
        addLog('error', `❌ Error cargando dashboard: ${e.message}`);
    }
}

// ── WebSocket ──────────────────────────────────────────────────────

function conectarWebSocket() {
    if (wsAdmin && wsAdmin.readyState === WebSocket.OPEN) return;

    // Si hemos intentado demasiadas veces, esperar más
    if (_reconnectAttempts >= _MAX_RECONNECT_ATTEMPTS) {
        addLog('error', `⚠️ Máximo número de reintentos alcanzado (${_MAX_RECONNECT_ATTEMPTS}). Próximo intento en ${_reconnectDelay/1000}s...`);
    } else if (_reconnectAttempts > 0) {
        addLog('activity', `🔄 Reintentando conexión WebSocket (intento ${_reconnectAttempts}/${_MAX_RECONNECT_ATTEMPTS})...`);
    } else {
        addLog('activity', '🔌 Conectando WebSocket admin...');
    }

    wsAdmin = new WebSocket(`${WS_BASE}/ws/admin`);

    wsAdmin.onopen = () => {
        setWsStatus(true);
        clearTimeout(_reconnectTimer);
        _reconnectAttempts = 0;
        _reconnectDelay = 2000; // Resetear delay
        addLog('activity', '✅ WebSocket admin CONECTADO');
    };

    wsAdmin.onmessage = (event) => {
        let data;
        try { data = JSON.parse(event.data); } catch { return; }

        if (data.tipo === 'status_update') {
            addLog('activity', `📡 Status update: ${data.total} terminal(es) conectada(s) [${(data.terminales||[]).join(', ')}]`);
            cargarDashboard();
        } else if (data.tipo === 'evento_log') {
            const cat = (data.nivel === 'error' || data.nivel === 'offline') ? 'error' : 'activity';
            addLog(cat, data.mensaje);
            if (data.nivel === 'offline' || (data.mensaje && data.mensaje.includes('desconectada'))) {
                cargarDashboard();
            }
        } else if (data.tipo === 'ok') {
            addLog('activity', `✅ Servidor: ${data.mensaje}`);
            mostrarNotificacion(data.mensaje, 'ok');
            // Cerrar modal de desbloqueo si estaba esperando
            if (_pendingUnlockModal) { _pendingUnlockModal.style.display = 'none'; _pendingUnlockModal = null; }
            cargarDashboard();
        } else if (data.tipo === 'info') {
            addLog('activity', `ℹ️ ${data.motivo}`);
        } else if (data.tipo === 'error') {
            addLog('error', `❌ Servidor: ${data.motivo}`);
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
        } else {
            addLog('activity', `📨 WS msg: ${JSON.stringify(data).substring(0, 120)}`);
        }
    };

    wsAdmin.onclose = () => {
        setWsStatus(false);
        _reconnectAttempts++;
        
        // Calcular delay exponencial (cap a 60s)
        const exponentialDelay = Math.min(2000 * Math.pow(1.5, _reconnectAttempts - 1), 60000);
        _reconnectDelay = exponentialDelay;
        
        if (_reconnectAttempts === 1) {
            addLog('error', `🔌 WebSocket admin DESCONECTADO — reintentando en ${_reconnectDelay/1000}s...`);
        } else if (_reconnectAttempts <= _MAX_RECONNECT_ATTEMPTS) {
            addLog('error', `🔌 WebSocket desconectado (intento ${_reconnectAttempts}) — reintentando en ${_reconnectDelay/1000}s...`);
        } else {
            addLog('error', `❌ WebSocket: demasiados intentos. Reintentos pausados. Puedes recargar la página manualmente.`);
            return;
        }
        
        _reconnectTimer = setTimeout(conectarWebSocket, _reconnectDelay);
    };

    wsAdmin.onerror = (e) => {
        addLog('error', '⚠️ Error en WebSocket admin — intentando reconectar...');
        wsAdmin.close();
    };
}

function wsEnviar(payload) {
    if (!wsAdmin || wsAdmin.readyState !== WebSocket.OPEN) {
        addLog('error', `❌ WS no conectado — no se pudo enviar: ${payload.tipo}`);
        mostrarNotificacion('Sin conexión WebSocket con el servidor', 'error');
        return false;
    }
    addLog('activity', `📤 Enviando WS: ${payload.tipo} ${payload.ip ? '→ ' + payload.ip : ''}`);
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
            addLog('activity', `🔒 Confirmado BLOQUEAR terminal: ${ip}`);
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
        errorEl.textContent = '⏳ Verificando en la UNASAM...';
        document.getElementById('btn-desbloqueo-confirmar').disabled = true;
        document.getElementById('btn-desbloqueo-cancelar').disabled  = true;

        addLog('activity', `🔓 Desbloquear terminal: ${ip} | DNI: ${dni} | Actividad: ${razon}`);
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
    mostrarConfirmacion('⚠️ ¿Bloquear TODAS las terminales activas?', () => {
        addLog('activity', '🔒 Botón BLOQUEAR TODAS las terminales');
        wsEnviar({ tipo: 'bloquear_todas' });
    });
}

async function finalizarTodo() {
    mostrarConfirmacion('🏁 ¿CERRAR TODAS LAS SESIONES ACTIVAS?\n\nEsto finalizará el cronómetro de todos los alumnos pero NO borrará los registros históricos.', async () => {
        addLog('activity', '🏁 Botón FINALIZAR TODAS — solicitando cierre masivo...');
        try {
            const res = await fetch(`${API_BASE}/admin/cerrar-todas`, {
                method: 'POST',
                headers: authHeaders(),
                cache: 'no-store'
            });
            
            if (res.ok) {
                const body = await res.json();
                addLog('activity', `✅ Servidor: ${body.mensaje}`);
                mostrarNotificacion('✅ Sesiones finalizadas correctamente', 'ok');
                cargarDashboard();
            } else {
                const err = await res.json();
                addLog('error', `❌ Finalizar todo: HTTP ${res.status} — ${err.detail || 'Fallo'}`);
                mostrarNotificacion('❌ ERROR: ' + (err.detail || 'Fallo al finalizar'), 'error');
            }
        } catch (e) {
            addLog('error', `❌ Error de red al finalizar todo: ${e.message}`);
            mostrarNotificacion('❌ ERROR: Problema de conexión', 'error');
        }
    });
}

async function limpiarTodo() {
    mostrarConfirmacion('⚠️ ¿BORRAR TODO EL SISTEMA?\n\nEsta acción eliminará terminales, alumnos y sesiones permanentemente.', async () => {
        addLog('activity', '🧹 Botón LIMPIAR TODO — solicitando reset total...');
        try {
            const res = await fetch(`${API_BASE}/admin/reset-total`, {
                method: 'DELETE',
                headers: authHeaders(),
                cache: 'no-store'
            });
            
            if (res.ok) {
                const body = await res.json();
                addLog('activity', `✅ Servidor: ${body.mensaje}`);
                mostrarNotificacion('✅ SISTEMA RESETEADO. Recargando...', 'ok');
                setTimeout(() => { location.reload(); }, 1500);
            } else {
                const err = await res.json().catch(() => ({}));
                addLog('error', `❌ Reset total: HTTP ${res.status} — ${err.mensaje || err.detail || 'Fallo'}`);
                mostrarNotificacion('❌ ERROR: ' + (err.detail || 'Fallo al limpiar'), 'error');
            }
        } catch (e) {
            addLog('error', `❌ Error de red al limpiar todo: ${e.message}`);
            mostrarNotificacion('❌ ERROR: Problema de conexión', 'error');
        }
    });
}

async function cerrarSesion(sesionId, motivo = 'admin', silent = false, nombrePc = null, nombreAlumno = null) {
    if (silent) {
        await ejecutarCierre(sesionId, motivo, true);
    } else {
        const pcPart = nombrePc ? ` en [${nombrePc}]` : '';
        const alumnoPart = nombreAlumno ? ` de ${nombreAlumno}` : '';
        mostrarConfirmacion(`⚠️ ¿Estás seguro de finalizar la sesión${alumnoPart}${pcPart}? Esta acción es irreversible.`, async () => {
            await ejecutarCierre(sesionId, motivo, false);
        });
    }
}

async function ejecutarCierre(sesionId, motivo, silent) {
    addLog('activity', `🚪 Cerrando sesión #${sesionId} (motivo: ${motivo})`);
    try {
        const res = await fetch(`${API_BASE}/sesiones/${sesionId}/cerrar?motivo=${motivo}&hora_salida=${encodeURIComponent(obtenerHoraActual())}`, {
            method: 'POST', headers: authHeaders()
        });
        if (res.ok) {
            addLog('activity', `✅ Sesión #${sesionId} cerrada OK`);
            if (!silent) cargarDashboard();
        } else {
            addLog('error', `❌ Error cerrando sesión #${sesionId}: HTTP ${res.status}`);
        }
    } catch (e) {
        addLog('error', `❌ Error de red cerrando sesión: ${e.message}`);
    }
}

async function apagarPc(ip, sesionId = null, nombrePc = null, nombreAlumno = null) {
    const pcLabel = nombrePc || ip;
    const alumnoLine = nombreAlumno ? `\nSe cerrará la sesión de: ${nombreAlumno}` : '';
    mostrarConfirmacion(`⚠️ ¿Confirmas el apagado de [${pcLabel}]?${alumnoLine}`, async () => {
        addLog('activity', `⏻ Botón APAGAR PC: ${ip}`);
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

    grid.innerHTML = terminales.map(t => {
        const inputId = `unlock-${t.ip.replace(/\./g, '-')}`;
        const online  = t.estado !== 'offline';
        const bloqueado = t.estado === 'bloqueado';
        const faltaDesbloqueo = bloqueado && online;
        
        // Buscar sesión activa para esta terminal
        const sesion = sesiones.find(s => s.terminal_ip === t.ip);

        let botonesPrimarios = '';
        const pcNombre = esc(t.nombre || t.ip);
        const alumnoNombre = sesion ? esc(sesion.alumno_nombre) : '';

        if (!online) {
            botonesPrimarios = `
                <button class="btn-card-apagar" onclick="apagarPc('${esc(t.ip)}', ${sesion ? sesion.id : 'null'}, '${pcNombre}', '${alumnoNombre}')">⏻ Apagar PC</button>
            `;
        } else if (faltaDesbloqueo) {
            botonesPrimarios = `
                <button class="btn-card-desbloquear" onclick="mostrarModalDesbloqueo('${esc(t.ip)}', '${pcNombre}')">🔓 Desbloquear</button>
                <button class="btn-card-apagar" onclick="apagarPc('${esc(t.ip)}', ${sesion ? sesion.id : 'null'}, '${pcNombre}', '${alumnoNombre}')">⏻ Apagar PC</button>
            `;
        } else {
            botonesPrimarios = `
                <button class="btn-card-bloquear" onclick="bloquearTerminal('${esc(t.ip)}', '${pcNombre}', '${alumnoNombre}')">🔒 Bloquear</button>
                <button class="btn-card-apagar" onclick="apagarPc('${esc(t.ip)}', ${sesion ? sesion.id : 'null'}, '${pcNombre}', '${alumnoNombre}')">⏻ Apagar PC</button>
            `;
        }

        return `
            <div class="terminal-card ${t.estado}">
                <div class="tc-header">
                    <div class="terminal-nombre">${escapeHtml(t.nombre || t.ip)}</div>
                    <div class="terminal-estado estado-${t.estado}">${estadoLabel(t.estado)}</div>
                </div>
                <div class="tc-info">
                    <div class="terminal-ip"><strong>IP:</strong> ${escapeHtml(t.ip)}</div>
                    <div class="tc-alumno ${sesion ? 'tc-alumno-activo' : ''}">${sesion ? escapeHtml(sesion.alumno_nombre) : 'Disponible'}</div>
                </div>
                <div class="tc-acciones">
                    ${botonesPrimarios}
                </div>
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
    // Log de verificación de identidad del alumno
    sesiones.filter(s => s.activa).forEach(s =>
        addLog('activity', `🎓 [ID] ${s.alumno_nombre} | Código: ${s.alumno_codigo} | DNI: ${s.alumno_dni || s.dni || '—'}`)
    );
    body.innerHTML = sesiones.map(s => {
        const inicio  = new Date(s.inicio).toLocaleTimeString('es-PE');
        const salida  = s.hora_salida_fmt
            ? s.hora_salida_fmt
            : (s.hora_salida
                ? new Date(s.hora_salida).toLocaleTimeString('es-PE')
                : '<span class="pulsar-verde">🟢 En curso...</span>');
        const fecha   = s.fecha_uso
            ? new Date(s.fecha_uso + 'T00:00:00').toLocaleDateString('es-PE')
            : new Date(s.inicio).toLocaleDateString('es-PE');
        const estadoBadge = s.activa
            ? '<span style="color:#16a34a;font-weight:bold">● Activa</span>'
            : '<span style="color:#6b7280">○ Cerrada</span>';
        return `
        <tr style="${s.activa ? '' : 'opacity:0.75'}">
            <td>${esc(s.alumno_nombre)}</td>
            <td>${esc(s.alumno_codigo)}</td>
            <td>${esc(s.alumno_dni || s.dni || '—')}</td>
            <td>${esc(s.facultad || '—')}</td>
            <td>${esc(s.escuela || '—')}</td>
            <td>${esc(s.razon_uso || '—')}</td>
            <td>${inicio}</td>
            <td>${salida}</td>
            <td>${fecha}</td>
            <td>${estadoBadge}</td>
        </tr>`;
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
            addLog('activity', `✅ ${fallbackName.split('.').pop().toUpperCase()} descargado correctamente`);
        })
        .catch(e => {
            addLog('error', `❌ Error al exportar: ${e.message}`);
            mostrarNotificacion('Error al exportar archivo', 'error');
        });
}

function exportarExcel() {
    _descargarArchivo(`${API_BASE}/admin/exportar-excel`, '📊 Descargando historial Excel...', 'historial.xlsx');
}

function exportarPdf() {
    _descargarArchivo(`${API_BASE}/admin/exportar-pdf`, '📄 Descargando historial PDF...', 'historial.pdf');
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

function mostrarConfirmacion(mensaje, onConfirm) {
    const modal = document.getElementById('modal-advertencia');
    document.getElementById('modal-mensaje').textContent = mensaje;
    _rebindBtn('btn-modal-confirmar', () => { modal.style.display = 'none'; onConfirm(); });
    _rebindBtn('btn-modal-cancelar',  () => { modal.style.display = 'none'; });
    modal.style.display = 'flex';
}

function estadoLabel(estado) {
    return { activo: '● Activo', bloqueado: '● Bloqueado', offline: '○ Offline' }[estado] ?? estado;
}

function setWsStatus(ok) {
    const el = document.getElementById('wsStatus');
    if (!el) return;
    el.textContent = ok ? '● WS Conectado' : '○ WS Desconectado';
    el.className   = ok ? 'ws-badge ok' : 'ws-badge off';
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


// escapeHtml y esc definidos al inicio del archivo

// Enter para login
document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && document.getElementById('loginPanel').style.display !== 'none')
        login();
});
