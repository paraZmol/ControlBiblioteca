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

// ── Sistema de Roles (Doble Factor) ──────────────────────────────
// 'asistente' = solo historial | 'admin' = vista completa (requiere clave nivel 2)
// Por seguridad: SIEMPRE inicia en asistente, NO se persiste entre sesiones/recargas
let _rol = 'asistente';
let _hashNivel2 = null; // se obtiene del servidor tras autenticarse (SHA-256)

function _aplicarRol() {
    const esAsistente = _rol === 'asistente';

    // Estadísticas y equipos: siempre visibles para todos
    const stats = document.getElementById('seccionStats');
    if (stats) stats.style.display = 'grid';

    const equipos = document.getElementById('seccionEquipos');
    if (equipos) equipos.style.display = '';

    // Botones globales: Asistente solo ve "Bloquear Todas"; Admin ve los 3
    const btnFinalizar = document.querySelector('.btn-finalizar');
    const btnLimpiar   = document.querySelector('.btn-limpiar');
    if (btnFinalizar) btnFinalizar.style.display = esAsistente ? 'none' : '';
    if (btnLimpiar)   btnLimpiar.style.display   = esAsistente ? 'none' : '';

    // Botón importar Historial: solo Admin
    const btnImportar = document.getElementById('btnImportarExcel');
    if (btnImportar) btnImportar.style.display = esAsistente ? 'none' : 'inline-block';

    // Botón y sección Base de Datos (Maestro): solo Admin Nivel 2
    const btnMaestro = document.getElementById('btnMaestro');
    if (btnMaestro) btnMaestro.style.display = esAsistente ? 'none' : '';

    // Botón Limpiar BD Alumnos: solo Admin Nivel 2
    const btnLimpiarMaestro = document.getElementById('btnLimpiarMaestro');
    if (btnLimpiarMaestro) btnLimpiarMaestro.style.display = esAsistente ? 'none' : '';
    // Si baja a asistente, ocultar la sección si estaba abierta
    if (esAsistente) {
        const secMaestro = document.getElementById('seccionMaestro');
        if (secMaestro) secMaestro.style.display = 'none';
    }

    // Consolas de logs: solo visibles en Vista Admin
    const footerMonitoreo = document.getElementById('footer-monitoreo');
    if (footerMonitoreo) footerMonitoreo.style.display = esAsistente ? 'none' : '';

    const btn = document.getElementById('btnRol');
    if (btn) btn.textContent = esAsistente ? '🔐 Vista Admin' : '👁 Vista Asistente';
}

function toggleRol() {
    if (_rol === 'asistente') {
        // Elevar a admin: pedir contraseña de nivel 2
        _abrirModalNivel2();
    } else {
        // Bajar a asistente: directo, sin contraseña
        _rol = 'asistente';
        _aplicarRol();
    }
}

function _abrirModalNivel2() {
    const modal = document.getElementById('modal-nivel2');
    const passInput = document.getElementById('modal-nivel2-pass');
    const errorEl = document.getElementById('modal-nivel2-error');
    const btnConfirmar = document.getElementById('btn-nivel2-confirmar');
    const btnCancelar = document.getElementById('btn-nivel2-cancelar');

    passInput.value = '';
    errorEl.textContent = '';
    modal.style.display = 'flex';
    setTimeout(() => passInput.focus(), 50);

    const confirmar = () => {
        if (!_hashNivel2) {
            errorEl.textContent = '⛔ Configuración de seguridad no disponible.';
            return;
        }
        _sha256(passInput.value).then(hash => {
            if (hash === _hashNivel2) {
                _rol = 'admin';
                _aplicarRol();
                modal.style.display = 'none';
                passInput.removeEventListener('keydown', onKey);
            } else {
                errorEl.textContent = '⛔ Acceso Denegado. Contraseña incorrecta.';
                passInput.value = '';
                passInput.focus();
            }
        });
    };
    const cancelar = () => {
        modal.style.display = 'none';
        passInput.removeEventListener('keydown', onKey);
    };
    const onKey = (e) => { if (e.key === 'Enter') confirmar(); if (e.key === 'Escape') cancelar(); };

    // Limpiar listeners previos clonando los botones
    const newConfirmar = btnConfirmar.cloneNode(true);
    const newCancelar  = btnCancelar.cloneNode(true);
    btnConfirmar.parentNode.replaceChild(newConfirmar, btnConfirmar);
    btnCancelar.parentNode.replaceChild(newCancelar, btnCancelar);
    newConfirmar.addEventListener('click', confirmar);
    newCancelar.addEventListener('click', cancelar);
    passInput.addEventListener('keydown', onKey);
}

function _initRol() {
    // Siempre inicia en asistente por seguridad (no leer localStorage)
    _rol = 'asistente';
    _aplicarRol();
}

// ── Seguridad: SHA-256 + carga de hash nivel2 ─────────────────────
// Implementación pura JS — funciona en HTTP sin contexto seguro
function _sha256(texto) {
    function rightRotate(v, a) { return (v >>> a) | (v << (32 - a)); }
    const K = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
               0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
               0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
               0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
               0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
               0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
               0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
               0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
    let h = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
    const bytes = new TextEncoder().encode(texto);
    const l = bytes.length;
    const bits = l * 8;
    let msg = new Uint8Array(Math.ceil((l + 9) / 64) * 64);
    msg.set(bytes);
    msg[l] = 0x80;
    new DataView(msg.buffer).setUint32(msg.length - 4, bits, false);
    for (let i = 0; i < msg.length; i += 64) {
        const w = new Array(64);
        for (let j = 0; j < 16; j++) w[j] = new DataView(msg.buffer, i).getUint32(j * 4, false);
        for (let j = 16; j < 64; j++) {
            const s0 = rightRotate(w[j-15],7) ^ rightRotate(w[j-15],18) ^ (w[j-15] >>> 3);
            const s1 = rightRotate(w[j-2],17) ^ rightRotate(w[j-2],19)  ^ (w[j-2] >>> 10);
            w[j] = (w[j-16] + s0 + w[j-7] + s1) | 0;
        }
        let [a,b,c,d,e,f,g,hh] = h;
        for (let j = 0; j < 64; j++) {
            const S1   = rightRotate(e,6) ^ rightRotate(e,11) ^ rightRotate(e,25);
            const ch   = (e & f) ^ (~e & g);
            const t1   = (hh + S1 + ch + K[j] + w[j]) | 0;
            const S0   = rightRotate(a,2) ^ rightRotate(a,13) ^ rightRotate(a,22);
            const maj  = (a & b) ^ (a & c) ^ (b & c);
            const t2   = (S0 + maj) | 0;
            hh=g; g=f; f=e; e=(d+t1)|0; d=c; c=b; b=a; a=(t1+t2)|0;
        }
        h = [h[0]+a,h[1]+b,h[2]+c,h[3]+d,h[4]+e,h[5]+f,h[6]+g,h[7]+hh].map(v => v|0);
    }
    return Promise.resolve(h.map(v => (v >>> 0).toString(16).padStart(8,'0')).join(''));
}

async function _cargarHashNivel2() {
    try {
        const res = await fetch(`${API_BASE}/config/nivel2-hash`, {
            headers: { Authorization: `Bearer ${token}` },
            cache: 'no-store'
        });
        if (res.ok) {
            const d = await res.json();
            _hashNivel2 = d.hash;
            addLog('activity', '🔐 Configuración de seguridad Nivel 2 cargada');
        } else {
            addLog('error', '⚠️ No se pudo cargar configuración Nivel 2 del servidor');
        }
    } catch (e) {
        addLog('error', `⚠️ Error cargando hash nivel2: ${e.message}`);
    }
}

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

        document.getElementById('usuarioActual').textContent = username;
        document.getElementById('loginPanel').style.display  = 'none';
        document.getElementById('dashboard').style.display   = 'block';
        document.body.classList.remove('login-screen');
        btn.disabled = false;
        btn.textContent = 'Iniciar Sesión';

        _initRol(); // siempre inicia en Vista Asistente
        await _cargarHashNivel2(); // carga hash seguro desde el servidor

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
    document.getElementById('loginPanel').style.display = 'flex';
    document.getElementById('username').value = '';
    document.getElementById('password').value = '';
    document.body.classList.add('login-screen');
    _rol = 'asistente';
    _hashNivel2 = null;
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
    mostrarConfirmacion(
        'Esta acción enviará una orden de bloqueo inmediato a todos los equipos conectados. Los alumnos no podrán usar las PCs hasta que sean desbloqueadas manualmente o por el administrador.',
        () => {
            addLog('activity', '🔒 Botón BLOQUEAR TODAS las terminales');
            wsEnviar({ tipo: 'bloquear_todas' });
        },
        { titulo: '🔒 Bloquear Todos los Equipos', textoConfirmar: 'Confirmar Bloqueo Global' }
    );
}

async function finalizarTodo() {
    mostrarConfirmacion(
        '⚠️ Advertencia: Se cerrarán todas las sesiones activas en este momento. Se registrará la hora de salida actual para todos los alumnos, pero los registros de la base de datos se mantendrán intactos.',
        async () => {
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
        },
        { titulo: '🏁 Finalizar Sesiones', textoConfirmar: 'Finalizar Sesiones' }
    );
}

async function limpiarTodo() {
    mostrarConfirmacion(
        '🔥 PELIGRO: Esta acción finalizará todas las sesiones activas Y BORRARÁ permanentemente el historial de sesiones actual de la base de datos. Use esto solo si desea iniciar un nuevo periodo desde cero.',
        async () => {
            addLog('activity', '🧹 Botón LIMPIAR TODO — borrando historial de sesiones...');
            try {
                const res = await fetch(`${API_BASE}/admin/limpiar-sesiones`, {
                    method: 'DELETE',
                    headers: authHeaders(),
                    cache: 'no-store'
                });
                if (res.ok) {
                    const body = await res.json();
                    addLog('activity', `✅ Servidor: ${body.mensaje}`);
                    mostrarNotificacion('✅ Historial borrado. Nuevo periodo iniciado.', 'ok');
                    cargarDashboard();
                } else {
                    const err = await res.json().catch(() => ({}));
                    addLog('error', `❌ Limpiar sesiones: HTTP ${res.status} — ${err.mensaje || err.detail || 'Fallo'}`);
                    mostrarNotificacion('❌ ERROR: ' + (err.detail || 'Fallo al limpiar'), 'error');
                }
            } catch (e) {
                addLog('error', `❌ Error de red al limpiar: ${e.message}`);
                mostrarNotificacion('❌ ERROR: Problema de conexión', 'error');
            }
        },
        { titulo: '🔥 Limpiar Historial', textoConfirmar: 'BORRAR TODO Y REINICIAR', critico: true }
    );
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
                    <div class="tc-alumno ${sesion && sesion.activa ? 'tc-alumno-activo' : ''}">${
                        sesion && sesion.activa
                            ? escapeHtml(sesion.alumno_nombre)
                            : t.estado === 'bloqueado'
                                ? '<span class="text-warning">PC Bloqueada por Admin</span>'
                                : 'Disponible'
                    }</div>
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

function abrirImportarExcel() {
    mostrarConfirmacion(
        '⚠️ Esta acción insertará registros masivos en el historial. Asegúrese de que el Excel siga el formato de exportación estándar para evitar errores.',
        () => { document.getElementById('inputImportarExcel').click(); },
        { titulo: '📥 Importar Historial (Excel)', textoConfirmar: 'Seleccionar archivo' }
    );
}

async function ejecutarImportacion(input) {
    const archivo = input.files[0];
    if (!archivo) return;
    input.value = '';

    mostrarNotificacion('⏳ Importando Excel...', 'ok');
    addLog('activity', `📥 Importando archivo: ${archivo.name}`);

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
            mostrarNotificacion(`✅ ${body.mensaje}`, 'ok');
            addLog('activity', `✅ Importación: ${body.mensaje}`);
            if (body.detalle_errores && body.detalle_errores.length > 0) {
                body.detalle_errores.forEach(e => addLog('error', `⚠️ ${e}`));
            }
            cargarDashboard();
        } else {
            mostrarNotificacion(`❌ ${body.detail || 'Error al importar'}`, 'error');
            addLog('error', `❌ Importación fallida: ${body.detail}`);
        }
    } catch (e) {
        mostrarNotificacion('❌ Error de conexión al importar', 'error');
        addLog('error', `❌ Error de red al importar: ${e.message}`);
    }
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

function mostrarConfirmacion(mensaje, onConfirm, { titulo = '⚠️ Advertencia', textoConfirmar = 'Confirmar', critico = false } = {}) {
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


// ── Maestro de Alumnos ────────────────────────────────────────────

let _maestroOffset  = 0;
const _maestroLimit = 50;
let _maestroSearch  = '';
let _maestroVisible = false;

function toggleMaestro() {
    _maestroVisible = !_maestroVisible;
    const sec = document.getElementById('seccionMaestro');
    const btn = document.getElementById('btnMaestro');
    sec.style.display = _maestroVisible ? '' : 'none';
    if (btn) btn.classList.toggle('btn-maestro-activo', _maestroVisible);
    if (_maestroVisible) { _maestroOffset = 0; cargarMaestro(); }
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
                <button class="btn-card-desbloquear" style="padding:4px 10px;font-size:12px"
                    onclick="abrirEditarMaestro('${esc(a.dni)}','${esc(a.nombre)}','${esc(a.codigo||'')}','${esc(a.facultad||'')}','${esc(a.escuela||'')}')">✏️</button>
                <button class="btn-card-apagar" style="padding:4px 10px;font-size:12px"
                    onclick="eliminarMaestro('${esc(a.dni)}','${esc(a.nombre)}')">🗑️</button>
            </td>
        </tr>`).join('');

    // Paginación simple
    if (pag) {
        const totalPages = Math.ceil(data.total / _maestroLimit);
        const curPage    = Math.floor(_maestroOffset / _maestroLimit);
        let html = '';
        for (let i = 0; i < totalPages; i++) {
            html += `<button onclick="irPaginaMaestro(${i})"
                style="padding:4px 10px;border-radius:4px;border:1px solid var(--border);background:${i===curPage?'var(--accent)':'transparent'};color:${i===curPage?'#fff':'var(--text-primary)'};cursor:pointer">${i+1}</button>`;
        }
        pag.innerHTML = html;
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
                body: JSON.stringify({ nombre, codigo: codigo || null })
            });
            const body = await res.json();
            if (res.ok) {
                modal.style.display = 'none';
                mostrarNotificacion('✅ ' + body.mensaje, 'ok');
                addLog('activity', `✏️ Maestro actualizado: DNI ${modal._dni}`);
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
                    mostrarNotificacion('✅ ' + body.mensaje, 'ok');
                    addLog('activity', `🗑️ Maestro: eliminado DNI ${dni}`);
                    cargarMaestro();
                } else {
                    mostrarNotificacion('❌ ' + (body.detail || 'Error'), 'error');
                }
            } catch (e) {
                mostrarNotificacion('❌ Error de conexión', 'error');
            }
        },
        { titulo: '⚠️ Eliminar del Maestro', textoConfirmar: 'Eliminar', critico: true }
    );
}

async function importarMaestro(input) {
    const archivo = input.files[0];
    if (!archivo) return;
    input.value = '';

    const resultado = document.getElementById('maestroResultado');
    if (resultado) { resultado.style.display = ''; resultado.className = 'maestro-resultado cargando'; resultado.textContent = '⏳ Importando...'; }
    mostrarNotificacion('⏳ Importando maestro...', 'ok');
    addLog('activity', `📥 Importando maestro: ${archivo.name}`);

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
            const msg = `✅ ${data.insertados} nuevo(s)  |  🔄 ${data.actualizados} actualizado(s)${data.errores ? '  |  ⚠️ ' + data.errores + ' ignorado(s)' : ''}`;
            if (resultado) { resultado.className = 'maestro-resultado ok'; resultado.innerHTML = msg; }
            mostrarNotificacion('✅ Importación completada', 'ok');
            addLog('activity', `✅ Maestro: ${data.mensaje}`);
            if (data.detalle_errores?.length) data.detalle_errores.forEach(e => addLog('error', `⚠️ ${e}`));
            _maestroOffset = 0;
            cargarMaestro();
        } else {
            const err = data.detail || 'Error en importación';
            if (resultado) { resultado.className = 'maestro-resultado error'; resultado.textContent = '❌ ' + err; }
            mostrarNotificacion('❌ ' + err, 'error');
            addLog('error', `❌ Maestro importación: ${err}`);
        }
    } catch (e) {
        if (resultado) { resultado.className = 'maestro-resultado error'; resultado.textContent = '❌ Error de conexión'; }
        mostrarNotificacion('❌ Error de conexión', 'error');
        addLog('error', `❌ Error de red al importar maestro: ${e.message}`);
    }
}

// escapeHtml y esc definidos al inicio del archivo

// ── Limpiar Base de Datos de Alumnos (solo Nivel 2) ──────────────────

function abrirLimpiarMaestro() {
    const modal    = document.getElementById('modal-limpiar-maestro');
    const passEl   = document.getElementById('limpiar-maestro-pass');
    const errorEl  = document.getElementById('limpiar-maestro-error');

    passEl.value        = '';
    errorEl.textContent = '';
    modal.style.display = 'flex';
    setTimeout(() => passEl.focus(), 50);

    const cerrar = () => {
        modal.style.display = 'none';
        passEl.removeEventListener('keydown', onKey);
    };

    const confirmar = () => {
        if (!_hashNivel2) {
            errorEl.textContent = '⛔ Configuración de seguridad no disponible.';
            return;
        }
        _sha256(passEl.value).then(async hash => {
            if (hash !== _hashNivel2) {
                errorEl.textContent = '⛔ Contraseña incorrecta. Operación cancelada.';
                passEl.value = '';
                passEl.focus();
                return;
            }
            cerrar();
            addLog('activity', '🗑️ Contraseña Nivel 2 correcta — ejecutando limpiar maestro...');
            try {
                const res = await fetch(`${API_BASE}/admin/reset-maestro`, {
                    method: 'DELETE',
                    headers: authHeaders(),
                });
                const body = await res.json();
                if (res.ok) {
                    mostrarNotificacion('✅ ' + body.mensaje, 'ok');
                    addLog('activity', `✅ ${body.mensaje}`);
                    cargarDashboard();
                    if (_maestroVisible) cargarMaestro();
                } else {
                    mostrarNotificacion('❌ ' + (body.detail || 'Error al limpiar'), 'error');
                    addLog('error', `❌ Limpiar maestro: ${body.detail || 'Error'}`);
                }
            } catch (e) {
                mostrarNotificacion('❌ Error de conexión', 'error');
                addLog('error', `❌ Error de red al limpiar maestro: ${e.message}`);
            }
        });
    };

    const onKey = (e) => { if (e.key === 'Enter') confirmar(); if (e.key === 'Escape') cerrar(); };

    _rebindBtn('btn-limpiar-maestro-cancelar',  cerrar);
    _rebindBtn('btn-limpiar-maestro-confirmar', confirmar);
    passEl.addEventListener('keydown', onKey);
}

// Enter para login
document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && document.getElementById('loginPanel').style.display !== 'none')
        login();
});
