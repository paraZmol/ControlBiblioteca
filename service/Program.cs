using ControlBiblioteca.Service;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

IHost host = Host.CreateDefaultBuilder(args)
    .UseWindowsService(options =>
    {
        options.ServiceName = "ControlBibliotecaPreWarm";
    })
    .ConfigureServices(services =>
    {
        services.AddHostedService<PreWarmWorker>();
    })
    .Build();

await host.RunAsync();
