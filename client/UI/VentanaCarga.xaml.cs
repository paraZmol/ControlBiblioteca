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
        // Shell_TrayWnd es la ventana de la barra de tareas de Windows.
        // Cuando existe, explorer.exe terminó de cargar y el escritorio está listo.
        [DllImport("user32.dll", SetLastError = true)]
        private static extern IntPtr FindWindow(string lpClassName, string? lpWindowName);

        public VentanaCarga()
        {
            InitializeComponent();
            Loaded += async (_, _) => await EsperarYTransicionarAsync();
        }

        protected override void OnSourceInitialized(EventArgs e)
        {
            base.OnSourceInitialized(e);
            CubrirPantallaCompleta();
        }

        private void CubrirPantallaCompleta()
        {
            // WindowState.Maximized excluye la barra de tareas — usamos coordenadas explícitas
            Left   = 0;
            Top    = 0;
            Width  = SystemParameters.PrimaryScreenWidth;
            Height = SystemParameters.PrimaryScreenHeight;
        }

        private async Task EsperarYTransicionarAsync()
        {
            // Esperar a que explorer haya cargado la barra de tareas
            // Límite: 30 segundos (60 intentos × 500 ms)
            for (int i = 0; i < 60; i++)
            {
                if (FindWindow("Shell_TrayWnd", null) != IntPtr.Zero)
                    break;
                await Task.Delay(500);
            }

            // Margen extra para que explorer termine de estabilizarse
            await Task.Delay(800);

            // Transición al kiosco — ya con el escritorio cargado debajo
            var mainWindow = new MainWindow();
            Application.Current.MainWindow = mainWindow;
            mainWindow.Show();
            Close();
        }
    }
}
