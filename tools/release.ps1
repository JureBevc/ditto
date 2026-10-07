# Release window for ditto: builds Build\ditto.exe and publishes it as a GitHub release on origin.
# Launched by release.bat. Requires the GitHub CLI (gh), logged in.

# Native commands write to stderr for normal messages, so exit codes are checked explicitly instead of aborting.
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$script:busy = $false
$script:ready = $false
$script:exitCode = 1
$script:version = $null
$script:tag = $null
$script:head = $null
$script:exe = $null

# ---------- Window ----------
$form = New-Object System.Windows.Forms.Form
$form.Text = 'ditto release'
$form.StartPosition = 'CenterScreen'
$form.Size = New-Object System.Drawing.Size(760, 520)
$form.MinimumSize = New-Object System.Drawing.Size(560, 360)
$form.Font = New-Object System.Drawing.Font('Segoe UI', 9)

$log = New-Object System.Windows.Forms.TextBox
$log.Multiline = $true
$log.ReadOnly = $true
$log.ScrollBars = 'Vertical'
$log.Dock = 'Fill'
$log.Font = New-Object System.Drawing.Font('Consolas', 9)

$status = New-Object System.Windows.Forms.Label
$status.Dock = 'Top'
$status.Height = 44
$status.Padding = New-Object System.Windows.Forms.Padding(12, 12, 12, 0)
$status.Font = New-Object System.Drawing.Font('Segoe UI', 11, [System.Drawing.FontStyle]::Bold)
$status.Text = 'Starting...'

$bottom = New-Object System.Windows.Forms.Panel
$bottom.Dock = 'Bottom'
$bottom.Height = 54

$draft = New-Object System.Windows.Forms.CheckBox
$draft.Text = 'Create as draft (visible only to repo members until published)'
$draft.AutoSize = $true
$draft.Location = New-Object System.Drawing.Point(12, 17)
$draft.Enabled = $false

$buttons = New-Object System.Windows.Forms.FlowLayoutPanel
$buttons.Dock = 'Right'
$buttons.AutoSize = $true
$buttons.FlowDirection = 'LeftToRight'
$buttons.Padding = New-Object System.Windows.Forms.Padding(0, 9, 9, 0)

$publish = New-Object System.Windows.Forms.Button
$publish.Text = 'Publish'
$publish.AutoSize = $true
$publish.Enabled = $false
$publish.Margin = New-Object System.Windows.Forms.Padding(0, 0, 8, 0)

$close = New-Object System.Windows.Forms.Button
$close.Text = 'Close'
$close.AutoSize = $true

$buttons.Controls.Add($publish)
$buttons.Controls.Add($close)
$bottom.Controls.Add($draft)
$bottom.Controls.Add($buttons)

# Fill first so the status bar and button bar keep their docked sizes.
$form.Controls.Add($log)
$form.Controls.Add($status)
$form.Controls.Add($bottom)

# ---------- Helpers ----------
function Write-Log([string]$Text) {
    $log.AppendText($Text + "`r`n")
    $log.SelectionStart = $log.TextLength
    $log.ScrollToCaret()
    [System.Windows.Forms.Application]::DoEvents()
}

function Set-Status([string]$Text, [System.Drawing.Color]$Color) {
    $status.Text = $Text
    $status.ForeColor = $Color
    [System.Windows.Forms.Application]::DoEvents()
}

function Set-Controls {
    $publish.Enabled = $script:ready -and -not $script:busy
    $draft.Enabled = $script:ready -and -not $script:busy
    $close.Enabled = -not $script:busy
    [System.Windows.Forms.Application]::DoEvents()
}

# Runs a command, streaming its output into the log. Returns the exit code.
function Invoke-Tool([string]$Exe, [string[]]$Arguments) {
    Write-Log ("> $Exe " + ($Arguments -join ' '))
    & $Exe @Arguments 2>&1 | ForEach-Object { Write-Log ([string]$_) }
    return $LASTEXITCODE
}

# Runs a command silently. Returns the exit code.
function Invoke-Quiet([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments *> $null
    return $LASTEXITCODE
}

function Show-Failure([string]$Message) {
    Write-Log "ERROR: $Message"
    Set-Status 'Stopped. See the log for details.' ([System.Drawing.Color]::Firebrick)
    $script:exitCode = 1
}

# ---------- Steps ----------
function Start-Release {
    $script:busy = $true
    Set-Controls
    try {
        Set-Status 'Checking tools...' ([System.Drawing.Color]::Black)
        if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
            throw 'GitHub CLI (gh) not found. Install it with: winget install GitHub.cli'
        }
        if ((Invoke-Tool 'gh' @('auth', 'status')) -ne 0) { throw 'gh is not logged in. Run: gh auth login' }

        # The version comes from the FileVersion compiled into the .exe, so the tag always matches the binary.
        $rc = Get-Content -Raw (Join-Path $root 'res\app.rc')
        if ($rc -notmatch 'VALUE "FileVersion", "(\d+)\.(\d+)\.(\d+)') { throw 'FileVersion not found in res\app.rc' }
        $script:version = "$($Matches[1]).$($Matches[2]).$($Matches[3])"
        $script:tag = "v$($script:version)"
        Write-Log "Release $($script:tag)"

        Set-Status 'Checking for an existing release...' ([System.Drawing.Color]::Black)
        if ((Invoke-Quiet 'gh' @('release', 'view', $script:tag)) -eq 0) {
            throw "Release $($script:tag) already exists. Bump FileVersion and ProductVersion in res\app.rc."
        }
        Write-Log "No release $($script:tag) yet."

        # gh tags the remote commit, so HEAD must already be pushed.
        $script:head = (& git rev-parse HEAD 2>$null | Select-Object -First 1).Trim()
        $remoteRefs = & git ls-remote origin 2>$null
        if (-not ($remoteRefs | Select-String -SimpleMatch $script:head)) {
            throw "HEAD ($($script:head.Substring(0, 7))) is not the tip of a branch on origin. Commit and push first."
        }
        Write-Log "HEAD $($script:head.Substring(0, 7)) is on origin."

        # Uncommitted source changes would make the .exe differ from the tagged commit.
        # Build\ is excluded because Build\ditto.ini is runtime state the app writes itself.
        $dirty = & git status --porcelain -- . ':!Build' 2>$null
        if ($dirty) {
            throw ("Uncommitted changes outside Build\:`n" + ($dirty -join "`n") + "`nCommit them first.")
        }
        Write-Log 'Working tree is clean.'

        Set-Status 'Building Build\ditto.exe...' ([System.Drawing.Color]::Black)
        if ((Invoke-Tool 'cmd' @('/c', 'build.bat')) -ne 0) { throw 'build.bat failed.' }

        $script:exe = Join-Path $root 'Build\ditto.exe'
        if (-not (Test-Path $script:exe)) { throw 'Build\ditto.exe was not produced.' }

        $script:ready = $true
        Set-Status "Ready to publish $($script:tag). Choose draft or public, then click Publish." ([System.Drawing.Color]::DarkGreen)
    }
    catch {
        Show-Failure $_.Exception.Message
    }
    finally {
        $script:busy = $false
        Set-Controls
    }
}

function Publish-Release {
    $script:busy = $true
    $script:ready = $false
    Set-Controls
    try {
        $kind = if ($draft.Checked) { 'draft' } else { 'public' }
        Set-Status "Publishing $($script:tag) as $kind..." ([System.Drawing.Color]::Black)

        $ghArgs = @(
            'release', 'create', $script:tag, "$($script:exe)#ditto.exe",
            '--target', $script:head,
            '--title', "ditto $($script:version)",
            '--generate-notes'
        )
        if ($draft.Checked) { $ghArgs += '--draft' }

        if ((Invoke-Tool 'gh' $ghArgs) -ne 0) { throw 'gh release create failed.' }

        Set-Status "Published $($script:tag) ($kind)." ([System.Drawing.Color]::DarkGreen)
        $script:exitCode = 0
    }
    catch {
        Show-Failure $_.Exception.Message
    }
    finally {
        $script:busy = $false
        Set-Controls
    }
}

# ---------- Wiring ----------
$publish.Add_Click({ Publish-Release })
$close.Add_Click({ $form.Close() })
$form.Add_FormClosing({
    param($sender, $e)
    if ($script:busy) { $e.Cancel = $true }
})
$form.Add_Shown({ Start-Release })

[void]$form.ShowDialog()
exit $script:exitCode
