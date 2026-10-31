$ErrorActionPreference = "SilentlyContinue"
# Aviso na area de trabalho quando a sincronizacao do clube falha varias vezes.
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
        [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $textos = $template.GetElementsByTagName("text")
    $textos.Item(0).AppendChild($template.CreateTextNode("Clube Diana - sincronizacao")) | Out-Null
    $textos.Item(1).AppendChild($template.CreateTextNode(
        "O envio de cadastros para o PDV esta falhando. Abra o painel da loja.")) | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Clube Diana").Show($toast)
} catch {
    # se o Windows bloquear o aviso, o ALERTA.txt continua valendo
}
