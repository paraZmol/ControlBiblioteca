using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;

namespace ControlBiblioteca.Client.UI
{
    public partial class MainWindow : Window
    {
        private readonly Services.WebSocketService _wsService;
        private bool _desbloqueado;
        private bool _cerrandoPorEscape;

        private int _contadorEscape;
        private const int ESCAPES_PARA_SALIR = 5;

        private CancellationTokenSource? _loginCts;

        // Combo de emergencia offline — sin PIN, sin servidor
        // Ctrl + Alt + F10 cierra el kiosco inmediatamente
        private const Key MASTER_KEY       = Key.F10;
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

            // Garantía extra: el SecurityManager debe estar activo antes de mostrarse.
            // App.Application_Startup ya lo hace, pero si MainWindow se instanciara
            // por otro camino esta línea lo cubre igualmente.
            if (!AppActual.Security.EstaBloqueado)
                AppActual.Security.Bloquear();

            Loaded += (_, _) => CubrirPantallaCompleta();

            // ── Configuración y conexión ──────────────────────────────
            var (cfg, cfgDiag) = Services.KioscoConfig.LeerConDiagnostico();
            string localIp     = ObtenerIpLocal();
            string wsUrl       = $"{cfg.WsBaseUrl}/ws/terminal/{localIp}";

            string hostname = Environment.MachineName;

            Loaded += (_, _) =>
            {
                LogDebug(cfgDiag);
                LogDebug($"IP local  : {localIp}");
                LogDebug($"Hostname  : {hostname}");
                LogDebug($"WS URL    : {wsUrl}");
                LogDebug("Conectando...");
            };

            bool primerError = true;

            _wsService = new Services.WebSocketService(wsUrl);
            // Enviar hello con hostname tras cada conexión exitosa
            _wsService.InitialGreeting = JsonSerializer.Serialize(new { tipo = "hello", hostname });
            _wsService.OnMensajeRecibido  += ProcesarMensajeServidor;
            _wsService.OnConexionCambiada += ActualizarEstadoConexion;
            _wsService.OnError += msg =>
            {
                LogDebug($"ERROR WS: {msg}");
                if (primerError)
                {
                    primerError = false;
                    Dispatcher.BeginInvoke(() =>
                        MessageBox.Show(
                            $"No se pudo conectar al servidor WebSocket.\n\n" +
                            $"URL intentada:\n{wsUrl}\n\n" +
                            $"Error:\n{msg}\n\n" +
                            $"Config:\n{cfgDiag}",
                            "Error de conexión — Kiosco",
                            MessageBoxButton.OK,
                            MessageBoxImage.Warning));
                }
            };
            _ = _wsService.ConectarAsync();

            // PreviewKeyDown: se dispara ANTES de que TxtCodigo u otro hijo
            // procese la tecla — garantiza que el contador de Escape funcione
            // aunque el TextBox tenga el foco.
            PreviewKeyDown += MainWindow_PreviewKeyDown;

            Loaded  += (_, _) => {
                FocusManager.SetFocusedElement(this, TxtCodigo);
                TxtCodigo.Focus();
                Keyboard.Focus(TxtCodigo);
            };
            Closing += (_, e) => { if (!_desbloqueado && !_cerrandoPorEscape) e.Cancel = true; };
        }

        // ── Eventos de UI ─────────────────────────────────────────────

        // Brushes para validación visual
        private static readonly SolidColorBrush _borderNormal = new(Color.FromRgb(0x8D, 0x99, 0xAE));
        private static readonly SolidColorBrush _borderError  = new(Color.FromRgb(0xFF, 0x4D, 0x4D));

        private async void BtnIngresar_Click(object sender, RoutedEventArgs e)
        {
            try
            {
                // Limpiar errores previos
                LimpiarErroresVisuales();
                bool hayError = false;

                // Validar DNI
                string codigo = TxtCodigo.Text.Trim().ToUpper();
                if (string.IsNullOrEmpty(codigo) || !System.Text.RegularExpressions.Regex.IsMatch(codigo, @"^\d{8}$"))
                {
                    TxtCodigo.BorderBrush = _borderError;
                    TxtEstado.Text = "Ingrese un DNI válido (8 dígitos)";
                    hayError = true;
                }

                // Validar razón de uso
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

                await _wsService.EnviarAsync(JsonSerializer.Serialize(new
                {
                    tipo   = "login_request",
                    codigo,
                    razon
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

        // ── Razón de uso ──────────────────────────────────────────────

        private void CmbRazon_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            if (PanelOtros == null) return; // diseñador
            var item = CmbRazon.SelectedItem as ComboBoxItem;
            string texto = item?.Content?.ToString() ?? "";
            bool esOtros = texto.StartsWith("Otros");
            PanelOtros.Visibility = esOtros ? Visibility.Visible : Visibility.Collapsed;
            if (!esOtros)
            {
                TxtOtroRazon.Text = "";
                TxtErrorOtros.Text = "";
            }
            // Limpiar error visual del ComboBox al cambiar selección
            CmbRazon.BorderBrush = _borderNormal;
        }

        private void TxtOtroRazon_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (PlaceholderOtros == null) return;
            PlaceholderOtros.Visibility = string.IsNullOrEmpty(TxtOtroRazon.Text)
                ? Visibility.Visible
                : Visibility.Collapsed;
            // Limpiar error visual al escribir
            TxtOtroRazon.BorderBrush = _borderNormal;
            TxtErrorOtros.Text = "";
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

        // PreviewKeyDown: tunelización → se ejecuta antes que el hijo con foco
        private void MainWindow_PreviewKeyDown(object sender, KeyEventArgs e)
        {
            // Ctrl+Alt+F10 — master key de emergencia (offline, sin PIN)
            if (e.Key == MASTER_KEY && (Keyboard.Modifiers & MASTER_MOD) == MASTER_MOD)
            {
                e.Handled = true;
                LogDebug("MASTER KEY activada — salida de emergencia");
                ActivarEscapeEmergencia("master_key_offline");
                return;
            }

            if (e.Key == Key.Escape)
            {
                _contadorEscape++;
                LogDebug($"Escape {_contadorEscape}/{ESCAPES_PARA_SALIR}");
                if (_contadorEscape >= ESCAPES_PARA_SALIR)
                    ActivarEscapeEmergencia("escape_5x");
                e.Handled = true; // evitar que el TextBox u otros hijos lo procesen
            }
            else
            {
                _contadorEscape = 0;
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

            // Cancelar el timeout de 10s del login — ya no es necesario
            _loginCts?.Cancel();

            Dispatcher.Invoke(() =>
            {
                Debug.WriteLine($"[ENTRADA] {nombres} {apellidos} | {codigo}");
                LogDebug($"Acceso concedido: {nombres} {apellidos}");

                // Levantar TODAS las capas de seguridad — acceso normal al escritorio
                AppActual.Security.Desbloquear();

                PanelBloqueo.Visibility = Visibility.Collapsed;
                PanelSesion.Visibility  = Visibility.Visible;
                TxtBienvenida.Text      = $"Bienvenido, {nombres}";
                TxtInfoAlumno.Text      = $"Código: {codigo}";

                // Ocultar el kiosco — el escritorio queda completamente libre
                Hide();
            });
        }

        private void Bloquear()
        {
            _desbloqueado   = false;
            _contadorEscape = 0;

            Dispatcher.Invoke(() =>
            {
                // Reactivar TODAS las capas ANTES de mostrarse
                AppActual.Security.Bloquear();

                PanelSesion.Visibility  = Visibility.Collapsed;
                PanelBloqueo.Visibility = Visibility.Visible;
                TxtCodigo.Text          = "";
                TxtEstado.Text          = "";
                BtnIngresar.IsEnabled   = true;
                CmbRazon.SelectedIndex  = 0;
                TxtOtroRazon.Text       = "";
                PanelOtros.Visibility   = Visibility.Collapsed;
                TxtErrorOtros.Text      = "";
                LimpiarErroresVisuales();

                Show();
                CubrirPantallaCompleta();
                Topmost = true;

                // Activate() lleva el foco al proceso — imprescindible para
                // que PreviewKeyDown reciba los eventos de teclado
                Activate();
                TxtCodigo.Focus();
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
                                    // Confirmar desbloqueo exitoso al servidor
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
                        try
                        {
                            // Mover a hilo secundario para no bloquear el WebSocket principal
                            _ = Task.Run(() => Bloquear());
                        }
                        catch (Exception ex)
                        {
                            LogDebug($"ERROR bloquear: {ex}");
                            _ = _wsService.ReportarErrorAsync($"Error bloqueo: {ex.Message}");
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
                                TxtEstado.Text        = motivo;
                                BtnIngresar.IsEnabled = true;
                            });
                        }
                        catch (Exception ex)
                        {
                            LogDebug($"ERROR login_rechazado: {ex}");
                        }
                        break;

                    case "heartbeat_ack":
                        // Sin acción necesaria
                        break;

                    case "remote_command":
                        try
                        {
                            string action = root.TryGetProperty("action", out var act) ? act.GetString() ?? "" : "";
                            if (action == "shutdown")
                            {
                                LogDebug("Comando remoto recibido: enviando session_closed y apagando PC...");
                                // Enviar mensaje de cierre de sesión antes de apagar
                                var sessionClosedMsg = JsonSerializer.Serialize(new { tipo = "session_closed", hora_salida = DateTime.UtcNow.ToString("O") });
                                _ = _wsService.EnviarAsync(sessionClosedMsg);
                                
                                // Pequeña pausa para asegurar que el mensaje se envía
                                System.Threading.Thread.Sleep(500);
                                
                                // Apagar PC
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

        protected override void OnClosed(EventArgs e)
        {
            _loginCts?.Cancel();
            _loginCts?.Dispose();
            _wsService.Desconectar();
            base.OnClosed(e);
        }
    }
}
