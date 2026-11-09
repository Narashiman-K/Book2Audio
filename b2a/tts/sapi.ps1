# book2audio — SAPI fallback engine (Windows PowerShell 5.1)
#
# Legacy desktop voices (Microsoft David Desktop, Zira Desktop...) through
# System.Speech. These are the older, more robotic voices that exist on every
# Windows machine — used only when natural voices (WinRT) are unavailable.
# SSML (W3C flavor) is spoken via SpeakSsml.

param(
    [string]$JobFile,
    [string]$OutDir,
    [string]$VoiceName,
    [string]$StatusFile,
    [switch]$ListVoices
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech

if ($ListVoices) {
    $result = [ordered]@{ ok = $true; engine = 'sapi'; voices = @() }
    try {
        $speak = New-Object System.Speech.Synthesis.SpeechSynthesizer
        foreach ($iv in $speak.GetInstalledVoices()) {
            $vi = $iv.VoiceInfo
            $result.voices += [ordered]@{
                name     = $vi.Name
                id       = $vi.Id
                language = $vi.Culture.Name
                gender   = "$($vi.Gender)"
                engine   = 'sapi'
            }
        }
        $speak.Dispose()
    } catch {
        $result.ok = $false
        $result.error = "$($_.Exception.Message)"
    }
    if ($StatusFile) { $result | ConvertTo-Json -Depth 5 | Set-Content -Path $StatusFile -Encoding UTF8 }
    else { $result | ConvertTo-Json -Depth 5 }
    exit 0
}

$job = Get-Content -Raw -Encoding UTF8 $JobFile | ConvertFrom-Json
$speak = New-Object System.Speech.Synthesis.SpeechSynthesizer

if ($VoiceName) {
    foreach ($iv in $speak.GetInstalledVoices()) {
        if ($iv.VoiceInfo.Name -like "*$VoiceName*") { $speak.SelectVoice($iv.VoiceInfo.Name); break }
    }
}
$chosenVoice = $speak.Voice.Name

$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    22050,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono
)

$results = New-Object System.Collections.Generic.List[object]
foreach ($j in $job.jobs) {
    $r = [ordered]@{ id = $j.id; ok = $false; bytes = 0; error = ''; content_type = 'audio/wav' }
    try {
        $ssml = [System.IO.File]::ReadAllText($j.ssml_path, [System.Text.Encoding]::UTF8)
        $outPath = Join-Path $OutDir ($j.id + $job.out_ext)
        $speak.SetOutputToWaveFile($outPath, $fmt)
        $speak.SpeakSsml($ssml)
        $speak.SetOutputToNull()
        $fi = Get-Item $outPath
        $r.ok = $true
        $r.bytes = $fi.Length
    } catch {
        try { $speak.SetOutputToNull() } catch {}
        $r.error = ("$($_.Exception.Message)" -replace '"', "'")
    }
    $results.Add( (New-Object PSObject -Property $r) )
}

$status = [ordered]@{ ok = $true; engine = 'sapi'; voice = $chosenVoice; results = $results }
if ($StatusFile) { $status | ConvertTo-Json -Depth 5 | Set-Content -Path $StatusFile -Encoding UTF8 }
$speak.Dispose()
exit 0
