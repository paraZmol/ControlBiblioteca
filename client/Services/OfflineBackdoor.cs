using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;

namespace ControlBiblioteca.Client.Services
{
    /// <summary>
    /// Desbloqueo offline de emergencia para uso cuando el servidor no es alcanzable por red.
    ///
    /// Cómo usarlo:
    ///   1. El kiosco debe estar en estado "desconectado por red" (hotkey inactivo si hay conexión).
    ///   2. Presionar Ctrl+Alt+F11.
    ///   3. El personal ingresa el PIN offline. El alumno ingresa su DNI y razón de uso.
    ///   4. El kiosco queda desbloqueado de forma indefinida hasta que se apague o se cierre sesión.
    ///   5. Cuando la red vuelve, se verifica si el DNI es válido y no está baneado.
    ///      - Si está bien: la sesión offline se convierte en sesión normal y se sincroniza.
    ///      - Si no está bien: cuenta regresiva de 3 minutos con motivo visible, luego bloqueo.
    /// </summary>
    internal sealed class OfflineBackdoor : IDisposable
    {
        private const int HOTKEY_ID  = 0x4F46; // 'OF' de Offline
        private const int WM_HOTKEY  = 0x0312;

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool RegisterHotKey(IntPtr hWnd, int id, int fsModifiers, int vk);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool UnregisterHotKey(IntPtr hWnd, int id);

        private readonly App         _app;
        private readonly KioscoConfig _config;
        private HwndSource?          _hwnd;
        private bool                 _hotkeyRegistrado;
        private bool                 _disposed;

        // Estado de sesión offline activa
        private string? _dniActual;
        private string? _razonActual;
        private DateTime _horaInicio;

        // Callback que MainWindow provee para notificar reconexión
        public Action<string, string, DateTime>? OnReconectado;

        // Ruta del log local (mismo directorio que el exe)
        private static readonly string _logPath = Path.Combine(
            Path.GetDirectoryName(Environment.ProcessPath)
                ?? AppDomain.CurrentDomain.BaseDirectory,
            "offline_sessions.json");

        public OfflineBackdoor(App app, KioscoConfig config)
        {
            _app    = app;
            _config = config;
            app.Dispatcher.Invoke(CrearVentanaMensajes);
        }

        private void CrearVentanaMensajes()
        {
            var param = new HwndSourceParameters("KioscoOfflineHotkey")
            {
                Width        = 0,
                Height       = 0,
                WindowStyle  = 0,
                ParentWindow = new IntPtr(-3)
            };
            _hwnd = new HwndSource(param);
            _hwnd.AddHook(WndProc);
            // El hotkey se registra solo cuando se activa el modo offline
        }

        private IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
        {
            if (msg == WM_HOTKEY && wParam.ToInt32() == HOTKEY_ID)
            {
                handled = true;
                MostrarDialogo();
            }
            return IntPtr.Zero;
        }

        // Llamado por MainWindow cuando detecta desconexión por red
        public void Activar()
        {
            _app.Dispatcher.Invoke(() =>
            {
                if (_hwnd != null && !_hotkeyRegistrado)
                {
                    bool ok = RegisterHotKey(_hwnd.Handle, HOTKEY_ID, _config.OfflineModifiers, _config.OfflineKey);
                    _hotkeyRegistrado = ok;
                    App.AppLog($"[Offline] Hotkey {(ok ? "registrado" : "falló")} (Mod:{_config.OfflineModifiers} Key:{_config.OfflineKey})");
                }
            });
        }

        // Llamado por MainWindow cuando la conexión se restaura
        public void Desactivar()
        {
            _app.Dispatcher.Invoke(() =>
            {
                if (_hwnd != null && _hotkeyRegistrado)
                {
                    UnregisterHotKey(_hwnd.Handle, HOTKEY_ID);
                    _hotkeyRegistrado = false;
                    App.AppLog("[Offline] Hotkey desregistrado (conexión restaurada).");
                }
            });
        }

        public bool     HaySesionActiva      => _dniActual != null || _verificandoReconexion;
        public bool     HaySesionOSincronizando => _dniActual != null || _verificandoReconexion;
        public string?  DniActual            => _dniActual;
        public DateTime HoraInicioActual     => _horaInicio;
        public string?  RazonActual          => _razonActual;

        private volatile bool _verificandoReconexion;

        // Llamado por MainWindow antes de que el alumno presione el hotkey,
        // para pasar el DNI y motivo que ya ingresó en el formulario principal.
        public void SetDatosAlumno(string dni, string razon)
        {
            _dniPendiente   = dni;
            _razonPendiente = razon;
        }

        public void LimpiarDatosPendientes()
        {
            _dniPendiente   = null;
            _razonPendiente = null;
        }

        private string? _dniPendiente;
        private string? _razonPendiente;

        private void MostrarDialogo()
        {
            // Si el formulario principal ya capturó los datos del alumno,
            // solo pedimos el PIN al encargado.
            string? dni   = _dniPendiente;
            string? razon = _razonPendiente;

            if (string.IsNullOrEmpty(dni))
            {
                // Fallback: no hay datos previos — pedir DNI y motivo como antes.
                if (!ValidarPinPersonal()) return;
                (dni, razon) = PedirDniYRazon();
                if (dni == null) return;
            }
            else
            {
                // Datos ya capturados — solo validar PIN del encargado.
                if (!ValidarPinPersonal()) return;
            }

            _dniActual   = dni;
            _razonActual = razon;
            _horaInicio  = DateTime.Now;
            _dniPendiente   = null;
            _razonPendiente = null;

            GuardarLogLocal(dni, razon!, _horaInicio, null, "activa");
            App.AppLog($"[Offline] Sesión iniciada — DNI:{dni} Razón:{razon}");

            _app.Dispatcher.Invoke(() =>
            {
                if (Application.Current is App app)
                    app.IniciarSesionOffline(dni, razon!, _horaInicio);
            });
        }

        private bool ValidarPinPersonal()
        {
            bool resultado = false;

            _app.Dispatcher.Invoke(() =>
            {
                var ventana = new Window
                {
                    Title                 = "Modo Offline",
                    Width                 = 340,
                    Height                = 200,
                    WindowStyle           = WindowStyle.None,
                    ResizeMode            = ResizeMode.NoResize,
                    Topmost               = true,
                    ShowInTaskbar         = false,
                    WindowStartupLocation = WindowStartupLocation.CenterScreen,
                    Background            = new SolidColorBrush(Color.FromRgb(30, 40, 55))
                };

                var panel = new StackPanel { Margin = new Thickness(24) };

                var titulo = new TextBlock
                {
                    Text         = "Acceso Offline — Encargado",
                    Foreground   = Brushes.LightSteelBlue,
                    FontSize     = 11,
                    Margin       = new Thickness(0, 0, 0, 14),
                    TextWrapping = TextWrapping.Wrap
                };

                var etiqueta = new TextBlock
                {
                    Text       = "PIN del encargado:",
                    Foreground = Brushes.White,
                    FontSize   = 13,
                    Margin     = new Thickness(0, 0, 0, 6)
                };

                var pinBox = new PasswordBox { FontSize = 15, MaxLength = 32, Margin = new Thickness(0, 0, 0, 16) };

                var btnAcceder = new Button
                {
                    Content             = "Continuar",
                    FontSize            = 13,
                    Padding             = new Thickness(12, 6, 12, 6),
                    HorizontalAlignment = HorizontalAlignment.Right
                };

                void Verificar(object? s, RoutedEventArgs ev)
                {
                    if (pinBox.Password == _config.OfflinePin)
                    {
                        resultado = true;
                        ventana.Close();
                    }
                    else
                    {
                        pinBox.Clear();
                        pinBox.Focus();
                        etiqueta.Text       = "PIN incorrecto — intente nuevamente:";
                        etiqueta.Foreground = Brushes.Salmon;
                    }
                }

                btnAcceder.Click += Verificar;
                pinBox.KeyDown += (_, e) =>
                {
                    if (e.Key == Key.Enter)  btnAcceder.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                    if (e.Key == Key.Escape) ventana.Close();
                };

                panel.Children.Add(titulo);
                panel.Children.Add(etiqueta);
                panel.Children.Add(pinBox);
                panel.Children.Add(btnAcceder);

                ventana.Content = panel;
                ventana.Loaded += (_, _) => pinBox.Focus();
                ventana.ShowDialog();
            });

            return resultado;
        }

        private (string? Dni, string? Razon) PedirDniYRazon()
        {
            string? dniResult   = null;
            string? razonResult = null;

            _app.Dispatcher.Invoke(() =>
            {
                var ventana = new Window
                {
                    Title                 = "Datos de uso offline",
                    Width                 = 380,
                    Height                = 260,
                    WindowStyle           = WindowStyle.None,
                    ResizeMode            = ResizeMode.NoResize,
                    Topmost               = true,
                    ShowInTaskbar         = false,
                    WindowStartupLocation = WindowStartupLocation.CenterScreen,
                    Background            = new SolidColorBrush(Color.FromRgb(30, 40, 55))
                };

                var panel = new StackPanel { Margin = new Thickness(24) };

                var titulo = new TextBlock
                {
                    Text         = "Registro de uso sin conexión",
                    Foreground   = Brushes.LightSteelBlue,
                    FontSize     = 12,
                    FontWeight   = FontWeights.Bold,
                    Margin       = new Thickness(0, 0, 0, 16),
                    TextWrapping = TextWrapping.Wrap
                };

                var lblDni = new TextBlock { Text = "DNI del alumno:", Foreground = Brushes.White, FontSize = 13, Margin = new Thickness(0, 0, 0, 4) };
                var txtDni = new TextBox  { FontSize = 15, MaxLength = 8, Margin = new Thickness(0, 0, 0, 12) };

                var lblRazon = new TextBlock { Text = "Razón de uso:", Foreground = Brushes.White, FontSize = 13, Margin = new Thickness(0, 0, 0, 4) };
                var txtRazon = new TextBox  { FontSize = 13, MaxLength = 100, Margin = new Thickness(0, 0, 0, 16) };

                var lblError = new TextBlock { Text = "", Foreground = Brushes.Salmon, FontSize = 11, Margin = new Thickness(0, 0, 0, 8) };

                var btnIngresar = new Button
                {
                    Content             = "Ingresar",
                    FontSize            = 13,
                    Padding             = new Thickness(12, 6, 12, 6),
                    HorizontalAlignment = HorizontalAlignment.Right
                };

                void Confirmar(object? s, RoutedEventArgs ev)
                {
                    string dni   = txtDni.Text.Trim();
                    string razon = txtRazon.Text.Trim();

                    if (dni.Length != 8 || !long.TryParse(dni, out _))
                    {
                        lblError.Text = "El DNI debe tener exactamente 8 dígitos.";
                        txtDni.Focus();
                        return;
                    }
                    if (string.IsNullOrWhiteSpace(razon))
                    {
                        lblError.Text = "Ingresa una razón de uso.";
                        txtRazon.Focus();
                        return;
                    }

                    dniResult   = dni;
                    razonResult = razon;
                    ventana.Close();
                }

                btnIngresar.Click += Confirmar;
                txtDni.KeyDown   += (_, e) => { if (e.Key == Key.Enter) txtRazon.Focus(); };
                txtRazon.KeyDown += (_, e) => { if (e.Key == Key.Enter) btnIngresar.RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); };
                panel.KeyDown    += (_, e) => { if (e.Key == Key.Escape) ventana.Close(); };

                panel.Children.Add(titulo);
                panel.Children.Add(lblDni);
                panel.Children.Add(txtDni);
                panel.Children.Add(lblRazon);
                panel.Children.Add(txtRazon);
                panel.Children.Add(lblError);
                panel.Children.Add(btnIngresar);

                ventana.Content = panel;
                ventana.Loaded += (_, _) => txtDni.Focus();
                ventana.ShowDialog();
            });

            return (dniResult, razonResult);
        }

        // Llamado por MainWindow justo antes de iniciar la verificación HTTP
        public void IniciarVerificacion()
        {
            _verificandoReconexion = true;
        }

        // Llamado por MainWindow cuando detecta reconexión con sesión offline activa
        public void NotificarReconexion(string estadoVerificacion, string motivo)
        {
            // G-5: SIEMPRE limpiar el flag de verificación, incluso si ya no hay
            // sesión activa. Antes el early-return dejaba _verificandoReconexion
            // en true para siempre cuando la sesión se cerró por otra vía (timeout
            // de gracia/bloqueo) entre IniciarVerificacion y este punto, haciendo
            // que HaySesionActiva nunca volviera a false y que el cliente ignorara
            // permanentemente los comandos de bloqueo del servidor.
            _verificandoReconexion = false;

            if (_dniActual == null) return;

            string dni      = _dniActual;
            string razon    = _razonActual ?? "";
            DateTime inicio = _horaInicio;
            DateTime fin    = DateTime.Now;

            GuardarLogLocal(dni, razon, inicio, fin, $"sync:{estadoVerificacion}");
            App.AppLog($"[Offline] Sesión cerrada al reconectar — DNI:{dni} Estado:{estadoVerificacion} Motivo:{motivo}");

            _dniActual             = null;
            _razonActual           = null;

            OnReconectado?.Invoke(estadoVerificacion, motivo, inicio);
        }

        public void CerrarSesion()
        {
            // G-5: limpiar también el flag de verificación al cerrar por cualquier
            // vía, para que no quede colgado si se cierra durante una reconexión.
            _verificandoReconexion = false;
            if (_dniActual == null) return;
            GuardarLogLocal(_dniActual, _razonActual ?? "", _horaInicio, DateTime.Now, "cerrada_manual");
            App.AppLog($"[Offline] Sesión cerrada manualmente — DNI:{_dniActual}");
            _dniActual   = null;
            _razonActual = null;
        }

        private static void GuardarLogLocal(string dni, string razon, DateTime inicio, DateTime? fin, string estado)
        {
            try
            {
                var entrada = new
                {
                    dni,
                    razon,
                    hora_inicio  = inicio.ToString("yyyy-MM-dd HH:mm:ss"),
                    hora_fin     = fin?.ToString("yyyy-MM-dd HH:mm:ss"),
                    estado,
                    terminal     = Environment.MachineName
                };
                string linea = JsonSerializer.Serialize(entrada) + Environment.NewLine;
                File.AppendAllText(_logPath, linea);
            }
            catch (Exception ex)
            {
                App.AppLog($"[Offline] Error al guardar log local: {ex.Message}");
            }
        }

        public void ActualizarConfig(int modifiers, int key, string pin)
        {
            _app.Dispatcher.Invoke(() =>
            {
                bool estabaActivo = _hotkeyRegistrado;

                if (_hwnd != null && estabaActivo)
                    UnregisterHotKey(_hwnd.Handle, HOTKEY_ID);

                _config.OfflineModifiers = modifiers;
                _config.OfflineKey       = key;
                _config.OfflinePin       = pin;
                _config.Guardar();

                if (_hwnd != null && estabaActivo)
                {
                    bool ok = RegisterHotKey(_hwnd.Handle, HOTKEY_ID, modifiers, key);
                    _hotkeyRegistrado = ok;
                    App.AppLog($"[Offline] Config actualizada y hotkey re-registrado: ok={ok}");
                }
                else
                {
                    App.AppLog("[Offline] Config actualizada (hotkey no activo actualmente).");
                }
            });
        }

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;

            if (_dniActual != null)
                CerrarSesion();

            if (_hwnd != null)
            {
                if (_hotkeyRegistrado)
                    UnregisterHotKey(_hwnd.Handle, HOTKEY_ID);
                _hwnd.Dispose();
                _hwnd = null;
            }
        }
    }
}
