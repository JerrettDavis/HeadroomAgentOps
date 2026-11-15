param(
    [Parameter(Mandatory=$true)][string]$DashboardUrl,
    [Parameter(Mandatory=$true)][int]$PanelProcessId
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$panel = Get-Process -Id $PanelProcessId
$panelStarted = $panel.StartTime
# One companion per panel, including manual attachment to an existing panel.
$mutex = New-Object System.Threading.Mutex($false, "Local\HeadroomAgentOpsTray-$PanelProcessId")
if (-not $mutex.WaitOne(0)) { $mutex.Dispose(); exit }
$icon = New-Object System.Windows.Forms.NotifyIcon
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$timer = New-Object System.Windows.Forms.Timer
$context = New-Object System.Windows.Forms.ApplicationContext
try {
    $brandIcon = New-Object System.Drawing.Icon((Join-Path $PSScriptRoot 'brand/icon.ico'), 32, 32)
    $icon.Icon = $brandIcon
    $icon.Text = 'HeadroomAgentOps - open dashboard'
    $icon.ContextMenuStrip = $menu
    $open = $menu.Items.Add('Open dashboard')
    $open.add_Click({ Start-Process $DashboardUrl })
    $icon.add_DoubleClick({ Start-Process $DashboardUrl })
    $status = $menu.Items.Add('Automatic review controls')
    $status.add_Click({ Start-Process "$DashboardUrl/#autostatus" })
    $menu.Items.Add('-') | Out-Null
    $hide = $menu.Items.Add('Hide tray icon (reviews keep running)')
    $hide.add_Click({ $context.ExitThread() })
    $timer.Interval = 3000
    $timer.add_Tick({
        $current = Get-Process -Id $PanelProcessId -ErrorAction SilentlyContinue
        if (-not $current -or $current.StartTime -ne $panelStarted) {
            $context.ExitThread()
        }
    })
    $icon.Visible = $true
    $timer.Start()
    [System.Windows.Forms.Application]::Run($context)
} finally {
    $timer.Stop()
    $timer.Dispose()
    $icon.Visible = $false
    $icon.Dispose()
    if ($brandIcon) { $brandIcon.Dispose() }
    $menu.Dispose()
    $context.Dispose()
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
