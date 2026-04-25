# Cambios Realizados - Sistema Control Biblioteca UNASAM

## 📋 Resumen Ejecutivo

Se han implementado **5 mejoras principales** para estabilizar el sistema, mejorar la visibilidad y blindar la seguridad:

1. **Estabilidad de Conexiones WebSocket** ✅
2. **Detección de IP Real del Servidor** ✅
3. **Corrección de Eliminación de Alumnos con Historial** ✅
4. **Auto-Registro Mejorado desde el SGA** ✅
5. **Blindaje de Endpoint Crítico de Limpieza** ✅

---

## 🔧 Cambios Técnicos

### Backend (Python - `/server/main.py`)

#### 1. Detección de IP Real
```python
def obtener_ip_local():
    """Obtiene la IP real usando socket.connect('8.8.8.8', 80)"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    ip = s.getsockname()[0]
    s.close()
    return ip
```

**IP Detectada:** `10.21.1.217`

#### 2. Timeout de WebSocket Aumentado a 60 Segundos
- El servidor espera 60 segundos antes de cerrar una conexión inactiva
- Permite mayor tolerancia a latencias o desconexiones temporales

#### 3. Nuevo Endpoint: GET `/api/server-info`
```json
{
  "ip": "10.21.1.217",
  "port": 8000,
  "ws_url": "ws://10.21.1.217:8000/ws/admin",
  "timestamp": "2026-04-22T14:59:57.800000"
}
```

#### 4. Endpoint de Limpieza: POST `/api/limpiar-todo`
- Desconecta todas las conexiones WebSocket
- Borra todas las sesiones activas
- Resetea el estado de las terminales a "offline"
- Requiere autenticación

#### 5. Corrección de Eliminación de Alumnos con Historial
**Problema:** Al intentar eliminar un alumno que tiene sesiones registradas, MySQL rechazaba la operación por restricción de llave foránea: `Cannot delete or update a parent row: a foreign key constraint fails`

**Solución Implementada (2 niveles de protección):**

1️⃣ **En la Relación ORM** (`/server/models.py`):
```python
class AlumnoMaestro(Base):
    # ...
    sesiones: Mapped[list["Sesion"]] = relationship(
        back_populates="alumno", 
        cascade="all, delete-orphan"  # ← NUEVO: Elimina sesiones en cascada
    )
```

2️⃣ **En el Endpoint DELETE** (`/server/api/endpoints.py`):
```python
@router.delete("/admin/maestro/{dni}")
async def eliminar_maestro(dni: str, ...):
    """Elimina un registro del maestro por DNI, incluyendo todas sus sesiones."""
    
    # Eliminar todas las sesiones del alumno primero
    await db.execute(delete(Sesion).where(Sesion.dni_alumno == dni))
    
    # Luego eliminar el alumno
    await db.delete(alumno)
    await db.commit()
```

**Beneficios:**
- ✅ Elimina exitosamente alumnos con o sin historial
- ✅ Respeta las restricciones de llave foránea en MySQL
- ✅ Mantiene la integridad referencial de la BD
- ✅ Mensaje de respuesta actualizado: "Alumno eliminado con todas sus sesiones"

### Cliente C# (WPF - `/client/Services/WebSocketService.cs`)

#### 1. Heartbeat Reducido a 5 Segundos
```csharp
private const int HEARTBEAT_MS = 5000;  // Antes: 30000
```
- Mantiene el túnel WebSocket activo
- Aumenta la confiabilidad en redes inestables

#### 2. Manejo Mejorado de Errores
- Try-catch en `BtnIngresar_Click()`
- Try-catch en `BtnCerrarSesion_Click()`
- Envío de error_report al servidor
- Logging detallado en consola de depuración

### Frontend (JavaScript - `/admin/static/js/app.js`)

#### 1. IP del Servidor Visible
- Función `obtenerYMostrarIpServidor()` llamada en login
- Llama a `/api/server-info` para obtener IP real
- Actualiza badge con estilos naranja fuerte

#### 2. Reconexión Inteligente del WebSocket Admin
```javascript
// Reintentos exponenciales (2s → 4s → 6s → ... → 60s máx)
_reconnectDelay = Math.min(2000 * Math.pow(1.5, attempts - 1), 60000)

// Límite de 10 reintentos
if (_reconnectAttempts >= _MAX_RECONNECT_ATTEMPTS) return;
```

#### 3. Mejor Gestión de Errores
- Log detallado de cada intento de reconexión
- Indicador visual en consola de actividad
- Pausas exponenciales sin sobrecargar la red

### Estilos CSS (`/admin/static/css/style.css`)

#### 1. Badge de IP Mejorado
```css
.server-badge {
    background: #FF9800;      /* Naranja fuerte */
    color: #000;              /* Texto negro */
    font-weight: bold;        /* Muy visible */
    box-shadow: 0 2px 8px rgba(255, 152, 0, 0.4);  /* Brillo */
}
```

#### 2. Botones de Administración
- Botón "Finalizar Todas" en púrpura
- Botón "Limpiar Todo" en naranja oscuro
- Mejor contraste y visibilidad

#### 4. Auto-Registro Mejorado desde el SGA (`/server/main.py`)

**Problema:** Los nuevos alumnos auto-registrados desde el SGA quedaban sin información de Escuela y Facultad en el panel.

**Solución Implementada:**

1️⃣ **Funciones Helper Get-or-Create** (líneas 54-93):
```python
async def get_or_create_facultad(db, nombre: str) -> Optional[Facultad]:
    """Busca Facultad por nombre, si no existe la crea."""
    # Búsqueda exacta por nombre
    # Si existe: retorna la existente
    # Si no existe: crea una nueva y retorna

async def get_or_create_escuela(db, nombre: str, facultad: Facultad) -> Optional[Escuela]:
    """Busca Escuela por nombre y facultad, si no existe la crea."""
    # Búsqueda compuesta (nombre + id_facultad)
    # Si existe: retorna la existente
    # Si no existe: crea una nueva y retorna
```

2️⃣ **Actualización del Auto-Registro** (líneas 474-500):
```python
# Auto-registro en maestro con Facultad y Escuela (get_or_create)
nombre_completo = f"{sga['nombres']} {sga['apellidos']}"
id_escuela = None

# Crear o recuperar Facultad
if sga.get("facultad"):
    facultad = await get_or_create_facultad(db, sga["facultad"])
    # Crear o recuperar Escuela asociada a la Facultad
    if facultad and sga.get("escuela"):
        escuela = await get_or_create_escuela(db, sga["escuela"], facultad)
        if escuela:
            id_escuela = escuela.id

# Crear alumno con escuela si está disponible
nuevo_alumno = AlumnoMaestro(
    dni=sga["dni"],
    nombre=nombre_completo,
    codigo=sga["codigo"],
    id_escuela=id_escuela,  # ← Nuevo: asignación de escuela
)
```

3️⃣ **Importes Actualizados** (línea 16):
```python
from models import AlumnoMaestro, Usuario, Terminal, Sesion, Facultad, Escuela
```

**Beneficios:**
- ✅ Alumnos nuevos tienen Escuela y Facultad asignadas automáticamente
- ✅ Evita duplicados usando búsqueda antes de crear
- ✅ Panel admin muestra datos completos (no quedan vacíos)
- ✅ Integración perfecta con la estructura jerárquica Facultad → Escuela → Alumno
- ✅ Logs detallados de creación: `[SGA] Nueva Facultad creada: ...`

**Flujo de Auto-Registro Nuevo:**
1. Alumno escanea DNI en kiosco
2. Sistema consulta SGA (obtiene dni, código, nombres, apellidos, **escuela, facultad**)
3. Si existe Facultad → usa la existente; sino → la crea
4. Si existe Escuela (para esa Facultad) → usa la existente; sino → la crea
5. Crea AlumnoMaestro con id_escuela asignado
6. Panel admin muestra alumno con Escuela y Facultad pobladas ✅

#### 5. Blindaje del Endpoint `/api/limpiar-todo` (`/server/main.py`)

**Riesgo Crítico Identificado:** El endpoint de limpieza total del sistema no requerería autenticación JWT, permitiendo que cualquiera pudiera ejecutarlo sin autorización.

**Solución Implementada:**

1️⃣ **Importes de Seguridad Agregados** (línea 11):
```python
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
```

2️⃣ **Función Protegida por Autenticación JWT** (líneas 260-310):
- Requiere autenticación JWT válida: `admin: Usuario = Depends(obtener_usuario_actual)`
- Valida que el usuario tenga rol `"admin"`
- Si no es admin: retorna HTTP 403 Forbidden
- Logging de auditoría: quién, cuándo y si fue autorizado o rechazado
- Respuesta incluye campo `"ejecutado_por": admin.username`

3️⃣ **Capas de Seguridad:**
- Capa 1: JWT validation (autenticación)
- Capa 2: Rol validation (autorización)
- Capa 3: Audit logging (trazabilidad)
- Capa 4: Rechazo con HTTPException 403

4️⃣ **Logs de Auditoría Detallados:**
```
[SEGURIDAD] Usuario 'encargado1' (encargado) intentó ejecutar LIMPIAR-TODO sin autorización
[LIMPIEZA] Iniciado por administrador 'admin' desde admin
[LIMPIEZA] Operación completada exitosamente por admin 'admin'
```

**Beneficios:**
- ✅ Solo administradores pueden ejecutar
- ✅ Requiere autenticación JWT válida
- ✅ Auditoría completa (quién, cuándo, resultado)
- ✅ Rechaza intentos no autorizados con HTTP 403
- ✅ Compatible con otros endpoints admin (`DELETE /admin/maestro/{dni}`, etc.)

**Ejemplo de uso desde panel admin:**
```javascript
// Requiere token JWT válido en header Authorization: Bearer <token>
fetch('/api/limpiar-todo', {
    method: 'POST',
    headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
    }
})
.then(r => r.json())
.then(data => {
    // Respuesta si es admin: 
    // {"estado": "ok", "mensaje": "...", "ejecutado_por": "admin"}
    
    // Respuesta si no es admin:
    // HTTP 403: "Solo administradores pueden ejecutar esta operación"
})
```

---

6. Panel admin muestra alumno con Escuela y Facultad pobladas ✅

| Aspecto | Antes | Después |
|--------|-------|---------|
| Heartbeat | 30s | 5s ⬇️ |
| WebSocket Timeout | Default | 60s ⬆️ |
| Reconexión Admin | 5s fijo | Exponencial (2-60s) |
| IP Visible | localhost | 10.21.1.217 🟢 |
| Errores Capturados | Parcial | Completo |
| Alumnos SGA sin Escuela | id_escuela=NULL | Asignada automáticamente ✅ |

---

## 🚀 Instrucciones de Despliegue

### En el Servidor (controlpc)

1. **Reiniciar el servidor Python** (ya está corriendo):
   ```bash
   # Ctrl+C para detener
   # Luego:
   cd c:\Users\nunes\StudioProjects\control\server
   python main.py
   ```

   ✅ **Estado:** Corriendo en `http://0.0.0.0:8000`

### En las Terminales (VMs)

1. **Copiar el nuevo .exe** a las terminales:
   ```
   c:\Users\nunes\StudioProjects\control\client\bin\Release\net8.0-windows\win-x64\ControlBiblioteca.Client.exe
   ```

2. **Desplegar en:**
   - `C:\SistemaBiblioteca\ControlBiblioteca.Client.exe` (en cada VM)

3. **Reiniciar las terminales** para que carguen el nuevo cliente

### En el Panel Admin

1. **Abrir el navegador:**
   ```
   http://10.21.1.217:8000/admin
   ```

2. **Credenciales:**
   - Usuario: `admin`
   - Contraseña: `admin123`

3. **Verificar:**
   - ✅ Badge naranja con IP `10.21.1.217`
   - ✅ Terminales mostradas como "Offline" si están apagadas
   - ✅ Botón "Limpiar Todo" funcional
   - ✅ WebSocket reconecta automáticamente si se interrumpe

---

## 🧪 Pruebas Recomendadas

### 1. Estabilidad de WebSocket
```bash
# Interrumpir la conexión del server en el panel admin
# → Debe reconectar automáticamente en ~2 segundos
# → Luego aumentar delay exponencialmente
```

### 2. Limpieza del Sistema
```bash
# En el panel admin: Clic en "🧹 Limpiar Todo"
# → Debe borrar todas las sesiones y terminales
# → Mostrar confirmación "SISTEMA RESETEADO"
# → Recargar automáticamente
```

### 3. IP del Servidor
```bash
# Verificar en el badge naranja que dice:
# 🌐 IP SERVIDOR: 10.21.1.217
# (Debe ser visible desde cualquier otro browser en la red)
```

### 4. Seguridad del Endpoint `/api/limpiar-todo`

**Prueba: Sin Autenticación (debe ser rechazado)**
```bash
curl -X POST http://localhost:8000/api/limpiar-todo
# Respuesta esperada: HTTP 403 o HTTP 401 (sin token JWT)
```

**Prueba: Con Token de Usuario No-Admin (debe ser rechazado)**
```bash
# Token de usuario con rol='encargado'
curl -X POST http://localhost:8000/api/limpiar-todo \
  -H "Authorization: Bearer <token_encargado>"
# Respuesta esperada: HTTP 403 Forbidden
# Mensaje: "Solo administradores pueden ejecutar esta operación"
# Log: "[SEGURIDAD] Usuario 'encargado1' (encargado) intentó ejecutar LIMPIAR-TODO sin autorización"
```

**Prueba: Con Token de Admin (debe ser exitoso)**
```bash
# Token de usuario con rol='admin'
curl -X POST http://localhost:8000/api/limpiar-todo \
  -H "Authorization: Bearer <token_admin>"
# Respuesta esperada: HTTP 200 OK
# Respuesta: {"estado": "ok", "mensaje": "Sistema limpiado completamente", "ejecutado_por": "admin"}
# Log: "[LIMPIEZA] Iniciado por administrador 'admin' desde admin"
# Log: "[LIMPIEZA] Operación completada exitosamente por admin 'admin'"
```

**Verificaciones de Logs:**
```
✅ Intentos no autorizados quedan registrados en [SEGURIDAD]
✅ Operaciones exitosas quedan registradas en [LIMPIEZA]
✅ Usuario que ejecutó queda registrado en respuesta JSON
✅ Auditoría completa: quién, cuándo, resultado
```

---

## ⚠️ Notas Importantes

1. **Socket.connect():** Ahora usamos `('8.8.8.8', 80)` para detectar la IP real sin causar conexión real
2. **Timeout 60s:** Protege contra desconexiones accidentales por latencia
3. **Heartbeat 5s:** Mantiene el túnel abierto sin sobrecargar la red
4. **Reintentos exponenciales:** Evita flooding del servidor en caso de desconexión
5. **JWT Obligatorio:** `/api/limpiar-todo` requiere autenticación válida + rol admin

---

## 📝 Archivos Modificados

```
✅ server/models.py
   └─ Agregado cascade="all, delete-orphan" en relación AlumnoMaestro.sesiones

✅ server/api/endpoints.py
   └─ Endpoint DELETE /admin/maestro/{dni} ahora elimina sesiones primero

✅ server/main.py
   ├─ Importes Nuevos (línea 11): Depends, HTTPException, status (seguridad)
   ├─ Importes Nuevos (línea 17): obtener_usuario_actual (autenticación)
   ├─ Importar: Facultad, Escuela (línea 16)
   ├─ Función: get_or_create_facultad() (líneas 54-70)
   ├─ Función: get_or_create_escuela() (líneas 73-93)
   ├─ Actualización docstring: consultar_sga() (línea 96)
   ├─ Auto-registro mejorado con Escuela/Facultad (líneas 474-500)
   ├─ Endpoint POST /api/limpiar-todo BLINDADO (líneas 260-310)
   │  ├─ Autenticación JWT obligatoria
   │  ├─ Validación de rol admin
   │  ├─ Logging de auditoría
   │  └─ HTTP 403 para usuarios no admin
   ├─ Importar: socket, asyncio, delete (ya estaban)
   ├─ Función: obtener_ip_local()
   ├─ Variable: _IP_LOCAL
   └─ Endpoint: GET /api/server-info

✅ client/Services/WebSocketService.cs
   ├─ HEARTBEAT_MS = 5000 (de 30000)
   └─ Logging de heartbeat

✅ client/UI/MainWindow.xaml.cs
   ├─ Try-catch en BtnIngresar_Click
   ├─ Try-catch en BtnCerrarSesion_Click
   ├─ Try-catch mejorado en ProcesarMensajeServidor
   └─ ReportarErrorAsync en eventos

✅ admin/static/js/app.js
   ├─ Función: obtenerYMostrarIpServidor()
   ├─ Reconexión exponencial en conectarWebSocket()
   ├─ Manejo mejorado en wsAdmin.onclose
   └─ Llamada a /api/limpiar-todo en limpiarTodo()

✅ admin/static/css/style.css
   ├─ Mejorado .server-badge
   ├─ Agregados .btn-finalizar y .btn-limpiar
   └─ Mejorado .terminal-card.offline
```

---

## 📞 Soporte

Si encontras problemas:

1. **WebSocket no reconecta:** Revisa logs del servidor (`python main.py`)
2. **IP no detectada:** Verifica conectividad de red
3. **Cliente C# no conecta:** Asegúrate de que el .exe sea la versión Release más reciente

---

**Última actualización:** 22 de Abril de 2026  
**Estado:** ✅ Completo y Listo para Despliegue  
**Próxima fase:** Monitoreo en producción
