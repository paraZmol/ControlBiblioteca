# -*- coding: utf-8 -*-
"""Catálogo de ejecutables conocidos para el Banco de programas y el Banco de ruido.

Fuente única usada por:
  - la pre-carga (seed) de las tablas banco_apps y banco_ruido (main.py),
  - el endpoint de "pendientes por clasificar", para describir un .exe que el
    superadmin aún no clasificó (sugerirle qué es y a quién pertenece).

Las claves SIEMPRE en minúscula: el resto del sistema compara nombre_exe en
lowercase (los .exe de Windows no distinguen mayúsculas).

NOTA DE SEGURIDAD: las herramientas peligrosas (cmd, powershell, regedit…) NO
están en ninguno de estos diccionarios a propósito — deben caer como sospechosas
para que nadie las apruebe sin querer.
"""

# ── Banco de PROGRAMAS (apps reales del alumno) ────────────────────────
# clave -> (nombre_amigable, categoria, descripcion)
APPS_CONOCIDAS = {
    # Ofimática
    "winword.exe":      ("Microsoft Word", "Ofimática", "Procesador de textos de Office."),
    "excel.exe":        ("Microsoft Excel", "Ofimática", "Hoja de cálculo de Office."),
    "powerpnt.exe":     ("Microsoft PowerPoint", "Ofimática", "Presentaciones de Office."),
    "onenote.exe":      ("Microsoft OneNote", "Ofimática", "Notas de Office."),
    "msaccess.exe":     ("Microsoft Access", "Ofimática", "Base de datos de Office."),
    "outlook.exe":      ("Microsoft Outlook", "Ofimática", "Correo y calendario de Office."),
    "mspub.exe":        ("Microsoft Publisher", "Ofimática", "Maquetación de Office."),
    "soffice.bin":      ("LibreOffice", "Ofimática", "Suite ofimática libre."),
    "soffice.exe":      ("LibreOffice", "Ofimática", "Suite ofimática libre."),
    "wps.exe":          ("WPS Office", "Ofimática", "Suite ofimática WPS."),
    "et.exe":           ("WPS Spreadsheets", "Ofimática", "Hoja de cálculo de WPS."),
    "wpp.exe":          ("WPS Presentation", "Ofimática", "Presentaciones de WPS."),
    # Lectores / visores
    "acrord32.exe":     ("Adobe Acrobat Reader", "Lectores", "Lector de PDF."),
    "acrobat.exe":      ("Adobe Acrobat", "Lectores", "Editor/lector de PDF."),
    "sumatrapdf.exe":   ("SumatraPDF", "Lectores", "Lector ligero de PDF."),
    "foxitreader.exe":  ("Foxit Reader", "Lectores", "Lector de PDF."),
    "photos.exe":       ("Fotos de Windows", "Lectores", "Visor de imágenes de Windows."),
    "microsoft.photos.exe": ("Fotos de Windows", "Lectores", "Visor de imágenes de Windows."),
    "mspaint.exe":      ("Paint", "Lectores", "Editor de imágenes básico de Windows."),
    "snippingtool.exe": ("Recortes", "Utilidades", "Herramienta de capturas de Windows."),
    "screenclippinghost.exe": ("Recortes", "Utilidades", "Captura de pantalla de Windows."),
    "notepad.exe":      ("Bloc de notas", "Ofimática", "Editor de texto plano de Windows."),
    "wordpad.exe":      ("WordPad", "Ofimática", "Editor de texto enriquecido de Windows."),
    "calc.exe":         ("Calculadora", "Utilidades", "Calculadora de Windows."),
    # Navegadores
    "chrome.exe":       ("Google Chrome", "Navegador", "Navegador web de Google."),
    "msedge.exe":       ("Microsoft Edge", "Navegador", "Navegador web de Microsoft."),
    "firefox.exe":      ("Mozilla Firefox", "Navegador", "Navegador web de Mozilla."),
    "opera.exe":        ("Opera", "Navegador", "Navegador web Opera."),
    "brave.exe":        ("Brave", "Navegador", "Navegador web Brave."),
    "iexplore.exe":     ("Internet Explorer", "Navegador", "Navegador heredado de Windows."),
    # Diseño / ingeniería (comunes en una facultad de sistemas/ingeniería)
    "acad.exe":         ("AutoCAD", "Diseño", "Diseño asistido por computadora."),
    "sldworks.exe":     ("SolidWorks", "Diseño", "Diseño mecánico 3D."),
    "photoshop.exe":    ("Adobe Photoshop", "Diseño", "Edición de imágenes."),
    "illustrator.exe":  ("Adobe Illustrator", "Diseño", "Diseño vectorial."),
    "gimp-2.10.exe":    ("GIMP", "Diseño", "Edición de imágenes libre."),
    "blender.exe":      ("Blender", "Diseño", "Modelado y animación 3D."),
    "inkscape.exe":     ("Inkscape", "Diseño", "Diseño vectorial libre."),
    # Programación (carrera de sistemas)
    "code.exe":         ("Visual Studio Code", "Programación", "Editor de código."),
    "devenv.exe":       ("Visual Studio", "Programación", "IDE de Microsoft."),
    "pycharm64.exe":    ("PyCharm", "Programación", "IDE de Python."),
    "idea64.exe":       ("IntelliJ IDEA", "Programación", "IDE de Java."),
    "studio64.exe":     ("Android Studio", "Programación", "IDE de Android."),
    "eclipse.exe":      ("Eclipse", "Programación", "IDE de Java."),
    "netbeans64.exe":   ("NetBeans", "Programación", "IDE de Java."),
    "sublime_text.exe": ("Sublime Text", "Programación", "Editor de código."),
    # Multimedia / utilidades comunes
    "vlc.exe":          ("VLC Media Player", "Multimedia", "Reproductor multimedia."),
    "wmplayer.exe":     ("Windows Media Player", "Multimedia", "Reproductor de Windows."),
    "zoom.exe":         ("Zoom", "Comunicación", "Videollamadas."),
    "teams.exe":        ("Microsoft Teams", "Comunicación", "Videollamadas y chat."),
    "ms-teams.exe":     ("Microsoft Teams", "Comunicación", "Videollamadas y chat."),
    "winrar.exe":       ("WinRAR", "Utilidades", "Compresor de archivos."),
    "7zfm.exe":         ("7-Zip", "Utilidades", "Compresor de archivos."),
    "7zg.exe":          ("7-Zip", "Utilidades", "Compresor de archivos."),
}

# ── Banco de RUIDO (procesos de fondo) ─────────────────────────────────
# clave -> (nombre_amigable, descripcion, dueno_exe)
#   dueno_exe = "__sistema__"  -> ruido del propio Windows
#   dueno_exe = "<algo>.exe"   -> ruido que pertenece a esa app del banco
RUIDO_SISTEMA = "__sistema__"

RUIDO_CONOCIDO = {
    # Núcleo del sistema operativo
    "svchost.exe":      ("Host de servicios de Windows", "Proceso base del sistema operativo.", RUIDO_SISTEMA),
    "lsass.exe":        ("Subsistema de seguridad", "Autenticación de Windows.", RUIDO_SISTEMA),
    "csrss.exe":        ("Subsistema cliente/servidor", "Proceso base de Windows.", RUIDO_SISTEMA),
    "wininit.exe":      ("Inicio de Windows", "Arranque del sistema.", RUIDO_SISTEMA),
    "winlogon.exe":     ("Inicio de sesión de Windows", "Gestión de inicio de sesión.", RUIDO_SISTEMA),
    "services.exe":     ("Administrador de servicios", "Controla los servicios de Windows.", RUIDO_SISTEMA),
    "smss.exe":         ("Administrador de sesiones", "Proceso base de Windows.", RUIDO_SISTEMA),
    "dwm.exe":          ("Administrador de ventanas", "Composición de escritorio.", RUIDO_SISTEMA),
    "explorer.exe":     ("Explorador de Windows", "Escritorio y barra de tareas.", RUIDO_SISTEMA),
    "taskhostw.exe":    ("Host de tareas", "Tareas en segundo plano de Windows.", RUIDO_SISTEMA),
    "taskhost.exe":     ("Host de tareas", "Tareas en segundo plano de Windows.", RUIDO_SISTEMA),
    "runtimebroker.exe":("Agente de permisos", "Permisos de apps de Windows.", RUIDO_SISTEMA),
    "searchindexer.exe":("Indizador de búsqueda", "Indexa archivos para búsquedas.", RUIDO_SISTEMA),
    "audiodg.exe":      ("Motor de audio", "Procesamiento de audio de Windows.", RUIDO_SISTEMA),
    "conhost.exe":      ("Host de consola", "Ventana de consola de Windows.", RUIDO_SISTEMA),
    "fontdrvhost.exe":  ("Host de fuentes", "Controlador de fuentes.", RUIDO_SISTEMA),
    "spoolsv.exe":      ("Cola de impresión", "Servicio de impresión.", RUIDO_SISTEMA),
    "wmiprvse.exe":     ("Proveedor WMI", "Instrumentación de Windows.", RUIDO_SISTEMA),
    "dllhost.exe":      ("Host COM", "Componentes COM de Windows.", RUIDO_SISTEMA),
    "ctfmon.exe":       ("Entrada de texto", "Teclado/idioma de Windows.", RUIDO_SISTEMA),
    "sihost.exe":       ("Host de shell", "Interfaz de Windows.", RUIDO_SISTEMA),
    "shellexperiencehost.exe": ("Shell de Windows", "Menú Inicio y notificaciones.", RUIDO_SISTEMA),
    "searchhost.exe":   ("Búsqueda de Windows", "Cuadro de búsqueda.", RUIDO_SISTEMA),
    "searchapp.exe":    ("Búsqueda de Windows", "Cuadro de búsqueda.", RUIDO_SISTEMA),
    "startmenuexperiencehost.exe": ("Menú Inicio", "Interfaz del menú Inicio.", RUIDO_SISTEMA),
    "systemsettings.exe": ("Configuración", "Ajustes de Windows.", RUIDO_SISTEMA),
    "systemsettingsbroker.exe": ("Configuración (agente)", "Ajustes de Windows.", RUIDO_SISTEMA),
    "textinputhost.exe":("Entrada de texto", "Teclado en pantalla.", RUIDO_SISTEMA),
    "lockapp.exe":      ("Pantalla de bloqueo", "Bloqueo de Windows.", RUIDO_SISTEMA),
    "applicationframehost.exe": ("Marco de aplicaciones", "Contenedor de apps de Windows.", RUIDO_SISTEMA),
    "pickerhost.exe":   ("Selector de archivos", "Diálogo de Windows para elegir/subir archivos.", RUIDO_SISTEMA),
    "openwith.exe":     ("Abrir con", "Diálogo de Windows para elegir programa.", RUIDO_SISTEMA),
    "backgroundtaskhost.exe": ("Tareas en segundo plano", "Tareas de apps de Windows.", RUIDO_SISTEMA),
    "comppkgsrv.exe":   ("Servicio de paquetes", "Mantenimiento de Windows.", RUIDO_SISTEMA),
    "shellhost.exe":    ("Host de shell", "Interfaz de Windows.", RUIDO_SISTEMA),
    "useroobebroker.exe": ("Agente de bienvenida", "Configuración inicial de Windows.", RUIDO_SISTEMA),
    "unsecapp.exe":     ("Receptor WMI", "Instrumentación de Windows.", RUIDO_SISTEMA),
    "dashost.exe":      ("Host de dispositivos", "Conexión de dispositivos.", RUIDO_SISTEMA),
    "wudfhost.exe":     ("Host de controladores", "Controladores en modo usuario.", RUIDO_SISTEMA),
    "wlanext.exe":      ("Extensiones WLAN", "Servicio de Wi-Fi.", RUIDO_SISTEMA),
    "aggregatorhost.exe": ("Agregador de Windows", "Telemetría/diagnóstico.", RUIDO_SISTEMA),
    "wmiadap.exe":      ("Adaptador WMI", "Mantenimiento de contadores de Windows.", RUIDO_SISTEMA),
    "wmiapsrv.exe":     ("Servicio WMI", "Instrumentación de Windows.", RUIDO_SISTEMA),
    "devicecensus.exe": ("Censo de dispositivo", "Telemetría de inventario de Windows.", RUIDO_SISTEMA),
    "compattelrunner.exe": ("Telemetría de compatibilidad", "Diagnóstico de Windows.", RUIDO_SISTEMA),
    "defrag.exe":       ("Desfragmentador", "Optimización de disco de Windows.", RUIDO_SISTEMA),
    "dstokenclean.exe": ("Limpieza de tokens", "Mantenimiento de Windows.", RUIDO_SISTEMA),
    "provtool.exe":     ("Aprovisionamiento", "Configuración de paquetes de Windows.", RUIDO_SISTEMA),
    "cleanmgr.exe":     ("Liberador de espacio", "Limpieza de disco de Windows.", RUIDO_SISTEMA),
    "vssvc.exe":        ("Instantáneas de volumen", "Copias de sombra de Windows.", RUIDO_SISTEMA),
    "hxtsr.exe":        ("Correo y Calendario", "Sincronización de la app Correo.", RUIDO_SISTEMA),
    "identity_helper.exe": ("Ayudante de identidad", "Componente de Edge/WebView2.", "msedge.exe"),
    # Windows Update / Store / activación / mantenimiento
    "wuauclt.exe":      ("Windows Update", "Actualizaciones de Windows.", RUIDO_SISTEMA),
    "wuaucltcore.exe":  ("Windows Update", "Actualizaciones de Windows.", RUIDO_SISTEMA),
    "usocoreworker.exe":("Windows Update", "Orquestador de actualizaciones.", RUIDO_SISTEMA),
    "mousocoreworker.exe": ("Windows Update", "Orquestador de actualizaciones.", RUIDO_SISTEMA),
    "monotificationux.exe": ("Aviso de actualización", "Notificación de Windows Update.", RUIDO_SISTEMA),
    "tiworker.exe":     ("Mantenimiento de Windows", "Instalador de actualizaciones.", RUIDO_SISTEMA),
    "trustedinstaller.exe": ("Instalador de módulos", "Instalador de Windows.", RUIDO_SISTEMA),
    "sppsvc.exe":       ("Plataforma de protección", "Activación de Windows.", RUIDO_SISTEMA),
    "uhssvc.exe":       ("Estado de Microsoft Update", "Servicio de actualización.", RUIDO_SISTEMA),
    "wermgr.exe":       ("Informe de errores", "Reporte de errores de Windows.", RUIDO_SISTEMA),
    "werfault.exe":     ("Informe de errores", "Reporte de errores de Windows.", RUIDO_SISTEMA),
    "searchprotocolhost.exe": ("Indizador de búsqueda", "Indexado de archivos.", RUIDO_SISTEMA),
    "searchfilterhost.exe": ("Indizador de búsqueda", "Indexado de archivos.", RUIDO_SISTEMA),
    "mscorsvw.exe":     ("Optimización .NET", "Compilación de .NET en segundo plano.", RUIDO_SISTEMA),
    "ngentask.exe":     ("Optimización .NET", "Compilación de .NET.", RUIDO_SISTEMA),
    "ngen.exe":         ("Optimización .NET", "Compilación de .NET.", RUIDO_SISTEMA),
    "sgrmbroker.exe":   ("Vigilancia de Windows", "Integridad del sistema.", RUIDO_SISTEMA),
    "usoclient.exe":    ("Windows Update", "Cliente de orquestación de actualizaciones.", RUIDO_SISTEMA),
    "waasmedicagent.exe": ("Reparación de Windows Update", "Recupera el servicio de actualización.", RUIDO_SISTEMA),
    "dismhost.exe":     ("Mantenimiento de imagen", "Servicio DISM de Windows.", RUIDO_SISTEMA),
    "mcbuilder.exe":    ("Catálogo de Defender", "Reconstrucción de catálogo de seguridad.", RUIDO_SISTEMA),
    "bdeuisrv.exe":     ("BitLocker", "Interfaz de cifrado de unidad.", RUIDO_SISTEMA),
    "lpremove.exe":     ("Limpieza de idiomas", "Mantenimiento de paquetes de idioma.", RUIDO_SISTEMA),
    "dmclient.exe":     ("Administración de dispositivo", "Telemetría de gestión de Windows.", RUIDO_SISTEMA),
    "securityhealthhost.exe": ("Seguridad de Windows", "Host de estado de seguridad.", RUIDO_SISTEMA),
    "la57setup.exe":    ("Configuración de Windows", "Tarea de instalación del sistema.", RUIDO_SISTEMA),
    "wsqmcons.exe":     ("Telemetría de Windows", "Consolidador de calidad de servicio.", RUIDO_SISTEMA),
    "backgroundtransferhost.exe": ("Transferencia en segundo plano", "Descargas del sistema.", RUIDO_SISTEMA),
    "tzsync.exe":       ("Zona horaria", "Sincronización de hora de Windows.", RUIDO_SISTEMA),
    "plugscheduler.exe":("Mantenimiento programado", "Tareas de mantenimiento de Windows.", RUIDO_SISTEMA),
    "sdiagnhost.exe":   ("Solucionador de problemas", "Diagnóstico de Windows.", RUIDO_SISTEMA),
    "ruximics.exe":     ("Experiencia de usuario", "Telemetría de Windows.", RUIDO_SISTEMA),
    "storedesktopextension.exe": ("Microsoft Store", "Extensión de la tienda.", RUIDO_SISTEMA),
    "windowspackagemanagerserver.exe": ("Administrador de paquetes", "Servicio de winget.", RUIDO_SISTEMA),
    "disksnapshot.exe": ("Instantánea de disco", "Copia de sombra de Windows.", RUIDO_SISTEMA),
    "softlandingtask.exe": ("Sugerencias de Windows", "Tarea de recomendaciones.", RUIDO_SISTEMA),
    "uieorchestrator.exe": ("Experiencia de Windows", "Orquestador de interfaz.", RUIDO_SISTEMA),
    "uieorchestratorstub.exe": ("Experiencia de Windows", "Orquestador de interfaz.", RUIDO_SISTEMA),
    "consent.exe":      ("Control de cuentas (UAC)", "Diálogo de elevación de Windows.", RUIDO_SISTEMA),
    "dataexchangehost.exe": ("Intercambio de datos", "Servicio de Windows.", RUIDO_SISTEMA),
    "dxgiadaptercache.exe": ("Caché de gráficos", "Componente DirectX.", RUIDO_SISTEMA),
    "sdbinst.exe":      ("Compatibilidad de apps", "Instalador de base de datos de compatibilidad.", RUIDO_SISTEMA),
    "midisrv.exe":      ("Servicio MIDI", "Audio del sistema.", RUIDO_SISTEMA),
    "speechmodeldownload.exe": ("Voz de Windows", "Descarga de modelos de voz.", RUIDO_SISTEMA),
    "sppextcomobj.exe": ("Activación de Windows", "Componente de licencias.", RUIDO_SISTEMA),
    "srtasks.exe":      ("Restaurar sistema", "Tareas de puntos de restauración.", RUIDO_SISTEMA),
    "tabtip.exe":       ("Teclado táctil", "Entrada en pantalla de Windows.", RUIDO_SISTEMA),
    "ucpdmgr.exe":      ("Protección de configuración", "Mantenimiento de Windows.", RUIDO_SISTEMA),
    "apphostregistrationverifier.exe": ("Verificador de apps", "Mantenimiento de Windows.", RUIDO_SISTEMA),
    "crossdeviceservice.exe": ("Vínculo entre dispositivos", "Servicio de continuidad.", RUIDO_SISTEMA),
    "dcv2.exe":         ("Censo de dispositivo", "Telemetría de Windows.", RUIDO_SISTEMA),
    "dcv2_startup.exe": ("Censo de dispositivo", "Telemetría de Windows.", RUIDO_SISTEMA),
    "mpdefendercoreservice.exe": ("Antivirus de Windows", "Servicio principal de Defender.", RUIDO_SISTEMA),
    "am_delta.exe":     ("Antivirus de Windows", "Actualización de firmas de Defender.", RUIDO_SISTEMA),
    "microsoft.data.usageandqualityinsights.maintenancetask.exe": ("Telemetría de Office", "Tarea de mantenimiento de Office.", "winword.exe"),
    "integrator.exe":   ("Integración de Office", "Mantenimiento de Office.", "winword.exe"),
    "platform_experience_helper.exe": ("Experiencia de plataforma", "Servicio del fabricante del equipo.", RUIDO_SISTEMA),
    "gamebarpresencewriter.exe": ("Xbox Game Bar", "Estado de presencia de juego.", RUIDO_SISTEMA),
    "xboxgamebarwidgets.exe": ("Xbox Game Bar", "Widgets de juego.", RUIDO_SISTEMA),
    "xboxpcappft.exe":  ("Aplicación Xbox", "Servicio de Xbox.", RUIDO_SISTEMA),
    "edgegameassist.exe": ("Edge", "Asistente de juego de Edge.", "msedge.exe"),
    "crashpad_handler.exe": ("Reporte de fallos", "Manejador de errores de aplicaciones.", RUIDO_SISTEMA),
    # Windows Defender (antivirus integrado)
    "msmpeng.exe":      ("Antivirus de Windows", "Motor de Windows Defender.", RUIDO_SISTEMA),
    "nissrv.exe":       ("Antivirus de Windows", "Inspección de red de Defender.", RUIDO_SISTEMA),
    "securityhealthservice.exe": ("Seguridad de Windows", "Estado de seguridad.", RUIDO_SISTEMA),
    "securityhealthsystray.exe": ("Seguridad de Windows", "Icono de seguridad.", RUIDO_SISTEMA),
    "smartscreen.exe":  ("SmartScreen", "Filtro de reputación de Windows.", RUIDO_SISTEMA),
    "mpcmdrun.exe":     ("Antivirus de Windows", "Análisis de Windows Defender.", RUIDO_SISTEMA),
    "mpsigstub.exe":    ("Antivirus de Windows", "Actualización de firmas de Defender.", RUIDO_SISTEMA),
    "mpdlpcmd.exe":     ("Antivirus de Windows", "Prevención de pérdida de datos de Defender.", RUIDO_SISTEMA),
    # Widgets / Xbox / experiencias varias
    "widgetservice.exe":("Widgets", "Servicio de widgets de Windows.", RUIDO_SISTEMA),
    "widgets.exe":      ("Widgets", "Panel de widgets.", RUIDO_SISTEMA),
    "widgetboard.exe":  ("Widgets", "Panel de widgets.", RUIDO_SISTEMA),
    "phoneexperiencehost.exe": ("Vínculo con el teléfono", "Conexión con móvil.", RUIDO_SISTEMA),
    "gamebar.exe":      ("Xbox Game Bar", "Barra de juego de Windows.", RUIDO_SISTEMA),
    "gamebarftserver.exe": ("Xbox Game Bar", "Barra de juego de Windows.", RUIDO_SISTEMA),
    # OneDrive (ruido de Microsoft)
    "onedrive.exe":     ("OneDrive", "Sincronización de archivos de Microsoft.", RUIDO_SISTEMA),
    "onedrivelauncher.exe": ("OneDrive", "Inicio de OneDrive.", RUIDO_SISTEMA),
    "onedrivestandaloneupdater.exe": ("Actualizador de OneDrive", "Actualizaciones de OneDrive.", RUIDO_SISTEMA),
    "filecoauth.exe":   ("OneDrive", "Coautoría de archivos.", RUIDO_SISTEMA),
    "filesyncconfig.exe": ("OneDrive", "Configuración de sincronización.", RUIDO_SISTEMA),
    # Office (ruido que pertenece a la suite Office)
    "officeclicktorun.exe": ("Office", "Mantenimiento de Office.", "winword.exe"),
    "officec2rclient.exe": ("Office", "Actualizador de Office.", "winword.exe"),
    "sdxhelper.exe":    ("Office", "Asistente de Office.", "winword.exe"),
    "msosync.exe":      ("Office", "Sincronización de Office.", "winword.exe"),
    "appvshnotify.exe": ("Office", "Virtualización de Office.", "winword.exe"),
    "splwow64.exe":     ("Impresión (32→64)", "Puente de impresión.", RUIDO_SISTEMA),
    # Edge / WebView (ruido que pertenece a Edge)
    "microsoftedgeupdate.exe": ("Actualizador de Edge", "Actualizaciones de Edge.", "msedge.exe"),
    "msedgewebview2.exe": ("WebView de Edge", "Componente web embebido.", "msedge.exe"),
    # Chrome (ruido que pertenece a Chrome)
    "googleupdate.exe": ("Actualizador de Google", "Actualizaciones de Chrome.", "chrome.exe"),
    "googlecrashhandler.exe": ("Google Crash Handler", "Reporte de fallos de Chrome.", "chrome.exe"),
    "googlecrashhandler64.exe": ("Google Crash Handler", "Reporte de fallos de Chrome.", "chrome.exe"),
    "elevation_service.exe": ("Servicio de Chrome", "Servicio de actualización de Chrome.", "chrome.exe"),
    # NVIDIA (ruido de drivers de video)
    "nvprofileupdater64.exe": ("Driver NVIDIA", "Actualizador de perfiles NVIDIA.", RUIDO_SISTEMA),
    "nvprofileupdater.exe": ("Driver NVIDIA", "Actualizador de perfiles NVIDIA.", RUIDO_SISTEMA),
    "nvcontainer.exe":  ("Driver NVIDIA", "Contenedor de servicios NVIDIA.", RUIDO_SISTEMA),
    "nvsphelper64.exe": ("Driver NVIDIA", "Asistente NVIDIA.", RUIDO_SISTEMA),
    "nvdisplay.container.exe": ("Driver NVIDIA", "Servicio de pantalla NVIDIA.", RUIDO_SISTEMA),
    # El propio cliente del kiosco (nunca debe contar como actividad)
    "controlbiblioteca.client.exe": ("Cliente de Control Biblioteca", "Software del kiosco (este sistema).", RUIDO_SISTEMA),
}


def describir(nombre_exe: str):
    """Devuelve sugerencia para un .exe desconocido: (tipo, nombre_amigable,
    descripcion, dueno_exe). tipo ∈ {'app','ruido',None}. Sirve para que el
    panel sugiera cómo clasificar un pendiente. None = no se reconoce."""
    k = (nombre_exe or "").strip().lower()
    if not k:
        return (None, None, None, None)
    if k in APPS_CONOCIDAS:
        nom, cat, desc = APPS_CONOCIDAS[k]
        return ("app", nom, desc, None)
    if k in RUIDO_CONOCIDO:
        nom, desc, dueno = RUIDO_CONOCIDO[k]
        return ("ruido", nom, desc, dueno)
    return (None, None, None, None)
