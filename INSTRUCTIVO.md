# Sistema de Control de Terminales — Biblioteca UNASAM

## Arquitectura

```
[PC Terminal] ──WS──► [Servidor Python FastAPI] ◄──HTTP── [Panel Admin Web]
  C# WPF                  PostgreSQL                        HTML/JS
```

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
