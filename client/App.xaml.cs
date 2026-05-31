using System;
using System.Diagnostics;
using System.IO;
using System.Security.Principal;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Threading;
using ControlBiblioteca.Client.Services;
using ControlBiblioteca.Client.UI;
using Microsoft.Win32;

namespace ControlBiblioteca.Client
{
    public partial class App : Application
    {
        private static Mutex? _mutex;
        private static bool _esDuenoMutex;

        // ── Log de arranque / crash ──────────────────────────────────────────────
        private static readonly string _crashLog = Path.Combine(
            Path.GetPathRoot(Environment.SystemDirectory) ?? "C:\\",
            "SistemaBiblioteca", "crash.log");

        internal static void AppLog(string msg)
        {
            string linea = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss.fff} [App] {msg}";
            Debug.WriteLine(linea);
            try { File.AppendAllText(_crashLog, linea + Environment.NewLine); } catch { }
        }

        // ── Bloqueo pre-ventana ───────────────────────────────────────────────────
        // El constructor estático es invocado por el CLR antes de que se cree
        // cualquier instancia y antes de Application_Startup.
        // Aquí solo aplicamos las capas de registro (no necesitan message loop):
        // DisableTaskMgr HKCU/HKLM + IFEO taskmgr.exe.
        // El hook de teclado se instala en Application_Startup porque WH_KEYBOARD_LL
        // necesita que el message loop de WPF esté activo para despachar callbacks.
        static App()
        {
            SecurityManager.BloquearRegistroEstatico();
        }

        // SecurityManager: activo desde el primer milisegundo del proceso
        internal readonly SecurityManager Security = new();

        // UIWatchdog
        private int _dispatcherTick;
        private Thread? _watchdogThread;
        private DispatcherTimer? _heartbeatTimer;

        // NetworkEnsurer
        private Thread? _networkThread;

        // Puerta trasera Ctrl+Alt+F12 + PIN
        private MantenimientoBackdoor? _backdoor;

        public volatile bool CerrandoApp;

        // ── Punto de entrada ─────────────────────────────────────────────────────

        private void Application_Startup(object sender, StartupEventArgs e)
        {
            // Evitar cierres automáticos e indeseados por WPF al cerrar ventanas temporales (ej: splash, diálogos).
            // La aplicación solo terminará cuando invoquemos explícitamente Environment.Exit o Current.Shutdown.
            ShutdownMode = ShutdownMode.OnExplicitShutdown;

            AppLog($"=== INICIO proceso PID={Environment.ProcessId} ===");

            // ── PRIMERO: bloquear antes de cualquier ventana ──────────────────────
            // Cubre la brecha entre el login de Windows y que el kiosco cargue.
            Security.Bloquear();
            AppLog("Security.Bloquear() OK");

            // ── Instancia única ───────────────────────────────────────────────────
            _mutex = new Mutex(true, "Global\\UNASAM_Biblioteca_Kiosco", out bool createdNew);
            _esDuenoMutex = createdNew;
            AppLog($"Mutex createdNew={createdNew}");
            if (!createdNew)
            {
                AppLog("Segunda instancia detectada — saliendo.");
                Security.Desbloquear();
                Current.Shutdown();
                return;
            }

            // ── ¿Necesita instalación? ────────────────────────────────────────────
            bool necesitaInstal = InstaladorKiosco.EsNecesario();
            AppLog($"InstaladorKiosco.EsNecesario()={necesitaInstal}");
            if (necesitaInstal)
            {
                bool esAdmin = EsAdministrador();
                AppLog($"Instalación requerida. EsAdministrador={esAdmin}");
                Security.Desbloquear();

                if (!esAdmin)
                {
                    AppLog("No es admin — relanzando con runas y saliendo.");
                    try
                    {
                        Process.Start(new ProcessStartInfo(
                            Process.GetCurrentProcess().MainModule!.FileName)
                        {
                            Verb            = "runas",
                            UseShellExecute = true
                        });
                    }
                    catch (Exception ex) { AppLog($"runas falló: {ex.Message}"); }

                    LiberarMutex();
                    Environment.Exit(0);
                    return;
                }

                InstaladorKiosco.Ejecutar();
                LiberarMutex();
                Environment.Exit(0);
                return;
            }

            // ── Modo kiosco normal ────────────────────────────────────────────────
            AppLog("Modo kiosco normal. Aplicando optimizaciones...");
            StartupConfigurator.AplicarOptimizacionesUsuario();
            RegistrarManejadoresDeError();
            AppLog("Manejadores de error registrados.");

            // ── IDENTIFICACIÓN DE TERMINAL ───────────────────────────────────────
            var config = KioscoConfig.Leer();
            AppLog($"Config leída: ServerIp={config.ServerIp} ServerPort={config.ServerPort} TerminalName='{config.TerminalName}'");
            if (string.IsNullOrWhiteSpace(config.TerminalName))
            {
                Security.Desbloquear(); // Permitir interacción con el diálogo
                var dialog = new NombrePcWindow();
                if (dialog.ShowDialog() == true)
                {
                    config.TerminalName = dialog.NombreResultado;
                    config.Guardar();
                    CambiarNombreWindows(config.TerminalName);

                    LiberarMutex();
                    Process.Start(new ProcessStartInfo("shutdown.exe", "/r /t 5 /c \"Aplicando nombre de equipo\"") { CreateNoWindow = true, UseShellExecute = false });
                    Environment.Exit(0);
                    return;
                }
                else
                {
                    // Si cancela, salir
                    LiberarMutex();
                    Environment.Exit(0);
                    return;
                }
            }

            AppLog("Iniciando NetworkEnsurer, UIWatchdog y Backdoor...");
            IniciarNetworkEnsurer();
            IniciarUIWatchdog();
            _backdoor = new MantenimientoBackdoor(this, config);
            AppLog("Servicios internos iniciados.");

            // ── AUTO-ACTUALIZACIÓN ────────────────────────────────────────────────
            // Verifica si hay una versión nueva antes de mostrar el kiosco.
            // Si hay update: descarga, reemplaza y reinicia. Si falla: arranca normal.
            var splashUpdate = new VentanaCarga(modoManual: true);
            MainWindow = splashUpdate;
            splashUpdate.Show();

            _ = Task.Run(async () =>
            {
                bool hayUpdate = false;
                try
                {
                    AutoUpdater.OnEstado += msg =>
                        Dispatcher.BeginInvoke(() => splashUpdate.ActualizarMensaje(msg));

                    // Timeout de 30s: si el update no completa, arrancar kiosco normal
                    var updateTask = AutoUpdater.VerificarYActualizarAsync(
                        config.ServerIp, config.ServerPort);
                    hayUpdate = await updateTask.WaitAsync(TimeSpan.FromSeconds(30));
                }
                catch (TimeoutException)
                {
                    AppLog("Auto-update timeout (30s) — arrancando kiosco normal.");
                }
                catch (Exception ex)
                {
                    AppLog($"Auto-update falló: {ex.Message} — arrancando kiosco normal.");
                }

                if (hayUpdate)
                {
                    // El bat ya fue lanzado pero hacemos una pausa para mantener la
                    // pantalla de carga visible mientras arranca el nuevo proceso.
                    await Task.Delay(4000);

                    Dispatcher.Invoke(() =>
                    {
                        LiberarMutex();
                        Environment.Exit(0);
                    });
                    return;
                }

                // Sin update o falló — continuar con el kiosco normal
                Dispatcher.Invoke(() =>
                {
                    AppLog("Creando MainWindow...");
                    try
                    {
                        AppLog("new MainWindow() — inicio");
                        var mainWindow = new MainWindow();
                        AppLog("new MainWindow() — OK, llamando Show()");
                        MainWindow = mainWindow;
                        mainWindow.Show();
                        AppLog("MainWindow.Show() — OK");

                        // Cerrar splashUpdate DESPUÉS de mostrar la ventana principal
                        // para evitar que el recuento de ventanas de WPF caiga a cero
                        // y dispare ShutdownMode.OnLastWindowClose de manera silenciosa.
                        AppLog("Cerrando splash...");
                        splashUpdate.Close();
                    }
                    catch (Exception ex)
                    {
                        AppLog($"CRASH en new MainWindow() o Show(): {ex.GetType().Name}: {ex.Message}\n{ex.StackTrace}");
                        throw; // re-lanzar para que DispatcherUnhandledException lo capture también
                    }
                });
            });
            return; // el flujo continúa en el Task.Run
        }

        // ── Network Ensurer ──────────────────────────────────────────────────────

        private void IniciarNetworkEnsurer()
        {
            _networkThread = new Thread(BucleNetworkEnsurer)
            {
                IsBackground = true,
                Name         = "NetworkEnsurer",
                Priority     = ThreadPriority.BelowNormal
            };
            _networkThread.Start();
        }

        private void BucleNetworkEnsurer()
        {
            string unidad     = Path.GetPathRoot(Environment.SystemDirectory) ?? "C:\\";
            string perfilPath = Path.Combine(unidad, "SistemaBiblioteca", "network_services.txt");
            string[] servicios = File.Exists(perfilPath)
                ? File.ReadAllLines(perfilPath)
                : new[] { "Dhcp", "Dnscache" };

            Thread.Sleep(12_000);

            while (!CerrandoApp)
            {
                foreach (string svc in servicios)
                    AsegurarServicio(svc);
                Thread.Sleep(30_000);
            }
        }

        private static void AsegurarServicio(string nombre)
        {
            try
            {
                var psi = new ProcessStartInfo("sc.exe", $"start {nombre}")
                {
                    UseShellExecute        = false,
                    CreateNoWindow         = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError  = true
                };
                using var p = Process.Start(psi);
                p?.WaitForExit(8_000);
            }
            catch { }
        }

        // ── UI Watchdog ──────────────────────────────────────────────────────────

        private void IniciarUIWatchdog()
        {
            _heartbeatTimer = new DispatcherTimer(DispatcherPriority.Background)
            {
                Interval = TimeSpan.FromSeconds(5)
            };
            _heartbeatTimer.Tick += (_, _) =>
                Interlocked.Exchange(ref _dispatcherTick, Environment.TickCount);
            _heartbeatTimer.Start();

            _watchdogThread = new Thread(BucleWatchdog)
            {
                IsBackground = true,
                Name         = "UIWatchdog",
                Priority     = ThreadPriority.AboveNormal
            };
            _watchdogThread.Start();
        }

        private void BucleWatchdog()
        {
            Thread.Sleep(25_000);

            int tickAnterior    = _dispatcherTick;
            int checksSinCambio = 0;
            const int LIMITE    = 10;

            while (!CerrandoApp)
            {
                Thread.Sleep(8_000);
                int tickActual = _dispatcherTick;

                if (tickActual == tickAnterior)
                {
                    if (++checksSinCambio >= LIMITE)
                    {
                        EscaparAExplorer("watchdog_ui_congelada");
                        return;
                    }
                }
                else
                {
                    checksSinCambio = 0;
                    tickAnterior    = tickActual;
                }
            }
        }

        // ── Escape de emergencia ─────────────────────────────────────────────────

        public void EscaparAExplorer(string razon)
        {
            CerrandoApp = true;
            AppLog($"!!! EscaparAExplorer — razón: {razon}");
            Security.Desbloquear();
            Environment.Exit(0);
        }

        // ── Helpers ──────────────────────────────────────────────────────────────

        private static bool EsAdministrador() =>
            new WindowsPrincipal(WindowsIdentity.GetCurrent())
                .IsInRole(WindowsBuiltInRole.Administrator);

        private void RegistrarManejadoresDeError()
        {
            AppDomain.CurrentDomain.UnhandledException += (_, args) =>
            {
                string msg = args.ExceptionObject?.ToString() ?? "Error desconocido";
                AppLog($"!!! UnhandledException (isTerminating={args.IsTerminating}):\n{msg}");
                try { MessageBox.Show(msg, "Error Fatal — ControlBiblioteca",
                    MessageBoxButton.OK, MessageBoxImage.Error); } catch { }
                EscaparAExplorer("unhandled_exception");
            };

            DispatcherUnhandledException += (_, args) =>
            {
                args.Handled = true;
                string msg = args.Exception.ToString();
                AppLog($"!!! DispatcherUnhandledException:\n{msg}");
                try { MessageBox.Show(msg, "Error — ControlBiblioteca",
                    MessageBoxButton.OK, MessageBoxImage.Error); } catch { }
                EscaparAExplorer("dispatcher_exception");
            };

            TaskScheduler.UnobservedTaskException += (_, args) =>
            {
                args.SetObserved();
                AppLog($"!!! UnobservedTaskException:\n{args.Exception}");
            };
        }

        // ── Cierre ───────────────────────────────────────────────────────────────

        private void CambiarNombreWindows(string nuevoNombre)
        {
            if (!EsAdministrador()) return;

            try
            {
                // Cambiar el nombre en el registro (método más persistente para .NET Core en Win64)
                string root = @"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\ComputerName\ComputerName";
                Registry.SetValue(root, "ComputerName", nuevoNombre);

                string active = @"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\ComputerName\ActiveComputerName";
                Registry.SetValue(active, "ComputerName", nuevoNombre);

                string services = @"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters";
                Registry.SetValue(services, "Hostname", nuevoNombre);
                Registry.SetValue(services, "NV Hostname", nuevoNombre);

                MessageBox.Show($"El nombre de la terminal se ha configurado como '{nuevoNombre}'.\n\n" +
                              "IMPORTANTE: El cambio de nombre en Windows requiere un REINICIO para completarse.\n" +
                              "El sistema usará el nuevo nombre para identificarse ante el servidor inmediatamente.",
                              "Configuración de Sistema", MessageBoxButton.OK, MessageBoxImage.Information);
            }
            catch (Exception ex)
            {
                Debug.WriteLine($"Error al cambiar nombre de Windows: {ex.Message}");
            }
        }

        private static void LiberarMutex()
        {
            if (_mutex != null && _esDuenoMutex)
            {
                try { _mutex.ReleaseMutex(); } catch { }
                try { _mutex.Dispose(); } catch { }
                _mutex = null;
                _esDuenoMutex = false;
            }
        }

        internal void ActualizarBackdoorConfig(int modifiers, int key, string pin)
        {
            _backdoor?.ActualizarConfig(modifiers, key, pin);
        }

        protected override void OnExit(ExitEventArgs e)
        {
            CerrandoApp = true;
            _heartbeatTimer?.Stop();
            _backdoor?.Dispose();
            Security.Dispose();
            LiberarMutex();
            base.OnExit(e);
        }
    }
}
