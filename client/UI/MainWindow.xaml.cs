using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace ControlBiblioteca.Client.UI
{
    public partial class MainWindow : Window
    {
        [DllImport("user32.dll")]
        private static extern bool SetForegroundWindow(IntPtr hWnd);

        private readonly Services.WebSocketService _wsService;
        private bool _desbloqueado;
        private bool _cerrandoPorEscape;

        private bool _esperandoEscapes = false;
        private int  _conteoEscapes    = 0;
        private const int ESCAPES_PARA_SALIR = 5;
        private System.Windows.Threading.DispatcherTimer? _timerEscape;

        private CancellationTokenSource? _loginCts;

        // ── Carrusel comunicado ───────────────────────────────────────
        private int _carruselIndex = 0;
        private const string IMG_COMUNICADO_0 = "pack://application:,,,/images/comunicado_1.jpg";
        private const string IMG_COMUNICADO_1 = "pack://application:,,,/images/comunicado_2.jpg";

        private const Key MASTER_KEY         = Key.F10;
        private const ModifierKeys MASTER_MOD = ModifierKeys.Control | ModifierKeys.Alt;

        private static readonly Dictionary<string, (string Nombres, string Apellidos, bool Habilitado)> _codigosPrueba = new()
        {
            ["2021001"] = ("Juan Carlos",    "Pérez López",    true),
            ["2021002"] = ("María Elena",    "García Huamán",  true),
            ["2021003"] = ("Luis Alberto",   "Rodríguez Meza", true),
            ["2021004"] = ("Ana Patricia",   "Torres Vargas",  true),
            ["2021005"] = ("Carlos Eduardo", "Sánchez Ríos",   false),
        };

        private static App AppActual => (App)Application.Current;

        public MainWindow()
        {
            InitializeComponent();

            if (!AppActual.Security.EstaBloqueado)
                AppActual.Security.Bloquear();

            Loaded += (_, _) => CubrirPantallaCompleta();
            Loaded += (_, _) => FocoDNI();

            var (cfg, cfgDiag) = Services.KioscoConfig.LeerConDiagnostico();
            string localIp     = ObtenerIpLocal();
            string wsUrl       = $"{cfg.WsBaseUrl}/ws/terminal/{localIp}";
            string hostname    = Environment.MachineName;

            Loaded += (_, _) =>
            {
                TxtNombreEquipo.Text = $"Equipo: {hostname}";
                LogDebug(cfgDiag);
                LogDebug($"IP local  : {localIp}");
                LogDebug($"Hostname  : {hostname}");
                LogDebug($"WS URL    : {wsUrl}");
                LogDebug("Conectando...");
            };

            string apiUrl = cfg.WsBaseUrl.Replace("ws://", "http://").Replace("wss://", "https://");

            bool primerError = true;

            _wsService = new Services.WebSocketService(wsUrl);
            _wsService.InitialGreeting     = JsonSerializer.Serialize(new { tipo = "hello", hostname });
            _wsService.OnMensajeRecibido  += ProcesarMensajeServidor;
            _wsService.OnConexionCambiada += conectado =>
            {
                ActualizarEstadoConexion(conectado);
                if (conectado)
                    _ = CargarMotivosAsync($"{apiUrl}/api/catalogos/motivos");
            };

            Loaded += (_, _) => _ = CargarMotivosAsync($"{apiUrl}/api/catalogos/motivos");
            _wsService.OnError += msg =>
            {
                LogDebug($"ERROR WS: {msg}");
                if (primerError)
                {
                    primerError = false;
                    Dispatcher.BeginInvoke(() =>
                        MessageBox.Show(
                            $"No se pudo conectar al servidor WebSocket.\n\nURL intentada:\n{wsUrl}\n\nError:\n{msg}\n\nConfig:\n{cfgDiag}",
                            "Error de conexión — Kiosco",
                            MessageBoxButton.OK,
                            MessageBoxImage.Warning));
                }
            };
            _ = _wsService.ConectarAsync();

            PreviewKeyDown += MainWindow_PreviewKeyDown;

            // Foco inteligente event-driven — sin timers
            this.Activated += (_, _) =>
            {
                PonerVentanaAlFrente();
                FocoDNI();
            };

            this.IsVisibleChanged += (_, _) =>
            {
                if (IsVisible && !_desbloqueado)
                {
                    PonerVentanaAlFrente();
                    FocoDNI();
                }
            };

            Closing += (_, e) => { if (!_desbloqueado && !_cerrandoPorEscape) e.Cancel = true; };
        }

        // ── Brushes de validación ─────────────────────────────────────
        private static readonly SolidColorBrush _borderNormal = new(Color.FromRgb(0x8D, 0x99, 0xAE));
        private static readonly SolidColorBrush _borderError  = new(Color.FromRgb(0xFF, 0x4D, 0x4D));

        // ── Foco inteligente (event-driven, sin timers) ───────────────

        private void FocoDNI()
        {
            if (_desbloqueado) return;
            if (EstaFocusEnControlLegitimo()) return;

            Dispatcher.BeginInvoke(new Action(() =>
            {
                if (_desbloqueado) return;
                if (EstaFocusEnControlLegitimo()) return;
                TxtCodigo?.Focus();
                Keyboard.Focus(TxtCodigo);
                if (TxtCodigo != null)
                    TxtCodigo.CaretIndex = TxtCodigo.Text.Length;
            }), System.Windows.Threading.DispatcherPriority.Input);
        }

        // Devuelve true si el foco está en un control legítimo distinto del DNI
        private bool EstaFocusEnControlLegitimo()
        {
            if (CmbRazon != null && (CmbRazon.IsFocused || CmbRazon.IsKeyboardFocusWithin)) return true;
            if (TxtOtroRazon != null && (TxtOtroRazon.IsFocused || TxtOtroRazon.IsKeyboardFocusWithin)) return true;
            if (BtnIngresar != null && BtnIngresar.IsFocused) return true;
            return false;
        }

        // ── Validación numérica en campo DNI ─────────────────────────

        private void TxtCodigo_PreviewTextInput(object sender, TextCompositionEventArgs e)
        {
            e.Handled = !System.Text.RegularExpressions.Regex.IsMatch(e.Text, @"^\d+$");
        }

        private void TxtCodigo_Pasting(object sender, DataObjectPastingEventArgs e)
        {
            if (e.DataObject.GetDataPresent(typeof(string)))
            {
                string texto = (string)e.DataObject.GetData(typeof(string));
                if (!System.Text.RegularExpressions.Regex.IsMatch(texto, @"^\d+$"))
                    e.CancelCommand();
            }
            else
            {
                e.CancelCommand();
            }
        }

        // ── Eventos de UI ─────────────────────────────────────────────

        private async void BtnIngresar_Click(object sender, RoutedEventArgs e)
        {
            try
            {
                LimpiarErroresVisuales();
                bool hayError = false;

                string codigo = TxtCodigo.Text.Trim().ToUpper();
                if (string.IsNullOrEmpty(codigo) || !System.Text.RegularExpressions.Regex.IsMatch(codigo, @"^\d{8}$"))
                {
                    TxtCodigo.BorderBrush = _borderError;
                    TxtEstado.Text = "Ingrese un DNI válido (8 dígitos)";
                    hayError = true;
                }

                string razon = ObtenerRazon();
                if (string.IsNullOrEmpty(razon))
                {
                    var item = CmbRazon.SelectedItem as ComboBoxItem;
                    string sel = item?.Content?.ToString() ?? "";
                    if (sel.StartsWith("Otros"))
                    {
                        TxtOtroRazon.BorderBrush = _borderError;
                        TxtErrorOtros.Text = "Debe especificar la razón de uso";
                    }
                    else
                    {
                        CmbRazon.BorderBrush = _borderError;
                    }
                    if (!hayError) TxtEstado.Text = "Seleccione una razón de uso";
                    hayError = true;
                }

                if (hayError) return;

                if (!_wsService.EstaConectado) { ValidarOffline(codigo); return; }

                TxtEstado.Text        = "Validando...";
                BtnIngresar.IsEnabled = false;

                var cbItem = CmbRazon.SelectedItem as ComboBoxItem;
                int motivo_id = cbItem?.Tag is int id ? id : 0;

                await _wsService.EnviarAsync(JsonSerializer.Serialize(new
                {
                    tipo = "login_request",
                    codigo,
                    razon,
                    motivo_id
                }));

                _loginCts?.Cancel();
                _loginCts?.Dispose();
                _loginCts = new CancellationTokenSource();
                var token = _loginCts.Token;

                _ = Task.Run(async () =>
                {
                    try
                    {
                        await Task.Delay(10_000, token);
                        Dispatcher.Invoke(() =>
                        {
                            if (!_desbloqueado)
                            {
                                BtnIngresar.IsEnabled = true;
                                TxtEstado.Text = "Sin respuesta del servidor. Intente de nuevo.";
                            }
                        });
                    }
                    catch (OperationCanceledException) { }
                }, token);
            }
            catch (Exception ex)
            {
                TxtEstado.Text = "Error en login: " + ex.Message;
                BtnIngresar.IsEnabled = true;
                _ = _wsService.ReportarErrorAsync($"Error BtnIngresar_Click: {ex.Message}");
                LogDebug($"ERROR BtnIngresar: {ex}");
            }
        }

        private void ValidarOffline(string codigo)
        {
            if (_codigosPrueba.TryGetValue(codigo, out var alumno))
            {
                if (alumno.Habilitado)
                    Desbloquear(alumno.Nombres, alumno.Apellidos, codigo);
                else
                {
                    TxtEstado.Text        = "Alumno no habilitado";
                    BtnIngresar.IsEnabled = true;
                }
            }
            else
            {
                TxtEstado.Text        = "Código inválido (modo sin servidor)";
                BtnIngresar.IsEnabled = true;
            }
        }

        private async void BtnCerrarSesion_Click(object sender, RoutedEventArgs e)
        {
            try
            {
                await _wsService.EnviarAsync(JsonSerializer.Serialize(new { tipo = "logout" }));
                Bloquear();
            }
            catch (Exception ex)
            {
                LogDebug($"ERROR BtnCerrarSesion: {ex}");
                _ = _wsService.ReportarErrorAsync($"Error al cerrar sesión: {ex.Message}");
                Bloquear();
            }
        }

        private void TxtCodigo_KeyDown(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.Enter) BtnIngresar_Click(sender, e);
        }

        private void TxtCodigo_TextChanged(object sender, TextChangedEventArgs e)
        {
            ActualizarEstadoBoton();
        }

        private void ActualizarEstadoBoton()
        {
            if (BtnIngresar == null || TxtCodigo == null || CmbRazon == null) return;
            string dni = TxtCodigo.Text.Trim();
            bool dniValido = System.Text.RegularExpressions.Regex.IsMatch(dni, @"^\d{8}$");
            bool razonValida = !string.IsNullOrEmpty(ObtenerRazon());
            BtnIngresar.IsEnabled = dniValido && razonValida;
            CommandManager.InvalidateRequerySuggested();
        }

        // ── Razón de uso ──────────────────────────────────────────────

        private void CmbRazon_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            if (PanelOtros == null) return;
            var item = CmbRazon.SelectedItem as ComboBoxItem;
            string texto = item?.Content?.ToString() ?? "";
            bool esOtros = texto.StartsWith("Otros");
            PanelOtros.Visibility = esOtros ? Visibility.Visible : Visibility.Collapsed;

            if (esOtros)
            {
                Dispatcher.BeginInvoke(new Action(() => {
                    TxtOtroRazon.Focus();
                    Keyboard.Focus(TxtOtroRazon);
                }), System.Windows.Threading.DispatcherPriority.Input);
            }
            else
            {
                TxtOtroRazon.Text  = "";
                TxtErrorOtros.Text = "";
                Dispatcher.BeginInvoke(new Action(() => {
                    if (CmbRazon == null || !CmbRazon.IsDropDownOpen)
                    {
                        TxtCodigo.Focus();
                        Keyboard.Focus(TxtCodigo);
                    }
                }), System.Windows.Threading.DispatcherPriority.Input);
            }

            CmbRazon.BorderBrush = _borderNormal;
            ActualizarEstadoBoton();
        }

        private void TxtOtroRazon_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (PlaceholderOtros == null) return;
            PlaceholderOtros.Visibility = string.IsNullOrEmpty(TxtOtroRazon.Text)
                ? Visibility.Visible
                : Visibility.Collapsed;
            TxtOtroRazon.BorderBrush = _borderNormal;
            TxtErrorOtros.Text = "";
            ActualizarEstadoBoton();
        }

        private string ObtenerRazon()
        {
            var item = CmbRazon.SelectedItem as ComboBoxItem;
            string texto = item?.Content?.ToString() ?? "";
            if (texto.StartsWith("Otros"))
            {
                string especificado = TxtOtroRazon.Text.Trim();
                return string.IsNullOrEmpty(especificado) ? "" : $"Otros: {especificado}";
            }
            return texto;
        }

        private void LimpiarErroresVisuales()
        {
            TxtCodigo.BorderBrush    = _borderNormal;
            CmbRazon.BorderBrush     = _borderNormal;
            TxtOtroRazon.BorderBrush = _borderNormal;
            TxtErrorOtros.Text       = "";
            TxtEstado.Text           = "";
        }

        private void MainWindow_PreviewKeyDown(object sender, KeyEventArgs e)
        {
            // Ctrl+B+U — mostrar/ocultar consola de depuración
            if (e.Key == Key.U && (Keyboard.Modifiers & ModifierKeys.Control) == ModifierKeys.Control
                                && Keyboard.IsKeyDown(Key.B))
            {
                e.Handled = true;
                if (PanelDebug != null)
                    PanelDebug.Visibility = PanelDebug.Visibility == Visibility.Visible
                        ? Visibility.Collapsed
                        : Visibility.Visible;
                return;
            }

            // Salida instantánea legado (Ctrl+Alt+F10)
            if (e.Key == MASTER_KEY && (Keyboard.Modifiers & MASTER_MOD) == MASTER_MOD)
            {
                e.Handled = true;
                LogDebug("MASTER KEY activada — salida de emergencia");
                ActivarEscapeEmergencia("master_key_offline");
                return;
            }

            // Paso 1: Ctrl+M activa el modo escape
            if (e.Key == Key.M && (Keyboard.Modifiers & ModifierKeys.Control) == ModifierKeys.Control)
            {
                e.Handled = true;
                _esperandoEscapes = true;
                _conteoEscapes    = 0;
                LogDebug("Ctrl+M — esperando 5x ESC en 5 segundos...");

                // Reiniciar timer de 5 segundos
                if (_timerEscape == null)
                {
                    _timerEscape = new System.Windows.Threading.DispatcherTimer
                    {
                        Interval = TimeSpan.FromSeconds(5)
                    };
                    _timerEscape.Tick += (_, _) =>
                    {
                        _timerEscape.Stop();
                        _esperandoEscapes = false;
                        _conteoEscapes    = 0;
                        LogDebug("Timeout — secuencia de escape cancelada");
                    };
                }
                _timerEscape.Stop();
                _timerEscape.Start();
                return;
            }

            // Paso 2: contar ESC solo si el modo está activo
            if (e.Key == Key.Escape && _esperandoEscapes)
            {
                e.Handled = true;
                _conteoEscapes++;
                LogDebug($"ESC {_conteoEscapes}/{ESCAPES_PARA_SALIR}");
                if (_conteoEscapes >= ESCAPES_PARA_SALIR)
                {
                    _timerEscape?.Stop();
                    _esperandoEscapes = false;
                    _conteoEscapes    = 0;
                    ActivarEscapeEmergencia("ctrl_m_escape_5x");
                }
                return;
            }
        }

        private void ActivarEscapeEmergencia(string razon)
        {
            _cerrandoPorEscape = true;
            _loginCts?.Cancel();
            _wsService.Desconectar();
            LogDebug($"Escape de emergencia: {razon}");
            AppActual.EscaparAExplorer(razon);
        }

        // ── Bloqueo / Desbloqueo ──────────────────────────────────────

        private void Desbloquear(string nombres, string apellidos, string codigo)
        {
            _desbloqueado = true;
            _loginCts?.Cancel();

            Dispatcher.Invoke(() =>
            {
                Debug.WriteLine($"[ENTRADA] {nombres} {apellidos} | {codigo}");
                LogDebug($"Acceso concedido: {nombres} {apellidos}");

                AppActual.Security.Desbloquear();

                PanelBloqueo.Visibility = Visibility.Collapsed;
                PanelSesion.Visibility  = Visibility.Visible;
                TxtBienvenida.Text      = $"Bienvenido, {nombres}";
                TxtInfoAlumno.Text      = $"Código: {codigo}";

                Hide();
            });
        }

        private void Bloquear()
        {
            _desbloqueado     = false;
            _esperandoEscapes = false;
            _conteoEscapes    = 0;

            Dispatcher.Invoke(() =>
            {
                AppActual.Security.Bloquear();

                if (PanelDebug != null)
                    PanelDebug.Visibility = Visibility.Collapsed;

                PanelSesion.Visibility  = Visibility.Collapsed;
                PanelBloqueo.Visibility = Visibility.Visible;
                TxtCodigo.Text          = "";
                TxtEstado.Text          = "";
                CmbRazon.SelectedIndex  = 0;
                BtnIngresar.IsEnabled   = false;
                TxtOtroRazon.Text       = "";
                PanelOtros.Visibility   = Visibility.Collapsed;
                TxtErrorOtros.Text      = "";
                LimpiarErroresVisuales();

                Show();
                CubrirPantallaCompleta();
                Topmost = true;
                Activate();
                PonerVentanaAlFrente();
                FocoDNI();
            });
        }

        // ── Mensajes WebSocket ────────────────────────────────────────

        private void ProcesarMensajeServidor(string mensaje)
        {
            try
            {
                using var doc = JsonDocument.Parse(mensaje);
                var root      = doc.RootElement;
                string tipo   = root.GetProperty("tipo").GetString() ?? "";

                switch (tipo)
                {
                    case "desbloquear":
                        try
                        {
                            var alumno = root.GetProperty("alumno");
                            string nom = alumno.GetProperty("nombres").GetString()   ?? "";
                            string ape = alumno.GetProperty("apellidos").GetString() ?? "";
                            string cod = alumno.GetProperty("codigo").GetString()    ?? "";
                            _ = Task.Run(async () =>
                            {
                                try
                                {
                                    Desbloquear(nom, ape, cod);
                                    await _wsService.EnviarAsync("{\"tipo\":\"unlock_confirmed\"}");
                                    LogDebug("unlock_confirmed enviado al servidor");
                                }
                                catch (Exception ex)
                                {
                                    string errorMsg = $"Fallo en desbloqueo local: {ex.Message}";
                                    LogDebug($"ERROR: {errorMsg}");
                                    await _wsService.ReportarErrorAsync(errorMsg);
                                }
                            });
                        }
                        catch (Exception ex)
                        {
                            LogDebug($"ERROR desbloquear: {ex}");
                            _ = _wsService.ReportarErrorAsync($"Error desbloqueo: {ex.Message}");
                        }
                        break;

                    case "bloquear":
                    case "forzar_cierre_sesion":
                        try
                        {
                            _ = Task.Run(() => Bloquear());
                        }
                        catch (Exception ex)
                        {
                            LogDebug($"ERROR {tipo}: {ex}");
                            _ = _wsService.ReportarErrorAsync($"Error {tipo}: {ex.Message}");
                        }
                        break;

                    case "login_rechazado":
                        try
                        {
                            string motivo = root.TryGetProperty("motivo", out var m)
                                ? m.GetString() ?? "Error"
                                : "Error";
                            LogDebug($"Login rechazado: {motivo}");
                            Dispatcher.Invoke(() =>
                            {
                                BtnIngresar.IsEnabled = true;
                                if (motivo.Contains("no registrado", StringComparison.OrdinalIgnoreCase) ||
                                    motivo.Contains("no encontrado", StringComparison.OrdinalIgnoreCase))
                                {
                                    MostrarComunicado();
                                }
                                else
                                {
                                    TxtEstado.Text = motivo;
                                }
                            });
                        }
                        catch (Exception ex)
                        {
                            LogDebug($"ERROR login_rechazado: {ex}");
                        }
                        break;

                    case "heartbeat_ack":
                        break;

                    case "remote_command":
                        try
                        {
                            string action = root.TryGetProperty("action", out var act) ? act.GetString() ?? "" : "";
                            if (action == "shutdown")
                            {
                                LogDebug("Comando remoto: apagando PC...");
                                var sessionClosedMsg = JsonSerializer.Serialize(new { tipo = "session_closed", hora_salida = DateTime.UtcNow.ToString("O") });
                                _ = _wsService.EnviarAsync(sessionClosedMsg);
                                System.Threading.Thread.Sleep(500);
                                System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(
                                    "shutdown", "/s /f /t 0")
                                {
                                    CreateNoWindow  = true,
                                    UseShellExecute = false
                                });
                            }
                        }
                        catch (Exception ex)
                        {
                            LogDebug($"ERROR remote_command: {ex}");
                            _ = _wsService.ReportarErrorAsync($"Error comando remoto: {ex.Message}");
                        }
                        break;
                }
            }
            catch (Exception ex)
            {
                string errorMsg = $"Error procesando mensaje: {ex.Message}";
                Dispatcher.Invoke(() => TxtEstado.Text = errorMsg);
                _ = _wsService.ReportarErrorAsync(errorMsg);
                LogDebug($"ERROR ProcesarMensajeServidor: {ex}");
            }
        }

        private void ActualizarEstadoConexion(bool conectado)
        {
            LogDebug(conectado ? "WS conectado OK" : "WS desconectado — reintentando...");
            Dispatcher.Invoke(() =>
            {
                IndicadorConexion.Fill = conectado
                    ? System.Windows.Media.Brushes.LimeGreen
                    : System.Windows.Media.Brushes.Red;
                TxtConexion.Text = conectado ? "Conectado al servidor" : "Desconectado";
            });
        }

        // ── Utilidades ────────────────────────────────────────────────

        private void LogDebug(string mensaje)
        {
            string linea = $"[{DateTime.Now:HH:mm:ss}] {mensaje}";
            Debug.WriteLine(linea);
            Dispatcher.BeginInvoke(() =>
            {
                TxtDebug.Text += linea + "\n";
                var partes = TxtDebug.Text.Split('\n');
                if (partes.Length > 32)
                    TxtDebug.Text = string.Join("\n", partes[^32..]);
                DebugScroll.ScrollToBottom();
            });
        }

        private void CubrirPantallaCompleta()
        {
            WindowState = WindowState.Normal;
            Left   = 0;
            Top    = 0;
            Width  = SystemParameters.PrimaryScreenWidth;
            Height = SystemParameters.PrimaryScreenHeight;
        }

        private void PonerVentanaAlFrente()
        {
            try
            {
                IntPtr handle = new System.Windows.Interop.WindowInteropHelper(this).Handle;
                if (handle != IntPtr.Zero)
                    SetForegroundWindow(handle);
            }
            catch (Exception ex)
            {
                LogDebug($"Error al poner ventana al frente: {ex.Message}");
            }
        }

        private static string ObtenerIpLocal()
        {
            try
            {
                foreach (var ip in System.Net.Dns.GetHostEntry(System.Net.Dns.GetHostName()).AddressList)
                    if (ip.AddressFamily == System.Net.Sockets.AddressFamily.InterNetwork)
                        return ip.ToString();
            }
            catch { }
            return "127.0.0.1";
        }

        // ── Comunicado carrusel ───────────────────────────────────────

        private void MostrarComunicado()
        {
            _carruselIndex = 0;
            ActualizarCarrusel();
            PanelComunicado.Visibility = Visibility.Visible;
        }

        private static BitmapImage? _imgCache0 = null;
        private static BitmapImage? _imgCache1 = null;

        private BitmapImage? CargarImagen(string packUri, string nombreArchivo)
        {
            // Intento 1: recurso embebido en el ensamblado (pack URI)
            try
            {
                var sri = Application.GetResourceStream(new Uri(packUri, UriKind.Absolute));
                if (sri != null)
                {
                    var bmp = new BitmapImage();
                    bmp.BeginInit();
                    bmp.StreamSource = sri.Stream;
                    bmp.CacheOption  = BitmapCacheOption.OnLoad;
                    bmp.EndInit();
                    bmp.Freeze();
                    LogDebug($"Imagen cargada desde recurso: {nombreArchivo}");
                    return bmp;
                }
            }
            catch (Exception ex) { LogDebug($"Pack URI falló para {nombreArchivo}: {ex.Message}"); }

            // Intento 2: junto al .exe (para desarrollo o si no se embebió)
            try
            {
                string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                string ruta    = System.IO.Path.Combine(baseDir, "images", nombreArchivo);
                if (System.IO.File.Exists(ruta))
                {
                    var bmp = new BitmapImage();
                    bmp.BeginInit();
                    bmp.UriSource    = new Uri(ruta, UriKind.Absolute);
                    bmp.CacheOption  = BitmapCacheOption.OnLoad;
                    bmp.EndInit();
                    bmp.Freeze();
                    LogDebug($"Imagen cargada desde disco: {ruta}");
                    return bmp;
                }
                else
                {
                    LogDebug($"No encontrada en disco: {ruta}");
                }
            }
            catch (Exception ex) { LogDebug($"Disco falló para {nombreArchivo}: {ex.Message}"); }

            LogDebug($"ERROR: no se pudo cargar {nombreArchivo} por ningún método");
            return null;
        }

        private void ActualizarCarrusel()
        {
            if (_imgCache0 == null)
                _imgCache0 = CargarImagen(IMG_COMUNICADO_0, "comunicado_1.jpg");
            if (_imgCache1 == null)
                _imgCache1 = CargarImagen(IMG_COMUNICADO_1, "comunicado_2.jpg");

            ImgComunicado0.Source = _imgCache0;
            ImgComunicado1.Source = _imgCache1;

            ImgComunicado0.Visibility = _carruselIndex == 0 ? Visibility.Visible : Visibility.Collapsed;
            ImgComunicado1.Visibility = _carruselIndex == 1 ? Visibility.Visible : Visibility.Collapsed;
            Dot0.Fill = _carruselIndex == 0
                ? new SolidColorBrush(Color.FromRgb(0x00, 0xB4, 0xDB))
                : new SolidColorBrush(Color.FromArgb(0x44, 0xFF, 0xFF, 0xFF));
            Dot1.Fill = _carruselIndex == 1
                ? new SolidColorBrush(Color.FromRgb(0x00, 0xB4, 0xDB))
                : new SolidColorBrush(Color.FromArgb(0x44, 0xFF, 0xFF, 0xFF));
        }

        private void BtnCarruselIzq_Click(object sender, RoutedEventArgs e)
        {
            _carruselIndex = (_carruselIndex - 1 + 2) % 2;
            ActualizarCarrusel();
        }

        private void BtnCarruselDer_Click(object sender, RoutedEventArgs e)
        {
            _carruselIndex = (_carruselIndex + 1) % 2;
            ActualizarCarrusel();
        }

        private void BtnCerrarComunicado_Click(object sender, RoutedEventArgs e)
        {
            PanelComunicado.Visibility = Visibility.Collapsed;
            TxtCodigo.Text  = "";
            TxtEstado.Text  = "";
            LimpiarErroresVisuales();
            FocoDNI();
        }

        protected override void OnClosed(EventArgs e)
        {
            _loginCts?.Cancel();
            _loginCts?.Dispose();
            _wsService.Desconectar();
            base.OnClosed(e);
        }

        private async Task CargarMotivosAsync(string url)
        {
            for (int intento = 1; intento <= 5; intento++)
            {
                try
                {
                    using var client = new System.Net.Http.HttpClient { Timeout = TimeSpan.FromSeconds(5) };
                    var json = await client.GetStringAsync(url);
                    var opciones = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };
                    var motivos = JsonSerializer.Deserialize<List<MotivoUso>>(json, opciones);

                    if (motivos == null || motivos.Count == 0)
                    {
                        await Task.Delay(2000);
                        continue;
                    }

                    Dispatcher.Invoke(() =>
                    {
                        CmbRazon.Items.Clear();
                        foreach (var m in motivos)
                        {
                            var cbi = new ComboBoxItem { Content = m.descripcion, Tag = m.id };
                            CmbRazon.Items.Add(cbi);
                        }
                        var otros = new ComboBoxItem { Content = "Otros (Especificar)", Tag = 0 };
                        CmbRazon.Items.Add(otros);
                        CmbRazon.SelectedIndex = 0;
                    });
                    return;
                }
                catch (Exception ex)
                {
                    LogDebug($"Error cargando motivos (intento {intento}/5): {ex.Message}");
                    await Task.Delay(2000);
                }
            }

            // Fallback: si el servidor no respondió, al menos agregar "Otros" para no bloquear el formulario
            Dispatcher.Invoke(() =>
            {
                if (CmbRazon.Items.Count == 0)
                {
                    CmbRazon.Items.Add(new ComboBoxItem { Content = "Otros (Especificar)", Tag = 0 });
                    CmbRazon.SelectedIndex = 0;
                    LogDebug("Motivos: fallback aplicado (servidor no respondió)");
                }
            });
        }
    }

    public class MotivoUso
    {
        public int id { get; set; }
        public string descripcion { get; set; }
    }
}
