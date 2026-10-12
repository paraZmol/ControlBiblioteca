using System;
using System.Net.WebSockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace ControlBiblioteca.Client.Services
{
    /// <summary>
    /// Servicio de conexión WebSocket con el servidor.
    /// Maneja reconexión automática y heartbeat.
    /// </summary>
    public class WebSocketService
    {
        private ClientWebSocket? _ws;
        private readonly string _url;
        private CancellationTokenSource _cts = new();

        public bool EstaConectado => _ws?.State == System.Net.WebSockets.WebSocketState.Open;

        // Eventos
        public event Action<string>? OnMensajeRecibido;
        public event Action<bool>?   OnConexionCambiada;
        public event Action<string>? OnError;

        private const int HEARTBEAT_MS   = 3000;    // Heartbeat cada 3 segundos para mayor estabilidad
        private const int RECONEXION_MIN = 5000;   // espera inicial entre reintentos
        private const int RECONEXION_MAX = 60000;  // tope: no reconectar más de 1 vez/minuto
        private const int ESPERA_SESION_INACTIVA_MS = 3000;

        // Un solo envío a la vez: ClientWebSocket no admite SendAsync concurrentes
        // (heartbeat + foco + actividad podían coincidir y lanzar excepción).
        private readonly SemaphoreSlim _envioLock = new(1, 1);

        // ── Sesión de Windows activa ──
        // Con "Cambiar de usuario" quedan varias sesiones abiertas y CADA una corre
        // su kiosco. Si todos se conectaran, el servidor tendría varios sockets con
        // el mismo nombre de PC y un bloqueo podía llegar a la sesión que nadie ve.
        // Regla: solo el kiosco de la sesión ACTIVA (la que está en pantalla) se
        // conecta; los de sesiones en segundo plano esperan desconectados.
        [DllImport("wtsapi32.dll", SetLastError = true)]
        private static extern bool WTSQuerySessionInformation(IntPtr hServer, int sessionId, int infoClass,
                                                              out IntPtr buffer, out int bytesReturned);
        [DllImport("wtsapi32.dll")]
        private static extern void WTSFreeMemory(IntPtr memory);
        private const int WTS_CURRENT_SESSION = -1;
        private const int WTSConnectState     = 8;
        private const int WTSActive           = 0;

        public static bool SesionActiva()
        {
            try
            {
                if (!WTSQuerySessionInformation(IntPtr.Zero, WTS_CURRENT_SESSION, WTSConnectState, out IntPtr buf, out _))
                    return true;   // ante la duda, comportarse como siempre (conectar)
                try { return Marshal.ReadInt32(buf) == WTSActive; }
                finally { WTSFreeMemory(buf); }
            }
            catch { return true; }
        }

        public WebSocketService(string url)
        {
            _url = url;
        }

        /// <summary>
        /// Función que devuelve el mensaje JSON a enviar tras cada conexión exitosa.
        /// Se evalúa en cada reconexión para capturar el estado actual del cliente.
        /// </summary>
        public Func<string>? InitialGreeting { get; set; }

        /// <summary>
        /// Conectar al servidor WebSocket con reconexión automática.
        /// </summary>
        public async Task ConectarAsync()
        {
            int espera = RECONEXION_MIN;

            while (!_cts.Token.IsCancellationRequested)
            {
                // Liberar conexión anterior antes de crear una nueva
                _ws?.Dispose();
                _ws = null;

                // Sesión en segundo plano (otro usuario está en pantalla): no conectar.
                if (!SesionActiva())
                {
                    try { await Task.Delay(ESPERA_SESION_INACTIVA_MS, _cts.Token).ConfigureAwait(false); }
                    catch (OperationCanceledException) { break; }
                    espera = RECONEXION_MIN;
                    continue;
                }

                try
                {
                    _ws = new ClientWebSocket();
                    await _ws.ConnectAsync(new Uri(_url), _cts.Token);
                    espera = RECONEXION_MIN; // resetear backoff al conectar

                    // Enviar saludo ANTES de notificar conexión — el servidor
                    // procesa el hello y puede mandar bloquear antes de que
                    // OnConexionCambiada active la lógica de sesión offline.
                    // Al invertir el orden: primero hello → servidor responde
                    // hello_ack (o bloquear) → cliente ya sabe el estado →
                    // luego OnConexionCambiada activa la lógica correcta.
                    if (InitialGreeting != null)
                        await EnviarAsync(InitialGreeting());

                    var wsActual = _ws;
                    _ = Task.Run(() => HeartbeatLoopAsync(wsActual), _cts.Token);

                    // Leer exactamente un mensaje (el hello_ack o bloquear)
                    // antes de notificar al resto del cliente
                    string? primerMensaje = await LeerUnMensajeAsync();
                    OnConexionCambiada?.Invoke(true);
                    if (primerMensaje != null)
                        OnMensajeRecibido?.Invoke(primerMensaje);

                    await EscucharAsync();
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (Exception ex)
                {
                    OnConexionCambiada?.Invoke(false);
                    OnError?.Invoke(ex.Message);
                    // No podemos reportar via WS si falló la conexión, pero lo dejamos listo
                }

                if (!_cts.Token.IsCancellationRequested)
                {
                    try
                    {
                        await Task.Delay(espera, _cts.Token).ConfigureAwait(false);
                        espera = Math.Min(espera * 2, RECONEXION_MAX);
                    }
                    catch (OperationCanceledException) { break; }
                }
            }
        }

        /// <summary>
        /// Enviar mensaje al servidor.
        /// </summary>
        public async Task EnviarAsync(string mensaje)
        {
            var ws = _ws;
            if (ws?.State != WebSocketState.Open) return;
            var bytes = Encoding.UTF8.GetBytes(mensaje);
            await _envioLock.WaitAsync(_cts.Token).ConfigureAwait(false);
            try
            {
                if (ws.State == WebSocketState.Open)
                    await ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, _cts.Token);
            }
            finally { _envioLock.Release(); }
        }

        /// <summary>
        /// Envía un reporte de error al servidor para que aparezca en el panel admin.
        /// </summary>
        public async Task ReportarErrorAsync(string detalle)
        {
            try
            {
                if (EstaConectado)
                    await EnviarAsync(JsonSerializer.Serialize(new { tipo = "error_report", message = detalle }));
            }
            catch { /* evitar bucles infinitos de error */ }
        }

        /// <summary>
        /// Desconectar del servidor.
        /// </summary>
        public void Desconectar()
        {
            _cts.Cancel();
            if (_ws?.State == WebSocketState.Open)
            {
                _ = _ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "Cierre", CancellationToken.None);
            }
            _ws?.Dispose();
        }

        // ── Métodos privados ───────────────────────────────────────

        private async Task<string?> LeerUnMensajeAsync()
        {
            if (_ws == null) return null;
            try
            {
                var buffer = new byte[4096];
                var result = await _ws.ReceiveAsync(new ArraySegment<byte>(buffer), _cts.Token);
                if (result.MessageType == WebSocketMessageType.Close) return null;
                return Encoding.UTF8.GetString(buffer, 0, result.Count);
            }
            catch { return null; }
        }

        private async Task EscucharAsync()
        {
            var buffer = new byte[4096];
            while (_ws?.State == WebSocketState.Open && !_cts.Token.IsCancellationRequested)
            {
                var result = await _ws.ReceiveAsync(new ArraySegment<byte>(buffer), _cts.Token);
                if (result.MessageType == WebSocketMessageType.Close)
                    break;

                string mensaje = Encoding.UTF8.GetString(buffer, 0, result.Count);
                OnMensajeRecibido?.Invoke(mensaje);
            }
        }

        // Cada conexión tiene su propio heartbeat atado a SU socket: tras una
        // reconexión el loop viejo termina en vez de sumarse al nuevo.
        private async Task HeartbeatLoopAsync(ClientWebSocket? ws)
        {
            while (!_cts.Token.IsCancellationRequested && ws != null
                   && ReferenceEquals(ws, _ws) && ws.State == WebSocketState.Open)
            {
                try
                {
                    await Task.Delay(HEARTBEAT_MS, _cts.Token);
                    if (!ReferenceEquals(ws, _ws)) break;
                    // El usuario cambió de sesión: soltar la conexión para que la
                    // tome el kiosco de la sesión que quedó en pantalla.
                    if (!SesionActiva())
                    {
                        // CloseOutputAsync (no CloseAsync): hay un ReceiveAsync pendiente en EscucharAsync,
                        // que recibirá el cierre del servidor y terminará solo.
                        try
                        {
                            await _envioLock.WaitAsync(_cts.Token).ConfigureAwait(false);
                            try { await ws.CloseOutputAsync(WebSocketCloseStatus.NormalClosure, "sesion inactiva", CancellationToken.None); }
                            finally { _envioLock.Release(); }
                        }
                        catch { /* el loop de conexión se encarga */ }
                        break;
                    }
                    await EnviarAsync("{\"tipo\":\"heartbeat\"}");
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (Exception ex)
                {
                    System.Diagnostics.Debug.WriteLine($"[Heartbeat] Error: {ex.Message}");
                    _ = ReportarErrorAsync("Fallo de Heartbeat: " + ex.Message);
                    break;
                }
            }
        }
    }
}
