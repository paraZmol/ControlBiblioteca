# Sistema de Control de Terminales — Biblioteca UNASAM

> **Instalación completa → ver [`docs/INSTALACION_SERVIDOR.txt`](docs/INSTALACION_SERVIDOR.txt)**
> Este archivo es solo un resumen de arquitectura. El instructivo detallado, con migración desde v3.0, está en `docs/`.

---

## Arquitectura

```
[PC Terminal] ──WS──► [Servidor Python FastAPI] ◄──HTTP── [Panel Admin Web]
  C# WPF .NET 8         MySQL/MariaDB  :8000              HTML/JS
```

| Componente | Tecnología | Requisito |
|---|---|---|
| Servidor | Python 3.11+, FastAPI, MySQL 8.x / MariaDB | IP fija en red local |
| Terminal (cliente) | Windows 10/11, .NET 8, WPF | Ejecutar como Administrador |
| Panel Admin | Navegador moderno | Acceso a puerto 8000 del servidor |

---

## Instalación rápida (resumen)

1. **MySQL corriendo** (XAMPP recomendado).
2. **Python 3.11+** con "Add to PATH".
3. Editar `server\config.json` → contraseña MySQL + IP fija de la PC.
4. Ejecutar `server\instalar_servidor.bat` **como Administrador**.
5. Reiniciar la PC (o correr `server\servidor_run.bat`).
6. Crear `C:\WinSysCache\` con el `.exe` del cliente + `version.txt`.
7. Acceder a `http://localhost:8000/admin` → `superadmin / admin123` → **cambiar contraseñas**.
8. Importar alumnos por Excel desde el panel.

> **Migración desde v3.0:** Leer obligatoriamente la Sección F de `docs/INSTALACION_SERVIDOR.txt`.

---

## Archivos clave

| Archivo | Propósito |
|---|---|
| `server/config.json` | Única fuente de verdad: BD, IP, seguridad. Editar antes de instalar. |
| `server/.env` | Generado automáticamente por el configurador. No editar a mano. |
| `server/instalar_servidor.bat` | Instalador completo (venv + deps + BD + firewall + autoarranque). |
| `server/servidor_run.bat` | Arranque manual del servidor. |
| `client/kiosco.config.json` | IP y puerto del servidor que usa el ejecutable del kiosco. |
| `C:\WinSysCache\version.txt` | Número de versión que leen los kioscos para auto-actualizarse. |

---

## Credenciales iniciales

| Rol | Usuario | Contraseña inicial |
|---|---|---|
| Administración completa | `superadmin` | `admin123` |
| Operación de sala | `admin` | `admin123` |
| Nivel 2 (consola admin) | — | `max123` |

> **Cambiar todas las contraseñas en el primer inicio** (Configuración → Credenciales).

---

## Seguridad — lista antes de producción

- [ ] Cambiar contraseña de `superadmin` y `admin` al primer inicio
- [ ] Cambiar `pass_nivel2` en `config.json` (`max123` es el valor por defecto)
- [ ] La `SECRET_KEY` la genera el configurador automáticamente — no tocar
- [ ] No exponer el puerto 8000 a internet (solo red local)
- [ ] Verificar que `.env` esté en `.gitignore` ✓

---

## Solución de problemas

| Problema | Causa probable | Solución |
|---|---|---|
| Panel no carga | MySQL apagado | Arrancar MySQL (XAMPP en verde) |
| `Can't connect to MySQL` | Contraseña en `config.json` incorrecta | Corregir `database.password` y volver a correr el instalador |
| Kiosco no conecta | IP del servidor incorrecta en `kiosco.config.json` | Editar `ServerIp` con la IP real del servidor |
| `Address already in use` | Ya hay un servidor corriendo | Cerrar `python.exe` en Administrador de Tareas |
| Kiosco no se actualiza | `version.txt` ausente o número no mayor | Verificar `C:\WinSysCache\version.txt` y reiniciar servidor |
| Olvidé contraseña superadmin | — | Ver `docs/CASOS_DE_USO.txt` caso A-3 |


---

## Requisitos

| Componente | Requisito |
|---|---|
| Servidor | Python 3.11+, PostgreSQL 14+ |
| Terminal (cliente) | Windows 10/11, .NET 8, permisos de Administrador |
| Admin | Navegador moderno |

---

## Instalación del Servidor

```bash
cd server

# 1. Crear entorno virtual
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar variables de entorno
# Editar .env con los valores reales:
#   DATABASE_URL  → credenciales reales de PostgreSQL
#   SECRET_KEY    → generar con: python -c "import secrets; print(secrets.token_urlsafe(32))"
#   CORS_ORIGINS  → IP/dominio del panel admin

# 4. Crear base de datos
psql -U postgres -c "CREATE DATABASE control_biblioteca;"
psql -U postgres -d control_biblioteca -f ../data/init.sql

# 5. Iniciar servidor
uvicorn main:app --host 0.0.0.0 --port 8000
```

**Primer inicio:** se crea el usuario `admin` con contraseña `admin123`.
**Cambiar la contraseña inmediatamente** desde el panel admin.

---

## Instalación del Cliente (Terminal)

1. Compilar el proyecto C# en Visual Studio (Release, x64).
2. Copiar el ejecutable a cada PC terminal.
3. Configurar la IP del servidor en [MainWindow.xaml.cs](client/UI/MainWindow.xaml.cs) línea 20:
   ```csharp
   private const string SERVER_WS_URL = "ws://IP_DEL_SERVIDOR:8000/ws/terminal/";
   ```
4. Crear un acceso directo en el **inicio automático de Windows** (`shell:startup`) apuntando al ejecutable.
5. Ejecutar **siempre como Administrador** (necesario para bloquear teclado y Task Manager).

---

## Uso diario

### Panel Admin
- Acceder a `http://IP_SERVIDOR:8000/admin`
- Login con usuario administrador
- El dashboard muestra terminales conectadas y sesiones activas en tiempo real

### Flujo en terminal
1. PC arranca → cliente C# se inicia automáticamente → pantalla de bloqueo
2. Alumno ingresa su código → servidor valida → terminal se desbloquea
3. Al terminar → alumno presiona "Cerrar sesión" → terminal se bloquea

---

## ¿Usar máquina virtual?

### Servidor Python — SÍ se recomienda VM o contenedor

| Razón | Detalle |
|---|---|
| Aislamiento | Un fallo del servidor no afecta el resto de la red |
| Snapshots | Rollback rápido ante actualizaciones problemáticas |
| Recursos | 1-2 vCPU / 2 GB RAM es suficiente |
| Opción más simple | Docker Compose (servidor + PostgreSQL en contenedores) |

### Cliente C# — NO usar VM

El cliente **debe correr en el hardware real** de cada terminal porque:
- Necesita acceso al registro de Windows del sistema real
- Los hooks de teclado de bajo nivel (`SetWindowsHookEx`) no funcionan correctamente dentro de VM
- La IP que reporta sería la de la VM, no la de la terminal física

---

## Seguridad — Lista de verificación antes de producción

- [ ] Cambiar `SECRET_KEY` en `.env` (generar con `secrets.token_urlsafe(32)`)
- [ ] Cambiar contraseña del usuario `admin` al primer inicio
- [ ] Configurar `CORS_ORIGINS` con la IP real del servidor admin
- [ ] Usar HTTPS/WSS con certificado SSL (nginx como reverse proxy recomendado)
- [ ] Cambiar credenciales de PostgreSQL (no usar `postgres/postgres`)
- [ ] No exponer el puerto 8000 directamente a internet
- [ ] Agregar `.env` al `.gitignore`

---

## Solución de problemas

| Problema | Causa probable | Solución |
|---|---|---|
| Terminal no conecta | IP del servidor incorrecta | Verificar `SERVER_WS_URL` en el cliente |
| Task Manager bloqueado tras cierre | App cerrada de forma abrupta | Ejecutar `RegistryControl.HabilitarTaskManager()` manualmente o reiniciar la app normalmente |
| `login_rechazado` siempre | Código no normalizado | El servidor convierte a mayúsculas; verificar que el código en DB también esté en mayúsculas |
| Sesiones no aparecen en admin | Sin sesiones activas | Verificar que el alumno esté marcado como `habilitado = true` en la DB |
| Módulos Python no encontrados | Venv no activado | Activar con `venv\Scripts\activate` antes de `uvicorn` |
