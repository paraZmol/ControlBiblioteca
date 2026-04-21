using System;
using System.Diagnostics;
using System.IO;
using System.Security.Principal;
using System.Threading;
using System.Windows;
using System.Windows.Threading;
using ControlBiblioteca.Client.Services;
using ControlBiblioteca.Client.UI;

namespace ControlBiblioteca.Client
{
    public partial class App : Application
    {
        private static Mutex? _mutex;

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
            // ── PRIMERO: bloquear antes de cualquier ventana ──────────────────────
            // Cubre la brecha entre el login de Windows y que el kiosco cargue.
            Security.Bloquear();

            // ── Instancia única ───────────────────────────────────────────────────
            _mutex = new Mutex(true, "Global\\ControlBiblioteca_v2", out bool esPrimero);
            if (!esPrimero)
            {
                // Segunda instancia (watchdog repitió el disparo) — salir silenciosamente
                _mutex.Dispose();
                Security.Desbloquear(); // la instancia principal ya está corriendo
                Environment.Exit(0);
                return;
            }

            // ── ¿Necesita instalación? ────────────────────────────────────────────
            if (InstaladorKiosco.EsNecesario())
            {
                Security.Desbloquear(); // instalador no debe bloquear el escritorio

                if (!EsAdministrador())
                {
                    try
                    {
                        Process.Start(new ProcessStartInfo(
                            Process.GetCurrentProcess().MainModule!.FileName)
                        {
                            Verb            = "runas",
                            UseShellExecute = true
                        });
                    }
                    catch { }

                    _mutex.ReleaseMutex();
                    Environment.Exit(0);
                    return;
                }

                InstaladorKiosco.Ejecutar();
                _mutex.ReleaseMutex();
                Environment.Exit(0);
                return;
            }

            // ── Modo kiosco normal ────────────────────────────────────────────────
            StartupConfigurator.AplicarOptimizacionesUsuario();
            RegistrarManejadoresDeError();
            IniciarNetworkEnsurer();
            IniciarUIWatchdog();
            _backdoor = new MantenimientoBackdoor(this);

            // VentanaCarga cubre la pantalla mientras explorer carga.
            // Security.Bloquear() ya está activo — hook + TaskMgr deshabilitado.
            var splash = new VentanaCarga();
            MainWindow = splash;
            splash.Show();
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
            Debug.WriteLine($"[App] EscaparAExplorer — {razon}");
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
                Debug.WriteLine($"[Crash] {msg}");
                MessageBox.Show(msg, "Error Fatal — ControlBiblioteca",
                    MessageBoxButton.OK, MessageBoxImage.Error);
                EscaparAExplorer("unhandled_exception");
            };

            DispatcherUnhandledException += (_, args) =>
            {
                args.Handled = true;
                string msg = args.Exception.ToString();
                Debug.WriteLine($"[Dispatcher] {msg}");
                MessageBox.Show(msg, "Error — ControlBiblioteca",
                    MessageBoxButton.OK, MessageBoxImage.Error);
                EscaparAExplorer("dispatcher_exception");
            };
        }

        // ── Cierre ───────────────────────────────────────────────────────────────

        protected override void OnExit(ExitEventArgs e)
        {
            CerrandoApp = true;
            _heartbeatTimer?.Stop();
            _backdoor?.Dispose();
            Security.Dispose();
            _mutex?.ReleaseMutex();
            _mutex?.Dispose();
            base.OnExit(e);
        }
    }
}
