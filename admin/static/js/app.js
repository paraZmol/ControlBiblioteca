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
let _reconnectDelay = 2000; // Comenzar con 2 segundos

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
    setWsStatus(false);
}

// ── REST API ───────────────────────────────────────────────────────

function authHeaders() {
    return { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
}

async function cargarDashboard() {
    try {
        const [statsRes, termRes, sesRes] = await Promise.all([
            fetch(`${API_BASE}/dashboard/stats`,  { headers: authHeaders(), cache: 'no-store' }),
            fetch(`${API_BASE}/terminales`,        { headers: authHeaders(), cache: 'no-store' }),
            fetch(`${API_BASE}/sesiones/activas`,  { headers: authHeaders(), cache: 'no-store' })
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
            addLog(data.nivel === 'error' ? 'error' : 'activity', data.mensaje);
        } else if (data.type === 'log') {
            addLog(data.category, data.message);
        } else if (data.tipo === 'ok') {
            addLog('activity', `✅ Servidor: ${data.mensaje}`);
            mostrarNotificacion(data.mensaje, 'ok');
            cargarDashboard();
        } else if (data.tipo === 'error') {
            addLog('error', `❌ Servidor: ${data.motivo}`);
            mostrarNotificacion(data.motivo, 'error');
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

function bloquearTerminal(ip) {
    addLog('activity', `🔒 Botón BLOQUEAR terminal: ${ip}`);
    wsEnviar({ tipo: 'bloquear_terminal', ip });
}

function desbloquearTerminal(ip) {
    const inputId = `unlock-${ip.replace(/\./g, '-')}`;
    const input   = document.getElementById(inputId);
    const codigo  = input ? input.value.trim().toUpperCase() : '';
    if (!codigo) { if (input) input.focus(); return; }
    addLog('activity', `🔓 Botón DESBLOQUEAR terminal: ${ip} con código ${codigo}`);
    wsEnviar({ tipo: 'desbloquear_terminal', ip, codigo });
    if (input) input.value = '';
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

async function cerrarSesion(sesionId, motivo = 'admin', silent = false) {
    if (silent) {
        await ejecutarCierre(sesionId, motivo, true);
    } else {
        mostrarConfirmacion('¿Estás seguro de finalizar esta sesión? Esta acción es irreversible.', async () => {
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

async function apagarPc(ip, sesionId = null) {
    mostrarConfirmacion(`⚠️ ¿Estás seguro de apagar la terminal [${ip}]? Esta acción es irreversible.`, async () => {
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
        grid.innerHTML = '<p class="empty-msg" style="grid-column:1/-1">No hay terminales registradas</p>';
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
        if (!online) {
            botonesPrimarios = `
                <button class="btn-apagar" onclick="apagarPc('${esc(t.ip)}', ${sesion ? sesion.id : 'null'})" style="width:100%;background:#f59e0b;color:#fff;border:none;border-radius:4px;padding:8px;cursor:pointer;font-weight:bold">⏻ Apagar PC</button>
            `;
        } else if (faltaDesbloqueo) {
            botonesPrimarios = `
                <button class="btn-desbloquear" style="width:100%;background:#10b981;color:#fff;border:none;border-radius:4px;padding:10px;cursor:pointer;font-weight:bold;margin-bottom:6px" onclick="mostrarInputDesbloqueo('${esc(t.ip)}', '${inputId}')">🔓 Desbloquear</button>
                <div id="unlock-input-${inputId}" style="display:none;margin-bottom:6px">
                    <input id="${inputId}" type="text" placeholder="Código alumno" maxlength="20" style="width:100%;padding:8px;margin-bottom:4px;border-radius:4px;border:1px solid #ccc" onkeydown="if(event.key==='Enter') desbloquearTerminal('${esc(t.ip)}')">
                    <button class="btn-confirm" style="width:100%;background:#059669;color:#fff;border:none;border-radius:4px;padding:6px;cursor:pointer" onclick="desbloquearTerminal('${esc(t.ip)}')">Confirmar</button>
                </div>
                <button class="btn-apagar" onclick="apagarPc('${esc(t.ip)}', ${sesion ? sesion.id : 'null'})" style="width:100%;background:#f59e0b;color:#fff;border:none;border-radius:4px;padding:8px;cursor:pointer;font-weight:bold">⏻ Apagar PC</button>
            `;
        } else {
            // Estado activo o libre
            botonesPrimarios = `
                <button class="btn-bloquear" style="width:100%;background:#ef4444;color:#fff;border:none;border-radius:4px;padding:10px;cursor:pointer;font-weight:bold;margin-bottom:6px" onclick="bloquearTerminal('${esc(t.ip)}')">🔒 Bloquear</button>
                ${sesion ? `<button class="btn-cerrar" onclick="cerrarSesion(${sesion.id})" style="width:100%;background:#6366f1;color:#fff;border:none;border-radius:4px;padding:8px;cursor:pointer;font-weight:bold;margin-bottom:6px">🚪 Cerrar Sesión</button>` : ''}
                <button class="btn-apagar" onclick="apagarPc('${esc(t.ip)}', ${sesion ? sesion.id : 'null'})" style="width:100%;background:#f59e0b;color:#fff;border:none;border-radius:4px;padding:8px;cursor:pointer;font-weight:bold">⏻ Apagar PC</button>
            `;
        }

        return `
            <div class="terminal-card ${t.estado}">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
                    <div>
                        <div class="terminal-nombre">${escapeHtml(t.nombre || t.ip)}</div>
                        <div class="terminal-estado estado-${t.estado}" style="font-size:12px;margin-top:4px">${estadoLabel(t.estado)}</div>
                    </div>
                </div>
                <div style="border-top:1px solid #f3f4f6;padding-top:8px;margin-bottom:10px">
                    <div class="terminal-ip" style="font-size:13px;color:#4b5563;margin-bottom:4px"><strong>IP:</strong> ${escapeHtml(t.ip)}</div>
                    ${sesion ? `<div class="terminal-user" style="font-size:13px;color:#1f2937"><strong>Usuario:</strong> ${escapeHtml(sesion.alumno_nombre)}</div>` : ''}
                </div>
                <div style="display:flex;flex-direction:column">
                    ${botonesPrimarios}
                </div>
            </div>`;
    }).join('');
}

function mostrarInputDesbloqueo(ip, inputId) {
    const inputDiv = document.getElementById(`unlock-input-${inputId}`);
    if (inputDiv) {
        inputDiv.style.display = inputDiv.style.display === 'none' ? 'block' : 'none';
        if (inputDiv.style.display === 'block') {
            document.getElementById(inputId)?.focus();
        }
    }
}

function renderSesiones(sesiones) {
    const body       = document.getElementById('sesionesBody');
    const sinSesiones = document.getElementById('sinSesiones');

    if (!sesiones.length) {
        body.innerHTML = '';
        sinSesiones.style.display = 'block';
        return;
    }

    sinSesiones.style.display = 'none';
    body.innerHTML = sesiones.map(s => {
        const inicio  = new Date(s.inicio).toLocaleTimeString('es-PE');
        const salida  = s.hora_salida ? new Date(s.hora_salida).toLocaleTimeString('es-PE') : '<span class="pulsar-verde">En curso...</span>';
        const fecha   = s.fecha_uso ? new Date(s.fecha_uso + 'T00:00:00').toLocaleDateString('es-PE') : new Date(s.inicio).toLocaleDateString('es-PE');
        return `
        <tr>
            <td>${escapeHtml(s.alumno_nombre)}</td>
            <td>${escapeHtml(s.alumno_codigo)}</td>
            <td>${escapeHtml(s.dni || s.alumno_codigo)}</td>
            <td>${escapeHtml(s.facultad || '—')}</td>
            <td>${escapeHtml(s.escuela || '—')}</td>
            <td>${escapeHtml(s.razon_uso || '—')}</td>
            <td>${inicio}</td>
            <td>${salida}</td>
            <td>${fecha}</td>
        </tr>`;
    }).join('');
}

function mostrarConfirmacion(mensaje, onConfirm) {
    const modal = document.getElementById('modal-advertencia');
    document.getElementById('modal-mensaje').textContent = mensaje;
    
    const btnConfirm = document.getElementById('btn-modal-confirmar');
    const btnCancel = document.getElementById('btn-modal-cancelar');
    
    const newBtnConfirm = btnConfirm.cloneNode(true);
    btnConfirm.parentNode.replaceChild(newBtnConfirm, btnConfirm);
    
    const newBtnCancel = btnCancel.cloneNode(true);
    btnCancel.parentNode.replaceChild(newBtnCancel, btnCancel);
    
    newBtnConfirm.addEventListener('click', () => {
        modal.style.display = 'none';
        onConfirm();
    });
    
    newBtnCancel.addEventListener('click', () => {
        modal.style.display = 'none';
    });
    
    modal.style.display = 'flex';
}

// ── UI helpers ─────────────────────────────────────────────────────

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
