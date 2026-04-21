using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;
using Microsoft.Win32;

namespace ControlBiblioteca.Client.Services
{
    /// <summary>
    /// Instalador de toque único.
    /// Primero analiza la máquina con DiagnosticoSistema y luego adapta
    /// cada paso al entorno real encontrado — sin asumir letras de unidad,
    /// versiones de Windows ni servicios específicos.
    /// </summary>
    public static class InstaladorKiosco
    {
        private const string NOMBRE_EXE = "ControlBiblioteca.Client.exe";

        /// <summary>
        /// True si la tarea watchdog falta, el Shell no es explorer.exe,
        /// o el exe no existe en la ruta de instalación.
        /// </summary>
        public static bool EsNecesario()
        {
            string unidad       = System.IO.Path.GetPathRoot(Environment.SystemDirectory) ?? "C:\\";
            string exeInstalado = System.IO.Path.Combine(unidad, "SistemaBiblioteca", NOMBRE_EXE);

            if (!StartupConfigurator.EstaConfigurado(exeInstalado)) return true;
            if (!System.IO.File.Exists(exeInstalado))               return true;

            return false;
        }

        /// <summary>Muestra la ventana de instalación (bloqueante).</summary>
        public static void Ejecutar()
        {
            var ventana = new VentanaInstalacion();
            Application.Current.MainWindow = ventana;
            ventana.ShowDialog();
        }

        // ── Lógica principal ─────────────────────────────────────────────────────

        internal static async Task InstalarAsync(IProgress<Paso> progreso)
        {
            await Task.Run(() =>
            {
                // ── FASE 1: Análisis de la máquina ────────────────────────────────
                progreso.Report(Paso.Info("Analizando configuración de la máquina…"));
                var diag = DiagnosticoSistema.Analizar();

                progreso.Report(Paso.Ok($"Windows {diag.VersionWindows}  ·  " +
                    $"build {diag.BuildWindows}  ·  " +
                    $"{(diag.Es64Bits ? "64 bits" : "32 bits")}"));

                progreso.Report(Paso.Ok($"Unidad del sistema: {diag.UnidadSistema}"));
                progreso.Report(Paso.Ok($"Ruta de instalación: {diag.RutaInstalacion}"));
                progreso.Report(Paso.Ok($"Shell actual: {diag.ShellActual}"));
                progreso.Report(Paso.Ok($"Espacio libre: {diag.EspacioLibreMB} MB"));
                progreso.Report(Paso.Ok($"PowerCfg disponible: {(diag.PowerCfgDisponible ? "Sí" : "No")}"));
                progreso.Report(Paso.Ok($"Servicios de red encontrados: {string.Join(", ", diag.ServiciosRedDisponibles)}"));

                foreach (string aviso in diag.Advertencias)
                    progreso.Report(Paso.Aviso(aviso));

                string exeInstalado = Path.Combine(diag.RutaInstalacion, NOMBRE_EXE);

                // ── FASE 1.5: Limpiar instalaciones anteriores ────────────────────
                progreso.Report(Paso.Inicio("Limpiando configuraciones e instalaciones anteriores…"));
                int limpiezas = LimpiarInstalacionesAnteriores(diag, progreso);
                progreso.Report(Paso.Ok($"{limpiezas} elemento(s) limpiado(s)."));

                // ── FASE 2: Directorio ────────────────────────────────────────────
                progreso.Report(Paso.Inicio("Preparando directorio de instalación…"));
                Directory.CreateDirectory(diag.RutaInstalacion);
                Directory.CreateDirectory(Path.Combine(diag.RutaInstalacion, "Backups"));
                progreso.Report(Paso.Ok("Directorios listos."));

                // ── FASE 3: Copia de archivos ─────────────────────────────────────
                string origen      = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
                bool   yaEnDestino = origen.Equals(diag.RutaInstalacion,
                    StringComparison.OrdinalIgnoreCase);

                if (!yaEnDestino)
                {
                    progreso.Report(Paso.Inicio("Copiando archivos del programa…"));
                    CopiarDirectorio(origen, diag.RutaInstalacion, progreso);
                    progreso.Report(Paso.Ok("Archivos copiados correctamente."));
                }
                else
                {
                    progreso.Report(Paso.Ok("Archivos ya en destino — se omite la copia."));
                }

                // ── FASE 4: Shell de Windows → explorer.exe ─────────────────────────
                progreso.Report(Paso.Inicio("Asegurando Shell = explorer.exe (Winlogon)…"));
                ConfigurarShell(exeInstalado, diag);
                progreso.Report(Paso.Ok("Shell → explorer.exe (kiosco se lanza vía tarea programada)"));

                // ── FASE 5: Tarea watchdog ────────────────────────────────────────
                string accionTarea = diag.TareaWatchdogExiste ? "Actualizando" : "Registrando";
                progreso.Report(Paso.Inicio($"{accionTarea} tarea watchdog en Programador de Tareas…"));
                StartupConfigurator.RegistrarTareaWatchdog(exeInstalado);
                progreso.Report(Paso.Ok("Tarea 'ControlBibliotecaKiosco' lista."));

                // ── FASE 6: Accesibilidad ─────────────────────────────────────────
                progreso.Report(Paso.Inicio("Desactivando atajos de accesibilidad…"));
                DesactivarAccesibilidad();
                progreso.Report(Paso.Ok("Sticky Keys, Filter Keys y Toggle Keys desactivados."));

                // ── FASE 7: Protector de pantalla y Lock Screen ───────────────────
                progreso.Report(Paso.Inicio("Desactivando protector de pantalla y Lock Screen…"));
                DesactivarProtectorYBloqueo();
                progreso.Report(Paso.Ok("Protector de pantalla y pantalla de bloqueo desactivados."));

                // ── FASE 8: Plan de energía (solo si powercfg existe) ─────────────
                if (diag.PowerCfgDisponible)
                {
                    progreso.Report(Paso.Inicio("Aplicando plan de energía (sin suspensión)…"));
                    AplicarPlanEnergia();
                    progreso.Report(Paso.Ok("Monitor y suspensión configurados en 'nunca'."));
                }
                else
                {
                    progreso.Report(Paso.Aviso("powercfg no disponible — plan de energía omitido."));
                }

                // ── FASE 9: Windows Update (sin reinicio automático) ──────────────
                progreso.Report(Paso.Inicio("Bloqueando reinicio automático de Windows Update…"));
                BloquearReinicioUpdate();
                progreso.Report(Paso.Ok("Windows Update no reiniciará sin confirmación."));

                // ── FASE 9.5: Bloquear Task Manager permanente (HKCU + HKLM) ──────
                //   HKLM: bloqueo a nivel máquina — inamovible sin admin.
                //   HKCU: capa adicional que aplica inmediatamente al usuario.
                //   Ambas persisten tras reinicio. El alumno NUNCA puede abrir TaskMgr.
                progreso.Report(Paso.Inicio("Bloqueando Administrador de Tareas (HKCU + HKLM)…"));
                BloquearTaskManagerPermanente();
                progreso.Report(Paso.Ok("Task Manager bloqueado a nivel usuario y máquina."));

                // ── FASE 10: NetworkEnsurer — guardar lista de servicios ──────────
                progreso.Report(Paso.Inicio("Guardando perfil de servicios de red detectados…"));
                GuardarPerfilServicios(diag, diag.RutaInstalacion);
                progreso.Report(Paso.Ok($"{diag.ServiciosRedDisponibles.Count} servicio(s) en perfil."));

                // ── FASE 11: Servicio de Windows (pre-calentamiento antes del login) ─
                progreso.Report(Paso.Inicio("Registrando servicio de arranque anticipado…"));
                StartupConfigurator.RegistrarServicioPreWarm(diag.RutaInstalacion);
                bool svcInstalado = File.Exists(
                    Path.Combine(diag.RutaInstalacion, "ControlBiblioteca.Service.exe"));
                progreso.Report(svcInstalado
                    ? Paso.Ok("Servicio 'ControlBibliotecaPreWarm' registrado — arranca antes del login.")
                    : Paso.Aviso("Service.exe no encontrado — pre-calentamiento omitido (opcional)."));
            });
        }

        // ── Pasos individuales ───────────────────────────────────────────────────

        private static void CopiarDirectorio(string origen, string destino,
            IProgress<Paso> progreso)
        {
            foreach (string archivo in Directory.GetFiles(origen, "*", SearchOption.AllDirectories))
            {
                string ext = Path.GetExtension(archivo).ToLowerInvariant();
                if (ext is ".pdb") continue;

                string relativo    = archivo[origen.Length..].TrimStart('\\', '/');
                string rutaDestino = Path.Combine(destino, relativo);

                Directory.CreateDirectory(Path.GetDirectoryName(rutaDestino)!);
                try
                {
                    File.Copy(archivo, rutaDestino, overwrite: true);
                    progreso.Report(Paso.Detalle($"→ {relativo}"));
                }
                catch (IOException)
                {
                    progreso.Report(Paso.Aviso($"En uso, omitido: {relativo}"));
                }
            }
        }

        private static void ConfigurarShell(string exeInstalado, DiagnosticoSistema diag)
        {
            const string WinlogonKey =
                @"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon";

            using RegistryKey key = Registry.LocalMachine.OpenSubKey(WinlogonKey, writable: true)
                ?? throw new InvalidOperationException("No se pudo abrir HKLM\\Winlogon.");

            // Guardar Shell original si no existe respaldo
            string backupPath = Path.Combine(diag.RutaInstalacion, "Backups", "OriginalShell.txt");
            if (!File.Exists(backupPath))
                File.WriteAllText(backupPath, diag.ShellActual);

            // Siempre forzar explorer.exe como Shell oficial.
            // El kiosco se lanza mediante la tarea programada (watchdog), no como Shell.
            // Esto corrige la pantalla negra de la arquitectura anterior.
            key.SetValue("Shell", "explorer.exe", RegistryValueKind.String);
        }

        private static void DesactivarAccesibilidad()
        {
            using (var k = Registry.CurrentUser.CreateSubKey(
                @"Control Panel\Accessibility\StickyKeys"))
                k.SetValue("Flags", "506", RegistryValueKind.String);

            using (var k = Registry.CurrentUser.CreateSubKey(
                @"Control Panel\Accessibility\Keyboard Response"))
                k.SetValue("Flags", "122", RegistryValueKind.String);

            using (var k = Registry.CurrentUser.CreateSubKey(
                @"Control Panel\Accessibility\ToggleKeys"))
                k.SetValue("Flags", "58", RegistryValueKind.String);
        }

        private static void DesactivarProtectorYBloqueo()
        {
            try
            {
                using var k = Registry.CurrentUser.CreateSubKey(@"Control Panel\Desktop");
                k.SetValue("ScreenSaveActive",    "0", RegistryValueKind.String);
                k.SetValue("ScreenSaverIsSecure", "0", RegistryValueKind.String);
            }
            catch { }

            try
            {
                using var k = Registry.LocalMachine.CreateSubKey(
                    @"SOFTWARE\Policies\Microsoft\Windows\Personalization");
                k.SetValue("NoLockScreen", 1, RegistryValueKind.DWord);
            }
            catch { }

            try
            {
                using var k = Registry.LocalMachine.CreateSubKey(
                    @"SOFTWARE\Microsoft\Windows\Windows Error Reporting");
                k.SetValue("Disabled", 1, RegistryValueKind.DWord);
            }
            catch { }
        }

        private static void AplicarPlanEnergia()
        {
            // Usamos cambios individuales en vez de activar un GUID fijo
            // porque el GUID del plan "Alto rendimiento" varía por OEM y edición
            RunCmd("powercfg.exe", "/change monitor-timeout-ac 0");
            RunCmd("powercfg.exe", "/change monitor-timeout-dc 0");
            RunCmd("powercfg.exe", "/change standby-timeout-ac 0");
            RunCmd("powercfg.exe", "/change standby-timeout-dc 0");
            RunCmd("powercfg.exe", "/change hibernate-timeout-ac 0");
            RunCmd("powercfg.exe", "/h off");
        }

        private static void BloquearReinicioUpdate()
        {
            try
            {
                using var k = Registry.LocalMachine.CreateSubKey(
                    @"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU");
                k.SetValue("NoAutoRebootWithLoggedOnUsers", 1, RegistryValueKind.DWord);
                k.SetValue("AUOptions", 2, RegistryValueKind.DWord);
            }
            catch { }
        }

        /// <summary>
        /// Guarda la lista de servicios detectados en un archivo de texto para que
        /// NetworkEnsurer en App.xaml.cs lo lea y solo vigile los que existen.
        /// </summary>
        private static void GuardarPerfilServicios(DiagnosticoSistema diag, string rutaInstalacion)
        {
            try
            {
                string perfil = Path.Combine(rutaInstalacion, "network_services.txt");
                File.WriteAllLines(perfil, diag.ServiciosRedDisponibles);
            }
            catch { }
        }

        /// <summary>
        /// Escribe DisableTaskMgr=1 en HKCU y HKLM para bloqueo total.
        /// HKLM requiere admin (el instalador corre elevado).
        /// HKCU es la capa rápida que aplica sin reiniciar.
        /// </summary>
        private static void BloquearTaskManagerPermanente()
        {
            // Capa 1: HKCU — aplica inmediatamente al usuario actual
            try
            {
                using var key = Registry.CurrentUser.CreateSubKey(
                    @"Software\Microsoft\Windows\CurrentVersion\Policies\System", true);
                key?.SetValue("DisableTaskMgr", 1, RegistryValueKind.DWord);
            }
            catch { }

            // Capa 2: HKLM — bloqueo a nivel máquina, inamovible sin admin
            try
            {
                using var key = Registry.LocalMachine.CreateSubKey(
                    @"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System", true);
                key?.SetValue("DisableTaskMgr", 1, RegistryValueKind.DWord);
            }
            catch { }
        }

        /// <summary>
        /// Detecta y limpia instalaciones anteriores: Shell viejo, tareas antiguas,
        /// claves de registro Run, y carpetas de versiones previas en otras ubicaciones.
        /// </summary>
        private static int LimpiarInstalacionesAnteriores(DiagnosticoSistema diag, IProgress<Paso> progreso)
        {
            int count = 0;

            // 1. Si el Shell de Winlogon apunta a nuestro exe (arquitectura vieja → pantalla negra)
            //    restaurarlo a explorer.exe antes de configurar la nueva arquitectura
            if (!diag.ShellActual.Equals("explorer.exe", StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    using var k = Registry.LocalMachine.OpenSubKey(
                        @"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon", writable: true);
                    if (k != null)
                    {
                        k.SetValue("Shell", "explorer.exe", RegistryValueKind.String);
                        progreso.Report(Paso.Detalle($"→ Shell restaurado de '{diag.ShellActual}' a explorer.exe"));
                        count++;
                    }
                }
                catch { }
            }

            // 2. Limpiar Userinit si contiene referencias a nuestro exe
            try
            {
                using var k = Registry.LocalMachine.OpenSubKey(
                    @"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon", writable: true);
                if (k != null)
                {
                    string userinit = k.GetValue("Userinit")?.ToString() ?? "";
                    if (userinit.Contains(NOMBRE_EXE, StringComparison.OrdinalIgnoreCase))
                    {
                        string limpio = userinit;
                        // Eliminar cualquier referencia al exe (con o sin comillas, con o sin coma)
                        foreach (string patron in new[] { "SistemaBiblioteca", NOMBRE_EXE })
                        {
                            int idx;
                            while ((idx = limpio.IndexOf(patron, StringComparison.OrdinalIgnoreCase)) >= 0)
                            {
                                // Buscar el inicio de esta ruta (desde la coma o inicio)
                                int inicio = limpio.LastIndexOf(',', idx);
                                if (inicio < 0) inicio = 0;
                                // Buscar el final (hasta la coma siguiente o fin)
                                int fin = limpio.IndexOf(',', idx);
                                if (fin < 0) fin = limpio.Length;
                                else fin++; // incluir la coma
                                limpio = limpio.Remove(inicio, fin - inicio);
                            }
                        }
                        // Asegurar que Userinit termina con la ruta base válida
                        if (!limpio.Contains("userinit.exe", StringComparison.OrdinalIgnoreCase))
                            limpio = @"C:\Windows\system32\userinit.exe,";
                        k.SetValue("Userinit", limpio, RegistryValueKind.String);
                        progreso.Report(Paso.Detalle("→ Userinit limpiado"));
                        count++;
                    }
                }
            }
            catch { }

            // 3. Eliminar tareas de versiones anteriores (nombres antiguos)
            foreach (string tareaVieja in new[] { "ArranqueBiblioteca", "ControlBibliotecaKiosco" })
            {
                try
                {
                    var psi = new ProcessStartInfo("schtasks.exe", $"/Delete /TN \"{tareaVieja}\" /F")
                    {
                        UseShellExecute = false,
                        CreateNoWindow  = true,
                        RedirectStandardOutput = true,
                        RedirectStandardError  = true
                    };
                    using var p = Process.Start(psi);
                    p?.WaitForExit(5_000);
                    if (p?.ExitCode == 0)
                    {
                        progreso.Report(Paso.Detalle($"→ Tarea '{tareaVieja}' eliminada"));
                        count++;
                    }
                }
                catch { }
            }

            // 4. Limpiar clave Run (autoarranque antiguo)
            try
            {
                using var run = Registry.CurrentUser.OpenSubKey(
                    @"Software\Microsoft\Windows\CurrentVersion\Run", writable: true);
                if (run?.GetValue("ControlBiblioteca") != null)
                {
                    run.DeleteValue("ControlBiblioteca", throwOnMissingValue: false);
                    progreso.Report(Paso.Detalle("→ Clave Run eliminada"));
                    count++;
                }
            }
            catch { }

            // 5. Buscar y matar procesos residuales del kiosco (otra instancia vieja)
            try
            {
                string miPid = Environment.ProcessId.ToString();
                foreach (var proc in Process.GetProcessesByName("ControlBiblioteca.Client"))
                {
                    if (proc.Id.ToString() != miPid)
                    {
                        proc.Kill();
                        progreso.Report(Paso.Detalle($"→ Proceso residual PID {proc.Id} terminado"));
                        count++;
                    }
                }
            }
            catch { }

            return count;
        }

        private static void RunCmd(string cmd, string args)
        {
            try
            {
                Process.Start(new ProcessStartInfo(cmd, args)
                {
                    UseShellExecute        = false,
                    CreateNoWindow         = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError  = true
                })?.WaitForExit(10_000);
            }
            catch { }
        }

        // ── Modelo de progreso ───────────────────────────────────────────────────

        internal record Paso(string Texto, TipoPaso Tipo)
        {
            public static Paso Info  (string t) => new(t, TipoPaso.Info);
            public static Paso Inicio(string t) => new(t, TipoPaso.Inicio);
            public static Paso Ok    (string t) => new(t, TipoPaso.Ok);
            public static Paso Aviso (string t) => new(t, TipoPaso.Aviso);
            public static Paso Detalle(string t)=> new(t, TipoPaso.Detalle);
        }

        internal enum TipoPaso { Info, Inicio, Ok, Aviso, Detalle }

        // ── Ventana de progreso ──────────────────────────────────────────────────

        internal sealed class VentanaInstalacion : Window
        {
            private readonly ProgressBar  _barra;
            private readonly StackPanel   _logPanel;
            private readonly ScrollViewer _scroll;
            private readonly TextBlock    _estadoText;
            private readonly Button       _btnReiniciar;
            private int _pasosOk;
            private const int TOTAL_FASES = 11;

            public VentanaInstalacion()
            {
                Title                 = "Instalación — Kiosco Biblioteca UNASAM";
                Width                 = 600;
                Height                = 560;
                WindowStyle           = WindowStyle.None;
                ResizeMode            = ResizeMode.NoResize;
                WindowStartupLocation = WindowStartupLocation.CenterScreen;
                Topmost               = true;
                Background            = new SolidColorBrush(Color.FromRgb(13, 17, 27));

                var root = new Grid();
                root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
                root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
                root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
                root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
                root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

                // Header
                var header = new Border
                {
                    Background = new SolidColorBrush(Color.FromRgb(21, 32, 52)),
                    Padding    = new Thickness(24, 18, 24, 16)
                };
                var headerPanel = new StackPanel();
                headerPanel.Children.Add(new TextBlock
                {
                    Text       = "Configuración automática del sistema",
                    Foreground = new SolidColorBrush(Color.FromRgb(96, 165, 250)),
                    FontSize   = 16,
                    FontWeight = FontWeights.SemiBold
                });
                headerPanel.Children.Add(new TextBlock
                {
                    Text       = "Analizando la máquina y aplicando configuración de kiosco…",
                    Foreground = new SolidColorBrush(Color.FromRgb(148, 163, 184)),
                    FontSize   = 11,
                    Margin     = new Thickness(0, 4, 0, 0)
                });
                header.Child = headerPanel;
                Grid.SetRow(header, 0);
                root.Children.Add(header);

                // Barra de progreso
                _barra = new ProgressBar
                {
                    Minimum    = 0,
                    Maximum    = 100,
                    Value      = 0,
                    Height     = 5,
                    Foreground = new SolidColorBrush(Color.FromRgb(34, 197, 94)),
                    Background = new SolidColorBrush(Color.FromRgb(30, 41, 59))
                };
                Grid.SetRow(_barra, 1);
                root.Children.Add(_barra);

                // Log de pasos
                _logPanel = new StackPanel { Margin = new Thickness(20, 10, 20, 0) };
                _scroll   = new ScrollViewer
                {
                    Content = _logPanel,
                    VerticalScrollBarVisibility = ScrollBarVisibility.Auto
                };
                Grid.SetRow(_scroll, 2);
                root.Children.Add(_scroll);

                // Estado actual
                _estadoText = new TextBlock
                {
                    Foreground   = new SolidColorBrush(Color.FromRgb(100, 116, 139)),
                    FontSize     = 10,
                    Margin       = new Thickness(20, 8, 20, 4),
                    TextTrimming = TextTrimming.CharacterEllipsis
                };
                Grid.SetRow(_estadoText, 3);
                root.Children.Add(_estadoText);

                // Botón reiniciar
                _btnReiniciar = new Button
                {
                    Content         = "✓   Reiniciar ahora para activar el modo kiosco",
                    FontSize        = 13,
                    FontWeight      = FontWeights.SemiBold,
                    Padding         = new Thickness(0, 14, 0, 14),
                    Margin          = new Thickness(20, 4, 20, 18),
                    Background      = new SolidColorBrush(Color.FromRgb(21, 128, 61)),
                    Foreground      = Brushes.White,
                    BorderThickness = new Thickness(0),
                    Visibility      = Visibility.Collapsed,
                    Cursor          = System.Windows.Input.Cursors.Hand
                };
                _btnReiniciar.Click += (_, _) =>
                {
                    RunCmd("shutdown.exe", "/r /t 5 /c \"Activando modo kiosco\"");
                    Application.Current.Shutdown();
                };
                Grid.SetRow(_btnReiniciar, 4);
                root.Children.Add(_btnReiniciar);

                Content = root;

                Loaded += async (_, _) =>
                {
                    var progress = new Progress<Paso>(AgregarLinea);
                    try
                    {
                        await InstaladorKiosco.InstalarAsync(progress);
                        AgregarLinea(Paso.Ok("Instalación completada exitosamente."));
                        _barra.Value = 100;
                        _estadoText.Text = "Todo listo. Reinicie la máquina para activar el kiosco.";
                        _btnReiniciar.Visibility = Visibility.Visible;
                    }
                    catch (Exception ex)
                    {
                        AgregarLinea(Paso.Aviso($"ERROR FATAL: {ex.Message}"));
                        _estadoText.Text = "La instalación falló. Revise el log.";
                    }
                };
            }

            private void AgregarLinea(Paso paso)
            {
                if (paso.Tipo == TipoPaso.Ok && !paso.Texto.StartsWith("→"))
                {
                    _pasosOk++;
                    _barra.Value = Math.Min(99, _pasosOk * 100 / TOTAL_FASES);
                }

                var (icono, color, size) = paso.Tipo switch
                {
                    TipoPaso.Ok     => ("✓ ", Color.FromRgb(74, 222, 128),  11),
                    TipoPaso.Inicio => ("► ", Color.FromRgb(250, 204, 21),  11),
                    TipoPaso.Aviso  => ("⚠ ", Color.FromRgb(251, 146, 60),  11),
                    TipoPaso.Detalle=> ("   ", Color.FromRgb(71, 85, 105),   9),
                    _               => ("  ", Color.FromRgb(148, 163, 184), 11)
                };

                _logPanel.Children.Add(new TextBlock
                {
                    Text         = icono + paso.Texto,
                    Foreground   = new SolidColorBrush(color),
                    FontSize     = size,
                    Margin       = new Thickness(0, 1, 0, 1),
                    TextWrapping = TextWrapping.Wrap
                });

                _estadoText.Text = paso.Texto;
                _scroll.ScrollToBottom();
            }
        }
    }
}
