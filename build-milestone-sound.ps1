$ErrorActionPreference = 'Stop'

$assetsDirectory = Join-Path $PSScriptRoot 'assets'
$outputPath = Join-Path $assetsDirectory 'milestone-alert.wav'
[System.IO.Directory]::CreateDirectory($assetsDirectory) | Out-Null

$sampleRate = 44100
$durationSeconds = 0.72
$sampleCount = [int]($sampleRate * $durationSeconds)
$dataSize = $sampleCount * 2
$pi = [Math]::PI
$notes = @(
    @{ Frequency = 659.25; Start = 0.00; Duration = 0.46; Amplitude = 0.26 },
    @{ Frequency = 987.77; Start = 0.10; Duration = 0.52; Amplitude = 0.20 },
    @{ Frequency = 1318.51; Start = 0.21; Duration = 0.60; Amplitude = 0.15 }
)

$stream = [System.IO.File]::Open(
    $outputPath,
    [System.IO.FileMode]::Create,
    [System.IO.FileAccess]::Write,
    [System.IO.FileShare]::Read
)
$writer = [System.IO.BinaryWriter]::new($stream)
try {
    $writer.Write([System.Text.Encoding]::ASCII.GetBytes('RIFF'))
    $writer.Write([int](36 + $dataSize))
    $writer.Write([System.Text.Encoding]::ASCII.GetBytes('WAVE'))
    $writer.Write([System.Text.Encoding]::ASCII.GetBytes('fmt '))
    $writer.Write([int]16)
    $writer.Write([int16]1)
    $writer.Write([int16]1)
    $writer.Write([int]$sampleRate)
    $writer.Write([int]($sampleRate * 2))
    $writer.Write([int16]2)
    $writer.Write([int16]16)
    $writer.Write([System.Text.Encoding]::ASCII.GetBytes('data'))
    $writer.Write([int]$dataSize)

    for ($sampleIndex = 0; $sampleIndex -lt $sampleCount; $sampleIndex++) {
        $time = $sampleIndex / $sampleRate
        $value = 0.0
        foreach ($note in $notes) {
            $localTime = $time - $note.Start
            if ($localTime -ge 0 -and $localTime -le $note.Duration) {
                $attack = [Math]::Min(1.0, $localTime / 0.025)
                $releaseStart = $note.Duration - 0.14
                $release = if ($localTime -gt $releaseStart) {
                    [Math]::Max(0.0, ($note.Duration - $localTime) / 0.14)
                } else {
                    1.0
                }
                $envelope = $attack * $release * [Math]::Exp(-$localTime * 1.9)
                $phase = 2.0 * $pi * $note.Frequency * $localTime
                $bellTone = [Math]::Sin($phase) + (0.22 * [Math]::Sin($phase * 2.0)) + (0.08 * [Math]::Sin($phase * 3.0))
                $value += $note.Amplitude * $envelope * $bellTone
            }
        }
        $value = [Math]::Max(-1.0, [Math]::Min(1.0, $value))
        $writer.Write([int16][Math]::Round($value * 32767.0))
    }
}
finally {
    $writer.Dispose()
    $stream.Dispose()
}
