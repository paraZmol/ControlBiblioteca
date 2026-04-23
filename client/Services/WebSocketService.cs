using System;
using System.Net.WebSockets;
using System.Text;
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

        private const int HEARTBEAT_MS   = 5000;    // Heartbeat cada 5 segundos para mantener conexión estable
        private const int RECONEXION_MIN = 5000;   // espera inicial entre reintentos
        private const int RECONEXION_MAX = 60000;  // tope: no reconectar más de 1 vez/minuto

        public WebSocketService(string url)
        {
            _url = url;
        }

        /// <summary>
        /// Mensaje JSON que se envía automáticamente tras cada conexión exitosa.
        /// Usado para enviar el hello con hostname.
        /// </summary>
        public string? InitialGreeting { get; set; }

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

                try
                {
                    _ws = new ClientWebSocket();
                    await _ws.ConnectAsync(new Uri(_url), _cts.Token);
                    OnConexionCambiada?.Invoke(true);
                    espera = RECONEXION_MIN; // resetear backoff al conectar

                    // Enviar saludo inicial con hostname si está configurado
                    if (!string.IsNullOrEmpty(InitialGreeting))
                        await EnviarAsync(InitialGreeting);

                    _ = Task.Run(HeartbeatLoopAsync, _cts.Token);
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
            if (_ws?.State == WebSocketState.Open)
            {
                var bytes = Encoding.UTF8.GetBytes(mensaje);
                await _ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, _cts.Token);
            }
        }

        /// <summary>
        /// Envía un reporte de error al servidor para que aparezca en el panel admin.
        /// </summary>
        public async Task ReportarErrorAsync(string detalle)
        {
            try
            {
                if (EstaConectado)
                {
                    var msg = "{\"type\":\"error_report\", \"message\":\"" + detalle.Replace("\"", "'").Replace("\\", "/") + "\"}";
                    await EnviarAsync(msg);
                }
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

        private async Task HeartbeatLoopAsync()
        {
            while (!_cts.Token.IsCancellationRequested && _ws?.State == WebSocketState.Open)
            {
                try
                {
                    await Task.Delay(HEARTBEAT_MS, _cts.Token);
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
