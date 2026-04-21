// Detectar si se abre como archivo local o desde el servidor
const _isFile = window.location.protocol === 'file:';
const API_BASE  = _isFile ? 'http://localhost:8000/api'   : window.location.origin + '/api';
const WS_BASE   = _isFile ? 'ws://localhost:8000'         : `ws://${window.location.host}`;

let token   = null;
let wsAdmin = null;
let _reconnectTimer = null;

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

        cargarDashboard();
        conectarWebSocket();
        setInterval(cargarDashboard, 15000);
    } catch {
        errorEl.textContent = 'No se pudo conectar al servidor (¿está corriendo en :8000?)';
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
            fetch(`${API_BASE}/dashboard/stats`,  { headers: authHeaders() }),
            fetch(`${API_BASE}/terminales`,        { headers: authHeaders() }),
            fetch(`${API_BASE}/sesiones/activas`,  { headers: authHeaders() })
        ]);

        if (statsRes.ok) {
            const s = await statsRes.json();
            document.getElementById('totalTerminales').textContent  = s.total_terminales;
            document.getElementById('terminalesActivas').textContent = s.terminales_activas;
            document.getElementById('sesionesActivas').textContent  = s.sesiones_activas;
            document.getElementById('totalAlumnos').textContent     = s.total_alumnos;
        }
        if (termRes.ok) renderTerminales(await termRes.json());
        if (sesRes.ok)  renderSesiones(await sesRes.json());
    } catch (e) {
        console.error('Error cargando dashboard:', e);
    }
}

// ── WebSocket ──────────────────────────────────────────────────────

function conectarWebSocket() {
    if (wsAdmin && wsAdmin.readyState === WebSocket.OPEN) return;

    wsAdmin = new WebSocket(`${WS_BASE}/ws/admin`);

    wsAdmin.onopen = () => {
        setWsStatus(true);
        clearTimeout(_reconnectTimer);
    };

    wsAdmin.onmessage = (event) => {
        let data;
        try { data = JSON.parse(event.data); } catch { return; }

        if (data.tipo === 'status_update') {
            // Push de terminal conectada/desconectada — refrescar lista
            cargarDashboard();
        } else if (data.tipo === 'ok') {
            mostrarNotificacion(data.mensaje, 'ok');
            cargarDashboard();
        } else if (data.tipo === 'error') {
            mostrarNotificacion(data.motivo, 'error');
        }
    };

    wsAdmin.onclose = () => {
        setWsStatus(false);
        _reconnectTimer = setTimeout(conectarWebSocket, 5000);
    };

    wsAdmin.onerror = () => wsAdmin.close();
}

function wsEnviar(payload) {
    if (!wsAdmin || wsAdmin.readyState !== WebSocket.OPEN) {
        mostrarNotificacion('Sin conexión WebSocket con el servidor', 'error');
        return false;
    }
    wsAdmin.send(JSON.stringify(payload));
    return true;
}

// ── Acciones de terminal ───────────────────────────────────────────

function bloquearTerminal(ip) {
    wsEnviar({ tipo: 'bloquear_terminal', ip });
}

function desbloquearTerminal(ip) {
    const inputId = `unlock-${ip.replace(/\./g, '-')}`;
    const input   = document.getElementById(inputId);
    const codigo  = input ? input.value.trim().toUpperCase() : '';
    if (!codigo) { if (input) input.focus(); return; }
    wsEnviar({ tipo: 'desbloquear_terminal', ip, codigo });
    if (input) input.value = '';
}

function bloquearTodas() {
    if (!confirm('¿Bloquear TODAS las terminales activas?')) return;
    wsEnviar({ tipo: 'bloquear_todas' });
}

async function cerrarSesion(sesionId) {
    if (!confirm('¿Cerrar esta sesión?')) return;
    try {
        const res = await fetch(`${API_BASE}/sesiones/${sesionId}/cerrar?motivo=admin`, {
            method: 'POST', headers: authHeaders()
        });
        if (res.ok) cargarDashboard();
    } catch (e) {
        console.error('Error cerrando sesión:', e);
    }
}

function apagarPc(ip) {
    if (!confirm(`¿Apagar la PC con IP ${ip}?`)) return;
    wsEnviar({ tipo: 'remote_command', action: 'shutdown', ip });
}

// ── Renderizado ────────────────────────────────────────────────────

function renderTerminales(terminales) {
    const grid = document.getElementById('terminalesGrid');
    if (!terminales.length) {
        grid.innerHTML = '<p class="empty-msg" style="grid-column:1/-1">No hay terminales registradas</p>';
        return;
    }

    grid.innerHTML = terminales.map(t => {
        const inputId = `unlock-${t.ip.replace(/\./g, '-')}`;
        const online  = t.estado !== 'offline';
        const activo  = t.estado === 'activo';

        const acciones = online ? `
            <div class="terminal-acciones">
                <button class="btn-bloquear" onclick="bloquearTerminal('${esc(t.ip)}')">🔒 Bloquear</button>
                <div class="unlock-row">
                    <input id="${inputId}" type="text" placeholder="Código alumno" maxlength="20"
                           onkeydown="if(event.key==='Enter') desbloquearTerminal('${esc(t.ip)}')">
                    <button class="btn-desbloquear" onclick="desbloquearTerminal('${esc(t.ip)}')">🔓</button>
                </div>
            </div>` : '';

        return `
            <div class="terminal-card ${t.estado}">
                <div class="terminal-nombre">${escapeHtml(t.nombre || t.ip)}</div>
                <div class="terminal-estado estado-${t.estado}">${estadoLabel(t.estado)}</div>
                <div class="terminal-ip">${escapeHtml(t.ip)}</div>
                ${acciones}
            </div>`;
    }).join('');
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
        const salida  = s.fin ? new Date(s.fin).toLocaleTimeString('es-PE') : '—';
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
            <td style="display:flex;gap:6px">
                <button class="btn-cerrar" onclick="cerrarSesion(${s.id})">Cerrar</button>
                <button class="btn-apagar" onclick="apagarPc('${esc(s.terminal_ip)}')" title="Apagar PC" style="background:#e53e3e;color:#fff;border:none;border-radius:4px;padding:4px 10px;cursor:pointer">&#9211; Apagar PC</button>
            </td>
        </tr>`;
    }).join('');
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

function escapeHtml(text) {
    const d = document.createElement('div');
    d.textContent = String(text ?? '');
    return d.innerHTML;
}

// Short alias for attribute contexts (values already safe from server)
function esc(s) { return escapeHtml(s); }

// Enter para login
document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && document.getElementById('loginPanel').style.display !== 'none')
        login();
});
