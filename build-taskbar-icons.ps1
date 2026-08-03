$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;

public static class CodexIconNativeMethods
{
    [DllImport("user32.dll")]
    public static extern bool DestroyIcon(IntPtr handle);
}
"@

$assetsDirectory = Join-Path $PSScriptRoot "assets"
$outputDirectory = Join-Path $assetsDirectory "taskbar"
[System.IO.Directory]::CreateDirectory($outputDirectory) | Out-Null

foreach ($percent in 0..100) {
    $bitmap = [System.Drawing.Bitmap]::new(
        64,
        64,
        [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
    )
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $shadowBrush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(255, 13, 18, 36))
    $textBrush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::White)
    $fontSize = if ($percent -ge 100) { 22 } elseif ($percent -ge 10) { 28 } else { 32 }
    $font = [System.Drawing.Font]::new("Segoe UI", $fontSize, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $format = [System.Drawing.StringFormat]::new()
    $format.Alignment = [System.Drawing.StringAlignment]::Center
    $format.LineAlignment = [System.Drawing.StringAlignment]::Center
    $textRectangle = [System.Drawing.RectangleF]::new(0, 0, 64, 64)
    $shadowRectangle = [System.Drawing.RectangleF]::new(2, 2, 64, 64)
    $iconHandle = [IntPtr]::Zero
    try {
        $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $graphics.Clear([System.Drawing.Color]::Transparent)
        $graphics.DrawString("$percent", $font, $shadowBrush, $shadowRectangle, $format)
        $graphics.DrawString("$percent", $font, $textBrush, $textRectangle, $format)

        $iconHandle = $bitmap.GetHicon()
        $icon = [System.Drawing.Icon]::FromHandle($iconHandle)
        $outputPath = Join-Path $outputDirectory ("usage-orbit-{0:D3}.ico" -f $percent)
        $stream = [System.IO.File]::Open($outputPath, [System.IO.FileMode]::Create)
        try {
            $icon.Save($stream)
        }
        finally {
            $stream.Dispose()
            $icon.Dispose()
        }
    }
    finally {
        if ($iconHandle -ne [IntPtr]::Zero) {
            [CodexIconNativeMethods]::DestroyIcon($iconHandle) | Out-Null
        }
        $format.Dispose()
        $font.Dispose()
        $textBrush.Dispose()
        $shadowBrush.Dispose()
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}
