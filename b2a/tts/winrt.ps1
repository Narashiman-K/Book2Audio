# book2audio — WinRT TTS batch engine (Windows PowerShell 5.1)
#
# Drives Windows 11 speech through Windows.Media.SpeechSynthesis (WinRT),
# which is the ONLY API family that can see OneCore and Narrator natural
# voices (Ava, Jenny, Aria, ...). System.Speech/SAPI cannot.
#
# Modes:
#   -ListVoices                                   -> dump all voices as JSON
#   -JobFile jobs.json -OutDir DIR [-VoiceName X] -> synthesize a batch
#
# Job file format (UTF-8 JSON):
#   { "out_ext": ".audio", "jobs": [ {"id": "...", "ssml_path": "..."}, ... ] }
#
# A status JSON (per-job ok/error/bytes) is written to -StatusFile so the
# Python side can resume cleanly. One failed chunk never kills the batch.

param(
    [string]$JobFile,
    [string]$OutDir,
    [string]$VoiceName,
    [string]$StatusFile,
    [switch]$ListVoices
)

$ErrorActionPreference = 'Stop'

# ---- load WinRT types ------------------------------------------------------
[void][Windows.Foundation.IAsyncOperation`1, Windows.Foundation, ContentType=WindowsRuntime]
[void][Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType=WindowsRuntime]
[void][Windows.Media.SpeechSynthesis.SpeechSynthesisStream, Windows.Media.SpeechSynthesis, ContentType=WindowsRuntime]
Add-Type -AssemblyName System.Runtime.WindowsRuntime

# ---- WinRT IAsyncOperation<T> -> .NET Task helper --------------------------
$_taskMethods = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1
}
$asTaskGeneric = ($_taskMethods | Where-Object {
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

function Write-Status($obj, $path) {
    $obj | ConvertTo-Json -Depth 5 | Set-Content -Path $path -Encoding UTF8
}

# ---- mode: list voices -----------------------------------------------------
if ($ListVoices) {
    $result = [ordered]@{ ok = $true; engine = 'winrt'; voices = @() }
    try {
        $voices = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices
        foreach ($v in $voices) {
            $result.voices += [ordered]@{
                name     = $v.DisplayName
                id       = $v.Id
                language = $v.Language
                gender   = "$($v.Gender)"
                engine   = 'winrt'
            }
        }
    } catch {
        $result.ok = $false
        $result.error = "$($_.Exception.Message)"
    }
    if ($StatusFile) { Write-Status $result $StatusFile } else { $result | ConvertTo-Json -Depth 5 }
    exit 0
}

# ---- mode: synthesize a batch ----------------------------------------------
$synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
$chosenVoice = 'default'

if ($VoiceName) {
    foreach ($v in $synth.AllVoices) {
        if ($v.DisplayName -like "*$VoiceName*") { $synth.Voice = $v; break }
    }
    if (-not $synth.Voice) {
        foreach ($v in $synth.AllVoices) {
            if ($v.Description -like "*$VoiceName*") { $synth.Voice = $v; break }
        }
    }
}
try { $chosenVoice = "$($synth.Voice.DisplayName)" } catch { $chosenVoice = 'default' }

$job = Get-Content -Raw -Encoding UTF8 $JobFile | ConvertFrom-Json
$results = New-Object System.Collections.Generic.List[object]

foreach ($j in $job.jobs) {
    $r = [ordered]@{ id = $j.id; ok = $false; bytes = 0; error = ''; content_type = '' }
    try {
        $ssml = [System.IO.File]::ReadAllText($j.ssml_path, [System.Text.Encoding]::UTF8)
        if ($ssml.TrimStart().StartsWith("<speak")) {
            $winrtStream = Await ($synth.SynthesizeSsmlToStreamAsync($ssml)) `
                ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
        } else {
            $winrtStream = Await ($synth.SynthesizeTextToStreamAsync($ssml)) `
                ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
        }

        $r.content_type = "$($winrtStream.ContentType)"
        $netStream = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($winrtStream)
        $ms = New-Object System.IO.MemoryStream
        $netStream.CopyTo($ms)
        $bytes = $ms.ToArray()

        $outPath = Join-Path $OutDir ($j.id + $job.out_ext)
        [System.IO.File]::WriteAllBytes($outPath, $bytes)

        $r.ok = $true
        $r.bytes = $bytes.Length

        $netStream.Dispose()
        $ms.Dispose()
        $winrtStream.Dispose()
    } catch {
        $r.error = ("$($_.Exception.Message)" -replace '"', "'").Substring(0, [Math]::Min(300, ("$($_.Exception.Message)".Length)))
    }
    $results.Add( (New-Object PSObject -Property $r) )
}

$status = [ordered]@{ ok = $true; engine = 'winrt'; voice = $chosenVoice; results = $results }
if ($StatusFile) { Write-Status $status $StatusFile }
try { $synth.Dispose() } catch {}
exit 0
