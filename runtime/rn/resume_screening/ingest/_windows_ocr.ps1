# Windows built-in OCR bridge for rn.resume_screening.ingest.
#
# Windows.Media.Ocr is a WinRT API.  It is only reliable through Windows
# PowerShell 5.1 (powershell.exe); PowerShell 7 (pwsh) cannot resolve the
# WinRT projections without extra shims.
#
# Invoked by rn.resume_screening.ingest.ocr.WindowsOcrEngine as:
#   powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass ^
#       -File _windows_ocr.ps1 -Path <absolute image path>
#
# Output contract:
#   stdout : UTF-8 text, one OcrResult line per output line.  OcrResult.Text
#            itself joins every line with a space (which destroys the line
#            structure resumes rely on), so we emit OcrResult.Lines instead.
#   stderr : "OCR_ERROR: <reason>" and a non-zero exit code on failure.
#
# This file is intentionally ASCII-only: Windows PowerShell 5.1 reads .ps1
# files without a BOM using the legacy ANSI code page, so non-ASCII literals
# would be mangled at parse time.

param(
    [Parameter(Mandatory = $false)][string]$Path,
    [switch]$Probe
)

$ErrorActionPreference = 'Stop'

try {
    # Best effort: force UTF-8 stdout bytes even when the pipe is redirected.
    # On some hosts a redirected handle refuses the switch; the Python side
    # falls back to gb18030 / cp936 decoding in that case.
    try {
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    } catch {
        # ignored on purpose
    }

    Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

    # -Probe：只检查本机是否真的装了 OCR 识别语言（WindowsOcrEngine.available()
    # 用它判断「引擎可用」，避免只查 powershell.exe 存在却一识别就 0 语言失败）。
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    if ($Probe) {
        $probeEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
        if ($null -eq $probeEngine) {
            $probeLanguages = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages
            if ($probeLanguages.Count -gt 0) {
                $probeEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($probeLanguages[0])
            }
        }
        if ($null -eq $probeEngine) {
            throw 'Windows OCR engine unavailable: no recognizer language installed'
        }
        [Console]::Out.Write('OK')
        exit 0
    }
    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw 'Path is required unless -Probe is used'
    }

    $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

    function Await($WinRtTask, $ResultType) {
        $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
        $netTask = $asTask.Invoke($null, @($WinRtTask))
        $netTask.Wait(-1) | Out-Null
        $netTask.Result
    }

    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]

    $full = (Resolve-Path -LiteralPath $Path).Path
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($full)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($null -eq $engine) {
        # This machine may only expose zh-Hans-CN; fall back to the first
        # installed recognizer language explicitly.
        $languages = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages
        if ($languages.Count -gt 0) {
            $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($languages[0])
        }
    }
    if ($null -eq $engine) {
        throw 'Windows OCR engine unavailable: no recognizer language installed'
    }

    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $stream.Dispose()

    $lines = @($result.Lines | ForEach-Object { $_.Text })
    if ($lines.Count -eq 0) {
        # Rare single-line images still expose result.Text; keep it as fallback.
        $lines = @($result.Text)
    }
    [Console]::Out.Write((($lines) -join "`n"))
} catch {
    [Console]::Error.Write('OCR_ERROR: ' + $_.Exception.Message)
    exit 1
}
