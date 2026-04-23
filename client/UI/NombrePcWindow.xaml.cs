using System.Windows;

namespace ControlBiblioteca.Client.UI
{
    public partial class NombrePcWindow : Window
    {
        public string NombreResultado { get; private set; } = "";

        public NombrePcWindow()
        {
            InitializeComponent();
            TxtNombre.Focus();
        }

        private void Button_Click(object sender, RoutedEventArgs e)
        {
            if (string.IsNullOrWhiteSpace(TxtNombre.Text))
            {
                MessageBox.Show("Por favor ingrese un nombre para la terminal.", "Aviso", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            NombreResultado = TxtNombre.Text.Trim().ToUpper();
            DialogResult = true;
            Close();
        }
    }
}
