using System;
using System.Windows;
using System.Windows.Threading;

namespace ControlBiblioteca.Client.UI
{
    public partial class VentanaDesconexion : Window
    {
        private readonly DispatcherTimer _timer;
        private int _segundosRestantes;

        public event Action? TiempoAgotado;

        public VentanaDesconexion(int segundos = 180, string? motivo = null)
        {
            InitializeComponent();
            _segundosRestantes = segundos;

            _timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
            _timer.Tick += OnTick;

            Loaded += (_, _) =>
            {
                PositionarEsquinaSuperiorDerecha();
                if (!string.IsNullOrWhiteSpace(motivo))
                    TxtMotivo.Text = motivo;
            };
        }

        public void Iniciar()
        {
            ActualizarTexto();
            _timer.Start();
            Show();
        }

        public void Cancelar()
        {
            _timer.Stop();
            Hide();
        }

        private void OnTick(object? sender, EventArgs e)
        {
            _segundosRestantes--;
            ActualizarTexto();

            if (_segundosRestantes <= 0)
            {
                _timer.Stop();
                Hide();
                TiempoAgotado?.Invoke();
            }
        }

        private void ActualizarTexto()
        {
            int min = _segundosRestantes / 60;
            int seg = _segundosRestantes % 60;
            TxtCuentaRegresiva.Text = $"{min}:{seg:D2}";
        }

        private void PositionarEsquinaSuperiorDerecha()
        {
            var area = SystemParameters.WorkArea;
            Left = area.Right - Width - 16;
            Top  = area.Top + 80; // un poco más abajo para no tapar la barra de tareas de apps
        }
    }
}
