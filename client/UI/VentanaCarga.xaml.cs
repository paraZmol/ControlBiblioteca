using System;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows;

namespace ControlBiblioteca.Client.UI
{
    /// <summary>
    /// Pantalla de carga que aparece inmediatamente después del login.
    /// Espera a que explorer.exe haya terminado de cargar (detección via Shell_TrayWnd)
    /// y solo entonces muestra el kiosco — evitando la pantalla negra inicial.
    ///
    /// La ventana es Topmost y cubre la pantalla completa (incluida la barra de tareas)
    /// mientras el escritorio carga en segundo plano.
    /// </summary>
    public partial class VentanaCarga : Window
    {
        [DllImport("user32.dll", SetLastError = true)]
        private static extern IntPtr FindWindow(string lpClassName, string? lpWindowName);

        // Si es true, la ventana no abre MainWindow sola — el control lo tiene App.xaml.cs
        private readonly bool _modoManual;

        public VentanaCarga(bool modoManual = false)
        {
            InitializeComponent();
            _modoManual = modoManual;
            if (!modoManual)
                Loaded += async (_, _) => await EsperarYTransicionarAsync();
        }

        public void ActualizarMensaje(string mensaje)
        {
            // Permite mostrar estado de actualización en la pantalla de carga
            // La VentanaCarga.xaml tiene un TextBlock con nombre TxtEstado si se desea mostrar
            System.Diagnostics.Debug.WriteLine($"[AutoUpdater] {mensaje}");
        }

        protected override void OnSourceInitialized(EventArgs e)
        {
            base.OnSourceInitialized(e);
            CubrirPantallaCompleta();
        }

        private void CubrirPantallaCompleta()
        {
            Left   = 0;
            Top    = 0;
            Width  = SystemParameters.PrimaryScreenWidth;
            Height = SystemParameters.PrimaryScreenHeight;
        }

        private async Task EsperarYTransicionarAsync()
        {
            for (int i = 0; i < 60; i++)
            {
                if (FindWindow("Shell_TrayWnd", null) != IntPtr.Zero)
                    break;
                await Task.Delay(500);
            }
            await Task.Delay(800);

            var mainWindow = new MainWindow();
            Application.Current.MainWindow = mainWindow;
            mainWindow.Show();
            Close();
        }
    }
}
